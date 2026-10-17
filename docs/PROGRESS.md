# Progress

## M0 — Foundations ✅ (2026-10-17)

**Plan**
- Repo scaffold per §7; `.gitignore` (with `.env`), `.env.example`, `portolan.toml.example`,
  Makefile, CI (mock only), docs files, `CLAUDE.md`, MIT `LICENSE`.
- Backend: config loader (toml + env), SQLite with migrations, Typer CLI (`doctor`, `serve`),
  FastAPI app (`/api/health`, `/api/budget`, error envelope).
- LLM gateway: prompt-template contract, persistent response cache, priority-aware rate limiter
  (RPM/TPM/RPD with P0 reserve), budget table, 429 backoff, schema validation with one repair
  retry, mock provider (replay) + recorder, Gemini/Ollama via LiteLLM (never called in tests).
- Frontend: Vite + React 18 + TS strict, ESLint, Vitest, design tokens, IBM Plex, empty-state shell.

**Done / verified on this machine (Windows 11)**
- Backend: `ruff check`, `ruff format --check`, `pyright` (strict on `portolan/`), `pytest` — 32 tests pass.
  Gateway tests cover cache hit, schema retry (success and double failure), 429 exponential backoff
  with jitter, `retry-after`, cap + give-up, P0 preemption of queued background calls, budget
  accounting, mock missing-fixture error, record → replay.
- Frontend: `lint`, `typecheck`, `test`, `build` pass (first-load JS ≈ 46 KB gzipped).
- `portolan doctor` explains a missing key / placeholder models and exits non-zero; passes under mock.

**Not verified**
- `make` targets themselves: GNU make isn't installed on the dev machine; the underlying commands were run directly.
- CI workflow hasn't run yet (no remote).
- Real Gemini model listing in `doctor` (mocked with respx only).

**Next: M1 — Paper ingestion** (resolver, polite cached fetchers, HTML/LaTeX/PDF parsers → `PaperDoc`,
`portolan ingest --dry-run`).
