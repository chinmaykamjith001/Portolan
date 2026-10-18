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

## M2 — Repo finder (2026-10-17)

- **Hugging Face Papers API verified** on real responses: `GET https://huggingface.co/api/papers/{id}`
  returns `githubRepo` (a full URL), `githubStars`, `githubRepoAddedBy` (e.g. `"auto"`) and
  `projectPage` only when set; neither development paper has one. Unknown ids return 404.
- **GitHub API verified** on real responses: `/repos/{o}/{r}` (`full_name`, `owner.type`,
  `stargazers_count`, `pushed_at`, `default_branch`, `fork`); anonymous search
  `/search/repositories?q="<arXiv id>" in:readme&sort=stars` works (60 requests/hour; search 10/min).
  Code search would need a token, so we search READMEs via repository search instead.
- **Placeholder detection** uses the recursive git tree (`/git/trees/{branch}?recursive=1`; 409 means
  an empty repo) plus the README: no source files, or ≤ 3 source files with "coming soon"-style
  wording, is a placeholder.
- **Official vs. unofficial, precision first:** a repo linked in the paper counts as official only when
  it's in the abstract, next to release wording ("our code is available at…"), or on the paper's
  project page. A repo merely mentioned in the paper (often a baseline or tool) stays *unverified*
  (`official=None`). Hugging Face listings stay unverified unless the owner matches an author. Owner ↔
  author matching uses the GitHub display name (family name + first initial) or the login
  (`edwardjhu`, `ehu…` with family names of 4+ letters). GitHub-search results are *unofficial*
  unless the owner matches an author. Only `official=True` produces "Official code found".
- **Ranking:** dead last; then official, unverified, unofficial; then usable before placeholder; then
  source confidence (paper link, Hugging Face, search); README cites the arXiv id; stars; recency.
- **Request budget:** at most 6 candidates are validated per paper, and owner-name lookups for search
  results are limited to the top 2. That's about 15 GitHub requests per paper anonymously; results
  are cached for a day.
- **Not used:** the Papers with Code archive (spec "MAY"); can be added as a fourth source later.
- **Fields added to `RepoCandidate`:** `host`, `default_branch`, `cites_paper`, `status_reason`,
  `local_path`.
- **Uploads:** zips are extracted into `~/.portolan/workspace/<paper>/upload-<sha>/`, skipping
  absolute or `..` paths, symlinks, `.git`/`__MACOSX`/`node_modules`, files over 50 MB, and refusing
  more than 20,000 entries, more than 500 MB unpacked, or a compression ratio over 200. Local folders
  are used in place, read-only. The future HTTP endpoint must only accept folder paths from localhost.
- **HTML link text:** external links whose visible text isn't the URL now keep it in the paragraph
  text ("our code (https://github.com/…)"), so the finder can read the words around them.

## M3 — Code index (2026-10-17)

Verified facts behind these choices: [docs/notes/m3-externals.md](notes/m3-externals.md).

- **Parsers: `tree-sitter` 0.26 + standalone grammar wheels** (`tree-sitter-python`, `-yaml`, `-json`,
  `-toml`) instead of `tree-sitter-language-pack`. Pack 1.x downloads a 22 MB parser archive at first
  use (breaks offline installs; fails with `DownloadError`); 0.x is 170 MB unpacked and its grammar
  shape differs. The grammar wheels are 0.1 MB each, MIT, fully offline, and pinned. Only Python is
  required in v1; adding languages later means adding their grammar wheels.
- **tree-sitter on Windows:** `Point.row` on 0.26.0 was seen to corrupt memory in this environment,
  so the indexer reads points by index (`point[0]`). Docstrings are read in Python, not in queries
  (grammar-shape differences).
- **Embeddings are an optional extra** (`uv sync --extra embeddings` → sentence-transformers +
  bge-small-en-v1.5, ~750 MB with CPU torch, ~29 s import). Without it, retrieval is BM25-only;
  `HashEmbedder` (hashed bag of words) is used in tests. The import is lazy. Not yet run with real
  weights. The fastembed/ONNX route (126 MB) is noted as an alternative; vectors from different routes
  must not be mixed in one index.
- **Retrieval:** FTS5 with `unicode61 tokenchars '_'`, an extra `name_parts` column with snake/Camel
  splits, column weights name 5 / name_parts 4 / docstring 2 / body 1 / path 0.5, user text sanitised
  into quoted tokens; hybrid = reciprocal-rank fusion (k=60) of BM25 and cosine. Module symbols index
  only their docstring (otherwise whole files outrank the function that implements a concept). The LLM
  rerank (§10.4 step 4) is a hook, wired in M6.
- **Cloning:** `--depth 1 --single-branch --no-tags --no-recurse-submodules --filter=blob:limit=1m
  --no-checkout`, empty `--template=` and `core.hooksPath`, `protocol.file/ext.allow=never`, then a
  sparse checkout that excludes the omitted >1 MB blobs (a plain checkout would lazily fetch them).
  Uploads and local folders get a `content:` hash instead of a SHA.
- **Walk guards:** skips vendored/generated folders (`third_party`, `vendor`, `node_modules`, `build`,
  `.venv`, …), symlinks, binaries, files over 1 MB, lockfiles, and caps at 5,000 files. Windows paths
  use the `\\?\` extended-length prefix (the LoRA repo has paths over 260 characters).
- **Library detection:** an import is a library when its top-level module isn't a file/folder name in
  the repo and isn't stdlib; calls are qualified through import aliases (`F.softmax` →
  `torch.nn.functional.softmax`).
- **Real run (microsoft/LoRA @ c4593f0):** 854 files (815 Python, 7 notebooks, 32 configs), 23,412
  symbols, 22.7 s including storage. Known gap: paper words vs code names ("low-rank adaptation" vs
  `LoRALayer`) defeat BM25 alone; embeddings + the M6 LLM rerank are meant to close it. Bulky example
  folders (a vendored copy of transformers under `examples/`) also add noise; consider a path prior in M6.

## M4 — Chart generation (2026-10-17)

- **Count limits live in the schema:** `MasterLayerOut.concepts` must have 10-20 items and each
  skeleton layer 4-10 (Pydantic `min_length`/`max_length`). The first real call returned 1 concept
  when the count was only stated in the prompt; with the schema limit, a short answer fails
  validation and gets the gateway's one repair retry. Template versions were bumped to 2. Worked
  examples in prompts are built with `model_construct` because they are deliberately short.
- **"Not section names" means structural names only.** Dropping every concept that matches a
  section title removed Attention's core ideas ("Scaled Dot-Product Attention", "Label Smoothing" are
  subsection titles *and* the right concepts). Now only generic names ("Results", "Training",
  "Experimental Setup": titles made only of generic words) are dropped.
- **Validation of LLM structure:** anchors not in the paper are removed; links whose ends are
  unknown, self-loops and duplicates are dropped (and reported as warnings); a child layer never
  contains its own center or ancestors; shared concepts keep their smallest depth.
- **Reasoning effort per template:** master `medium`, skeleton `low`, same-concept `minimal`
  (`PromptTemplate.reasoning` → LiteLLM `reasoning_effort`).
- **Frontier:** ingest builds master + two skeleton levels (P1 after the P0 master). Opening a layer
  re-queues its frontier at the front of P1; jumping into a concept without a layer is a P0 job.
- **API in M4:** `POST /api/papers` resolves the input (and arXiv version) synchronously so it can
  return the versioned `paper_id`, then enqueues ingest. Titles return `needs_choice` with
  candidates. PDF upload via multipart waits for M5 (it needs `python-multipart`).
- **Record mode** writes fixtures for cached answers too, so a re-record never misses calls. Mock
  fixtures are keyed by template version + inputs hash; the replay test needs the same model ids in
  config because the response cache key includes the model.
- **Rate-limit events** from the gateway aren't yet routed to a paper's SSE stream (the gateway has
  no paper context); to wire in M5 with the budget indicator.

## Gemini setup (2026-10-17)

- Owner's free key is in their local `.env`; `PORTOLAN_LLM_PROVIDER` stays `mock` there, and real calls
  are made only by explicit commands with `PORTOLAN_LLM_PROVIDER=gemini`.
- **Owner go-ahead:** the owner authorized real Gemini calls for recording fixtures without per-run
  approval ("take as many as you want"), within the free tier. The rate limiter's caps
  (8 RPM, 1,400 RPD) still apply. Every real run is logged below under "Measured request budgets".
- **Model listing verified** with `portolan doctor` against the real API: 46 models. Chosen roles
  (local `portolan.toml`, git-ignored): `fast = gemini/gemini-3.5-flash-lite`,
  `default = strong = gemini/gemini-3.8-flash` (newest non-preview IDs; 3.8 Flash-Lite exists only as TTS).
- **Smoke test** (2 requests, structured `{concept, gist}` output through the gateway): both roles returned
  valid JSON. Findings: (1) `gemini-3.8-flash` spent 485 output tokens (reasoning) on a 20-word answer,
  so M4 should set a low reasoning effort on skeleton/label calls to protect TPM; (2) LiteLLM warns that
  `temperature`/`top_p` are deprecated for Gemini 3+ — keep passing temperature for now, and move
  sampling guidance into system prompts if it's removed.

## Measured request budgets

| Date | Run | Requests |
|---|---|---|
| 2026-10-17 | Gateway smoke test (fast + default) | 2 |
| 2026-10-17 | LoRA master layer (v1 prompt, returned 1 concept; discarded) | 1 |
| 2026-10-17 | LoRA master layer (v2) | 1 |
| 2026-10-17 | LoRA skeletons, two levels (61 child layers) | 11 |
| 2026-10-17 | Attention master + skeletons (section filter too strict; partly discarded) | 8 |
| 2026-10-17 | Attention skeletons after the filter fix (55 child layers) | 10 |

Per paper, initial Chart processing took **12 requests (LoRA)** and **11 requests (Attention)**:
1 master plus 10-11 skeleton batches of 6. The §5.3 estimate was ~20 (1-2 + ~4 + ~15).

## TODO(owner)

- Replace the placeholder compass-rose logo (`frontend/src/design/Logo.tsx`) with your own drawing.

## Ideas (not in spec; not built)

_None yet._

## M5 infrastructure (2026-10-18)

- **PDF upload:** `POST /api/papers` also takes multipart form data (field `file`). Added
  `python-multipart` (FastAPI needs it to parse forms). The upload is checked for `%PDF` magic
  bytes and a size cap (`[storage] max_upload_mb`, default 50). Then it's saved to
  `<home>/uploads/<sha256[:16]>.pdf` and ingested by the `ingest` job. Paper ids are
  `upload:<sha256[:16]>`, the same scheme the CLI uses, so the same file always maps to the same
  paper.
- **Client fix:** `POST /api/papers` answers 202 with `{paper_id, status: "queued"}` for a new
  paper. `request()` used to treat every 202 as "pending" and dropped the body, so a new paper
  never opened. `createPaper`/`uploadPdf` now read the body of a 202.
- **Style guard** (`frontend/tests/style.test.ts`): scans `src/**/*.{css,ts,tsx}` for gradients,
  blur/backdrop-filter, color literals outside `tokens.css`, named colors, Inter, Tailwind palette
  classes, font weights other than 400/500/600, emoji, and `var(--x)` with no definition. It reads
  files with `node:fs` because Vitest hides CSS text from `?raw` imports.
- **`@types/node`** (dev only): needed by the Playwright config/specs and the style test. These
  files are type-checked by `tsconfig.node.json`, so Node globals (e.g. `setTimeout` returning
  `Timeout`) stay out of the app's tsconfig. `vite.config.ts` moved there too.
- **E2E data: real backend + seeded DB**, not `page.route()` stubs, so the flow exercises the
  real endpoints and SSE. `portolan seed-demo` (`backend/portolan/dev/seed.py`) writes a fixed
  demo paper (`demo:toyformer`: 3 levels, every grounding, one mismatch, explanations and edge
  explanations for everything) with no network and no LLM. Every concept without a layer is a
  first principle, so opening layers never queues LLM work. Playwright starts the backend on
  :8765 (fresh temp `PORTOLAN_HOME`, empty config, `PORTOLAN_NO_DOTENV=1`, mock provider) and
  Vite on :5174 (`PORTOLAN_API_URL` points the proxy at it).
- **Chromium:** if `/opt/pw-browsers/chromium` exists, the config uses it (that container ships
  build 1194; @playwright/test 1.64 expects 1248). `PW_CHROMIUM` overrides it. CI runs
  `playwright install --with-deps chromium`.
- **Perf page:** `frontend/e2e/perf.html` (dev server only; `vite build` builds only index.html)
  renders `ChartCanvas` with `syntheticLayer(300)`. Measured in headless Chromium in the cloud
  container, dev build: median 59.9 fps while panning (p95 frame 17-33 ms). The assertion is a
  median of at least 50 fps.

