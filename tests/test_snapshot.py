import hashlib

import httpx
import pytest
import yaml

from sdr.network_policy import NetworkPolicyError, RedirectRecord
from sdr.parser import parse_artifact
from sdr.research import Research
from sdr.snapshot import FetchResult, assign_source_ids, capture_source_snapshot, fetch_url

PUBLIC_IP = "93.184.216.34"


def _public_resolver(host: str):
    return [PUBLIC_IP]


def _fetch_result(
    declared_url: str,
    *,
    final_url: str | None = None,
    redirects: tuple[RedirectRecord, ...] = (),
    status_code: int = 200,
    content_type: str = "text/html",
    content_eligible: bool = True,
    text: str = "",
) -> FetchResult:
    return FetchResult(
        declared_url=declared_url,
        final_url=final_url or declared_url,
        redirects=redirects,
        status_code=status_code,
        content_type=content_type,
        content_eligible=content_eligible,
        text=text,
    )


def test_capture_source_snapshot_persists_meta_and_content(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")

    def fetcher(url: str) -> FetchResult:
        return _fetch_result(
            url,
            text="<html><head><title>Doc Foo</title></head><body><main>Foo soporta modo offline.</main></body></html>",
        )

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url="https://docs.foo.dev/guide",
        fetcher=fetcher,
    )

    meta_path = r.root / "notes" / "sources" / "S1" / "meta.yaml"
    content_path = r.root / "notes" / "sources" / "S1" / "content.md"
    assert result.status == "ok"
    assert result.content_hash
    assert meta_path.exists()
    assert content_path.read_text(encoding="utf-8") == "Foo soporta modo offline.\n"
    meta_text = meta_path.read_text(encoding="utf-8")
    assert "schema_version: 2" in meta_text
    assert "declared_url: https://docs.foo.dev/guide" in meta_text
    assert "final_url: https://docs.foo.dev/guide" in meta_text
    assert "title: Doc Foo" in meta_text
    assert "http_status: 200" in meta_text
    assert "org: foo" in meta_text
    assert "status: ok" in meta_text


def test_capture_source_snapshot_marks_empty_content_unverifiable(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")

    def fetcher(url: str) -> FetchResult:
        return _fetch_result(url, text="<html><title>Paywall</title></html>")

    result = capture_source_snapshot(
        r,
        source_id="S2",
        url="https://example.com/paywall",
        fetcher=fetcher,
    )

    meta_path = r.root / "notes" / "sources" / "S2" / "meta.yaml"
    content_path = r.root / "notes" / "sources" / "S2" / "content.md"
    assert result.status == "unverifiable"
    assert content_path.read_text(encoding="utf-8") == ""
    assert "status: unverifiable" in meta_path.read_text(encoding="utf-8")


def test_fetch_url_rejects_response_larger_than_limit_without_buffering_it():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 11,
        )
    )

    with pytest.raises(NetworkPolicyError, match="tamaño máximo"):
        fetch_url(
            "https://public.example/large",
            mock_transport=transport,
            resolver=_public_resolver,
            max_response_bytes=10,
        )


def test_fetch_url_represents_unsupported_content_type_without_binary_text():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"binary",
        )
    )

    result = fetch_url(
        "https://public.example/file",
        mock_transport=transport,
        resolver=_public_resolver,
    )

    assert result.status_code == 200
    assert result.content_type == "application/octet-stream"
    assert result.content_eligible is False
    assert result.text == ""


def test_assign_source_ids_preserves_declaration_order(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    note = r.root / "notes" / "n1.md"
    note.write_text(
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        "sources:\n"
        "  - url: https://a.example.com\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://b.example.com\n    tier: T2\n    date: 2026-01-02\n"
        "---\n\n"
        "## Alternativas evaluadas\nFoo.\n",
        encoding="utf-8",
    )

    changed = assign_source_ids(r)

    sources = parse_artifact(note).frontmatter["sources"]
    assert changed == 2
    assert [source["id"] for source in sources] == ["S1", "S2"]


def test_assign_source_ids_keeps_existing_ids_and_fills_gaps(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    note = r.root / "notes" / "n1.md"
    note.write_text(
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        "sources:\n"
        "  - id: S7\n    url: https://a.example.com\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://b.example.com\n    tier: T2\n    date: 2026-01-02\n"
        "---\n\n"
        "## Alternativas evaluadas\nFoo.\n",
        encoding="utf-8",
    )

    changed = assign_source_ids(r)

    sources = parse_artifact(note).frontmatter["sources"]
    assert changed == 1
    assert [source["id"] for source in sources] == ["S7", "S8"]


def test_capture_declared_sources_generates_orgs_yaml(tmp_path):
    from sdr.snapshot import capture_declared_sources

    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    note = r.root / "notes" / "n1.md"
    note.write_text(
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        "sources:\n"
        "  - url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://github.com/bar/repo\n    tier: T1\n    date: 2026-01-01\n"
        "---\n\n"
        "## Alternativas evaluadas\nFoo.\n",
        encoding="utf-8",
    )

    def fetcher(url: str) -> FetchResult:
        return _fetch_result(url, text="<html><body>Contenido.</body></html>")

    capture_declared_sources(r, fetcher=fetcher)

    orgs = (r.root / "notes" / "sources" / "orgs.yaml").read_text(encoding="utf-8")
    assert "sources:" in orgs
    assert "S1: foo" in orgs
    assert "S2: bar" in orgs
    assert "aliases:" in orgs


def test_capture_declared_sources_uses_existing_snapshot_cache(tmp_path):
    from sdr.snapshot import capture_declared_sources

    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    note = r.root / "notes" / "n1.md"
    note.write_text(
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        "sources:\n"
        "  - url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "---\n\n"
        "## Alternativas evaluadas\nFoo.\n",
        encoding="utf-8",
    )
    calls = 0

    def fetcher(url: str) -> FetchResult:
        nonlocal calls
        calls += 1
        return _fetch_result(url, text="<html><body>Contenido.</body></html>")

    first = capture_declared_sources(r, fetcher=fetcher)
    second = capture_declared_sources(r, fetcher=fetcher)

    assert len(first) == 1
    assert second == []
    assert calls == 1


def test_capture_source_snapshot_writes_versioned_complete_provenance_and_exact_byte_hash(
    tmp_path,
):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"
    final_url = "https://cdn.foo.dev/current"
    redirects = (
        RedirectRecord(
            url=declared_url,
            status_code=301,
            location="https://cdn.foo.dev/current",
            target_url=final_url,
        ),
    )

    capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            final_url=final_url,
            redirects=redirects,
            text="<html><body><main>Exact persisted evidence.</main></body></html>",
        ),
    )

    source_dir = r.root / "notes" / "sources" / "S1"
    persisted = source_dir.joinpath("content.md").read_bytes()
    meta = yaml.safe_load(source_dir.joinpath("meta.yaml").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 2
    assert meta["declared_url"] == declared_url
    assert meta["final_url"] == final_url
    assert meta["redirects"] == [
        {
            "url": declared_url,
            "status_code": 301,
            "location": final_url,
            "target_url": final_url,
        }
    ]
    assert meta["http_status"] == 200
    assert meta["captured_at"]
    assert meta["status"] == "ok"
    assert meta["content_type"] == "text/html"
    assert meta["content_eligible"] is True
    assert meta["content_hash"] == hashlib.sha256(persisted).hexdigest()
    assert persisted == b"Exact persisted evidence.\n"


@pytest.mark.parametrize(
    ("fetch_kwargs", "expected_status", "expected_http_status", "expected_eligible"),
    [
        (
            {
                "status_code": 404,
                "text": "<html><body>Error page must not become evidence.</body></html>",
            },
            "unverifiable",
            404,
            True,
        ),
        (
            {
                "content_type": "application/octet-stream",
                "content_eligible": False,
                "text": "binary-looking text",
            },
            "unverifiable",
            200,
            False,
        ),
        (
            {"text": "<html><title>No extractable body</title></html>"},
            "unverifiable",
            200,
            True,
        ),
        (
            {
                "declared_url": "https://wrong.example/identity",
                "final_url": "https://wrong.example/identity",
                "text": "This provenance is incomplete for the declaration.",
            },
            "unverifiable",
            200,
            True,
        ),
    ],
)
def test_capture_source_snapshot_fails_closed_and_preserves_actual_outcome(
    tmp_path,
    fetch_kwargs,
    expected_status,
    expected_http_status,
    expected_eligible,
):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = fetch_kwargs.pop("declared_url", "https://docs.foo.dev/guide")
    fetched = _fetch_result(declared_url, **fetch_kwargs)

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url="https://docs.foo.dev/guide",
        fetcher=lambda url: fetched,
    )

    source_dir = r.root / "notes" / "sources" / "S1"
    content = source_dir.joinpath("content.md").read_bytes()
    meta = yaml.safe_load(source_dir.joinpath("meta.yaml").read_text(encoding="utf-8"))
    assert result.status == expected_status
    assert content == b""
    assert meta["status"] == expected_status
    assert meta["http_status"] == expected_http_status
    assert meta["content_eligible"] is expected_eligible
    assert meta["declared_url"] == fetched.declared_url
    assert meta["final_url"] == fetched.final_url
    assert meta["content_hash"] == hashlib.sha256(b"").hexdigest()


def test_capture_source_snapshot_keeps_declared_url_as_identity_after_redirect(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"
    final_url = "https://other.example/mirror"
    redirect = RedirectRecord(
        url=declared_url,
        status_code=302,
        location=final_url,
        target_url=final_url,
    )

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            final_url=final_url,
            redirects=(redirect,),
            text="<html><body>Redirected evidence.</body></html>",
        ),
    )

    meta = yaml.safe_load(
        (r.root / "notes" / "sources" / "S1" / "meta.yaml").read_text(encoding="utf-8")
    )
    assert result.url == declared_url
    assert meta["declared_url"] == declared_url
    assert meta["final_url"] == final_url
    assert meta["org"] == "foo"
    assert meta["declared_url"] != meta["final_url"]


def test_cross_organization_redirect_reports_locations_without_identity_inference(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://research.alpha.example/report"
    intermediate_url = "https://routing.example/alpha-report"
    final_url = "https://publisher.beta.example/mirror"
    redirects = (
        RedirectRecord(
            url=declared_url,
            status_code=302,
            location=intermediate_url,
            target_url=intermediate_url,
        ),
        RedirectRecord(
            url=intermediate_url,
            status_code=301,
            location=final_url,
            target_url=final_url,
        ),
    )

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            final_url=final_url,
            redirects=redirects,
            text="<html><body>A locally persisted statement.</body></html>",
        ),
    )
    meta = yaml.safe_load(
        (r.root / "notes" / "sources" / "S1" / "meta.yaml").read_text(encoding="utf-8")
    )
    rendered = yaml.safe_dump({"result": result.to_dict(), "metadata": meta}).lower()

    assert result.declared_url == declared_url
    assert result.final_url == final_url
    assert result.redirects == redirects
    assert [redirect["target_url"] for redirect in meta["redirects"]] == [
        intermediate_url,
        final_url,
    ]
    for overclaim in (
        "publisher_identity",
        "authenticated_publisher",
        "independence",
        "independent",
        "authorship",
    ):
        assert overclaim not in rendered


@pytest.mark.parametrize("injected_status", [300, 304, 305, 306])
def test_capture_source_snapshot_rejects_redirect_statuses_not_followed_by_policy(
    tmp_path, injected_status
):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"
    final_url = "https://cdn.foo.dev/current"
    redirect = RedirectRecord(
        url=declared_url,
        status_code=injected_status,
        location=final_url,
        target_url=final_url,
    )

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            final_url=final_url,
            redirects=(redirect,),
            text="Injected redirect provenance must not become evidence.",
        ),
    )

    assert result.status == "unverifiable"
    assert (r.root / "notes" / "sources" / "S1" / "content.md").read_bytes() == b""


def test_failed_capture_preserves_fetchers_actual_outcome_metadata(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"
    actual_declared_url = "https://unexpected.example/request"
    final_url = "https://unexpected.example/failure"
    redirect = RedirectRecord(
        url=actual_declared_url,
        status_code=302,
        location=final_url,
        target_url=final_url,
    )

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            actual_declared_url,
            final_url=final_url,
            redirects=(redirect,),
            status_code=503,
            content_type="text/html",
            content_eligible=True,
            text="Service unavailable.",
        ),
    )

    meta = yaml.safe_load(
        (r.root / "notes" / "sources" / "S1" / "meta.yaml").read_text(encoding="utf-8")
    )
    assert result.url == declared_url
    assert result.status == "unverifiable"
    assert result.declared_url == actual_declared_url
    assert result.final_url == final_url
    assert meta["url"] == declared_url
    assert meta["declared_url"] == actual_declared_url
    assert meta["final_url"] == final_url
    assert meta["http_status"] == 503
    assert meta["redirects"] == [
        {
            "url": actual_declared_url,
            "status_code": 302,
            "location": final_url,
            "target_url": final_url,
        }
    ]


@pytest.mark.parametrize("content_type", ["", "application/octet-stream"])
def test_capture_derives_content_eligibility_from_canonical_content_type_policy(
    tmp_path, content_type
):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            content_type=content_type,
            content_eligible=True,
            text="Untrusted eligibility must not make this evidence.",
        ),
    )

    meta = yaml.safe_load(
        (r.root / "notes" / "sources" / "S1" / "meta.yaml").read_text(encoding="utf-8")
    )
    assert result.status == "unverifiable"
    assert meta["content_eligible"] is False
    assert (r.root / "notes" / "sources" / "S1" / "content.md").read_bytes() == b""


@pytest.mark.parametrize(
    ("final_url", "redirects"),
    [
        ("file:///etc/passwd", ()),
        ("https://user:secret@docs.foo.dev/guide", ()),
        ("https://docs.foo.dev:70000/guide", ()),
        ("https://bad host.example/guide", ()),
        ("http://127.0.0.1/private", ()),
        (
            "file:///etc/passwd",
            (
                RedirectRecord(
                    url="https://docs.foo.dev/guide",
                    status_code=302,
                    location="file:///etc/passwd",
                    target_url="file:///etc/passwd",
                ),
            ),
        ),
    ],
)
def test_capture_rejects_structurally_impossible_provenance_urls(tmp_path, final_url, redirects):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "https://docs.foo.dev/guide"

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(
            url,
            final_url=final_url,
            redirects=redirects,
            text="Impossible retrieval provenance must not become evidence.",
        ),
    )

    assert result.status == "unverifiable"
    assert (r.root / "notes" / "sources" / "S1" / "content.md").read_bytes() == b""


def test_capture_rejects_structurally_invalid_declared_url(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    declared_url = "file:///etc/passwd"

    result = capture_source_snapshot(
        r,
        source_id="S1",
        url=declared_url,
        fetcher=lambda url: _fetch_result(url, text="Local files cannot be HTTP evidence."),
    )

    assert result.status == "unverifiable"
    assert (r.root / "notes" / "sources" / "S1" / "content.md").read_bytes() == b""
