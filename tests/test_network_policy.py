import threading
import time

import httpx
import pytest

from sdr.network_policy import (
    MAX_BLOCKING_WORKERS,
    NetworkPolicyError,
    fetch_http,
    is_supported_text_content_type,
)

PUBLIC_IP = "93.184.216.34"
MAX_HEADER_VALUE_BYTES = 8 * 1024
MAX_HEADER_BYTES = 64 * 1024


def _resolver(mapping):
    def resolve(host: str):
        return mapping.get(host, [PUBLIC_IP])

    return resolve


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("text/html; charset=utf-8", True),
        ("Application/JSON", True),
        ("application/octet-stream", False),
        ("text/plain, application/octet-stream", False),
        ("text/", False),
        ("text/plain@invalid", False),
        ("text/plain; charset=utf-8; boundary=valid-token", True),
        ("text/plain; charset", False),
        ("", False),
    ],
)
def test_supported_text_content_type_policy_is_canonical_and_pure(content_type, expected):
    assert is_supported_text_content_type(content_type) is expected


@pytest.mark.parametrize("url", ["ftp://example.com/file", "file:///etc/passwd", "//example.com"])
def test_fetch_http_rejects_non_http_schemes_before_request(url):
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))

    with pytest.raises(NetworkPolicyError, match="HTTP/HTTPS"):
        fetch_http(url, mock_transport=transport, resolver=_resolver({}))

    assert calls == []


def test_fetch_http_rejects_hostname_resolving_to_private_address_before_request():
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))

    with pytest.raises(NetworkPolicyError, match="non-public"):
        fetch_http(
            "https://public.example/resource",
            mock_transport=transport,
            resolver=_resolver({"public.example": ["10.0.0.7"]}),
        )

    assert calls == []


def test_fetch_http_revalidates_redirect_target_and_blocks_private_destination():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    transport = httpx.MockTransport(handler)

    with pytest.raises(NetworkPolicyError, match="non-public"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({"public.example": [PUBLIC_IP]}),
        )

    assert calls == [f"https://{PUBLIC_IP}/start"]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "100.100.100.200",
    ],
)
def test_fetch_http_blocks_non_public_and_cloud_metadata_addresses(address):
    with pytest.raises(NetworkPolicyError, match="non-public|metadata"):
        fetch_http(f"http://{address}/", resolver=_resolver({}))


def test_fetch_http_limits_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"})

    transport = httpx.MockTransport(handler)

    with pytest.raises(NetworkPolicyError, match="redirects"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({"public.example": [PUBLIC_IP]}),
            max_redirects=2,
        )


def test_fetch_http_returns_distinct_declared_and_terminal_response_provenance():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8", "x-result": "terminal"},
            content=b"bounded body",
        )
    )

    result = fetch_http(
        "https://public.example/declared",
        mock_transport=transport,
        resolver=_resolver({}),
        max_response_bytes=64,
        require_text_content=True,
    )

    assert result.declared_url == "https://public.example/declared"
    assert result.final_url == "https://public.example/declared"
    assert result.redirects == ()
    assert result.status_code == 200
    assert result.headers["x-result"] == "terminal"
    assert result.content == b"bounded body"


def test_fetch_http_returns_complete_ordered_redirect_records():
    responses = {
        f"https://{PUBLIC_IP}/declared": httpx.Response(
            301,
            headers={"location": "/moved"},
        ),
        f"https://{PUBLIC_IP}/moved": httpx.Response(
            307,
            headers={"location": "https://final.example/resource"},
        ),
        f"https://{PUBLIC_IP}/resource": httpx.Response(
            204,
            headers={"content-type": "text/plain"},
        ),
    }
    transport = httpx.MockTransport(lambda request: responses[str(request.url)])

    result = fetch_http(
        "https://public.example/declared",
        mock_transport=transport,
        resolver=_resolver({}),
        max_response_bytes=64,
        require_text_content=True,
    )

    assert result.declared_url == "https://public.example/declared"
    assert result.final_url == "https://final.example/resource"
    assert [
        (redirect.url, redirect.status_code, redirect.location, redirect.target_url)
        for redirect in result.redirects
    ] == [
        (
            "https://public.example/declared",
            301,
            "/moved",
            "https://public.example/moved",
        ),
        (
            "https://public.example/moved",
            307,
            "https://final.example/resource",
            "https://final.example/resource",
        ),
    ]
    assert result.status_code == 204


def test_fetch_http_returns_bounded_non_2xx_terminal_response_provenance():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            404,
            headers={"content-type": "application/octet-stream", "x-result": "not-found"},
            content=b"missing",
        )
    )

    result = fetch_http(
        "https://public.example/missing",
        mock_transport=transport,
        resolver=_resolver({}),
        max_response_bytes=7,
        require_text_content=True,
    )

    assert result.declared_url == "https://public.example/missing"
    assert result.final_url == "https://public.example/missing"
    assert result.redirects == ()
    assert result.status_code == 404
    assert result.headers["x-result"] == "not-found"
    assert result.content == b"missing"


def test_fetch_http_keeps_size_bound_for_non_2xx_terminal_response():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            503,
            headers={"content-type": "application/octet-stream"},
            content=b"too large",
        )
    )

    with pytest.raises(NetworkPolicyError, match="maximum size"):
        fetch_http(
            "https://public.example/unavailable",
            mock_transport=transport,
            resolver=_resolver({}),
            max_response_bytes=4,
            require_text_content=True,
        )


def test_fetch_http_pins_deterministic_public_ip_and_preserves_host_and_sni():
    requests = []
    transport = httpx.MockTransport(
        lambda request: (
            requests.append(request)
            or httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
        )
    )

    result = fetch_http(
        "https://public.example:8443/path?q=visible",
        mock_transport=transport,
        resolver=_resolver({"public.example": ["93.184.216.35", PUBLIC_IP]}),
        max_response_bytes=8,
        require_text_content=True,
    )

    assert str(requests[0].url) == f"https://{PUBLIC_IP}:8443/path?q=visible"
    assert requests[0].headers["host"] == "public.example:8443"
    assert requests[0].extensions["sni_hostname"] == "public.example"
    assert result.declared_url == "https://public.example:8443/path?q=visible"
    assert result.final_url == "https://public.example:8443/path?q=visible"


def test_fetch_http_re_resolves_and_re_pins_every_redirect_target():
    requests = []
    resolutions = []

    def resolver(host):
        resolutions.append(host)
        return {"first.example": [PUBLIC_IP], "second.example": ["93.184.216.35"]}[host]

    def handler(request):
        requests.append(request)
        if request.headers["host"] == "first.example":
            return httpx.Response(302, headers={"location": "https://second.example/end"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"done")

    result = fetch_http(
        "https://first.example/start",
        mock_transport=httpx.MockTransport(handler),
        resolver=resolver,
        max_response_bytes=8,
        require_text_content=True,
    )

    assert resolutions == ["first.example", "second.example"]
    assert [str(request.url) for request in requests] == [
        f"https://{PUBLIC_IP}/start",
        "https://93.184.216.35/end",
    ]
    assert result.final_url == "https://second.example/end"
    assert result.redirects[0].target_url == "https://second.example/end"


def test_fetch_http_does_not_send_same_ip_cookies_to_a_redirected_hostname():
    requests = []

    def handler(request):
        requests.append(request)
        if request.headers["host"] == "first.example":
            return httpx.Response(
                302,
                headers={
                    "location": "https://second.example/end",
                    "set-cookie": "origin_secret=first-only; Path=/",
                },
            )
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"done")

    fetch_http(
        "https://first.example/start",
        mock_transport=httpx.MockTransport(handler),
        resolver=_resolver({"first.example": [PUBLIC_IP], "second.example": [PUBLIC_IP]}),
        max_response_bytes=8,
        require_text_content=True,
    )

    assert [request.headers["host"] for request in requests] == [
        "first.example",
        "second.example",
    ]
    assert "cookie" not in requests[1].headers


def test_fetch_http_uses_and_closes_a_fresh_client_for_every_logical_hop(monkeypatch):
    from sdr import network_policy

    original_client = httpx.Client
    clients = []

    def recording_client(*args, **kwargs):
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    def handler(request):
        if request.headers["host"] == "first.example":
            return httpx.Response(302, headers={"location": "https://second.example/end"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"done")

    monkeypatch.setattr(network_policy.httpx, "Client", recording_client)
    fetch_http(
        "https://first.example/start",
        mock_transport=httpx.MockTransport(handler),
        resolver=_resolver({"first.example": [PUBLIC_IP], "second.example": [PUBLIC_IP]}),
        max_response_bytes=8,
        require_text_content=True,
    )

    assert len(clients) == 2
    assert all(client.is_closed for client in clients)


def test_fetch_http_falls_back_across_validated_addresses_without_changing_origin():
    requests = []
    first_ip = PUBLIC_IP
    second_ip = "93.184.216.35"

    def handler(request):
        requests.append(request)
        if request.url.host == first_ip:
            raise httpx.ConnectError("first address failed", request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    result = fetch_http(
        "https://public.example/path",
        mock_transport=httpx.MockTransport(handler),
        resolver=_resolver({"public.example": [second_ip, first_ip]}),
        max_response_bytes=8,
        require_text_content=True,
    )

    assert [str(request.url) for request in requests] == [
        f"https://{first_ip}/path",
        f"https://{second_ip}/path",
    ]
    assert all(request.headers["host"] == "public.example" for request in requests)
    assert all(request.extensions["sni_hostname"] == "public.example" for request in requests)
    assert result.final_url == "https://public.example/path"


@pytest.mark.parametrize("content_type", [None, "application/octet-stream"])
def test_fetch_http_preserves_ineligible_2xx_provenance(content_type):
    headers = {"content-type": content_type} if content_type else {}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, headers=headers, content=b"not usable")
    )

    result = fetch_http(
        "https://public.example/file",
        mock_transport=transport,
        resolver=_resolver({}),
        max_response_bytes=64,
        require_text_content=True,
    )

    assert result.status_code == 200
    assert result.content_type == (content_type or "")
    assert result.content_eligible is False
    assert result.content == b""


@pytest.mark.parametrize(
    "headers",
    [
        {"x-large": "x" * (MAX_HEADER_VALUE_BYTES + 1)},
        {f"x-{index}": "x" * 100 for index in range(MAX_HEADER_BYTES // 100 + 1)},
        {"location": "/" + "x" * MAX_HEADER_VALUE_BYTES},
    ],
)
def test_fetch_http_rejects_unbounded_response_metadata(headers):
    transport = httpx.MockTransport(lambda request: httpx.Response(302, headers=headers))

    with pytest.raises(NetworkPolicyError, match="HTTP metadata"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({}),
        )


def test_fetch_http_bounds_duplicate_header_after_coalescing():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers=[("x-meta", "a" * 5000), ("x-meta", "b" * 5000)],
        )
    )

    with pytest.raises(NetworkPolicyError, match="HTTP metadata"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({}),
        )


def test_fetch_http_counts_duplicate_header_separators_in_aggregate_bound(monkeypatch):
    from sdr import network_policy

    monkeypatch.setattr(network_policy, "MAX_HEADER_BYTES", 12)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers=[("x", "12345"), ("x", "67890")],
        )
    )

    with pytest.raises(NetworkPolicyError, match="HTTP metadata"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({}),
        )


def test_fetch_http_rejects_ambiguous_duplicate_location_headers():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers=[("location", "/one"), ("location", "/two")],
        )
    )

    with pytest.raises(NetworkPolicyError, match="duplicate and ambiguous Location header"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({}),
        )


@pytest.mark.parametrize(
    "values",
    [
        ("text/plain", "text/plain"),
        ("text/plain", "application/octet-stream"),
    ],
)
def test_fetch_http_rejects_ambiguous_duplicate_content_type_headers(values):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers=[("content-type", value) for value in values],
            content=b"must not be classified",
        )
    )

    with pytest.raises(NetworkPolicyError, match="duplicate and ambiguous Content-Type header"):
        fetch_http(
            "https://public.example/start",
            mock_transport=transport,
            resolver=_resolver({}),
            max_response_bytes=64,
            require_text_content=True,
        )


def test_fetch_http_enforces_overall_deadline_across_redirects(monkeypatch):
    from sdr import network_policy

    elapsed = 0.0

    def monotonic():
        return elapsed

    def handler(request):
        nonlocal elapsed
        elapsed += 0.6
        return httpx.Response(302, headers={"location": "/again"})

    monkeypatch.setattr(network_policy, "monotonic", monotonic)

    with pytest.raises(NetworkPolicyError, match="total retrieval time"):
        fetch_http(
            "https://public.example/start",
            mock_transport=httpx.MockTransport(handler),
            resolver=_resolver({}),
            total_timeout=1.0,
        )


def test_fetch_http_enforces_overall_deadline_when_stream_ends_without_a_chunk(monkeypatch):
    from sdr import network_policy

    elapsed = 0.0

    class SlowEmptyStream(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal elapsed
            elapsed += 1.1
            yield from ()

    monkeypatch.setattr(network_policy, "monotonic", lambda: elapsed)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=SlowEmptyStream(),
        )
    )

    with pytest.raises(NetworkPolicyError, match="total retrieval time"):
        fetch_http(
            "https://public.example/stream",
            mock_transport=transport,
            resolver=_resolver({}),
            max_response_bytes=64,
            require_text_content=True,
            total_timeout=1.0,
        )


def test_fetch_http_deadline_is_observable_while_dns_resolution_is_blocked():
    release = threading.Event()
    started = threading.Event()

    def blocking_resolver(host):
        started.set()
        release.wait(timeout=1.0)
        return [PUBLIC_IP]

    before = time.monotonic()
    try:
        with pytest.raises(NetworkPolicyError, match="total retrieval time"):
            fetch_http(
                "https://public.example/start",
                mock_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
                resolver=blocking_resolver,
                total_timeout=0.03,
            )
        elapsed = time.monotonic() - before
    finally:
        release.set()

    assert started.is_set()
    assert elapsed < 0.15


def test_fetch_http_bounds_timed_out_blocking_workers_globally():
    release = threading.Event()
    entered = 0
    entered_lock = threading.Lock()

    def blocking_resolver(host):
        nonlocal entered
        with entered_lock:
            entered += 1
        release.wait(timeout=2.0)
        return [PUBLIC_IP]

    def fetch():
        with pytest.raises(NetworkPolicyError, match="total retrieval time"):
            fetch_http(
                "https://public.example/start",
                mock_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
                resolver=blocking_resolver,
                total_timeout=0.1,
            )

    callers = [threading.Thread(target=fetch) for _ in range(MAX_BLOCKING_WORKERS + 3)]
    try:
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=1.0)

        assert all(not caller.is_alive() for caller in callers)
        assert entered == MAX_BLOCKING_WORKERS

        fetch()
        assert entered == MAX_BLOCKING_WORKERS
    finally:
        release.set()


def test_fetch_http_returns_blocking_worker_slots_after_workers_exit():
    release = threading.Event()
    all_entered = threading.Event()
    entered = 0
    entered_lock = threading.Lock()

    def blocking_resolver(host):
        nonlocal entered
        with entered_lock:
            entered += 1
            if entered == MAX_BLOCKING_WORKERS:
                all_entered.set()
        release.wait(timeout=2.0)
        return [PUBLIC_IP]

    def fetch():
        with pytest.raises(NetworkPolicyError, match="total retrieval time"):
            fetch_http(
                "https://public.example/start",
                mock_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
                resolver=blocking_resolver,
                total_timeout=0.1,
            )

    callers = [threading.Thread(target=fetch) for _ in range(MAX_BLOCKING_WORKERS)]
    try:
        for caller in callers:
            caller.start()
        assert all_entered.wait(timeout=1.0)
        for caller in callers:
            caller.join(timeout=1.0)
    finally:
        release.set()

    deadline = time.monotonic() + 1.0
    while any(
        thread.name == "sdr-http-fetch" and thread.is_alive() for thread in threading.enumerate()
    ):
        assert time.monotonic() < deadline
        time.sleep(0.01)

    result = fetch_http(
        "https://public.example/recovered",
        mock_transport=httpx.MockTransport(lambda request: httpx.Response(204)),
        resolver=_resolver({}),
        total_timeout=0.1,
    )
    assert result.status_code == 204


def test_fetch_http_deadline_is_observable_during_slow_empty_stream_and_cleans_up():
    stream_closed = threading.Event()

    class SlowEmptyStream(httpx.SyncByteStream):
        def __iter__(self):
            time.sleep(0.2)
            yield from ()

        def close(self):
            stream_closed.set()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=SlowEmptyStream(),
        )
    )

    before = time.monotonic()
    with pytest.raises(NetworkPolicyError, match="total retrieval time"):
        fetch_http(
            "https://public.example/stream",
            mock_transport=transport,
            resolver=_resolver({}),
            max_response_bytes=64,
            require_text_content=True,
            total_timeout=0.03,
        )
    elapsed = time.monotonic() - before

    assert elapsed < 0.15
    assert stream_closed.wait(timeout=1.0)


def test_fetch_http_rejects_external_clients_instead_of_inheriting_their_trust_settings():
    client = httpx.Client(proxy="http://127.0.0.1:8080", trust_env=True)
    try:
        with pytest.raises(TypeError, match="client"):
            fetch_http(
                "https://public.example/start",
                client=client,
                resolver=_resolver({}),
            )
    finally:
        client.close()


def test_fetch_http_rejects_non_mock_external_transport():
    with pytest.raises(TypeError, match="MockTransport"):
        fetch_http(
            "https://public.example/start",
            mock_transport=httpx.HTTPTransport(),
            resolver=_resolver({}),
        )


def test_fetch_http_rejects_credentials_without_emitting_them():
    secret = "user:password"

    with pytest.raises(NetworkPolicyError) as captured:
        fetch_http(f"https://{secret}@public.example/path?token=query-secret")

    assert "password" not in str(captured.value)
    assert "query-secret" not in str(captured.value)


def test_fetch_http_redacts_transport_errors_containing_query_secrets():
    def handler(request):
        raise httpx.ConnectError(f"failed for {request.url}", request=request)

    with pytest.raises(NetworkPolicyError) as captured:
        fetch_http(
            "https://public.example/path?token=query-secret",
            mock_transport=httpx.MockTransport(handler),
            resolver=_resolver({}),
        )

    assert "query-secret" not in str(captured.value)
    assert "token=" not in str(captured.value)
