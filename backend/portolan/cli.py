"""`portolan` command line. Commands are added milestone by milestone (spec §18)."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import typer

from portolan.config import api_key_present, load_config, load_env, repo_root
from portolan.llm.providers.gemini import list_models, suggest_models
from portolan.models.paper import PaperDoc, PaperRef
from portolan.store.db import Database, open_database

app = typer.Typer(help="Portolan: explore any paper and its code.", no_args_is_help=True)


def _line(ok: bool | None, text: str) -> None:
    mark = {True: "ok  ", False: "FIX ", None: "--  "}[ok]
    typer.echo(f"  {mark}{text}")


@app.command()
def doctor() -> None:
    """Check your setup and explain anything that is missing."""
    problems = 0
    typer.echo("Portolan doctor\n")

    py_ok = sys.version_info[:2] == (3, 12)
    _line(py_ok, f"Python {sys.version.split()[0]}" + ("" if py_ok else " (Portolan needs 3.12)"))
    problems += not py_ok

    env_path = load_env()
    if env_path:
        _line(True, f".env found at {env_path}")
    else:
        _line(None, f".env not found. Create it: copy {repo_root() / '.env.example'} to .env")

    config = load_config(env=False)
    if config.config_path:
        _line(True, f"Config: {config.config_path}")
    else:
        _line(None, "portolan.toml not found; using defaults (copy portolan.toml.example)")

    provider = config.llm.provider
    _line(True, f"LLM provider: {provider}")

    if provider == "mock":
        _line(None, "Mock provider: replays recorded fixtures, makes no real LLM calls.")
    elif provider == "gemini":
        typer.echo(
            "\n  Note: Free Gemini keys may let Google use what you send. "
            "Don't analyze private code with a free key.\n"
        )
        if not api_key_present("gemini"):
            _line(
                False,
                "GEMINI_API_KEY is not set. Get a key at https://aistudio.google.com and put "
                "GEMINI_API_KEY=... in your local .env (never paste it into chat).",
            )
            problems += 1
        else:
            _line(True, "GEMINI_API_KEY is set (value not shown)")
            problems += _check_gemini_models()
    elif provider == "ollama":
        _line(None, f"Ollama at {config.llm.ollama_base_url} (verification quality will be lower)")

    if provider != "mock":
        for role in config.llm.models.placeholders():
            _line(False, f"Model for role '{role}' is still a placeholder in portolan.toml")
            problems += 1

    try:
        db = open_database(config.storage.db_path)
        version = db.schema_version()
        db.close()
        _line(True, f"Database: {config.storage.db_path} (schema v{version})")
    except Exception as exc:
        _line(False, f"Could not open the database at {config.storage.db_path}: {exc}")
        problems += 1

    if not config.http.contact:
        _line(None, "[http] contact not set (optional; sent to arXiv in the User-Agent)")

    github = bool(os.environ.get("GITHUB_TOKEN", "").strip())
    _line(
        None, "GITHUB_TOKEN " + ("is set" if github else "not set (optional; raises GitHub limits)")
    )

    typer.echo("")
    if problems:
        typer.echo(f"{problems} thing(s) to fix above.")
        raise typer.Exit(code=1)
    typer.echo("All good.")


def _check_gemini_models() -> int:
    api_key = os.environ["GEMINI_API_KEY"].strip()
    try:
        names = asyncio.run(list_models(api_key))
    except httpx.HTTPStatusError as exc:
        _line(False, f"Gemini rejected the model-list request (HTTP {exc.response.status_code}).")
        return 1
    except httpx.HTTPError:
        _line(False, "Could not reach the Gemini API to list models. Check your connection.")
        return 1
    _line(True, f"Gemini models available to your key: {len(names)}")
    for name in names:
        typer.echo(f"        {name}")
    suggestion = suggest_models(names)
    if suggestion:
        typer.echo("\n  Suggested portolan.toml section:\n\n  [llm.models]")
        for role, model in suggestion.items():
            typer.echo(f'  {role} = "{model}"')
        typer.echo("")
    else:
        _line(False, "No Flash/Flash-Lite model found; set [llm.models] by hand.")
        return 1
    return 0


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port."),
    reload: bool = typer.Option(False, help="Reload on code changes (development)."),
) -> None:
    """Run the backend API server."""
    import uvicorn

    uvicorn.run("portolan.api.app:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def ingest(
    paper: str = typer.Argument(..., help="arXiv link or id, DOI, title, or a path to a PDF."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and print; don't store."),
    refresh: bool = typer.Option(False, help="Ignore the stored copy and parse again."),
    choose: int | None = typer.Option(
        None, help="For a title search: pick match N (1-3) without asking."
    ),
) -> None:
    """Fetch and parse a paper, then print its section tree and anchor counts."""
    from pathlib import Path

    config = load_config()
    db = open_database(config.storage.db_path)
    try:
        path = Path(paper)
        if path.suffix.lower() == ".pdf" and path.is_file():
            from portolan.ingest.pipeline import IngestError, parse_upload

            try:
                doc = parse_upload(path.read_bytes(), path.name)
            except IngestError as exc:
                typer.echo(str(exc))
                raise typer.Exit(code=1) from None
            if not dry_run:
                from portolan.store.papers import save_paper

                save_paper(db, doc)
            _print_doc(doc, ["upload: ok"])
            return
        asyncio.run(_ingest_input(paper, config.http.contact, db, dry_run, refresh, choose))
    finally:
        db.close()


async def _ingest_input(
    raw: str, contact: str, db: Database, dry_run: bool, refresh: bool, choose: int | None
) -> None:
    from portolan.ingest.http import FetchError, PoliteClient
    from portolan.ingest.pipeline import IngestError, ingest_ref
    from portolan.ingest.resolver import resolve

    async with PoliteClient(db, contact=contact) as client:
        try:
            result = await resolve(raw, client)
            if result.error:
                typer.echo(result.error)
                raise typer.Exit(code=1)
            ref = result.ref
            if ref is None:
                typer.echo("Matches on arXiv:")
                for i, c in enumerate(result.candidates, start=1):
                    who = ", ".join(c.authors[:3]) + (" et al." if len(c.authors) > 3 else "")
                    typer.echo(f"  {i}. {c.title} — {who} ({c.year}) arXiv:{c.arxiv_id}")
                pick = choose
                if pick is None:
                    pick = int(typer.prompt("Which one? (0 to cancel)", default="0"))
                if not 1 <= pick <= len(result.candidates):
                    typer.echo("Cancelled.")
                    raise typer.Exit(code=1)
                c = result.candidates[pick - 1]
                ref = PaperRef(arxiv_id=c.arxiv_id, version=c.version, title=c.title)
            typer.echo("Reading paper…")
            report = await ingest_ref(
                ref, client, db, pdf_url=result.pdf_url, refresh=refresh, save=not dry_run
            )
        except (IngestError, FetchError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=1) from None
    _print_doc(report.doc, report.attempts)
    if client.requests_made:
        typer.echo(f"\n{client.requests_made} network request(s); the rest came from the cache.")


def _print_doc(doc: PaperDoc, attempts: list[str]) -> None:
    from portolan.ingest.summary import anchor_counts, section_tree

    typer.echo(f"\n{doc.title}")
    if doc.authors:
        more = f" + {len(doc.authors) - 6} more" if len(doc.authors) > 6 else ""
        typer.echo(", ".join(doc.authors[:6]) + more)
    typer.echo(f"{doc.paper_id} · source: {doc.ref.source_kind} · " + "; ".join(attempts))
    if doc.low_structure:
        typer.echo("Parsed from PDF; structure may be approximate.")
    typer.echo("\nSections")
    for line in section_tree(doc):
        typer.echo(f"  {line}")
    counts = anchor_counts(doc)
    typer.echo("\nAnchors: " + ", ".join(f"{n} {k}s" for k, n in counts.items()))
    typer.echo(
        f"Equation references: {sum(len(v) for v in doc.equation_refs.values())} · "
        f"notation entries: {len(doc.symbols)} · references: {len(doc.references)}"
    )
    code_links = [u for u in doc.links_found if "github.com" in u or "gitlab.com" in u]
    if code_links:
        typer.echo("Code links in the paper: " + ", ".join(code_links[:5]))


if __name__ == "__main__":
    app()
