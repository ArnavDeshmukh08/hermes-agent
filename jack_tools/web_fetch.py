"""web_fetch — safe, stdlib-only URL fetch for Jack tools.

GET-only, no auth headers. Hard SSRF guard: we refuse loopback, private,
link-local, and cloud-metadata addresses both by hostname literal AND by
resolving the host and inspecting every returned IP (blocks DNS rebinding).
Body is size-capped and HTML is stripped to plain text. Returns None on any
error; never raises. Blocked attempts are logged.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_logger = logging.getLogger("jack_tools.web_fetch")

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_REDIRECTS = 3
_USER_AGENT = "JackBot/1.0 (+personal-assistant)"
# Hostnames we refuse outright, before resolution.
_BLOCKED_HOST_LITERALS = frozenset({"localhost", "0.0.0.0", "::1", "::"})

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_WS_RE = re.compile(r"\s+")


def _ip_is_blocked(ip_str: str) -> bool:
    """Return True if *ip_str* is a blocked IP address (private/loopback/link-local/etc).

    Returns False (not blocked) if *ip_str* is not a valid IP address literal —
    callers use this for resolved IPs where a ValueError means a bad sockaddr,
    which we treat as safe to skip (the gaierror path handles DNS failure).

    Covers 127/8, 10/8, 192.168/16, 172.16-31/12, 169.254/16 (metadata at
    169.254.169.254), ::1, fc00::/7, fe80::/10, etc.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Not a valid IP literal — not our job to block it here.
        # _host_is_blocked handles the hostname-not-IP case via DNS resolution.
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host_is_blocked(host: str) -> bool:
    """Block by literal name, by literal IP, and by resolving every A/AAAA."""
    if not host:
        return True
    h = host.strip().lower().strip("[]")  # strip IPv6 brackets
    if h in _BLOCKED_HOST_LITERALS:
        return True
    # Literal IP in the URL? (only fires when host IS a valid IP string)
    if _ip_is_blocked(h):
        return True
    # Resolve and inspect EVERY address (blocks DNS rebinding to private).
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        return True  # can't resolve → unsafe
    for info in infos:
        sockaddr = info[4]
        if _ip_is_blocked(sockaddr[0]):
            return True
    return False


def _strip_html(body: str) -> str:
    """Strip script/style blocks, all HTML tags, decode entities, collapse whitespace."""
    body = _SCRIPT_STYLE_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
    body = html.unescape(body)
    return _WS_RE.sub(" ", body).strip()


def fetch_url(
    url: str,
    max_bytes: int = 50_000,
    timeout: float = 8.0,
) -> Optional[str]:
    """Fetch *url* (GET) and return stripped text, or None on any error/block.

    Security constraints (hard, non-negotiable):
    - GET only — no POST, PUT, PATCH, or forms
    - SSRF guard: block all private/localhost/link-local IPs before any request,
      checked by literal hostname AND by resolving every DNS A/AAAA record
    - No auth headers, no cookies, no login
    - *timeout* second timeout, *max_bytes* response size cap
    - Extracts text only (strips HTML)
    - Each redirect hop is re-validated against the SSRF guard

    Returns None (never raises) on any error.
    """
    try:
        parsed = urllib.parse.urlparse(url or "")
    except Exception:  # noqa: BLE001
        return None

    if parsed.scheme not in _ALLOWED_SCHEMES:
        _logger.warning("web_fetch blocked scheme: %r", url)
        return None

    if _host_is_blocked(parsed.hostname or ""):
        _logger.warning(
            "web_fetch BLOCKED (SSRF guard) host=%r url=%r", parsed.hostname, url
        )
        return None

    # Manual redirect handling so each hop is re-validated against the SSRF guard.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        p = urllib.parse.urlparse(current)
        if p.scheme not in _ALLOWED_SCHEMES or _host_is_blocked(p.hostname or ""):
            _logger.warning("web_fetch BLOCKED (SSRF guard on redirect) host=%r", p.hostname)
            return None
        req = urllib.request.Request(
            current, method="GET", headers={"User-Agent": _USER_AGENT}
        )
        try:
            resp = opener.open(req, timeout=timeout)  # noqa: S310 — scheme guarded above
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    return None
                current = urllib.parse.urljoin(current, loc)
                continue
            _logger.warning("web_fetch HTTP error %s for %r", e.code, current)
            return None
        except Exception as e:  # noqa: BLE001
            _logger.warning("web_fetch failed for %r: %r", current, e)
            return None

        with resp:
            raw = resp.read(max_bytes + 1)
        body = raw[:max_bytes].decode("utf-8", errors="replace")
        return _strip_html(body) or None

    _logger.warning("web_fetch too many redirects: %r", url)
    return None
