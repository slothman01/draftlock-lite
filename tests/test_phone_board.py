from __future__ import annotations

from pathlib import Path


def test_static_phone_board_runs_without_this_pc():
    html = (Path(__file__).resolve().parents[1] / "docs" / "index.html").read_text(encoding="utf-8")
    assert "never submits picks" in html.lower() or "Never submits picks" in html
    assert "Add to Home screen" in html
    assert html.index("Your roster") < html.index('id="copy-src"')
    assert html.index("Queue (top 5)") < html.index("Your roster")
    assert "1389407386388664320" in html
    assert "1389407387676315648" in html
    assert "Slothman01" in html
    assert "wait risk" in html.lower()
    assert "Copy top 5" in html
    assert "Your roster" in html
    assert "api.sleeper.app" in html
    assert "api.fantasycalc.com" in html
    assert "localhost" not in html
    assert "192.168." not in html
    assert "8501" not in html
