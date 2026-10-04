"""Write-ahead filesystem store. Acknowledgements follow fsync; replay is authoritative."""

from __future__ import annotations
import fcntl
import json
import math
import os
import re
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from .contracts import Judgment, Defect, ReviewSubmission, digest, now
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
            self._events(d)
            self._append(d, {"event": "external", "at": now(), "detail": event})

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
                    provenance={"schema": "review-bundle/v1", **bundle.document_hashes},
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

    def hook_status(self, task_id, revision, status):
        with self._lock(task_id) as d:
            state = self._state(d)
            if state["last_submission"] != revision:
                raise Conflict("stale hook result")
            state["hook"] = {**status, "revision": revision}
            self._append(d, {"event": "hook_status", "at": now(), "state": state})
            atomic(d / "snapshot.json", json.dumps(state).encode())
            return state
