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
    hooks = hooks or Hooks()
    state = store.load_task(task_id)
    if state["hook"]["status"] == "succeeded":
        return state
    if state["hook"].get("invoked") and not reconcile:
        return state
    if reconcile and hooks.reconcile is None:
        # Keep the recorded outcome (pending/failed/unknown); only the caller can resolve it.
        prior = state["hook"]
        return store.hook_status(
            task_id,
            revision,
            {
                "status": prior["status"] if prior["status"] in ("failed", "unknown") else "pending",
                "identifier": None,
                "reason": "caller reconciliation required",
                "invoked": True,
                "attempt": prior.get("attempt", 0),
            },
        )
    sub = store.export_submission(task_id, revision)
    callback = (
        hooks.reconcile
        if reconcile
        else (hooks.on_revision if sub.amendment_reason else hooks.on_submission)
    )
    attempt = state["hook"].get("attempt", 0) + 1
    store.hook_status(
        task_id,
        revision,
        {
            "status": "pending",
            "identifier": None,
            "reason": "continuation in progress; crash requires reconciliation",
            "invoked": True,
            "attempt": attempt,
        },
    )
    if callback is None:
        status = {
            "status": "succeeded",
            "identifier": f"local:{task_id}:{revision}",
            "reason": "local submission only; no external hook configured",
        }
        return store.hook_status(task_id, revision, {**status, "invoked": True, "attempt": attempt})
    try:
        status, done, box = _call(
            callback, frozen(sub.model_dump(mode="json")), hooks.timeout
        )
        if status is None:
            # The external action may or may not have happened. Never replay it;
            # record the late result only if nothing newer has been recorded.
            def late():
                done.wait()
                if "result" in box:
                    _record_late(store, task_id, revision, attempt, box["result"])

            threading.Thread(target=late, daemon=True).start()
            status = {
                "status": "unknown",
                "identifier": None,
                "reason": f"no result within {hooks.timeout:g}s; external outcome unknown, reconcile with a receipt",
            }
    except Exception as exc:
        # Human work is already durable. Record callback failure, never imply remote success.
        status = {
            "status": "failed",
            "identifier": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return store.hook_status(task_id, revision, {**status, "invoked": True, "attempt": attempt})


def _record_late(store, task_id, revision, attempt, status):
    try:
        current = store.load_task(task_id)["hook"]
        if current.get("attempt") == attempt and current["status"] == "unknown":
            store.hook_status(
                task_id,
                revision,
                {**status, "reason": "late result: " + (status.get("reason") or ""), "invoked": True, "attempt": attempt},
            )
    except Exception:
        pass  # A stale late result is dropped; reconciliation remains available.
