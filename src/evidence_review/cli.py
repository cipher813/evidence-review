"""Offline local CLI, sharing the same API/store as embedded consumers."""

import argparse
import json
import time
from pathlib import Path
from . import open_review, validate_bundle
from .example import example_bundle
from .store import FileStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--state-dir", type=Path, default=Path(".evidence-review-demo"))
    serve = sub.add_parser("serve")
    serve.add_argument("--bundle", type=Path, required=True)
    serve.add_argument("--state-dir", type=Path, required=True)
    for p in (demo, serve):
        p.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "serve":
        if (
            args.bundle.is_symlink()
            or not args.bundle.is_file()
            or args.bundle.stat().st_size > 20_000_000
        ):
            parser.error("bundle must be a regular file of at most 20 MB")
        bundle = validate_bundle(json.loads(args.bundle.read_text(encoding="utf-8")))
    else:
        bundle = example_bundle()
    with open_review(
        bundle, FileStore(args.state_dir), launch=not args.no_browser
    ) as handle:
        # Explicit local access URL, never written into artifacts or analytics.
        print(handle.url, flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
