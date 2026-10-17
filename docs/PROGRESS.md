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
