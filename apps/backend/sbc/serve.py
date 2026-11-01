"""Startet die API auf IPv4 und IPv6 gleichzeitig (Dual-Stack).

``uvicorn --host ::`` lauscht unter asyncio nur auf IPv6 (IPV6_V6ONLY=1). Railway prüft den
Healthcheck aber per IPv4, während Private Networking IPv6 nutzt. Deshalb wird hier ein
Dual-Stack-Socket vorbereitet und an uvicorn übergeben.
"""

from __future__ import annotations

import os
import socket

import uvicorn


def dual_stack_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("::", port))
    return sock


def main() -> None:
    # Admin/Org/Gebührenprofile sicherstellen (idempotent). Läuft zusätzlich zum Pre-Deploy,
    # weil dessen Ausgabe in den Railway-Logs nicht sichtbar ist.
    try:
        from sbc.cli import bootstrap

        bootstrap()
    except Exception as e:  # Start der API nicht verhindern
        print(f"FEHLER beim Bootstrap: {e.__class__.__name__}: {e}", flush=True)
    port = int(os.environ.get("PORT", "8000"))
    sock = dual_stack_socket(port)
    config = uvicorn.Config("sbc.api.main:app", proxy_headers=True, forwarded_allow_ips="*",
                            log_level=os.environ.get("LOG_LEVEL", "info").lower())
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
