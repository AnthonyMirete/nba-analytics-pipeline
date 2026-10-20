"""
app_data.py: everything the app reads and writes, in one place.

The app (app.py) only draws the screens; all file access lives here. To move
the app online later, only this file needs to change (e.g. read from a
database instead of the CSV files on this Mac).
"""

import os
import subprocess
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

import parlay

FOLDER = os.path.dirname(os.path.abspath(__file__))
MODEL_FILE = os.path.join(FOLDER, "model_output.csv")
HISTORY_FILE = os.path.join(FOLDER, parlay.HISTORY_FILE)
INJURIES_FILE = os.path.join(FOLDER, "injuries.csv")
LOG_FILE = os.path.join(FOLDER, "daily_log.txt")
EASTERN = ZoneInfo("America/New_York")

STAT_LABELS = {
    "player_points": "Points", "player_rebounds": "Rebounds", "player_assists": "Assists",
    "player_threes": "Threes", "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "P+R", "player_points_assists": "P+A",
    "player_rebounds_assists": "R+A", "player_blocks": "Blocks", "player_steals": "Steals",
    "player_turnovers": "Turnovers", "player_pra": "PRA",
    "moneyline": "Moneyline", "spread": "Spread", "other": "Other sport",
}
BET_TYPES = ["Single bet", "2-leg", "3-leg", "Underdog", "Other parlay"]
PROP_STATS = ["Points", "Rebounds", "Assists", "Threes", "PRA", "P+R", "P+A", "R+A",
              "Blocks", "Steals", "Turnovers"]
LABEL_TO_MARKET = {
    "Points": "player_points", "Rebounds": "player_rebounds", "Assists": "player_assists",
    "Threes": "player_threes", "PRA": "player_points_rebounds_assists",
    "P+R": "player_points_rebounds", "P+A": "player_points_assists",
    "R+A": "player_rebounds_assists", "Blocks": "player_blocks", "Steals": "player_steals",
    "Turnovers": "player_turnovers",
}
TEAMS = sorted(name for name in parlay.TEAM_CODES if name != "LA Clippers")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def history():
    """All logged parlays (the model's and yours), one row per leg."""
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame(columns=["source", "slate_date", "parlay_id", "parlay_type", "stake",
                                     "player", "market", "side", "line", "price", "leg_result"])
    h = pd.read_csv(HISTORY_FILE)
    if "source" not in h.columns:
        h["source"] = "model"
    h["source"] = h["source"].fillna("model")
    h["leg_result"] = h["leg_result"].fillna("").astype(str)
    return h


def describe_leg(leg):
    market = leg["market"]
    if market == "other":
        return f"{leg['player']} ({leg['side']})"
    if market == "moneyline":
        return f"{leg['player']} to win"
    if market == "spread":
        return f"{leg['player']} {float(leg['line']):+g}"
    return f"{leg['player']} {leg['side']} {leg['line']} {STAT_LABELS.get(market, market)}"


def parlay_odds(legs):
    decimal = 1.0
    for price in legs["price"]:
        decimal *= parlay.to_decimal(price)
    return decimal


def todays_model_parlays():
    """The model's newest parlays, each as a dict with its legs."""
    h = history()
    model = h[h["source"] == "model"]
    if model.empty:
        return [], None
    newest = model["slate_date"].astype(str).max()
    today = model[model["slate_date"].astype(str) == newest]
    out = []
    for kind in ["2-leg", "3-leg", "Underdog"]:
        legs = today[today["parlay_type"] == kind]
        if legs.empty:
            continue
        decimal = parlay_odds(legs)
        model_hit = pd.to_numeric(legs["model_chance"], errors="coerce").prod()
        book_hit = pd.to_numeric(legs["book_chance"], errors="coerce").prod()
        out.append({
            "type": kind, "stake": float(legs["stake"].iloc[0]), "decimal": decimal,
            "american": parlay.to_american(decimal),
            "payout": float(legs["stake"].iloc[0]) * decimal,
            "model_chance": model_hit, "book_chance": book_hit,
            "legs": [{"Leg": describe_leg(leg), "Odds": f"{int(leg['price']):+d}",
                      "Book": leg.get("bookmaker", ""),
                      "Result": leg["leg_result"] or "pending"} for _, leg in legs.iterrows()],
        })
    return out, newest


def settled_bets(source):
    """One row per finished parlay: date, type, stake, outcome, profit."""
    h = history()
    rows = []
    for parlay_id, legs in h[h["source"] == source].groupby("parlay_id"):
        result = parlay.settle(legs)
        if result:
            rows.append({"date": str(legs["slate_date"].iloc[0]), "type": legs["parlay_type"].iloc[0],
                         "stake": float(legs["stake"].iloc[0]), "outcome": result[0],
                         "profit": result[1],
                         "bet": " + ".join(describe_leg(l) for _, l in legs.iterrows())})
    return pd.DataFrame(rows, columns=["date", "type", "stake", "outcome", "profit", "bet"])


def record_summary(source):
    done = settled_bets(source)
    if done.empty:
        return pd.DataFrame(columns=["Type", "Won", "Lost", "Staked", "Profit"]), done
    rows = []
    for kind, part in done.groupby("type"):
        rows.append({"Type": kind, "Won": int((part["outcome"] == "Won").sum()),
                     "Lost": int((part["outcome"] == "Lost").sum()),
                     "Staked": part["stake"].sum(), "Profit": part["profit"].sum()})
    rows.append({"Type": "Total", "Won": int((done["outcome"] == "Won").sum()),
                 "Lost": int((done["outcome"] == "Lost").sum()),
                 "Staked": done["stake"].sum(), "Profit": done["profit"].sum()})
    return pd.DataFrame(rows), done


def bankroll_over_time():
    """Balance after each day, for you and the model (for the chart)."""
    series = {}
    for source, name in [("me", "You"), ("model", "Model (paper)")]:
        done = settled_bets(source)
        done = done[done["date"] >= parlay.BANKROLL_START_DATE]
        balance = {parlay.BANKROLL_START_DATE: parlay.STARTING_BANKROLL}
        running = parlay.STARTING_BANKROLL
        for day, profit in done.groupby("date")["profit"].sum().items():
            running += profit
            balance[day] = running
        series[name] = pd.Series(balance, dtype=float)
    chart = pd.DataFrame(series).sort_index().ffill()
    chart.index = pd.to_datetime(chart.index)
    return chart


def bankroll_and_stakes(source):
    h = history()
    now = parlay.balance(h, source)
    stakes, monday = parlay.this_weeks_stakes(h, source)
    return now, stakes, monday


def suggested_stake(kind):
    _, stakes, _ = bankroll_and_stakes("me")
    return {"Single bet": stakes["2-leg"], "2-leg": stakes["2-leg"], "3-leg": stakes["3-leg"],
            "Underdog": stakes["Underdog"], "Other parlay": stakes["3-leg"]}[kind]


def todays_props():
    """Today's model comparison, for the props explorer."""
    if not os.path.exists(MODEL_FILE):
        return pd.DataFrame()
    m = pd.read_csv(MODEL_FILE)
    m["tip_time_utc"] = pd.to_datetime(m["tip_time_utc"], utc=True)
    m = m[m["tip_time_utc"] > pd.Timestamp.now(tz="UTC")]
    if m.empty:
        return m
    m["Stat"] = m["market"].map(STAT_LABELS).fillna(m["market"])
    m["Game"] = m["away_team"] + " @ " + m["home_team"]
    m["Tip (ET)"] = m["tip_time_utc"].dt.tz_convert(EASTERN).dt.strftime("%a %I:%M %p")
    if "line_type" not in m.columns:
        m["line_type"] = ""
    m["line_type"] = m["line_type"].fillna("").replace("", "main")
    return m


def tonight_summary():
    """Number of upcoming games and the first tip-off time (Eastern), from saved props."""
    props = todays_props()
    if props.empty:
        return {"games": 0, "first_tip": None}
    first = props["tip_time_utc"].min().tz_convert(EASTERN)
    return {"games": props["Game"].nunique(), "first_tip": first.strftime("%I:%M %p").lstrip("0")}


def injuries_today():
    if not os.path.exists(INJURIES_FILE):
        return pd.DataFrame()
    report = pd.read_csv(INJURIES_FILE)
    if report.empty or not str(report["report_time_et"].iloc[0]).startswith(
            datetime.now(EASTERN).strftime("%Y-%m-%d")):
        return pd.DataFrame()
    return report


def pending_other_legs():
    h = history()
    waiting = h[(h["market"] == "other") & (h["leg_result"] == "")]
    return waiting


def last_run_summary():
    """When the daily routine last ran, from daily_log.txt."""
    if not os.path.exists(LOG_FILE):
        return None
    stamps = [line.strip("= \n") for line in open(LOG_FILE) if line.startswith("=====")]
    return stamps[-1] if stamps else None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def save_bet(kind, stake, game_date, legs):
    """Save one of your bets (same format log_bet.py uses). legs = list of dicts."""
    tip = datetime.combine(game_date, time(19, 0), tzinfo=EASTERN).astimezone(ZoneInfo("UTC"))
    logged_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
    parlay_id = f"{game_date}-me-{datetime.now().strftime('%H%M%S%f')}"
    rows = [{
        "source": "me", "slate_date": str(game_date), "parlay_id": parlay_id,
        "parlay_type": kind, "stake": float(stake), "created_utc": logged_at,
        "tip_time_utc": tip.isoformat(), "game": "", "player": leg["player"],
        "market": leg["market"], "side": leg["side"], "line": leg["line"],
        "price": int(leg["price"]), "bookmaker": "", "model_chance": "", "book_chance": "",
        "actual": "", "leg_result": "",
    } for leg in legs]
    existing = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()
    pd.concat([existing, pd.DataFrame(rows)], ignore_index=True).to_csv(HISTORY_FILE, index=False)
    return parlay_id


def settle_leg(row_index, result):
    """Mark one other-sport leg as Won / Lost / Push / Void."""
    h = pd.read_csv(HISTORY_FILE)
    h.loc[row_index, "leg_result"] = result
    h.to_csv(HISTORY_FILE, index=False)


def run_scripts(names):
    """Run some of the pipeline scripts in this folder; returns their combined output."""
    output = []
    for name in names:
        done = subprocess.run([sys.executable, name], cwd=FOLDER, capture_output=True,
                              text=True, timeout=600)
        output.append(f"$ python3 {name}\n{done.stdout}{done.stderr}")
    return "\n".join(output)
