"""
log_bet.py: record a parlay you actually bet, so it's graded next to the model's.

Run it right after you place a bet:
    python3 log_bet.py

It asks a few questions (type, stake, each leg), then saves the bet to
parlay_history.csv. The daily run grades it automatically once the games
are over, and shows your record next to the model's.

NBA legs are graded automatically: player props (points, rebounds, assists,
threes, and the combos PRA, P+R, P+A, R+A), moneylines and spreads.
Bets on any other sport can be logged too; you mark those won or lost
yourself afterwards with settle_bet.py.
"""

import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from parlay import HISTORY_FILE, STAKE_PERCENT, TEAM_CODES, this_weeks_stakes, to_american, to_decimal

STATS = [
    ("points", "player_points"), ("rebounds", "player_rebounds"),
    ("assists", "player_assists"), ("threes made", "player_threes"),
    ("PRA (pts+reb+ast)", "player_pra"), ("P+R", "player_points_rebounds"),
    ("P+A", "player_points_assists"), ("R+A", "player_rebounds_assists"),
]
EASTERN = ZoneInfo("America/New_York")


def ask(prompt, check=None, default=None):
    """Keep asking until the answer passes the check."""
    while True:
        answer = input(prompt).strip()
        if not answer and default is not None:
            return default
        if check is None:
            if answer:
                return answer
        else:
            try:
                return check(answer)
            except (ValueError, KeyError, IndexError):
                pass
        print("   Didn't catch that, try again.")


def choose(prompt, options):
    for number, option in enumerate(options, 1):
        print(f"   [{number}] {option}")
    return ask(prompt, lambda a: options[int(a) - 1])


def odds(answer):
    value = int(answer.replace("+", ""))
    if abs(value) < 100:
        raise ValueError
    return value


def money(answer):
    value = float(answer.replace("$", "").replace(",", ""))
    if value <= 0:
        raise ValueError
    return value


def game_date(answer):
    return datetime.strptime(answer, "%Y-%m-%d").date()


def team(answer):
    """Accepts 'Knicks', 'new york', 'NYK', etc."""
    text = answer.lower()
    matches = [name for name, code in TEAM_CODES.items()
               if text in name.lower() or text == code.lower()]
    if len(matches) != 1:
        raise ValueError
    return matches[0]


def ask_leg(number):
    print(f"\nLeg {number}:")
    kind = choose("   Type of leg: ", ["NBA player prop", "NBA moneyline", "NBA spread",
                                         "Other sport (any bet)"])
    if kind == "Other sport (any bet)":
        sport = ask("   Sport (e.g. Soccer, NFL, Tennis): ")
        text = ask("   Describe the bet (e.g. Barcelona to win): ")
        price = ask("   Odds for this leg (e.g. -140 or +120): ", odds)
        return {"player": text, "market": "other", "side": sport, "line": "",
                "price": price, "text": f"{text} ({sport})"}
    if kind == "NBA player prop":
        player = ask("   Player name (e.g. Jalen Brunson): ")
        label = choose("   Which stat: ", [label for label, _ in STATS])
        market = dict(STATS)[label]
        side = ask("   Over or under? (o/u): ",
                   lambda a: {"o": "Over", "over": "Over", "u": "Under", "under": "Under"}[a.lower()])
        line = ask("   Line (e.g. 24.5): ", float)
        text = f"{player} {side} {line} {label}"
    elif kind == "NBA moneyline":
        player = ask("   Team (e.g. Knicks): ", team)
        market, side, line = "moneyline", "Win", ""
        text = f"{player} to win"
    else:
        player = ask("   Team (e.g. Knicks): ", team)
        line = ask("   Spread for that team (e.g. -4.5 or +6): ", lambda a: float(a.replace("+", "")))
        market, side = "spread", "Spread"
        text = f"{player} {line:+g}"
    price = ask("   Odds for this leg (e.g. -140 or +120): ", odds)
    return {"player": player, "market": market, "side": side, "line": line,
            "price": price, "text": text}


def suggested_stake(kind):
    """This week's suggested stake for your bets, from the bankroll system in parlay.py."""
    history = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()
    if history.empty:
        history = pd.DataFrame(columns=["source", "slate_date", "parlay_id", "leg_result"])
    elif "source" not in history.columns:
        history["source"] = "model"
    stakes, bankroll = this_weeks_stakes(history, "me")
    by_kind = {"Single bet": stakes["2-leg"], "2-leg": stakes["2-leg"], "3-leg": stakes["3-leg"],
               "Underdog": stakes["Underdog"], "Other parlay": stakes["3-leg"]}
    return by_kind[kind], bankroll


def main():
    print("Log a bet you placed. Press Ctrl+C any time to cancel.\n")
    kind = choose("Bet type: ", ["Single bet", "2-leg", "3-leg", "Underdog", "Other parlay"])
    default_legs = {"Single bet": 1, "2-leg": 2, "3-leg": 3}.get(kind)
    hint = f" (Enter for {default_legs})" if default_legs else ""
    leg_count = ask(f"How many legs?{hint}: ", int, default=default_legs)
    suggested, bankroll = suggested_stake(kind)
    print(f"   This week's suggested stake for this bet: ${suggested:,.2f} "
          f"({STAKE_PERCENT:.1%} system, bankroll ${bankroll:,.2f} as of Monday)")
    stake = ask("Stake you actually bet, in dollars: ", money)
    if stake > suggested + 0.01:
        print(f"   WARNING: ${stake:,.2f} is more than the suggested ${suggested:,.2f} "
              f"(${stake - suggested:,.2f} over). It'll still be logged at ${stake:,.2f}.")
    today = datetime.now(EASTERN).date()
    date = ask(f"Game date (Enter for today, {today}, or type YYYY-MM-DD): ", game_date, default=today)

    legs = [ask_leg(n) for n in range(1, leg_count + 1)]

    decimal = 1.0
    for leg in legs:
        decimal *= to_decimal(leg["price"])
    print(f"\n{kind}, ${stake:,.2f} at {to_american(decimal)} "
          f"(pays ${stake * decimal:,.2f} if it hits), games on {date}:")
    for leg in legs:
        print(f"   {leg['text']} ({leg['price']:+d})")
    if input("\nSave this bet? (y/n): ").strip().lower() not in ("y", "yes"):
        print("Not saved.")
        return

    # Games are graded by date; 7pm Eastern stands in for the tip time.
    tip = datetime.combine(date, time(19, 0), tzinfo=EASTERN).astimezone(ZoneInfo("UTC"))
    logged_at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
    parlay_id = f"{date}-me-{datetime.now().strftime('%H%M%S')}"
    rows = [{
        "source": "me", "slate_date": str(date), "parlay_id": parlay_id,
        "parlay_type": kind, "stake": stake, "created_utc": logged_at,
        "tip_time_utc": tip.isoformat(), "game": "", "player": leg["player"],
        "market": leg["market"], "side": leg["side"], "line": leg["line"],
        "price": leg["price"], "bookmaker": "", "model_chance": "", "book_chance": "",
        "actual": "", "leg_result": "",
    } for leg in legs]

    history = pd.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else pd.DataFrame()
    pd.concat([history, pd.DataFrame(rows)], ignore_index=True).to_csv(HISTORY_FILE, index=False)
    if any(leg["market"] == "other" for leg in legs):
        print("Saved. NBA legs are graded automatically. For the other-sport legs, run\n"
              "   python3 settle_bet.py\n"
              "after the games and tell it whether each one won or lost.")
    else:
        print("Saved. It'll be graded automatically after the games.")


if __name__ == "__main__":
    main()
