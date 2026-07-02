"""
SDK contract guards.

These tests don't exercise MCP tool code directly; they pin behaviors of
`litmussdk` that our DeviceHub tools rely on. If a future SDK bump regresses
either of these, `get_devicehub_devices` and any other code path that triggers
`Device.model_validate` would start failing on a cold driver-record cache.

Background — fixed in litmussdk 2.5.6:
  - `load_dh_record` now catches `MissingRecordVersionError`, calls
    `create_dh_cache(version, le_connection)`, and retries instead of
    propagating the error up through `devices.list_devices(...)`.
  - `create_dh_cache` is now idempotent (returns silently if a cache for
    the version already exists), so concurrent auto-downloads can't crash
    each other.
"""

from unittest.mock import MagicMock, patch

from litmussdk.devicehub.record._functions import (
    clear_dh_record_cache,
    load_dh_record,
)
from litmussdk.devicehub.record._utils import create_dh_cache
from litmussdk.utils.errors.devicehub import MissingRecordVersionError


def test_load_dh_record_recovers_from_cold_cache():
    """First DriverRecord lookup raises (cache miss) → create_dh_cache is
    called with the live connection → second lookup succeeds."""
    clear_dh_record_cache()
    conn = MagicMock()
    conn.get_le_version.return_value = "4.0.0"
    fake_record = MagicMock(name="DriverRecord")

    calls = []

    def fake_driver_record(version):
        calls.append(version)
        if len(calls) == 1:
            raise MissingRecordVersionError(version)
        return fake_record

    with (
        patch(
            "litmussdk.devicehub.record._functions.DriverRecord",
            side_effect=fake_driver_record,
        ),
        patch(
            "litmussdk.devicehub.record._functions.create_dh_cache"
        ) as mock_create_cache,
    ):
        result = load_dh_record(le_connection=conn)

    assert result is fake_record
    assert calls == ["4.0.0", "4.0.0"]
    mock_create_cache.assert_called_once_with("4.0.0", conn)


def test_load_dh_record_warm_cache_does_not_download():
    """Warm cache path: DriverRecord returns immediately, create_dh_cache
    is never invoked. Guards against an over-eager fix that always
    downloads."""
    clear_dh_record_cache()
    conn = MagicMock()
    conn.get_le_version.return_value = "4.0.0"
    fake_record = MagicMock(name="DriverRecord")

    with (
        patch(
            "litmussdk.devicehub.record._functions.DriverRecord",
            return_value=fake_record,
        ),
        patch(
            "litmussdk.devicehub.record._functions.create_dh_cache"
        ) as mock_create_cache,
    ):
        result = load_dh_record(le_connection=conn)

    assert result is fake_record
    mock_create_cache.assert_not_called()


def test_create_dh_cache_idempotent_on_existing_cache():
    """If a cache file already exists for the version, create_dh_cache
    must short-circuit silently — not raise. Required because the
    auto-download retry in `load_dh_record` may race with another caller
    that has already populated the cache."""
    conn = MagicMock()

    with (
        patch(
            "litmussdk.devicehub.record._utils.dh_cache_dir",
            return_value="/already/cached",
        ),
        patch("litmussdk.devicehub.record._utils.download_dh_cache") as mock_download,
    ):
        create_dh_cache("4.0.0", le_connection=conn)

    mock_download.assert_not_called()


def test_web_client_lazy_sdk_imports_resolve():
    """src/web_client.py (the production entry point via run.sh / Docker CMD)
    imports these litmussdk symbols lazily inside request handlers, so no
    other test touches them at collection time. Mirror those imports here so
    an SDK bump that moves or removes any of them fails CI instead of
    surfacing at runtime in the web UI."""
    from litmussdk.lem.companies import list_all_company_stats  # noqa: F401
    from litmussdk.lem.lifecycle.dashboard import (  # noqa: F401
        deployment_info,
        get_system_time,
    )
    from litmussdk.utils.api import direct_request  # noqa: F401
    from litmussdk.utils.conn import (  # noqa: F401
        new_le_connection,
        new_lem_bridge_connection,
        new_lem_connection,
    )
