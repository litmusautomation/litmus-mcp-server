"""Certificate verification policy for outbound Litmus connections.

Verification is on by default. A deployment whose edge presents a self-signed
certificate still works, because a tool call that fails on the certificate is
run again with verification off, but that downgrade is never silent: it is
recorded for the current call and the response carries the warning, so the
operator and the model both learn that the data crossed an unverified channel
rather than assuming it was checked.

The retry lives at the tool-dispatch level rather than around the connection
constructors, because the SDK's `new_*_connection` helpers only build a
configuration object. Nothing touches the network until the tool issues its
first request, so that is the earliest point a bad certificate can surface.

The retry is deliberately not cached. A host whose certificate is later fixed
verifies again on the next call, at the cost of one rejected handshake per call
while it stays self-signed.
"""

import contextvars
import logging
import os
import ssl

logger = logging.getLogger(__name__)

# Absent an explicit VALIDATE_CERTIFICATE header, verify.
DEFAULT_VALIDATE_CERTIFICATE = True

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}

# Markers from the several layers that can report a bad certificate (ssl,
# requests, urllib3, httpx, and the Go CLI), matched as text because none of
# them share an exception type once the SDK has wrapped them.
_TLS_MARKERS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "sslcertverificationerror",
    "sslerror",
    "self signed certificate",
    "self-signed certificate",
    "unable to get local issuer certificate",
    "certificate has expired",
    "certificate is not trusted",
    "certificate signed by unknown authority",
    "hostname mismatch",
    "certificate is not valid for",
    "x509:",
)

# Set for the duration of a retry, so every connection built underneath it
# skips verification without each call site needing to know about the retry.
_unverified_retry: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tls_unverified_retry", default=False
)
_downgrades: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "tls_downgrades", default=None
)


def _parse(raw: str) -> bool:
    """Interpret a VALIDATE_CERTIFICATE value, defaulting to on."""
    raw = (raw or "").strip().lower()
    if raw in _FALSE:
        return False
    if raw in _TRUE:
        return True
    if raw:
        logger.warning(
            "VALIDATE_CERTIFICATE=%r is not a boolean; verifying certificates", raw
        )
    return DEFAULT_VALIDATE_CERTIFICATE


def resolve_validate_certificate(headers) -> bool:
    """Read VALIDATE_CERTIFICATE from request headers, defaulting to on.

    Returns False while an unverified retry is in progress. Anything that is
    not a recognised false-y word verifies, so a typo fails closed rather than
    quietly disabling verification.
    """
    if _unverified_retry.get():
        return False
    return _parse(headers.get("VALIDATE_CERTIFICATE", ""))


def resolve_validate_certificate_env() -> bool:
    """Same policy, read from the environment.

    Used by the web UI, which is configured through .env rather than through
    per-request headers, so that the console and the MCP server cannot end up
    disagreeing about whether certificates are checked.
    """
    return _parse(os.environ.get("VALIDATE_CERTIFICATE", ""))


def is_certificate_error_text(text: str) -> bool:
    """True when a message reads as a certificate rejection."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _TLS_MARKERS)


def is_certificate_error(exc: BaseException) -> bool:
    """True when a failure anywhere in the chain is a certificate rejection."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if is_certificate_error_text(f"{type(current).__name__}: {current}"):
            return True
        current = current.__cause__ or current.__context__
    return False


def reset_tls_state() -> None:
    """Clear the per-call record. Called once per tool dispatch."""
    _unverified_retry.set(False)
    _downgrades.set(None)


def begin_unverified_retry() -> None:
    """Make every connection built from here on skip verification."""
    _unverified_retry.set(True)


def in_unverified_retry() -> bool:
    return _unverified_retry.get()


def note_downgrade(target: str, detail: str) -> None:
    """Record that `target` was reached without certificate verification."""
    notes = _downgrades.get()
    if notes is None:
        notes = {}
        _downgrades.set(notes)
    notes[target] = detail
    logger.warning(
        "certificate verification failed for %s; retried without verification (%s)",
        target,
        detail,
    )


def downgrade_warning() -> str | None:
    """The warning to attach to responses built during this call, if any."""
    notes = _downgrades.get()
    if not notes:
        return None
    targets = ", ".join(sorted(notes))
    return (
        f"TLS certificate verification FAILED for {targets} and the call was "
        "retried without verification, so this data crossed an unverified "
        "channel and could have been intercepted. Treat it as untrusted until "
        "the certificate is fixed. Install a certificate the server host "
        "trusts, or set VALIDATE_CERTIFICATE=false to accept this deliberately "
        "and stop the retry."
    )


def call_with_certificate_fallback(
    operation, validate_certificate: bool, target: str, failure_in_result=None
):
    """Run `operation(verify)` and, if a rejected certificate is the only thing
    stopping it, run it again unverified and record the downgrade.

    `operation` must be the call that actually reaches the network. Wrapping a
    connection constructor achieves nothing: litmussdk's `new_*_connection`
    helpers only build a configuration object, so no certificate is presented
    until the first request goes out.

    `failure_in_result` handles operations that collect their own errors
    instead of raising: it is given the result and returns the certificate
    complaint found in it, or None. Without it such an operation would appear
    to succeed and never be retried.

    Returns (result, downgraded).
    """
    if not validate_certificate:
        return operation(False), False
    try:
        result = operation(True)
        detail = failure_in_result(result) if failure_in_result else None
        if detail is None:
            return result, False
    except Exception as exc:
        if not is_certificate_error(exc):
            raise
        detail = str(exc)
    note_downgrade(target, detail.strip()[:400])
    return operation(False), True


def certificate_complaint(values) -> str | None:
    """The first certificate complaint among `values`, or None."""
    for value in values:
        if isinstance(value, str) and is_certificate_error_text(value):
            return value
    return None
