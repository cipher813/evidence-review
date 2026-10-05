"""Offline local CLI, sharing the same API/store as embedded consumers."""

import argparse
import json
import sys
import time
from pathlib import Path
from . import export_json, inventory, open_review, validate_bundle
from .example import example_bundle
from .store import FileStore


def read_bundle(parser, path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
        parser.error("bundle must be a regular file of at most 20 MB")
    return validate_bundle(json.loads(path.read_text(encoding="utf-8")))


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
    inspect = sub.add_parser(
        "inspect", help="validate a bundle and print its evidence inventory"
    )
    inspect.add_argument("--bundle", type=Path, required=True)
    export = sub.add_parser(
        "export", help="print one submitted revision as canonical JSON"
    )
    export.add_argument("--state-dir", type=Path, required=True)
    export.add_argument("--task", required=True)
    export.add_argument("--revision", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(inventory(read_bundle(parser, args.bundle)), indent=2))
        return 0
    if args.command == "export":
        if not (args.state_dir / args.task).is_dir():
            parser.error("no such task in state directory")
        try:
            data = export_json(FileStore(args.state_dir), args.task, args.revision)
        except KeyError:
            parser.error(f"revision {args.revision} was not submitted")
        sys.stdout.buffer.write(data + b"\n")
        return 0
    if args.command == "serve":
        bundle = read_bundle(parser, args.bundle)
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
