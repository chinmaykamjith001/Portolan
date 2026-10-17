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

## M1 — Paper ingestion ✅ (2026-10-17)

**Plan**
- `models/paper.py`: `PaperRef`, `Anchor`, `PaperDoc` (§9.1).
- `ingest/anchors.py`: one `DocBuilder` shared by all parsers — assigns stable ids
  (`sec:3.2.1`, `para:3.2.1:2`, `eq:1`, `tab:3`, `fig:2`, `alg:1`), labels, document order,
  parents, resolves cross-references into `equation_refs`, extracts the notation table.
- `ingest/http.py`: polite client — per-host spacing (arXiv ≥ 3 s), descriptive User-Agent,
  SQLite response cache, retry on 429/5xx.
- `ingest/resolver.py`: arXiv URLs/IDs (abs/pdf/html, ar5iv, alphaxiv, HF papers, old-style IDs),
  DOIs (arXiv DOIs directly; others via Semantic Scholar), titles via the arXiv API (top 3, user confirms).
- Parsers: `parse_html.py` (LaTeXML: arxiv.org/html and ar5iv), `parse_latex.py` (flatten
  `\input`/`\include`, strip comments, expand simple macros, sections/equations/labels/refs),
  `parse_pdf.py` (PyMuPDF, font-size heuristics, `low_structure`).
- `ingest/pipeline.py`: metadata from the arXiv API, then HTML → LaTeX → PDF; store `PaperDoc` in SQLite.
- CLI: `portolan ingest <input|file.pdf> [--dry-run]` prints the section tree and anchor counts.
- Tests: synthetic HTML/LaTeX fixtures, generated PDF, resolver with mocked HTTP, http client
  politeness/caching; `-m network` test on both development papers.

**Done / verified**
- `ruff`, `pyright` (strict), `pytest`: 98 tests pass (66 new). Synthetic HTML and LaTeX fixtures
  cover nested sections, `\paragraph` headings, numbered/unnumbered/grouped equations, align rows
  with `\nonumber`, `\input` flattening (cycle-safe), comment stripping, `\iffalse`/comment blocks,
  macro expansion (args, `\def`, `\DeclareMathOperator`, protected names), `\ref`/`\eqref`/`\cref`
  rendering, floats, appendices, bibliography, notation extraction. A PDF generated at test time
  covers the PDF fallback. Mocked HTTP covers the resolver (arXiv URL forms, DOIs via Semantic Scholar,
  titles), politeness spacing, caching/TTL, 429/5xx retry, tarball safety, and every pipeline fallback.
- **Network test (`pytest -m network`) passes** for both development papers from real arXiv
  (1706.03762v7 and 2106.09685v2, both via arxiv.org/html).
- Ran the LaTeX parser by hand on both papers' real e-print sources: same section tree and
  equation numbering as the HTML path.
- `portolan ingest https://arxiv.org/abs/1706.03762 --dry-run` prints the full section tree
  (28 sections, 61 paragraphs, 5 equations, 4 tables, 5 figures) and finds the tensor2tensor link.

**Not done / known gaps**
- No `POST /api/papers` endpoint yet: ingestion is CLI-only until the orchestrator and SSE exist (M4/M5).
- `equation_refs` is empty for both development papers: neither paper cross-references its
  equations with `\ref`, so this is correct, but it is only exercised by the fixtures.
- The PDF heuristics are only tested on a generated PDF, not a real paper PDF.

**Next: M2 — Repo finder** (paper links, HF Papers API, GitHub search; validation; RepoStatus; uploads).

## M2 — Repo finder ✅ (2026-10-17)

**Plan**
- `models/repo.py`: `RepoCandidate`, `RepoStatus` (§9.2).
- `repo/links.py`: GitHub/GitLab URL normalization; "release" context detection around a link.
- `repo/github.py`, `repo/gitlab.py`: thin API clients over `PoliteClient` (token from `GITHUB_TOKEN`,
  rate-limit errors in plain words).
- `repo/finder.py`: sources in confidence order — paper links (+ project pages), HF Papers API,
  GitHub search for the arXiv id (then title) in READMEs; merge; validate (exists/dead/renamed,
  placeholder, official vs. likely-official vs. unofficial); rank; `RepoStatus` message.
- `repo/upload.py`: zip upload (safe extraction into the workspace) and local folder path.
- Storage: candidates + chosen repo per paper (migration 3). CLI: `portolan repos <paper>`.
- Tests: mocked HTTP for official / unofficial / placeholder / dead / none, renames, rate limits,
  zip safety; `-m network` test on both development papers.

**Done / verified**
- `ruff`, `pyright` (strict), `pytest`: 144 tests pass (46 new). Mocked HTTP covers official,
  unofficial, placeholder, empty, dead, none, renamed repos, Hugging Face listings with and without
  an author match, third-party mentions, project pages, GitLab, GitHub rate limits, and token
  handling. Zip uploads are tested for zip-slip, symlinks, bombs and bad zips; local folders and
  storage are covered too. CLI: `portolan repos` lists, picks, reuses stored results, `--use`, `--upload`.
- **Network test (`pytest -m network tests/test_network_repos.py`) passes**, anonymously against GitHub:
  1706.03762 → official `tensorflow/tensor2tensor`; 2106.09685 → official `microsoft/LoRA`.
- M3 groundwork: external facts for tree-sitter, bge-small embeddings, FTS5 and safe shallow
  clones are verified in `docs/notes/m3-externals.md`.

**Not done / known gaps**
- No `GET/POST /api/papers/{id}/repo...` endpoints yet (they need the papers API from M4/M5).
- Papers with Code archive not used (spec MAY).
- Test suite setup is slow (~0.3–0.9 s per test creating HTTPS clients; ~45 s total). Worth trimming later.

**Next: M3 — Code index** (clone, tree-sitter index, config indexing, FTS5 + local embeddings,
hybrid retrieval, library-call detection).

## M3 — Code index ✅ (2026-10-17)

**Plan**: shared models (`models/code.py`), then four parallel pieces — cloner, Python indexer,
config indexer, FTS5 store + hybrid retrieval — integrated by `code/indexer.py` and `portolan index`.

**Done / verified**
- `ruff`, `pyright` (strict), `pytest`: 241 tests pass. Acceptance: on the fixture repo, all 7 known
  concepts (scaled dot-product attention, multi-head attention, positional encoding, label smoothing,
  warmup, residual + layer norm, feed-forward) are in the top 3, BM25-only and hybrid. A synthetic
  200-file repo indexes in well under the 60 s budget.
- Cloner tests: shallow, SHA recorded, >1 MB file never checked out, planted hook never runs, no
  submodule contents, reuse, clear errors, content hash for uploads.
- Real run: `portolan repos 2106.09685` → microsoft/LoRA; `portolan index 2106.09685` cloned and
  indexed 854 files / 23,412 symbols in 22.7 s and answered queries (e.g. "lora alpha scaling" →
  the `--lora_alpha` arguments and `loralib/layers.py`).

**Not done / known gaps**
- Real sentence-transformers embeddings not run (optional extra not installed); hashed embeddings in tests.
- Retrieval vocabulary gap on real repos (see DECISIONS); LLM rerank arrives with M6 grounding.
- Test suite takes ~70 s (per-test HTTPS client setup); worth trimming.

**Next: M4 — Chart generation** (first real LLM use).

## M4 — Chart generation ✅ (2026-10-17)

**Plan**: contracts first (concept models, output schemas, migration 5), then parallel sessions for
prompt templates, the orchestrator queue + SSE bus, and the chart store/canonicalization/issue
roll-up; the builder, record CLI, API and recording done here.

**Done / verified**
- `ruff`, `pyright` (strict), `pytest`: 323 tests pass. API flow test (fake LLM): paste → ingest →
  master layer (invented anchors and links removed, section-name concept dropped) → background
  frontier two levels deep → layer and concept endpoints → SSE events.
- **Real recordings** with the owner's free key: LoRA (14 concepts, 61 child layers, 12 requests)
  and Attention (13 concepts, 55 child layers, 11 requests). Concepts are ideas, e.g. "Intrinsic Rank
  Hypothesis", "Weight Merging at Inference", "Scaled Dot-Product Attention", "Label Smoothing".
- **Acceptance replay** (`pytest -m network tests/test_network_chart_replay.py`): both papers
  rebuild entirely from fixtures with 10-20 concepts, no structural section names, labeled links,
  and two skeleton levels below every decomposable concept; zero real requests.

**Not done / known gaps**
- Rate-limit events aren't routed to per-paper SSE yet; PDF multipart upload endpoint deferred to M5.
- A few orphaned skeleton fixtures remain from the discarded first Attention run (harmless).

**Next: M5 — Frontend shell + Chart view.**
