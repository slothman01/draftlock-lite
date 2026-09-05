"""Read-only Sleeper client, snake-order math, and draft-state rebuild."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from config import KNOWN_DRAFT_ID, KNOWN_LEAGUE_ID, ensure_cache_dir

SLEEPER_BASE = "https://api.sleeper.app/v1"
PLAYERS_CACHE_TTL_S = 24 * 60 * 60
DEFAULT_TIMEOUT_S = 12


class SleeperError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class SleeperClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        cache_dir: Path | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.session = session or requests.Session()
        self.cache_dir = cache_dir or ensure_cache_dir()
        self.timeout_s = timeout_s

    def get_json(self, path: str) -> Any:
        url = path if path.startswith("http") else f"{SLEEPER_BASE}{path}"
        response = self.session.get(url, timeout=self.timeout_s)
        response.raise_for_status()
        return response.json()

    def league(self, league_id: str = KNOWN_LEAGUE_ID) -> dict[str, Any]:
        return self.get_json(f"/league/{league_id}")

    def users(self, league_id: str = KNOWN_LEAGUE_ID) -> list[dict[str, Any]]:
        return self.get_json(f"/league/{league_id}/users")

    def rosters(self, league_id: str = KNOWN_LEAGUE_ID) -> list[dict[str, Any]]:
        return self.get_json(f"/league/{league_id}/rosters")

    def draft(self, draft_id: str = KNOWN_DRAFT_ID) -> dict[str, Any]:
        return self.get_json(f"/draft/{draft_id}")

    def picks(self, draft_id: str = KNOWN_DRAFT_ID) -> list[dict[str, Any]]:
        return self.get_json(f"/draft/{draft_id}/picks")

    def traded_picks(self, draft_id: str = KNOWN_DRAFT_ID) -> list[dict[str, Any]]:
        try:
            payload = self.get_json(f"/draft/{draft_id}/traded_picks")
        except requests.RequestException:
            return []
        return payload if isinstance(payload, list) else []

    def user_by_username(self, username: str) -> dict[str, Any] | None:
        handle = username.strip()
        if not handle:
            return None
        try:
            payload = self.get_json(f"/user/{handle}")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise
        return payload if isinstance(payload, dict) else None

    def nfl_players(self, now: float | None = None) -> dict[str, Any]:
        cache_path = self.cache_dir / "nfl_players.json"
        meta_path = self.cache_dir / "nfl_players.meta.json"
        now = _now() if now is None else now
        meta = _read_json(meta_path) or {}
        cached = _read_json(cache_path)
        fetched_at = float(meta.get("fetched_at") or 0)
        if isinstance(cached, dict) and cached and now - fetched_at < PLAYERS_CACHE_TTL_S:
            return cached
        payload = self.get_json("/players/nfl")
        if not isinstance(payload, dict):
            raise SleeperError("Unexpected NFL players payload")
        _write_json(cache_path, payload)
        _write_json(meta_path, {"fetched_at": now})
        return payload


def snake_pick_number(round_no: int, slot: int, teams: int) -> int:
    if round_no < 1 or slot < 1 or slot > teams:
        raise ValueError("round and slot must be in range")
    if round_no % 2 == 1:
        return (round_no - 1) * teams + slot
    return round_no * teams - slot + 1


def snake_slot_for_pick(pick_no: int, teams: int) -> tuple[int, int]:
    if pick_no < 1 or teams < 1:
        raise ValueError("pick and teams must be positive")
    round_no = (pick_no - 1) // teams + 1
    offset = (pick_no - 1) % teams
    slot = offset + 1 if round_no % 2 == 1 else teams - offset
    return round_no, slot


def all_picks_for_slot(slot: int, teams: int, rounds: int) -> list[int]:
    return [snake_pick_number(round_no, slot, teams) for round_no in range(1, rounds + 1)]


def _int_map(raw: Any) -> dict[int, int]:
    out: dict[int, int] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        try:
            out[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _user_id_to_slot(draft: dict[str, Any]) -> dict[str, int]:
    raw = draft.get("draft_order") or {}
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for user_id, slot in raw.items():
        try:
            out[str(user_id)] = int(slot)
        except (TypeError, ValueError):
            continue
    return out


def original_roster_for_pick(pick_no: int, draft: dict[str, Any]) -> int | None:
    settings = draft.get("settings") or {}
    teams = int(settings.get("teams") or 0)
    if teams < 1:
        return None
    _, slot = snake_slot_for_pick(pick_no, teams)
    slot_to_roster = _int_map(draft.get("slot_to_roster_id"))
    return slot_to_roster.get(slot)


def traded_owner_map(traded_picks: list[dict[str, Any]] | None) -> dict[tuple[int, int], int]:
    """Map (round, original_roster_id) -> current owner roster_id."""
    out: dict[tuple[int, int], int] = {}
    for row in traded_picks or []:
        try:
            round_no = int(row.get("round"))
            original = int(row.get("roster_id"))
            owner = int(row.get("owner_id"))
        except (TypeError, ValueError):
            continue
        out[(round_no, original)] = owner
    return out


def owner_roster_for_pick(
    pick_no: int,
    draft: dict[str, Any],
    traded_picks: list[dict[str, Any]] | None = None,
) -> int | None:
    settings = draft.get("settings") or {}
    teams = int(settings.get("teams") or 0)
    if teams < 1:
        return None
    round_no, _slot = snake_slot_for_pick(pick_no, teams)
    original = original_roster_for_pick(pick_no, draft)
    if original is None:
        return None
    return traded_owner_map(traded_picks).get((round_no, original), original)


def upcoming_picks_for_roster(
    roster_id: int,
    current_pick: int,
    draft: dict[str, Any],
    traded_picks: list[dict[str, Any]] | None = None,
    limit: int = 2,
) -> list[int]:
    settings = draft.get("settings") or {}
    teams = int(settings.get("teams") or 12)
    rounds = int(settings.get("rounds") or 29)
    total = teams * rounds
    found: list[int] = []
    for pick_no in range(max(current_pick, 1), total + 1):
        if owner_roster_for_pick(pick_no, draft, traded_picks) == roster_id:
            found.append(pick_no)
            if len(found) >= limit:
                break
    return found


def drafted_player_ids(picks: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    found: set[str] = set()
    for pick in picks:
        player_id = str(pick.get("player_id") or "").strip()
        if not player_id or player_id in found:
            continue
        found.add(player_id)
        seen.append(player_id)
    return seen


@dataclass
class UserMapping:
    username: str
    user_id: str
    roster_id: int | None
    draft_slot: int | None
    display_name: str
    team_name: str
    board_only: bool


def map_user(
    username: str,
    sleeper_user: dict[str, Any] | None,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]],
    draft: dict[str, Any],
) -> UserMapping:
    handle = username.strip()
    user_id = str((sleeper_user or {}).get("user_id") or "")
    if not user_id and handle:
        lowered = handle.lower()
        for row in users:
            if str(row.get("username") or "").lower() == lowered:
                user_id = str(row.get("user_id") or "")
                sleeper_user = row
                break
            if str(row.get("display_name") or "").lower() == lowered:
                user_id = str(row.get("user_id") or "")
                sleeper_user = row
                break
    league_user = next((row for row in users if str(row.get("user_id")) == user_id), None)
    roster = next((row for row in rosters if str(row.get("owner_id")) == user_id), None)
    slot_map = _user_id_to_slot(draft)
    draft_slot = slot_map.get(user_id)
    display = str((league_user or sleeper_user or {}).get("display_name") or handle)
    meta = (league_user or {}).get("metadata") or {}
    team_name = str(meta.get("team_name") or display)
    board_only = not user_id or roster is None or draft_slot is None
    return UserMapping(
        username=handle,
        user_id=user_id,
        roster_id=int(roster["roster_id"]) if roster and roster.get("roster_id") is not None else None,
        draft_slot=draft_slot,
        display_name=display,
        team_name=team_name,
        board_only=board_only,
    )


def player_display_name(player: dict[str, Any] | None, player_id: str) -> str:
    if not player:
        return player_id
    full = f"{player.get('first_name') or ''} {player.get('last_name') or ''}".strip()
    return full or str(player.get("full_name") or player_id)


@dataclass
class DraftView:
    current_pick: int
    round_no: int
    slot_on_clock: int | None
    roster_on_clock: int | None
    user_on_clock: str
    picks_until_user: int | None
    next_two_picks: list[int]
    drafted_ids: list[str]
    user_player_ids: list[str]
    pick_log: list[dict[str, Any]] = field(default_factory=list)
    total_picks: int = 0
    teams: int = 12
    rounds: int = 29


def display_name_for_roster(
    roster_id: int | None,
    users: list[dict[str, Any]],
    rosters: list[dict[str, Any]] | None = None,
) -> str:
    if roster_id is None:
        return "Unknown"
    owner_id = ""
    for row in rosters or []:
        if int(row.get("roster_id") or 0) == roster_id:
            owner_id = str(row.get("owner_id") or "")
            break
    user_lookup = {str(row.get("user_id")): row for row in users}
    if owner_id and owner_id in user_lookup:
        return str(user_lookup[owner_id].get("display_name") or "Unknown")
    return "Unknown"


def rebuild_draft_view(
    draft: dict[str, Any],
    picks: list[dict[str, Any]],
    users: list[dict[str, Any]],
    mapping: UserMapping | None,
    traded_picks: list[dict[str, Any]] | None = None,
    players: dict[str, Any] | None = None,
    rosters: list[dict[str, Any]] | None = None,
) -> DraftView:
    settings = draft.get("settings") or {}
    teams = int(settings.get("teams") or 12)
    rounds = int(settings.get("rounds") or 29)
    total = teams * rounds
    current_pick = len(picks) + 1
    if current_pick <= total:
        round_no, slot = snake_slot_for_pick(current_pick, teams)
        roster_on_clock = owner_roster_for_pick(current_pick, draft, traded_picks)
    else:
        round_no, slot, roster_on_clock = rounds, None, None
    on_clock_name = display_name_for_roster(roster_on_clock, users, rosters)
    next_two: list[int] = []
    picks_until: int | None = None
    user_player_ids: list[str] = []
    if mapping and mapping.roster_id is not None:
        next_two = upcoming_picks_for_roster(mapping.roster_id, current_pick, draft, traded_picks, limit=2)
        if next_two:
            picks_until = max(0, next_two[0] - current_pick)
        user_player_ids = [
            str(pick.get("player_id"))
            for pick in picks
            if int(pick.get("roster_id") or 0) == mapping.roster_id and pick.get("player_id")
        ]
    pick_log = []
    for pick in picks:
        pid = str(pick.get("player_id") or "")
        meta = pick.get("metadata") or {}
        player = (players or {}).get(pid) or {}
        name = f"{meta.get('first_name') or ''} {meta.get('last_name') or ''}".strip()
        if not name:
            name = player_display_name(player, pid)
        pick_log.append(
            {
                "pick_no": pick.get("pick_no"),
                "round": pick.get("round"),
                "player": name,
                "player_id": pid,
                "position": meta.get("position") or player.get("position"),
                "team": meta.get("team") or player.get("team"),
                "roster_id": pick.get("roster_id"),
                "draft_slot": pick.get("draft_slot"),
            }
        )
    return DraftView(
        current_pick=current_pick,
        round_no=round_no,
        slot_on_clock=slot,
        roster_on_clock=roster_on_clock,
        user_on_clock=on_clock_name,
        picks_until_user=picks_until,
        next_two_picks=next_two,
        drafted_ids=drafted_player_ids(picks),
        user_player_ids=user_player_ids,
        pick_log=pick_log,
        total_picks=total,
        teams=teams,
        rounds=rounds,
    )
