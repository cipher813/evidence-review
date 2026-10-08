"""Authenticated loopback-only service; serves packaged assets and neutral bundles."""

from __future__ import annotations
import json
import hashlib
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit, parse_qsl
import re
from .atomic_evidence import (ATOM_EVIDENCE_SCHEMA, RENDER_MANIFEST_SCHEMA, atom_view, build_atom_manifest,
                              validate_atom_evidence)
from .contracts import ReviewWorkload, PACKAGE_VERSION, blind_terms, blind_violations, validate_bundle
from .source_rendering import (DERIVATIVE_SCHEMA, RenderedSourceAsset, render_frozen_text,
                               validate_render_asset)
from .evidence import evidence_views, inventory, navigation_coverage
from .store import Conflict, submission_matches_snapshot
from .hooks import Hooks, run_hook

EVENT_KINDS = {
    "span_opened",
    "subject_opened",
    "source_opened",
    "search",
    "passage_selected",
    "checked_quantity_evidence_attached",
    "reference_evidence_receipts_attached",
    "original_opened",
    "rendered_target_opened",
    "atom_row_expanded",
}

ATOMIC_SOURCE_CHECK = "atomic-source-check/v1"
PRESENTATION_MODES = ("default", ATOMIC_SOURCE_CHECK)
CONTRACTS = ("review-bundle/v1", "review-submission/v1", ATOM_EVIDENCE_SCHEMA, RENDER_MANIFEST_SCHEMA,
             DERIVATIVE_SCHEMA)
MAX_RENDER_RESPONSE = 8_000_000
SAFE_SOURCE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
SAFE_TARGET = re.compile(r"^tgt:[0-9a-f]{24}$")


class UnsupportedContract(ValueError):
    """A caller asked for a contract or mode this package version does not implement."""


def capabilities():
    """What this package version can validate and present; callers refuse rather than degrade silently."""
    return {"package_version": PACKAGE_VERSION, "contracts": list(CONTRACTS),
            "presentation_modes": list(PRESENTATION_MODES)}

MAX_BODY = 2_000_000
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "application/javascript"),
    "/styles.css": ("styles.css", "text/css"),
}


@dataclass
class ReviewHandle:
    server: ThreadingHTTPServer
    thread: threading.Thread
    token: str
    origin: str

    @property
    def url(self):
        return self.origin + "/#token=" + self.token

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class BlindingViolation(ValueError):
    pass


def source_link_map(source_links):
    """Validate caller-supplied links from frozen sources to their public originals.

    ``{source_id: {"url": ..., "line_url": ..., "label": ..., "note": ...}}``.
    ``url`` opens the whole original; ``line_url``, when the original has stable
    line anchors, is a template whose ``{start}`` and ``{end}`` are replaced
    with the cited line numbers. Both must be https. Links are navigation aids
    served beside the bundle, never part of it, so adding one does not change a
    bundle hash."""
    links = {}
    for source_id, link in (source_links or {}).items():
        if not isinstance(source_id, str) or not isinstance(link, dict):
            raise ValueError("source link must map a source id to an object")
        extra = set(link) - {"url", "line_url", "label", "note"}
        if extra or not all(isinstance(v, str) for v in link.values()):
            raise ValueError(f"source link for {source_id} has unknown or non-text fields")
        url = link.get("url", "")
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.netloc or parts.fragment or parts.username or parts.password
                or any(ord(c) < 33 for c in url)):
            raise ValueError(f"source link for {source_id} needs an https url without a fragment")
        line_url = link.get("line_url", "")
        if line_url:
            filled = line_url.replace("{start}", "1").replace("{end}", "1")
            lp = urlsplit(filled)
            if (lp.scheme != "https" or not lp.netloc or lp.username or lp.password
                    or any(ord(c) < 33 for c in line_url) or "{start}" not in line_url
                    or "{" in filled or "}" in filled
                    or (lp.scheme, lp.netloc, lp.path) != (parts.scheme, parts.netloc, parts.path)
                    or not set(parse_qsl(parts.query)).issubset(set(parse_qsl(lp.query)))):
                raise ValueError(f"source link for {source_id} has an invalid line_url template")
        links[source_id] = {
            "url": url,
            "line_url": line_url,
            "label": link.get("label", ""),
            "note": link.get("note", ""),
        }
    return links


def open_review(
    bundle,
    store,
    hooks=None,
    launch=True,
    port=0,
    assessor="",
    blind_markers=(),
    source_links=None,
    workload=None,
    required_fields_only=False,
    atom_evidence=None,
    rendered_sources=None,
    presentation_mode="default",
    require_contracts=(),
):
    """Serve one review task on loopback.

    blind_markers are caller-known identity or machine-label strings (model
    names, arm codes, judge verdict tokens). Outside adjudication, a bundle or
    API response containing one is refused rather than shown.

    workload may be a callback receiving the active validated bundle, returning
    current caller-owned display counts. It does not change frozen bundle identity.
    source_links may likewise be a callback for per-task eligible links.

    required_fields_only hides optional form controls for independent tasks only.
    It is presentation metadata: frozen bundles, validation and saved judgments
    are unchanged. Other task kinds retain every control, including reference
    quantity checks. Saved optional answers that block submission can be reopened.

    atom_evidence (an atom-evidence/v1 manifest or a callback receiving the
    active bundle) and rendered_sources (RenderedSourceAsset objects, or a
    callback) are optional hash-bound sidecars; neither changes bundle or
    submission identity. presentation_mode="atomic-source-check/v1" shows the
    statements above, one row per atom on the left and the rendered source on
    the right; sources without a supplied rendering get a labelled rendering
    of their frozen text, and a bundle without atom evidence gets one quantity
    row per asserted number. require_contracts names contracts the caller
    depends on; an unsupported one is refused instead of silently ignored.

    source_links optionally maps source ids to their public originals (see
    ``source_link_map``); the UI offers each as a link that opens the original
    at the cited passage, so a reviewer can check the frozen copy against it.
    """
    if type(required_fields_only) is not bool:
        raise TypeError("required_fields_only must be a boolean")
    if presentation_mode not in PRESENTATION_MODES:
        raise UnsupportedContract(f"unsupported presentation mode: {presentation_mode}")
    missing = [c for c in require_contracts if c not in CONTRACTS]
    if missing:
        raise UnsupportedContract("unsupported contract(s): " + ", ".join(map(str, missing)))
    markers = blind_terms(blind_markers)

    def checked(b):
        b = validate_bundle(b.model_dump(mode="json") if hasattr(b, "model_dump") else b)
        if b.task_kind != "adjudication":
            found = blind_violations(b.model_dump(mode="json"), markers)
            if found:
                raise BlindingViolation(f"bundle {b.bundle_id} exposes {len(found)} blinded marker(s)")
        return b

    bundle = checked(bundle)
    def links_for(b):
        return source_link_map(source_links(b) if callable(source_links) else source_links)

    def workload_for(b):
        value = workload(b) if callable(workload) else workload
        if value is None:
            value = b.workload
        return ReviewWorkload.model_validate(value).model_dump(mode="json") if value is not None else None

    def sidecars_for(b):
        renders = {}
        supplied = rendered_sources(b) if callable(rendered_sources) else rendered_sources
        for asset in supplied or ():
            # Hash, allowlist, bundle binding and node proof of every exact target; refused otherwise.
            manifest = validate_render_asset(b, asset).manifest
            if manifest.source_id in renders:
                raise ValueError("two renderings supplied for one source")
            renders[manifest.source_id] = asset
        atoms = atom_evidence(b) if callable(atom_evidence) else atom_evidence
        if atoms is not None:
            atoms = validate_atom_evidence(b, atoms, [a.manifest for a in renders.values()])
        if presentation_mode == ATOMIC_SOURCE_CHECK:
            if atoms is None:
                atoms = build_atom_manifest(b)
            for s in b.sources:
                if s.source_id not in renders:
                    renders[s.source_id] = render_frozen_text(b, s.source_id, atoms)
        if b.task_kind != "adjudication":
            for asset in renders.values():
                if blind_violations([asset.derivative, asset.manifest.model_dump(mode="json")], markers):
                    raise BlindingViolation(f"rendered source {asset.manifest.source_id} exposes a blinded marker")
        return {"atoms": atoms, "renders": renders}

    links = [links_for(bundle)]
    workload_for(bundle)  # Reject invalid initial display metadata before serving.
    sidecars = [sidecars_for(bundle)]
    store.register(bundle)
    hooks = hooks or Hooks()
    token = secrets.token_urlsafe(32)
    current = [bundle]
    views = [evidence_views(bundle)]
    mutex = threading.RLock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No content/token in server logs.

        def response(self, code, body, mime="application/json"):
            if (
                mime == "application/json"
                and current[0].task_kind != "adjudication"
                and blind_violations(body, markers)
            ):
                # Never send it; the error names no marker.
                code, body = 500, {"error": "blinding violation: response withheld"}
            data = (
                json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                if mime == "application/json"
                else body
            )
            self.send_response(code)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def authorized(self, post=False):
            if self.headers.get("Host") != self.server.authority:
                return False
            if post and self.headers.get("Origin") != self.server.origin:
                return False
            supplied = self.headers.get("Authorization", "")
            return secrets.compare_digest(supplied, "Bearer " + token)

        def do_GET(self):
            if self.headers.get("Host") != self.server.authority:
                self.response(403, {"error": "invalid host"})
                return
            if self.path in ASSETS:
                name, mime = ASSETS[self.path]
                self.response(
                    200,
                    files("evidence_review").joinpath("ui", name).read_bytes(),
                    mime,
                )
                return
            if not self.authorized():
                self.response(403, {"error": "authorization required"})
                return
            with mutex:
                if self.path.startswith("/api/source-render/") or self.path.startswith("/api/source-target/"):
                    self.rendered()
                    return
                if self.path == "/api/bundle":
                    self.response(200, current[0].model_dump(mode="json"))
                elif self.path == "/api/evidence":
                    try:
                        display_workload = workload_for(current[0])
                    except (ValueError, TypeError) as exc:
                        self.response(500, {"error": "invalid caller workload: " + str(exc)})
                        return
                    self.response(
                        200,
                        {
                            "views": views[0],
                            "inventory": inventory(current[0]),
                            "navigation": navigation_coverage(current[0], links[0]),
                            "workload": display_workload,
                            "presentation": {
                                "required_fields_only": required_fields_only and current[0].task_kind == "independent",
                                "mode": presentation_mode,
                            },
                            "capabilities": capabilities(),
                            "atoms": atom_view(current[0], sidecars[0]["atoms"],
                                               [a.manifest for a in sidecars[0]["renders"].values()])
                            if sidecars[0]["atoms"] is not None else None,
                            "rendered_sources": {
                                sid: {k: a.manifest.model_dump(mode="json")[k] for k in (
                                    "render_mode", "lineage", "fidelity_note", "derivative_sha256", "raw_sha256",
                                    "frozen_sha256", "raw_media_type", "transforms")}
                                for sid, a in sidecars[0]["renders"].items()
                            },
                            "diagnostics": {
                                "package_version": PACKAGE_VERSION,
                                "bundle_hash": current[0].bundle_hash,
                                "asset_sha256": {
                                    name: hashlib.sha256(files("evidence_review").joinpath("ui", name).read_bytes()).hexdigest()
                                    for name, _ in ASSETS.values()
                                },
                            },
                            "links": {
                                s.source_id: links[0][s.source_id]
                                for s in current[0].sources
                                if s.source_id in links[0]
                            },
                        },
                    )
                elif self.path == "/api/state":
                    self.response(
                        200,
                        {
                            **store.load_task(current[0].bundle_id),
                            "suggested_assessor": assessor,
                        },
                    )
                else:
                    self.response(404, {"error": "unknown path"})

        def rendered(self):
            """Protected rendered-source routes: allowlisted ids, current bundle only, re-verified bytes."""
            kind, _, ident = self.path[len("/api/"):].partition("/")
            renders = sidecars[0]["renders"]

            def proven(asset):
                """Re-run the shared proof (hash, allowlist, line correspondence, exact targets) before serving."""
                try:
                    validate_render_asset(current[0], asset)
                except ValueError as exc:
                    self.response(500, {"error": str(exc)})
                    return False
                return True

            if kind == "source-render":
                if not SAFE_SOURCE.match(ident) or ident not in renders:
                    self.response(404, {"error": "no rendered source with that id in this task"})
                    return
                asset = renders[ident]
                if not proven(asset):
                    return
                body = {"manifest": asset.manifest.model_dump(mode="json"), "derivative": asset.derivative}
                if len(json.dumps(body, ensure_ascii=False)) > MAX_RENDER_RESPONSE:
                    self.response(413, {"error": "rendered source exceeds the response bound"})
                    return
                self.response(200, body)
                return
            if not SAFE_TARGET.match(ident):
                self.response(404, {"error": "invalid target id"})
                return
            for asset in renders.values():
                target = next((t for t in asset.manifest.targets if t.target_id == ident), None)
                if target is not None:
                    if not proven(asset):
                        return
                    self.response(200, {"target": target.model_dump(mode="json"),
                                        "derivative_sha256": asset.manifest.derivative_sha256})
                    return
            self.response(404, {"error": "no rendered target with that id in this task"})

        def do_POST(self):
            if not self.authorized(post=True):
                self.response(403, {"error": "invalid authorization or origin"})
                return
            if self.path not in (
                "/api/save",
                "/api/submit",
                "/api/reconcile",
                "/api/next",
                "/api/event",
            ):
                self.response(404, {"error": "unknown path"})
                return
            try:
                if (
                    self.headers.get("Content-Type", "").split(";")[0]
                    != "application/json"
                ):
                    raise ValueError("JSON required")
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > MAX_BODY:
                    raise ValueError("invalid body size")
                body = json.loads(self.rfile.read(length))
                with mutex:
                    b = current[0]
                    if (
                        body.get("bundle_id") != b.bundle_id
                        or body.get("bundle_hash") != b.bundle_hash
                    ):
                        raise Conflict("bundle changed")
                    if self.path == "/api/event":
                        kind, subject = body.get("kind"), body.get("subject", "")
                        if (
                            set(body) - {"bundle_id", "bundle_hash", "kind", "subject"}
                            or kind not in EVENT_KINDS
                            or not isinstance(subject, str)
                            or len(subject) > 200
                        ):
                            raise ValueError("invalid navigation event")
                        store.append_event(
                            b.bundle_id,
                            {"kind": kind, "subject": subject, "verification": False},
                        )
                        self.response(200, {"recorded": True})
                        return
                    if self.path == "/api/next":
                        state = store.load_task(b.bundle_id)
                        if (
                            not submission_matches_snapshot(state)
                            or state["hook"]["status"] != "succeeded"
                        ):
                            raise Conflict("submission/continuation incomplete")
                        nxt = hooks.next_bundle() if hooks.next_bundle else None
                        if nxt is None:
                            self.response(200, {"done": True})
                            return
                        nxt = checked(nxt)
                        store.register(nxt)
                        next_links = links_for(nxt)
                        workload_for(nxt)
                        next_sidecars = sidecars_for(nxt)
                        current[0] = nxt
                        links[0] = next_links
                        sidecars[0] = next_sidecars
                        views[0] = evidence_views(nxt)
                        self.response(200, {"bundle": nxt.model_dump(mode="json")})
                        return
                    if self.path == "/api/reconcile":
                        state = store.load_task(b.bundle_id)
                        if state["last_submission"] is None:
                            raise ValueError("nothing submitted")
                        state = run_hook(
                            store,
                            b.bundle_id,
                            state["last_submission"],
                            hooks,
                            reconcile=True,
                        )
                    else:
                        allowed = {
                            "bundle_id",
                            "bundle_hash",
                            "revision",
                            "key",
                            "answers",
                            "assessor",
                            "active_seconds",
                            "amendment_reason",
                        }
                        if set(body) - allowed:
                            raise ValueError("unknown request field")
                        fn = (
                            store.save_submission
                            if self.path == "/api/submit"
                            else store.save_snapshot
                        )
                        kwargs = (
                            {"amendment_reason": body.get("amendment_reason", "")}
                            if self.path == "/api/submit"
                            else {}
                        )
                        state = fn(
                            b,
                            body["revision"],
                            body["key"],
                            body["answers"],
                            body["assessor"],
                            body.get("active_seconds", 0),
                            **kwargs,
                        )
                        if self.path == "/api/submit":
                            state = run_hook(
                                store, b.bundle_id, state["last_submission"], hooks
                            )
                    self.response(200, state)
            except Conflict as exc:
                self.response(409, {"error": str(exc)})
            except (ValueError, KeyError, TypeError) as exc:
                self.response(400, {"error": str(exc)})
            except Exception as exc:
                # Durable state remains recoverable. Storage/hook failures are visible, never acknowledged as saved.
                self.response(500, {"error": f"{type(exc).__name__}: {exc}"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.authority = f"127.0.0.1:{server.server_port}"
    server.origin = "http://" + server.authority
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handle = ReviewHandle(server, thread, token, server.origin)
    if launch:
        webbrowser.open(handle.url)
    return handle
