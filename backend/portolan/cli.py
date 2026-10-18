"""`portolan` command line. Commands are added milestone by milestone (spec §18)."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import typer

from portolan.config import Config, api_key_present, load_config, load_env, repo_root
from portolan.ingest.http import PoliteClient
from portolan.ingest.pipeline import IngestReport
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


async def _resolve_and_ingest(
    raw: str,
    client: PoliteClient,
    db: Database,
    *,
    refresh: bool = False,
    choose: int | None = None,
    save: bool = True,
) -> IngestReport:
    """Resolve user input (asking which title match when needed) and ingest the paper."""
    from portolan.ingest.http import FetchError
    from portolan.ingest.pipeline import IngestError, ingest_ref
    from portolan.ingest.resolver import resolve

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
        return await ingest_ref(ref, client, db, pdf_url=result.pdf_url, refresh=refresh, save=save)
    except (IngestError, FetchError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None


async def _ingest_input(
    raw: str, contact: str, db: Database, dry_run: bool, refresh: bool, choose: int | None
) -> None:
    from portolan.ingest.http import PoliteClient

    async with PoliteClient(db, contact=contact) as client:
        report = await _resolve_and_ingest(
            raw, client, db, refresh=refresh, choose=choose, save=not dry_run
        )
    _print_doc(report.doc, report.attempts)
    if client.requests_made:
        typer.echo(f"\n{client.requests_made} network request(s); the rest came from the cache.")


@app.command()
def repos(
    paper: str = typer.Argument(..., help="arXiv link or id, DOI or title."),
    use: int | None = typer.Option(None, help="Choose candidate N as this paper's code."),
    upload: str | None = typer.Option(
        None, help="Use your own code instead: a .zip file or a local folder."
    ),
    refresh: bool = typer.Option(False, help="Search again instead of using stored results."),
    choose: int | None = typer.Option(None, help="For a title search: pick match N (1-3)."),
) -> None:
    """Find the paper's code: official repo, community implementations, or none."""
    config = load_config()
    db = open_database(config.storage.db_path)
    try:
        asyncio.run(_repos(paper, config, db, use, upload, refresh, choose))
    finally:
        db.close()


async def _repos(
    raw: str,
    config: Config,
    db: Database,
    use: int | None,
    upload: str | None,
    refresh: bool,
    choose: int | None,
) -> None:
    from pathlib import Path

    from portolan.ingest.http import FetchError, PoliteClient
    from portolan.repo.finder import RepoFinder
    from portolan.repo.upload import UploadError, extract_zip, use_folder
    from portolan.store.repos import choose_repo, chosen_repo, load_candidates, save_candidates

    async with PoliteClient(db, contact=config.http.contact) as client:
        doc = (await _resolve_and_ingest(raw, client, db, choose=choose)).doc

        if upload:
            path = Path(upload).expanduser()
            try:
                if path.suffix.lower() == ".zip" and path.is_file():
                    cand = extract_zip(
                        path.read_bytes(), doc.paper_id, config.storage.workspace_dir, path.name
                    )
                else:
                    cand = use_folder(path)
            except UploadError as exc:
                typer.echo(str(exc))
                raise typer.Exit(code=1) from None
            choose_repo(db, doc.paper_id, cand)
            typer.echo(f"\nUsing your code for {doc.title}: {cand.local_path}")
            if cand.status != "ok":
                typer.echo(f"Warning: {cand.status_reason}.")
            return

        stored = None if refresh else load_candidates(db, doc.paper_id)
        warnings: list[str] = []
        if stored is None:
            typer.echo("Finding code…")
            try:
                result = await RepoFinder(client).find(doc)
            except FetchError as exc:
                typer.echo(str(exc))
                raise typer.Exit(code=1) from None
            candidates, status, warnings = result.candidates, result.status, result.warnings
            save_candidates(db, doc.paper_id, candidates, status)
        else:
            candidates, status = stored

    typer.echo(f"\n{doc.title}\n{status.message}")
    for w in warnings:
        typer.echo(f"Note: {w}")
    for i, c in enumerate(candidates, start=1):
        label = {True: "official", None: "unverified", False: "unofficial"}[c.official]
        stars = f"★ {c.stars:,}" if c.stars is not None else ""
        health_note = "" if c.status == "ok" else f" — {c.status}: {c.status_reason}"
        typer.echo(f"  {i}. {c.owner}/{c.name}  [{label}] {stars}{health_note}")
        typer.echo(f"       {c.url} · {c.official_evidence}")

    if use is not None:
        if not 1 <= use <= len(candidates):
            typer.echo(f"There is no candidate {use}.")
            raise typer.Exit(code=1)
        choose_repo(db, doc.paper_id, candidates[use - 1])
    current = chosen_repo(db, doc.paper_id)
    if current is None and candidates and candidates[0].status == "ok":
        choose_repo(db, doc.paper_id, candidates[0])
        current = candidates[0]
    if current is not None:
        typer.echo(
            f"\nUsing: {current.owner}/{current.name}  (change with --use N or --upload PATH)"
        )
    else:
        typer.echo("\nNo code linked. Use --upload PATH to add your own implementation.")
    if client.requests_made:
        typer.echo(f"{client.requests_made} network request(s); the rest came from the cache.")


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


@app.command()
def index(
    paper: str = typer.Argument(..., help="arXiv link or id, DOI or title."),
    query: list[str] | None = typer.Option(None, "--query", "-q", help="Try retrieval with this."),
    embed: bool = typer.Option(False, help="Also compute embeddings (needs the embeddings extra)."),
    choose: int | None = typer.Option(None, help="For a title search: pick match N (1-3)."),
) -> None:
    """Clone (or reuse) the paper's chosen code and index it for retrieval."""
    config = load_config()
    db = open_database(config.storage.db_path)
    try:
        asyncio.run(_index(paper, config, db, query or [], embed, choose))
    finally:
        db.close()


async def _index(
    raw: str, config: Config, db: Database, queries: list[str], embed: bool, choose: int | None
) -> None:
    import time

    from portolan.code.embeddings import default_embedder, embed_symbols
    from portolan.code.index_store import IndexStore
    from portolan.code.indexer import index_checkout
    from portolan.code.retrieval import retrieve
    from portolan.repo.cloner import CloneError, clone
    from portolan.store.repos import chosen_repo

    async with PoliteClient(db, contact=config.http.contact) as client:
        doc = (await _resolve_and_ingest(raw, client, db, choose=choose)).doc
    repo = chosen_repo(db, doc.paper_id)
    if repo is None:
        typer.echo(f"No code chosen for {doc.title} yet. Run `portolan repos {raw}` first.")
        raise typer.Exit(code=1)

    typer.echo(f"Cloning {repo.owner}/{repo.name}…")
    try:
        checkout = await asyncio.to_thread(clone, repo, config.storage.workspace_dir, doc.paper_id)
    except CloneError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None

    store = IndexStore(db)
    start = time.perf_counter()
    built = index_checkout(store, checkout.root, repo.id, checkout.commit_sha)
    took = time.perf_counter() - start
    idx = built.index
    typer.echo(
        f"Indexed {len(idx.files)} files ({dict(built.kinds)}) · {idx.symbol_count} symbols · "
        f"{took:.1f}s at {checkout.commit_sha[:12]}"
    )
    typer.echo("Libraries: " + (", ".join(idx.library_modules) or "none"))
    skipped = len(idx.skipped) + len(checkout.skipped_large)
    if skipped:
        typer.echo(f"Skipped {skipped} file(s)/folder(s): vendored, binary, or over 1 MB.")

    embedder = None
    if embed:
        embedder = default_embedder()
        if embedder is None:
            typer.echo("Embeddings extra not installed: `uv sync --extra embeddings`. BM25 only.")
        else:
            n = embed_symbols(store, repo.id, checkout.commit_sha, embedder)
            typer.echo(f"Embedded {n} symbols with {embedder.model}.")

    for q in queries:
        typer.echo(f"\nTop matches for “{q}”:")
        for hit in retrieve(store, repo.id, checkout.commit_sha, q, k=5, embedder=embedder):
            s = hit.symbol
            typer.echo(f"  {hit.score:7.3f}  {s.path}:{s.start_line}-{s.end_line}  {s.qualname}")


@app.command()
def schema(out: str = typer.Option("", help="Write to this file instead of stdout.")) -> None:
    """Print the JSON Schema of every API model (for generating frontend types)."""
    import json
    from pathlib import Path

    from pydantic import BaseModel

    from portolan.models.concept import Concept, Layer, Link
    from portolan.models.detail import EdgeExplanation, Explanation
    from portolan.models.paper import PaperDoc
    from portolan.models.repo import RepoCandidate, RepoStatus

    models: list[type[BaseModel]] = [
        Concept, Link, Layer, PaperDoc, RepoCandidate, RepoStatus, Explanation, EdgeExplanation,
    ]  # fmt: skip
    text = json.dumps({m.__name__: m.model_json_schema() for m in models}, indent=2)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        typer.echo(f"Wrote {len(models)} schemas to {out}")
    else:
        typer.echo(text)


@app.command("seed-demo")
def seed_demo() -> None:
    """Write a small demo paper and its map into the database (no network, no LLM)."""
    from portolan.dev.seed import seed_demo as seed

    config = load_config()
    db = open_database(config.storage.db_path)
    try:
        paper_id = seed(db)
    finally:
        db.close()
    typer.echo(f"Seeded {paper_id} into {config.storage.db_path}")
    typer.echo(f"Open http://localhost:5173/?paper={paper_id}")


DETAIL_STEPS = ("explain", "ground", "example", "graph")
RECORD_STEPS = (
    "master", "skeleton", "explain", "ground", "example", "graph", "route", "faq", "verify",
)  # fmt: skip


@app.command()
def record(
    paper: str = typer.Option(..., "--paper", help="arXiv link or id, DOI or title."),
    steps: str = typer.Option(
        "master,skeleton", help=f"Comma-separated: {', '.join(RECORD_STEPS)}."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before spending requests."),
) -> None:
    """Run steps against the REAL provider and save the responses as mock fixtures.

    Uses your Gemini key from .env. Responses go to backend/tests/fixtures/llm/ so tests and
    development can replay them with PORTOLAN_LLM_PROVIDER=mock at no cost.
    """
    wanted = [s.strip() for s in steps.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in RECORD_STEPS]
    if unknown:
        typer.echo(f"Unknown step(s): {', '.join(unknown)}. Choose from {', '.join(RECORD_STEPS)}.")
        raise typer.Exit(code=1)
    config = load_config()
    config.llm.provider = "gemini"
    if not api_key_present("gemini"):
        typer.echo("GEMINI_API_KEY isn't set in .env. Add it there (never paste it into chat).")
        raise typer.Exit(code=1)
    if config.llm.models.placeholders():
        typer.echo("portolan.toml still has placeholder model ids. Run `portolan doctor` first.")
        raise typer.Exit(code=1)
    # §5.3: master 1-2, level-1 skeletons ~4, level-2 ~15, plus a few same-concept checks.
    estimate = (
        (2 if "master" in wanted else 0)
        + (25 if "skeleton" in wanted else 0)
        + 15 * len([s for s in wanted if s in DETAIL_STEPS])  # one per master-layer concept
        + (4 if "faq" in wanted else 0)  # up to 3 answer batches + 1 anticipated batch
        + (9 if "route" in wanted else 0)  # one per top-level section
        + (18 if "verify" in wanted else 0)  # <= 6 extraction + <= 10 judge batches + 1 reverse
    )
    typer.echo(f"This will use up to ~{estimate} real Gemini requests (cached answers are free).")
    if estimate > 30 and not yes and not typer.confirm("Continue?", default=False):
        raise typer.Exit(code=1)
    db = open_database(config.storage.db_path)
    try:
        asyncio.run(_record(paper, config, db, wanted))
    finally:
        db.close()


async def _record(raw: str, config: Config, db: Database, steps: list[str]) -> None:
    from portolan.chart.master import ChartBuilder
    from portolan.llm.gateway import Gateway
    from portolan.llm.providers.base import FixtureStore
    from portolan.llm.providers.mock import DEFAULT_FIXTURES
    from portolan.models.concept import MASTER
    from portolan.store.chart import ChartStore

    async with PoliteClient(db, contact=config.http.contact) as client:
        doc = (await _resolve_and_ingest(raw, client, db)).doc

    def on_event(kind: str, data: dict[str, object]) -> None:
        if kind == "rate_limited":
            typer.echo(f"  waiting for the free-tier rate limit (~{data['wait_seconds']} s)…")

    gateway = Gateway(config, db, recorder=FixtureStore(DEFAULT_FIXTURES), on_event=on_event)
    store = ChartStore(db)
    if "master" in steps or "skeleton" in steps:
        store.delete_paper(
            doc.paper_id
        )  # rebuild so every call is captured (cache answers are free)
    elif store.get_layer(doc.paper_id, MASTER) is None:
        # Detail steps need the map the earlier recordings made: replay it from fixtures (free).
        replay_config = config.model_copy(deep=True)
        replay_config.llm.provider = "mock"
        replay = ChartBuilder(doc, Gateway(replay_config, db), store)
        await replay.master()
        await replay.ensure_frontier(MASTER, levels=2)
    builder = ChartBuilder(doc, gateway, store)
    before = gateway.limiter.budget.today()
    if "master" in steps:
        typer.echo("Building the master layer…")
        layer = await builder.master()
        names = [c.title for c in store.concepts(doc.paper_id, layer.concepts).values()]
        typer.echo(f"  {len(names)} concepts, {len(layer.links)} links: " + "; ".join(names))
    if "skeleton" in steps:
        typer.echo("Building skeletons two levels below the master layer…")
        made = await builder.ensure_frontier(MASTER, levels=2)
        typer.echo(f"  {len(made)} child layers")
    detail_steps = [st for st in steps if st in DETAIL_STEPS]
    if detail_steps:
        from portolan.code.ensure import ensure_index
        from portolan.detail.example import make_example
        from portolan.detail.explain import explain
        from portolan.detail.graph import make_graph
        from portolan.detail.ground import ground_code
        from portolan.store.repos import chosen_repo

        layer = store.get_layer(doc.paper_id, MASTER)
        if layer is None:
            typer.echo("Skipping detail steps: no master layer (run the master step first).")
        else:
            if "ground" in detail_steps:
                repo = chosen_repo(db, doc.paper_id)
                typer.echo("Cloning and indexing the code…")
                if repo is None or not await ensure_index(config, db, doc.paper_id, repo):
                    typer.echo("  no code to ground against; skipping ground.")
                    detail_steps.remove("ground")
            for cid in layer.concepts:
                title = store.concepts(doc.paper_id, [cid])[cid].title
                for st in detail_steps:
                    try:
                        if st == "explain":
                            await explain(doc, gateway, store, cid, "default")
                        elif st == "ground":
                            g = await ground_code(doc, gateway, store, db, cid)
                            typer.echo(f"  {title}: {g.grounding} ({len(g.notes)} notes)")
                        elif st == "example":
                            await make_example(doc, gateway, store, cid, 0, [])
                        elif st == "graph":
                            await make_graph(doc, gateway, store, cid)
                    except Exception as exc:  # one bad concept must not waste the rest of the run
                        typer.echo(f"  {title}: {st} failed ({exc})")
                typer.echo(f"  done: {title}")
    if "verify" in steps:
        from portolan.code.ensure import ensure_index
        from portolan.store.findings import FindingStore
        from portolan.store.repos import chosen_repo
        from portolan.verify.engine import verify_paper

        repo = chosen_repo(db, doc.paper_id)
        typer.echo("Cloning and indexing the code…")
        if repo is None or not await ensure_index(config, db, doc.paper_id, repo):
            typer.echo("  no code to verify against; skipping verify.")
        else:
            FindingStore(db).delete_paper(doc.paper_id)  # a recording run starts clean
            typer.echo("Checking the paper's claims against the code…")
            await verify_paper(
                doc, gateway, store, db,
                lambda f: typer.echo(f"  {f.verdict:<22} {f.claim_text[:70]}"),
            )  # fmt: skip
            counts: dict[str, int] = {}
            for f in FindingStore(db).all(doc.paper_id):
                counts[f.verdict] = counts.get(f.verdict, 0) + 1
            typer.echo("  " + (", ".join(f"{n} {v}" for v, n in sorted(counts.items())) or "none"))
    if "route" in steps:
        from portolan.route.build import build_main_route
        from portolan.store.routes import RouteStore

        typer.echo("Writing the Route (one request per top-level section)…")
        routes = RouteStore(db)
        route = await build_main_route(doc, gateway, store, routes)
        routes.save(route)
        openable = sum(1 for s in route.stops if s.sub_route_id)
        typer.echo(f"  {len(route.stops)} stops, {openable} that open into a sub-route")
    if "faq" in steps:
        from portolan.faq.build import build_faq

        typer.echo("Building the FAQ (reviewer discussion, GitHub issues, findings, anticipated)…")
        kinds: list[str] = []
        async with PoliteClient(db, contact=config.http.contact) as faq_http:
            await build_faq(
                doc, gateway, store, db, faq_http, lambda item: kinds.append(item.source_kind)
            )
        typer.echo(f"  {len(kinds)} items: " + (", ".join(sorted(set(kinds))) or "none"))
    after = gateway.limiter.budget.today()
    used = sum(int(r["requests"]) for r in after) - sum(int(r["requests"]) for r in before)
    typer.echo(f"\nReal requests this run: {used} (calls answered from cache: free).")
    for w in builder.build.warnings:
        typer.echo(f"  warning: {w}")
    typer.echo(f"Fixtures written under {DEFAULT_FIXTURES}")


@app.command("eval")
def eval_command(
    data: str | None = typer.Option(None, "--data", help="SciCoQA download (folder or file)."),
    limit: int = typer.Option(25, help="How many records to run (0 = all)."),
    split: str = typer.Option("all", help="all | real | synthetic"),
    seed: int = typer.Option(0, help="Sampling seed: same seed, same records."),
    out: str | None = typer.Option(None, help="Checkpoint folder (default eval/runs)."),
    report_dir: str | None = typer.Option(None, help="Report folder (default eval/reports)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before spending requests."),
    fresh: bool = typer.Option(False, help="Ignore saved results and start over."),
    dry_run: bool = typer.Option(False, help="Fake engine on the sample data: no requests."),
    inspect: str | None = typer.Option(None, help="Show how a dataset is read, then stop."),
    retry_failed: bool = typer.Option(True, help="Run records that failed last time again."),
) -> None:
    """Measure the verification engine on SciCoQA (resumable, stops when the daily budget ends)."""
    from portolan.evalharness.commands import run_eval

    run_eval(
        data=data, limit=limit, split=split, seed=seed, out=out, report_dir=report_dir, yes=yes,
        fresh=fresh, dry_run=dry_run, inspect=inspect, retry_failed=retry_failed,
    )  # fmt: skip


if __name__ == "__main__":
    app()
