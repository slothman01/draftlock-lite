"""LAN phone access helpers for DraftLock Lite."""

from __future__ import annotations

import html
import io
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import segno

DEFAULT_PORT = 8501
PHONE_UA_MARKERS = ("iphone", "ipod", "ipad", "android", "mobile")
ROOT = Path(__file__).resolve().parent
TUNNEL_PATH = ROOT / "data" / "cache" / "phone-tunnel.txt"
HIT_PATH = ROOT / "data" / "cache" / "phone-hits.log"


def is_phone_user_agent(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return any(marker in ua for marker in PHONE_UA_MARKERS)


def record_phone_hit(user_agent: str | None) -> None:
    if not is_phone_user_agent(user_agent):
        return
    HIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ua = (user_agent or "").replace("\n", " ")[:300]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with HIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {ua}\n")


def prepare_phone_session(is_phone: bool, state: dict) -> None:
    """On first phone load, freeze the default strategy so the board is above the fold."""
    if not is_phone or state.get("phone_board_ready"):
        return
    state["strategy_locked"] = True
    state["phone_board_ready"] = True


def lan_ipv4() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 1))
            ip = str(sock.getsockname()[0])
        except OSError:
            return None
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return None
    return ip


def phone_url(ip: str | None = None, port: int = DEFAULT_PORT) -> str | None:
    address = lan_ipv4() if ip is None else ip
    if not address:
        return None
    return f"http://{address}:{port}"


def phone_tunnel_base() -> str | None:
    try:
        text = TUNNEL_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("https://") and " " not in text:
        return text.rstrip("/")
    return None


def write_phone_tunnel(url: str) -> None:
    TUNNEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_PATH.write_text(url.strip().rstrip("/") + "\n", encoding="utf-8")


def phone_open_url(username: str = "", ip: str | None = None, port: int = DEFAULT_PORT) -> str | None:
    base = phone_tunnel_base() or phone_url(ip=ip, port=port)
    if not base:
        return None
    handle = username.strip()
    if not handle:
        return base
    return f"{base}/?u={quote(handle)}"


def phone_qr_png(url: str, scale: int = 5) -> bytes:
    buffer = io.BytesIO()
    segno.make(url, error="m").save(buffer, kind="png", scale=scale, border=1)
    return buffer.getvalue()


def phone_banner_lines(port: int = DEFAULT_PORT) -> list[str]:
    url = phone_url(port=port)
    if not url:
        return [
            "Could not find a Wi-Fi address for this PC.",
            "Connect the PC and your phone to the same Wi-Fi, then restart.",
        ]
    return [
        f"On your phone (same Wi-Fi): {url}",
        "This dashboard never submits picks. Enter your Sleeper username on the phone, then lock strategy.",
    ]


def print_launch_help(port: int = DEFAULT_PORT) -> None:
    for line in phone_banner_lines(port):
        print(line)


def mobile_css() -> str:
    return """
    .stApp { max-width: 1400px; }
    @media (max-width: 700px) {
        h1 { font-size: 1.4rem; }
        .block-container { padding: 1rem 0.7rem 2.5rem !important; }
        [data-testid="stHorizontalBlock"] {
            flex-direction: column !important;
            gap: 0.35rem !important;
        }
        [data-testid="stHorizontalBlock"] > div {
            width: 100% !important;
            min-width: 100% !important;
            flex: 1 1 100% !important;
        }
        input, textarea, select { font-size: 16px !important; }
        button { min-height: 44px; }
    }
    """


def copy_top5_html(names: list[str]) -> str:
    payload = json.dumps("\n".join(names))
    listed = html.escape("\n".join(names))
    return f"""
        <textarea id="copy-src" readonly
            style="width:100%;height:5.5rem;font-size:16px;padding:8px;box-sizing:border-box;">{listed}</textarea>
        <button id="copy-top-5" type="button"
            style="margin-top:6px;padding:10px 16px;font-size:16px;min-height:44px;cursor:pointer;width:100%;">Copy top 5</button>
        <script>
        (function() {{
          const btn = document.getElementById("copy-top-5");
          const src = document.getElementById("copy-src");
          const text = {payload};
          function ok() {{
            btn.textContent = "Copied";
            setTimeout(function() {{ btn.textContent = "Copy top 5"; }}, 1200);
          }}
          function fail() {{
            src.focus();
            src.select();
            btn.textContent = "Select names, then copy";
            setTimeout(function() {{ btn.textContent = "Copy top 5"; }}, 2000);
          }}
          function tryLegacy() {{
            src.focus();
            src.select();
            try {{
              if (document.execCommand("copy")) {{ ok(); }} else {{ fail(); }}
            }} catch (err) {{ fail(); }}
          }}
          btn.addEventListener("click", function() {{
            if (navigator.clipboard && navigator.clipboard.writeText) {{
              navigator.clipboard.writeText(text).then(ok).catch(tryLegacy);
              return;
            }}
            tryLegacy();
          }});
        }})();
        </script>
        """
