import http.server
import threading

import pytest

from modules import web_fetcher


# --- pure IP-classification tests (no network needed) -----------------------

@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.0.0.53",
        "10.0.0.5",
        "172.16.4.4",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata endpoint
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "224.0.0.1",  # multicast
    ],
)
def test_private_and_internal_ips_are_rejected(ip):
    assert web_fetcher.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34"])
def test_public_ips_are_accepted(ip):
    assert web_fetcher.is_public_ip(ip) is True


def test_default_validate_host_rejects_localhost():
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.default_validate_host("localhost")


def test_default_validate_host_rejects_hostname_resolving_to_private_ip(monkeypatch):
    def fake_getaddrinfo(host, port):
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(web_fetcher.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.default_validate_host("evil-that-resolves-to-loopback.example")


def test_fetch_url_rejects_non_http_scheme():
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.fetch_url("ftp://example.com/file")


def test_fetch_url_rejects_file_scheme():
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.fetch_url("file:///etc/passwd")


def test_fetch_url_rejects_direct_private_ip_url():
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.fetch_url("http://127.0.0.1:1/whatever")


def test_fetch_url_rejects_cloud_metadata_ip():
    with pytest.raises(web_fetcher.UnsafeURLError):
        web_fetcher.fetch_url("http://169.254.169.254/latest/meta-data/")


# --- tests against a real local HTTP server ---------------------------------
# These use a `validate_host` override to allow 127.0.0.1 for THIS test only,
# to exercise the actual connection/redirect/size/content-type/timeout logic.
# The override is never used by application code -- default_validate_host
# (exercised above) is what production/API code always uses.


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redirect-once":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        elif self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"x" * (200))
        elif self.path == "/wrong-type":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(b"%PDF-1.4")
        elif self.path == "/slow":
            import time

            time.sleep(2)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>slow</html>")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body>ok page</body></html>")


@pytest.fixture(scope="module")
def local_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _allow_localhost(hostname):
    return ["127.0.0.1"]


def test_fetch_follows_redirect_to_final_page(local_server):
    page = web_fetcher.fetch_url(f"{local_server}/redirect-once", validate_host=_allow_localhost)
    assert page.status_code == 200
    assert "ok page" in page.text


def test_fetch_enforces_redirect_limit(local_server):
    with pytest.raises(web_fetcher.FetchError):
        web_fetcher.fetch_url(f"{local_server}/redirect-loop", max_redirects=2, validate_host=_allow_localhost)


def test_fetch_enforces_size_limit(local_server):
    with pytest.raises(web_fetcher.FetchError, match="byte limit"):
        web_fetcher.fetch_url(f"{local_server}/big", max_bytes=50, validate_host=_allow_localhost)


def test_fetch_rejects_unsupported_content_type(local_server):
    with pytest.raises(web_fetcher.FetchError, match="content type"):
        web_fetcher.fetch_url(f"{local_server}/wrong-type", validate_host=_allow_localhost)


def test_fetch_enforces_timeout(local_server):
    with pytest.raises(web_fetcher.FetchError, match="timed out"):
        web_fetcher.fetch_url(f"{local_server}/slow", timeout=0.2, validate_host=_allow_localhost)


def test_fetch_revalidates_every_redirect_hop(local_server):
    """Even when the entry URL is allowed for the test, a redirect to a
    disallowed destination must still be blocked (this is what protects real
    usage against an attacker-controlled redirect to an internal address)."""

    class _RedirectingHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/secret")
            self.end_headers()

    def allow_localhost_else_real_check(hostname):
        if hostname == "127.0.0.1":
            return ["127.0.0.1"]
        return web_fetcher.default_validate_host(hostname)  # real check for the redirect target

    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(web_fetcher.UnsafeURLError):
            web_fetcher.fetch_url(f"http://127.0.0.1:{port}/", validate_host=allow_localhost_else_real_check)
    finally:
        server.shutdown()
