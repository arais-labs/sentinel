"""TLS integrity failures must be retried, certificate failures must not."""

from __future__ import annotations

import ssl

import httpx
import pytest

from sentral.llm.generic.errors import error_tag, is_retryable


def bad_record_mac() -> ssl.SSLError:
    """The error seen in the wild when a TLS record fails its integrity check."""
    return ssl.SSLError(1, "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac")


def test_tls_integrity_failure_is_retryable():
    # A corrupted record kills the connection but says nothing about the request,
    # so giving up after one attempt turns a transient blip into a failed turn.
    assert is_retryable(bad_record_mac()) is True
    assert error_tag(bad_record_mac()) == "tls_error"


def test_certificate_verification_is_not_retryable():
    exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
    assert is_retryable(exc) is False
    assert error_tag(exc) == "tls_certificate_error"


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError(),
        ConnectionError(),
        httpx.ConnectError("refused"),
        httpx.ReadError("reset"),
    ],
)
def test_transport_failures_remain_retryable(exc):
    assert is_retryable(exc) is True


def test_permanent_failures_remain_non_retryable():
    assert is_retryable(ValueError("bad request")) is False


def test_retry_loop_consumes_all_attempts_for_tls_errors():
    """Mirror the tier retry guard so a regression cannot silently stop at attempt 1."""
    max_retries = 3
    attempts = 0
    for attempt in range(1, max_retries + 1):
        attempts += 1
        exc = bad_record_mac()
        if is_retryable(exc) and attempt < max_retries:
            continue
        break
    assert attempts == max_retries
