# Evaluation (SciCoQA)

Measures Portolan's verification engine (paper claims against the linked code, spec §10.9) on
[SciCoQA](https://github.com/UKPLab/scicoqa), a benchmark of real and synthetic paper-code
discrepancies (spec §17).

**No number appears in this repository until an actual run produced it.** `eval/reports/` holds
only reports from real runs. A dry run prints a report stamped "TEST RUN — not a result" to the
terminal and never writes it there.

## Read this first: what is unverified

* **The dataset format.** This harness was written without access to the SciCoQA repository, so
  the field names it looks for are guesses (a documented alias table), not the official schema.
  Before trusting any number, run

  ```
  git clone https://github.com/UKPLab/scicoqa ~/data/scicoqa
  cd backend && uv run portolan eval --inspect ~/data/scicoqa
  ```

  and compare the "detected" and "not found" lines with the dataset's README. If a field is
  missing or wrong, edit `ALIASES` (or add an adapter function) in
  `backend/portolan/evalharness/adapters.py`. That is the only module that knows the format.
* **The matcher.** `scoring.py` matches predictions to gold discrepancies by code location, then by
  word overlap. It is not the benchmark's own scorer, which is probably judged differently. If the
  official definition of recall, precision or matching differs, implement it as a `Matcher` and
  say so in the report.
* **Commit pinning.** The repository is cloned at its default branch HEAD, not the commit the
  dataset names. Records where they differ are listed in the report's setup notes.

## Running

```
make eval ARGS="--data ~/data/scicoqa --limit 25"
```

Needs a Gemini key in `.env`, `PORTOLAN_LLM_PROVIDER=gemini`, and real model ids in
`portolan.toml` (`portolan doctor`). The command prints a request estimate first and asks before
spending more than 30 requests (`--yes` skips the question).

| Option | Meaning |
|---|---|
| `--data PATH` | Folder or file of the SciCoQA download (required for a real run). |
| `--limit N` | Records to run, default 25 (`0` = all). |
| `--split all\|real\|synthetic` | Which records to sample from. |
| `--seed S` | Sampling seed. Same data, split, limit and seed always pick the same records. |
| `--yes` | Don't ask before spending requests. |
| `--fresh` | Ignore saved results and start over. |
| `--no-retry-failed` | Don't re-run records that failed last time. |
| `--out DIR` / `--report-dir DIR` | Checkpoint folder (default `eval/runs/`, git-ignored) and report folder (default `eval/reports/`). |
| `--inspect PATH` | Show how the dataset's fields are read, then stop. |
| `--dry-run` | Use the fake engine on `eval/scicoqa/sample/`. No requests. Not a result. |

### The free tier

A free key allows about 1,400 requests a day and a record costs roughly 10-15. So 25 records
take a day or two. The run is resumable: every finished record is saved to
`eval/runs/results.jsonl` (keyed by record and engine version), so running the same command again
skips them. When the daily budget runs out the run stops cleanly, tells you how many records are
left, and continues after midnight US Pacific time. The LLM cache makes repeated calls free. One
record failing (paper not found, repo gone) is recorded and the rest continue; failed records are
retried on the next run unless you pass `--no-retry-failed`.

Changing the verification engine or its prompts changes the engine version, so old results are
kept but never reused.

## Scoring

See the module docstring of `backend/portolan/evalharness/scoring.py` for the exact rule. In short:
a *prediction* is a `mismatch` or `undocumented_in_paper` finding; it matches a gold discrepancy if
it points at the gold file within 5 lines of the gold lines, or its description shares enough
words with the gold description; matching is one-to-one. Recall is matched gold over all gold;
precision is matched predictions over all predictions, a lower bound when the gold list is
incomplete. Only finished records count.

## Report

`eval/reports/<date>.md` contains the command, dataset path, sample size, seed and engine version,
overall and per-split/category recall, per-claim-type precision, which check (literal match,
static, LLM judge) decided each matched finding, examples of found and missed discrepancies,
the real requests used, and any records that failed or did not run.
