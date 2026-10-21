"""Offline HTTP boundary tests: synthetic streams, no socket/DNS exceptions."""

import io
import json
import ssl
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from http.client import HTTPException, HTTPResponse, IncompleteRead
from pathlib import Path

import pytest

from hellas_dataops.domain import HourlyInterval
from hellas_dataops.sources import open_meteo_client as client
from hellas_dataops.sources.cities import CITY_REGISTRY
from hellas_dataops.sources.open_meteo import normalize_open_meteo
from hellas_dataops.sources.open_meteo_requests import (
    ERA5_DATASET,
    plan_open_meteo_requests,
)


@pytest.fixture
def request_spec():
    return plan_open_meteo_requests(
        CITY_REGISTRY["athens"],
        dataset=ERA5_DATASET,
        interval=HourlyInterval(
            start=datetime(2025, 1, 1, 5, tzinfo=UTC),
            end=datetime(2025, 1, 8, tzinfo=UTC),
        ),
    )[0]


class FakeResponse:
    def __init__(self, body=b'{"ok":true}', *, status=200, headers=None, chunk_size=7):
        self.status = status
        self.headers = (
            [("Content-Type", "application/json")] if headers is None else headers
        )
        self.stream = io.BytesIO(body)
        self.chunk_size = chunk_size
        self.read_sizes = []
        self.closed = False

    def getheaders(self):
        return self.headers

    def read1(self, size):
        self.read_sizes.append(size)
        return self.stream.read(min(size, self.chunk_size))


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    @contextmanager
    def __call__(self, request, *, timeout_seconds):
        self.calls.append((request, timeout_seconds))
        try:
            yield self.response
        finally:
            self.response.closed = True


def fetch(request, response, **kwargs):
    return client.fetch_open_meteo(request, transport=FakeTransport(response), **kwargs)


def test_full_synthetic_fixture_is_unchanged_and_can_be_normalized(request_spec):
    path = Path(__file__).parents[1] / "fixtures/open_meteo/seven_days.json"
    body = path.read_bytes()
    response = FakeResponse(
        body,
        headers=[
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Content-Encoding", "identity"),
            ("Server", "synthetic-test-server"),
        ],
    )
    transport = FakeTransport(response)
    before = request_spec.to_json()
    payload = client.fetch_open_meteo(
        request_spec, transport=transport, timeout_seconds=2.5
    )
    assert payload == json.loads(body)
    assert transport.calls == [(request_spec, 2.5)]
    assert response.closed
    assert request_spec.to_json() == before
    normalized = normalize_open_meteo(
        payload,
        city_id=request_spec.city.city_id,
        dataset=request_spec.dataset,
        interval=request_spec.retrieval_interval,
    )
    # The client retains the full provider response, including hours before 05:00.
    assert len(normalized.observations) == 168
    assert normalized.observations[0].timestamp_utc.hour == 0


def test_unicode_nulls_and_caller_ownership(request_spec):
    response = FakeResponse('{"unit":"°C","values":[null,0,1.5]}'.encode())
    payload = fetch(request_spec, response)
    assert payload == {"unit": "°C", "values": [None, 0, 1.5]}
    payload["values"].append(99)
    assert fetch(request_spec, FakeResponse(b'{"values":[null]}')) == {"values": [None]}


def test_nesting_boundary_and_escaped_string_content(request_spec):
    text = '[{\\"' * 100
    payload = {"text": text}
    assert fetch(request_spec, FakeResponse(json.dumps(payload).encode())) == payload
    body = b'{"x":' + b"[" * 63 + b"0" + b"]" * 63 + b"}"
    assert "x" in fetch(request_spec, FakeResponse(body))
    body = b'{"x":' + b"[" * 64 + b"0" + b"]" * 64 + b"}"
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, FakeResponse(body))
    assert error.value.code == "invalid_json"


@pytest.mark.parametrize("limit, succeeds", [(2, True), (1, False)])
def test_exact_byte_boundary_without_length(request_spec, limit, succeeds):
    response = FakeResponse(b"{}")
    if succeeds:
        assert fetch(request_spec, response, max_response_bytes=limit) == {}
    else:
        with pytest.raises(client.OpenMeteoResponseError, match="byte limit") as error:
            fetch(request_spec, response, max_response_bytes=limit)
        assert error.value.code == "response_too_large"
    assert response.closed


def test_large_stream_stops_at_limit_plus_one(request_spec):
    response = FakeResponse(b"x" * 500_000, chunk_size=500_000)
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, response, max_response_bytes=100_000)
    assert error.value.code == "response_too_large"
    assert response.stream.tell() == 100_001
    assert max(response.read_sizes) <= 65536
    assert response.closed


@pytest.mark.parametrize(
    "headers, code",
    [
        ([], "content_type"),
        ([("Content-Type", "text/html")], "content_type"),
        ([("Content-Encoding", "gzip")], "content_encoding"),
        ([("Content-Length", "3")], "response_too_large"),
        ([("Content-Length", "-1")], "invalid_headers"),
        ([("Content-Length", "1.5")], "invalid_headers"),
        ([("Content-Length", "²")], "invalid_headers"),
        ([("Content-Length", "9" * 21)], "invalid_headers"),
        ([("Content-Length", "2"), ("content-length", "2")], "invalid_headers"),
        ([("Transfer-Encoding", "gzip")], "invalid_headers"),
        (
            [("Transfer-Encoding", "chunked"), ("Content-Length", "2")],
            "invalid_headers",
        ),
    ],
)
def test_invalid_or_oversized_headers_reject_before_read(request_spec, headers, code):
    if headers and not any(k.lower() == "content-type" for k, _ in headers):
        headers = [("Content-Type", "application/json"), *headers]
    response = FakeResponse(b"{}", headers=headers)
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, response, max_response_bytes=2)
    assert error.value.code == code
    assert response.read_sizes == []
    assert response.closed


@pytest.mark.parametrize("length", ["1", "3"])
def test_short_or_misreported_content_length(request_spec, length):
    response = FakeResponse(
        b"{}",
        headers=[
            ("Content-Type", "application/json"),
            ("Content-Length", length),
        ],
    )
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, response)
    assert error.value.code == "content_length_mismatch"
    assert response.closed


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html>error</html>",
        b'{"secret":',
        b"\xff",
        b"{}{}",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":1e999}',
        b'{"x":1,"x":2}',
        b'{"a":{"x":1,"x":2}}',
        b'{"x":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
        b'{"x":' + b"9" * 5000 + b"}",
        b"\xef\xbb\xbf{}",
    ],
)
def test_bad_json_is_typed_sanitized_and_closed(request_spec, body):
    response = FakeResponse(body)
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, response)
    assert error.value.code == "invalid_json"
    assert str(error.value) == "Invalid UTF-8 JSON response"
    assert response.closed


@pytest.mark.parametrize("body", [b"[]", b"null", b'"text"', b"1", b"true"])
def test_requires_single_location_object(request_spec, body):
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, FakeResponse(body))
    assert error.value.code == "invalid_json_shape"


def test_provider_error_does_not_echo_reason(request_spec):
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, FakeResponse(b'{"error":true,"reason":"private details"}'))
    assert error.value.code == "provider_error"
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "status",
    [201, 204, 301, 302, 307, 308, 400, 401, 403, 404, 429, 500, 502, 503, 504],
)
def test_non_200_never_reads_body_retries_or_redirects(request_spec, status):
    response = FakeResponse(
        b"private upstream diagnostics",
        status=status,
        headers=[
            ("Location", "http://127.0.0.1/private"),
            ("Retry-After", "30"),
        ],
    )
    transport = FakeTransport(response)
    with pytest.raises(client.OpenMeteoStatusError) as error:
        client.fetch_open_meteo(request_spec, transport=transport)
    assert error.value.status == status
    assert error.value.code == "http_status"
    assert error.value.retry_after_seconds == 30
    assert error.value.retry_after_at is None
    assert len(transport.calls) == 1
    assert response.read_sizes == []
    assert response.closed
    assert str(error.value) == f"Archive request returned HTTP {status}"


@pytest.mark.parametrize(
    "value, seconds, date",
    [
        ("0", 0, None),
        (" 42 ", 42, None),
        (
            "Wed, 21 Oct 2026 07:28:00 GMT",
            None,
            datetime(2026, 10, 21, 7, 28, tzinfo=UTC),
        ),
        ("Wed, 21 Oct 2026 07:28:00 -0000", None, None),
        ("bad", None, None),
        ("-1", None, None),
        ("1.5", None, None),
        ("9" * 129, None, None),
        ("", None, None),
        ("²", None, None),
    ],
)
def test_retry_after_is_bounded_advisory_metadata(request_spec, value, seconds, date):
    response = FakeResponse(status=429, headers=[("retry-after", value)])
    with pytest.raises(client.OpenMeteoStatusError) as error:
        fetch(request_spec, response)
    assert error.value.retry_after_seconds == seconds
    assert error.value.retry_after_at == date


@pytest.mark.parametrize("headers", [[], [("Retry-After", "1"), ("retry-after", "2")]])
def test_missing_or_ambiguous_retry_hint_ignored(request_spec, headers):
    with pytest.raises(client.OpenMeteoStatusError) as error:
        fetch(request_spec, FakeResponse(status=503, headers=headers))
    assert error.value.retry_after_seconds is None
    assert error.value.retry_after_at is None


@pytest.mark.parametrize(
    "failure, expected_type, code",
    [
        (TimeoutError("private"), client.OpenMeteoTimeoutError, "timeout"),
        (ConnectionResetError("private"), client.OpenMeteoTransportError, "transport"),
        (
            ssl.SSLCertVerificationError("private"),
            client.OpenMeteoTransportError,
            "transport",
        ),
        (HTTPException("private"), client.OpenMeteoTransportError, "transport"),
        (IncompleteRead(b"private"), client.OpenMeteoTransportError, "transport"),
    ],
)
@pytest.mark.parametrize("phase", ["open", "read"])
def test_transport_failures_are_typed_without_raw_exception_text(
    request_spec,
    failure,
    expected_type,
    code,
    phase,
):
    response = FakeResponse()

    @contextmanager
    def transport(request, *, timeout_seconds):
        if phase == "open":
            raise failure
        try:
            yield response
        finally:
            response.closed = True

    def fail_read(size):
        raise failure

    response.read1 = fail_read
    with pytest.raises(expected_type) as error:
        client.fetch_open_meteo(request_spec, transport=transport)
    assert error.value.code == code
    assert error.value.__suppress_context__
    rendered = "".join(traceback.format_exception(error.value))
    assert "private" not in rendered
    if phase == "read":
        assert response.closed


@pytest.mark.parametrize(
    "option, value",
    [
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", True),
        ("timeout_seconds", "10"),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("timeout_seconds", 301),
        ("max_response_bytes", 0),
        ("max_response_bytes", -1),
        ("max_response_bytes", True),
        ("max_response_bytes", 2.0),
        ("max_response_bytes", 16 * 1024 * 1024 + 1),
    ],
)
def test_bad_limits_fail_before_transport(request_spec, option, value):
    transport = FakeTransport(FakeResponse())
    with pytest.raises(ValueError):
        client.fetch_open_meteo(request_spec, transport=transport, **{option: value})
    assert not transport.calls


def test_untyped_request_rejected_before_io():
    transport = FakeTransport(FakeResponse())
    with pytest.raises(TypeError):
        client.fetch_open_meteo("https://example.com", transport=transport)
    assert not transport.calls


@pytest.mark.parametrize("chunk", ["{}", b"too many bytes"])
def test_injected_transport_must_obey_read_bound(request_spec, chunk):
    response = FakeResponse()
    response.read1 = lambda size: chunk
    with pytest.raises(client.OpenMeteoResponseError) as error:
        fetch(request_spec, response, max_response_bytes=2)
    assert error.value.code == "invalid_stream"
    assert response.closed


class MemorySocket:
    def __init__(self, wire):
        self.wire = wire

    def makefile(self, mode):
        return io.BytesIO(self.wire)


@pytest.mark.parametrize(
    "wire, error_code",
    [
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
            None,
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n1\r\n{\r\n1\r\n}\r\n0\r\n\r\n",
            None,
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 3\r\n\r\n{}",
            "content_length_mismatch",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n",
            "transport",
        ),
    ],
)
def test_default_adapter_with_real_offline_http_parser(
    monkeypatch, request_spec, wire, error_code
):
    response = HTTPResponse(MemorySocket(wire))
    response.begin()
    calls = []
    closed = []

    class Connection:
        def __init__(self, host, *, timeout):
            calls.append((host, timeout))

        def request(self, method, target, *, headers):
            calls.append((method, target, headers))

        def getresponse(self):
            return response

        def close(self):
            closed.append(True)

    monkeypatch.setattr(client, "HTTPSConnection", Connection)
    if error_code is None:
        assert client.fetch_open_meteo(request_spec, timeout_seconds=3) == {}
    else:
        with pytest.raises(client.OpenMeteoClientError) as error:
            client.fetch_open_meteo(request_spec, timeout_seconds=3)
        assert error.value.code == error_code
    assert calls[0] == ("archive-api.open-meteo.com", 3.0)
    assert calls[1] == (
        "GET",
        "/v1/archive?" + request_spec.query_string,
        {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "hellas-dataops/0.1.0.dev0",
            "Connection": "close",
        },
    )
    assert response.closed
    assert closed == [True]


@pytest.mark.parametrize("phase", ["request", "getresponse"])
def test_default_adapter_closes_connection_on_setup_failure(
    monkeypatch, request_spec, phase
):
    closed = []

    class Connection:
        def __init__(self, host, *, timeout):
            pass

        def request(self, *args, **kwargs):
            if phase == "request":
                raise TimeoutError

        def getresponse(self):
            raise TimeoutError

        def close(self):
            closed.append(True)

    monkeypatch.setattr(client, "HTTPSConnection", Connection)
    with pytest.raises(client.OpenMeteoTimeoutError):
        client.fetch_open_meteo(request_spec)
    assert closed == [True]
