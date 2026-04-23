"""
Results tracker — runs at 9:00 AM ET the morning after.

For each pick in yesterday's slate:
  1. Fetch the game's play-by-play from MLB Stats API
  2. Determine if any runs scored in the 1st inning
  3. Mark the pick WIN (NRFI pick + no runs) / WIN (YRFI pick + runs) / LOSS
  4. Append to the rolling accuracy log at docs/results.json

That log powers the results dashboard.
"""
from __future__ import annotations
import json
import sys
import datetime as dt
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
PICKS_DIR = ROOT / "data" / "picks"
RESULTS_FILE = ROOT / "docs" / "results.json"
MLB_API = "https://statsapi.mlb.com/api/v1"
ET = dt.timezone(dt.timedelta(hours=-4))


def first_inning_runs(game_pk: int) -> int | None:
    """Return total runs scored in the 1st inning, or None if game hasn't finished."""
    try:
        r = requests.get(f"{MLB_API}.1/game/{game_pk}/feed/live", timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        try:
            r = requests.get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
                             timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[track] fetch failed for {game_pk}: {e}", file=sys.stderr)
            return None

    status = data.get("gameData", {}).get("status", {}).get("abstractGameState", "")
    if status != "Final":
        print(f"[track] game {game_pk} status={status}, skipping")
        return None

    linescore = data.get("liveData", {}).get("linescore", {})
    innings = linescore.get("innings", [])
    if not innings:
        return None
    first = innings[0]
    return (first.get("away", {}).get("runs", 0) or 0) + \
           (first.get("home", {}).get("runs", 0) or 0)


def grade_pick(label: str, runs_first: int) -> str:
    """Return 'win', 'loss', or 'push'."""
    nrfi_happened = runs_first == 0
    predicted_nrfi = "NRFI" in label
    if predicted_nrfi and nrfi_happened: return "win"
    if not predicted_nrfi and not nrfi_happened: return "win"
    return "loss"


def update_results(date: str):
    picks_file = PICKS_DIR / f"{date}.json"
    if not picks_file.exists():
        print(f"[track] no picks file for {date}")
        return
    picks = json.loads(picks_file.read_text())

    graded = []
    for p in picks:
        runs = first_inning_runs(p["game_pk"])
        if runs is None:
            graded.append({**p, "runs_1st": None, "grade": "pending"})
            continue
        graded.append({
            **p,
            "runs_1st": runs,
            "grade": grade_pick(p["label"], runs),
        })

    # Load or initialize results log
    log = {"days": {}}
    if RESULTS_FILE.exists():
        log = json.loads(RESULTS_FILE.read_text())
    log.setdefault("days", {})[date] = graded

    # Rollup stats
    all_graded = [g for day in log["days"].values() for g in day if g["grade"] != "pending"]
    strong_nrfi = [g for g in all_graded if g["label"] == "Strong NRFI"]
    lean_nrfi = [g for g in all_graded if g["label"] == "Lean NRFI"]

    log["summary"] = {
        "total_picks": len(all_graded),
        "overall_wins": sum(1 for g in all_graded if g["grade"] == "win"),
        "strong_nrfi_picks": len(strong_nrfi),
        "strong_nrfi_wins": sum(1 for g in strong_nrfi if g["grade"] == "win"),
        "lean_nrfi_picks": len(lean_nrfi),
        "lean_nrfi_wins": sum(1 for g in lean_nrfi if g["grade"] == "win"),
        "last_updated": dt.datetime.now(ET).isoformat(),
    }

    RESULTS_FILE.write_text(json.dumps(log, indent=2))
    print(f"[track] wrote {RESULTS_FILE} — graded {len(graded)} picks for {date}")


def main():
    yesterday = (dt.datetime.now(ET) - dt.timedelta(days=1)).date()
    date_str = yesterday.isoformat()
    update_results(date_str)


if __name__ == "__main__":
    main()
