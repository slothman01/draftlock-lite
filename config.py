"""League assumptions, locked-strategy schema, and strategy variants."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "policy.yaml"
CACHE_DIR = ROOT / "data" / "cache"
SAMPLE_RANKINGS_PATH = ROOT / "data" / "sample-rankings.csv"

KNOWN_LEAGUE_ID = "1389407386388664320"
KNOWN_DRAFT_ID = "1389407387676315648"
KNOWN_LEAGUE_NAME = "Waiver Wire Witnesses"
KNOWN_USERNAME = "Slothman01"


def resolved_sleeper_username(query: str | None = None, current: str | None = None) -> str:
    queried = str(query or "").strip()
    if queried:
        return queried
    existing = str(current or "").strip()
    return existing or KNOWN_USERNAME
KNOWN_TEAMS = 12
KNOWN_ROUNDS = 29
KNOWN_PICK_TIMER = 120
KNOWN_DIVISIONS = 2
KNOWN_ROSTER_POSITIONS = [
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "FLEX",
    "SUPER_FLEX",
    "K",
    "DEF",
    "IDP_FLEX",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
]

STRATEGY_BALANCED = "Balanced dynasty"
STRATEGY_WIN_NOW = "Win now"
STRATEGY_LONG = "Long horizon"
STRATEGY_OPTIONS = (STRATEGY_BALANCED, STRATEGY_WIN_NOW, STRATEGY_LONG)

SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})
IDP_POSITIONS = frozenset({"IDP", "DL", "LB", "DB", "IDP_FLEX"})
CANON_POSITIONS = ("QB", "RB", "WR", "TE", "IDP", "K", "DEF")


class HardRules(BaseModel):
    minimum_qbs_after_round_6: int = 2
    maximum_qbs_before_round_10: int = 3
    earliest_k_round: int = 25
    earliest_def_round: int = 25
    earliest_idp_round_without_uploaded_rankings: int = 20
    avoid_same_nfl_team_skill_stack_over: int = 3


class Policy(BaseModel):
    name: str
    league_type: str = "dynasty"
    superflex: bool = True
    tep_bonus: float = 0.5
    position_multipliers: dict[str, float]
    roster_targets_by_round: dict[int, dict[str, int]]
    hard_rules: HardRules
    tie_breakers: list[str]
    age_penalty_per_year_over_24: float = 1.8
    starter_bonus: float = 0.0
    display_name: str = STRATEGY_BALANCED

    @field_validator("roster_targets_by_round", mode="before")
    @classmethod
    def _int_round_keys(cls, value: Any) -> dict[int, dict[str, int]]:
        if not isinstance(value, dict):
            raise TypeError("roster_targets_by_round must be a mapping")
        return {int(round_no): dict(targets) for round_no, targets in value.items()}


def load_base_policy(path: Path | None = None) -> Policy:
    raw = yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8"))
    return Policy.model_validate(raw)


def policy_for_strategy(strategy: str, base: Policy | None = None) -> Policy:
    policy = (base or load_base_policy()).model_copy(deep=True)
    strategy = strategy.strip()
    if strategy == STRATEGY_WIN_NOW:
        policy.name = "win_now"
        policy.display_name = STRATEGY_WIN_NOW
        policy.position_multipliers["RB"] = 1.06
        policy.age_penalty_per_year_over_24 = 0.6
        policy.starter_bonus = 4.0
    elif strategy == STRATEGY_LONG:
        policy.name = "long_horizon"
        policy.display_name = STRATEGY_LONG
        policy.position_multipliers["WR"] = 1.10
        policy.position_multipliers["RB"] = 0.92
        policy.age_penalty_per_year_over_24 = 3.2
        policy.starter_bonus = 0.0
    else:
        policy.name = "balanced_dynasty"
        policy.display_name = STRATEGY_BALANCED
        policy.age_penalty_per_year_over_24 = 1.8
        policy.starter_bonus = 0.0
    return policy


def canon_position(raw: str | None) -> str:
    pos = (raw or "").strip().upper()
    if pos in IDP_POSITIONS:
        return "IDP"
    if pos in {"DST", "D/ST"}:
        return "DEF"
    if pos in CANON_POSITIONS:
        return pos
    if pos in SKILL_POSITIONS:
        return pos
    return pos or "UNK"


def expected_league_warnings(league: dict[str, Any], draft: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    name = str(league.get("name") or "")
    if name and name != KNOWN_LEAGUE_NAME:
        warnings.append(f"League name is {name!r}, expected {KNOWN_LEAGUE_NAME!r}.")
    league_id = str(league.get("league_id") or "")
    if league_id and league_id != KNOWN_LEAGUE_ID:
        warnings.append(f"League ID is {league_id}, expected {KNOWN_LEAGUE_ID}.")
    draft_id = str(league.get("draft_id") or draft.get("draft_id") or "")
    if draft_id and draft_id != KNOWN_DRAFT_ID:
        warnings.append(f"Draft ID is {draft_id}, expected {KNOWN_DRAFT_ID}.")
    settings = league.get("settings") or {}
    if int(settings.get("num_teams") or 0) not in {0, KNOWN_TEAMS}:
        warnings.append(f"Team count is {settings.get('num_teams')}, expected {KNOWN_TEAMS}.")
    if int(settings.get("divisions") or 0) not in {0, KNOWN_DIVISIONS}:
        warnings.append(f"Division count is {settings.get('divisions')}, expected {KNOWN_DIVISIONS}.")
    draft_settings = draft.get("settings") or {}
    if int(draft_settings.get("rounds") or 0) not in {0, KNOWN_ROUNDS}:
        warnings.append(f"Draft rounds are {draft_settings.get('rounds')}, expected {KNOWN_ROUNDS}.")
    if int(draft_settings.get("pick_timer") or 0) not in {0, KNOWN_PICK_TIMER}:
        warnings.append(
            f"Pick timer is {draft_settings.get('pick_timer')}s, expected {KNOWN_PICK_TIMER}s."
        )
    roster_positions = list(league.get("roster_positions") or [])
    if roster_positions and roster_positions != KNOWN_ROSTER_POSITIONS:
        warnings.append("Roster slots differ from the known Waiver Wire Witnesses lineup.")
    scoring = league.get("scoring_settings") or {}
    if scoring:
        if float(scoring.get("bonus_rec_te") or 0) < 0.4:
            warnings.append("TE premium scoring looks weaker than the expected 1.5 PPR TE setting.")
        if float(scoring.get("pass_td") or 0) not in {0.0, 4.0}:
            warnings.append("Passing TD scoring differs from the expected 4-point setting.")
        if float(scoring.get("rec_fd") or scoring.get("rush_fd") or 0) == 0:
            warnings.append("First-down scoring is missing; expected 0.25 PPR first downs.")
    return warnings


def ensure_cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def deepcopy_policy(policy: Policy) -> Policy:
    return Policy.model_validate(deepcopy(policy.model_dump()))
