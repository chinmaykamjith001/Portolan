# CLAUDE.md — Portolan

Portolan: paste a paper → find its code → Chart (concept map you can jump into to first principles)
+ Route (the paper as an S-shaped road) + code grounding with line annotations + mismatch findings + FAQ.
The full spec is PORTOLAN_SPEC.md. It wins over anything here.

## Non-negotiables
- All LLM calls go through backend/portolan/llm/gateway.py. Never call LiteLLM directly.
- Develop and test with PORTOLAN_LLM_PROVIDER=mock. Never make real LLM calls without the owner's go-ahead;
  give them the exact `portolan record ...` command and a request estimate instead.
- The owner has a FREE Gemini key: respect rate limits, cache everything, batch, skeleton-ahead/detail-on-demand.
- Never ask for, print, log or write the API key. It lives only in the owner's local .env.
- Numbers in examples/graphs come from the sandbox, never from LLM text.
- Design: tokens only, IBM Plex Sans/Mono, minimal. No gradients, blur, emoji, default Tailwind colors, card grids.
- Diagrams are .drawio files in docs/, never inline in Markdown.
- Work milestone by milestone (spec §18). Update docs/PROGRESS.md; log choices in docs/DECISIONS.md.

## Commands
- make setup | make dev | make test | make lint | make typecheck
- uv run portolan doctor | serve | ingest <input> --dry-run | record ... | eval --limit 25
