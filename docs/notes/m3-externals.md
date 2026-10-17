# M3 externals: verified facts

Research for Milestone 3 (code index, spec §10.4 and §6). Checked 2026-10-17 on Windows 11, Python 3.12.10,
uv 0.12.13, git 2.55.0.windows.5. Probes ran in throwaway venvs under the system temp dir; the repo's
`pyproject.toml` and `uv.lock` were not touched.

**Tags:** `[RAN]` = I ran it here. `[DOCS]` = taken from the linked docs or metadata, not executed.

## Headline findings

1. **`tree-sitter-language-pack` 1.x (current: 1.22.1) no longer ships parsers.** The first `get_parser("python")`
   downloads a 22 MB all-languages archive from GitHub Releases and caches a DLL under `%LOCALAPPDATA%`. M3 needs
   a network at that moment, so `make setup` or a `portolan doctor` step should pre-fetch it. Alternatives are in §1.4.
2. **The Python grammar differs between sources.** Docstring queries written for one source return nothing or fail
   on another. Extract docstrings in Python, not in the query (§1.3).
3. **`sentence-transformers` is heavy and slow to import.** About 0.75–0.86 GB installed, with a 29 s import on this
   machine. Import it lazily and keep it off the CLI and test paths (§2).
4. **FTS5 and `bm25()` work** in the bundled Python 3.12 sqlite3 (3.49.1).
5. **A safe shallow clone works** with the flags in §4. `--filter=blob:limit=1m` does not by itself keep big files
   out of the working tree.

---

## 1. tree-sitter and tree-sitter-language-pack

### 1.1 Versions, wheels, licences

| Package | Latest on PyPI | Win cp312 wheel | Licence |
|---|---|---|---|
| `tree-sitter` | **0.26.0** (2026-06-30) | `cp312-cp312-win_amd64`, 0.1 MB | MIT |
| `tree-sitter-language-pack` | **1.22.1** (2026-10-12) | `cp310-abi3-win_amd64`, 2.2 MB | MIT |
| `tree-sitter-language-pack` (last 0.x) | 0.13.0 (2025-11-26) | `cp310-abi3-win_amd64`, 16.1 MB | MIT OR Apache-2.0 |
| `tree-sitter-python` (standalone grammar) | 0.25.0 (2025-09-11) | `cp310-abi3-win_amd64`, 0.1 MB | MIT |

- `[RAN]` All four installed and imported in a fresh Python 3.12.10 venv. ABI levels: `LANGUAGE_VERSION` 15,
  `MIN_COMPATIBLE_LANGUAGE_VERSION` 13.
- `[DOCS]` Wheel lists and licences are from the PyPI JSON API: https://pypi.org/pypi/tree-sitter/json,
  https://pypi.org/pypi/tree-sitter-language-pack/json, https://pypi.org/pypi/tree-sitter-python/json.
  Source repos: https://github.com/tree-sitter/py-tree-sitter and
  https://github.com/xberg-io/tree-sitter-language-pack (PyPI and the release manifest now point at `xberg-io`;
  the README page I fetched was under `kreuzberg-dev`).
- `[DOCS]` The pack README says all included grammars are permissively licensed.
- `[RAN]` `uv pip install --dry-run` against `backend/.venv` resolves with no conflicts for
  `tree-sitter tree-sitter-python tree-sitter-language-pack sentence-transformers`.
  It would add 18 packages (numpy 2.5.4, torch 2.14.1, scipy 1.18.1, transformers 5.20.1, tree-sitter 0.26.0, ...).

### 1.2 Getting a Python parser

```python
# A) tree-sitter-language-pack (the spec's choice)
import tree_sitter_language_pack as tslp
parser = tslp.get_parser("python")      # returns tree_sitter.Parser
lang   = tslp.get_language("python")    # returns tree_sitter.Language

# B) standalone grammar wheel (parser compiled into the wheel, nothing downloaded)
import tree_sitter_python
from tree_sitter import Language, Parser
lang = Language(tree_sitter_python.language())
parser = Parser(lang)
```

`[RAN]` Both work.

**Language-pack 1.22.1 behaviour** (`[RAN]` unless tagged):

- First `get_parser("python")` took 1.2 s on a good connection. It fetched
  `https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.22.1/parsers-windows-x86_64.tar.zst`
  (22,021,182 bytes, sha256 in the wheel's manifest) and wrote `tree_sitter_python.dll` (796 KB) to
  `%LOCALAPPDATA%\tree-sitter-language-pack\v1.22.1\libs\`.
- The cache location is configurable: `tslp.configure(tslp.PackConfig(cache_dir=...))`.
- Pre-fetch with `tslp.download(["python", "yaml", "toml", "json"])` or `tslp.prefetch([...])`.
  `tslp.downloaded_languages()` lists what is cached.
- Offline with an empty cache, `get_parser` fails after 2 s with `tree_sitter_language_pack.DownloadError`
  ("Failed to fetch manifest from .../parsers.json"). The pack's own wheel is only about 2 MB.
- The manifest lists 372 languages, including python, yaml, toml and json (the config formats the spec wants indexed).
- The 1.x line also adds a `process()` code-intelligence API (structure, docstrings, imports). I did not evaluate it.
  The plain `Parser` and `Query` route below is enough for M3.

### 1.3 Current Query API (py-tree-sitter 0.25–0.26)

Changes, from the release notes (`[DOCS]`: https://github.com/tree-sitter/py-tree-sitter/releases) and API docs
(https://tree-sitter.github.io/py-tree-sitter/classes/tree_sitter.QueryCursor.html):

- 0.23: `Query.captures()` returns `dict[str, list[Node]]` and `Query.matches()` returns
  `list[tuple[int, dict[str, list[Node]]]]`.
- 0.25: `Query.captures` and `Query.matches` moved to `QueryCursor(query)`.
  `Language.query(src)` was deprecated in favour of `Query(language, src)`.
- 0.26: `Language.query` is removed. `[RAN]` Calling it gives `AttributeError`.

So the working form is `Query(lang, src)` plus `QueryCursor(query).matches(node)` or `.captures(node)`.
`matches()` groups captures per definition. `captures()` flattens them across matches.

**Working snippet** `[RAN]`. It passed on tree-sitter 0.26.0 with all three parser sources: tree-sitter-python
0.25.0, language-pack 0.13.0 and language-pack 1.22.1.

```python
from tree_sitter import Language, Node, Parser, Query, QueryCursor
import tree_sitter_python

lang = Language(tree_sitter_python.language())
parser = Parser(lang)

DEFS = Query(lang, """
(function_definition name: (identifier) @name) @def
(class_definition   name: (identifier) @name) @def
""")

def docstring(defn: Node) -> str | None:
    """First statement of the body, wrapped in expression_statement or not (grammar-agnostic)."""
    body = defn.child_by_field_name("body")
    first = body.named_children[0] if body and body.named_children else None
    if first is not None and first.type == "expression_statement" and first.named_children:
        first = first.named_children[0]
    if first is None or first.type != "string":
        return None
    return "".join(c.text.decode() for c in first.children if c.type == "string_content").strip() or None

tree = parser.parse(source_bytes)
for _, caps in QueryCursor(DEFS).matches(tree.root_node):
    node, name = caps["def"][0], caps["name"][0].text.decode()
    print(node.type, name, node.start_point.row + 1, node.end_point.row + 1,   # rows are 0-based
          node.start_byte, node.end_byte, docstring(node))
```

Output for a sample module with a class, two methods (one `async`) and a decorated function:

```
class_definition     Net       L5-13  bytes 30-184  doc='A network.'
function_definition  forward   L8-10  bytes 73-133  doc='Run it.'
function_definition  aforward  L12-13  bytes 139-184  doc=None
function_definition  train     L17-18  bytes 198-238  doc=None
```

Gotchas `[RAN]`:

- **Grammar shape differs.** For `def f(x):\n """doc"""\n return x`:
  - `tree-sitter-python` 0.25.0: `(block (expression_statement (string ...)) ...)`
  - language-pack 0.13.0 and 1.22.1: `(block (string ...) ...)`. There is no `expression_statement` wrapper, so the
    docstring is the first child of `block`.

  A query like `body: (block . (expression_statement (string) @doc))` works on tree-sitter-python 0.25.0,
  raises `QueryError: Impossible pattern` on language-pack 0.13.0, and matches nothing (no error) on 1.22.1.
  That is why the snippet reads the docstring in Python. Pin one parser source and add a golden test for the
  docstring shape.
- **Decorators.** A decorated function's span excludes the decorator lines (`train` above starts at its `def`).
  Its parent is `decorated_definition`, which has the full span. Use the parent's span if the decorator lines
  should be annotated.
- **Nested defs.** `matches` returns nested functions and methods too, so decide whether the index wants them flat.
- **Bytes.** `parse()` takes `bytes` and columns are byte offsets, so decode `node.text` as UTF-8.

### 1.4 Options for the Python parser

| Option | Wheel | Offline after install | Notes |
|---|---|---|---|
| `tree-sitter-python==0.25.0` + `tree-sitter` | 0.1 MB | yes | Smallest; pinned grammar; Python only. Config files would need `tomllib`, `json` and a YAML lib, or more grammar wheels. |
| `tree-sitter-language-pack<1` (0.13.0) | 16.1 MB (170 MB unpacked) | yes | Bundles many grammars. Pulls extra wheels (`tree-sitter-yaml` 0.7.2, `-c-sharp`, `-embedded-template`). |
| `tree-sitter-language-pack` 1.22.1 | 2.2 MB | **no**, needs one 22 MB download | Pre-fetch in `make setup` / `portolan doctor`; failure mode is `DownloadError`. |

The spec's choice (language-pack) works. Which option M3 takes is the owner's call.

---

## 2. sentence-transformers with BAAI/bge-small-en-v1.5

**Model** `[DOCS]` (https://huggingface.co/BAAI/bge-small-en-v1.5; file sizes from
https://huggingface.co/api/models/BAAI/bge-small-en-v1.5?blobs=true; I did not download the weights, so no
embedding was run):

- Licence **MIT** (HF tag `license:mit`, model card).
- Dimension **384** (`config.json` `hidden_size` and `1_Pooling/config.json` `word_embedding_dimension`).
  Max sequence length 512. BERT with 12 layers. Pooling is CLS, followed by L2 normalisation (`modules.json` has a
  `Normalize` step). The card lists 33.4M parameters.
- Files: `model.safetensors` 133,466,304 bytes (127.3 MiB, fp32); tokenizer files about 0.9 MiB; the repo also has
  `pytorch_model.bin` (127.3 MiB) and `onnx/model.onnx` (126.9 MiB). Expect about 130 MB in
  `~/.cache/huggingface/hub`, to be confirmed on first run.
- Retrieval: for v1.5 the query instruction `Represent this sentence for searching relevant passages: ` is
  optional. The card says dropping it costs a "slight degradation"; passages never get a prefix. Raw scores are not
  thresholdable, only their order.

**Packages** (`[RAN]` install into a clean venv; wheels from https://pypi.org/pypi/sentence-transformers/json,
https://pypi.org/pypi/torch/json):

- `sentence-transformers` **6.1.0** (2026-09-18, Apache-2.0, `py3-none-any`, 0.7 MB). It pulls `torch` **2.14.1**,
  `transformers` 5.20.1, `scipy` 1.18.1, `scikit-learn` 1.9.1, `numpy` 2.5.4, `huggingface-hub` 1.33.0 and
  `tokenizers` 0.23.3.
- **CPU-only on Windows by default.** The PyPI `torch-2.14.1-cp312-cp312-win_amd64.whl` is 118.4 MB and reports
  `torch.__version__ == '2.14.1+cpu'` with `torch.version.cuda is None`. No extra index is needed.
- **Footprint:** 42 packages, about **749 MB** of venv right after install (**857 MB** of site-packages measured
  after first import). Largest dirs: torch 498 MB, scipy 98 MB, transformers 57 MB, sympy 39 MB, sklearn 29 MB.
  Install took 224 s (133 s download, 88 s unpack).
- **Import time:** `import sentence_transformers` took **29 s** warm (two runs, 29.1 s and 29.4 s). The first cold
  run took 17 s for `import torch` plus another 55 s for `sentence_transformers`. Other sessions were running tests
  at the same time, so read these as upper bounds.
  Import it lazily inside the embedding function. A naive module-level import would slow `portolan doctor`, the
  CLI help and every pytest collection.
- Extras: `sentence-transformers[onnx]` pulls `optimum-onnx[onnxruntime]`. It still depends on torch, so it saves
  nothing.

**Lighter route to the same model** (report only; the spec's choice stands):

- `fastembed` **0.9.0** (Apache-2.0) + `onnxruntime` 1.31.0 (cp312 win_amd64 wheel, 14.2 MB): the whole venv is
  **126 MB** `[RAN]`, with no torch, scipy or sklearn.
- Caveat `[RAN: list_supported_models, no download]`: fastembed's `BAAI/bge-small-en-v1.5` entry resolves to
  `Qdrant/bge-small-en-v1.5-onnx-Q`, an **int8-quantised** ONNX file (0.067 GB, dim 384, MIT). It is the same model
  but not bit-identical to the fp32 weights, so vectors from the two routes should not be mixed in one index.
- Alternative: run `onnxruntime` + `tokenizers` yourself on the official fp32 `onnx/model.onnx` from the HF repo
  (126.9 MiB), with manual CLS pooling and L2 normalisation. This stays numerically close to the sentence-transformers output.

---

## 3. SQLite FTS5 (Python 3.12 bundled sqlite3, Windows)

`[RAN]` with `backend/.venv/Scripts/python.exe` (3.12.10). The system Python 3.12.10 reported the same SQLite
version and FTS5 compile options.

- `sqlite3.sqlite_version` = **3.49.1**. `compile_options` include `ENABLE_FTS3`, `ENABLE_FTS4` and
  `ENABLE_FTS5`. `fts5_source_id()` reports 2025-02-18. `enable_load_extension` is available.
- `create virtual table ... using fts5(...)` works, with `bm25()`, `highlight()` and `snippet()`.
  Tokenizers `unicode61`, `porter unicode61` and `trigram` all create fine.

```sql
create virtual table sym using fts5(name, doc, body, path unindexed,
                                    tokenize = "unicode61 tokenchars '_'");
select path, bm25(sym, 5.0, 2.0, 1.0, 0.0) as score        -- one weight per column, in declaration order
from sym where sym match ? order by score limit 20;         -- score is negative: lower is better
```

Behaviours that matter for the indexer:

- `bm25()` scores are **negative**, so order ascending. Per-column weights work (the `unindexed` column still
  takes a weight slot).
- **Unsanitised user text breaks `MATCH`**: `a.b(c` raises `sqlite3.OperationalError: fts5: syntax error near "."`.
  Quote each token, e.g. `'"a.b(c" OR "train_step"'`.
- With `tokenchars '_'`, snake_case identifiers are one token, so `train_step` matches but `train` does not.
  Use a prefix query (`train*`) or index split sub-tokens.
- `unicode61` does **not** split CamelCase. `multi` does not match `MultiHeadAttention`; only the whole
  lower-cased token does. Add the split sub-tokens (`multi head attention`) as an extra indexed column,
  or use the `trigram` tokenizer, which matches substrings like `Head` and `step`.

---

## 4. Safe shallow git clone

`[RAN]` with git 2.55.0.windows.5 (Git Bash). Public targets: `octocat/Hello-World` (tiny) and
`lucidrains/vit-pytorch` (11 MB repo with a 6.0 MB `images/vit.gif` and a 1.1 MB notebook, used to test the size
filter; MIT, https://github.com/lucidrains/vit-pytorch). Hook, submodule and sparse-checkout tests used a local
`file://` repo. Docs: https://git-scm.com/docs/git-clone, https://git-scm.com/docs/partial-clone.

### 4.1 Recommended command (all flags worked against GitHub)

```bash
GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 \
git -c core.hooksPath=<EMPTY_DIR> -c core.longpaths=true -c core.symlinks=false \
    -c protocol.file.allow=never -c protocol.ext.allow=never -c credential.helper= \
    -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 \
  clone --depth 1 --single-branch --no-tags --no-recurse-submodules \
        --filter=blob:limit=1m --template= --no-checkout \
        https://github.com/OWNER/REPO.git <DEST>
git -C <DEST> rev-parse HEAD        # record the commit SHA
# then materialise files with the sparse-checkout step in 4.3 (pass -c core.hooksPath=<EMPTY_DIR> again)
```

Result for `octocat/Hello-World` (run with the same flags, without `--no-checkout`): exit 0 in 2.4 s. The repo is
shallow (`.git/shallow` exists), the origin remote is `promisor=true` with `partialclonefilter=blob:limit=1048576`,
and the fetch refspec is limited to the one branch. Run with `--no-checkout` against `lucidrains/vit-pytorch`,
the object store was 4.8 MB, against 11 MB for a plain `--depth 1` clone.

### 4.2 What each flag does

| Flag | Result |
|---|---|
| `--depth 1` | Verified. Implies `--single-branch`; `--single-branch` is kept anyway for clarity. |
| `--no-tags` | Verified; fewer refs. |
| `--no-recurse-submodules` | Verified on a repo with a submodule: `.gitmodules` is present, but `vendor/sub` is an **empty directory**. Plain `clone` behaves the same (no recursion unless asked); `-c submodule.recurse=false` makes that explicit. |
| `-c core.hooksPath=<empty dir>` | Verified: a `post-checkout` hook planted via `--template` ran in the unblocked clone (1 marker) and did **not** run with this flag (0). `-c core.hooksPath=NUL` and `-c core.hooksPath=/dev/null` blocked it too. |
| `--template=` (empty) | Verified: no template, so `.git/hooks` is never created and no hook can run. Combine with the line above. |
| `--filter=blob:limit=1m` | **GitHub honours it** (see 4.3), but a normal checkout fetches the omitted blobs again. |
| `-c core.longpaths=true` | Accepted. I did not build a path over 260 characters to prove the effect `[DOCS]`. |
| `-c core.symlinks=false` | Accepted. Symlinks become plain text files holding the target path. Not exercised with a symlinked repo. |
| `-c protocol.ext.allow=never`, `protocol.file.allow=never` | Accepted for GitHub HTTPS. (`file` must be `always` in local tests.) |
| `GIT_TERMINAL_PROMPT=0`, `-c credential.helper=` | A private or missing repo should then fail instead of prompting. I did not run a missing-repo case. |
| `GIT_LFS_SKIP_SMUDGE=1` | Set in all runs; no LFS repo was tested. `[DOCS]` it stops the LFS smudge filter from fetching large files. |
| `http.lowSpeedLimit` / `http.lowSpeedTime` | Accepted; aborts a stalled transfer. Not triggered here. |

Hooks notes:

- `-c core.hooksPath=...` is **not saved** in `.git/config`, so it must be passed again on later git commands in the
  clone, or saved with `git config core.hooksPath <empty>`. Using `--template=` makes this moot because there is
  no hooks directory.
- Git never copies a remote repo's hooks on clone `[DOCS]`; hooks only come from the template dir (user or system
  config), which is what `--template=` and `hooksPath` neutralise.

### 4.3 Size limits (the part with a catch)

- **GitHub honours `--filter=blob:limit=1m`.** With `lucidrains/vit-pytorch` and `--no-checkout`,
  `git rev-list --objects --missing=print HEAD` listed exactly the 2 oversized blobs (`images/vit.gif`,
  `examples/cats_and_dogs.ipynb`) as `?<oid>`, and the object database was 4.8 MB.
- **A plain `git checkout` lazily fetches those blobs anyway.** `vit.gif` ended up in the working tree at
  6,042,506 bytes and `.git/objects` grew to 11 MB.
- **To really skip oversize files, build a sparse-checkout from the omitted oids** (`[RAN]` on GitHub and locally):

  ```bash
  omitted=$(git rev-list --objects --missing=print HEAD | sed -n 's/^?//p')
  # map each omitted oid to its path with `git ls-tree -r HEAD`, then:
  { echo '/*'; echo '!/images/vit.gif'; echo '!/examples/cats_and_dogs.ipynb'; } \
      | git sparse-checkout set --no-cone --stdin
  GIT_NO_LAZY_FETCH=1 git checkout -q HEAD     # succeeds, proving no missing blob was needed
  ```

  Result: both oversize files absent, 126 of 128 tracked files checked out, `.git/objects` stayed at 4.8 MB.
  Locally, a 2 MiB random blob was skipped the same way and `.git/objects` stayed at 11 KB.
- `git clone` has no built-in repo-size cap. A pre-flight guard can use the GitHub REST API:
  `GET https://api.github.com/repos/{owner}/{repo}` returns `size` in KB (whole history, so an upper bound for a
  depth-1 clone) `[RAN]`, and `GET /repos/{o}/{r}/git/trees/{branch}?recursive=1` lists per-file `size` `[RAN]`.
  Without a token, GitHub allows 60 API requests per hour `[DOCS]`.
- Not tested: GitLab and other hosts (the filter needs server support), and submodules fetched on purpose.

---

## Sources

- https://pypi.org/project/tree-sitter/ · https://pypi.org/project/tree-sitter-language-pack/ · https://pypi.org/project/tree-sitter-python/
- https://github.com/tree-sitter/py-tree-sitter/releases · https://tree-sitter.github.io/py-tree-sitter/
- https://github.com/xberg-io/tree-sitter-language-pack
- https://huggingface.co/BAAI/bge-small-en-v1.5 · https://pypi.org/project/sentence-transformers/ · https://pypi.org/project/torch/
- https://pypi.org/project/fastembed/ · https://pypi.org/project/onnxruntime/
- https://www.sqlite.org/fts5.html
- https://git-scm.com/docs/git-clone · https://git-scm.com/docs/partial-clone
