"""Ranking feed, CSV fallback, and conservative player-name matching."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from rapidfuzz import fuzz, process

from config import SAMPLE_RANKINGS_PATH, canon_position, ensure_cache_dir

FANTASYCALC_URL = (
    "https://api.fantasycalc.com/values/current?isDynasty=true&numQbs=2&numTeams=12&ppr=1"
)
RANKINGS_CACHE_TTL_S = 6 * 60 * 60
FUZZY_MIN_SCORE = 92
FUZZY_AMBIGUOUS_GAP = 3
REQUIRED_CSV_COLUMNS = ("player", "position")


class RankingError(RuntimeError):
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


def normalize_name(name: str) -> str:
    text = (name or "").lower()
    text = text.replace(".", " ")
    text = re.sub(r"[^a-z0-9\s]+", "", text)
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class RankedPlayer:
    name: str
    position: str
    team: str = ""
    value: float | None = None
    rank: int | None = None
    sleeper_id: str | None = None
    age: float | None = None
    starter: bool = False
    source: str = "unknown"


@dataclass
class RankingSet:
    players: list[RankedPlayer]
    fetched_at: float
    source: str
    warnings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    has_idp: bool = False
    has_k: bool = False
    has_def: bool = False
    from_upload: bool = False


def _row_to_player(row: dict[str, Any], source: str) -> RankedPlayer | None:
    player = row.get("player") if isinstance(row.get("player"), dict) else {}
    name = str(player.get("name") or row.get("player") or row.get("name") or "").strip()
    if not name or name.upper() == "PICK":
        return None
    position = canon_position(player.get("position") or row.get("position"))
    if position == "PICK":
        return None
    sleeper_id = player.get("sleeperId") or row.get("sleeper_id") or row.get("sleeperId")
    sleeper_id = str(sleeper_id).strip() if sleeper_id not in (None, "", "None") else None
    value_raw = row.get("value")
    rank_raw = row.get("overallRank") or row.get("rank")
    age_raw = player.get("maybeAge") or row.get("age")
    team = str(player.get("maybeTeam") or row.get("team") or "").strip().upper()
    try:
        value = float(value_raw) if value_raw not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    try:
        rank = int(rank_raw) if rank_raw not in (None, "") else None
    except (TypeError, ValueError):
        rank = None
    try:
        age = float(age_raw) if age_raw not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    if value is None and rank is None:
        return None
    starter = bool(row.get("starter")) or int(player.get("maybeYoe") or 0) >= 3
    return RankedPlayer(
        name=name,
        position=position,
        team=team,
        value=value,
        rank=rank,
        sleeper_id=sleeper_id,
        age=age,
        starter=starter,
        source=source,
    )


def parse_fantasycalc_payload(payload: Any, fetched_at: float) -> RankingSet:
    rows = payload if isinstance(payload, list) else payload.get("players") if isinstance(payload, dict) else []
    players: list[RankedPlayer] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        parsed = _row_to_player(row, "fantasycalc")
        if parsed is None:
            continue
        players.append(parsed)
    if not players:
        raise RankingError("FantasyCalc payload had no usable players")
    _assign_missing_ranks(players)
    return RankingSet(
        players=players,
        fetched_at=fetched_at,
        source="fantasycalc",
        has_idp=any(p.position == "IDP" for p in players),
        has_k=any(p.position == "K" for p in players),
        has_def=any(p.position == "DEF" for p in players),
    )


def parse_csv_rankings(path: Path, fetched_at: float | None = None) -> RankingSet:
    frame = pd.read_csv(path)
    columns = {str(col).strip().lower(): col for col in frame.columns}
    if "player" not in columns:
        raise RankingError("CSV must include a player column")
    if "position" not in columns:
        raise RankingError("CSV must include a position column")
    if "value" not in columns and "rank" not in columns:
        raise RankingError("CSV must include value or rank")
    players: list[RankedPlayer] = []
    for _, row in frame.iterrows():
        payload = {key: row[col] for key, col in columns.items()}
        parsed = _row_to_player(payload, "csv")
        if parsed is None:
            continue
        if parsed.sleeper_id:
            parsed.sleeper_id = str(int(float(parsed.sleeper_id))) if str(parsed.sleeper_id).replace(".", "", 1).isdigit() else str(parsed.sleeper_id)
        players.append(parsed)
    if not players:
        raise RankingError("CSV had no usable ranking rows")
    _assign_missing_ranks(players)
    now = fetched_at if fetched_at is not None else _now()
    return RankingSet(
        players=players,
        fetched_at=now,
        source="csv",
        from_upload=True,
        has_idp=any(p.position == "IDP" for p in players),
        has_k=any(p.position == "K" for p in players),
        has_def=any(p.position == "DEF" for p in players),
    )


def _assign_missing_ranks(players: list[RankedPlayer]) -> None:
    ordered = sorted(
        players,
        key=lambda p: (
            -(p.value if p.value is not None else float("-inf")),
            p.rank if p.rank is not None else 10**9,
            p.name,
        ),
    )
    for index, player in enumerate(ordered, start=1):
        if player.rank is None:
            player.rank = index


def load_cached_rankings(cache_dir: Path | None = None) -> RankingSet | None:
    cache_dir = cache_dir or ensure_cache_dir()
    payload = _read_json(cache_dir / "fantasycalc.json")
    meta = _read_json(cache_dir / "fantasycalc.meta.json") or {}
    if payload is None:
        return None
    fetched_at = float(meta.get("fetched_at") or 0)
    try:
        ranking = parse_fantasycalc_payload(payload, fetched_at)
    except RankingError:
        return None
    ranking.source = "cache"
    ranking.warnings.append("Using cached FantasyCalc values.")
    return ranking


def fetch_fantasycalc(
    session: requests.Session | None = None,
    cache_dir: Path | None = None,
    now: float | None = None,
    timeout_s: float = 12,
) -> RankingSet:
    cache_dir = cache_dir or ensure_cache_dir()
    now = _now() if now is None else now
    cached = load_cached_rankings(cache_dir)
    meta = _read_json(cache_dir / "fantasycalc.meta.json") or {}
    fetched_at = float(meta.get("fetched_at") or 0)
    if cached and now - fetched_at < RANKINGS_CACHE_TTL_S:
        cached.source = "fantasycalc"
        cached.warnings = []
        return cached
    sess = session or requests.Session()
    response = sess.get(FANTASYCALC_URL, timeout=timeout_s)
    response.raise_for_status()
    payload = response.json()
    ranking = parse_fantasycalc_payload(payload, now)
    _write_json(cache_dir / "fantasycalc.json", payload)
    _write_json(cache_dir / "fantasycalc.meta.json", {"fetched_at": now})
    return ranking


def load_rankings(
    uploaded_csv: Path | None = None,
    session: requests.Session | None = None,
    cache_dir: Path | None = None,
    now: float | None = None,
) -> RankingSet:
    cache_dir = cache_dir or ensure_cache_dir()
    now = _now() if now is None else now
    errors: list[str] = []
    if uploaded_csv is not None:
        ranking = parse_csv_rankings(uploaded_csv, now)
        ranking.warnings.append("Using uploaded ranking CSV.")
        return ranking
    try:
        return fetch_fantasycalc(session=session, cache_dir=cache_dir, now=now)
    except (requests.RequestException, RankingError, ValueError) as exc:
        errors.append(str(exc))
    cached = load_cached_rankings(cache_dir)
    if cached:
        cached.warnings.append("Live ranking feed failed; using the most recent cache.")
        return cached
    if SAMPLE_RANKINGS_PATH.exists():
        ranking = parse_csv_rankings(SAMPLE_RANKINGS_PATH, now)
        ranking.source = "sample"
        ranking.warnings.append("Live ranking feed failed and no cache exists; using sample-rankings.csv.")
        return ranking
    raise RankingError("No ranking feed, cache, or CSV is available")


def _player_names(players: dict[str, Any]) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for sleeper_id, row in players.items():
        if not isinstance(row, dict):
            continue
        full = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        if not full:
            continue
        names.setdefault(normalize_name(full), []).append(str(sleeper_id))
    return names


def match_rankings_to_sleeper(
    ranking: RankingSet,
    sleeper_players: dict[str, Any],
) -> RankingSet:
    by_id = {str(key): value for key, value in sleeper_players.items()}
    names = _player_names(by_id)
    name_keys = list(names.keys())
    unresolved: list[str] = []
    matched: list[RankedPlayer] = []
    for player in ranking.players:
        if player.sleeper_id and player.sleeper_id in by_id:
            sleeper = by_id[player.sleeper_id]
            player.age = player.age or _sleeper_age(sleeper)
            player.team = player.team or str(sleeper.get("team") or "")
            player.position = canon_position(player.position or sleeper.get("position"))
            matched.append(player)
            continue
        key = normalize_name(player.name)
        exact = names.get(key) or []
        if len(exact) == 1:
            player.sleeper_id = exact[0]
            sleeper = by_id[player.sleeper_id]
            player.age = player.age or _sleeper_age(sleeper)
            player.team = player.team or str(sleeper.get("team") or "")
            matched.append(player)
            continue
        if len(exact) > 1:
            unresolved.append(f"{player.name} (duplicate exact name)")
            continue
        if not name_keys:
            unresolved.append(player.name)
            continue
        result = process.extract(key, name_keys, scorer=fuzz.ratio, limit=2)
        if not result:
            unresolved.append(player.name)
            continue
        best_name, best_score, _ = result[0]
        second_score = result[1][1] if len(result) > 1 else 0
        ids = names.get(best_name) or []
        if (
            best_score >= FUZZY_MIN_SCORE
            and (best_score - second_score) >= FUZZY_AMBIGUOUS_GAP
            and len(ids) == 1
        ):
            player.sleeper_id = ids[0]
            sleeper = by_id[player.sleeper_id]
            player.age = player.age or _sleeper_age(sleeper)
            player.team = player.team or str(sleeper.get("team") or "")
            matched.append(player)
        else:
            reason = "ambiguous" if best_score >= FUZZY_MIN_SCORE else "unmatched"
            unresolved.append(f"{player.name} ({reason})")
    ranking.players = matched
    ranking.unresolved = unresolved
    ranking.has_idp = any(p.position == "IDP" for p in matched)
    ranking.has_k = any(p.position == "K" for p in matched)
    ranking.has_def = any(p.position == "DEF" for p in matched)
    return ranking


def _sleeper_age(player: dict[str, Any]) -> float | None:
    age = player.get("age")
    try:
        return float(age) if age is not None else None
    except (TypeError, ValueError):
        return None


def available_ranked_players(ranking: RankingSet, drafted_ids: list[str] | set[str]) -> list[RankedPlayer]:
    taken = {str(pid) for pid in drafted_ids}
    seen: set[str] = set()
    available: list[RankedPlayer] = []
    for player in ranking.players:
        if not player.sleeper_id or player.sleeper_id in taken or player.sleeper_id in seen:
            continue
        seen.add(player.sleeper_id)
        available.append(player)
    return available
