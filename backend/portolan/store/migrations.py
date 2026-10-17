"""Ordered schema migrations. Append only; never edit a migration that has shipped."""

from __future__ import annotations

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "llm cache and budget",
        """
        CREATE TABLE llm_cache (
            key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version INTEGER NOT NULL,
            response_json TEXT NOT NULL,
            usage_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX llm_cache_template ON llm_cache (template_id, template_version);

        -- One row per (Pacific-time day, model). RPD resets at midnight US Pacific.
        CREATE TABLE budget (
            day TEXT NOT NULL,
            model TEXT NOT NULL,
            requests INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, model)
        );
        """,
    ),
    (
        2,
        "papers and external http cache",
        """
        CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY,
            ref_json TEXT NOT NULL,
            doc_json TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Responses from arXiv, Semantic Scholar, etc. (spec §15: cache all external responses).
        CREATE TABLE http_cache (
            key TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status INTEGER NOT NULL,
            content_type TEXT,
            body BLOB NOT NULL,
            fetched_at REAL NOT NULL
        );
        """,
    ),
    (
        3,
        "repo candidates and chosen repo",
        """
        CREATE TABLE repo_candidates (
            paper_id TEXT PRIMARY KEY,
            candidates_json TEXT NOT NULL,
            status_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE paper_repo (
            paper_id TEXT PRIMARY KEY,
            candidate_json TEXT NOT NULL,
            chosen_at TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        "code index, full-text search and embeddings",
        """
        CREATE TABLE code_indexes (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            root TEXT NOT NULL,
            index_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (repo_id, commit_sha)
        );

        CREATE TABLE code_files (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            path TEXT NOT NULL,
            language TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            n_lines INTEGER NOT NULL,
            text TEXT NOT NULL,  -- notebooks: the joined code cells (FileIndex.virtual_text)
            PRIMARY KEY (repo_id, commit_sha, path)
        );

        CREATE TABLE code_symbols (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            symbol_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            qualname TEXT NOT NULL,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            docstring TEXT,
            signature TEXT,
            value TEXT,
            parent TEXT,
            PRIMARY KEY (repo_id, commit_sha, symbol_id)
        );
        CREATE INDEX code_symbols_path ON code_symbols (repo_id, commit_sha, path);

        CREATE TABLE code_imports (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            path TEXT NOT NULL,
            line INTEGER NOT NULL,
            module TEXT NOT NULL,
            names_json TEXT NOT NULL,
            alias TEXT
        );
        CREATE INDEX code_imports_path ON code_imports (repo_id, commit_sha, path);

        CREATE TABLE code_calls (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            path TEXT NOT NULL,
            line INTEGER NOT NULL,
            callee TEXT NOT NULL,
            in_symbol TEXT
        );
        CREATE INDEX code_calls_path ON code_calls (repo_id, commit_sha, path);

        -- repo_key = "<repo_id>@<commit_sha>". bm25() weights go in column order (spec §10.4).
        CREATE VIRTUAL TABLE code_fts USING fts5(
            symbol_id UNINDEXED,
            repo_key UNINDEXED,
            name,
            name_parts,
            docstring,
            body,
            path,
            tokenize = "unicode61 tokenchars '_'"
        );

        CREATE TABLE code_embeddings (
            repo_id TEXT NOT NULL,
            commit_sha TEXT NOT NULL,
            symbol_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,  -- little-endian float32 x dim
            PRIMARY KEY (repo_id, commit_sha, model, symbol_id)
        );
        """,
    ),
    (
        5,
        "concept graph and job queue",
        """
        CREATE TABLE concepts (
            paper_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            depth INTEGER NOT NULL,
            concept_json TEXT NOT NULL,
            PRIMARY KEY (paper_id, concept_id)
        );
        CREATE INDEX concepts_canonical ON concepts (paper_id, canonical_name);

        CREATE TABLE layers (
            paper_id TEXT NOT NULL,
            layer_id TEXT NOT NULL,
            center TEXT NOT NULL,
            gen_state TEXT NOT NULL,
            layer_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (paper_id, layer_id)
        );

        -- Which layers a concept appears in (the concept graph is a DAG).
        CREATE TABLE layer_members (
            paper_id TEXT NOT NULL,
            layer_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            PRIMARY KEY (paper_id, layer_id, concept_id)
        );
        CREATE INDEX layer_members_concept ON layer_members (paper_id, concept_id);

        -- Persistent priority job queue (spec §8): survives restarts; P0 > P1 > P2.
        CREATE TABLE jobs (
            job_key TEXT PRIMARY KEY,
            paper_id TEXT,
            kind TEXT NOT NULL,
            priority TEXT NOT NULL,
            state TEXT NOT NULL,      -- queued | running | done | failed | paused
            payload_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX jobs_state ON jobs (state, priority, created_at);
        """,
    ),
]
