"""The job queue: a bounded thread pool with its state in SQLite.

The build spec asked for RQ and Redis. This does the same work with neither,
and the reason is the same reason the fold moved off the droplet: with the
models on a ZeroGPU Space there is nothing left for a separate worker process
to hold. A job here spends its entire life waiting on one HTTPS request, which
is what threads are for.

What that buys, on a box with 2 GB free and eight other applications on it: no
Redis daemon, no second systemd unit, and no third moving part to be down at
three in the morning. What it costs: the queue does not survive a restart,
which is handled by failing orphaned jobs at start-up with a message rather
than leaving a client polling something that will never finish.

Job state lives in SQLite rather than in memory because gunicorn runs two web
workers. A job started by one worker must be visible to a status request that
lands on the other, and a dictionary in one process is not.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .cache import Cache, sequence_id
from .folding import FoldBackend, FoldError
from .payload import build

# Stage labels, shown verbatim in the UI. Honest about which part is slow,
# because a user watching a progress line for ninety seconds deserves to know
# whether that is normal.
STAGES = {
    "queued": "waiting in the queue",
    "waking": "waking the folding service (it sleeps when idle)",
    "folding": "folding, this is the slow bit",
    "entropy": "asking the language model what it would have preferred",
    "accessibility": "measuring what the solvent can reach",
    "assembling": "arranging the letters",
    "done": "done",
    "failed": "failed",
}


class JobRunner:
    """Submits folds to a bounded pool and records their progress."""

    def __init__(self, cache: Cache, backend: FoldBackend, config):
        self.cache = cache
        self.backend = backend
        self.config = config
        self.pool = ThreadPoolExecutor(
            max_workers=max(1, config.MAX_CONCURRENT_FOLDS),
            thread_name_prefix="alphabetti-fold",
        )
        self._lock = threading.Lock()
        # Anything left running when the process died can never finish now.
        self.cache.reap_stale_jobs(config.FOLD_TIMEOUT_SECONDS)

    def submit(self, parsed, masked: bool = True) -> str:
        """Queue a fold. Returns a job id.

        The caller checks the cache first, so reaching here means a genuine
        miss.
        """
        job_id = uuid.uuid4().hex
        self.cache.create_job(job_id, parsed.sequence, parsed.as_source())
        self.pool.submit(self._run, job_id, parsed, masked)
        return job_id

    def _stage(self, job_id: str, stage: str) -> None:
        self.cache.update_job(job_id, stage=stage)

    def _run(self, job_id: str, parsed, masked: bool) -> None:
        started = time.time()
        self.cache.update_job(job_id, state="running", stage="waking",
                              started_at=started)
        try:
            result_id = sequence_id(parsed.sequence)

            # Another request may have folded the identical sequence while this
            # one sat in the queue. Two identical folds is a wasted minute of
            # GPU quota, so check again here rather than only at submit.
            cached = self.cache.get(result_id)
            if cached is not None:
                self.cache.update_job(
                    job_id, state="done", stage="done", result_id=result_id,
                    finished_at=time.time(),
                )
                return

            self._stage(job_id, "folding")
            fold = self.backend.fold(parsed.sequence, masked=masked)

            self._stage(job_id, "accessibility")
            payload = build(
                fold,
                parsed.sequence,
                parsed.as_source(),
                top_k=self.config.TOP_K,
                truncated=parsed.truncated,
                original_length=parsed.original_length,
                result_id=result_id,
            )
            payload["notes"] = list(parsed.notes)

            self._stage(job_id, "assembling")
            self.cache.put(result_id, parsed.sequence, payload, fold.pdb)
            self.cache.update_job(
                job_id, state="done", stage="done", result_id=result_id,
                finished_at=time.time(),
            )

        except FoldError as exc:
            # Already phrased for a human by the backend.
            self.cache.update_job(job_id, state="failed", stage="failed",
                                  error=str(exc), finished_at=time.time())
        except Exception as exc:
            self.cache.update_job(
                job_id, state="failed", stage="failed",
                error=(
                    f"Something went wrong after the fold ({exc.__class__.__name__}). "
                    "This is a bug rather than a problem with your sequence."
                ),
                finished_at=time.time(),
            )

    def status(self, job_id: str) -> dict | None:
        """What the client polls."""
        job = self.cache.get_job(job_id)
        if job is None:
            return None

        now = time.time()
        elapsed = (job["finished_at"] or now) - job["created_at"]
        out = {
            "job_id": job_id,
            "state": job["state"],
            "stage": job["stage"],
            "stage_label": STAGES.get(job["stage"], job["stage"]),
            "elapsed_seconds": round(elapsed, 1),
            "length": job["length"],
            "result_id": job["result_id"],
            "error": job["error"],
        }
        if job["state"] == "queued":
            out["queue_position"] = self.cache.queue_position(job_id)

        # A job that outlives the timeout is dead whatever the table says: the
        # thread holding it may be blocked on a socket that will never answer.
        if job["state"] in {"queued", "running"} and elapsed > self.config.FOLD_TIMEOUT_SECONDS:
            message = (
                f"This fold passed {self.config.FOLD_TIMEOUT_SECONDS} s without "
                "finishing and was given up on. Shorter sequences fold faster."
            )
            self.cache.update_job(job_id, state="failed", stage="failed",
                                  error=message, finished_at=now)
            out.update(state="failed", stage="failed", error=message,
                       stage_label=STAGES["failed"])
        return out
