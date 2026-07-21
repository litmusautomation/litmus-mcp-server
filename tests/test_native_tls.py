"""
Tests for config.tls_settings (native TLS via SSL_CERTFILE / SSL_KEYFILE)

Key cases:
  - neither var set          -> {} (plain HTTP, the default)
  - both set to real files   -> uvicorn ssl kwargs
  - only one set             -> ValueError (never silently fall back)
  - path is not a file       -> ValueError
  - SSL_KEYFILE_PASSWORD     -> included only when set
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from config import tls_settings

TLS_VARS = ("SSL_CERTFILE", "SSL_KEYFILE", "SSL_KEYFILE_PASSWORD")


@pytest.fixture(autouse=True)
def clean_tls_env(monkeypatch):
    for var in TLS_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cert_pair(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("dummy cert")
    key.write_text("dummy key")
    return str(cert), str(key)


class TestTlsSettings:

    def test_returns_empty_dict_when_unset(self):
        assert tls_settings() == {}

    def test_empty_strings_count_as_unset(self, monkeypatch):
        monkeypatch.setenv("SSL_CERTFILE", "")
        monkeypatch.setenv("SSL_KEYFILE", "  ")
        assert tls_settings() == {}

    def test_both_set_returns_uvicorn_kwargs(self, monkeypatch, cert_pair):
        cert, key = cert_pair
        monkeypatch.setenv("SSL_CERTFILE", cert)
        monkeypatch.setenv("SSL_KEYFILE", key)
        assert tls_settings() == {"ssl_certfile": cert, "ssl_keyfile": key}

    def test_certfile_alone_raises(self, monkeypatch, cert_pair):
        monkeypatch.setenv("SSL_CERTFILE", cert_pair[0])
        with pytest.raises(ValueError, match="must be set together"):
            tls_settings()

    def test_keyfile_alone_raises(self, monkeypatch, cert_pair):
        monkeypatch.setenv("SSL_KEYFILE", cert_pair[1])
        with pytest.raises(ValueError, match="must be set together"):
            tls_settings()

    def test_missing_certfile_raises(self, monkeypatch, cert_pair, tmp_path):
        monkeypatch.setenv("SSL_CERTFILE", str(tmp_path / "absent.crt"))
        monkeypatch.setenv("SSL_KEYFILE", cert_pair[1])
        with pytest.raises(ValueError, match="SSL_CERTFILE is not a readable file"):
            tls_settings()

    def test_missing_keyfile_raises(self, monkeypatch, cert_pair, tmp_path):
        monkeypatch.setenv("SSL_CERTFILE", cert_pair[0])
        monkeypatch.setenv("SSL_KEYFILE", str(tmp_path / "absent.key"))
        with pytest.raises(ValueError, match="SSL_KEYFILE is not a readable file"):
            tls_settings()

    def test_directory_is_not_a_file(self, monkeypatch, cert_pair, tmp_path):
        monkeypatch.setenv("SSL_CERTFILE", str(tmp_path))
        monkeypatch.setenv("SSL_KEYFILE", cert_pair[1])
        with pytest.raises(ValueError, match="SSL_CERTFILE is not a readable file"):
            tls_settings()

    def test_key_password_included_when_set(self, monkeypatch, cert_pair):
        cert, key = cert_pair
        monkeypatch.setenv("SSL_CERTFILE", cert)
        monkeypatch.setenv("SSL_KEYFILE", key)
        monkeypatch.setenv("SSL_KEYFILE_PASSWORD", "hunter2")
        assert tls_settings()["ssl_keyfile_password"] == "hunter2"

    def test_key_password_omitted_when_unset(self, monkeypatch, cert_pair):
        cert, key = cert_pair
        monkeypatch.setenv("SSL_CERTFILE", cert)
        monkeypatch.setenv("SSL_KEYFILE", key)
        assert "ssl_keyfile_password" not in tls_settings()
