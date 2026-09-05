"""DraftLock Lite — one-screen Sleeper draft decision dashboard."""

from __future__ import annotations

import io
import math
import struct
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from config import (
    KNOWN_DRAFT_ID,
    KNOWN_LEAGUE_ID,
    STRATEGY_BALANCED,
    STRATEGY_OPTIONS,
    canon_position,
    expected_league_warnings,
    load_base_policy,
    policy_for_strategy,
    resolved_sleeper_username,
)
from decision import count_positions, next_target_checkpoint, recommend
from phone_access import (
    copy_top5_html,
    is_phone_user_agent,
    mobile_css,
    phone_open_url,
    phone_qr_png,
    prepare_phone_session,
    record_phone_hit,
)
from rankings import (
    RankedPlayer,
    RankingError,
    available_ranked_players,
    load_rankings,
    match_rankings_to_sleeper,
    parse_csv_rankings,
)
from sleeper import (
    SleeperClient,
    map_user,
    player_display_name,
    rebuild_draft_view,
)

POLL_SECONDS = 2
RANKING_STALE_S = 24 * 60 * 60


def _beep_wav() -> bytes:
    sample_rate = 16000
    duration = 0.22
    freq = 880
    frames = int(sample_rate * duration)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            value = int(24000 * math.sin(2 * math.pi * freq * index / sample_rate))
            handle.writeframesraw(struct.pack("<h", value))
    return buffer.getvalue()


BEEP_WAV = _beep_wav()


def _init_state() -> None:
    defaults = {
        "strategy_locked": False,
        "locked_strategy": STRATEGY_BALANCED,
        "session_log": [],
        "confirm_unlock": False,
        "confirm_draft_switch": False,
        "active_draft_id": KNOWN_DRAFT_ID,
        "mute_alerts": False,
        "last_alert_key": "",
        "last_good": None,
        "status": "OFFLINE",
        "status_error": "",
        "uploaded_path": None,
        "board_only": False,
        "client": SleeperClient(),
        "base_policy": load_base_policy(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.session_log = [f"{stamp} {message}"] + list(st.session_state.session_log)[:19]


def _status_color(status: str) -> str:
    return {"LIVE": "#1f9d55", "STALE": "#d4a017", "OFFLINE": "#c0392b"}.get(status, "#666")


def _ranked_from_sleeper(player_id: str, sleeper_players: dict, ranking_by_id: dict[str, RankedPlayer]) -> RankedPlayer:
    if player_id in ranking_by_id:
        return ranking_by_id[player_id]
    row = sleeper_players.get(player_id) or {}
    return RankedPlayer(
        name=player_display_name(row, player_id),
        position=canon_position(row.get("position")),
        team=str(row.get("team") or ""),
        sleeper_id=player_id,
        age=float(row["age"]) if row.get("age") is not None else None,
        source="sleeper",
    )


def _is_phone() -> bool:
    try:
        user_agent = st.context.headers.get("User-Agent") or ""
    except Exception:  # noqa: BLE001 — headers are missing outside a browser session
        return False
    if is_phone_user_agent(user_agent):
        record_phone_hit(user_agent)
        return True
    return False


def _slots(phone: bool, spec: list):
    if phone:
        return [st.container() for _ in spec]
    return list(st.columns(spec))


def _copy_button(names: list[str]) -> None:
    # Keep JS out of HTML attributes. `=>` in onclick is parsed as a tag closer
    # inside Streamlit's iframe srcdoc and the script leaks onto the page.
    # Phone browsers on http://LAN also need a selectable textarea fallback.
    components.html(copy_top5_html(names), height=170)


def _maybe_alert(picks_until: int | None, current_pick: int, muted: bool) -> None:
    if muted or picks_until is None:
        return
    if picks_until == 3:
        key = f"three-{current_pick}"
    elif picks_until == 0:
        key = f"clock-{current_pick}"
    else:
        return
    if st.session_state.last_alert_key == key:
        return
    st.session_state.last_alert_key = key
    st.audio(BEEP_WAV, format="audio/wav", autoplay=True)


def _freshness_label(fetched_at: float) -> str:
    if not fetched_at:
        return "unknown"
    return datetime.fromtimestamp(fetched_at, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _load_snapshot(username: str, league_id: str, uploaded) -> dict:
    client: SleeperClient = st.session_state.client
    sleeper_user = client.user_by_username(username) if username.strip() else None
    league = client.league(league_id)
    users = client.users(league_id)
    rosters = client.rosters(league_id)
    discovered_draft_id = str(league.get("draft_id") or st.session_state.active_draft_id)
    draft_id = st.session_state.active_draft_id
    draft = client.draft(draft_id)
    picks = client.picks(draft_id)
    traded = client.traded_picks(draft_id)
    players = client.nfl_players()
    mapping = map_user(username, sleeper_user, users, rosters, draft)
    ranking = load_rankings(uploaded_csv=st.session_state.uploaded_path, session=client.session)
    if uploaded is not None:
        tmp = Path("data/cache") / "uploaded-rankings.csv"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(uploaded.getvalue())
        st.session_state.uploaded_path = tmp
        ranking = parse_csv_rankings(tmp, time.time())
        ranking.warnings.append("Using uploaded ranking CSV.")
    ranking = match_rankings_to_sleeper(ranking, players)
    view = rebuild_draft_view(draft, picks, users, mapping, traded, players, rosters)
    return {
        "league": league,
        "users": users,
        "rosters": rosters,
        "draft": draft,
        "picks": picks,
        "traded": traded,
        "players": players,
        "mapping": mapping,
        "ranking": ranking,
        "view": view,
        "discovered_draft_id": discovered_draft_id,
        "fetched_at": time.time(),
        "warnings": expected_league_warnings(league, draft) + list(ranking.warnings),
    }


def _score_snapshot(snapshot: dict, strategy: str) -> tuple[list, list[RankedPlayer]]:
    mapping = snapshot["mapping"]
    ranking = snapshot["ranking"]
    view = snapshot["view"]
    players = snapshot["players"]
    ranking_by_id = {p.sleeper_id: p for p in ranking.players if p.sleeper_id}
    roster = [_ranked_from_sleeper(pid, players, ranking_by_id) for pid in view.user_player_ids]
    available = available_ranked_players(ranking, view.drafted_ids)
    policy = policy_for_strategy(strategy, st.session_state.base_policy)
    next_pick = view.next_two_picks[0] if view.next_two_picks else None
    scored = recommend(
        available=available,
        roster=roster,
        policy=policy,
        round_no=view.round_no,
        current_pick=view.current_pick,
        next_user_pick=next_pick,
        completed_picks=snapshot["picks"],
        has_uploaded_idp=bool(ranking.from_upload and ranking.has_idp),
        all_ranked=ranking.players,
    )
    return scored, roster


def main() -> None:
    st.set_page_config(page_title="DraftLock Lite", layout="wide")
    st.markdown(f"<style>{mobile_css()}</style>", unsafe_allow_html=True)
    _init_state()
    raw = st.query_params.get("u", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    st.session_state.sleeper_username = resolved_sleeper_username(
        raw,
        st.session_state.get("sleeper_username"),
    )
    phone = _is_phone()
    prepare_phone_session(phone, st.session_state)
    st.title("DraftLock Lite")
    st.caption("Decision dashboard only. It never submits picks to Sleeper. Paste the top five into Sleeper’s native queue.")
    if not phone:
        url = phone_open_url(st.session_state.sleeper_username)
        if url:
            left, right = st.columns([3, 1])
            with left:
                where = "cellular is fine" if url.startswith("https://") else "same Wi-Fi"
                st.info(
                    f"Scan this on your phone ({where}): `{url}` — lock strategy on the phone. "
                    "It still never submits picks."
                )
            with right:
                st.image(phone_qr_png(url), caption="Phone camera", width=148)
        else:
            st.warning("Could not find a Wi-Fi address. Connect this PC and your phone to the same network, then restart.")

    locked = st.session_state.strategy_locked
    settings = (
        st.expander("Draft settings", expanded=not locked)
        if phone
        else st.container(border=True)
    )
    with settings:
        c1, c2, c3, c4 = _slots(phone, [1.1, 1.1, 1.2, 1.3])
        with c1:
            league_id = st.text_input("League ID", value=KNOWN_LEAGUE_ID, disabled=locked)
        with c2:
            username = st.text_input(
                "Sleeper username",
                placeholder="your Sleeper handle",
                key="sleeper_username",
            )
        with c3:
            strategy = st.selectbox(
                "Strategy",
                list(STRATEGY_OPTIONS),
                index=list(STRATEGY_OPTIONS).index(st.session_state.locked_strategy)
                if st.session_state.locked_strategy in STRATEGY_OPTIONS
                else 0,
                disabled=locked,
            )
        with c4:
            uploaded = st.file_uploader(
                "Optional ranking CSV",
                type=["csv"],
                disabled=locked,
                help="Columns: player, position, team, value, rank, sleeper_id",
            )
        b1, b2, b3, b4 = _slots(phone, [1, 1, 1, 2])
        with b1:
            if not locked and st.button("Lock strategy", type="primary"):
                st.session_state.strategy_locked = True
                st.session_state.locked_strategy = strategy
                st.session_state.confirm_unlock = False
                _log(f"Locked strategy: {strategy}")
                st.rerun()
        with b2:
            if locked:
                st.session_state.confirm_unlock = st.checkbox("Confirm unlock", value=st.session_state.confirm_unlock)
        with b3:
            if locked and st.button("Unlock strategy", disabled=not st.session_state.confirm_unlock):
                st.session_state.strategy_locked = False
                _log(f"Unlocked strategy (was {st.session_state.locked_strategy})")
                st.session_state.confirm_unlock = False
                st.rerun()
        with b4:
            st.session_state.mute_alerts = st.checkbox("Mute alerts", value=st.session_state.mute_alerts)
        if st.session_state.session_log:
            st.caption("Session log: " + " · ".join(st.session_state.session_log[:6]))

    active_strategy = st.session_state.locked_strategy if locked else strategy

    @st.fragment(run_every=POLL_SECONDS)
    def live_board() -> None:
        snapshot = None
        try:
            snapshot = _load_snapshot(username, league_id.strip() or KNOWN_LEAGUE_ID, uploaded)
            st.session_state.last_good = snapshot
            st.session_state.status = "LIVE"
            st.session_state.status_error = ""
        except Exception as exc:  # noqa: BLE001 — keep last good screen
            st.session_state.status = "STALE" if st.session_state.last_good else "OFFLINE"
            st.session_state.status_error = str(exc)
            snapshot = st.session_state.last_good
        if snapshot is None:
            st.error("OFFLINE — waiting for Sleeper. " + st.session_state.status_error)
            return

        discovered = snapshot["discovered_draft_id"]
        if discovered and discovered != st.session_state.active_draft_id:
            st.warning(f"League draft ID is {discovered}, different from {st.session_state.active_draft_id}.")
            if st.button("Switch to the current league draft"):
                st.session_state.active_draft_id = discovered
                _log(f"Switched draft ID to {discovered}")
                st.rerun()

        view = snapshot["view"]
        mapping = snapshot["mapping"]
        ranking = snapshot["ranking"]
        status = st.session_state.status
        scored, roster = _score_snapshot(snapshot, active_strategy)
        top5 = scored[:5]
        _maybe_alert(view.picks_until_user, view.current_pick, st.session_state.mute_alerts)

        next_two = ", ".join(str(n) for n in view.next_two_picks) if view.next_two_picks else "—"
        until = "—" if view.picks_until_user is None else str(view.picks_until_user)
        status_badge = (
            f"<div style='margin-top:12px;padding:10px;border-radius:8px;text-align:center;"
            f"background:{_status_color(status)};color:white;font-weight:700'>{status}</div>"
        )
        if phone:
            st.markdown(
                f"**Pick {view.current_pick}** · Round {view.round_no} · "
                f"On the clock: {view.user_on_clock} · Until you: {until} · Next: {next_two}"
            )
            st.markdown(status_badge, unsafe_allow_html=True)
        else:
            s1, s2, s3, s4, s5, s6 = st.columns([1, 1, 1.2, 1, 1.2, 0.8])
            s1.metric("Overall pick", f"{view.current_pick}")
            s2.metric("Round", f"{view.round_no}")
            s3.metric("On the clock", view.user_on_clock)
            s4.metric("Picks until you", until)
            s5.metric("Your next two", next_two)
            s6.markdown(status_badge, unsafe_allow_html=True)
        if mapping.board_only:
            st.info("Board-only mode: enter a Sleeper username from this league to attach your roster and pick slot.")
        else:
            st.caption(
                f"{mapping.display_name} · slot {mapping.draft_slot} · roster {mapping.roster_id} · {mapping.team_name}"
            )

        for warning in snapshot["warnings"]:
            st.warning(warning)
        if not ranking.has_idp:
            st.warning("No IDP rankings were supplied. IDP stays out of the main queue until the policy allows it.")
        if ranking.fetched_at and time.time() - ranking.fetched_at > RANKING_STALE_S:
            st.warning("Rankings are older than 24 hours.")
        if ranking.unresolved:
            with st.expander(f"Unresolved ranking matches ({len(ranking.unresolved)})"):
                st.write(ranking.unresolved)
        if st.session_state.status_error and status != "LIVE":
            st.caption("Last fetch error: " + st.session_state.status_error)

        if not top5:
            st.error("No eligible recommendations yet. Upload a ranking CSV or wait for the value feed.")
            return

        top = top5[0]
        player = top.player
        label = "Take now" if top.take_now else "Likely safe to wait"

        def _recommend_card() -> None:
            st.subheader(player.name)
            st.markdown(
                f"**{player.position}** · {player.team or 'FA'} · "
                f"age {player.age if player.age is not None else '—'} · "
                f"dynasty value {player.value if player.value is not None else '—'} · "
                f"adj. score {top.final_score:.1f}"
            )
            st.markdown(f"**{label}** · wait risk **{int(round(top.wait_risk * 100))}%**")
            for reason in top.reasons:
                st.write(f"- {reason}")
            st.caption(f"Data freshness: rankings {_freshness_label(ranking.fetched_at)} · source {ranking.source}")

        def _queue_card() -> None:
            st.markdown("##### Queue (top 5)")
            for index, row in enumerate(top5, start=1):
                mark = "→" if index == 1 else f"{index}."
                st.write(
                    f"{mark} {row.player.name} ({row.player.position})  "
                    f"score {row.final_score:.1f}  risk {int(round(row.wait_risk * 100))}%"
                )
            _copy_button([row.player.name for row in top5])
            st.caption("Copy top 5 does **not** update the Sleeper queue. Paste the names into Sleeper yourself.")

        rec, queue = _slots(phone, [1.4, 1])
        with rec:
            _recommend_card()
        with queue:
            _queue_card()

        counts = count_positions(roster)
        targets = next_target_checkpoint(view.round_no, policy_for_strategy(active_strategy, st.session_state.base_policy))
        remaining = {pos: max(0, targets.get(pos, 0) - counts.get(pos, 0)) for pos in ("QB", "RB", "WR", "TE", "IDP")}

        def _roster_block() -> None:
            st.markdown("##### Your roster")
            if roster:
                roster_rows = [
                    {
                        "player": p.name,
                        "position": p.position,
                        "team": p.team,
                        "age": p.age,
                    }
                    for p in sorted(roster, key=lambda p: (p.position, p.name))
                ]
                st.dataframe(pd.DataFrame(roster_rows), hide_index=True, use_container_width=True)
            else:
                st.caption("No drafted players on your roster yet.")
            st.markdown("##### Position counts")
            count_rows = [
                {
                    "position": pos,
                    "have": counts.get(pos, 0),
                    "target": targets.get(pos, 0),
                    "remaining_target": remaining.get(pos, 0),
                }
                for pos in ("QB", "RB", "WR", "TE", "IDP", "K", "DEF")
            ]
            st.dataframe(pd.DataFrame(count_rows), hide_index=True, use_container_width=True)

        def _board_table() -> None:
            board = []
            for row in scored[:20]:
                board.append(
                    {
                        "player": row.player.name,
                        "pos": row.player.position,
                        "team": row.player.team,
                        "age": row.player.age,
                        "value": row.player.value,
                        "base": round(row.base_score, 1),
                        "pos_score": round(row.position_score, 1),
                        "need": row.need_multiplier,
                        "wait": int(round(row.wait_risk * 100)),
                        "urgency": round(row.urgency_bonus, 1),
                        "final": round(row.final_score, 1),
                    }
                )
            st.dataframe(pd.DataFrame(board), hide_index=True, use_container_width=True)

        def _pick_log_table() -> None:
            if view.pick_log:
                st.dataframe(pd.DataFrame(view.pick_log), hide_index=True, use_container_width=True)
            else:
                st.caption("No picks yet.")

        if phone:
            _roster_block()
            with st.expander("Best 20 available"):
                _board_table()
            with st.expander("Pick log"):
                _pick_log_table()
        else:
            t1, t2 = st.columns(2)
            with t1:
                _roster_block()
            with t2:
                st.markdown("##### Best 20 available")
                _board_table()
            st.markdown("##### Pick log")
            _pick_log_table()

    live_board()


if __name__ == "__main__":
    main()
