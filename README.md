# Portolan

**Explore any paper and its code.** Paste a research paper. Portolan finds its code, turns the paper
into a map of concepts you can jump into down to first principles, walks you through the paper in
the order it was written, and shows where every concept lives in the code — and where the code
disagrees with the paper.

> Status: early development (Milestones 0–3 done: foundations, paper ingestion, repo finder, code index). See [docs/PROGRESS.md](docs/PROGRESS.md).
> The full specification is [PORTOLAN_SPEC.md](PORTOLAN_SPEC.md); the architecture diagram is
> [docs/architecture.drawio](docs/architecture.drawio) (open in diagrams.net).

## Quickstart (development)

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), and Node 20+ with pnpm.

1. `make setup` (or `cd backend && uv sync`, then `cd frontend && pnpm install`)
2. Copy `.env.example` to `.env`. The default `PORTOLAN_LLM_PROVIDER=mock` needs no key.
3. `make test`, then `cd backend && uv run portolan doctor` to check your setup.
4. Try ingestion (no key needed): `uv run portolan ingest https://arxiv.org/abs/1706.03762 --dry-run`

### Adding a Gemini key (needed from Milestone 4)

Get a free key at [aistudio.google.com](https://aistudio.google.com), put `GEMINI_API_KEY=...` in
your local `.env`, set `PORTOLAN_LLM_PROVIDER=gemini`, copy `portolan.toml.example` to
`portolan.toml`, and run `uv run portolan doctor` — it lists the models your key can use and
prints a suggested `[llm.models]` section. Never commit `.env` or paste your key anywhere.

Free Gemini keys may let Google use what you send. Don't analyze private code with a free key.

## License

MIT — see [LICENSE](LICENSE).
