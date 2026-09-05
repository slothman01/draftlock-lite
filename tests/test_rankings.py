from __future__ import annotations

from pathlib import Path

from rankings import (
    available_ranked_players,
    match_rankings_to_sleeper,
    parse_csv_rankings,
    parse_fantasycalc_payload,
)
from tests.conftest import load_fixture


def test_parse_ranking_fixture_without_http():
    payload = load_fixture("ranking_response.json")
    ranking = parse_fantasycalc_payload(payload, fetched_at=1.0)
    assert ranking.players[0].sleeper_id == "4984"
    assert ranking.players[0].name == "Josh Allen"
    assert ranking.has_idp is False


def test_drafted_ids_removed_from_available_pool():
    payload = load_fixture("ranking_response.json")
    ranking = parse_fantasycalc_payload(payload, fetched_at=1.0)
    picks = load_fixture("picks_partial.json")
    drafted = [row["player_id"] for row in picks]
    available = available_ranked_players(ranking, drafted)
    available_ids = [p.sleeper_id for p in available]
    assert "4984" not in available_ids
    assert "6794" in available_ids
    assert len(available_ids) == len(set(available_ids))
    assert len(available) == len(ranking.players) - len(set(drafted) & {p.sleeper_id for p in ranking.players})


def test_csv_fallback_columns(tmp_path: Path):
    csv_path = tmp_path / "ranks.csv"
    csv_path.write_text(
        "player,position,team,rank\nJa'Marr Chase,WR,CIN,1\nBrock Bowers,TE,LV,2\n",
        encoding="utf-8",
    )
    ranking = parse_csv_rankings(csv_path, fetched_at=1.0)
    assert ranking.from_upload is True
    assert ranking.players[0].name == "Ja'Marr Chase"
    assert ranking.players[0].value is None
    assert ranking.players[0].rank == 1


def test_fuzzy_matching_rejects_ambiguous_names():
    players = load_fixture("sleeper_players.json")
    payload = [
        {
            "player": {"name": "J Smith", "position": "WR", "maybeTeam": "NYG"},
            "value": 100,
            "overallRank": 1,
        },
        {
            "player": {"name": "Ja'Marr Chase", "sleeperId": "7564", "position": "WR", "maybeTeam": "CIN"},
            "value": 200,
            "overallRank": 2,
        },
    ]
    ranking = parse_fantasycalc_payload(payload, fetched_at=1.0)
    matched = match_rankings_to_sleeper(ranking, players)
    names = {p.name for p in matched.players}
    assert "Ja'Marr Chase" in names
    assert matched.players[0].sleeper_id == "7564" or any(p.sleeper_id == "7564" for p in matched.players)
    assert any("J Smith" in item or "ambiguous" in item for item in matched.unresolved)
    assert not any(p.name == "J Smith" for p in matched.players)
