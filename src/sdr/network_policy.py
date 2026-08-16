"""Safe network policy for outbound HTTP requests."""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from time import monotonic
from urllib.parse import ParseResult, urljoin, urlparse, urlunparse

import httpx

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_HEADER_VALUE_BYTES = 8 * 1024
MAX_HEADER_BYTES = 64 * 1024
TOTAL_TIMEOUT = 30.0
MAX_BLOCKING_WORKERS = 4
FOLLOWED_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str], Iterable[str]]

_METADATA_HOSTS = {
    "instance-data.ec2.internal",
    "metadata.azure.internal",
    "metadata.google.internal",
    "metadata.goog",
}
_METADATA_ADDRESSES = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("192.0.0.192"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_SUPPORTED_APPLICATION_TYPES = {
    "application/json",
    "application/markdown",
    "application/xhtml+xml",
    "application/xml",
}
_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_QUOTED_STRING = r'"(?:[\t !#-\[\]-~\x80-\xff]|\\[\t !-~\x80-\xff])*"'
_MEDIA_TYPE_RE = re.compile(
    rf"[ \t]*(?P<type>{_TOKEN})/(?P<subtype>{_TOKEN})"
    rf"(?:[ \t]*;[ \t]*{_TOKEN}[ \t]*=[ \t]*(?:{_TOKEN}|{_QUOTED_STRING}))*[ \t]*"
)
_BLOCKING_WORKER_SLOTS = threading.BoundedSemaphore(MAX_BLOCKING_WORKERS)


class NetworkPolicyError(ValueError):
    """The URL or response violates the outbound network policy."""


@dataclass(frozen=True)
class RedirectRecord:
    """HTTP hop followed after its target was validated."""

    url: str
    status_code: int
    location: str
    target_url: str


@dataclass(frozen=True)
class HttpResult:
    """Bounded terminal HTTP response with full provenance."""

    declared_url: str
    final_url: str
    redirects: tuple[RedirectRecord, ...]
    status_code: int
    headers: dict[str, str]
    content: bytes
    content_type: str
    content_eligible: bool

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for parameter in content_type.split(";")[1:]:
            key, separator, value = parameter.strip().partition("=")
            if separator and key.lower() == "charset":
                charset = value.strip(' "') or charset
                break
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")


def resolve_host(host: str) -> list[str]:
    """Resolve every candidate address of a hostname."""
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkPolicyError(f"could not resolve hostname {host!r}") from exc
    return sorted({str(record[4][0]) for record in records})


def validate_http_url(url: str, *, resolver: Resolver | None = None) -> None:
    """Validate the scheme and every resulting IP before connecting."""
    _validated_url_and_addresses(url, resolver=resolver)


def validate_http_url_structure(url: str) -> None:
    """Validate HTTP URL structure and static policy without DNS or network access."""
    _parse_http_url_structure(url)


def _parse_http_url_structure(url: str) -> ParseResult:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise NetworkPolicyError("invalid HTTP/HTTPS URL") from None
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise NetworkPolicyError("only absolute HTTP/HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise NetworkPolicyError("credentials are not allowed in HTTP/HTTPS URLs")
    if port is not None and not 1 <= port <= 65535:
        raise NetworkPolicyError("invalid port in HTTP/HTTPS URL")

    normalized_host = host.rstrip(".").lower()
    if normalized_host in _METADATA_HOSTS or any(
        normalized_host.endswith(f".{metadata_host}") for metadata_host in _METADATA_HOSTS
    ):
        raise NetworkPolicyError(f"blocked cloud metadata hostname: {host}")

    try:
        literal_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        try:
            ascii_host = normalized_host.encode("idna").decode("ascii")
        except UnicodeError:
            raise NetworkPolicyError("invalid hostname in HTTP/HTTPS URL") from None
        labels = ascii_host.split(".")
        if (
            not ascii_host
            or len(ascii_host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(character.isalnum() or character == "-" for character in label)
                for label in labels
            )
        ):
            raise NetworkPolicyError("invalid hostname in HTTP/HTTPS URL") from None
    else:
        _validate_public_address(literal_address, host)
    return parsed


def _validated_url_and_addresses(
    url: str, *, resolver: Resolver | None = None
) -> tuple[ParseResult, tuple[str, ...]]:
    parsed = _parse_http_url_structure(url)
    host = parsed.hostname
    assert host is not None
    normalized_host = host.rstrip(".").lower()

    try:
        literal_address = ipaddress.ip_address(normalized_host)
    except ValueError:
        addresses = list((resolver or resolve_host)(normalized_host))
        if not addresses:
            raise NetworkPolicyError(f"hostname {host!r} resolved no addresses") from None
    else:
        addresses = [str(literal_address)]

    validated_addresses = []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise NetworkPolicyError(
                f"invalid DNS resolution for {host!r}: {raw_address!r}"
            ) from exc
        _validate_public_address(address, host)
        validated_addresses.append(address)
    ordered = sorted(set(validated_addresses), key=lambda address: (address.version, int(address)))
    return parsed, tuple(str(address) for address in ordered)


def _validate_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str
) -> None:
    if address in _METADATA_ADDRESSES:
        raise NetworkPolicyError(f"blocked cloud metadata address: {address}")
    if any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
            address.is_reserved,
            not address.is_global,
        )
    ):
        raise NetworkPolicyError(f"blocked non-public address for {host!r}: {address}")


def fetch_http(
    url: str,
    *,
    method: str = "GET",
    mock_transport: httpx.MockTransport | None = None,
    resolver: Resolver | None = None,
    max_redirects: int = MAX_REDIRECTS,
    max_response_bytes: int | None = None,
    require_text_content: bool = False,
    total_timeout: float = TOTAL_TIMEOUT,
) -> HttpResult:
    """Run a request, validating every hop and reading the body in a bounded way."""
    if mock_transport is not None and not isinstance(mock_transport, httpx.MockTransport):
        raise TypeError("mock_transport must be an httpx.MockTransport")
    if total_timeout <= 0:
        raise NetworkPolicyError("the total retrieval time was exceeded")
    deadline = monotonic() + total_timeout
    cancelled = threading.Event()
    results: list[HttpResult] = []
    errors: list[BaseException] = []

    def retrieve() -> None:
        try:
            results.append(
                _fetch_http(
                    url,
                    method=method,
                    mock_transport=mock_transport,
                    resolver=resolver,
                    max_redirects=max_redirects,
                    max_response_bytes=max_response_bytes,
                    require_text_content=require_text_content,
                    deadline=deadline,
                    cancelled=cancelled,
                )
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            _BLOCKING_WORKER_SLOTS.release()

    remaining = deadline - monotonic()
    if remaining <= 0 or not _BLOCKING_WORKER_SLOTS.acquire(timeout=remaining):
        raise NetworkPolicyError("the total retrieval time was exceeded")
    worker = threading.Thread(target=retrieve, name="sdr-http-fetch", daemon=True)
    try:
        worker.start()
    except BaseException:
        _BLOCKING_WORKER_SLOTS.release()
        raise
    worker.join(max(0.0, deadline - monotonic()))
    if worker.is_alive():
        cancelled.set()
        raise NetworkPolicyError("the total retrieval time was exceeded")
    if errors:
        raise errors[0]
    return results[0]


def _fetch_http(
    url: str,
    *,
    method: str,
    mock_transport: httpx.MockTransport | None,
    resolver: Resolver | None,
    max_redirects: int,
    max_response_bytes: int | None,
    require_text_content: bool,
    deadline: float,
    cancelled: threading.Event,
) -> HttpResult:
    current_url = url
    redirects: list[RedirectRecord] = []
    try:
        while True:
            _operation_timeout(deadline, cancelled)
            parsed, addresses = _validated_url_and_addresses(current_url, resolver=resolver)
            followed_redirect = False
            for address in addresses:
                try:
                    operation_timeout = _operation_timeout(deadline, cancelled)
                    with httpx.Client(
                        timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
                        follow_redirects=False,
                        trust_env=False,
                        transport=mock_transport,
                    ) as active_client:
                        request = active_client.build_request(
                            method,
                            _pinned_url(parsed, address),
                            headers={"Host": _host_header(parsed)},
                            extensions={
                                "sni_hostname": parsed.hostname.rstrip("."),
                                "timeout": operation_timeout.as_dict(),
                            },
                        )
                        streamed_response = active_client.send(
                            request, stream=True, follow_redirects=False
                        )
                        with closing(streamed_response) as response:
                            _operation_timeout(deadline, cancelled)
                            headers = _bounded_headers(response)
                            if (
                                response.status_code in FOLLOWED_REDIRECT_STATUSES
                                and "location" in headers
                            ):
                                if len(redirects) >= max_redirects:
                                    raise NetworkPolicyError(
                                        f"the limit of {max_redirects} redirects was exceeded"
                                    )
                                response_url = current_url
                                location = headers["location"]
                                target_url = urljoin(response_url, location)
                                redirects.append(
                                    RedirectRecord(
                                        url=response_url,
                                        status_code=response.status_code,
                                        location=location,
                                        target_url=target_url,
                                    )
                                )
                                current_url = target_url
                                followed_redirect = True
                                break

                            content = b""
                            content_type = _content_type(headers)
                            content_eligible = _content_type_eligible(
                                content_type, require_text_content=require_text_content
                            )
                            if max_response_bytes is not None:
                                should_read = (
                                    not (200 <= response.status_code < 300) or content_eligible
                                )
                                if should_read:
                                    _validate_content_length(headers, max_response_bytes)
                                    chunks: list[bytes] = []
                                    size = 0
                                    for chunk in response.iter_bytes():
                                        _operation_timeout(deadline, cancelled)
                                        size += len(chunk)
                                        if size > max_response_bytes:
                                            raise NetworkPolicyError(
                                                "the response exceeds the maximum size of "
                                                f"{max_response_bytes} bytes"
                                            )
                                        chunks.append(chunk)
                                    _operation_timeout(deadline, cancelled)
                                    content = b"".join(chunks)
                            return HttpResult(
                                declared_url=url,
                                final_url=current_url,
                                redirects=tuple(redirects),
                                status_code=response.status_code,
                                headers=headers,
                                content=content,
                                content_type=content_type,
                                content_eligible=content_eligible,
                            )
                except httpx.TransportError:
                    continue
            if followed_redirect:
                continue
            raise NetworkPolicyError("the HTTP request to the validated target failed")
    except httpx.HTTPError:
        raise NetworkPolicyError("the HTTP request to the validated target failed") from None


def is_supported_text_content_type(content_type: str) -> bool:
    """Return whether a media type is supported for textual evidence."""
    match = _MEDIA_TYPE_RE.fullmatch(content_type)
    if match is None:
        return False
    media_type = f"{match['type']}/{match['subtype']}".lower()
    return media_type.startswith("text/") or media_type in _SUPPORTED_APPLICATION_TYPES


def _content_type_eligible(content_type: str, *, require_text_content: bool) -> bool:
    if not require_text_content:
        return True
    return is_supported_text_content_type(content_type)


def _validate_content_length(headers: dict[str, str], maximum: int) -> None:
    raw_length = headers.get("content-length")
    if raw_length is None:
        return
    try:
        length = int(raw_length)
    except ValueError:
        return
    if length > maximum:
        raise NetworkPolicyError(f"the response exceeds the maximum size of {maximum} bytes")


def _content_type(headers: dict[str, str]) -> str:
    return headers.get("content-type", "").strip()


def _bounded_headers(response: httpx.Response) -> dict[str, str]:
    total = 0
    retained: dict[str, str] = {}
    for name, value in response.headers.multi_items():
        normalized_name = name.lower()
        name_size = len(name.encode("utf-8"))
        value_size = len(value.encode("utf-8"))
        if value_size > MAX_HEADER_VALUE_BYTES:
            raise NetworkPolicyError("the HTTP metadata exceeds the allowed limit")
        if normalized_name in {"location", "content-type"} and normalized_name in retained:
            display_name = "Location" if normalized_name == "location" else "Content-Type"
            raise NetworkPolicyError(f"duplicate and ambiguous {display_name} header")
        if normalized_name in retained:
            combined = f"{retained[normalized_name]}, {value}"
            total += 2 + value_size
        else:
            combined = value
            total += name_size + value_size
        if len(combined.encode("utf-8")) > MAX_HEADER_VALUE_BYTES:
            raise NetworkPolicyError("the HTTP metadata exceeds the allowed limit")
        if total > MAX_HEADER_BYTES:
            raise NetworkPolicyError("the HTTP metadata exceeds the allowed limit")
        retained[normalized_name] = combined
    return retained


def _operation_timeout(deadline: float, cancelled: threading.Event | None = None) -> httpx.Timeout:
    remaining = deadline - monotonic()
    if remaining <= 0 or (cancelled is not None and cancelled.is_set()):
        raise NetworkPolicyError("the total retrieval time was exceeded")
    return httpx.Timeout(
        min(READ_TIMEOUT, remaining),
        connect=min(CONNECT_TIMEOUT, remaining),
    )


def _host_header(parsed: ParseResult) -> str:
    return parsed.netloc


def _pinned_url(parsed: ParseResult, address: str) -> str:
    host = f"[{address}]" if ":" in address else address
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunparse(parsed._replace(netloc=netloc))
