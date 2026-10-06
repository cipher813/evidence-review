"""Protect loopback authorization, untrusted text and acknowledged HTTP persistence."""

import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from evidence_review import open_review
from evidence_review.example import example_bundle
from evidence_review.store import FileStore
from test_store import answer


def request(handle, path, method="GET", payload=None, **headers):
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(
        handle.origin + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + handle.token,
            "Content-Type": "application/json",
            **headers,
        },
    )
    return urlopen(req)


def test_server_authorization_and_save(tmp_path):
    b = example_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        with pytest.raises(HTTPError) as error:
            urlopen(h.origin + "/api/bundle")
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            request(h, "/api/state", Host="evil.invalid")
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            request(h, "/api/save", "POST", {}, Origin="http://evil.invalid")
        assert error.value.code == 403
        body = {
            "bundle_id": b.bundle_id,
            "bundle_hash": b.bundle_hash,
            "revision": 0,
            "key": "save-1",
            "answers": answer(),
            "assessor": "Ada",
            "active_seconds": 2,
        }
        with request(h, "/api/save", "POST", body, Origin=h.origin) as r:
            assert json.load(r)["revision"] == 1
        body.update(revision=1, key="submit-1")
        with request(h, "/api/submit", "POST", body, Origin=h.origin) as r:
            assert json.load(r)["hook"]["status"] == "succeeded"
        with request(h, "/") as r:
            assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        with pytest.raises(HTTPError):
            request(h, "/../../etc/passwd")
        with pytest.raises(HTTPError):
            request(h, "/api/save", "POST", body)


def test_independent_payload_has_no_private_storage_or_disclosures(tmp_path):
    b = example_bundle()
    with open_review(b, FileStore(tmp_path), launch=False) as h:
        with request(h, "/api/bundle") as r:
            data = json.load(r)
        assert not data["disclosures"]
        assert str(tmp_path) not in json.dumps(data)
        assert not any(k in data for k in ["identity", "judge", "model", "arm"])


def test_source_links_are_validated_and_served_beside_the_bundle(tmp_path):
    b = example_bundle()
    sid = b.sources[0].source_id
    for bad in (
        {sid: {"url": "http://example.com/doc"}},
        {sid: {"url": "javascript:alert(1)"}},
        {sid: {"url": "https://example.com/doc#frag"}},
        {sid: {"url": "https://example.com/doc", "extra": "x"}},
        {sid: "https://example.com/doc"},
        {sid: {"url": "https://example.com/doc", "line_url": "https://example.com/doc#L1"}},
        {sid: {"url": "https://example.com/doc", "line_url": "http://example.com/doc#L{start}"}},
        {sid: {"url": "https://example.com/doc", "line_url": "https://example.com/doc#L{start}-{bad}"}},
    ):
        with pytest.raises(ValueError):
            open_review(b, FileStore(tmp_path / "bad"), launch=False, source_links=bad)
    links = {
        sid: {
            "url": "https://github.com/o/r/blob/abc/doc.md?plain=1",
            "line_url": "https://github.com/o/r/blob/abc/doc.md?plain=1#L{start}-L{end}",
            "label": "Open in source repo",
            "note": "same bytes",
        },
        "not-in-bundle": {"url": "https://example.com/other"},
    }
    with open_review(b, FileStore(tmp_path), launch=False, source_links=links) as h:
        with request(h, "/api/evidence") as r:
            served = json.load(r)["links"]
        with request(h, "/api/bundle") as r:
            assert "github.com" not in r.read().decode()
    assert served == {sid: links[sid]}


def test_navigation_coverage_matches_served_assets_and_reports_unresolved_reasons(tmp_path):
    import hashlib
    from importlib.resources import files
    from evidence_review.contracts import PACKAGE_VERSION
    from test_browser_review import bound_bundle
    b = bound_bundle()
    links = {'filing': {'url': 'https://example.com/frozen', 'line_url': 'https://example.com/frozen#L{start}-L{end}'}}
    with open_review(b, FileStore(tmp_path), launch=False, source_links=links) as h:
        with request(h, '/api/evidence') as r:
            d = json.load(r)
        with request(h, '/app.js') as r:
            asset = r.read()
        assert d['diagnostics']['package_version'] == PACKAGE_VERSION
        assert d['diagnostics']['asset_sha256']['app.js'] == hashlib.sha256(asset).hexdigest()
        coverage = d['navigation']
        assert coverage['direct_spans'] == 1 and coverage['derived_spans'] == 1
        assert coverage['cited_operands_with_links'] == 2
        assert any(n['reason'] for n in coverage['unresolved'])


@pytest.mark.parametrize('url,line_url', [
    ('https://user:secret@example.com/doc', ''),
    ('https://example.com/doc', 'https://other.example/doc#L{start}'),
    ('https://example.com/doc', 'https://user:secret@example.com/doc#L{start}'),
    ('https://example.com/doc\n', ''),
])
def test_source_links_refuse_credentials_control_characters_and_changed_destination(url, line_url):
    from evidence_review.server import source_link_map
    with pytest.raises(ValueError):
        source_link_map({'s': {'url': url, 'line_url': line_url}})


def test_provider_neutral_query_line_locator_preserves_immutable_query():
    from evidence_review.server import source_link_map
    link = {'url': 'https://example.org/frozen.txt?revision=pinned', 'line_url': 'https://example.org/frozen.txt?revision=pinned&start={start}&end={end}'}
    assert source_link_map({'s': link})['s']['line_url'] == link['line_url']
    with pytest.raises(ValueError):
        source_link_map({'s': {**link, 'line_url': 'https://example.org/frozen.txt?revision=current&start={start}'}})


def test_live_display_workload_and_links_update_without_mutating_bundle_identity(tmp_path):
    b = example_bundle()
    bundle_hash = b.bundle_hash
    display = {'answer_index': 1, 'assigned_answers': 3, 'task_counts': {'independent': 3}}
    link = {'url': 'https://example.com/frozen', 'line_url': 'https://example.com/frozen#L{start}'}
    with open_review(b, FileStore(tmp_path), launch=False,
                     workload=lambda current: display,
                     source_links=lambda current: {current.sources[0].source_id: link}) as h:
        with request(h, '/api/evidence') as r:
            first = json.load(r)
        display.update(assigned_answers=9, task_counts={'independent': 9})
        with request(h, '/api/evidence') as r:
            second = json.load(r)
        assert first['workload']['assigned_answers'] == 3
        assert second['workload']['assigned_answers'] == 9
        with request(h, '/api/bundle') as r:
            assert json.load(r)['workload'] is None
    assert b.bundle_hash == bundle_hash
