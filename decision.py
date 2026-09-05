"""Transparent weighted ranker for DraftLock Lite."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

from config import IDP_POSITIONS, Policy, SKILL_POSITIONS, canon_position
from rankings import RankedPlayer

NEED_MISSING_BY_TWO = 1.14
NEED_MISSING_BY_ONE = 1.08
NEED_AT_TARGET = 1.00
NEED_ABOVE_TARGET = 0.92
URGENCY_COEFFICIENT = 12.0
SAFE_SURVIVAL_PICKS = 20
MIN_ROOM_SAMPLES = 8


@dataclass
class ScoredPlayer:
    player: RankedPlayer
    base_score: float
    position_score: float
    need_multiplier: float
    wait_risk: float
    urgency_bonus: float
    final_score: float
    reasons: list[str] = field(default_factory=list)
    eligible: bool = True
    forced: bool = False
    take_now: bool = False


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def next_target_checkpoint(round_no: int, policy: Policy) -> dict[str, int]:
    checkpoints = sorted(policy.roster_targets_by_round)
    if not checkpoints:
        return {}
    for checkpoint in checkpoints:
        if round_no <= checkpoint:
            return dict(policy.roster_targets_by_round[checkpoint])
    return dict(policy.roster_targets_by_round[checkpoints[-1]])


def count_positions(players: list[RankedPlayer]) -> dict[str, int]:
    counts = {pos: 0 for pos in ("QB", "RB", "WR", "TE", "IDP", "K", "DEF")}
    for player in players:
        pos = canon_position(player.position)
        if pos in counts:
            counts[pos] += 1
    return counts


def need_multiplier(have: int, target: int) -> float:
    missing = target - have
    if missing >= 2:
        return NEED_MISSING_BY_TWO
    if missing == 1:
        return NEED_MISSING_BY_ONE
    if missing <= -1:
        return NEED_ABOVE_TARGET
    return NEED_AT_TARGET


def market_base_score(player: RankedPlayer, ranked: list[RankedPlayer]) -> float:
    values = [p.value for p in ranked if p.value is not None]
    if player.value is not None and len(values) >= 2:
        low = min(values)
        high = max(values)
        if high == low:
            return 100.0
        return 100.0 * (player.value - low) / (high - low)
    ranks = [p.rank for p in ranked if p.rank is not None]
    rank = player.rank if player.rank is not None else (max(ranks) if ranks else 1)
    total = max(len(ranks) or len(ranked), 1)
    return 100.0 * (1.0 - (rank - 1) / max(total - 1, 1))


def room_delta(picks: list[dict[str, Any]], rank_by_id: dict[str, int]) -> float:
    deltas: list[float] = []
    for pick in picks:
        pid = str(pick.get("player_id") or "")
        rank = rank_by_id.get(pid)
        pick_no = pick.get("pick_no")
        if rank is None or pick_no is None:
            continue
        deltas.append(float(pick_no) - float(rank))
    if len(deltas) < MIN_ROOM_SAMPLES:
        return 0.0
    return float(median(deltas))


def wait_risk_for(
    market_rank: int,
    next_user_pick: int | None,
    current_pick: int,
    room: float,
) -> tuple[float, float]:
    expected = float(market_rank)
    adjusted = expected + room
    if next_user_pick is None:
        return 0.05, adjusted
    risk = clamp(0.50 + (next_user_pick - adjusted) / 20.0, 0.05, 0.95)
    return risk, adjusted


def _age_penalty(age: float | None, per_year: float) -> float:
    if age is None:
        return 0.0
    return max(0.0, age - 24.0) * per_year


def _eligible(
    player: RankedPlayer,
    round_no: int,
    policy: Policy,
    has_uploaded_idp: bool,
    roster: list[RankedPlayer],
    qb_count: int,
) -> tuple[bool, str | None]:
    rules = policy.hard_rules
    pos = canon_position(player.position)
    if pos == "K" and round_no < rules.earliest_k_round:
        return False, None
    if pos == "DEF" and round_no < rules.earliest_def_round:
        return False, None
    if pos == "IDP" and not has_uploaded_idp and round_no < rules.earliest_idp_round_without_uploaded_rankings:
        return False, None
    if pos == "QB" and round_no < 10 and qb_count >= rules.maximum_qbs_before_round_10:
        return False, None
    if pos in SKILL_POSITIONS and player.team:
        same = sum(
            1
            for owned in roster
            if canon_position(owned.position) in SKILL_POSITIONS
            and (owned.team or "").upper() == player.team.upper()
        )
        if same >= rules.avoid_same_nfl_team_skill_stack_over:
            return False, None
    return True, None


def _reasons(
    player: RankedPlayer,
    scored: ScoredPlayer,
    available: list[RankedPlayer],
    policy: Policy,
    have: dict[str, int],
    targets: dict[str, int],
    next_user_pick: int | None,
) -> list[str]:
    reasons: list[str] = []
    pos = canon_position(player.position)
    remaining_values = [p.value for p in available if p.value is not None]
    if player.value is not None and remaining_values and player.value == max(remaining_values):
        reasons.append("Top remaining dynasty value")
    if pos == "QB" and policy.superflex:
        reasons.append("Superflex QB scarcity")
    if pos == "TE" and policy.tep_bonus > 0:
        reasons.append("TE premium raises his value")
    target = targets.get(pos, 0)
    owned = have.get(pos, 0)
    if target > owned:
        ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth"}.get(owned + 1, f"{owned + 1}th")
        label = "starting-QB" if pos == "QB" else pos
        reasons.append(f"Fills your {ordinal} {label} target")
    if scored.wait_risk >= 0.45 and next_user_pick is not None:
        pct = int(round(scored.wait_risk * 100))
        reasons.append(f"{pct}% risk he is gone before pick {next_user_pick}")
    if player.age is not None and player.age <= 24 and policy.age_penalty_per_year_over_24 >= 2.5:
        reasons.append("Younger player fits the long-horizon plan")
    if policy.starter_bonus and player.starter:
        reasons.append("Current-starter profile fits win-now")
    return reasons[:3]


def _tie_key(scored: ScoredPlayer, have: dict[str, int], targets: dict[str, int], policy: Policy) -> tuple:
    player = scored.player
    pos = canon_position(player.position)
    keys: list[Any] = [-scored.final_score]
    for rule in policy.tie_breakers:
        if rule == "younger_player":
            keys.append(player.age if player.age is not None else 99)
        elif rule == "scarcer_position":
            keys.append(have.get(pos, 0) - targets.get(pos, 0))
        elif rule == "higher_market_value":
            keys.append(-(player.value or 0))
    keys.append(player.rank or 10**9)
    keys.append(player.name)
    return tuple(keys)


def recommend(
    available: list[RankedPlayer],
    roster: list[RankedPlayer],
    policy: Policy,
    round_no: int,
    current_pick: int,
    next_user_pick: int | None,
    completed_picks: list[dict[str, Any]] | None = None,
    has_uploaded_idp: bool = False,
    all_ranked: list[RankedPlayer] | None = None,
) -> list[ScoredPlayer]:
    if not available:
        return []
    have = count_positions(roster)
    targets = next_target_checkpoint(round_no, policy)
    rank_source = all_ranked or available
    rank_by_id = {p.sleeper_id: int(p.rank or 0) for p in rank_source if p.sleeper_id}
    for pick in completed_picks or []:
        pid = str(pick.get("player_id") or "")
        meta_rank = pick.get("market_rank")
        if pid and meta_rank is not None:
            rank_by_id[pid] = int(meta_rank)
    room = room_delta(completed_picks or [], rank_by_id)
    qb_count = have.get("QB", 0)
    scored: list[ScoredPlayer] = []
    for player in available:
        pos = canon_position(player.position)
        ok, _ = _eligible(player, round_no, policy, has_uploaded_idp, roster, qb_count)
        if not ok:
            continue
        base = market_base_score(player, available)
        multiplier = policy.position_multipliers.get(pos, 1.0)
        if pos == "TE" and policy.tep_bonus:
            multiplier *= 1.0 + policy.tep_bonus
        position_score = base * multiplier
        position_score -= _age_penalty(player.age, policy.age_penalty_per_year_over_24)
        if policy.starter_bonus and player.starter:
            position_score += policy.starter_bonus
        need = need_multiplier(have.get(pos, 0), targets.get(pos, 0))
        market_rank = int(player.rank or 1)
        risk, adjusted = wait_risk_for(market_rank, next_user_pick, current_pick, room)
        urgency = 0.0
        if next_user_pick is not None and adjusted < next_user_pick + SAFE_SURVIVAL_PICKS:
            urgency = URGENCY_COEFFICIENT * risk
        final = position_score * need + urgency
        row = ScoredPlayer(
            player=player,
            base_score=base,
            position_score=position_score,
            need_multiplier=need,
            wait_risk=risk,
            urgency_bonus=urgency,
            final_score=final,
            take_now=risk >= 0.55,
        )
        row.reasons = _reasons(player, row, available, policy, have, targets, next_user_pick)
        scored.append(row)

    scored.sort(key=lambda row: _tie_key(row, have, targets, policy))

    required_pos = None
    if round_no > 6 and have.get("QB", 0) < policy.hard_rules.minimum_qbs_after_round_6:
        required_pos = "QB"
    if required_pos:
        forced = next((row for row in scored if canon_position(row.player.position) == required_pos), None)
        if forced is not None:
            forced.forced = True
            if "Hard rule: starting QB target is still open" not in forced.reasons:
                forced.reasons = (["Hard rule: starting QB target is still open"] + forced.reasons)[:3]
            scored = [forced] + [row for row in scored if row is not forced]
    return scored
