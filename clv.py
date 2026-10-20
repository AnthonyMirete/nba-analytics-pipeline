"""
clv.py: closing line value (CLV) of the model's picks and your NBA prop bets.

For every pick: CLV = the closing market's fair chance of that side, minus
the chance implied by the price the pick got. Positive CLV means the price
was better than where the market finished, which is the fastest reliable
sign of a real edge (a few hundred picks, versus thousands for win/loss).

The closing market is Pinnacle's no-vig price at the same line when it has
one, otherwise the average of the other real sportsbooks. If the line moved
to a different number, the chance is shifted along a bell curve.

Run it (the daily routine does):
    python3 clv.py
"""

import os
import unicodedata
from statistics import NormalDist

import pandas as pd

CLOSING_FILE = "closing_lines.csv"
MODEL_HISTORY = "model_history.csv"
PARLAY_HISTORY = "parlay_history.csv"
OUTPUT_FILE = "clv_picks.csv"

SHARP_BOOK = "pinnacle"
PICK_BOOK = "fanduel"
MIN_EDGE = 0.03
MAX_MARGIN = 0.15
NOT_REAL_PRICES = {"prizepicks", "underdog", "sleeper", "dabble", "betr", "parlayplay",
                   "pick6", "chalkboard", "fliff", "boom", "ownersbox", "draftkings_pick6"}
ALIASES = {"player_pra": "player_points_rebounds_assists"}
STANDARD = NormalDist()


def clean_name(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv"})


def implied(price):
    price = float(price)
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


def game_date(tip):
    return pd.to_datetime(tip, utc=True).dt.tz_convert("America/New_York").dt.date


def closing_market():
    """Fair (no-vig) closing chance of Over, per player / stat / line / sportsbook."""
    c = pd.read_csv(CLOSING_FILE, low_memory=False)
    c = c[~c["bookmaker"].isin(NOT_REAL_PRICES)]
    keys = ["tip_time_utc", "bookmaker", "market", "player", "line"]
    over = c[c["side"] == "Over"].drop_duplicates(keys)[keys + ["price", "line_type"]]
    under = c[c["side"] == "Under"].drop_duplicates(keys)[keys + ["price"]]
    pairs = over.merge(under, on=keys, suffixes=("_over", "_under"))
    po, pu = pairs["price_over"].map(implied), pairs["price_under"].map(implied)
    pairs = pairs[(po + pu - 1) <= MAX_MARGIN].copy()
    pairs["fair_over"] = (po / (po + pu))[pairs.index]
    pairs["date"] = game_date(pairs["tip_time_utc"])
    pairs["name_key"] = pairs["player"].map(clean_name)
    return pairs


def fair_close(closing, date, name_key, market, line, swing):
    """Closing fair chance of Over at this line, or None if the market wasn't captured.
    closing: dict of (date, name_key, market) -> that market's closing rows."""
    rows = closing.get((date, name_key, market))
    if rows is None or rows.empty:
        return None
    exact = rows[rows["line"] == line]
    sharp = exact[exact["bookmaker"] == SHARP_BOOK]
    if len(sharp):
        return float(sharp["fair_over"].iloc[0])
    if len(exact):
        return float(exact["fair_over"].mean())
    # The line moved: start from the sharpest main line and shift along a bell curve.
    main = rows[rows["line_type"].fillna("main").isin(["main", ""])]
    anchor = main[main["bookmaker"] == SHARP_BOOK]
    anchor = anchor if len(anchor) else main if len(main) else rows
    ref_line = float(anchor["line"].median())
    ref = anchor[anchor["line"] == ref_line]
    ref = ref if len(ref) else anchor
    p = min(max(float(ref["fair_over"].mean()), 0.01), 0.99)
    center = ref_line - swing * STANDARD.inv_cdf(1 - p)
    return 1 - STANDARD.cdf((line - center) / swing)


def swing_lookup(history):
    if history is None or "swing" not in history.columns:
        return {}
    h = history.dropna(subset=["swing"])
    return {(d, k, m): s for d, k, m, s in zip(h["date"], h["name_key"], h["market"], h["swing"])}


def picks_from_model(history):
    """The model's FanDuel main-line leans with an edge of 3%+, at its last run before each game."""
    h = history[(history["bookmaker"] == PICK_BOOK)]
    if "line_type" in h.columns:
        h = h[h["line_type"].fillna("").isin(["", "main"])]
    h = h[pd.to_numeric(h["difference"], errors="coerce").abs() >= MIN_EDGE]
    h = h.sort_values("model_run_utc").drop_duplicates(["tip_time_utc", "player", "market"], keep="last")
    return pd.DataFrame({
        "group": "Model picks (FanDuel, edge 3%+)", "date": h["date"], "name_key": h["name_key"],
        "player": h["player"], "market": h["market"], "line": h["line"].astype(float),
        "side": h["model_leans"],
        "price": h["price_over"].where(h["model_leans"] == "Over", h["price_under"]),
    })


def picks_from_parlays(parlays):
    p = parlays[parlays["market"].astype(str).str.startswith("player_")
                | parlays["market"].isin(["moneyline", "spread"])].copy()
    p["market"] = p["market"].replace(ALIASES)
    p["date"] = game_date(p["tip_time_utc"])
    p["name_key"] = p["player"].map(clean_name)
    is_game = p["market"].isin(["moneyline", "spread"])
    groups = p["source"].fillna("model").map({"model": "Model parlay legs", "me": "Your NBA prop legs"})
    groups = groups.where(~is_game, "Your NBA moneyline/spread legs")
    return pd.DataFrame({"group": groups, "date": p["date"], "name_key": p["name_key"], "player": p["player"],
                         "market": p["market"], "line": pd.to_numeric(p["line"], errors="coerce"),
                         "side": p["side"], "price": p["price"]})


def score():
    """All picks with their CLV (rows whose closing market wasn't captured are skipped)."""
    if not os.path.exists(CLOSING_FILE):
        return pd.DataFrame()
    closing_rows = closing_market()
    closing = {key: grp for key, grp in closing_rows.groupby(["date", "name_key", "market"])}
    history = None
    picks = []
    if os.path.exists(MODEL_HISTORY):
        history = pd.read_csv(MODEL_HISTORY, low_memory=False)
        history["date"] = game_date(history["tip_time_utc"])
        history["name_key"] = history["player"].map(clean_name)
        picks.append(picks_from_model(history))
    if os.path.exists(PARLAY_HISTORY):
        picks.append(picks_from_parlays(pd.read_csv(PARLAY_HISTORY)))
    if not picks:
        return pd.DataFrame()
    picks = pd.concat(picks, ignore_index=True)
    is_game = picks["market"].isin(["moneyline", "spread"])
    picks = picks[(is_game & picks["price"].notna()) | picks[["line", "price"]].notna().all(axis=1)]
    picks = picks[picks["date"].isin(set(closing_rows["date"]))]
    swings = swing_lookup(history)
    rows = []
    for _, p in picks.iterrows():
        if p["market"] in ("moneyline", "spread"):
            import game_lines
            fair_side = game_lines.closing_fair(p["date"], p["player"], p["market"],
                                                None if pd.isna(p["line"]) else float(p["line"]))
            if fair_side is None:
                continue
            rows.append({**p.to_dict(), "close_fair": fair_side, "price_implied": implied(p["price"]),
                         "clv": fair_side - implied(p["price"])})
            continue
        swing = float(swings.get((p["date"], p["name_key"], p["market"]), max(1.5, (p["line"] + 1) ** 0.5)))
        over = fair_close(closing, p["date"], p["name_key"], p["market"], p["line"], max(swing, 0.5))
        if over is None:
            continue
        fair_side = over if p["side"] == "Over" else 1 - over
        rows.append({**p.to_dict(), "close_fair": fair_side, "price_implied": implied(p["price"]),
                     "clv": fair_side - implied(p["price"])})
    return pd.DataFrame(rows)


def summary(scored):
    if scored.empty:
        return pd.DataFrame(columns=["group", "picks", "avg_clv", "beat_close"])
    out = scored.groupby("group").agg(
        picks=("clv", "size"), avg_clv=("clv", "mean"), beat_close=("clv", lambda c: (c > 0).mean()))
    return out.reset_index()


def main():
    scored = score()
    if scored.empty:
        print("No closing lines matched yet. They're saved before each tip-off by closing_lines.py.")
        return
    scored.to_csv(OUTPUT_FILE, index=False)
    print("Closing line value (CLV): closing fair chance minus the chance your price implied.")
    print("Positive = the price beat where the market closed. Around +2 points or more over a few")
    print("hundred picks is a strong sign of a real edge.\n")
    for _, r in summary(scored).iterrows():
        print(f"   {r['group']:34s} {r['picks']:5d} picks   avg CLV {r['avg_clv'] * 100:+.1f} pts   "
              f"beat the close {r['beat_close']:.0%}")
    print(f"\nEvery scored pick saved to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
