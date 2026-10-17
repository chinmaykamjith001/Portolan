# Prompt templates

One module per template in `backend/portolan/llm/prompts/`, each exposing a `PromptTemplate` with
`id`, `version`, `render(inputs)`, output `schema` and `temperature`. Bump `version` whenever the
rendered prompt or schema changes — that invalidates only that template's cache entries and
fixtures. Rendered prompts are snapshot-tested.

| Template | Version | Role | Milestone | Status |
|---|---|---|---|---|
| `master_layer` | – | strong | M4 | not written |
| `skeleton_batch` | – | fast | M4 | not written |
| `canonical_same_concept` | – | fast | M4 | not written |
| `route_section` | – | fast | M7 | not written |
| `sub_route` | – | fast | M7 | not written |
| `explain` | – | default | M5/M6 | not written |
| `explain_angle` | – | default | M6 | not written |
| `edge_explain` | – | default | M5/M6 | not written |
| `ground_code` | – | default | M6 | not written |
| `rerank_spans` | – | fast | M3/M6 | not written |
| `example_program` | – | default | M6 | not written |
| `graph_program` | – | default | M6 | not written |
| `claims_extract` | – | strong | M8 | not written |
| `claim_judge` | – | strong | M8 | not written |
| `reverse_scan` | – | strong | M8 | not written |
| `faq_answer` | – | default | M9 | not written |
| `ask_answer` | – | default | M9 | not written |
