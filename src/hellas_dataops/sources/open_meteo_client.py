"""Bounded, synchronous archive JSON retrieval; no normalization or retries.

The default transport uses verified HTTPS to the fixed archive host. Tests inject
a context-managed response instead. Importing this module performs no I/O.
"""

import json
import math
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException, HTTPSConnection
from typing import Protocol

from hellas_dataops.sources.open_meteo_requests import OpenMeteoRequest

_READ_CHUNK = 64 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class OpenMeteoClientError(Exception):
    """A retrieval failure with a stable code and no raw response/error content."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class OpenMeteoTransportError(OpenMeteoClientError):
    """Connection, TLS, HTTP framing, or stream failure."""


class OpenMeteoTimeoutError(OpenMeteoTransportError):
    """A socket operation timed out (not a total request deadline)."""


class OpenMeteoResponseError(OpenMeteoClientError):
    """Unsupported, oversized, truncated, or invalid JSON response."""


class OpenMeteoStatusError(OpenMeteoClientError):
    """Non-200 response; retry hints are advisory and never trigger a retry."""

    def __init__(self, status: int, retry_after: str | None) -> None:
        self.status = status
        self.retry_after_seconds: int | None = None
        self.retry_after_at: datetime | None = None
        # Bound parsing work and never retain or print arbitrary header text.
        if retry_after is not None and len(retry_after) <= 128:
            value = retry_after.strip()
            if value.isascii() and value.isdecimal() and len(value) <= 10:
                self.retry_after_seconds = int(value)
            else:
                try:
                    parsed = parsedate_to_datetime(value)
                    if parsed.tzinfo is not None:
                        self.retry_after_at = parsed.astimezone(UTC)
                except ValueError, TypeError, OverflowError:
                    pass
        super().__init__("http_status", f"Archive request returned HTTP {status}")


class ArchiveResponse(Protocol):
    """Streaming response owned and closed by the transport context manager.

    read1(size) must return at most size bytes, with b'' indicating EOF. The
    transport must enforce the supplied timeout and avoid automatic retries,
    redirects, decompression, or whole-body buffering.
    """

    status: int

    def getheaders(self) -> list[tuple[str, str]]: ...

    def read1(self, size: int) -> bytes: ...


class ArchiveTransport(Protocol):
    """Injection boundary for a single request, including response cleanup."""

    def __call__(
        self, request: OpenMeteoRequest, *, timeout_seconds: float
    ) -> AbstractContextManager[ArchiveResponse]: ...


@contextmanager
def _https_transport(
    request: OpenMeteoRequest, *, timeout_seconds: float
) -> Iterator[ArchiveResponse]:
    # HTTPSConnection's default SSL context verifies certificates and hostname.
    # Fixed host/path, no redirects, environment proxies, cookies, or credentials.
    connection = HTTPSConnection("archive-api.open-meteo.com", timeout=timeout_seconds)
    try:
        connection.request(
            "GET",
            "/v1/archive?" + request.query_string,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "hellas-dataops/0.1.0.dev0",
                "Connection": "close",
            },
        )
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


def _headers(response: ArchiveResponse) -> dict[str, str]:
    headers: dict[str, str] = {}
    relevant = {
        "content-type",
        "content-encoding",
        "content-length",
        "transfer-encoding",
    }
    for name, value in response.getheaders():
        name = name.lower()
        if name in relevant:
            if name in headers:
                raise OpenMeteoResponseError(
                    "invalid_headers", "Duplicate response framing or content header"
                )
            headers[name] = value.strip()
    return headers


def _read_body(response: ArchiveResponse, limit: int) -> bytes:
    headers = _headers(response)
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise OpenMeteoResponseError(
            "content_type", "Expected an application/json response"
        )
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise OpenMeteoResponseError(
            "content_encoding", "Compressed or unknown content encoding is unsupported"
        )
    transfer = headers.get("transfer-encoding")
    length = headers.get("content-length")
    if transfer is not None and (transfer.lower() != "chunked" or length is not None):
        raise OpenMeteoResponseError(
            "invalid_headers", "Unsupported or ambiguous response framing"
        )
    expected = None
    if length is not None:
        if not length.isascii() or not length.isdecimal() or len(length) > 20:
            raise OpenMeteoResponseError(
                "invalid_headers", "Invalid response content length"
            )
        expected = int(length)
        if expected > limit:
            raise OpenMeteoResponseError(
                "response_too_large", "Response exceeds byte limit"
            )
    body = bytearray()
    while True:
        # One excess byte detects overflow even when Content-Length is absent.
        size = min(_READ_CHUNK, limit + 1 - len(body))
        chunk = response.read1(size)
        if not isinstance(chunk, bytes) or len(chunk) > size:
            raise OpenMeteoResponseError(
                "invalid_stream", "Transport violated the bounded bytes contract"
            )
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > limit:
            raise OpenMeteoResponseError(
                "response_too_large", "Response exceeds byte limit"
            )
    if expected is not None and len(body) != expected:
        raise OpenMeteoResponseError(
            "content_length_mismatch", "Incomplete response body"
        )
    return bytes(body)


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Nonfinite JSON number")
    return parsed


def _reject_constant(value: str) -> object:
    raise ValueError("Nonstandard JSON constant")


def _check_json_depth(text: str) -> None:
    """Bound nesting before decoding; leave all syntax validation to json.loads."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > 64:
                raise ValueError("JSON nesting exceeds 64 containers")
        elif char in "]}":
            depth -= 1


def fetch_open_meteo(
    request: OpenMeteoRequest,
    *,
    transport: ArchiveTransport | None = None,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 2 * 1024 * 1024,
) -> dict[str, object]:
    """Fetch one planned chunk as strict UTF-8 JSON, without interpreting weather.

    Timeout is per blocking socket operation, not a wall-clock deadline; system
    DNS resolution can exceed it. Size bounds apply to body bytes (max 16 MiB),
    not the decoded Python object's memory. The caller owns the returned mapping.
    No calls occur until this function is invoked. A supplied transport must
    honor ArchiveTransport's streaming, timeout, and cleanup contract.
    """
    if not isinstance(request, OpenMeteoRequest):
        raise TypeError("request must be an OpenMeteoRequest")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be a finite number in (0, 300]")
    if (
        type(max_response_bytes) is not int
        or not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES
    ):
        raise ValueError("max_response_bytes must be an integer in [1, 16777216]")
    selected_transport = _https_transport if transport is None else transport
    try:
        with selected_transport(
            request, timeout_seconds=float(timeout_seconds)
        ) as response:
            if response.status != 200:
                hints = [
                    v for k, v in response.getheaders() if k.lower() == "retry-after"
                ]
                raise OpenMeteoStatusError(
                    response.status, hints[0] if len(hints) == 1 else None
                )
            body = _read_body(response, max_response_bytes)
    except TimeoutError:
        raise OpenMeteoTimeoutError(
            "timeout", "Archive socket operation timed out"
        ) from None
    except OSError, HTTPException:
        raise OpenMeteoTransportError("transport", "Archive transport failed") from None
    try:
        text = body.decode("utf-8")
        _check_json_depth(text)
        payload = json.loads(
            text,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_json_object,
        )
    except ValueError, RecursionError:
        raise OpenMeteoResponseError(
            "invalid_json", "Invalid UTF-8 JSON response"
        ) from None
    if not isinstance(payload, dict):
        raise OpenMeteoResponseError("invalid_json_shape", "Expected one JSON object")
    if payload.get("error") is True:
        raise OpenMeteoResponseError(
            "provider_error", "Provider returned an error object"
        )
    return payload
