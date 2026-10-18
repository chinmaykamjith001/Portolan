# Contributing

Portolan is built milestone by milestone from [PORTOLAN_SPEC.md](PORTOLAN_SPEC.md), which wins over
anything written here. Read the part you are touching first, and [CLAUDE.md](CLAUDE.md) for the
non-negotiables.

## Setup

```
make setup                                  # uv sync + pnpm install
cp .env.example .env                        # keep PORTOLAN_LLM_PROVIDER=mock while developing
cd backend && uv run portolan doctor        # should end with "All good."
make dev                                    # API on :8000, frontend on :5173
```

Python 3.12 with uv, Node 20+ with pnpm, `git` and `make`. Playwright's Chromium is needed only for
`make e2e` (`pnpm exec playwright install chromium`, or set `PW_CHROMIUM` to an installed build).

## Checks

Before you push, these must pass:

```
make lint typecheck test
make e2e            # if you touched the frontend or an endpoint it uses
```

That is backend `ruff check`, `ruff format --check`, `pyright` (strict) and `pytest`, then frontend
`eslint`, `tsc` and `vitest`. CI runs the same, plus a production build and the browser tests.

## Rules that matter

- **Mock first.** Develop and test with `PORTOLAN_LLM_PROVIDER=mock`. Tests replay recorded answers
  from `backend/tests/fixtures/llm/` or use a fake LLM; a missing fixture fails with a clear error.
- **CI never calls a real LLM**, and neither should your tests. Real calls happen only through
  `portolan record` and `portolan eval`, run by someone with their own key.
- **All LLM calls go through `backend/portolan/llm/gateway.py`.** Never call LiteLLM or a provider
  directly: the gateway is where caching, rate limits and the daily budget live.
- **The free tier is a design constraint.** Batch calls, cache everything, generate cheap skeletons
  ahead and expensive details only when a user opens something (spec §5).
- **Keys.** A key lives only in a local `.env`. Never print, log, commit or paste one, and never put
  one in a fixture.
- **Numbers in examples and graphs come from the sandbox**, never from LLM text.
- **Design.** Tokens only (`frontend/src/design/tokens.css`), IBM Plex Sans and Mono, minimal. No
  gradients, blur, emoji, default Tailwind colors or card grids (spec §14, enforced by
  `frontend/tests/style.test.ts`). Diagrams are `.drawio` files in `docs/`, never inline in Markdown.
- **No dependency or feature that isn't in the spec** without a reason written in `docs/DECISIONS.md`
  (ideas go under "Ideas" there).

## Recording fixtures

Mock mode replays answers saved by `portolan record`. Recording uses a real key and real requests, so
only do it with your own key in `.env`, `PORTOLAN_LLM_PROVIDER` left alone (the command forces
`gemini`), and real model ids in `portolan.toml` (`portolan doctor` suggests them).

```
make record ARGS="--paper 2106.09685 --steps master,skeleton"
make record ARGS="--paper 2106.09685 --steps explain,ground,example,graph,route,verify,faq --yes"
```

It prints an estimate first and asks before spending more than 30 requests. Rough cost per paper, with
a free key allowing about 1,400 requests a day:

| Step | Requests |
|---|---|
| `master` | 1-2 |
| `skeleton` | up to ~25 |
| `explain`, `ground`, `example`, `graph` | about 15 each (one per master-layer concept) |
| `route` | about 9 (one per top-level section) |
| `verify` | up to ~18, often fewer |
| `faq` | up to 4 |

`ground`, `verify` and `faq` need the paper's code cloned, so they need network access to GitHub. The
detail steps rebuild the Chart from existing fixtures, so they don't repeat `master` and `skeleton`.
Fixtures hold only response text, token counts and the model id. Commit the new files under
`backend/tests/fixtures/llm/`. Re-recording after a prompt change is expected; prompt snapshot tests
(`backend/tests/__snapshots__/`) exist so prompt changes are reviewed on purpose.

Some tests need the real network (`pytest -m network`); they are not run in CI.

## Commits and pull requests

- Small commits with a message that says what changed and why; never commit failing tests or lint.
- Update `docs/PROGRESS.md` when a milestone moves, and log choices in `docs/DECISIONS.md`.
- Report honestly: say what you did not verify. Don't claim evaluation numbers a run didn't produce.

Pull request checklist:

- [ ] `make lint typecheck test` pass; `make e2e` if the UI changed
- [ ] New behavior has tests (mock provider only)
- [ ] No real LLM calls, no keys, no new fixtures with secrets
- [ ] Design tokens only; keyboard and screen-reader behavior considered
- [ ] `docs/PROGRESS.md` / `docs/DECISIONS.md` updated if needed
