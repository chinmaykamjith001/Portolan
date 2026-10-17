"""`portolan` command line. Commands are added milestone by milestone (spec §18)."""

from __future__ import annotations

import asyncio
import os
import sys

import httpx
import typer

from portolan.config import api_key_present, load_config, load_env, repo_root
from portolan.llm.providers.gemini import list_models, suggest_models
from portolan.store.db import open_database

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


if __name__ == "__main__":
    app()
