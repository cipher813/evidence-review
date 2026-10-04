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
