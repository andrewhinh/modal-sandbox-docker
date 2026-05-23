from __future__ import annotations

import modal


def get_tunnel_urls(sb: modal.Sandbox, ports: list[int]) -> dict[str, str]:
    tunnels = sb.tunnels(timeout=90)
    urls: dict[str, str] = {}
    for port in ports:
        tunnel = tunnels.get(port)
        if tunnel is not None:
            urls[str(port)] = tunnel.url
    return urls

