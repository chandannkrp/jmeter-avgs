import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

ALLOWED_ROOTS = [Path(p).resolve() for p in ("workspace", "samples", "output")]
MAX_SPEC_BYTES = 5_000_000


def safe_path(p: str) -> Path:
    """Resolve a path and refuse anything outside the allowed folders."""
    path = Path(p).resolve()
    if not any(path.is_relative_to(root) for root in ALLOWED_ROOTS):
        raise PermissionError(f"Access outside the workspace is not allowed: {p}")
    return path


def check_url(url: str) -> None:
    """Allow only http(s) URLs that resolve to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only http(s) URLs are allowed.")
    for info in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise PermissionError("URLs resolving to private or internal addresses are blocked.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect could point somewhere internal


opener = build_opener(_NoRedirect)