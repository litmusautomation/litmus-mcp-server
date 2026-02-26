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
        _write_env(env_path, {
            "EDGE_URL": "https://edge.local",
            "EDGE_API_CLIENT_ID": "my-client",
        })

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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
