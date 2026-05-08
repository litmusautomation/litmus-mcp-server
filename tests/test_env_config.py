"""
Tests for env_config._get_env_vars (Fix A)

Covers the `if not path_to_env:` correction that replaced the broken
`if path_to_env == "" or None:` expression.

Key cases:
  - find_dotenv returns ""  (not found) → should create .env and retry
  - find_dotenv returns None (not found) → same — this was the silent bug
  - find_dotenv returns a real path     → must NOT create a new file
  - existing .env content is parsed correctly
"""

import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from env_config import _get_env_vars

# ── helpers ────────────────────────────────────────────────────────────────


def _write_env(path, content: dict):
    with open(path, "w") as f:
        for k, v in content.items():
            f.write(f"{k}={v}\n")


# ── Fix A: branch taken when .env is not found ─────────────────────────────


class TestGetEnvVarsCreatesFile:

    def test_creates_env_file_when_find_dotenv_returns_empty_string(
        self, tmp_path, monkeypatch
    ):
        """find_dotenv returning '' should trigger file creation and a retry."""
        monkeypatch.chdir(tmp_path)

        expected_path = str(tmp_path / ".env")
        find_calls = []

        def fake_find(filename):
            find_calls.append(filename)
            # First call: not found; second call (after creation): found
            return "" if len(find_calls) == 1 else expected_path

        with patch("env_config.dotenv.find_dotenv", side_effect=fake_find):
            with patch("env_config.dotenv.load_dotenv"):
                _get_env_vars(".env", override=False)

        # find_dotenv must have been called twice (initial check + retry)
        assert len(find_calls) == 2
        # The .env file should have been written to disk
        assert os.path.exists(expected_path)

    def test_creates_env_file_when_find_dotenv_returns_none(
        self, tmp_path, monkeypatch
    ):
        """find_dotenv returning None must also trigger file creation.

        With the OLD code (`path_to_env == "" or None`), `None == ""` is
        False, so the branch was silently skipped — this is the regression
        test for that bug.
        """
        monkeypatch.chdir(tmp_path)

        expected_path = str(tmp_path / ".env")
        find_calls = []

        def fake_find(filename):
            find_calls.append(filename)
            return None if len(find_calls) == 1 else expected_path

        with patch("env_config.dotenv.find_dotenv", side_effect=fake_find):
            with patch("env_config.dotenv.load_dotenv"):
                _get_env_vars(".env", override=False)

        # Must have retried — would have been 1 with the old bug
        assert len(find_calls) == 2
        assert os.path.exists(expected_path)

    def test_new_env_file_contains_initiate_sentinel(self, tmp_path, monkeypatch):
        """Created .env file should contain the sentinel line 'ENV=Initiate'."""
        monkeypatch.chdir(tmp_path)

        expected_path = str(tmp_path / ".env")

        def fake_find(filename):
            return "" if not os.path.exists(expected_path) else expected_path

        with patch("env_config.dotenv.find_dotenv", side_effect=fake_find):
            with patch("env_config.dotenv.load_dotenv"):
                _get_env_vars(".env", override=False)

        with open(expected_path) as f:
            contents = f.read()
        assert "ENV=Initiate" in contents


# ── Fix A: branch NOT taken when .env already exists ──────────────────────


class TestGetEnvVarsExistingFile:

    def test_does_not_create_file_when_found(self, tmp_path):
        """find_dotenv returning a real path must not create a new file."""
        env_path = str(tmp_path / ".env")
        _write_env(env_path, {"KEY": "val"})

        find_calls = []

        def fake_find(filename):
            find_calls.append(filename)
            return env_path  # Always found

        with patch("env_config.dotenv.find_dotenv", side_effect=fake_find):
            with patch("env_config.dotenv.load_dotenv"):
                _get_env_vars(".env", override=False)

        # Only one call: no retry needed
        assert len(find_calls) == 1

    def test_reads_key_value_pairs_from_existing_file(self, tmp_path):
        """Key=value lines in .env are returned as a dict."""
        env_path = str(tmp_path / ".env")
        _write_env(
            env_path,
            {
                "EDGE_URL": "https://edge.local",
                "EDGE_API_CLIENT_ID": "my-client",
            },
        )

        with patch("env_config.dotenv.find_dotenv", return_value=env_path):
            with patch("env_config.dotenv.load_dotenv"):
                env_vars, path = _get_env_vars(".env", override=False)

        assert env_vars["EDGE_URL"] == "https://edge.local"
        assert env_vars["EDGE_API_CLIENT_ID"] == "my-client"
        assert path == env_path

    def test_ignores_lines_without_equals(self, tmp_path):
        """Comment lines and blank lines are not parsed as key-value pairs."""
        env_path = str(tmp_path / ".env")
        with open(env_path, "w") as f:
            f.write("# This is a comment\n")
            f.write("\n")
            f.write("VALID_KEY=value\n")

        with patch("env_config.dotenv.find_dotenv", return_value=env_path):
            with patch("env_config.dotenv.load_dotenv"):
                env_vars, _ = _get_env_vars(".env", override=False)

        assert "VALID_KEY" in env_vars
        assert len(env_vars) == 1


# ── activate_edge_instance: standalone LEM preservation ───────────────────


class TestActivateEdgeInstancePreservesLemSettings:
    """Switching to a direct-mode instance must not wipe standalone LEM creds."""

    def test_direct_mode_switch_preserves_manager_url_and_token(self, monkeypatch):
        from env_config import activate_edge_instance

        # Standalone LEM creds set via the LEM Settings panel
        monkeypatch.setenv("EDGE_MANAGER_URL", "https://lem.example.com")
        monkeypatch.setenv("EDGE_API_TOKEN", "lem-token-xyz")
        # Bridge-only fields populated from a previous bridge instance
        monkeypatch.setenv("EDGE_MANAGER_PROJECT_ID", "proj-old")
        monkeypatch.setenv("EDGE_MANAGER_DEVICE_ID", "dev-old")

        # Direct-mode instance to activate
        monkeypatch.setenv("EDGE_INSTANCE_1_TYPE", "direct")
        monkeypatch.setenv("EDGE_INSTANCE_1_URL", "https://edge.local")
        monkeypatch.setenv("EDGE_INSTANCE_1_CLIENT_ID", "client-1")
        monkeypatch.setenv("EDGE_INSTANCE_1_SECRET", "secret-1")

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            with patch("env_config.mcp_env_loader"):
                activate_edge_instance(1)

        # LEM standalone creds survive
        assert "EDGE_MANAGER_URL" not in writes
        assert "EDGE_API_TOKEN" not in writes
        assert os.environ["EDGE_MANAGER_URL"] == "https://lem.example.com"
        assert os.environ["EDGE_API_TOKEN"] == "lem-token-xyz"

        # Bridge-only fields were cleared
        assert writes["EDGE_MANAGER_PROJECT_ID"] == ""
        assert writes["EDGE_MANAGER_DEVICE_ID"] == ""

        # Direct-mode fields populated
        assert writes["EDGE_URL"] == "https://edge.local"
        assert writes["EDGE_API_CLIENT_ID"] == "client-1"
        assert writes["EDGE_API_CLIENT_SECRET"] == "secret-1"


# ── LEM connection helpers + legacy migration ────────────────────────────


class TestLemConnectionHelpers:
    """get_lem_connections / next_lem_connection_index / activate / remove."""

    def test_get_lem_connections_reads_indexed_keys(self, monkeypatch):
        from env_config import get_lem_connections

        monkeypatch.setenv("LEM_CONNECTION_1_URL", "https://lem-a.example.com")
        monkeypatch.setenv("LEM_CONNECTION_1_TOKEN", "tok-a")
        monkeypatch.setenv("LEM_CONNECTION_1_NAME", "Lab A")
        monkeypatch.setenv("LEM_CONNECTION_3_URL", "https://lem-c.example.com")
        monkeypatch.setenv("LEM_CONNECTION_3_TOKEN", "tok-c")

        connections = get_lem_connections()
        assert [c["index"] for c in connections] == [1, 3]
        assert connections[0]["name"] == "Lab A"
        assert connections[1]["name"] == "LEM 3"  # default name when none set

    def test_next_lem_connection_index_skips_used(self, monkeypatch):
        from env_config import next_lem_connection_index

        monkeypatch.setenv("LEM_CONNECTION_1_URL", "https://x")
        monkeypatch.setenv("LEM_CONNECTION_2_URL", "https://y")
        assert next_lem_connection_index() == 3

    def test_activate_lem_connection_writes_main_vars(self, monkeypatch):
        from env_config import activate_lem_connection, ACTIVE_LEM_CONNECTION

        monkeypatch.setenv("LEM_CONNECTION_2_URL", "https://lem.example.com")
        monkeypatch.setenv("LEM_CONNECTION_2_TOKEN", "tok-2")
        # Stale bridge fields should be cleared on activation.
        monkeypatch.setenv("EDGE_MANAGER_PROJECT_ID", "old-proj")
        monkeypatch.setenv("EDGE_MANAGER_DEVICE_ID", "old-dev")

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            with patch("env_config.mcp_env_loader"):
                activate_lem_connection(2)

        assert writes["EDGE_MANAGER_URL"] == "https://lem.example.com"
        assert writes["EDGE_API_TOKEN"] == "tok-2"
        assert writes["EDGE_MANAGER_PROJECT_ID"] == ""
        assert writes["EDGE_MANAGER_DEVICE_ID"] == ""
        assert writes[ACTIVE_LEM_CONNECTION] == "2"

    def test_activate_bridge_edge_clears_active_lem(self, monkeypatch):
        """Activating a bridge edge instance overrides EDGE_MANAGER_URL/TOKEN, so
        ACTIVE_LEM_CONNECTION must be cleared to reflect that the LEM creds now
        belong to the edge instance, not a standalone connection."""
        from env_config import activate_edge_instance, ACTIVE_LEM_CONNECTION

        monkeypatch.setenv("EDGE_INSTANCE_5_TYPE", "lem")
        monkeypatch.setenv("EDGE_INSTANCE_5_URL", "https://bridge-lem.example.com")
        monkeypatch.setenv("EDGE_INSTANCE_5_API_TOKEN", "bridge-tok")
        monkeypatch.setenv("EDGE_INSTANCE_5_PROJECT_ID", "proj-x")
        monkeypatch.setenv("EDGE_INSTANCE_5_DEVICE_ID", "dev-x")
        monkeypatch.setenv(ACTIVE_LEM_CONNECTION, "1")  # stale

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            with patch("env_config.mcp_env_loader"):
                activate_edge_instance(5)

        assert writes[ACTIVE_LEM_CONNECTION] == "0"
        assert writes["EDGE_MANAGER_URL"] == "https://bridge-lem.example.com"


class TestLegacyLemMigration:
    """migrate_legacy_lem_settings: lift EDGE_MANAGER_URL/EDGE_API_TOKEN into LEM_CONNECTION_1."""

    def test_migration_creates_first_connection(self, monkeypatch):
        from env_config import migrate_legacy_lem_settings, ACTIVE_LEM_CONNECTION

        monkeypatch.setenv("EDGE_MANAGER_URL", "https://legacy-lem.example.com")
        monkeypatch.setenv("EDGE_API_TOKEN", "legacy-tok")
        # No existing LEM_CONNECTION_* keys, no bridge project id.
        for k in list(os.environ):
            if k.startswith("LEM_CONNECTION_"):
                monkeypatch.delenv(k, raising=False)
        monkeypatch.delenv("EDGE_MANAGER_PROJECT_ID", raising=False)

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            migrate_legacy_lem_settings()

        assert writes["LEM_CONNECTION_1_URL"] == "https://legacy-lem.example.com"
        assert writes["LEM_CONNECTION_1_TOKEN"] == "legacy-tok"
        assert "LEM_CONNECTION_1_NAME" in writes
        # Auto-activate since no bridge instance owns the legacy creds.
        assert writes[ACTIVE_LEM_CONNECTION] == "1"

    def test_migration_skips_when_existing_connection_present(self, monkeypatch):
        from env_config import migrate_legacy_lem_settings

        monkeypatch.setenv("EDGE_MANAGER_URL", "https://legacy.example.com")
        monkeypatch.setenv("EDGE_API_TOKEN", "tok")
        monkeypatch.setenv("LEM_CONNECTION_1_URL", "https://existing.example.com")

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            migrate_legacy_lem_settings()

        # Nothing written: existing connection blocks migration.
        assert writes == {}

    def test_migration_skips_when_no_legacy_creds(self, monkeypatch):
        from env_config import migrate_legacy_lem_settings

        monkeypatch.delenv("EDGE_MANAGER_URL", raising=False)
        monkeypatch.delenv("EDGE_API_TOKEN", raising=False)
        for k in list(os.environ):
            if k.startswith("LEM_CONNECTION_"):
                monkeypatch.delenv(k, raising=False)

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            migrate_legacy_lem_settings()

        assert writes == {}

    def test_migration_does_not_auto_activate_when_bridge_project_set(
        self, monkeypatch
    ):
        """If EDGE_MANAGER_PROJECT_ID is set, the legacy creds belong to a bridge
        instance, not a standalone connection. Migrate them but don't auto-activate."""
        from env_config import migrate_legacy_lem_settings, ACTIVE_LEM_CONNECTION

        monkeypatch.setenv("EDGE_MANAGER_URL", "https://legacy.example.com")
        monkeypatch.setenv("EDGE_API_TOKEN", "tok")
        monkeypatch.setenv("EDGE_MANAGER_PROJECT_ID", "owned-by-bridge")
        for k in list(os.environ):
            if k.startswith("LEM_CONNECTION_"):
                monkeypatch.delenv(k, raising=False)

        writes: dict = {}

        def fake_updater(key, value):
            writes[key] = value
            os.environ[key] = value

        with patch("env_config.mcp_env_updater", side_effect=fake_updater):
            migrate_legacy_lem_settings()

        # The connection is created (so it's selectable later), but not active.
        assert writes["LEM_CONNECTION_1_URL"] == "https://legacy.example.com"
        assert ACTIVE_LEM_CONNECTION not in writes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
