# Prompt templates

One module per template in `backend/portolan/llm/prompts/`, each exposing a `PromptTemplate` with
`id`, `version`, `render(inputs)`, output `schema` and `temperature`. Bump `version` whenever the
rendered prompt or schema changes — that invalidates only that template's cache entries and
fixtures. Rendered prompts are snapshot-tested.

| Template | Version | Role | Milestone | Status |
|---|---|---|---|---|
| `master_layer` | 2 | strong | M4 | written |
| `skeleton_batch` | 2 | fast | M4 | written |
| `canonical_same_concept` | 1 | fast | M4 | written |
| `route_section` | 1 | fast | M7 | written |
| `sub_route` | – | fast | M7 | not written |
| `explain` | 1 | default | M5 | written (default angle) |
| `explain_angle` | – | default | M6 | not written |
| `edge_explain` | 1 | default | M5 | written |
| `ground_code` | 1 | default | M6 | written |
| `rerank_spans` | – | fast | M3/M6 | not written |
| `example_program` | 1 | default | M6 | written |
| `graph_program` | 1 | default | M6 | written |
| `claims_extract` | 1 | strong | M8 | written |
| `claim_judge` | 1 | strong | M8 | written |
| `reverse_scan` | 1 | strong | M8 | written |
| `faq_answer` | 1 | default | M9 | written (batches of ≤ 6 clusters, P2) |
| `faq_anticipated` | 1 | default | M9 | written (one batched call, P2) |
| `ask_answer` | 1 | default | M9 | written (the Ask box, P0) |
