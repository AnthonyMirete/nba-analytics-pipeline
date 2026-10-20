"""
injury_watch.py: phone alerts when a rotation player is newly ruled out.

Every 15 minutes on game days (setup_injury_watch.sh schedules it), it checks
the NBA's official injury report. When a player who has been in his team's
rotation is newly listed Out or Doubtful for a game that hasn't started, it:
  1. refreshes the prop lines and reruns the model with him removed
     (the model knows how much teammates usually pick up), and
  2. sends a notification to your phone through the free ntfy app with the
     teammates' FanDuel props the model now likes.

The paper parlays are NOT changed (they stay as picked at 1pm, for a fair record).

First-time setup:
    python3 injury_watch.py --test     shows the channel to subscribe to in ntfy and sends a test alert
Run once by hand:
    python3 injury_watch.py
"""

import json
import os
import secrets
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from config import ENV_FILE, get_secret

EASTERN = ZoneInfo("America/New_York")
STATE_FILE = "injury_watch_state.json"
INJURIES_FILE = "injuries.csv"
LOGS_FILE = "player_logs.csv"
PROPS_FILE = "nba_props.csv"
MODEL_FILE = "model_output.csv"
NTFY_SERVER = "https://ntfy.sh"

ALERT_STATUSES = {"Out", "Doubtful"}
MIN_EDGE = 0.03          # only mention teammate props where the model beats FanDuel by 3%+
START_HOUR = 10          # don't check before 10am Eastern
ROTATION_MINUTES = 15    # "rotation player" = averaging 15+ minutes over his last 10 games

TEAM_CODES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
STAT_WORDS = {
    "player_points": "pts", "player_rebounds": "reb", "player_assists": "ast", "player_threes": "3pm",
    "player_points_rebounds_assists": "PRA", "player_points_rebounds": "P+R",
    "player_points_assists": "P+A", "player_rebounds_assists": "R+A", "player_steals": "stl",
    "player_blocks": "blk", "player_turnovers": "tov",
}


def clean_name(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv"})


# ---------------------------------------------------------------------------
# Notifications (ntfy): the channel name is a secret kept in .env
# ---------------------------------------------------------------------------

def channel():
    topic = get_secret("NTFY_TOPIC")
    if topic:
        return topic
    topic = "nba-" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:22]
    with open(ENV_FILE, "a") as f:
        f.write(f"NTFY_TOPIC={topic}\n")
    os.chmod(ENV_FILE, 0o600)
    return topic


def notify(title, message, priority="high"):
    response = requests.post(f"{NTFY_SERVER}/{channel()}", data=message.encode("utf-8"), timeout=20,
                             headers={"Title": title.encode("utf-8"), "Priority": priority, "Tags": "basketball"})
    response.raise_for_status()


# ---------------------------------------------------------------------------
# Injury report and who counts as a rotation player
# ---------------------------------------------------------------------------

def current_report():
    """Newest official injury report as a table, also saved to injuries.csv for the model."""
    import get_injuries
    from nba_injury_report.report import _process_pdf
    stamp, url, pdf = get_injuries.newest_report()
    if pdf is None:
        return None
    table = pd.DataFrame([e.model_dump() for e in _process_pdf(pdf)]).rename(columns={
        "Game Date": "game_date", "Game Time": "game_time", "Matchup": "matchup", "Team": "team",
        "Player Name": "player", "Current Status": "status", "Reason": "reason"})
    table = table.dropna(subset=["player", "status"])
    table["player"] = table["player"].map(get_injuries.to_first_last)
    table["report_time_et"] = stamp.strftime("%Y-%m-%d %H:%M")
    table = table[["report_time_et", "game_date", "matchup", "team", "player", "status", "reason"]]
    table.to_csv(INJURIES_FILE, index=False)
    return table


def rotation_players():
    """name_key -> (team code, minutes over last 10) for players currently in a rotation."""
    logs = pd.read_csv(LOGS_FILE, parse_dates=["GAME_DATE"], low_memory=False)
    logs = logs[logs["SEASON_TYPE"] == "Regular Season"].sort_values("GAME_DATE")
    recent = logs.groupby("PLAYER_ID").tail(10)
    summary = recent.groupby("PLAYER_ID").agg(name=("PLAYER_NAME", "last"), team=("TEAM_ABBREVIATION", "last"),
                                              minutes=("MIN", "mean"), last_game=("GAME_DATE", "max"))
    team_latest = logs.groupby("TEAM_ABBREVIATION")["GAME_DATE"].max()
    summary = summary[summary["minutes"] >= ROTATION_MINUTES]
    summary = summary[summary["last_game"] >= summary["team"].map(team_latest) - pd.Timedelta(days=10)]
    return {clean_name(r["name"]): (r["team"], r["minutes"]) for _, r in summary.iterrows()}


def upcoming_games():
    """Matchups still to tip off today, from the saved prop lines: list of (home, away, tip time)."""
    if not os.path.exists(PROPS_FILE):
        return []
    props = pd.read_csv(PROPS_FILE, usecols=["home_team", "away_team", "tip_time_utc"], low_memory=False)
    props["tip"] = pd.to_datetime(props["tip_time_utc"], utc=True).dt.tz_convert(EASTERN)
    now = datetime.now(EASTERN)
    today = props[(props["tip"] > now) & (props["tip"] < now + timedelta(hours=18))]
    return list(today.drop_duplicates(["home_team", "away_team"])[["home_team", "away_team", "tip"]]
                .itertuples(index=False, name=None))


# ---------------------------------------------------------------------------
# Changes since the last check
# ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def new_absences(report, state, today):
    """Players newly Out/Doubtful since the last check (the first check of a day only records)."""
    statuses = {f"{clean_name(p)}|{t}": s for p, t, s in zip(report["player"], report["team"], report["status"])}
    first_check_today = state.get("date") != today
    previous = state.get("statuses", {})
    changes = []
    if not first_check_today:
        for key, status in statuses.items():
            if status in ALERT_STATUSES and previous.get(key) not in ALERT_STATUSES:
                name_key, team = key.split("|", 1)
                row = report[(report["player"].map(clean_name) == name_key) & (report["team"] == team)].iloc[0]
                changes.append({"name_key": name_key, "player": row["player"], "team": team,
                                "status": status, "reason": row["reason"], "was": previous.get(key, "not listed")})
    state.update(date=today, statuses=statuses)
    return changes


# ---------------------------------------------------------------------------
# What the model now likes for the teammates
# ---------------------------------------------------------------------------

def rerun_model():
    for script in ["save_props.py", "simple_model.py"]:
        subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=600)


def teammate_picks(team_code, out_name_key, limit=3):
    model = pd.read_csv(MODEL_FILE)
    model = model[(model["bookmaker"] == "fanduel") & (model["stats_team"] == team_code)]
    if "line_type" in model.columns:
        model = model[model["line_type"].fillna("").isin(["", "main"])]
    model = model[model["player"].map(clean_name) != out_name_key]
    model = model[~model["injury_status"].fillna("").isin(["Out", "Doubtful", "Questionable"])]
    model = model[model["difference"].abs() >= MIN_EDGE]
    model = model.reindex(model["difference"].abs().sort_values(ascending=False).index)
    model = model.drop_duplicates("player").head(limit)          # best prop for each teammate
    lines = []
    for _, r in model.iterrows():
        over = r["model_leans"] == "Over"
        chance = r["final_over_chance"] if over else 1 - r["final_over_chance"]
        book = r["book_over_chance"] if over else 1 - r["book_over_chance"]
        price = int(r["price_over"] if over else r["price_under"])
        lines.append(f"{r['player']} {r['model_leans']} {r['line']:g} {STAT_WORDS.get(r['market'], r['market'])}: "
                     f"model {chance:.0%} vs FanDuel {book:.0%} ({price:+d})")
    return lines


def main():
    if "--test" in sys.argv:
        topic = channel()
        notify("NBA model test", "Alerts are working. You'll get one when a rotation player is ruled out.",
               priority="default")
        print(f"Test alert sent. In the ntfy app, tap + and subscribe to this channel:\n\n    {topic}\n")
        print("Keep it private: anyone who knows this name can read your alerts.")
        return

    now = datetime.now(EASTERN)
    games = upcoming_games()
    if now.hour < START_HOUR or not games:
        print(f"{now:%H:%M}: nothing to watch (before {START_HOUR}am or no games left today).")
        return

    report = current_report()
    if report is None:
        print(f"{now:%H:%M}: couldn't get the injury report this time.")
        return

    state = load_state()
    changes = new_absences(report, state, now.strftime("%Y-%m-%d"))
    rotation = rotation_players()
    playing_today = {TEAM_CODES.get(t) for home, away, _ in games for t in (home, away)}
    important = [c for c in changes
                 if c["name_key"] in rotation and TEAM_CODES.get(c["team"]) in playing_today]
    if not important:
        save_state(state)
        print(f"{now:%H:%M}: checked report ({len(report)} players); no new rotation absences.")
        return

    rerun_model()
    for c in important:
        code = TEAM_CODES.get(c["team"])
        home, away, tip = next(g for g in games if code in (TEAM_CODES.get(g[0]), TEAM_CODES.get(g[1])))
        minutes = rotation[c["name_key"]][1]
        picks = teammate_picks(code, c["name_key"])
        body = (f"{c['player']} ({c['team']}) now {c['status']}, was {c['was']}. "
                f"Averaging {minutes:.0f} min. {c['reason']}\n\n")
        body += ("Teammate props the model now likes (FanDuel):\n" + "\n".join(picks)) if picks else \
            "No teammate prop with a 3%+ edge right now."
        title = f"{c['player']} {c['status'].upper()}: {TEAM_CODES.get(away)} @ {TEAM_CODES.get(home)} {tip:%I:%M %p}".replace(" 0", " ")
        notify(title, body)
        print(f"{now:%H:%M}: alert sent for {c['player']} ({c['status']}).")
    save_state(state)


if __name__ == "__main__":
    main()
