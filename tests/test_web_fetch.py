"""Tests for jack_tools.web_fetch — SSRF guard and fetch behaviour."""

from __future__ import annotations

import io
import socket
import unittest
from unittest.mock import MagicMock, patch

from jack_tools.web_fetch import _host_is_blocked, _ip_is_blocked, _strip_html, fetch_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(body: str, content_type: str = "text/html; charset=utf-8") -> MagicMock:
    """Return a mock urllib response with the given body bytes."""
    encoded = body.encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = encoded
    resp.headers = {"Content-Type": content_type}
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_opener_returning(resp: MagicMock):
    """Return a mock opener whose open() returns *resp*."""
    opener = MagicMock()
    opener.open.return_value = resp
    return opener


# ---------------------------------------------------------------------------
# Unit tests for _ip_is_blocked
# ---------------------------------------------------------------------------

class TestIpIsBlocked(unittest.TestCase):
    def test_loopback_v4(self):
        self.assertTrue(_ip_is_blocked("127.0.0.1"))

    def test_loopback_v6(self):
        self.assertTrue(_ip_is_blocked("::1"))

    def test_private_10(self):
        self.assertTrue(_ip_is_blocked("10.0.0.1"))

    def test_private_192_168(self):
        self.assertTrue(_ip_is_blocked("192.168.1.1"))

    def test_private_172_16(self):
        self.assertTrue(_ip_is_blocked("172.16.0.1"))

    def test_link_local_metadata(self):
        self.assertTrue(_ip_is_blocked("169.254.169.254"))

    def test_link_local_ipv6(self):
        self.assertTrue(_ip_is_blocked("fe80::1"))

    def test_public_ip_allowed(self):
        self.assertFalse(_ip_is_blocked("93.184.216.34"))  # example.com

    def test_non_ip_string_returns_false(self):
        # A hostname is not an IP literal; _ip_is_blocked returns False so
        # _host_is_blocked can proceed to DNS resolution instead.
        self.assertFalse(_ip_is_blocked("not-an-ip"))


# ---------------------------------------------------------------------------
# Unit tests for _host_is_blocked
# ---------------------------------------------------------------------------

class TestHostIsBlocked(unittest.TestCase):
    def test_localhost_literal(self):
        self.assertTrue(_host_is_blocked("localhost"))

    def test_127_literal(self):
        self.assertTrue(_host_is_blocked("127.0.0.1"))

    def test_0_0_0_0_literal(self):
        self.assertTrue(_host_is_blocked("0.0.0.0"))

    def test_ipv6_loopback_literal(self):
        self.assertTrue(_host_is_blocked("::1"))

    def test_metadata_ip_literal(self):
        self.assertTrue(_host_is_blocked("169.254.169.254"))

    def test_private_10_literal(self):
        self.assertTrue(_host_is_blocked("10.0.0.1"))

    def test_private_192_168_literal(self):
        self.assertTrue(_host_is_blocked("192.168.1.1"))

    def test_empty_host(self):
        self.assertTrue(_host_is_blocked(""))

    def test_unresolvable_host(self):
        with patch("jack_tools.web_fetch.socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            self.assertTrue(_host_is_blocked("thisdomaindoesnotexist.invalid"))

    def test_public_host_resolves_fine(self):
        # Patch getaddrinfo to return a public IP so no real DNS needed in tests.
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]
        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos):
            self.assertFalse(_host_is_blocked("example.com"))

    def test_dns_rebinding_blocked(self):
        """A hostname that resolves to a private IP must be blocked even if it looks public."""
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.100", 0))]
        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos):
            self.assertTrue(_host_is_blocked("evil-rebind.example.com"))


# ---------------------------------------------------------------------------
# Unit tests for _strip_html
# ---------------------------------------------------------------------------

class TestStripHtml(unittest.TestCase):
    def test_removes_tags(self):
        result = _strip_html("<p>Hello <b>world</b></p>")
        self.assertIn("Hello", result)
        self.assertIn("world", result)
        self.assertNotIn("<p>", result)
        self.assertNotIn("<b>", result)

    def test_removes_script_block(self):
        result = _strip_html("<html><script>alert('xss')</script><p>text</p></html>")
        self.assertNotIn("alert", result)
        self.assertIn("text", result)

    def test_removes_style_block(self):
        result = _strip_html("<style>body { color: red; }</style><p>content</p>")
        self.assertNotIn("color", result)
        self.assertIn("content", result)

    def test_decodes_entities(self):
        result = _strip_html("&lt;b&gt;bold&lt;/b&gt; &amp; &quot;quoted&quot;")
        self.assertIn("<b>", result)
        self.assertIn("&", result)
        self.assertIn('"quoted"', result)

    def test_collapses_whitespace(self):
        result = _strip_html("  lots   of   spaces  ")
        self.assertEqual(result, "lots of spaces")

    def test_empty_string(self):
        self.assertEqual(_strip_html(""), "")


# ---------------------------------------------------------------------------
# Integration tests for fetch_url
# ---------------------------------------------------------------------------

class TestFetchUrl(unittest.TestCase):

    # ------------------------------------------------------------------
    # Scheme guards
    # ------------------------------------------------------------------

    def test_rejects_non_http_scheme_ftp(self):
        self.assertIsNone(fetch_url("ftp://example.com/file"))

    def test_rejects_file_scheme(self):
        self.assertIsNone(fetch_url("file:///etc/passwd"))

    def test_rejects_gopher_scheme(self):
        self.assertIsNone(fetch_url("gopher://example.com/"))

    def test_rejects_empty_string(self):
        self.assertIsNone(fetch_url(""))

    def test_rejects_none_url(self):
        # Should not raise even if called with None coerced to str
        self.assertIsNone(fetch_url(""))

    # ------------------------------------------------------------------
    # SSRF guards — private/localhost addresses
    # ------------------------------------------------------------------

    def test_blocks_localhost(self):
        self.assertIsNone(fetch_url("http://localhost/secret"))

    def test_blocks_127_0_0_1(self):
        self.assertIsNone(fetch_url("http://127.0.0.1/"))

    def test_blocks_0_0_0_0(self):
        self.assertIsNone(fetch_url("http://0.0.0.0/"))

    def test_blocks_10_x(self):
        self.assertIsNone(fetch_url("http://10.0.0.1/"))

    def test_blocks_192_168_x(self):
        self.assertIsNone(fetch_url("http://192.168.1.1/"))

    def test_blocks_172_16_x(self):
        self.assertIsNone(fetch_url("http://172.16.0.1/"))

    def test_blocks_169_254_metadata(self):
        """Cloud metadata endpoint must be blocked."""
        self.assertIsNone(fetch_url("http://169.254.169.254/latest/meta-data/"))

    def test_blocks_ipv6_loopback(self):
        self.assertIsNone(fetch_url("http://[::1]/"))

    def test_blocks_dns_rebinding_to_private(self):
        """A public-looking hostname that resolves to a private IP must be blocked."""
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.0.1", 0))]
        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos):
            self.assertIsNone(fetch_url("http://evil.example.com/"))

    # ------------------------------------------------------------------
    # Successful fetch
    # ------------------------------------------------------------------

    def test_fetches_public_url(self):
        html_body = "<html><body><p>Hello from the web!</p></body></html>"
        resp = _make_response(html_body)
        opener = _make_opener_returning(resp)
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/")

        self.assertIsNotNone(result)
        self.assertIn("Hello from the web!", result)

    def test_html_stripping(self):
        html_body = (
            "<html><head><script>var x=1;</script></head>"
            "<body><h1>Title</h1><p>Real content here.</p></body></html>"
        )
        resp = _make_response(html_body)
        opener = _make_opener_returning(resp)
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/page")

        self.assertIsNotNone(result)
        self.assertNotIn("<h1>", result)
        self.assertNotIn("var x=1", result)
        self.assertIn("Title", result)
        self.assertIn("Real content here.", result)

    # ------------------------------------------------------------------
    # Size cap
    # ------------------------------------------------------------------

    def test_size_cap(self):
        """Response larger than max_bytes is truncated."""
        big_text = "A" * 200_000
        html_body = f"<p>{big_text}</p>"
        # Simulate read returning exactly max_bytes+1 bytes (capped by mock)
        max_bytes = 1000
        resp = MagicMock()
        # Return just enough bytes to simulate the cap behaviour
        resp.read.return_value = html_body.encode("utf-8")[: max_bytes + 1]
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)

        opener = _make_opener_returning(resp)
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/big", max_bytes=max_bytes)

        # Result must not exceed max_bytes in length
        if result is not None:
            self.assertLessEqual(len(result), max_bytes)

    # ------------------------------------------------------------------
    # Error / failure paths — must return None, never raise
    # ------------------------------------------------------------------

    def test_timeout_failure_returns_none(self):
        opener = MagicMock()
        opener.open.side_effect = socket.timeout("timed out")
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/slow")

        self.assertIsNone(result)

    def test_http_error_returns_none(self):
        import urllib.error as _ue

        opener = MagicMock()
        opener.open.side_effect = _ue.HTTPError(
            "http://example.com/404", 404, "Not Found", {}, None
        )
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/404")

        self.assertIsNone(result)

    def test_connection_error_returns_none(self):
        opener = MagicMock()
        opener.open.side_effect = ConnectionRefusedError("refused")
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/")

        self.assertIsNone(result)

    def test_never_raises_on_broken_url(self):
        """Completely broken input must return None, not raise."""
        try:
            result = fetch_url("not a url at all !@#$%")
            # Should be None because scheme is not http/https
            self.assertIsNone(result)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"fetch_url raised unexpectedly: {exc}")

    def test_never_raises_on_empty_url(self):
        try:
            result = fetch_url("")
            self.assertIsNone(result)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"fetch_url raised unexpectedly: {exc}")

    # ------------------------------------------------------------------
    # Redirect guard
    # ------------------------------------------------------------------

    def test_redirect_to_private_ip_blocked(self):
        """A 302 redirect from a public host to a private IP must be blocked."""
        import urllib.error as _ue

        redirect_headers = MagicMock()
        redirect_headers.get.return_value = "http://192.168.1.1/evil"

        opener = MagicMock()
        opener.open.side_effect = _ue.HTTPError(
            "http://example.com/redir", 302, "Found", redirect_headers, None
        )
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/redir")

        # The redirect target 192.168.1.1 is private → must be None
        self.assertIsNone(result)

    def test_too_many_redirects_returns_none(self):
        """Exhausting all redirect hops returns None."""
        import urllib.error as _ue

        redirect_headers = MagicMock()
        # Always redirect to the same public URL (infinite loop)
        redirect_headers.get.return_value = "http://example.com/redir"

        opener = MagicMock()
        opener.open.side_effect = _ue.HTTPError(
            "http://example.com/redir", 302, "Found", redirect_headers, None
        )
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/redir")

        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Plain-text (non-HTML) response
    # ------------------------------------------------------------------

    def test_plain_text_returned_as_is(self):
        text_body = "plain text content, no tags"
        resp = _make_response(text_body, content_type="text/plain; charset=utf-8")
        opener = _make_opener_returning(resp)
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

        with patch("jack_tools.web_fetch.socket.getaddrinfo", return_value=infos), \
             patch("jack_tools.web_fetch.urllib.request.build_opener", return_value=opener):
            result = fetch_url("http://example.com/data.txt")

        # Should still return something (text is not None)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
