#!/usr/bin/env python3
"""Tests for the private-network caller check on the pipeline endpoints."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.api.pipeline_router import is_private_network_host, verify_localhost
from gluesync_scheduler.core.internal_auth import INTERNAL_TOKEN_HEADER

ALLOWED = [
    "localhost",
    "127.0.0.1",
    "::1",
    "10.89.0.5",
    "10.88.0.2",
    "172.18.0.3",
    "192.168.1.5",
    "::ffff:10.89.0.5",
    "::ffff:127.0.0.1",
    "fd00::1",
    "fe80::1",
    "169.254.1.1",
]
DENIED = [
    "8.8.8.8",
    "172.32.0.1",
    "172.15.255.255",
    "::ffff:8.8.8.8",
    "2001:4860:4860::8888",
    "not-an-ip",
    "",
    None,
]


@pytest.fixture(autouse=True)
def _internal_token(monkeypatch):
    monkeypatch.setenv("CHRONOS_INTERNAL_TOKEN", "test-internal-token")


def _request(host, token="test-internal-token"):
    client = SimpleNamespace(host=host) if host is not None else None
    headers = {INTERNAL_TOKEN_HEADER: token} if token is not None else {}
    return SimpleNamespace(client=client, headers=headers, query_params={}, state=SimpleNamespace())


def _status(request):
    check = verify_localhost(request)
    with pytest.raises(HTTPException) as error:
        asyncio.run(check)
    return error.value.status_code


@pytest.mark.parametrize("host", ALLOWED)
def test_private_network_hosts_with_the_token_are_allowed(host):
    assert is_private_network_host(host) is True
    asyncio.run(verify_localhost(_request(host)))


@pytest.mark.parametrize("host", ALLOWED)
def test_private_network_hosts_without_the_token_get_403(host):
    assert _status(_request(host, token=None)) == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "172.18.0.3"])
def test_a_wrong_token_gets_403(host):
    assert _status(_request(host, token="guess")) == 403


@pytest.mark.parametrize("host", DENIED)
def test_other_hosts_get_403_even_with_the_token(host):
    assert is_private_network_host(host) is False
    assert _status(_request(host)) == 403


def test_token_file_is_created_once_with_mode_600(tmp_path, monkeypatch):
    from gluesync_scheduler.core import internal_auth
    monkeypatch.delenv("CHRONOS_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    internal_auth.reset_cache()
    try:
        first = internal_auth.internal_token()
        path = tmp_path / ".internal-token"
        assert path.read_text() == first
        assert (path.stat().st_mode & 0o777) == 0o600
        internal_auth.reset_cache()
        assert internal_auth.internal_token() == first
        assert internal_auth.internal_headers()[INTERNAL_TOKEN_HEADER] == first
    finally:
        internal_auth.reset_cache()
