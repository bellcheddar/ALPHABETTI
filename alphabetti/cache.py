"""SQLite result cache, keyed on the sequence.

Two jobs. It makes a repeat request instant, which the acceptance criteria
require to be under a second; and it holds the pre-warmed examples that give
the app a landing state, which section 8 of the spec requires never to be
empty.

The key is sha256 of the uppercased sequence and nothing else. Deliberately not
the user's input: a raw sequence, the same sequence in FASTA, and the UniProt
accession that resolves to it are one fold and must share one cache entry.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id          TEXT PRIMARY KEY,
    sequence    TEXT NOT NULL,
    length      INTEGER NOT NULL,
    payload     BLOB NOT NULL,          -- gzipped JSON
    pdb         TEXT NOT NULL,          -- raw, for download and future features
    protected   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    last_hit_at REAL
);
CREATE INDEX IF NOT EXISTS results_created ON results(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    result_id   TEXT,
    state       TEXT NOT NULL,          -- queued|running|done|failed
    stage       TEXT NOT NULL,
    sequence    TEXT,
    length      INTEGER,
    source      TEXT,                   -- JSON blob of the ParsedInput source
    error       TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
"""


def sequence_id(sequence: str) -> str:
    """The cache key: sha256 of the uppercased sequence."""
    return hashlib.sha256(sequence.strip().upper().encode()).hexdigest()


class Cache:
    """A small, thread-safe SQLite wrapper.

    One connection per thread, because SQLite connections are not shareable
    across threads and the job pool is threads. WAL mode so a fold writing a
    result never blocks a reader serving a cached one.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = sqlite3.connect(self.path, timeout=30.0)
            existing.row_factory = sqlite3.Row
            existing.execute("PRAGMA journal_mode=WAL")
            existing.execute("PRAGMA synchronous=NORMAL")
            self._local.connection = existing
        return existing

    # -- results ---------------------------------------------------------

    def get(self, result_id: str) -> dict | None:
        """Return the payload and count the hit, or None."""
        connection = self._connect()
        row = connection.execute(
            "SELECT payload FROM results WHERE id = ?", (result_id,)
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            "UPDATE results SET hits = hits + 1, last_hit_at = ? WHERE id = ?",
            (time.time(), result_id),
        )
        connection.commit()
        payload = json.loads(gzip.decompress(row["payload"]).decode())
        # The stored payload records the fold that produced it; a cached read is
        # a different event and the UI says which it was.
        payload.setdefault("stats", {})["cached"] = True
        return payload

    def has(self, result_id: str) -> bool:
        return self._connect().execute(
            "SELECT 1 FROM results WHERE id = ?", (result_id,)
        ).fetchone() is not None

    def get_pdb(self, result_id: str) -> str | None:
        row = self._connect().execute(
            "SELECT pdb FROM results WHERE id = ?", (result_id,)
        ).fetchone()
        return row["pdb"] if row else None

    def put(self, result_id: str, sequence: str, payload: dict, pdb: str,
            protected: bool = False) -> None:
        connection = self._connect()
        blob = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 6)
        connection.execute(
            "INSERT OR REPLACE INTO results "
            "(id, sequence, length, payload, pdb, protected, created_at, hits) "
            "VALUES (?,?,?,?,?,?,?, COALESCE((SELECT hits FROM results WHERE id=?),0))",
            (result_id, sequence, len(sequence), blob, pdb, int(protected),
             time.time(), result_id),
        )
        connection.commit()

    def stats(self) -> dict:
        row = self._connect().execute(
            "SELECT COUNT(*) n, COALESCE(SUM(hits),0) hits, "
            "COALESCE(SUM(LENGTH(payload)),0) bytes FROM results"
        ).fetchone()
        return {"entries": row["n"], "hits": row["hits"], "bytes": row["bytes"]}

    # -- jobs ------------------------------------------------------------

    def create_job(self, job_id: str, sequence: str, source: dict) -> None:
        connection = self._connect()
        connection.execute(
            "INSERT INTO jobs (job_id, state, stage, sequence, length, source, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (job_id, "queued", "queued", sequence, len(sequence),
             json.dumps(source), time.time()),
        )
        connection.commit()

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        connection = self._connect()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        connection.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )
        connection.commit()

    def get_job(self, job_id: str) -> dict | None:
        row = self._connect().execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def queue_depth(self) -> int:
        return self._connect().execute(
            "SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')"
        ).fetchone()["n"]

    def queue_position(self, job_id: str) -> int:
        """How many jobs are ahead of this one. Zero means it is next or running."""
        row = self._connect().execute(
            "SELECT created_at FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return 0
        return self._connect().execute(
            "SELECT COUNT(*) n FROM jobs WHERE state = 'queued' AND created_at < ?",
            (row["created_at"],),
        ).fetchone()["n"]

    def reap_stale_jobs(self, timeout: float) -> int:
        """Fail jobs that were running when the process died.

        Called at start-up. A job left in `running` by a restart would otherwise
        be polled by its client forever, because nothing is left alive to
        finish it.
        """
        connection = self._connect()
        cutoff = time.time() - timeout
        cursor = connection.execute(
            "UPDATE jobs SET state='failed', stage='failed', "
            "error='The server restarted while this fold was running. Try again.', "
            "finished_at=? WHERE state IN ('queued','running') AND created_at < ?",
            (time.time(), cutoff),
        )
        connection.commit()
        return cursor.rowcount
