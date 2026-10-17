# Decisions

Choices the spec left open, deviations, and things verified against real docs. Newest last.

## Open questions (§20) — defaults in use until the owner says otherwise

| # | Question | Using |
|---|---|---|
| 1 | License | **MIT** (`LICENSE`, copyright "Portolan contributors"). Owner to confirm before publishing. |
| 2 | PyPI package name | **`portolan-papers`** (import name and CLI stay `portolan`). |
| 3 | "terra incognita" hover | **yes, subtle** (M5). |
| 4 | Default view after paste | **Chart**. |
| 5 | Default theme | **follow system**. |
| 6 | Development papers | **as listed in Appendix E**. |
| 7 | Hosting | decide later. |

## M0 — Foundations (2026-10-17)

- **SQLite access via stdlib `sqlite3`, not SQLModel/SQLAlchemy.** The spec allows "SQLModel (or
  SQLAlchemy 2.0)"; we chose neither for now: the tables are few, FTS5 (M3) needs raw SQL anyway,
  and it removes a dependency. Migrations are an append-only list in `store/migrations.py` with a
  `schema_version` table. Revisit if the data model grows awkward to map by hand.
- **Extra dependencies:** `python-dotenv` (load `.env`; already a LiteLLM dependency) and `tzdata`
  (Windows has no system time-zone database; needed for the US Pacific day boundary of RPD).
- **Deferred dependencies:** tree-sitter, sentence-transformers, PyMuPDF, selectolax, pylatexenc,
  React Flow, elkjs, Zustand, TanStack Query, Shiki, KaTeX are added in the milestones that use them,
  not in M0, to keep setup fast.
- **Mock fixture key.** Fixtures live at `backend/tests/fixtures/llm/<template_id>/v<version>/<inputs_hash>[.retryN].json`
  and are keyed by template + inputs hash, *not* by model id, so changing `[llm.models]` doesn't
  invalidate recorded fixtures. The SQLite response cache *is* keyed by model as §5.2 requires.
  Fixtures contain only response text, token usage and model id (no headers).
- **Mock provider bypasses the rate limiter and budget** (it costs no quota).
- **Rate limiter details.** RPM/TPM are sliding 60 s windows per model, in memory; RPD is a
  persistent per-model counter keyed by the US Pacific date. A request counts toward RPD when it
  starts (so 429s count too). P1/P2 RPM cap is `max(1, rpm - p0_reserve_rpm)` so background work
  can't be starved entirely by a misconfiguration. Strict priority: a lower priority never starts
  while a higher one is waiting. Hitting the daily cap raises `DailyBudgetExhausted` instead of
  waiting until midnight; the orchestrator (later) will pause P2 on it.
- **429 backoff:** 2 s doubling to a 60 s cap, +0–25 % jitter, `retry-after` honored when present,
  the model is put in cooldown so other waiters also hold off; gives up after 6 retries.
- **Gemini model listing** (`portolan doctor`): `GET https://generativelanguage.googleapis.com/v1beta/models`
  with the key in the `x-goog-api-key` header (never in the URL), paging via `nextPageToken`,
  keeping models whose `supportedGenerationMethods` contains `generateContent`. Shape taken from
  Google's REST docs; **to verify against a real response on the owner's first doctor run.**
  Suggestions pick the highest-numbered stable `gemini-X-flash-lite` / `gemini-X-flash` (no
  preview/exp, never Pro).
- **Tests never load `.env`:** `PORTOLAN_NO_DOTENV=1` is set by the test suite so a real key in the
  owner's `.env` can never reach a test.
- **Windows sandbox note for M6:** `resource` limits are unavailable on Windows; per §10.7.3 we'll use
  timeout-only there and log a warning (or a job object if it proves simple).

## Measured request budgets

_None yet — filled in from real `portolan record` runs (M4)._

## TODO(owner)

- Replace the placeholder compass-rose logo (`frontend/src/design/Logo.tsx`) with your own drawing.

## Ideas (not in spec; not built)

_None yet._
