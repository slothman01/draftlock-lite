from __future__ import annotations

from phone_access import (
    copy_top5_html,
    is_phone_user_agent,
    lan_ipv4,
    mobile_css,
    phone_banner_lines,
    phone_open_url,
    phone_qr_png,
    phone_url,
    prepare_phone_session,
    record_phone_hit,
)


class _FakeSock:
    def __init__(self, ip: str, fail: bool = False) -> None:
        self.ip = ip
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connect(self, _addr):
        if self.fail:
            raise OSError("offline")

    def getsockname(self):
        return (self.ip, 12345)


def test_is_phone_user_agent_detects_iphone_and_android():
    assert is_phone_user_agent(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605"
    )
    assert is_phone_user_agent(
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Mobile Safari/537.36"
    )
    assert not is_phone_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0 Safari/537.36"
    )
    assert not is_phone_user_agent("")
    assert not is_phone_user_agent(None)


def test_phone_url_from_lan_ip(monkeypatch):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("192.168.4.12"))
    assert lan_ipv4() == "192.168.4.12"
    assert phone_url() == "http://192.168.4.12:8501"
    assert phone_url(port=9000) == "http://192.168.4.12:9000"


def test_phone_url_rejects_loopback_and_link_local(monkeypatch):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("127.0.0.1"))
    assert phone_url() is None
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("169.254.10.2"))
    assert phone_url() is None


def test_phone_url_offline(monkeypatch):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("0.0.0.0", fail=True))
    assert phone_url() is None
    lines = phone_banner_lines()
    assert any("Wi-Fi" in line for line in lines)


def test_phone_banner_includes_url_and_no_submit_promise(monkeypatch):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("10.0.0.8"))
    lines = phone_banner_lines()
    assert "http://10.0.0.8:8501" in lines[0]
    assert any("never submits picks" in line for line in lines)


def test_phone_open_url_prefers_https_tunnel(monkeypatch, tmp_path):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("192.168.0.107"))
    tunnel = tmp_path / "phone-tunnel.txt"
    tunnel.write_text("https://draft.example.trycloudflare.com\n", encoding="utf-8")
    monkeypatch.setattr("phone_access.TUNNEL_PATH", tunnel)
    assert phone_open_url("Slothman01") == "https://draft.example.trycloudflare.com/?u=Slothman01"


def test_phone_open_url_adds_username_query(monkeypatch, tmp_path):
    monkeypatch.setattr("phone_access.socket.socket", lambda *a, **k: _FakeSock("192.168.0.107"))
    monkeypatch.setattr("phone_access.TUNNEL_PATH", tmp_path / "missing-tunnel.txt")
    assert phone_open_url() == "http://192.168.0.107:8501"
    assert phone_open_url("Slothman01") == "http://192.168.0.107:8501/?u=Slothman01"
    png = phone_qr_png("http://192.168.0.107:8501/?u=Slothman01")
    assert png.startswith(b"\x89PNG")
    assert len(png) > 80


def test_record_phone_hit_writes_android_only(monkeypatch, tmp_path):
    hit = tmp_path / "phone-hits.log"
    monkeypatch.setattr("phone_access.HIT_PATH", hit)

    record_phone_hit("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0")
    assert not hit.exists()
    record_phone_hit(
        "Mozilla/5.0 (Linux; Android 16; Pixel 10 Pro XL) AppleWebKit/537.36 Mobile Safari/537.36"
    )
    text = hit.read_text(encoding="utf-8")
    assert "Android 16" in text
    assert "Pixel 10 Pro XL" in text


def test_copy_top5_html_has_http_fallback_and_selectable_names():
    html_blob = copy_top5_html(["Ja'Marr Chase", "Bijan Robinson"])
    assert "copy-src" in html_blob
    assert "Copy top 5" in html_blob
    assert "execCommand" in html_blob
    assert "Ja&#x27;Marr Chase" in html_blob or "Ja&#39;Marr Chase" in html_blob
    assert "Bijan Robinson" in html_blob
    assert "onclick" not in html_blob


def test_mobile_css_stacks_columns_on_narrow_screens():
    css = mobile_css()
    assert "@media (max-width: 700px)" in css
    assert "flex-direction: column" in css
    assert "font-size: 16px" in css


def test_prepare_phone_session_locks_once_on_phone():
    state: dict = {}
    prepare_phone_session(True, state)
    assert state["strategy_locked"] is True
    assert state["phone_board_ready"] is True
    state["strategy_locked"] = False
    prepare_phone_session(True, state)
    assert state["strategy_locked"] is False


def test_prepare_phone_session_skips_desktop():
    state: dict = {}
    prepare_phone_session(False, state)
    assert state == {}
