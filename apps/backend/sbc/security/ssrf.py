"""SSRF-Schutz für URL-Importe (Feeds, Online-Quellen, Bild-Downloads).

Erlaubt nur http(s) auf öffentliche Adressen. Private, Loopback-, Link-Local-, Multicast-
und Railway-interne Ziele (*.railway.internal) werden abgelehnt. Die Auflösung erfolgt vor
dem Request; Redirects müssen erneut geprüft werden (``safe_get`` folgt ihnen manuell).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

BLOCKED_SUFFIXES = (".railway.internal", ".internal", ".local", ".localhost")
MAX_REDIRECTS = 3


class UnsafeURL(ValueError):
    pass


def check_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        raise UnsafeURL("Nur http/https erlaubt")
    host = (p.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURL("Interner Host nicht erlaubt")
    if p.username or p.password:
        raise UnsafeURL("Zugangsdaten in URL nicht erlaubt")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeURL(f"Host nicht auflösbar: {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise UnsafeURL(f"Ziel-IP {ip} ist nicht öffentlich")


def safe_get(url: str, *, max_bytes: int = 50 * 1024 * 1024, timeout: float = 30,
             headers: dict | None = None) -> bytes:
    with httpx.Client(follow_redirects=False, timeout=timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url)
            with client.stream("GET", url, headers=headers) as resp:
                if resp.is_redirect:
                    url = str(resp.next_request.url) if resp.next_request else ""
                    continue
                resp.raise_for_status()
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise UnsafeURL("Antwort überschreitet Größenlimit")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise UnsafeURL("Zu viele Weiterleitungen")
