"""Caller-owned durable continuation, with no automatic retry of unknown side effects."""

import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable

STATUSES = ("pending", "succeeded", "failed", "unknown")


def frozen(value):
    if isinstance(value, dict):
        return MappingProxyType({k: frozen(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(frozen(v) for v in value)
    return value


@dataclass
class Hooks:
    on_submission: Callable | None = None
    on_revision: Callable | None = None
    reconcile: Callable | None = None
    next_bundle: Callable | None = None
    # Seconds before an unfinished callback is recorded as an unknown outcome.
    timeout: float = 30.0


def _checked(status):
    if (
        not isinstance(status, dict)
        or set(status) - {"status", "identifier", "reason"}
        or status.get("status") not in STATUSES
    ):
        raise ValueError("invalid hook result")
    if status["status"] == "succeeded" and not status.get("identifier"):
        raise ValueError("success needs durable identifier")
    return status


def _call(callback, payload, timeout):
    """Run a callback with a deadline. On timeout the thread keeps running;
    its eventual result is delivered through the returned late-result box."""
    box = {}
    done = threading.Event()

    def target():
        try:
            box["result"] = _checked(callback(payload))
        except Exception as exc:
            box["error"] = exc
        done.set()

    threading.Thread(target=target, daemon=True).start()
    if not done.wait(timeout):
        return None, done, box
    if "error" in box:
        raise box["error"]
    return box["result"], done, box


def run_hook(store, task_id, revision, hooks=None, reconcile=False):
    """Run the continuation for one submitted revision at most once per claimed attempt.

    Ownership is a durable compare-and-set claim taken under the task lock
    (``store.claim_hook``); only the claim holder calls the callback, and only
    after the lock is released. Outcomes, including late results after a
    timeout, are recorded with ``store.settle_hook``, which compares revision,
    attempt and claim atomically, so a stale attempt can never overwrite a
    newer record. This bounds callback invocations per attempt; it does not
    make remote effects exactly-once (a crash or timeout leaves the outcome
    unknown), so callers' side effects must still be idempotent and
    reconciliation must check the caller's own receipt."""
    hooks = hooks or Hooks()
    state = store.load_task(task_id)
    if state["hook"]["status"] == "succeeded":
        return state
    if state["hook"].get("invoked") and not reconcile:
        return state
    if reconcile and hooks.reconcile is None:
        # Keep the recorded outcome (pending/failed/unknown); only the caller can resolve it.
        return store.require_reconciliation(
            task_id, revision, "caller reconciliation required"
        )
    sub = store.export_submission(task_id, revision)
    callback = (
        hooks.reconcile
        if reconcile
        else (hooks.on_revision if sub.amendment_reason else hooks.on_submission)
    )
    state, claim = store.claim_hook(
        task_id,
        revision,
        state["hook"].get("attempt", 0),
        reconcile=reconcile,
        lease=hooks.timeout,
    )
    if claim is None:
        # Another caller owns this attempt (or already finished it); never run twice.
        return state
    attempt = state["hook"]["attempt"]
    if callback is None:
        status = {
            "status": "succeeded",
            "identifier": f"local:{task_id}:{revision}",
            "reason": "local submission only; no external hook configured",
        }
        return store.settle_hook(task_id, revision, attempt, claim, status)[0]
    try:
        status, done, box = _call(
            callback, frozen(sub.model_dump(mode="json")), hooks.timeout
        )
        if status is None:
            # The external action may or may not have happened. Never replay it.
            # Record unknown first, then accept the late result only while this
            # attempt still owns an unknown record.
            state = store.settle_hook(
                task_id,
                revision,
                attempt,
                claim,
                {
                    "status": "unknown",
                    "identifier": None,
                    "reason": f"no result within {hooks.timeout:g}s; external outcome unknown, reconcile with a receipt",
                },
            )[0]

            def late():
                done.wait()
                if "result" in box:
                    _record_late(store, task_id, revision, attempt, claim, box["result"])

            threading.Thread(target=late, daemon=True).start()
            return state
    except Exception as exc:
        # Human work is already durable. Record callback failure, never imply remote success.
        status = {
            "status": "failed",
            "identifier": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return store.settle_hook(task_id, revision, attempt, claim, status)[0]


def _record_late(store, task_id, revision, attempt, claim, status):
    try:
        store.settle_hook(
            task_id,
            revision,
            attempt,
            claim,
            {**status, "reason": "late result: " + (status.get("reason") or "")},
            late=True,
        )
    except Exception:
        pass  # A stale late result is dropped; reconciliation remains available.
