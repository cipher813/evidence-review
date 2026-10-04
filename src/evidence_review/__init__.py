from .contracts import ReviewBundle, ReviewSubmission, validate_bundle
__version__ = '0.1.0'

# UI imports are lazy so contract-only consumers do not start a server.
def open_review(bundle, store, hooks=None, **kwargs):
    from .server import open_review as implementation
    return implementation(bundle, store, hooks, **kwargs)


def export_submission(store, task_id, revision):
    return store.export_submission(task_id, revision)
