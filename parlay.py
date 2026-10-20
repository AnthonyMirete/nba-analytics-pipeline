"""
parlay.py: builds today's parlays from the model's picks and tracks results.

Your system:
  - 2-leg parlay of favorites around -140
  - 3-leg parlay at half the 2-leg stake
  - underdog parlay at half the 3-leg stake

Stakes follow the bankroll: every Monday the 2-leg stake is reset to 2.5%
of the current balance (so it grows after a good week and shrinks after a
bad one). The model's paper bankroll and yours are tracked separately.

Each run it first grades any past parlays whose games are finished
(using player_logs.csv), then builds today's parlays from model_output.csv.

Run it after simple_model.py:
    python3 parlay.py

Everything is logged to parlay_history.csv. This is PAPER TRACKING:
nothing is bet, it just records what the model would have picked.
"""

import os
import unicodedata
from datetime import date, datetime, timedelta, timezone

import pandas as pd

# ---------------------------------------------------------------------------
# SETTINGS: change these to match how you bet
# ---------------------------------------------------------------------------
STARTING_BANKROLL = 3700.00        # your bankroll on the start date below
BANKROLL_START_DATE = "2026-10-20" # only bets from this date on change the balance
STAKE_PERCENT = 0.025              # 2-leg stake = 2.5% of the bankroll, reset every Monday
# The 3-leg is half the 2-leg, and the underdog parlay is half the 3-leg.

FAVORITE_RANGE = (-170, -115)      # legs for the 2- and 3-leg parlays (around -140)
DOG_MIN_PRICE = 100                # underdog legs must pay +100 or better
DOG_LEGS = 2                       # how many legs in the underdog parlay
MIN_EDGE = 0.03                    # only use legs where the model beats the book by 3%+
PARLAY_BOOK = "fanduel"            # only pick prices you can actually bet
# ---------------------------------------------------------------------------

MODEL_FILE = "model_output.csv"
LOGS_FILE = "player_logs.csv"
HISTORY_FILE = "parlay_history.csv"

MARKET_TO_STAT = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "FG3M",
    "player_pra": "PRA",                   # points + rebounds + assists
    "player_points_rebounds": "PR",
    "player_points_assists": "PA",
    "player_rebounds_assists": "RA",
}

# Injury statuses that keep a player out of the model's parlays.
SKIP_STATUSES = {"Out", "Doubtful", "Questionable"}


def clean_name(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    words = [w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(words)


def to_decimal(price):
    """American odds to decimal: -140 -> 1.714, +150 -> 2.50."""
    price = float(price)
    return 1 + (100 / -price if price < 0 else price / 100)


def to_american(decimal):
    """Decimal odds back to American: 2.94 -> +194."""
    if decimal >= 2:
        return f"+{round((decimal - 1) * 100)}"
    return f"{round(-100 / (decimal - 1))}"


# ---------------------------------------------------------------------------
# Building today's parlays
# ---------------------------------------------------------------------------

def candidate_legs():
    """One leg per player: the side the model leans, at the book where its edge is biggest."""
    model = pd.read_csv(MODEL_FILE)
    model["tip_time_utc"] = pd.to_datetime(model["tip_time_utc"], utc=True)
    model = model[model["tip_time_utc"] > datetime.now(timezone.utc)]
    model = model[model["bookmaker"] == PARLAY_BOOK]
    model = model[~model["team_changed"].astype(str).str.lower().eq("true")]
    if "injury_status" in model.columns:
        model = model[~model["injury_status"].fillna("").isin(SKIP_STATUSES)]

    is_over = model["model_leans"] == "Over"
    model["side"] = model["model_leans"]
    model["price"] = model["price_over"].where(is_over, model["price_under"])
    # Version 3 of the model mixes the market average in ("final"); older output doesn't have it.
    chance = model["final_over_chance"] if "final_over_chance" in model.columns else model["model_over_chance"]
    model["model_chance"] = chance.where(is_over, 1 - chance)
    model["book_chance"] = model["book_over_chance"].where(is_over, 1 - model["book_over_chance"])
    model["edge"] = model["model_chance"] - model["book_chance"]
    model["game"] = model["away_team"] + " @ " + model["home_team"]

    model = model[model["edge"] >= MIN_EDGE]
    model = model.sort_values("edge", ascending=False).drop_duplicates(["player", "market"])
    return model


def pick(legs, count):
    """Take the best legs by edge, using a different game for each leg when possible."""
    chosen, games = [], set()
    for _, leg in legs.iterrows():
        if leg["game"] not in games:
            chosen.append(leg)
            games.add(leg["game"])
        if len(chosen) == count:
            return chosen
    for _, leg in legs.iterrows():                      # not enough games: allow repeats
        if len(chosen) == count:
            break
        if not any(c["player"] == leg["player"] for c in chosen):
            chosen.append(leg)
    return chosen if len(chosen) == count else []


def build_today(legs, stakes):
    favorites = legs[legs["price"].between(*FAVORITE_RANGE)]
    dogs = legs[legs["price"] >= DOG_MIN_PRICE]
    plan = [
        ("2-leg", stakes["2-leg"], pick(favorites, 2)),
        ("3-leg", stakes["3-leg"], pick(favorites, 3)),
        ("Underdog", stakes["Underdog"], pick(dogs, DOG_LEGS)),
    ]
    run_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    slate = datetime.now().strftime("%Y-%m-%d")
    rows = []
    for name, stake, chosen in plan:
        if not chosen:
            print(f"\n{name}: not enough legs met the rules today (edge {MIN_EDGE:.0%}+, right price range). Skipped.")
            continue
        decimal = 1.0
        model_hit = book_hit = 1.0
        for leg in chosen:
            decimal *= to_decimal(leg["price"])
            model_hit *= leg["model_chance"]
            book_hit *= leg["book_chance"]
        parlay_id = f"{slate}-{name}"
        print(f"\n{name} parlay: ${stake:.2f} at {to_american(decimal)} "
              f"(pays ${stake * decimal:.2f}). Model chance {model_hit:.0%}, book chance {book_hit:.0%}")
        for leg in chosen:
            stat = leg["market"].replace("player_", "")
            print(f"   {leg['player']} {leg['side']} {leg['line']} {stat} "
                  f"({int(leg['price']):+d} at {leg['bookmaker']})  edge {leg['edge']:.0%}")
            rows.append({
                "source": "model",
                "slate_date": slate, "parlay_id": parlay_id, "parlay_type": name,
                "stake": stake, "created_utc": run_time,
                "tip_time_utc": leg["tip_time_utc"].isoformat(), "game": leg["game"],
                "player": leg["player"], "market": leg["market"], "side": leg["side"],
                "line": leg["line"], "price": int(leg["price"]), "bookmaker": leg["bookmaker"],
                "model_chance": round(leg["model_chance"], 4),
                "book_chance": round(leg["book_chance"], 4),
                "actual": "", "leg_result": "",
            })
    return slate, pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Grading past parlays (the model's and your own)
# ---------------------------------------------------------------------------

TEAM_CODES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


def load_logs():
    logs = pd.read_csv(LOGS_FILE, parse_dates=["GAME_DATE"])
    logs["game_date"] = logs["GAME_DATE"].dt.date
    logs["name_key"] = logs["PLAYER_NAME"].map(clean_name)
    logs["PRA"] = logs["PTS"] + logs["REB"] + logs["AST"]
    logs["PR"] = logs["PTS"] + logs["REB"]
    logs["PA"] = logs["PTS"] + logs["AST"]
    logs["RA"] = logs["REB"] + logs["AST"]
    return logs


def team_scores(logs):
    """Each team's final score per game, by adding up its players' points."""
    scores = logs.groupby(["GAME_ID", "game_date", "TEAM_ABBREVIATION"], as_index=False)["PTS"].sum()
    return scores


def grade_leg(leg, logs, scores):
    """Returns (actual result, 'Won' / 'Lost' / 'Push' / 'Void' / 'Check name')."""
    game_date = pd.Timestamp(leg["tip_time_utc"]).tz_convert("America/New_York").date()
    market = leg["market"]

    if market in ("moneyline", "spread"):
        code = TEAM_CODES.get(leg["player"])
        mine = scores[(scores["TEAM_ABBREVIATION"] == code) & (scores["game_date"] == game_date)]
        if mine.empty:
            return "", "Void"                            # no game found that day
        game = scores[(scores["GAME_ID"] == mine["GAME_ID"].iloc[0]) & (scores["game_date"] == game_date)]
        own = mine["PTS"].iloc[0]
        opp = game[game["TEAM_ABBREVIATION"] != code]["PTS"].iloc[0]
        actual = f"{own}-{opp}"
        margin = own - opp + (float(leg["line"]) if market == "spread" else 0)
        if margin == 0:
            return actual, "Push"
        return actual, "Won" if margin > 0 else "Lost"

    stat = MARKET_TO_STAT[market]
    key = clean_name(leg["player"])
    if key not in set(logs["name_key"]):
        return "", "Check name"                          # probably a typo in the name
    found = logs[(logs["name_key"] == key) & (logs["game_date"] == game_date)]
    if found.empty:
        return "", "Void"                                # didn't play: books void the leg
    actual = found[stat].iloc[0]
    line = float(leg["line"])
    if actual == line:
        return actual, "Push"
    won = (actual > line) == (leg["side"] == "Over")
    return actual, "Won" if won else "Lost"


def grade_history(history):
    """Fill in each leg's real result once its game is in player_logs.csv."""
    if history.empty or not os.path.exists(LOGS_FILE):
        return history
    logs = load_logs()
    scores = team_scores(logs)
    newest_game = logs["game_date"].max()

    history["leg_result"] = history["leg_result"].fillna("").astype(str)
    history["actual"] = history["actual"].astype(object)
    to_check = history["leg_result"].isin(["", "Check name"])
    for i, leg in history[to_check].iterrows():
        if leg["market"] == "other":
            continue                                     # other sports: settled by hand
        game_date = pd.Timestamp(leg["tip_time_utc"]).tz_convert("America/New_York").date()
        if game_date > newest_game:
            continue                                     # game not played yet
        actual, result = grade_leg(leg, logs, scores)
        history.at[i, "actual"] = actual
        history.at[i, "leg_result"] = result
    return history


def settle(legs):
    """Work out a finished parlay's outcome and profit, or None if still pending."""
    results = legs["leg_result"].astype(str)
    if (results == "").any() or (results == "Check name").any():
        return None
    stake = float(legs["stake"].iloc[0])
    if (results == "Lost").any():
        return "Lost", -stake
    live = legs[results == "Won"]                        # pushes and voids drop out
    if live.empty:
        return "Refund", 0.0
    decimal = 1.0
    for p in live["price"]:
        decimal *= to_decimal(p)
    return "Won", stake * (decimal - 1)


def balance(history, source, before=None):
    """Bankroll = starting amount + profit from settled bets since the start date."""
    total = STARTING_BANKROLL
    if history.empty:
        return total
    mine = history[(history["source"] == source)
                   & (history["slate_date"].astype(str) >= BANKROLL_START_DATE)]
    if before is not None:
        mine = mine[mine["slate_date"].astype(str) < str(before)]
    for parlay_id, legs in mine.groupby("parlay_id"):
        settled = settle(legs)
        if settled:
            total += settled[1]
    return total


def this_weeks_stakes(history, source):
    """Stakes for this week, set from the bankroll as it stood on Monday morning."""
    monday = date.today() - timedelta(days=date.today().weekday())
    bankroll = balance(history, source, before=monday)
    two_leg = max(5, round(bankroll * STAKE_PERCENT / 5) * 5)     # nearest $5
    return {"2-leg": float(two_leg), "3-leg": two_leg / 2, "Underdog": two_leg / 4}, bankroll


def report(history):
    if "source" not in history.columns:
        history["source"] = "model"
    history["source"] = history["source"].fillna("model")

    names = history[history["leg_result"] == "Check name"]["player"].unique()
    if len(names):
        print(f"Couldn't find these names in the stats (typo?): {', '.join(names)}. "
              f"Fix them in {HISTORY_FILE} and they'll be graded next run.")

    waiting = history[(history["market"] == "other") & (history["leg_result"].astype(str) == "")]
    if len(waiting):
        print(f"{len(waiting)} other-sport leg(s) waiting for a result. "
              f"Run  python3 settle_bet.py  to mark them won or lost.")

    for source, title in [("model", "Model's parlays (paper)"), ("me", "Your real bets")]:
        rows = []
        for parlay_id, legs in history[history["source"] == source].groupby("parlay_id"):
            settled = settle(legs)
            if settled:
                rows.append({"type": legs["parlay_type"].iloc[0],
                             "stake": float(legs["stake"].iloc[0]),
                             "outcome": settled[0], "profit": settled[1]})
        if not rows:
            continue
        done = pd.DataFrame(rows)
        print(f"\n{title}:")
        for kind, part in done.groupby("type"):
            wins = (part["outcome"] == "Won").sum()
            losses = (part["outcome"] == "Lost").sum()
            print(f"   {kind:12s} {wins}-{losses}   staked ${part['stake'].sum():,.2f}   "
                  f"profit ${part['profit'].sum():+,.2f}")
        print(f"   {'Total':12s} staked ${done['stake'].sum():,.2f}   "
              f"profit ${done['profit'].sum():+,.2f}   ({len(done)} bets)")
    print("\nParlays swing a lot: judge either record only after 100+ parlays.")


def bankroll_summary(history):
    print("\nBankroll:")
    for source, who in [("model", "Model (paper)"), ("me", "You")]:
        now = balance(history, source)
        stakes, monday_balance = this_weeks_stakes(history, source)
        print(f"   {who:14s} ${now:,.2f} now  (started ${STARTING_BANKROLL:,.2f}).  "
              f"This week's stakes: 2-leg ${stakes['2-leg']:.0f}, 3-leg ${stakes['3-leg']:.2f}, "
              f"underdog ${stakes['Underdog']:.2f}")
    print(f"   Stakes reset every Monday to {STAKE_PERCENT:.1%} of that morning's balance.")


def main():
    history = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()
    if not history.empty:
        if "source" not in history.columns:
            history["source"] = "model"
        history["source"] = history["source"].fillna("model")
        history = grade_history(history)
        report(history)
    else:
        history = pd.DataFrame(columns=["source", "slate_date", "parlay_id", "leg_result"])
    bankroll_summary(history)

    slate = datetime.now().strftime("%Y-%m-%d")
    if len(history):
        model_today = (history["source"] == "model") & (history["slate_date"] == slate)
        if (model_today & (history["leg_result"].astype(str) != "")).any():
            history.to_csv(HISTORY_FILE, index=False)
            print("\nToday's model parlays are already being graded; not building new ones.")
            return
        history = history[~model_today]                  # replace today's earlier picks

    legs = candidate_legs()
    print(f"\n{len(legs)} legs passed the {MIN_EDGE:.0%} edge filter today.")
    stakes, _ = this_weeks_stakes(history, "model")
    slate, today = build_today(legs, stakes)

    history = pd.concat([history, today], ignore_index=True)
    history.to_csv(HISTORY_FILE, index=False)
    print("\nPaper tracking only: these are logged, not bet. Don't bet on these yet.")


if __name__ == "__main__":
    main()
