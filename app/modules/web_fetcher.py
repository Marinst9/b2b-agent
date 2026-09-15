"""Safe outbound HTTP(S) fetcher for company research.

Only public http/https destinations are allowed. Private, loopback, link-local,
and other internal/reserved addresses are rejected -- including redirect
targets. DNS is resolved exactly once per hop and the connection is opened
directly against that validated IP address (never re-resolved), which closes
the classic DNS-rebinding TOCTOU gap where a hostname could validate safe and
then resolve to an internal address at actual connect time.
"""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urljoin

DEFAULT_TIMEOUT = 8.0
DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
DEFAULT_MAX_REDIRECTS = 3
ALLOWED_CONTENT_TYPES = ("text/html", "text/plain")
USER_AGENT = "B2BOutreachResearchBot/1.0 (+company-research)"
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class UnsafeURLError(Exception):
    """The URL (or a redirect target) is not an allowed public destination."""


class FetchError(Exception):
    """The request could not be completed safely (timeout, too large, bad content type, DNS failure, ...)."""


class FetchedPage:
    def __init__(self, url: str, status_code: int, content_type: str, text: str):
        self.url = url
        self.status_code = status_code
        self.content_type = content_type
        self.text = text


def is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def default_validate_host(hostname: str) -> list[str]:
    """Resolves `hostname` and returns its validated public IP addresses, or
    raises UnsafeURLError/FetchError. Every candidate address must be public --
    if any resolved address is internal, the whole hostname is rejected."""
    if not hostname:
        raise UnsafeURLError("URL has no hostname")
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise UnsafeURLError("localhost is not an allowed research destination")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise FetchError(f"DNS resolution failed for '{hostname}': {e}")

    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise UnsafeURLError(f"could not resolve '{hostname}'")

    for ip in ips:
        if not is_public_ip(ip):
            raise UnsafeURLError(f"'{hostname}' resolves to a non-public address ({ip})")

    return ips


def _make_pinned_connection(scheme: str, host: str, port: int, ip: str, timeout: float):
    if scheme == "https":
        raw_sock = socket.create_connection((ip, port), timeout=timeout)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=host)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        conn.sock = sock
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = socket.create_connection((ip, port), timeout=timeout)
    return conn


def fetch_url(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    validate_host=default_validate_host,
) -> FetchedPage:
    """Fetches `url`, following at most `max_redirects` redirects. Every hop
    (including each redirect target) is independently scheme-checked and
    DNS-validated before any connection is opened."""
    current_url = url

    for _ in range(max_redirects + 1):
        parts = urlsplit(current_url)
        if parts.scheme not in ("http", "https"):
            raise UnsafeURLError(f"unsupported URL scheme: {parts.scheme!r}")
        if not parts.hostname:
            raise UnsafeURLError("URL has no hostname")

        host = parts.hostname
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        ips = validate_host(host)
        conn = _make_pinned_connection(parts.scheme, host, port, ips[0], timeout)
        try:
            conn.sock.settimeout(timeout)
            conn.request(
                "GET",
                path,
                headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "text/html,text/plain"},
            )
            resp = conn.getresponse()

            if resp.status in _REDIRECT_STATUSES:
                location = resp.getheader("Location")
                if not location:
                    raise FetchError(f"redirect from {current_url} had no Location header")
                current_url = urljoin(current_url, location)
                continue

            ctype_header = resp.getheader("Content-Type") or ""
            content_type = ctype_header.split(";")[0].strip().lower()
            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                raise FetchError(f"unsupported content type: {content_type!r}")

            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise FetchError(f"response exceeded {max_bytes} byte limit")

            charset = "utf-8"
            if "charset=" in ctype_header:
                charset = ctype_header.split("charset=")[-1].split(";")[0].strip() or "utf-8"
            try:
                text = body.decode(charset, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")

            return FetchedPage(
                url=current_url,
                status_code=resp.status,
                content_type=content_type or "text/html",
                text=text,
            )
        except (TimeoutError, socket.timeout) as e:
            raise FetchError(f"request to {current_url} timed out: {e}")
        finally:
            conn.close()

    raise FetchError(f"too many redirects (> {max_redirects}) starting from {url}")
