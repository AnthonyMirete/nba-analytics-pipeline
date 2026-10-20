"""
ml_model.py: machine-learning predictions of player stats.

Instead of a hand-written formula, a gradient-boosted model learns how a
player's next game relates to what was known before it: recent minutes and
stats, season averages, rest, home/away and the opponent's defense.
One model per stat (points, rebounds, assists, threes, steals, blocks,
turnovers, minutes). Combos like PRA are the sum of their parts.

One-time install:
    pip3 install scikit-learn
Run it:
    python3 ml_model.py              # train on all games, save ml_models.pkl
    python3 ml_model.py --evaluate   # honest test: train on 2024-25, predict 2025-26

simple_model.py uses the saved models automatically when ml_models.pkl exists.
"""

import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

LOGS_FILE = "player_logs.csv"
MODEL_FILE = "ml_models.pkl"

TARGETS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "MIN"]
HISTORY_STATS = ["MIN", "PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA", "FTA", "PF"]
OPPONENT_STATS = ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA"]
WINDOWS = [5, 10, 25]
TRAIN_SEASON, TEST_SEASON = 22024, 22025

MODEL_SETTINGS = dict(loss="poisson", learning_rate=0.05, max_iter=400, max_leaf_nodes=31,
                      min_samples_leaf=40, l2_regularization=1.0, random_state=0)


# ---------------------------------------------------------------------------
# Features: everything known BEFORE each game (shift(1) keeps the game itself out)
# ---------------------------------------------------------------------------

def prepare(logs):
    logs = logs[logs["SEASON_TYPE"] == "Regular Season"].copy()
    logs["GAME_DATE"] = pd.to_datetime(logs["GAME_DATE"])
    logs["home"] = logs["MATCHUP"].str.contains("vs.", regex=False).astype(int)
    logs["opponent"] = logs["MATCHUP"].str.split().str[-1]
    return logs.sort_values(["PLAYER_ID", "GAME_DATE"]).reset_index(drop=True)


def opponent_defense(logs):
    """For each team and game: what that team's opponents scored against it over its last 10 games."""
    team_games = logs.groupby(["GAME_ID", "GAME_DATE", "TEAM_ABBREVIATION"], as_index=False)[OPPONENT_STATS].sum()
    other = team_games.rename(columns={"TEAM_ABBREVIATION": "other_team",
                                       **{s: f"{s}_against" for s in OPPONENT_STATS}})
    paired = team_games[["GAME_ID", "GAME_DATE", "TEAM_ABBREVIATION"]].merge(
        other.drop(columns="GAME_DATE"), on="GAME_ID")
    paired = paired[paired["TEAM_ABBREVIATION"] != paired["other_team"]]
    paired = paired.sort_values(["TEAM_ABBREVIATION", "GAME_DATE"])
    cols = [f"{s}_against" for s in OPPONENT_STATS]
    rolled = paired.groupby("TEAM_ABBREVIATION")[cols].transform(
        lambda x: x.shift(1).rolling(10, min_periods=3).mean())
    paired[[f"opp_allows_{s}" for s in OPPONENT_STATS]] = rolled.values
    return paired[["GAME_ID", "TEAM_ABBREVIATION"] + [f"opp_allows_{s}" for s in OPPONENT_STATS]]


def missing_teammates(df):
    """For each team-game: how much usual production was missing because rotation players sat.

    A player counts as missing if he played for the team in one of its previous 3 games,
    was averaging 15+ minutes, and is not in this game's box score. Returns per (GAME_ID, team):
    missing_MIN, missing_PTS, missing_REB, missing_AST, missing_count.
    """
    stats = ["MIN", "PTS", "REB", "AST"]
    rows = df[["GAME_ID", "GAME_DATE", "TEAM_ABBREVIATION", "PLAYER_ID"] + [f"{s}_last10" for s in stats]]
    out = []
    for team, team_rows in rows.groupby("TEAM_ABBREVIATION"):
        games = team_rows.groupby("GAME_ID")["GAME_DATE"].first().sort_values()
        by_game = {g: grp for g, grp in team_rows.groupby("GAME_ID")}
        recent = {}                               # player -> (games ago last seen, his averages)
        for game_id in games.index:
            here = by_game[game_id]
            present = set(here["PLAYER_ID"])
            missing = [v for pid, (ago, v) in recent.items() if pid not in present and ago < 3]
            totals = {f"missing_{s}": sum(v[i] for v in missing) for i, s in enumerate(stats)}
            totals.update(GAME_ID=game_id, TEAM_ABBREVIATION=team, missing_count=len(missing))
            out.append(totals)
            recent = {pid: (ago + 1, v) for pid, (ago, v) in recent.items()}
            for _, r in here.iterrows():
                if r["MIN_last10"] >= 15:
                    recent[r["PLAYER_ID"]] = (0, [r[f"{s}_last10"] for s in stats])
                else:
                    recent.pop(r["PLAYER_ID"], None)
    return pd.DataFrame(out).fillna(0)


def build_features(logs):
    """One row per player-game with only pre-game information. Returns (frame, feature names)."""
    df = logs.copy()
    by_player = df.groupby("PLAYER_ID")
    names = []
    for stat in HISTORY_STATS:
        prior = by_player[stat].shift(1)
        for n in WINDOWS:
            col = f"{stat}_last{n}"
            df[col] = prior.groupby(df["PLAYER_ID"]).transform(lambda x: x.rolling(n, min_periods=1).mean())
            names.append(col)
        col = f"{stat}_season"
        df[col] = prior.groupby([df["PLAYER_ID"], df["SEASON_ID"]]).transform(lambda x: x.expanding().mean())
        names.append(col)
    # per-minute rates over the last 25 games
    prior_min = by_player["MIN"].shift(1)
    min_sum = prior_min.groupby(df["PLAYER_ID"]).transform(lambda x: x.rolling(25, min_periods=1).sum())
    for stat in ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV", "FGA"]:
        stat_sum = by_player[stat].shift(1).groupby(df["PLAYER_ID"]).transform(
            lambda x: x.rolling(25, min_periods=1).sum())
        df[f"{stat}_per_min"] = stat_sum / min_sum.replace(0, np.nan)
        names.append(f"{stat}_per_min")
    # how settled the player's role is, rest and venue
    df["games_this_season"] = df.groupby(["PLAYER_ID", "SEASON_ID"]).cumcount()
    df["games_total"] = by_player.cumcount()
    df["rest_days"] = by_player["GAME_DATE"].diff().dt.days.clip(upper=10)
    df["back_to_back"] = (df["rest_days"] == 1).astype(int)
    df["min_trend"] = df["MIN_last5"] - df["MIN_last25"]
    names += ["games_this_season", "games_total", "rest_days", "back_to_back", "home", "min_trend"]
    # opponent defense
    defense = opponent_defense(df).rename(columns={"TEAM_ABBREVIATION": "opponent"})
    df = df.merge(defense, on=["GAME_ID", "opponent"], how="left")
    names += [f"opp_allows_{s}" for s in OPPONENT_STATS]
    # teammates missing tonight (learned redistribution instead of a guessed 60%)
    df = df.merge(missing_teammates(df), on=["GAME_ID", "TEAM_ABBREVIATION"], how="left")
    missing_cols = ["missing_MIN", "missing_PTS", "missing_REB", "missing_AST", "missing_count"]
    df[missing_cols] = df[missing_cols].fillna(0)
    names += missing_cols
    return df, names


# ---------------------------------------------------------------------------
# Training, uncertainty and the honest test
# ---------------------------------------------------------------------------

def fit(frame, names, target):
    usable = frame.dropna(subset=[target])
    usable = usable[usable["games_total"] >= 3]
    model = HistGradientBoostingRegressor(**MODEL_SETTINGS)
    model.fit(usable[names], usable[target])
    return model


def spread_table(predictions, actuals):
    """Typical miss size by prediction size: used as the 'swing' around each prediction."""
    bins = np.quantile(predictions, np.linspace(0, 1, 11))
    bins = np.unique(bins)
    centers, spreads = [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        inside = (predictions >= lo) & (predictions <= hi)
        if inside.sum() >= 30:
            centers.append(float(np.mean(predictions[inside])))
            spreads.append(float(np.std(actuals[inside] - predictions[inside])))
    return np.array(centers), np.array(spreads)


def formula_baseline(frame, stat):
    """The current hand-written model, rebuilt point-in-time: last-10 minutes x last-25 rate."""
    return frame["MIN_last10"] * frame.get(f"{stat}_per_min", np.nan)


def evaluate():
    logs = prepare(pd.read_csv(LOGS_FILE))
    frame, names = build_features(logs)
    train = frame[frame["SEASON_ID"] == TRAIN_SEASON]
    test = frame[(frame["SEASON_ID"] == TEST_SEASON) & (frame["games_total"] >= 3)]
    print(f"Training on {len(train):,} games from 2024-25, testing on {len(test):,} games from 2025-26.\n")
    rows = []
    for stat in ["PTS", "REB", "AST", "FG3M", "STL", "BLK", "TOV"]:
        model = fit(train, names, stat)
        ml = model.predict(test[names])
        base = formula_baseline(test, stat).to_numpy()
        actual = test[stat].to_numpy()
        ok = ~np.isnan(base)
        rows.append({
            "stat": stat,
            "formula_error": np.mean(np.abs(base[ok] - actual[ok])),
            "ml_error": np.mean(np.abs(ml[ok] - actual[ok])),
            "formula_bias": np.mean(base[ok] - actual[ok]),
            "ml_bias": np.mean(ml[ok] - actual[ok]),
        })
    table = pd.DataFrame(rows)
    table["better_by"] = (1 - table["ml_error"] / table["formula_error"]) * 100
    print("Average miss per game (lower is better), and bias (+ = predicts too high):\n")
    print(table.round(2).to_string(index=False))
    print("\nbetter_by = % smaller average miss for the machine-learning model.")


def train_and_save():
    logs = prepare(pd.read_csv(LOGS_FILE))
    frame, names = build_features(logs)
    saved = {"names": names, "models": {}, "spreads": {}}
    holdout = frame[frame["SEASON_ID"] == frame["SEASON_ID"].max()]
    for stat in TARGETS:
        # spread from an honest out-of-sample check, then the final model on everything
        earlier = frame[frame["GAME_DATE"] < holdout["GAME_DATE"].min()]
        if len(earlier) > 5000:
            check = fit(earlier, names, stat)
            test = holdout[holdout["games_total"] >= 3]
            saved["spreads"][stat] = spread_table(check.predict(test[names]), test[stat].to_numpy())
        saved["models"][stat] = fit(frame, names, stat)
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(saved, f)
    print(f"Trained {len(TARGETS)} models on {len(frame):,} games. Saved to {MODEL_FILE}.")


# ---------------------------------------------------------------------------
# Predicting tonight's games (used by simple_model.py)
# ---------------------------------------------------------------------------

def load():
    try:
        with open(MODEL_FILE, "rb") as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError, pickle.UnpicklingError, ModuleNotFoundError):
        return None


def swing_for(saved, stat, prediction):
    centers, spreads = saved["spreads"].get(stat, (np.array([]), np.array([])))
    if len(centers) == 0:
        return max(np.sqrt(max(prediction, 1)), 1.0)
    return float(np.interp(prediction, centers, spreads))


def predict_tonight(saved, logs, players, out_ids=()):
    """players: DataFrame with PLAYER_ID, team, opponent, home (1/0), game_date.
    out_ids: PLAYER_IDs listed Out/Doubtful on today's injury report.
    Returns one row per player with a prediction for every stat in TARGETS."""
    logs = prepare(logs)
    future = players.copy()
    future["GAME_ID"] = "tonight-" + future[["team", "opponent"]].apply(lambda r: "-".join(sorted(r)), axis=1)
    future["GAME_DATE"] = pd.to_datetime(future["game_date"])
    future["TEAM_ABBREVIATION"] = future["team"]
    # NBA season ids: 2 + the year the season starts (games from October on belong to the new season)
    start_year = np.where(future["GAME_DATE"].dt.month >= 10, future["GAME_DATE"].dt.year,
                          future["GAME_DATE"].dt.year - 1)
    future["SEASON_ID"] = 20000 + start_year
    future["SEASON_TYPE"] = "Regular Season"
    future["MATCHUP"] = np.where(future["home"] == 1, future["team"] + " vs. " + future["opponent"],
                                 future["team"] + " @ " + future["opponent"])
    combined = pd.concat([logs, future[["PLAYER_ID", "GAME_ID", "GAME_DATE", "TEAM_ABBREVIATION", "SEASON_ID",
                                        "SEASON_TYPE", "MATCHUP"]]], ignore_index=True)
    combined = prepare(combined)
    frame, names = build_features(combined)
    rows = frame[frame["GAME_ID"].astype(str).str.startswith("tonight-")].copy()

    # Who's missing tonight comes from the injury report, not from box scores:
    # rotation players (15+ minutes, played in one of the team's last 3 games) listed Out.
    history = frame[~frame["GAME_ID"].astype(str).str.startswith("tonight-")]
    latest = history.sort_values("GAME_DATE").groupby("PLAYER_ID").tail(1)
    team_games = history.groupby("TEAM_ABBREVIATION")["GAME_DATE"].apply(
        lambda d: sorted(d.unique())[-3:] if len(d) else [])
    for team in rows["TEAM_ABBREVIATION"].unique():
        recent_dates = set(team_games.get(team, []))
        gone = latest[(latest["PLAYER_ID"].isin(set(out_ids))) & (latest["TEAM_ABBREVIATION"] == team)
                      & (latest["GAME_DATE"].isin(recent_dates)) & (latest["MIN_last10"] >= 15)]
        mask = rows["TEAM_ABBREVIATION"] == team
        for stat in ["MIN", "PTS", "REB", "AST"]:
            rows.loc[mask, f"missing_{stat}"] = gone[f"{stat}_last10"].sum()
        rows.loc[mask, "missing_count"] = len(gone)

    out = rows[["PLAYER_ID"]].copy()
    for stat, model in saved["models"].items():
        out[f"ml_{stat}"] = model.predict(rows[saved["names"]])
    return out.reset_index(drop=True)


if __name__ == "__main__":
    if "--evaluate" in sys.argv:
        evaluate()
    else:
        train_and_save()
