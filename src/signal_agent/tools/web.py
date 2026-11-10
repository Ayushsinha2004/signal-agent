"""Research tools with hard guards: timeouts, size caps, scheme allowlist, robots.txt, page budget."""
from __future__ import annotations

import ipaddress
import socket
import urllib.robotparser
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

UA = "signal-agent/0.1 (+https://github.com/Ayushsinha2004/signal-agent)"
MAX_BYTES = 200_000
MAX_CHARS = 8_000


class FetchError(Exception):
    pass


def _is_public_host(host: str) -> bool:
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except (socket.gaierror, ValueError):
        return False


@lru_cache(maxsize=256)
def _robots(base: str) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = httpx.get(urljoin(base, "/robots.txt"), timeout=5, headers={"User-Agent": UA})
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])
    return rp


def html_to_text(html: str) -> str:
    tree = HTMLParser(html)
    for sel in ("script", "style", "noscript", "svg", "nav", "footer"):
        for n in tree.css(sel):
            n.decompose()
    title = tree.css_first("title")
    body = tree.body.text(separator="\n") if tree.body else ""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return ((title.text().strip() + "\n") if title else "") + "\n".join(lines)


def fetch_url(url: str) -> str:
    p = urlparse(url if "://" in url else f"https://{url}")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("only http(s) URLs")
    if not _is_public_host(p.hostname):
        raise FetchError("host is not public")
    base = f"{p.scheme}://{p.hostname}"
    if not _robots(base).can_fetch(UA, p.geturl()):
        raise FetchError("disallowed by robots.txt")
    with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": UA}) as c, \
            c.stream("GET", p.geturl()) as r:
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code}")
        if "html" not in r.headers.get("content-type", "") and "text" not in r.headers.get("content-type", ""):
            raise FetchError(f"unsupported content-type {r.headers.get('content-type')}")
        buf = b""
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > MAX_BYTES:
                break
    return html_to_text(buf.decode("utf-8", "ignore"))[:MAX_CHARS]
