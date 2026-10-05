from .contracts import (
    PACKAGE_VERSION,
    ReviewBundle,
    ReviewSubmission,
    blind_violations,
    canonical_json,
    validate_bundle,
)
from .evidence import inventory
from .hooks import Hooks
from .store import Conflict, FileStore

__version__ = PACKAGE_VERSION
__all__ = [
    "Conflict",
    "FileStore",
    "Hooks",
    "ReviewBundle",
    "ReviewSubmission",
    "blind_violations",
    "export_json",
    "export_submission",
    "inventory",
    "open_review",
    "validate_bundle",
]


# UI imports are lazy so contract-only consumers do not start a server.
def open_review(bundle, store, hooks=None, **kwargs):
    from .server import open_review as implementation

    return implementation(bundle, store, hooks, **kwargs)


def export_submission(store, task_id, revision):
    return store.export_submission(task_id, revision)


def export_json(store, task_id, revision) -> bytes:
    """Canonical bytes: identical for the same task revision on every call."""
    return canonical_json(export_submission(store, task_id, revision).model_dump(mode="json"))
