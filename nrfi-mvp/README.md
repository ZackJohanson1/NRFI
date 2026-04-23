# NRFI Daily

Automated MLB No-Run-First-Inning leaderboard. Publishes each morning at 7 AM ET; grades itself the next day.

## What it does

1. Pulls today's probable pitchers from MLB's official API
2. Fetches each pitcher's 2026 stats (ERA, WHIP, K/9, first-inning ERA, BB/9, HR/9)
3. Scrapes team first-inning offensive trends from TeamRankings
4. Fetches live weather at outdoor stadiums (optional, needs free API key)
5. Scores each game and classifies as Strong NRFI / Lean NRFI / Toss-up / Lean YRFI
6. Renders a static HTML leaderboard to `docs/index.html`
7. The next morning, grades yesterday's picks by fetching play-by-play

## Architecture

```
nrfi-mvp/
├── scripts/
│   ├── build_slate.py       # runs 7 AM ET — builds today's slate
│   └── track_results.py     # runs 9 AM ET — grades yesterday's picks
├── templates/
│   └── leaderboard.html.j2  # Jinja template
├── data/
│   ├── stadiums.json        # coords + orientation for weather
│   └── picks/               # daily pick archive (JSON)
├── docs/                    # GitHub Pages publishes from here
│   ├── index.html           # today's leaderboard (generated)
│   └── results.json         # rolling accuracy log (generated)
├── .github/workflows/
│   └── daily.yml            # schedules both scripts
├── requirements.txt
└── README.md
```

## Setup (one-time, about 15 minutes)

### 1. Create the repo and push

```bash
git init nrfi-daily && cd nrfi-daily
# copy all files from this MVP into the repo
git add .
git commit -m "Initial NRFI MVP"
# create a repo on github.com, then:
git remote add origin git@github.com:YOURNAME/nrfi-daily.git
git push -u origin main
```

### 2. Enable GitHub Pages

- Repo → Settings → Pages
- Source: "Deploy from a branch"
- Branch: `main`, folder: `/docs`
- Save. Your site will be live at `https://YOURNAME.github.io/nrfi-daily/` within 2 minutes.

### 3. (Optional) Add weather

- Create a free account at openweathermap.org (1,000 calls/day free, way more than needed)
- Copy your API key
- Repo → Settings → Secrets and variables → Actions → New repository secret
- Name: `OPENWEATHERMAP_API_KEY`, value: your key

### 4. Test the first build manually

- Repo → Actions → "Build NRFI Daily Slate" → Run workflow
- Wait 2-3 minutes. You'll see a new commit with today's `docs/index.html`.

That's it. Tomorrow at 7 AM ET, it runs on its own.

## Running locally

```bash
pip install -r requirements.txt
python scripts/build_slate.py                 # today's slate
SLATE_DATE=2026-04-22 python scripts/build_slate.py  # specific date
open docs/index.html
```

## How the algorithm scores

**Pitcher score (10-90)** — first-inning ERA carries ~45% of the weight, up from ~26% in the v1 algorithm. This was the biggest lesson from the April 19 TEX@SEA miss: season ERA was 2.16 but Woo surrendered a first-pitch homer.

```
ERA:              <3=+10, <3.75=+5, <4.5=0, <5.5=-6, else=-12
WHIP:             <1.0=+6, <1.2=+3, <1.35=0, else=-5
K/9:              >10=+5, >8.5=+3, >7=0, else=-3
1st-inning ERA:   <2=+20, <3=+12, <4=+3, <5=-8, else=-18
BB/9 >= 4.0:      -5 (leadoff risk)
HR/9 >= 1.5:      -5 (leadoff HR risk)
```

**Lineup score** — derived from season NRFI% (TeamRankings).

**Combined NRFI%** — `(away_pitcher_holds / 100) × (home_pitcher_holds / 100) × 100`, clamped to 5-92%.

**Classification** — ≥65% Strong NRFI · ≥55% Lean NRFI · ≥45% Toss-up · <45% Lean YRFI.

**Weather** — wind ≥12 mph blowing toward CF at outdoor parks drops NRFI% by up to 8 points. Wind blowing in adds up to 5.

**Edge vs market** — if FanDuel's implied NRFI probability is successfully scraped, the card shows your model's gap vs. market. A +8 or more edge is where you'd actually want to wager.

## Cost

$0. GitHub Actions free tier (2,000 min/month) handles this with ~5 minutes/day of runtime. OpenWeatherMap free tier is 1,000 calls/day (we use ~15). MLB Stats API and TeamRankings are free and unkeyed.

## Known gaps / next upgrades

- **FanDuel NRFI scrape is stubbed.** FanDuel's page markup changes frequently and needs a proper browser automation setup (Playwright) or a paid odds API like The Odds API ($0/month for 500 req covers full MLB schedule). Until this is wired up, the "edge" column shows blank.
- **Lineup data is team-level.** Real lineups (who's leading off against this handedness?) would improve the leadoff-HR risk flag. MLB API exposes starting lineups ~1 hour before first pitch — you'd need to add a second run of the build at 6 PM ET for late cards.
- **Park factors are wind-only.** Add temperature, humidity, barometric pressure for a better model. The data is already in the weather payload, just needs a scoring rule.
- **No results UI.** `docs/results.json` is just JSON. Adding `docs/results.html` that reads the JSON and renders a dashboard (hit rate by tier, by day, by pitcher first-inning ERA bucket) is maybe 100 lines of HTML+JS.

## Publishing options beyond GitHub Pages

- **Email digest** — `scripts/send_email.py` using Resend free tier (3,000/month), triggered after build
- **Discord/Twitter bot** — post the top 3 picks with the card image each morning
- **RSS feed** — render a `feed.xml` alongside `index.html` for folks who want it in their reader

## Not financial advice

This is a tool for tracking and presenting data. It does not guarantee winning bets. First-inning run outcomes are high-variance — one pitch can change everything. Bet with money you can afford to lose.
