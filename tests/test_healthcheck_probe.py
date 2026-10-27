#!/usr/bin/env python3
"""Tests for healthcheck.py, the container HEALTHCHECK probe, against stub servers."""

import datetime
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import healthcheck  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    status = 200
    delay = 0.0
    paths = []

    def do_GET(self):
        type(self).paths.append(self.path)
        if self.delay:
            time.sleep(self.delay)
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def _self_signed_cert(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "not-localhost.invalid")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_file), str(key_file)


@pytest.fixture
def stub_server(tmp_path):
    """Start a stub /health server; returns a factory (use_tls, status, delay) -> port."""
    servers = []

    def start(use_tls=False, status=200, delay=0.0):
        handler = type("Handler", (_Handler,), {"status": status, "delay": delay, "paths": []})
        server = HTTPServer(("127.0.0.1", 0), handler)
        if use_tls:
            cert_file, key_file = _self_signed_cert(tmp_path)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_file, key_file)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        server.handler = handler
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def probe_env(monkeypatch):
    def configure(port, ssl_enabled):
        monkeypatch.setenv("HOST", "127.0.0.1")
        monkeypatch.setenv("PORT", str(port))
        monkeypatch.setenv("SSL_ENABLED", ssl_enabled)

    return configure


def _port(server):
    return server.server_address[1]


def test_http_200_exits_0(stub_server, probe_env):
    server = stub_server(status=200)
    probe_env(_port(server), "false")

    assert healthcheck.main(["--timeout", "3"]) == 0
    assert server.handler.paths == ["/health"]


def test_http_503_exits_1(stub_server, probe_env):
    server = stub_server(status=503)
    probe_env(_port(server), "false")

    assert healthcheck.main(["--timeout", "3"]) == 1


def test_nothing_listening_exits_1(probe_env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    probe_env(free_port, "false")

    assert healthcheck.main(["--timeout", "2"]) == 1


def test_server_that_does_not_answer_exits_1_within_the_timeout(stub_server, probe_env):
    server = stub_server(status=200, delay=3)
    probe_env(_port(server), "false")

    started = time.monotonic()
    assert healthcheck.main(["--timeout", "1"]) == 1
    assert time.monotonic() - started < 3


def test_https_200_exits_0_without_verifying_the_certificate(stub_server, probe_env):
    # The certificate is self-signed and issued to another name: it must not matter
    server = stub_server(use_tls=True, status=200)
    probe_env(_port(server), "true")

    assert healthcheck.main(["--timeout", "5"]) == 0
    assert server.handler.paths == ["/health"]


def test_https_503_exits_1(stub_server, probe_env):
    server = stub_server(use_tls=True, status=503)
    probe_env(_port(server), "true")

    assert healthcheck.main(["--timeout", "5"]) == 1


def test_ssl_enabled_but_server_fell_back_to_http_exits_0(stub_server, probe_env):
    # SSL_ENABLED=true without usable certificates: run_scheduler.py serves plain HTTP
    server = stub_server(use_tls=False, status=200)
    probe_env(_port(server), "true")

    assert healthcheck.main(["--timeout", "5"]) == 0


def test_ssl_disabled_does_not_try_https(stub_server, probe_env):
    server = stub_server(use_tls=True, status=200)
    probe_env(_port(server), "false")

    # A TLS-only server cannot be reached over plain HTTP
    assert healthcheck.main(["--timeout", "3"]) == 1


@pytest.mark.parametrize("value", ["True", "1", "t"])
def test_ssl_enabled_accepts_the_same_truthy_values_as_the_server(stub_server, probe_env, value):
    server = stub_server(use_tls=True, status=200)
    probe_env(_port(server), value)

    assert healthcheck.main(["--timeout", "5"]) == 0


def test_runs_as_a_script_and_exit_code_follows_the_answer(stub_server):
    ok = stub_server(status=200)
    down = stub_server(status=503)
    base_env = {**os.environ, "HOST": "127.0.0.1", "SSL_ENABLED": "false"}

    def run(port):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "healthcheck.py"), "--timeout", "3"],
            env={**base_env, "PORT": str(port)},
            capture_output=True,
            text=True,
        )

    assert run(_port(ok)).returncode == 0
    failed = run(_port(down))
    assert failed.returncode == 1
    assert "HTTP 503" in failed.stderr
