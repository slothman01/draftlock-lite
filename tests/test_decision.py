from __future__ import annotations

from config import load_base_policy, policy_for_strategy
from decision import need_multiplier, recommend
from rankings import RankedPlayer, parse_fantasycalc_payload
from tests.conftest import load_fixture


def _player(**kwargs) -> RankedPlayer:
    defaults = dict(name="Player", position="WR", team="KC", value=1000.0, rank=10, sleeper_id="x", age=24.0)
    defaults.update(kwargs)
    return RankedPlayer(**defaults)


def test_two_qb_target_applies_need_bonus():
    policy = load_base_policy()
    empty = need_multiplier(0, 2)
    one = need_multiplier(1, 2)
    met = need_multiplier(2, 2)
    extra = need_multiplier(3, 2)
    assert empty == 1.14
    assert one == 1.08
    assert met == 1.00
    assert extra == 0.92

    qb = _player(name="Mid QB", position="QB", sleeper_id="qb1", value=4000, rank=15, team="NE")
    wr = _player(name="Mid WR", position="WR", sleeper_id="wr1", value=4000, rank=16, team="DAL")
    scored = recommend(
        available=[qb, wr],
        roster=[],
        policy=policy,
        round_no=1,
        current_pick=1,
        next_user_pick=12,
    )
    by_name = {row.player.name: row for row in scored}
    assert by_name["Mid QB"].need_multiplier == 1.14
    assert by_name["Mid WR"].need_multiplier == 1.14
    loaded = recommend(
        available=[qb, wr],
        roster=[_player(name="QB1", position="QB", sleeper_id="qb0", team="BUF")],
        policy=policy,
        round_no=1,
        current_pick=1,
        next_user_pick=12,
    )
    assert {row.player.name: row.need_multiplier for row in loaded}["Mid QB"] == 1.08


def test_tep_multiplier_changes_te_ordering():
    wr = _player(name="WR Twin", position="WR", sleeper_id="wr", value=5000, rank=10, team="CIN")
    te = _player(name="TE Twin", position="TE", sleeper_id="te", value=5000, rank=11, team="ARI")
    with_tep = load_base_policy()
    no_tep = load_base_policy()
    no_tep.tep_bonus = 0.0
    no_tep.position_multipliers["TE"] = no_tep.position_multipliers["WR"]

    scored_tep = recommend([wr, te], [], with_tep, 1, 1, 12)
    scored_flat = recommend([wr, te], [], no_tep, 1, 1, 12)
    assert scored_tep[0].player.name == "TE Twin"
    assert scored_flat[0].player.name == "WR Twin" or scored_flat[0].player.position == "WR"


def test_k_and_def_cannot_appear_early():
    policy = load_base_policy()
    wr = _player(name="Normal WR", position="WR", sleeper_id="wr", value=1000, rank=20)
    k = _player(name="Star K", position="K", sleeper_id="k", value=9000, rank=1, team="BAL")
    defense = _player(name="Star DEF", position="DEF", sleeper_id="def", value=9000, rank=2, team="SF")
    early = recommend([wr, k, defense], [], policy, round_no=8, current_pick=90, next_user_pick=96)
    names = [row.player.name for row in early]
    assert "Star K" not in names
    assert "Star DEF" not in names
    late = recommend([wr, k, defense], [], policy, round_no=25, current_pick=289, next_user_pick=300)
    late_names = [row.player.name for row in late]
    assert "Star K" in late_names
    assert "Star DEF" in late_names


def test_wait_risk_breaks_close_tie_but_not_elite_vs_low():
    policy = load_base_policy()
    elite = _player(name="Elite WR", position="WR", sleeper_id="e", value=10000, rank=1, team="CIN")
    low = _player(name="Replacement WR", position="WR", sleeper_id="l", value=1000, rank=2, team="NYG")
    close_a = _player(name="Close A", position="WR", sleeper_id="a", value=5000, rank=8, team="SEA")
    close_b = _player(name="Close B", position="WR", sleeper_id="b", value=5010, rank=50, team="LAR")
    filler = _player(name="Filler", position="WR", sleeper_id="f", value=2000, rank=40, team="CHI")

    mismatch = recommend(
        available=[elite, low, filler],
        roster=[],
        policy=policy,
        round_no=1,
        current_pick=1,
        next_user_pick=24,
    )
    assert mismatch[0].player.name == "Elite WR"

    close = recommend(
        available=[close_a, close_b, filler],
        roster=[],
        policy=policy,
        round_no=1,
        current_pick=1,
        next_user_pick=24,
    )
    by_name = {row.player.name: row for row in close}
    assert by_name["Close A"].wait_risk > by_name["Close B"].wait_risk
    assert by_name["Close A"].urgency_bonus > by_name["Close B"].urgency_bonus
    assert close[0].player.name == "Close A"


def test_fixture_ranking_round_one_has_skill_players():
    policy = policy_for_strategy("Balanced dynasty")
    payload = load_fixture("ranking_response.json")
    ranking = parse_fantasycalc_payload(payload, fetched_at=1.0)
    available = list(ranking.players)
    scored = recommend(available, [], policy, 1, 1, 11)
    assert scored
    assert scored[0].player.position in {"QB", "RB", "WR", "TE"}
    assert all(row.player.position not in {"K", "DEF"} for row in scored)
