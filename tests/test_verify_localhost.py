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


def _request(host):
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(client=client, headers={}, query_params={}, state=SimpleNamespace())


@pytest.mark.parametrize("host", ALLOWED)
def test_private_network_hosts_are_allowed(host):
    assert is_private_network_host(host) is True
    asyncio.run(verify_localhost(_request(host)))


@pytest.mark.parametrize("host", DENIED)
def test_other_hosts_get_403(host):
    assert is_private_network_host(host) is False
    with pytest.raises(HTTPException) as error:
        asyncio.run(verify_localhost(_request(host)))
    assert error.value.status_code == 403
