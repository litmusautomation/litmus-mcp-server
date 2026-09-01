"""Certificate verification defaults on, and any downgrade is visible.

The point of these is that the fallback must never be quiet: a caller who is
talking to an unverified host has to be able to see it in the response.
"""

import asyncio
import json
import ssl
from types import SimpleNamespace

import pytest

from server import _call_with_tls_fallback
from utils.formatting import format_error_response, format_success_response
from utils.tls import (
    call_with_certificate_fallback,
    certificate_complaint,
    downgrade_warning,
    is_certificate_error,
    is_certificate_error_text,
    reset_tls_state,
    resolve_validate_certificate,
    resolve_validate_certificate_env,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_tls_state()
    yield
    reset_tls_state()


def _cert_error():
    return ssl.SSLCertVerificationError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self signed certificate"
    )


# ---------------------------------------------------------------- the default


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({}, True),
        ({"VALIDATE_CERTIFICATE": ""}, True),
        ({"VALIDATE_CERTIFICATE": "true"}, True),
        ({"VALIDATE_CERTIFICATE": "TRUE"}, True),
        ({"VALIDATE_CERTIFICATE": "false"}, False),
        ({"VALIDATE_CERTIFICATE": "  False "}, False),
        ({"VALIDATE_CERTIFICATE": "off"}, False),
    ],
)
def test_resolve_reads_the_header_and_defaults_on(headers, expected):
    assert resolve_validate_certificate(headers) is expected


def test_an_unparseable_value_fails_closed():
    """A typo must not be read as consent to skip verification."""
    assert resolve_validate_certificate({"VALIDATE_CERTIFICATE": "flase"}) is True


# ---------------------------------------------------------------- the fallback
#
# The retry lives in the tool dispatcher, not around the SDK's connection
# constructors: those only build a config object and never touch the network,
# so a certificate can only be rejected once the tool issues its first request.


def _cert_failure_response():
    return format_error_response("read_failed", str(_cert_error()))


async def _dispatch(handler, headers=None):
    reset_tls_state()
    request = SimpleNamespace(headers=headers or {"EDGE_URL": "https://edge.local"})
    result = await _call_with_tls_fallback({"handler": handler}, request, {})
    return json.loads(result[0].text)


def test_verifies_first_and_does_not_retry_when_that_works():
    attempts = []

    async def handler(request, args):
        attempts.append(resolve_validate_certificate(request.headers))
        return format_success_response({"devices": []})

    body = asyncio.run(_dispatch(handler))
    assert attempts == [True]
    assert "tls_warning" not in body


def test_retries_unverified_after_a_certificate_rejection():
    attempts = []

    async def handler(request, args):
        verify = resolve_validate_certificate(request.headers)
        attempts.append(verify)
        return (
            _cert_failure_response() if verify else format_success_response({"ok": 1})
        )

    body = asyncio.run(_dispatch(handler))
    assert attempts == [True, False], "should verify first, then fall back"
    assert body["success"] is True


def test_the_downgrade_reaches_the_payload_not_just_the_log():
    """The note has to be recorded before the retry runs, because the handler
    formats its own response and would otherwise emit it without the warning."""

    async def handler(request, args):
        verify = resolve_validate_certificate(request.headers)
        return (
            _cert_failure_response() if verify else format_success_response({"ok": 1})
        )

    body = asyncio.run(_dispatch(handler))
    warning = body.get("tls_warning")
    assert warning is not None, "a silent downgrade is the bug this guards"
    assert "https://edge.local" in warning
    assert "FAILED" in warning
    assert "VALIDATE_CERTIFICATE=false" in warning


def test_retries_when_the_tool_raises_instead_of_returning_an_error():
    attempts = []

    async def handler(request, args):
        verify = resolve_validate_certificate(request.headers)
        attempts.append(verify)
        if verify:
            raise _cert_error()
        return format_success_response({"ok": 1})

    body = asyncio.run(_dispatch(handler))
    assert attempts == [True, False]
    assert "tls_warning" in body


def test_a_non_certificate_failure_is_never_retried():
    """A bad password must not be re-sent over an unverified channel."""
    attempts = []

    async def handler(request, args):
        attempts.append(resolve_validate_certificate(request.headers))
        return format_error_response("auth_failed", "401 invalid client secret")

    body = asyncio.run(_dispatch(handler))
    assert attempts == [True]
    assert "tls_warning" not in body


def test_a_raised_non_certificate_failure_propagates():
    async def handler(request, args):
        raise ValueError("connection refused")

    with pytest.raises(ValueError):
        asyncio.run(_dispatch(handler))


def test_an_explicit_opt_out_skips_verification_without_warning():
    """Opting out is a decision, not a downgrade, so it is not reported."""
    attempts = []

    async def handler(request, args):
        attempts.append(resolve_validate_certificate(request.headers))
        return format_success_response({"ok": 1})

    body = asyncio.run(
        _dispatch(
            handler, {"EDGE_URL": "https://edge.local", "VALIDATE_CERTIFICATE": "false"}
        )
    )
    assert attempts == [False]
    assert "tls_warning" not in body


def test_a_failure_that_survives_the_retry_still_reports_both():
    async def handler(request, args):
        return _cert_failure_response()

    body = asyncio.run(_dispatch(handler))
    assert body["success"] is False
    assert "tls_warning" in body, "the attempt was still made unverified"


def test_the_retry_flag_does_not_leak_into_the_next_dispatch():
    async def failing(request, args):
        verify = resolve_validate_certificate(request.headers)
        return (
            _cert_failure_response() if verify else format_success_response({"ok": 1})
        )

    asyncio.run(_dispatch(failing))

    attempts = []

    async def healthy(request, args):
        attempts.append(resolve_validate_certificate(request.headers))
        return format_success_response({"ok": 1})

    body = asyncio.run(_dispatch(healthy))
    assert attempts == [True], "the next call must verify again"
    assert "tls_warning" not in body


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "message",
    [
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
        "x509: certificate has expired",
        "unable to get local issuer certificate",
        "self-signed certificate in certificate chain",
        "certificate is not valid for edge.local",
    ],
)
def test_recognises_certificate_failures(message):
    assert is_certificate_error_text(message)
    assert is_certificate_error(RuntimeError(message))


def test_does_not_mistake_other_failures_for_certificate_failures():
    for message in ("connection refused", "401 unauthorized", "read timed out"):
        assert not is_certificate_error_text(message)


def test_finds_a_certificate_error_wrapped_in_another_exception():
    try:
        try:
            raise _cert_error()
        except ssl.SSLCertVerificationError as inner:
            raise RuntimeError("connection failed") from inner
    except RuntimeError as outer:
        assert is_certificate_error(outer)


# ---------------------------------------------------------------- visibility


def _payload(content):
    return json.loads(content[0].text)


def test_clean_responses_stay_clean():
    reset_tls_state()
    assert "tls_warning" not in _payload(format_success_response({"devices": []}))
    assert "tls_warning" not in _payload(format_error_response("nope", "nope"))


def test_error_responses_carry_the_warning_too():
    async def handler(request, args):
        if resolve_validate_certificate(request.headers):
            return format_error_response("read_failed", str(_cert_error()))
        return format_error_response("read_failed", "still broken")

    body = asyncio.run(_dispatch(handler))
    assert body["success"] is False
    assert "tls_warning" in body


def test_stdio_mode_verifies_by_default_too():
    """StdioRequestContext used to inject a literal "false", which would have
    pinned verification off for every stdio deployment."""
    import os

    from server import StdioRequestContext

    saved = os.environ.pop("VALIDATE_CERTIFICATE", None)
    try:
        reset_tls_state()
        assert resolve_validate_certificate(StdioRequestContext().headers) is True
        os.environ["VALIDATE_CERTIFICATE"] = "false"
        assert resolve_validate_certificate(StdioRequestContext().headers) is False
    finally:
        os.environ.pop("VALIDATE_CERTIFICATE", None)
        if saved is not None:
            os.environ["VALIDATE_CERTIFICATE"] = saved


# ------------------------------------------------- the web UI (env-configured)
#
# The console reads .env rather than per-request headers. It has to reach the
# same verdict as the MCP server, or the two halves of the same product would
# disagree about whether certificates are checked.


def _env(value):
    import os

    saved = os.environ.pop("VALIDATE_CERTIFICATE", None)
    if value is not None:
        os.environ["VALIDATE_CERTIFICATE"] = value
    return saved


def _restore(saved):
    import os

    os.environ.pop("VALIDATE_CERTIFICATE", None)
    if saved is not None:
        os.environ["VALIDATE_CERTIFICATE"] = saved


@pytest.mark.parametrize(
    "value,expected",
    [(None, True), ("", True), ("true", True), ("false", False), ("off", False)],
)
def test_env_resolver_matches_the_header_policy(value, expected):
    saved = _env(value)
    try:
        assert resolve_validate_certificate_env() is expected
    finally:
        _restore(saved)


def test_env_resolver_fails_closed_on_a_typo():
    saved = _env("flase")
    try:
        assert resolve_validate_certificate_env() is True
    finally:
        _restore(saved)


def test_io_fallback_verifies_first_then_retries():
    attempts = []

    def operation(verify):
        attempts.append(verify)
        if verify:
            raise _cert_error()
        return "probed"

    reset_tls_state()
    result, downgraded = call_with_certificate_fallback(
        operation, True, "https://lem.local"
    )
    assert (result, downgraded, attempts) == ("probed", True, [True, False])
    assert "https://lem.local" in downgrade_warning()


def test_io_fallback_leaves_other_failures_alone():
    attempts = []

    def operation(verify):
        attempts.append(verify)
        raise ValueError("401 unauthorized")

    reset_tls_state()
    with pytest.raises(ValueError):
        call_with_certificate_fallback(operation, True, "https://lem.local")
    assert attempts == [True]
    assert downgrade_warning() is None


def test_io_fallback_finds_a_certificate_error_a_probe_swallowed():
    """The LEM probe collects per-section errors instead of raising, so without
    inspecting the result it would look like a success and never be retried."""
    attempts = []

    def operation(verify):
        attempts.append(verify)
        if verify:
            return {"status": "ok", "deployment_error": str(_cert_error())}
        return {"status": "ok", "deployment": {}}

    reset_tls_state()
    result, downgraded = call_with_certificate_fallback(
        operation,
        True,
        "https://lem.local",
        lambda out: certificate_complaint(out.values()),
    )
    assert attempts == [True, False]
    assert downgraded is True
    assert "deployment_error" not in result
    assert downgrade_warning() is not None


def test_certificate_complaint_ignores_unrelated_errors():
    assert certificate_complaint(["connection refused", {"a": 1}, None]) is None
    assert certificate_complaint(["x509: certificate has expired"]) is not None
