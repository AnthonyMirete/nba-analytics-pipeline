"""
game_lines.py: FanDuel's moneylines, spreads and totals next to Pinnacle's fair prices.

Pinnacle is the sharpest sportsbook, so its prices with the bookmaker's cut
removed are the best free estimate of each side's true chance. For every
FanDuel bet:
    edge = Pinnacle fair chance - chance implied by FanDuel's price
Positive means FanDuel's price is better than fair. If the two books hang
different spread or total numbers, Pinnacle's chance is shifted along a bell
curve (NBA margins swing about 12.5 points game to game, totals about 18).

Run it:   python3 game_lines.py
The app's "Game lines" page shows the same table.
"""

import os
from statistics import NormalDist

import pandas as pd

GAME_FILE = "game_lines.csv"
CLOSING_GAME_FILE = "closing_game_lines.csv"
PICK_BOOK, SHARP_BOOK = "fanduel", "pinnacle"
SPREAD_SWING, TOTAL_SWING = 12.5, 18.0
MAX_MARGIN = 0.15
STANDARD = NormalDist()


def implied(price):
    price = float(price)
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


def short(team):
    return str(team).split()[-1] if team else ""


def load(path=GAME_FILE, upcoming_only=True):
    if not os.path.exists(path):
        return pd.DataFrame()
    g = pd.read_csv(path, low_memory=False)
    g["tip_time_utc"] = pd.to_datetime(g["tip_time_utc"], utc=True)
    if upcoming_only:
        g = g[g["tip_time_utc"] > pd.Timestamp.now(tz="UTC")]
    if g.empty:
        return g
    newest = g.groupby("game_id")["captured_at_utc"].transform("max")
    g = g[g["captured_at_utc"] == newest]
    g = g[g["line_type"].fillna("").isin(["", "main"])]
    g["point"] = pd.to_numeric(g["point"], errors="coerce")
    g["price"] = pd.to_numeric(g["price"], errors="coerce")
    return g.dropna(subset=["price"])


def two_sides(rows, first, second, near=None):
    """The two sides of one book's market (e.g. home and away), or None if not both present.
    near: for spreads/totals, use the line closest to this number. Some books label a
    first-half total (e.g. 118.5) as a main line next to the real one (231.5)."""
    if near is not None and rows["point"].notna().any():
        target = rows.loc[(rows["point"].abs() - abs(near)).abs().idxmin(), "point"]
        rows = rows[rows["point"].abs() == abs(target)]
    a = rows[rows["selection"] == first]
    b = rows[rows["selection"] == second]
    if a.empty or b.empty:
        return None
    a, b = a.iloc[0], b.iloc[0]
    pa, pb = implied(a["price"]), implied(b["price"])
    if pa + pb - 1 > MAX_MARGIN:
        return None
    return a, b, pa / (pa + pb)


def shifted(prob_at_ref, ref_threshold, threshold, swing):
    """Chance a normally distributed quantity beats `threshold`, given its chance of beating `ref_threshold`."""
    p = min(max(prob_at_ref, 0.01), 0.99)
    center = ref_threshold - swing * STANDARD.inv_cdf(1 - p)
    return 1 - STANDARD.cdf((threshold - center) / swing)


def compare(g):
    """One row per FanDuel bet: game, market, bet, price, FanDuel chance, Pinnacle fair chance, edge."""
    rows = []
    for game_id, game in g.groupby("game_id"):
        home, away = game["home_team"].iloc[0], game["away_team"].iloc[0]
        tip = game["tip_time_utc"].iloc[0]
        label = f"{short(away)} @ {short(home)}"
        fd = game[game["bookmaker"] == PICK_BOOK]
        pin = game[game["bookmaker"] == SHARP_BOOK]
        if fd.empty or pin.empty:
            continue

        # Moneyline: chance the home team wins
        f, p = two_sides(fd[fd["market"] == "h2h"], home, away), two_sides(pin[pin["market"] == "h2h"], home, away)
        if f and p:
            for side, team, fair in [(0, home, p[2]), (1, away, 1 - p[2])]:
                rows.append({"tip": tip, "game": label, "market": "Moneyline", "bet": f"{short(team)} to win",
                             "team": team, "point": None, "price": int(f[side]["price"]), "fair": fair})

        # Spread: chance the home team covers. Home covers line L when margin > -L.
        f = two_sides(fd[fd["market"] == "spreads"], home, away)
        p = two_sides(pin[pin["market"] == "spreads"], home, away, near=f[0]["point"]) if f else None
        if f and p and abs(abs(p[0]["point"]) - abs(f[0]["point"])) <= 6:
            home_cover = shifted(p[2], -p[0]["point"], -f[0]["point"], SPREAD_SWING)
            for side, team, fair in [(0, home, home_cover), (1, away, 1 - home_cover)]:
                point = f[side]["point"]
                rows.append({"tip": tip, "game": label, "market": "Spread", "bet": f"{short(team)} {point:+g}",
                             "team": team, "point": point, "price": int(f[side]["price"]), "fair": fair})

        # Total: chance of the Over
        fd_totals = fd[fd["market"] == "totals"]
        f = two_sides(fd_totals, "Over", "Under", near=fd_totals["point"].max() if len(fd_totals) else None)
        p = two_sides(pin[pin["market"] == "totals"], "Over", "Under", near=f[0]["point"]) if f else None
        if f and p and abs(p[0]["point"] - f[0]["point"]) <= 10:
            over = shifted(p[2], p[0]["point"], f[0]["point"], TOTAL_SWING)
            for side, name, fair in [(0, "Over", over), (1, "Under", 1 - over)]:
                rows.append({"tip": tip, "game": label, "market": "Total", "bet": f"{name} {f[side]['point']:g}",
                             "team": name, "point": f[side]["point"], "price": int(f[side]["price"]), "fair": fair})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["fanduel_chance"] = out["price"].map(implied)
    out["edge"] = out["fair"] - out["fanduel_chance"]
    return out


def closing_fair(date, team, market, point):
    """Pinnacle's closing fair chance for one of your moneyline/spread legs, or None. Used by clv.py."""
    if not os.path.exists(CLOSING_GAME_FILE):
        return None
    g = load(CLOSING_GAME_FILE, upcoming_only=False)
    g = g[g["tip_time_utc"].dt.tz_convert("America/New_York").dt.date == date]
    g = g[(g["home_team"] == team) | (g["away_team"] == team)]
    pin = g[g["bookmaker"] == SHARP_BOOK]
    if pin.empty:
        return None
    home = pin["home_team"].iloc[0]
    away = pin["away_team"].iloc[0]
    if market == "moneyline":
        p = two_sides(pin[pin["market"] == "h2h"], home, away)
        return None if p is None else (p[2] if team == home else 1 - p[2])
    if market == "spread":
        p = two_sides(pin[pin["market"] == "spreads"], home, away, near=point)
        if p is None:
            return None
        home_point = point if team == home else -point
        home_cover = shifted(p[2], -p[0]["point"], -home_point, SPREAD_SWING)
        return home_cover if team == home else 1 - home_cover
    return None


def main():
    table = compare(load())
    if table.empty:
        print("No upcoming game lines with both FanDuel and Pinnacle prices. Run save_props.py first.")
        return
    table = table.sort_values("edge", ascending=False)
    print("FanDuel vs Pinnacle (edge = Pinnacle fair chance minus FanDuel's implied chance):\n")
    for _, r in table.iterrows():
        print(f"   {r['game']:12s} {r['market']:9s} {r['bet']:16s} {r['price']:+5d}   "
              f"FanDuel {r['fanduel_chance']:.1%}  Pinnacle fair {r['fair']:.1%}  edge {r['edge'] * 100:+.1f} pts")
    print("\nAbout -2 to -3 points is normal (FanDuel's cut). Positive means FanDuel's price is better than fair.")


if __name__ == "__main__":
    main()
