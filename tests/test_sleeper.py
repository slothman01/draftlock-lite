from __future__ import annotations

import responses

from config import KNOWN_USERNAME, resolved_sleeper_username
from sleeper import (
    SleeperClient,
    all_picks_for_slot,
    drafted_player_ids,
    map_user,
    owner_roster_for_pick,
    rebuild_draft_view,
    snake_pick_number,
    snake_slot_for_pick,
    upcoming_picks_for_roster,
)
from tests.conftest import load_fixture

TEAMS = 12
ROUNDS = 29


def test_blank_username_prefills_slothman01():
    assert KNOWN_USERNAME == "Slothman01"
    assert resolved_sleeper_username("", "") == "Slothman01"
    assert resolved_sleeper_username(None, None) == "Slothman01"
    assert resolved_sleeper_username("OtherUser", "") == "OtherUser"
    assert resolved_sleeper_username("", "KeepMe") == "KeepMe"


def test_snake_pick_math_round_trip():
    for pick_no in range(1, TEAMS * ROUNDS + 1):
        round_no, slot = snake_slot_for_pick(pick_no, TEAMS)
        assert snake_pick_number(round_no, slot, TEAMS) == pick_no


def test_snake_next_picks_from_every_slot():
    draft = load_fixture("draft_metadata.json")
    expected_round1 = list(range(1, 13))
    expected_round2 = list(range(24, 12, -1))
    for slot in range(1, 13):
        picks = all_picks_for_slot(slot, TEAMS, ROUNDS)
        assert picks[0] == expected_round1[slot - 1]
        assert picks[1] == expected_round2[slot - 1]
        assert picks[2] == 24 + slot
        assert len(picks) == ROUNDS
        roster_id = int(draft["slot_to_roster_id"][str(slot)])
        upcoming = upcoming_picks_for_roster(roster_id, 1, draft, limit=3)
        assert upcoming == picks[:3]


def test_drafted_players_removed_exactly_once():
    picks = load_fixture("picks_partial.json")
    duplicated = picks + [dict(picks[0], pick_no=99)]
    ids = drafted_player_ids(duplicated)
    assert ids.count("4984") == 1
    assert len(ids) == len(picks)


def test_traded_picks_use_current_owner():
    draft = load_fixture("draft_metadata.json")
    users = load_fixture("users.json")
    rosters = load_fixture("rosters.json")
    # Round 3, slot 1 is overall pick 25 and originally belongs to roster 6.
    assert owner_roster_for_pick(25, draft, []) == 6
    traded = [{"round": 3, "roster_id": 6, "owner_id": 2}]
    assert owner_roster_for_pick(25, draft, traded) == 2
    assert owner_roster_for_pick(1, draft, traded) == 6
    view = rebuild_draft_view(
        draft,
        [],
        users,
        mapping=None,
        traded_picks=traded,
        rosters=rosters,
    )
    # Pick 1 still original owner; after 24 picks, pick 25 is roster 2.
    fake_picks = [{"player_id": str(i), "pick_no": i, "roster_id": 1} for i in range(1, 25)]
    view = rebuild_draft_view(draft, fake_picks, users, None, traded, rosters=rosters)
    assert view.current_pick == 25
    assert view.roster_on_clock == 2
    assert view.user_on_clock == "Slothman01"


def test_blank_username_stays_board_only():
    users = load_fixture("users.json")
    rosters = load_fixture("rosters.json")
    draft = load_fixture("draft_metadata.json")
    mapping = map_user("", None, users, rosters, draft)
    assert mapping.board_only is True
    assert mapping.roster_id is None
    assert mapping.display_name == ""
    named = map_user("Slothman01", None, users, rosters, draft)
    assert named.board_only is False
    assert named.roster_id == 2


@responses.activate
def test_sleeper_client_uses_fixtures_not_live_http():
    league = load_fixture("league_settings.json")
    users = load_fixture("users.json")
    rosters = load_fixture("rosters.json")
    draft = load_fixture("draft_metadata.json")
    picks = load_fixture("picks_empty.json")
    responses.get("https://api.sleeper.app/v1/league/1389407386388664320", json=league)
    responses.get("https://api.sleeper.app/v1/league/1389407386388664320/users", json=users)
    responses.get("https://api.sleeper.app/v1/league/1389407386388664320/rosters", json=rosters)
    responses.get("https://api.sleeper.app/v1/draft/1389407387676315648", json=draft)
    responses.get("https://api.sleeper.app/v1/draft/1389407387676315648/picks", json=picks)
    responses.get("https://api.sleeper.app/v1/user/Slothman01", json={"user_id": "1389744881773051904", "username": "Slothman01"})
    client = SleeperClient()
    mapping = map_user(
        "Slothman01",
        client.user_by_username("Slothman01"),
        client.users(),
        client.rosters(),
        client.draft(),
    )
    assert mapping.board_only is False
    assert mapping.roster_id == 2
    assert mapping.draft_slot == 11
    view = rebuild_draft_view(client.draft(), client.picks(), client.users(), mapping, rosters=client.rosters())
    assert view.current_pick == 1
    assert view.next_two_picks == [11, 14]
