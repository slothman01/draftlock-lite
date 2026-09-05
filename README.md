# DraftLock Lite

A local one-screen dashboard for the Waiver Wire Witnesses Sleeper draft. It watches the board, applies a strategy you lock before the draft, and always shows a recommended player, four fallbacks, why that player won, wait risk, and your roster. It does not submit picks.

## Windows setup

1. Install Python 3.12 or later if needed.
2. Keep this folder where you unzipped or cloned it.
3. Double-click `run.bat`. The first run creates `.venv` and installs packages.

## How to run

Double-click `run.bat`. The app opens at `http://localhost:8501`. Enter your Sleeper username, leave the league ID as-is unless you were told otherwise, choose a strategy, then press **Lock strategy**.

## On your phone

Open [https://slothman01.github.io/draftlock-lite/?u=Slothman01](https://slothman01.github.io/draftlock-lite/?u=Slothman01). That page is hosted on GitHub, so this PC can be off, asleep, or on a different network. It still never submits picks. Paste the top five into Sleeper’s native queue.

Double-click `run.bat` only if you want the desktop Streamlit board on this PC.

## Uploading rankings

Use a CSV with columns `player,position,team,value,rank,sleeper_id`. Only `player`, `position`, and either `value` or `rank` are required. Match by Sleeper ID when you can. If the live dynasty feed is down, the app uses its last cache, then this CSV, then `data/sample-rankings.csv`.

## Putting the top five into Sleeper

Press **Copy top 5**, paste the names into Sleeper’s native draft queue, and let Sleeper’s CPU autopick fire if you disconnect. Copying names does not update Sleeper by itself.

## Wait risk

Wait risk is a 5–95% estimate that the player will be gone before your next pick. It uses market rank plus how this room has been picking versus rank. **Take now** means the player is unlikely to survive. **Likely safe to wait** means you can probably look elsewhere first.

## Editing `policy.yaml`

`policy.yaml` is the Balanced dynasty starting point. Position multipliers, round checkpoints, and hard rules (no kicker/defense before round 25, two QBs after round 6, and so on) live there. Win now and Long horizon are generated from that same file in code: Win now boosts RB and current starters; Long horizon boosts WR and younger players.

## Known limitations

This is a hobby decision aid, not a projection engine. It does not click Sleeper, model injuries, simulate the full draft, or rank IDP/K/DEF well unless you upload those rows. Third-party libraries are MIT-licensed; ranking responses stay on this machine and are not redistributed.
