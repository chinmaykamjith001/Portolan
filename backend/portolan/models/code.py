"""Code index shapes (spec §9.2, §10.4). Shared by the cloner, indexers, store and retrieval."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CodeSpan(BaseModel):
    repo_id: str
    commit_sha: str
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int


class LineNote(BaseModel):
    line: int  # absolute line number in file
    tag: str  # short pointer to the paper: "Eq. 1 · QKᵀ/√dₖ", "§3.2.3"
    note: str  # what the line does, in plain words (≤ 25 words)
    kind: Literal["implements", "detail_not_in_paper", "mismatch", "context"]


FileLanguage = Literal["python", "notebook", "yaml", "json", "toml"]
SymbolKind = Literal[
    "module",  # one per Python file: the module docstring and whole-file span
    "class",
    "function",  # module-level or nested function
    "method",  # function directly inside a class
    "constant",  # module-level (or class-level) assignment, e.g. D_MODEL = 512
    "cli_default",  # argparse add_argument(..., default=...) / click option default
    "config_key",  # a key in a YAML/JSON/TOML file
]


class Symbol(BaseModel):
    id: str  # "<path>::<qualname>" (unique within a file index; duplicates get "#2")
    kind: SymbolKind
    name: str  # "forward", "d_model", "--label-smoothing"
    qualname: str  # "MultiHeadAttention.forward"; config: "model.d_model"; module: ""
    path: str  # repo-relative, POSIX separators
    start_line: int  # 1-based, inclusive
    end_line: int
    docstring: str | None = None
    signature: str | None = None  # first line of a def/class, e.g. "def forward(self, x, mask=None):"
    value: str | None = None  # source text of the value for constants / defaults / config keys
    parent: str | None = None  # qualname of the enclosing class/function, if any


class ImportRef(BaseModel):
    path: str
    line: int
    module: str  # "torch.nn.functional" (relative imports keep their dots: ".layers")
    names: list[str] = Field(default_factory=list[str])  # imported names; [] for `import x`
    alias: str | None = None  # "F" in `import torch.nn.functional as F`


class CallRef(BaseModel):
    path: str
    line: int
    callee: str  # dotted name as written: "torch.softmax", "F.softmax", "self.attn"
    in_symbol: str | None = None  # qualname of the enclosing function/class


class FileIndex(BaseModel):
    path: str
    language: FileLanguage
    sha256: str
    n_lines: int
    symbols: list[Symbol] = Field(default_factory=list[Symbol])
    imports: list[ImportRef] = Field(default_factory=list[ImportRef])
    calls: list[CallRef] = Field(default_factory=list[CallRef])
    # Notebooks: the code cells joined with "\n" — line numbers refer to this text.
    virtual_text: str | None = None
    parse_errors: bool = False


class CodeIndex(BaseModel):
    repo_id: str  # RepoCandidate.id
    commit_sha: str  # git SHA, or a content hash for uploads/local folders
    root: str  # absolute path of the checkout
    files: list[str]  # indexed repo-relative paths
    symbol_count: int
    library_modules: list[str]  # top-level imports that don't resolve inside the repo ("torch")
    skipped: dict[str, str] = Field(default_factory=dict[str, str])  # path → reason
