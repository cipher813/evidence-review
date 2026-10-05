"""Write-ahead filesystem store. Acknowledgements follow fsync; replay is authoritative."""

from __future__ import annotations
import fcntl
import json
import math
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from .contracts import PACKAGE_VERSION, Judgment, Defect, ReviewSubmission, digest, now
from .evidence import passage


class Conflict(ValueError):
    pass


class Store(Protocol):
    def register(self, bundle): ...
    def load_task(self, task_id): ...
    def append_event(self, task_id, event): ...
    def save_snapshot(
        self, bundle, expected_revision, key, answers, assessor, active_seconds=0
    ): ...
    def save_submission(
        self,
        bundle,
        expected_revision,
        key,
        answers,
        assessor,
        active_seconds=0,
        amendment_reason="",
    ): ...
    def export_submission(self, task_id, revision): ...
    # Continuation ownership used by run_hook; each must be atomic per task.
    def claim_hook(self, task_id, revision, expected_attempt, reconcile=False, lease=30.0): ...
    def settle_hook(self, task_id, revision, attempt, claim, status, late=False): ...
    def require_reconciliation(self, task_id, revision, reason): ...


def atomic(path, data):
    if path.is_symlink():
        raise ValueError("symlink state refused")
    temp = path.with_name("." + path.name + "." + secrets.token_hex(8))
    with open(temp, "xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_answers(bundle, answers, complete=False):
    if set(answers) - {"judgments", "defects"}:
        raise ValueError("unknown answer section")
    judgments = {
        k: Judgment.model_validate(v) for k, v in answers.get("judgments", {}).items()
    }
    defects = [Defect.model_validate(d) for d in answers.get("defects", [])]
    fields = {f.field_id: f for f in bundle.form}
    if judgments.keys() - fields.keys():
        raise ValueError("unknown form field")
    claims = {c.claim_id for c in bundle.claims}
    refs = {r.reference_id for r in bundle.references}
    sources = {s.source_id: s for s in bundle.sources}
    for key, j in judgments.items():
        field = fields[key]
        if (
            field.kind == "choice"
            and j.value not in field.options
            and (complete or j.value != "")
        ):
            raise ValueError("unknown option")
        if field.kind == "boolean" and type(j.value) is not bool:
            raise ValueError("boolean required")
        if field.kind == "text" and not isinstance(j.value, str):
            raise ValueError("text required")
        if set(j.claim_ids) - claims:
            raise ValueError("unknown selected claim")
        if (
            complete
            and field.note_required_unless
            and j.value not in field.note_required_unless
            and not j.note.strip()
        ):
            raise ValueError(f"explanation required: {key}")
        if complete and field.evidence_required and not j.selections:
            raise ValueError(f"evidence required: {key}")
        if (
            complete
            and bundle.task_kind == "adjudication"
            and not field.require_true
            and not j.note.strip()
        ):
            raise ValueError(f"adjudication reason required: {key}")
    for field in bundle.form:
        if complete and field.required:
            j = judgments.get(field.field_id)
            if (
                j is None
                or j.value == ""
                or (field.require_true and j.value is not True)
                or (field.kind == "text" and not str(j.value).strip())
            ):
                raise ValueError(f"required field: {field.field_id}")
    if len({d.defect_id for d in defects}) != len(defects):
        raise ValueError("duplicate defect identity")
    for d in defects:
        if set(d.claim_ids) - claims or set(d.reference_ids) - refs:
            raise ValueError("unknown defect subject")
        if complete and (
            not d.category.strip() or not d.evidence_note.strip() or d.material is None
        ):
            raise ValueError("defect category, materiality and explanation required")
    for record in [*judgments.values(), *defects]:
        for selection in record.selections:
            source = sources.get(selection.source_id)
            if source is None or source.sha256 != selection.source_hash:
                raise ValueError("source selection hash invalid")
            text = passage(source, selection.start_line, selection.end_line)["text"]
            if text != selection.excerpt:
                raise ValueError("source selection excerpt invalid")
    return {
        "judgments": {k: v.model_dump(mode="json") for k, v in judgments.items()},
        "defects": [d.model_dump(mode="json") for d in defects],
    }


class FileStore:
    def __init__(self, root):
        self.root = Path(root)
        if self.root.is_symlink():
            raise ValueError("symlink state refused")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._mutex = threading.RLock()

    def _dir(self, task_id):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", task_id) or task_id in (
            ".",
            "..",
        ):
            raise ValueError("invalid task id")
        path = self.root / task_id
        if path.is_symlink():
            raise ValueError("symlink state refused")
        path.mkdir(exist_ok=True, mode=0o700)
        return path

    @contextmanager
    def _lock(self, task_id):
        with self._mutex:
            directory = self._dir(task_id)
            path = directory / "writer.lock"
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield directory
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _events(self, directory):
        path = directory / "events.jsonl"
        if path.is_symlink():
            raise ValueError("symlink event log refused")
        if not path.exists():
            return []
        raw = path.read_bytes()
        records = []
        offset = 0
        for line in raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                # A partial final write was never acknowledged; preserve it separately.
                atomic(directory / ("torn-" + secrets.token_hex(4) + ".bin"), line)
                with open(path, "r+b") as f:
                    f.truncate(offset)
                    f.flush()
                    os.fsync(f.fileno())
                break
            records.append(json.loads(line))
            offset += len(line)
        return records

    def _append(self, directory, event):
        path = directory / "events.jsonl"
        fd = os.open(
            path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "ab") as f:
            f.write(
                (
                    json.dumps(
                        event, sort_keys=True, ensure_ascii=False, allow_nan=False
                    )
                    + "\n"
                ).encode()
            )
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def append_event(self, task_id, event):
        with self._lock(task_id) as d:
            self._state(d)
            self._append(d, {"event": "external", "at": now(), "detail": event})

    def activity(self, task_id):
        """Navigation counts. Opening evidence is exposure, never verification."""
        with self._lock(task_id) as d:
            counts = {}
            for e in self._events(d):
                if e.get("event") == "external" and isinstance(e.get("detail"), dict):
                    kind = e["detail"].get("kind", "other")
                    counts[kind] = counts.get(kind, 0) + 1
            return {"counts": dict(sorted(counts.items())), "verification": False}

    def _state(self, directory):
        states = [e["state"] for e in self._events(directory) if "state" in e]
        if not states:
            raise KeyError("task not registered")
        return states[-1]

    def register(self, bundle):
        with self._lock(bundle.bundle_id) as d:
            events = self._events(d)
            if events:
                state = self._state(d)
                if state["bundle_hash"] != bundle.bundle_hash:
                    raise Conflict("bundle or rubric changed")
                return state
            state = {
                "bundle_id": bundle.bundle_id,
                "bundle_hash": bundle.bundle_hash,
                "revision": 0,
                "started_at": now(),
                "active_seconds": 0,
                "answers": {"judgments": {}, "defects": []},
                "assessor": "",
                "submissions": {},
                "operations": {},
                "hook": {
                    "status": "pending",
                    "identifier": None,
                    "reason": "not submitted",
                },
                "last_submission": None,
            }
            self._append(d, {"event": "registered", "at": now(), "state": state})
            atomic(d / "snapshot.json", json.dumps(state).encode())
            return state

    def load_task(self, task_id):
        with self._lock(task_id) as d:
            return self._state(d)

    def _save(
        self,
        bundle,
        expected_revision,
        key,
        answers,
        assessor,
        active_seconds,
        complete,
        amendment_reason,
    ):
        if not assessor.strip():
            raise ValueError("assessor required")
        if (
            not isinstance(active_seconds, (float, int))
            or not math.isfinite(active_seconds)
            or not 0 <= active_seconds <= 3600
        ):
            raise ValueError("invalid active time increment")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", key):
            raise ValueError("invalid idempotency key")
        cleaned = validate_answers(bundle, answers, complete)
        signature = digest(
            {
                "answers": cleaned,
                "assessor": assessor,
                "active_seconds": active_seconds,
                "complete": complete,
                "reason": amendment_reason,
            }
        )
        with self._lock(bundle.bundle_id) as d:
            state = self._state(d)
            if state["bundle_hash"] != bundle.bundle_hash:
                raise Conflict("bundle or rubric changed")
            prior = state["operations"].get(key)
            if prior:
                if prior["signature"] != signature:
                    raise Conflict("idempotency key reused with different content")
                # Return the original operation, never newer state that conceals intervening writes.
                return next(
                    e["state"] for e in self._events(d) if e.get("operation") == key
                )
            if state["revision"] != expected_revision:
                raise Conflict("stale revision; reload before editing")
            if state["assessor"] and state["assessor"] != assessor:
                raise Conflict("task belongs to another assessor")
            if complete and state["submissions"] and not amendment_reason.strip():
                raise ValueError("amendment reason required")
            revision = state["revision"] + 1
            state.update(
                revision=revision,
                answers=cleaned,
                assessor=assessor,
                active_seconds=state["active_seconds"] + active_seconds,
            )
            if complete:
                submitted = now()
                submission = ReviewSubmission(
                    bundle_id=bundle.bundle_id,
                    bundle_hash=bundle.bundle_hash,
                    revision=revision,
                    assessor=assessor,
                    started_at=state["started_at"],
                    submitted_at=submitted,
                    active_seconds=state["active_seconds"],
                    session_seconds=(
                        datetime.fromisoformat(submitted)
                        - datetime.fromisoformat(state["started_at"])
                    ).total_seconds(),
                    **cleaned,
                    complete=True,
                    amendment_reason=amendment_reason,
                    provenance={
                        "schema": "review-bundle/v1",
                        **bundle.document_hashes,
                        "submission_schema": "review-submission/v1",
                        "package_version": PACKAGE_VERSION,
                        "task_kind": bundle.task_kind,
                        **(
                            {"amends_revision": str(state["last_submission"])}
                            if state["last_submission"]
                            else {}
                        ),
                    },
                )
                state["submissions"][str(revision)] = submission.model_dump(mode="json")
                state["last_submission"] = revision
                state["hook"] = {
                    "status": "pending",
                    "identifier": None,
                    "reason": "submission durable; continuation pending",
                    "revision": revision,
                }
            state["operations"][key] = {"signature": signature, "revision": revision}
            self._append(
                d,
                {
                    "event": "submitted" if complete else "answer_saved",
                    "at": now(),
                    "operation": key,
                    "state": state,
                },
            )
            atomic(d / "snapshot.json", json.dumps(state, ensure_ascii=False).encode())
            return state

    def save_snapshot(
        self, bundle, expected_revision, key, answers, assessor, active_seconds=0
    ):
        return self._save(
            bundle, expected_revision, key, answers, assessor, active_seconds, False, ""
        )

    def save_submission(
        self,
        bundle,
        expected_revision,
        key,
        answers,
        assessor,
        active_seconds=0,
        amendment_reason="",
    ):
        return self._save(
            bundle,
            expected_revision,
            key,
            answers,
            assessor,
            active_seconds,
            True,
            amendment_reason,
        )

    def export_submission(self, task_id, revision):
        state = self.load_task(task_id)
        return ReviewSubmission.model_validate(state["submissions"][str(revision)])

    def _write_hook(self, d, state, hook, event="hook_status"):
        state["hook"] = hook
        self._append(d, {"event": event, "at": now(), "state": state})
        atomic(d / "snapshot.json", json.dumps(state, ensure_ascii=False).encode())
        return state

    def hook_status(self, task_id, revision, status):
        """Unconditionally record a continuation outcome (operator override).

        Replacing the record drops any attempt claim, so an in-flight or late
        result for a claimed attempt is fenced out afterwards."""
        with self._lock(task_id) as d:
            state = self._state(d)
            if state["last_submission"] != revision:
                raise Conflict("stale hook result")
            return self._write_hook(d, state, {**status, "revision": revision})

    def claim_hook(self, task_id, revision, expected_attempt, reconcile=False, lease=30.0):
        """Compare-and-set ownership of the next continuation attempt.

        Under the task lock: the claim succeeds only if the submission is still
        current, the outcome is not already succeeded, the recorded attempt is
        still ``expected_attempt`` (no competing claim landed since the caller
        read it), and either this is the first invocation or, for
        reconciliation, no other attempt holds an unexpired lease. Returns
        ``(state, claim)``; ``claim`` is None when this caller does not own an
        attempt and must not run a callback. The lock is released before
        returning, so callbacks never run under it."""
        with self._lock(task_id) as d:
            state = self._state(d)
            if state["last_submission"] != revision:
                raise Conflict("stale hook claim")
            hook = state["hook"]
            if hook["status"] == "succeeded" or hook.get("attempt", 0) != expected_attempt:
                return state, None
            if not reconcile and hook.get("invoked"):
                return state, None
            lease_expires = hook.get("lease_expires")
            if (
                reconcile
                and hook["status"] == "pending"
                and hook.get("invoked")
                and isinstance(lease_expires, (int, float))
                and time.time() < lease_expires
            ):
                return state, None  # another attempt is still within its deadline
            claim = secrets.token_hex(16)
            seconds = lease if isinstance(lease, (int, float)) and math.isfinite(lease) else 86400.0
            self._write_hook(
                d,
                state,
                {
                    "status": "pending",
                    "identifier": None,
                    "reason": "continuation in progress; crash requires reconciliation",
                    "invoked": True,
                    "attempt": expected_attempt + 1,
                    "claim": claim,
                    "lease_expires": time.time() + min(max(seconds, 0.0), 86400.0),
                    "revision": revision,
                },
                "hook_claimed",
            )
            return state, claim

    def settle_hook(self, task_id, revision, attempt, claim, status, late=False):
        """Record the outcome of a claimed attempt only if it still owns the record.

        Compares revision, attempt and claim under the task lock. An on-time
        result requires the attempt still pending; a late result requires it
        still recorded as unknown. Returns ``(state, recorded)``; a fenced
        result leaves the newer record untouched."""
        with self._lock(task_id) as d:
            state = self._state(d)
            if state["last_submission"] != revision:
                raise Conflict("stale hook result")
            hook = state["hook"]
            expected = "unknown" if late else "pending"
            if (
                hook.get("revision") != revision
                or hook.get("attempt") != attempt
                or hook.get("claim") != claim
                or hook["status"] != expected
            ):
                return state, False
            record = {**status, "invoked": True, "attempt": attempt, "claim": claim, "revision": revision}
            return self._write_hook(d, state, record), True

    def require_reconciliation(self, task_id, revision, reason):
        """Note that only the caller can resolve the outcome, keeping ownership fields."""
        with self._lock(task_id) as d:
            state = self._state(d)
            if state["last_submission"] != revision:
                raise Conflict("stale hook result")
            hook = state["hook"]
            if hook["status"] == "succeeded":
                return state
            return self._write_hook(
                d,
                state,
                {
                    **hook,
                    "status": hook["status"] if hook["status"] in ("failed", "unknown") else "pending",
                    "identifier": None,
                    "reason": reason,
                    "invoked": True,
                    "attempt": hook.get("attempt", 0),
                    "revision": revision,
                },
            )
