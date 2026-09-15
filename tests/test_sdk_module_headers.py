import os
from types import SimpleNamespace
from unittest.mock import patch

from gluesync_scheduler.core.gluesync_sdk_client import _with_module_endpoint_headers


def test_with_module_endpoint_headers_adds_port_and_scheme():
    client = SimpleNamespace(
        get_connection_headers=lambda: {"Existing": "keep"},
    )

    with patch.dict(os.environ, {"PORT": "2727", "SSL_ENABLED": "true"}):
        wrapped = _with_module_endpoint_headers(client)
        headers = wrapped.get_connection_headers()

    assert headers["Existing"] == "keep"
    assert headers["Module-Port"] == "2727"
    assert headers["Module-Scheme"] == "https"


def test_with_module_endpoint_headers_defaults_to_http_on_1717():
    client = SimpleNamespace(get_connection_headers=lambda: {})

    env = os.environ.copy()
    env.pop("PORT", None)
    env.pop("SSL_ENABLED", None)
    with patch.dict(os.environ, env, clear=True):
        headers = _with_module_endpoint_headers(client).get_connection_headers()

    assert headers["Module-Port"] == "1717"
    assert headers["Module-Scheme"] == "http"
