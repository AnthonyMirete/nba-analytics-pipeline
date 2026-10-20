"""
simple_model.py (version 3): a simple prop model.

For every player prop in nba_props.csv (from save_props.py) it predicts the
player's stat using player_logs.csv (from get_stats.py):

    prediction = expected minutes x stat per minute

What it does:
  1. Regular-season games only (playoff minutes made every starter look like
     a 40-minute player and pushed everything toward Over).
  2. This season's games count more as they pile up, last season's less.
  3. Players whose team changed are flagged and left out of the top list,
     because their old numbers may not describe their new role.
  4. Players listed Out, Doubtful or Questionable on today's official injury
     report (from get_injuries.py) are left out too.
  5. Teammates out: when a rotation player is Out, his usual points (or
     rebounds, assists) are shared among his healthy teammates.
  6. Market check: each sportsbook's price is compared with Pinnacle (the
     sharpest book) at the same line, or with the average of the other books
     when Pinnacle doesn't have that line. The final chance mixes that market
     view with the model, so a book that's out of line stands out.

Run it after get_stats.py:
    python3 simple_model.py

It prints the biggest disagreements, saves today's comparison to
model_output.csv, and adds it to model_history.csv so grade.py can later
check how the model did.

Still a learning model: it ignores opponents and rest. Don't bet on it.
"""

import csv
import math
import os
import unicodedata
from datetime import datetime, timezone

import pandas as pd

try:
    import ml_model                      # needs scikit-learn; the formula alone is used without it
except ImportError:
    ml_model = None

PROPS_FILE = "nba_props.csv"
LOGS_FILE = "player_logs.csv"
OUTPUT_FILE = "model_output.csv"
HISTORY_FILE = "model_history.csv"
INJURIES_FILE = "injuries.csv"

PRIOR_MIN_GAMES = 10     # last season's minutes: average of their last 10 regular-season games
PRIOR_RATE_GAMES = 25    # last season's per-minute rate: from their last 25 regular-season games
TRUST_GAMES = 8          # after 8 games this season, this season and last season count equally
MIN_GAMES = 5            # skip players with fewer total games than this
SHOW_TOP = 15            # how many disagreements to print

MARKET_WEIGHT = 0.7      # final chance = 70% market (Pinnacle or average) + 30% model
SHARP_BOOK = "pinnacle"
MAX_MARGIN = 0.15        # drop lines whose Over + Under add up to more than a 15% bookmaker margin
                         # (exchange quotes like Over -330 / Under -225 aren't a real market)
SHARE_RECOVERED = 0.6    # teammates pick up 60% of an injured player's usual stat (to be tuned with real results)
OUT_STATUSES = {"Out", "Doubtful"}
FULL_GAME_PERIODS = {"", "full", "full_game", "fullgame", "game", "fg", "ft", "match", "nan"}

# Pick'em sites post fake "+100 / +100" prices, so they're left out of the market average.
NOT_REAL_PRICES = {"prizepicks", "underdog", "sleeper", "dabble", "betr", "parlayplay",
                   "pick6", "chalkboard", "fliff", "boom", "ownersbox", "draftkings_pick6"}

MARKET_TO_STAT = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "FG3M",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds": "PR",
    "player_points_assists": "PA",
    "player_rebounds_assists": "RA",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_turnovers": "TOV",
}

def add_combo_stats(logs):
    """Combo props (PRA, P+R, P+A, R+A) are sums of the box-score stats."""
    logs["PRA"] = logs["PTS"] + logs["REB"] + logs["AST"]
    logs["PR"] = logs["PTS"] + logs["REB"]
    logs["PA"] = logs["PTS"] + logs["AST"]
    logs["RA"] = logs["REB"] + logs["AST"]
    return logs


# The odds site uses full team names; NBA.com uses three-letter codes.
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
    "LA Clippers": "LAC",   # the injury report's spelling
}


def clean_name(name):
    """Make names match across sites: 'Nikola Jokić' and 'Nikola Jokic' -> 'nikola jokic'."""
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    words = [w for w in name.split() if w not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(words)


def american_to_prob(price):
    """Turn American odds into the chance they imply: -110 -> 0.524, +150 -> 0.400."""
    price = float(price)
    if price < 0:
        return -price / (-price + 100)
    return 100 / (price + 100)


def chance_over(line, mean, spread):
    """Chance a stat lands above the line, using a bell curve around our prediction."""
    z = (line - mean) / spread
    return 0.5 * (1 - math.erf(z / math.sqrt(2)))


def latest_lines():
    """Keep the most recent pre-game capture of each prop, Over and Under side by side."""
    props = pd.read_csv(PROPS_FILE)
    props["tip_time_utc"] = pd.to_datetime(props["tip_time_utc"], utc=True)
    props["captured_at_utc"] = pd.to_datetime(props["captured_at_utc"], utc=True)

    now = datetime.now(timezone.utc)
    props = props[props["tip_time_utc"] > now]          # only games that haven't started
    props = props[props["market"].isin(MARKET_TO_STAT)]
    # Full-game lines only: first-quarter / first-half props would be compared
    # with full-game predictions and look like huge (fake) edges.
    if "period" in props.columns:
        period = props["period"].fillna("").astype(str).str.lower().str.strip()
        props = props[period.isin(FULL_GAME_PERIODS)]

    newest = props.groupby("game_id")["captured_at_utc"].transform("max")
    props = props[props["captured_at_utc"] == newest]

    if "line_type" not in props.columns:
        props["line_type"] = ""
    props["line_type"] = props["line_type"].fillna("").astype(str)
    keys = ["game_id", "tip_time_utc", "home_team", "away_team",
            "bookmaker", "market", "player", "line_type"]
    over = props[props["side"] == "Over"][keys + ["line", "price"]]
    under = props[props["side"] == "Under"][keys + ["line", "price"]]
    return over.merge(under, on=keys + ["line"], suffixes=("_over", "_under"))


def todays_injuries():
    """Today's official injury report as a table (empty if we don't have today's)."""
    if not os.path.exists(INJURIES_FILE):
        return pd.DataFrame(columns=["player", "team", "status"])
    report = pd.read_csv(INJURIES_FILE)
    today = datetime.now().strftime("%Y-%m-%d")
    if report.empty or not str(report["report_time_et"].iloc[0]).startswith(today):
        print("Injury report is not from today, so injuries are being ignored. "
              "Run get_injuries.py first.")
        return pd.DataFrame(columns=["player", "team", "status"])
    report["name_key"] = report["player"].map(clean_name)
    report["team_code"] = report["team"].map(TEAM_CODES)
    return report


def teammate_boosts(profiles, injuries):
    """How much each team's healthy players should go up because teammates are Out.

    Only counts injured players who were part of the recent rotation (they
    played in or near the team's latest game). Players who've been out for
    weeks are already missing from everyone's recent numbers.
    Returns {team code: multiplier}, e.g. {"PHI": 1.12} = healthy 76ers +12%.
    """
    if injuries.empty or profiles.empty:
        return {}, {}
    out_keys = set(injuries[injuries["status"].isin(OUT_STATUSES)]["name_key"])
    team_latest = profiles.groupby("stats_team")["last_game"].max()
    boosts, missing_by_team = {}, {}
    for team, roster in profiles.groupby("stats_team"):
        recent = roster["last_game"] >= team_latest[team] - pd.Timedelta(days=10)
        rotation = roster[recent & (roster["exp_minutes"] >= 12)]
        missing = rotation[rotation["name_key"].isin(out_keys)]
        healthy = rotation[~rotation["name_key"].isin(out_keys)]
        if missing.empty or healthy["prediction"].sum() <= 0:
            continue
        lost = missing["prediction"].sum()
        boosts[team] = 1 + SHARE_RECOVERED * lost / healthy["prediction"].sum()
        missing_by_team[team] = list(missing["name_key"])
    return boosts, missing_by_team


def market_average(out):
    """For each row, the average no-vig Over chance at the OTHER real sportsbooks, same line."""
    real = out[~out["bookmaker"].isin(NOT_REAL_PRICES)]
    keys = ["tip_time_utc", "market", "player", "line"]
    totals = real.groupby(keys)["book_over_chance"].agg(["sum", "count"]).reset_index()
    merged = out[keys + ["bookmaker", "book_over_chance"]].merge(totals, on=keys, how="left")
    own_is_real = ~merged["bookmaker"].isin(NOT_REAL_PRICES)
    others_sum = merged["sum"] - merged["book_over_chance"].where(own_is_real, 0)
    others_count = merged["count"] - own_is_real.astype(int)
    average = (others_sum / others_count).where(others_count > 0)

    # Pinnacle at the same line, when it has one (and the row isn't Pinnacle itself).
    sharp = out[out["bookmaker"] == SHARP_BOOK][keys + ["book_over_chance"]]
    sharp = sharp.drop_duplicates(keys).rename(columns={"book_over_chance": "sharp"})
    merged = merged.merge(sharp, on=keys, how="left")
    use_sharp = merged["sharp"].notna() & (merged["bookmaker"] != SHARP_BOOK)
    market = merged["sharp"].where(use_sharp, average)
    source = pd.Series("average", index=merged.index).where(~use_sharp, "pinnacle")
    source = source.where(market.notna(), "")
    return market.values, others_count.fillna(0).astype(int).values, source.values


def blend(current_value, current_games, prior_value):
    """Mix this season and last season: more weight on this season as games pile up."""
    if current_games == 0 or pd.isna(current_value):
        return prior_value
    if pd.isna(prior_value):
        return current_value
    weight = current_games / (current_games + TRUST_GAMES)
    return weight * current_value + (1 - weight) * prior_value


def player_profiles(logs, stat):
    """For each player: expected minutes, stat per minute, prediction and game-to-game swing."""
    logs = logs[(logs["MIN"] > 0) & (logs["SEASON_TYPE"] == "Regular Season")]
    logs = logs.sort_values("GAME_DATE")
    this_season = logs["SEASON_ID"].max()

    rows = []
    for player_id, games in logs.groupby("PLAYER_ID"):
        if len(games) < MIN_GAMES:
            continue
        current = games[games["SEASON_ID"] == this_season]
        prior = games[games["SEASON_ID"] != this_season]
        prior_min = prior.tail(PRIOR_MIN_GAMES)
        prior_rate = prior.tail(PRIOR_RATE_GAMES)

        exp_minutes = blend(
            current["MIN"].mean() if len(current) else float("nan"),
            len(current),
            prior_min["MIN"].mean() if len(prior_min) else float("nan"),
        )
        per_minute = blend(
            current[stat].sum() / current["MIN"].sum() if len(current) else float("nan"),
            len(current),
            prior_rate[stat].sum() / prior_rate["MIN"].sum() if len(prior_rate) else float("nan"),
        )
        prediction = exp_minutes * per_minute
        recent = games.tail(PRIOR_RATE_GAMES)
        swing = max(recent[stat].std(), math.sqrt(max(prediction, 1)), 1.5)

        rows.append({
            "name_key": clean_name(games["PLAYER_NAME"].iloc[-1]),
            "stats_team": games["TEAM_ABBREVIATION"].iloc[-1],
            "games_this_season": len(current),
            "exp_minutes": round(exp_minutes, 1),
            "prediction": round(prediction, 1),
            "swing": swing,
            "last_game": games["GAME_DATE"].iloc[-1],
        })
    return pd.DataFrame(rows)


# Which box-score stats make up each prop (combos are sums).
MARKET_PARTS = {
    "player_points": ["PTS"], "player_rebounds": ["REB"], "player_assists": ["AST"],
    "player_threes": ["FG3M"], "player_steals": ["STL"], "player_blocks": ["BLK"],
    "player_turnovers": ["TOV"], "player_points_rebounds_assists": ["PTS", "REB", "AST"],
    "player_points_rebounds": ["PTS", "REB"], "player_points_assists": ["PTS", "AST"],
    "player_rebounds_assists": ["REB", "AST"],
}


def ml_predictions(logs, lines, injuries):
    """Machine-learning predictions for tonight's players, keyed by cleaned name (or empty)."""
    saved = ml_model.load() if ml_model else None
    if saved is None:
        print("Machine-learning model not available (run ml_model.py); using the formula only.")
        return pd.DataFrame()
    regular = logs[logs["SEASON_TYPE"] == "Regular Season"].sort_values("GAME_DATE")
    latest = regular.groupby("PLAYER_ID").tail(1).copy()
    latest["name_key"] = latest["PLAYER_NAME"].map(clean_name)
    games = lines.drop_duplicates("player")[["player", "home_team", "away_team", "tip_time_utc"]].copy()
    games["name_key"] = games["player"].map(clean_name)
    games = games.merge(latest[["name_key", "PLAYER_ID", "TEAM_ABBREVIATION"]], on="name_key")
    games["home_code"] = games["home_team"].map(TEAM_CODES)
    games["away_code"] = games["away_team"].map(TEAM_CODES)
    games = games[(games["TEAM_ABBREVIATION"] == games["home_code"]) | (games["TEAM_ABBREVIATION"] == games["away_code"])]
    if games.empty:
        return pd.DataFrame()
    players = pd.DataFrame({
        "PLAYER_ID": games["PLAYER_ID"], "team": games["TEAM_ABBREVIATION"],
        "home": (games["TEAM_ABBREVIATION"] == games["home_code"]).astype(int),
        "opponent": games["away_code"].where(games["TEAM_ABBREVIATION"] == games["home_code"], games["home_code"]),
        "game_date": games["tip_time_utc"].dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize(),
    })
    out_ids = []
    if len(injuries):
        out_keys = set(injuries[injuries["status"].isin(OUT_STATUSES)]["name_key"])
        out_ids = list(latest[latest["name_key"].isin(out_keys)]["PLAYER_ID"])
    preds = ml_model.predict_tonight(saved, logs, players, out_ids)
    preds = preds.merge(games[["PLAYER_ID", "name_key"]].drop_duplicates("PLAYER_ID"), on="PLAYER_ID")
    return preds.drop_duplicates("name_key")


def append_history(out):
    """Add today's rows to model_history.csv, even if older runs wrote fewer columns.

    Each model version added columns. Rows from older versions are lined up by
    column name (missing columns left empty) so the file stays readable.
    """
    if not os.path.exists(HISTORY_FILE):
        out.to_csv(HISTORY_FILE, index=False)
        return
    with open(HISTORY_FILE, newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    columns = list(out.columns)
    if header == columns and all(len(r) == len(header) for r in body):
        out.to_csv(HISTORY_FILE, mode="a", index=False, header=False)
        return
    layouts = {len(header): header, len(columns): columns}
    parts = [pd.DataFrame([r for r in body if len(r) == width], columns=names)
             for width, names in layouts.items() if any(len(r) == width for r in body)]
    unreadable = sum(1 for r in body if len(r) not in layouts)
    if unreadable:
        print(f"Note: {unreadable} old rows in {HISTORY_FILE} had an unknown layout and were dropped.")
    merged = pd.concat(parts + [out.astype(str)], ignore_index=True)
    merged = merged[columns + [c for c in merged.columns if c not in columns]]
    merged.to_csv(HISTORY_FILE, index=False)


def main():
    lines = latest_lines()
    if lines.empty:
        print("No upcoming props found in nba_props.csv. Run save_props.py before tip-off.")
        return

    logs = add_combo_stats(pd.read_csv(LOGS_FILE, parse_dates=["GAME_DATE"]))
    injuries = todays_injuries()
    ml = ml_predictions(logs, lines, injuries)
    results = []
    boost_notes = {}
    for market, stat in MARKET_TO_STAT.items():
        market_lines = lines[lines["market"] == market].copy()
        if market_lines.empty:
            continue
        profiles = player_profiles(logs, stat)
        boosts, missing = teammate_boosts(profiles, injuries)
        profiles["teammate_boost"] = profiles["stats_team"].map(boosts).fillna(1.0)
        profiles["prediction"] = (profiles["prediction"] * profiles["teammate_boost"]).round(1)
        if market == "player_points":
            boost_notes = {team: (boosts[team], missing[team]) for team in boosts}
        market_lines["name_key"] = market_lines["player"].map(clean_name)
        merged = market_lines.merge(profiles, on="name_key", how="left")
        merged["formula_prediction"] = merged["prediction"]
        merged["ml_prediction"] = float("nan")
        if len(ml):
            # Final prediction = average of the formula and the machine-learning model
            # (tested on the 2025-26 season: 1-3% more accurate than the formula alone).
            parts = ml[["name_key"] + [f"ml_{p}" for p in MARKET_PARTS[market]]]
            merged = merged.merge(parts, on="name_key", how="left")
            merged["ml_prediction"] = merged[[f"ml_{p}" for p in MARKET_PARTS[market]]].sum(axis=1, min_count=1)
            has_ml = merged["ml_prediction"].notna() & merged["prediction"].notna()
            merged.loc[has_ml, "prediction"] = ((merged.loc[has_ml, "formula_prediction"]
                                                 + merged.loc[has_ml, "ml_prediction"]) / 2).round(1)
            merged = merged.drop(columns=[f"ml_{p}" for p in MARKET_PARTS[market]])

        unmatched = merged[merged["prediction"].isna()]["player"].unique()
        if len(unmatched):
            print(f"[{market}] No stats found for {len(unmatched)} players "
                  f"(names didn't match or too few games): {', '.join(unmatched[:8])}")
        results.append(merged.dropna(subset=["prediction"]))

    if not results:
        print("No props could be matched to player stats.")
        return
    out = pd.concat(results, ignore_index=True)

    # Did the player's team change? Compare his last stats team with tonight's two teams.
    home = out["home_team"].map(TEAM_CODES)
    away = out["away_team"].map(TEAM_CODES)
    out["team_changed"] = (out["stats_team"] != home) & (out["stats_team"] != away)

    # Official injury report: who's Out, Doubtful, Questionable or Probable today.
    status = dict(zip(injuries["name_key"], injuries["status"])) if len(injuries) else {}
    out["injury_status"] = out["player"].map(clean_name).map(status).fillna("")
    # A boost only makes sense for players still on that team.
    out.loc[out["team_changed"], "teammate_boost"] = 1.0

    p_over = out["price_over"].map(american_to_prob)
    p_under = out["price_under"].map(american_to_prob)
    margin = p_over + p_under - 1
    junk = margin > MAX_MARGIN
    if junk.any():
        print(f"Ignored {junk.sum()} lines whose prices don't form a real two-way market "
              f"(margin over {MAX_MARGIN:.0%}), mostly from: "
              f"{', '.join(out[junk]['bookmaker'].value_counts().head(3).index)}")
    out, p_over, p_under = out[~junk].copy(), p_over[~junk], p_under[~junk]
    out["book_over_chance"] = p_over / (p_over + p_under)
    out["model_over_chance"] = [
        chance_over(line, mean, swing)
        for line, mean, swing in zip(out["line"], out["prediction"], out["swing"])
    ]
    # Market check: what do the other sportsbooks say at the same line?
    out["market_over_chance"], out["other_books"], out["market_source"] = market_average(out)
    market = out["market_over_chance"].fillna(out["book_over_chance"])
    out["final_over_chance"] = MARKET_WEIGHT * market + (1 - MARKET_WEIGHT) * out["model_over_chance"]
    out["difference"] = out["final_over_chance"] - out["book_over_chance"]
    out["model_leans"] = out["difference"].map(lambda d: "Over" if d > 0 else "Under")
    out["model_run_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    columns = ["model_run_utc", "tip_time_utc", "away_team", "home_team", "player",
               "market", "line_type", "bookmaker", "line", "price_over", "price_under",
               "exp_minutes", "prediction", "formula_prediction", "ml_prediction", "swing",
               "teammate_boost", "book_over_chance",
               "market_over_chance", "market_source", "other_books", "model_over_chance", "final_over_chance",
               "difference", "model_leans", "team_changed", "injury_status", "games_this_season",
               "stats_team", "last_game"]
    out = out[columns].sort_values("difference", key=abs, ascending=False)
    out["last_game"] = pd.to_datetime(out["last_game"]).dt.date
    out.to_csv(OUTPUT_FILE, index=False)
    append_history(out)

    hurt = out["injury_status"].isin(["Out", "Doubtful", "Questionable"])
    trusted = out[~out["team_changed"] & ~hurt & out["line_type"].isin(["", "main"])
                  & ~out["bookmaker"].isin(NOT_REAL_PRICES)]
    changed = out[out["team_changed"]]["player"].unique()
    injured = out[hurt].drop_duplicates("player")

    print(f"\nCompared {len(out)} props "
          f"({(out['model_leans'] == 'Over').sum()} lean Over, "
          f"{(out['model_leans'] == 'Under').sum()} lean Under).")
    if len(changed):
        print(f"Left out of the list below (team changed, old numbers may mislead): "
              f"{', '.join(changed)}")
    if len(injured):
        listed = ", ".join(f"{p} ({st})" for p, st in zip(injured["player"], injured["injury_status"]))
        print(f"Left out because of the injury report: {listed}")
    for team, (factor, names) in boost_notes.items():
        print(f"Teammates out for {team} ({', '.join(n.title() for n in names)}): "
              f"healthy {team} players' points raised {factor - 1:.0%}")
    print("\nBiggest disagreements with the books:\n")
    show = trusted.head(SHOW_TOP).copy()
    show["book"] = (show["book_over_chance"] * 100).round(0).astype(int).astype(str) + "%"
    show["model"] = (show["model_over_chance"] * 100).round(0).astype(int).astype(str) + "%"
    show["mkt"] = (show["market_over_chance"] * 100).round(0).map(
        lambda v: "-" if pd.isna(v) else f"{int(v)}%")
    show["final"] = (show["final_over_chance"] * 100).round(0).astype(int).astype(str) + "%"
    show["stat"] = show["market"].map({
        "player_points": "pts", "player_rebounds": "reb", "player_assists": "ast",
        "player_threes": "3pm", "player_points_rebounds_assists": "pra",
        "player_points_rebounds": "p+r", "player_points_assists": "p+a",
        "player_rebounds_assists": "r+a", "player_blocks": "blk", "player_steals": "stl",
        "player_turnovers": "tov"})
    show["type"] = show["line_type"].replace({"alternate": "alt", "": "main"})
    show["vs"] = show["market_source"].replace({"pinnacle": "pinn", "average": "avg"})
    print(show[["player", "stat", "type", "bookmaker", "line", "prediction", "book", "mkt",
                "vs", "model", "final", "model_leans"]].to_string(index=False))
    print("\nAll % = chance of going Over. book = this sportsbook; mkt = Pinnacle (vs pinn) or the "
          "average of the other books (vs avg);\nmodel = our prediction; final = 70% market + 30% model.")
    print(f"Full list in {OUTPUT_FILE}.")
    print("Reminder: this is a learning model. Don't bet on these.")


if __name__ == "__main__":
    main()
