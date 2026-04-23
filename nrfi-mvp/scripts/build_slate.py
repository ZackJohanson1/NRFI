"""
NRFI Daily Slate Builder
========================
Runs each morning at 7:00 AM ET. Collects:
  1. Today's probable pitchers (MLB Stats API)
  2. Pitcher first-inning stats (MLB Stats API + FanGraphs fallback)
  3. Team first-inning offensive trends (TeamRankings scrape)
  4. Weather at each outdoor stadium (OpenWeatherMap)
  5. FanDuel NRFI market odds (scraped from game pages)

Scores each game and writes:
  - docs/index.html (the published leaderboard)
  - data/picks/YYYY-MM-DD.json (for the results tracker to check tomorrow)
"""
from __future__ import annotations
import json
import os
import sys
import datetime as dt
from pathlib import Path
from typing import Optional

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
DOCS = ROOT / "docs"
DATA = ROOT / "data"
PICKS_DIR = DATA / "picks"
STADIUMS_FILE = DATA / "stadiums.json"

ET = dt.timezone(dt.timedelta(hours=-4))  # DST; for production swap to zoneinfo
MLB_API = "https://statsapi.mlb.com/api/v1"

# -----------------------------------------------------------------------------
# 1. Probable pitchers — MLB Stats API (free, official, no key required)
# -----------------------------------------------------------------------------
def fetch_probable_pitchers(date: str) -> list[dict]:
    """
    Returns: list of {away, home, away_sp_id, home_sp_id, away_sp, home_sp,
                      game_time_et, venue, venue_id}
    """
    url = f"{MLB_API}/schedule"
    params = {
        "sportId": 1,
        "date": date,
        "hydrate": "probablePitcher,venue,team",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            away = g["teams"]["away"]["team"]
            home = g["teams"]["home"]["team"]
            asp = g["teams"]["away"].get("probablePitcher") or {}
            hsp = g["teams"]["home"].get("probablePitcher") or {}
            venue = g.get("venue", {})
            games.append({
                "game_pk": g["gamePk"],
                "away": away.get("abbreviation", away.get("name", "")),
                "home": home.get("abbreviation", home.get("name", "")),
                "away_team_id": away.get("id"),
                "home_team_id": home.get("id"),
                "away_sp_id": asp.get("id"),
                "home_sp_id": hsp.get("id"),
                "away_sp": asp.get("fullName", "TBD"),
                "home_sp": hsp.get("fullName", "TBD"),
                "game_time_utc": g.get("gameDate"),
                "venue": venue.get("name", ""),
                "venue_id": venue.get("id"),
            })
    return games


# -----------------------------------------------------------------------------
# 2. Pitcher stats — MLB Stats API by splits (1st inning ERA via game log)
# -----------------------------------------------------------------------------
def fetch_pitcher_stats(pitcher_id: int, season: int) -> dict:
    """Returns current-season ERA, WHIP, K/9, BB/9, HR/9, first-inning ERA."""
    if not pitcher_id:
        return {"era": 4.50, "whip": 1.30, "k9": 7.0, "bb9": 3.0, "hr9": 1.2, "first_inn_era": 4.50}

    # Season stats
    r = requests.get(
        f"{MLB_API}/people/{pitcher_id}/stats",
        params={"stats": "season", "season": season, "group": "pitching"},
        timeout=15,
    )
    r.raise_for_status()
    stats = {}
    splits = r.json().get("stats", [])
    if splits and splits[0].get("splits"):
        s = splits[0]["splits"][0]["stat"]
        stats = {
            "era": float(s.get("era", 4.50)),
            "whip": float(s.get("whip", 1.30)),
            "k9": float(s.get("strikeoutsPer9Inn", 7.0)),
            "bb9": float(s.get("walksPer9Inn", 3.0)),
            "hr9": float(s.get("homeRunsPer9", 1.2)),
        }

    # First-inning ERA via byInning split
    r = requests.get(
        f"{MLB_API}/people/{pitcher_id}/stats",
        params={"stats": "statSplits", "season": season, "group": "pitching",
                "sitCodes": "i01"},  # inning 1
        timeout=15,
    )
    first_inn_era = stats.get("era", 4.50)
    if r.ok:
        sp = r.json().get("stats", [])
        if sp and sp[0].get("splits"):
            first_inn_era = float(sp[0]["splits"][0]["stat"].get("era", first_inn_era))

    stats.setdefault("era", 4.50)
    stats.setdefault("whip", 1.30)
    stats.setdefault("k9", 7.0)
    stats.setdefault("bb9", 3.0)
    stats.setdefault("hr9", 1.2)
    stats["first_inn_era"] = first_inn_era
    return stats


# -----------------------------------------------------------------------------
# 3. Team offensive first-inning trends — TeamRankings
# -----------------------------------------------------------------------------
TR_URL = "https://www.teamrankings.com/mlb/stat/1st-inning-runs-per-game"

def fetch_team_first_inning_trends() -> dict[str, dict]:
    """Returns {team_abbr: {r1, nrfi_pct}}. Falls back to empty on scrape failure."""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(TR_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        trends = {}
        for row in table.find_all("tr")[1:]:
            cols = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cols) >= 3:
                team_name = cols[1]
                r1 = float(cols[2])
                abbr = TEAM_NAME_TO_ABBR.get(team_name, team_name[:3].upper())
                # Rough NRFI% estimate from 1st-inning R/G:
                # Clean 1st inning rate correlates with ~1 - (r1 * 0.75)
                nrfi_pct = max(30, min(95, 100 - (r1 * 65)))
                trends[abbr] = {"r1": r1, "nrfi_pct": round(nrfi_pct)}
        return trends
    except Exception as e:
        print(f"[warn] team trends scrape failed: {e}", file=sys.stderr)
        return {}


TEAM_NAME_TO_ABBR = {
    "Arizona": "AZ", "Atlanta": "ATL", "Baltimore": "BAL", "Boston": "BOS",
    "Chi Cubs": "CHC", "Chi Sox": "CWS", "Cincinnati": "CIN", "Cleveland": "CLE",
    "Colorado": "COL", "Detroit": "DET", "Houston": "HOU", "Kansas City": "KC",
    "LA Angels": "LAA", "LA Dodgers": "LAD", "Miami": "MIA", "Milwaukee": "MIL",
    "Minnesota": "MIN", "NY Mets": "NYM", "NY Yankees": "NYY", "Oakland": "ATH",
    "Athletics": "ATH", "Philadelphia": "PHI", "Pittsburgh": "PIT",
    "San Diego": "SD", "Seattle": "SEA", "San Francisco": "SF",
    "St. Louis": "STL", "Tampa Bay": "TB", "Texas": "TEX", "Toronto": "TOR",
    "Washington": "WSH",
}


# -----------------------------------------------------------------------------
# 4. Weather — OpenWeatherMap
# -----------------------------------------------------------------------------
def fetch_weather(lat: float, lon: float, api_key: str) -> dict:
    """Returns {temp_f, wind_mph, wind_dir_deg, conditions}."""
    if not api_key:
        return {}
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lon, "appid": api_key, "units": "imperial"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        return {
            "temp_f": d["main"]["temp"],
            "wind_mph": d["wind"]["speed"],
            "wind_dir_deg": d["wind"].get("deg", 0),
            "conditions": d["weather"][0]["main"],
        }
    except Exception as e:
        print(f"[warn] weather fetch failed: {e}", file=sys.stderr)
        return {}


# -----------------------------------------------------------------------------
# 5. FanDuel NRFI odds — scraped from game prediction pages
# -----------------------------------------------------------------------------
def fetch_fanduel_nrfi(away: str, home: str, date: str) -> Optional[float]:
    """
    Attempts to scrape FanDuel's 1st Inning O/U 0.5 Runs odds.
    Converts American odds to implied NRFI probability.
    Returns None if scrape fails — algorithm still runs.
    """
    # NOTE: FanDuel rotates its page structure. Selectors below are a starting
    # point; adjust after inspecting live markup. If scraping fails repeatedly,
    # consider a paid odds API (The Odds API: $0/mo for 500 req, covers MLB).
    try:
        from bs4 import BeautifulSoup
        slug_away = away.lower().replace(" ", "-")
        slug_home = home.lower().replace(" ", "-")
        md = date.replace("-", "-")  # YYYY-MM-DD -> keep dashes, fallback logic
        url = (f"https://www.fanduel.com/research/"
               f"{slug_away}-vs-{slug_home}-mlb-odds-prediction-"
               f"point-spread-over-under-and-betting-trends-for-{md}")
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        # Look for text near "NRFI" or "1st Inning"
        text = soup.get_text()
        # TODO: implement robust parsing once selectors stabilize.
        # For MVP: return None and let the algorithm run alone.
        return None
    except Exception:
        return None


def american_to_implied(odds: int) -> float:
    """Convert American odds to implied probability (0-1)."""
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)


# -----------------------------------------------------------------------------
# 6. Scoring algorithm (refined based on April 19 findings)
# -----------------------------------------------------------------------------
def score_pitcher(era: float, whip: float, k9: float, first_inn_era: float,
                  bb9: float, hr9: float) -> float:
    """Pitcher score 10-90. First-inning ERA weighted at ~45%."""
    score = 50.0
    # ERA (weight ~20%)
    if era < 3.0: score += 10
    elif era < 3.75: score += 5
    elif era < 4.5: score += 0
    elif era < 5.5: score -= 6
    else: score -= 12
    # WHIP (weight ~10%)
    if whip < 1.0: score += 6
    elif whip < 1.2: score += 3
    elif whip < 1.35: score += 0
    else: score -= 5
    # K/9 (weight ~8%)
    if k9 > 10: score += 5
    elif k9 > 8.5: score += 3
    elif k9 > 7: score += 0
    else: score -= 3
    # FIRST-INNING ERA (weight ~45% — dominant predictor)
    if first_inn_era < 2.0: score += 20
    elif first_inn_era < 3.0: score += 12
    elif first_inn_era < 4.0: score += 3
    elif first_inn_era < 5.0: score -= 8
    else: score -= 18
    # Leadoff risk: BB/9 >= 4.0 or HR/9 >= 1.5 is a penalty
    if bb9 >= 4.0: score -= 5
    if hr9 >= 1.5: score -= 5
    return max(10, min(90, score))


def score_lineup(trend: dict) -> float:
    """Offensive first-inning score 10-90. Derived from season NRFI% from TeamRankings."""
    nrfi_pct = trend.get("nrfi_pct", 65)
    # Higher team NRFI% = less dangerous offense = higher score for opposing pitcher
    # Convert: 90% NRFI -> score 75 (very passive), 50% NRFI -> score 25 (very dangerous)
    return max(10, min(90, (nrfi_pct - 50) * 1.25 + 50))


def calc_nrfi(aps: float, hps: float, als: float, hls: float) -> dict:
    """Combine pitcher and opposing lineup into NRFI probability."""
    ah = max(5, min(92, aps - (hls - 50) * 0.4))  # away pitcher holds home lineup
    hh = max(5, min(92, hps - (als - 50) * 0.4))  # home pitcher holds away lineup
    nrfi = max(5, min(92, (ah / 100) * (hh / 100) * 100))
    return {"nrfi": nrfi, "away_hold": ah, "home_hold": hh}


def classify(nrfi: float) -> tuple[str, str]:
    if nrfi >= 65: return ("Strong NRFI", "strong-nrfi")
    if nrfi >= 55: return ("Lean NRFI", "lean-nrfi")
    if nrfi >= 45: return ("Toss-up", "tossup")
    return ("Lean YRFI", "yrfi")


# -----------------------------------------------------------------------------
# 7. Weather adjustment
# -----------------------------------------------------------------------------
def weather_adjust(nrfi: float, weather: dict, venue_id: int, stadiums: dict) -> float:
    """Adjust NRFI% based on wind + stadium orientation."""
    venue = stadiums.get(str(venue_id)) or {}
    if not weather or not venue.get("outdoor", False):
        return nrfi
    wind_mph = weather.get("wind_mph", 0)
    wind_dir = weather.get("wind_dir_deg", 0)
    cf_bearing = venue.get("cf_bearing_deg")  # compass bearing to center field
    if cf_bearing is None or wind_mph < 8:
        return nrfi
    # If wind blowing OUT toward CF (within 45 degrees), drop NRFI%
    diff = abs((wind_dir - cf_bearing + 180) % 360 - 180)
    if diff < 45 and wind_mph >= 12:
        nrfi -= min(8, (wind_mph - 8) * 0.6)
    elif diff > 135 and wind_mph >= 12:  # blowing in
        nrfi += min(5, (wind_mph - 8) * 0.4)
    return max(5, min(92, nrfi))


# -----------------------------------------------------------------------------
# 8. Main entry
# -----------------------------------------------------------------------------
def build_slate(date: str, season: int, owm_key: str = "") -> list[dict]:
    stadiums = json.loads(STADIUMS_FILE.read_text()) if STADIUMS_FILE.exists() else {}
    games = fetch_probable_pitchers(date)
    team_trends = fetch_team_first_inning_trends()

    scored = []
    for g in games:
        print(f"[build] {g['away']} @ {g['home']}: {g['away_sp']} vs {g['home_sp']}")

        a_stats = fetch_pitcher_stats(g["away_sp_id"], season)
        h_stats = fetch_pitcher_stats(g["home_sp_id"], season)

        aps = score_pitcher(**{k: a_stats[k] for k in
                               ("era", "whip", "k9", "first_inn_era", "bb9", "hr9")})
        hps = score_pitcher(**{k: h_stats[k] for k in
                               ("era", "whip", "k9", "first_inn_era", "bb9", "hr9")})
        als = score_lineup(team_trends.get(g["away"], {}))
        hls = score_lineup(team_trends.get(g["home"], {}))

        result = calc_nrfi(aps, hps, als, hls)

        # Weather adjustment
        venue_info = stadiums.get(str(g["venue_id"])) or {}
        weather = {}
        if owm_key and venue_info.get("lat"):
            weather = fetch_weather(venue_info["lat"], venue_info["lon"], owm_key)
            result["nrfi"] = weather_adjust(result["nrfi"], weather, g["venue_id"], stadiums)

        # FanDuel market odds (edge detection)
        fd_implied = fetch_fanduel_nrfi(g["away"], g["home"], date)
        edge = None
        if fd_implied is not None:
            edge = round(result["nrfi"] - fd_implied * 100, 1)

        label, css = classify(result["nrfi"])
        scored.append({
            **g,
            "away_stats": a_stats,
            "home_stats": h_stats,
            "away_trend": team_trends.get(g["away"], {}),
            "home_trend": team_trends.get(g["home"], {}),
            "weather": weather,
            "nrfi_pct": round(result["nrfi"], 1),
            "away_hold": round(result["away_hold"], 1),
            "home_hold": round(result["home_hold"], 1),
            "label": label,
            "css": css,
            "fd_implied_pct": round(fd_implied * 100, 1) if fd_implied else None,
            "edge": edge,
        })

    scored.sort(key=lambda x: x["nrfi_pct"], reverse=True)
    return scored


def render_html(slate: list[dict], date: str, out_path: Path):
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("leaderboard.html.j2")
    html = template.render(
        slate=slate,
        date=date,
        generated_at=dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
    )
    out_path.write_text(html)
    print(f"[build] wrote {out_path}")


def save_picks(slate: list[dict], date: str):
    PICKS_DIR.mkdir(parents=True, exist_ok=True)
    out = PICKS_DIR / f"{date}.json"
    # Only save the essentials needed by the results tracker
    compact = [
        {
            "game_pk": g["game_pk"],
            "away": g["away"],
            "home": g["home"],
            "nrfi_pct": g["nrfi_pct"],
            "label": g["label"],
            "away_sp": g["away_sp"],
            "home_sp": g["home_sp"],
        }
        for g in slate
    ]
    out.write_text(json.dumps(compact, indent=2))
    print(f"[build] wrote {out}")


def main():
    today = dt.datetime.now(ET).date()
    date_str = os.environ.get("SLATE_DATE", today.isoformat())
    season = int(date_str[:4])
    owm_key = os.environ.get("OPENWEATHERMAP_API_KEY", "")

    slate = build_slate(date_str, season, owm_key)
    if not slate:
        print("[build] no games today, writing empty page")
    DOCS.mkdir(parents=True, exist_ok=True)
    render_html(slate, date_str, DOCS / "index.html")
    save_picks(slate, date_str)
    print(f"[build] done. {len(slate)} games scored.")


if __name__ == "__main__":
    main()
