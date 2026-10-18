"""SciCoQA evaluation harness (spec §17, M10).

Pieces, in the order a run uses them: `adapters` (read the dataset into `records`), `sampling`,
`estimate`, `runner` (per-record isolation, resumable through `checkpoint`), `scoring`, `report`.
`real` holds the engine that uses Portolan's own pipeline; `fake` is for dry runs and tests.
"""
