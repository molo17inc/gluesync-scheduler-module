from urllib.parse import urlparse

DEFAULT_COREHUB_PORT = 1717


def normalize_corehub_host(raw_value: str, ssl_enabled: bool, default_port: int = DEFAULT_COREHUB_PORT):
    """Normalize GLUESYNC_HOST values.

    Returns a tuple of ``(host, port, base_url)`` with protocol inferred from the
    SSL setting. When parsing fails the tuple contains ``None`` values.
    """
    if not raw_value:
        return None, None, None

    value = raw_value.strip()
    if not value:
        return None, None, None

    has_scheme = "://" in value
    parse_target = value if has_scheme else f"//{value}"
    parsed = urlparse(parse_target)

    host = parsed.hostname or parsed.netloc or parsed.path or value
    if host:
        host = host.strip("[]")  # Handle IPv6 literals

    if not host:
        return None, None, None

    port = parsed.port or default_port
    scheme = parsed.scheme if has_scheme and parsed.scheme else ("https" if ssl_enabled else "http")

    base_url = f"{scheme}://{host}:{port}"
    return host, port, base_url
