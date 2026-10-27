# Portolan

**Explore any paper and its code.** Paste a research paper and Portolan finds its code, turns the
paper into a map of concepts you can jump into all the way down to first principles, and walks you
through the paper in the order it was written. For each idea it shows where it lives in the code,
and where the code disagrees with the paper.

> Status: all eleven milestones are built and tested against a mock LLM and a seeded demo paper.
> It has **not** yet been run end to end on a real paper with a real key. See
> [Status and limits](#status-and-limits) and [docs/PROGRESS.md](docs/PROGRESS.md).

## What you can do

- **Chart.** A map of the paper's ideas. Double-click a concept to jump into the ideas it is made of;
  Back (or Esc) climbs out again.
- **Route.** The paper as an S-shaped road, in its own writing order, with the connective words
  between stops. Jump into any stop for the detail under it.
- **Code grounding.** Open a concept, then the Code tab: the lines that implement it, with a note
  under each line saying which part of the paper it is.
- **Findings.** Claims in the paper checked against the code. Mismatches and behavior the paper never
  mentions are flagged on the Chart and listed in one place.
- **FAQ.** Questions from reviews and GitHub issues, plus an Ask box that answers from the paper and
  code with citations, or says it can't.

## Quickstart

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+ with
[pnpm](https://pnpm.io/), and `git` and `make`.

1. **Install.** `make setup`
2. **Add your key.** Get a free key at [aistudio.google.com](https://aistudio.google.com), then
   ```
   cp .env.example .env
   cp portolan.toml.example portolan.toml
   ```
   In `.env`, set `GEMINI_API_KEY=` to your key and change `PORTOLAN_LLM_PROVIDER=mock` to
   `PORTOLAN_LLM_PROVIDER=gemini`. Then run `cd backend && uv run portolan doctor`: it lists the
   models your key can use and prints a `[llm.models]` section. Paste that section over the
   placeholders in `portolan.toml`.
3. **Run.** `make dev`, open <http://localhost:5173>, and paste an arXiv link, a DOI or a paper title.

Never commit `.env` and never paste your key anywhere else; it is git-ignored and only ever read
from your environment.

### Free-tier notes

- A free Gemini key allows roughly 10 requests per minute and about 1,500 per day (Google doesn't
  publish a table; check your project in AI Studio and set `[llm.limits]` in `portolan.toml`).
  Portolan paces itself below that, caches every answer, and does cheap work ahead of time while
  doing the expensive work only when you open something.
- **"Waiting for the free-tier rate limit" is normal.** It means Portolan is pacing itself, not that
  something broke. Results appear as soon as the wait ends.
- The top bar shows how many requests you have used today. The counter resets at midnight US Pacific.
- Free Gemini keys may let Google use what you send. Don't analyze private code with a free key.

### Try it without a key (mock mode)

With `PORTOLAN_LLM_PROVIDER=mock` (the default in `.env.example`) no request is ever made. You get the
built-in demo paper, with every feature filled in:

```
cd backend && uv run portolan seed-demo
make dev          # then open http://localhost:5173/?paper=demo:toyformer
```

Mock mode can also replay the recorded Chart for the two development papers (Attention Is All You
Need, LoRA) when you paste them, but arXiv must be reachable to download them, and their explanations,
code notes and findings were not recorded yet (see [CONTRIBUTING.md](CONTRIBUTING.md#recording-fixtures)).

### Docker

```
cp .env.example .env      # optional; add your key and set PORTOLAN_LLM_PROVIDER=gemini
docker compose up --build
```

Open <http://localhost:8000>. The image serves the API and the built frontend from one process, and
keeps its data in the `portolan-data` volume. The Docker setup has not been built or run by the
author yet (no Docker daemon was available); treat it as untested.

To put it on a server with HTTPS and a login, see
[docs/deploy-lightsail.md](docs/deploy-lightsail.md) (AWS Lightsail, about $12 a month) or
[docs/deploy-oracle.md](docs/deploy-oracle.md) (Oracle Cloud Always Free).

To run the same single process without Docker: `cd frontend && pnpm build`, then
`cd backend && uv run portolan serve` and open <http://localhost:8000>.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Install backend (`uv sync`) and frontend (`pnpm install`) dependencies. |
| `make dev` | Run the API on :8000 and the frontend on :5173. |
| `make test` / `make lint` / `make typecheck` | Run the tests, linters and type checkers for both halves. |
| `make e2e` | Playwright browser tests against the mock provider and the demo paper. |
| `make record ARGS="..."` | Real LLM calls that save mock fixtures. Costs requests; see CONTRIBUTING. |
| `make eval ARGS="..."` | Measure the verification engine on SciCoQA. See [eval/README.md](eval/README.md). |

Run these from `backend/` as `uv run portolan <command>`:

| Command | What it does |
|---|---|
| `doctor` | Check your setup and say what is missing. |
| `serve` | Run the API (and the built frontend, if `frontend/dist` exists). |
| `ingest <paper> [--dry-run]` | Fetch and parse a paper; print its section tree. |
| `repos <paper>` | Find the paper's code: official repo, community code, or none. |
| `index <paper>` | Clone the chosen code and index it for retrieval. |
| `record --paper <paper> --steps ...` | Run steps against the real provider and save the answers as fixtures. |
| `eval --data <path> --limit 25` | Run the SciCoQA evaluation (resumable). |
| `seed-demo` | Write the built-in demo paper into the database. |
| `schema` | Print the JSON Schema of the API models. |

`<paper>` is an arXiv link or id, a DOI, or a title.

## Project layout

```
backend/portolan/   ingest/ repo/ code/ chart/ route/ detail/ verify/ faq/ sandbox/ llm/ api/ store/
backend/tests/      pytest suite (mock provider only)
frontend/src/       app/ chart/ route/ detail/ findings/ faq/ design/ api/ state/
frontend/e2e/       Playwright specs
eval/               SciCoQA harness notes, sample data, reports
docs/               PROGRESS.md, DECISIONS.md, prompts.md, architecture.drawio, screenshots/
PORTOLAN_SPEC.md    the full specification (it wins over everything else)
```

## Screenshots

None yet; see [docs/screenshots/](docs/screenshots/README.md).

## Status and limits

Built and tested: ingestion, repo finding, code indexing, Chart, Route, node details with a sandboxed
example runner, verification, FAQ and the evaluation harness. Backend and frontend suites pass, and
the browser tests pass against a seeded demo paper.

Not verified, and worth knowing before you rely on it:

- **No real end-to-end run.** The pieces were tested with a mock LLM and recorded answers. Only the
  Chart for the two development papers was ever recorded with a real key.
- **No SciCoQA numbers.** The harness exists, but the dataset's real format was never checked and
  nothing has been measured. Don't quote any accuracy figure for Portolan.
- **OpenReview and GitHub response shapes** in the FAQ follow their documentation and were only
  tested against mocked responses.
- **Mismatch findings can be wrong.** They are surfaced only above a confidence threshold, but treat
  them as leads to check, not verdicts.
- **The sandbox** that runs example programs is strongest on Linux; on Windows only a timeout and the
  code checks apply. Portolan never runs a repository's own code.

More: [PORTOLAN_SPEC.md](PORTOLAN_SPEC.md) (specification), [docs/PROGRESS.md](docs/PROGRESS.md)
(what is done and what isn't), [docs/DECISIONS.md](docs/DECISIONS.md) (why).

## License

MIT. See [LICENSE](LICENSE).
