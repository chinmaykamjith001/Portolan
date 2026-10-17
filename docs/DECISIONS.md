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

## M1 — Paper ingestion (2026-10-17)

- **HTML parsing: BeautifulSoup + lxml** (the spec allows selectolax or BS4+lxml); BS4's tree API
  makes the nested LaTeXML walk simpler. **LaTeXML structure verified** on real pages
  (arxiv.org/html/2106.09685 and ar5iv.labs.arxiv.org/html/2106.09685, same markup):
  `section.ltx_section|ltx_subsection|ltx_subsubsection|ltx_paragraph|ltx_appendix`, number in
  `.ltx_title > span.ltx_tag`; paragraphs `div.ltx_para`; equations `table.ltx_equation`, or
  `table.ltx_equationgroup` with one `tbody[id=S2.E1]` per numbered row; LaTeX in `math[alttext]`;
  floats `figure.ltx_table|ltx_figure` with `figcaption > span.ltx_tag` ("Table 1: ");
  cross-refs `a.ltx_ref[href="#S4.E3"]`; bibliography `section.ltx_bibliography li.ltx_bibitem`.
- **arXiv API Atom verified** on a real `id_list=2106.09685` response: entry `<id>` is
  `http://arxiv.org/abs/<id><version>`; authors in `author/name`; abstract in `summary`. Parsed with
  lxml with entity resolution and network access disabled.
- **Fields added to §9.1 models:** `Anchor.level` (section depth, for the tree and the Route),
  `Anchor.is_appendix` (Route side roads, §10.6), `PaperDoc.references` (bibliography; the §8
  contract lists references but §9.1 had no field), `PaperDoc.low_structure` (§10.2 PDF flag).
- **Anchor id details** beyond the spec's examples: unnumbered sections → `sec:<parent>.<slug>`;
  unnumbered equations → `eq:<section>:u<n>`; unnumbered floats → `tab:<section>:u<n>`; content
  before the first heading → `para:front:<n>`; collisions get a `~2` suffix. In align-style
  environments each numbered row is its own equation anchor; unnumbered rows join the next numbered row.
- **Same ids from HTML and LaTeX:** on both development papers the LaTeX parser gives the same
  section ids and equation numbers as the HTML parser (checked by hand on the real sources).
- **LaTeX limits:** `\numberwithin{equation}{section}` and `\tag` are handled; other counter
  changes, `\def` with parameters, and `\newcommand` with optional-argument defaults are not
  expanded. `\iffalse...\fi` removal is non-nesting.
- **e-print extraction is in memory only:** regular files with safe relative names, `.tex/.bbl/.ltx`
  only, ≤ 20 MB each, ≤ 100 MB total, ≤ 5000 members. Nothing from a source bundle is written to disk.
- **Politeness:** 3 s between requests to each arXiv host (export.arxiv.org, arxiv.org, ar5iv),
  1.1 s for Semantic Scholar, 1 s elsewhere. User-Agent `Portolan/<version> (research paper
  explorer; self-hosted[; contact])`; the contact comes from the new `[http] contact` option.
  Responses with status 200 are cached forever when the URL is versioned (with a TTL for searches and
  unversioned metadata); 404s are cached for a day.
- **Title input never auto-resolves:** the CLI lists up to 3 arXiv matches and asks.
- **PyMuPDF is AGPL-3.0.** It's a runtime dependency (not vendored), which is fine for local use, but
  matters for the MIT license story and for any hosted mode. **Owner to decide** before release:
  keep it, or swap the PDF fallback to a permissive library (e.g. pypdfium2).
- **Note for the orchestrator (M4):** politeness spacing is per `PoliteClient` instance, so the
  server must share one client per process.

## Measured request budgets

_None yet — filled in from real `portolan record` runs (M4)._

## TODO(owner)

- Replace the placeholder compass-rose logo (`frontend/src/design/Logo.tsx`) with your own drawing.

## Ideas (not in spec; not built)

_None yet._
