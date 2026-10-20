"""
app.py: the NBA model app. Opens in your browser.

One-time install:
    pip3 install streamlit
Start it (from the nba-model folder):
    streamlit run app.py
Stop it: press Ctrl+C in that Terminal window.

All data access is in app_data.py; this file only draws the screens.
"""

from datetime import date

import pandas as pd
import streamlit as st

import app_data as data

st.set_page_config(page_title="NBA Model", page_icon="🏀", layout="wide")

PAGES = ["Today", "Log a bet", "Settle", "Records", "Props explorer"]
page = st.sidebar.radio("Go to", PAGES)

last = data.last_run_summary()
st.sidebar.caption(f"Daily routine last ran: {last}" if last else "Daily routine hasn't run yet.")
st.sidebar.caption("Model picks are paper-tracked only.")


def money(value):
    return f"${value:,.2f}"


def money_columns(df, columns, signed=False):
    """Show dollar columns as text like $1,234.56 (or +$12.00)."""
    df = df.copy()
    for c in columns:
        df[c] = df[c].map(lambda v: (f"{v:+,.2f}" if signed else f"{v:,.2f}").replace(
            "+", "+$").replace("-", "-$") if signed else f"${v:,.2f}")
    return df


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
if page == "Today":
    st.title("Today's model parlays")
    st.warning("Paper tracking only. The model is still being tested. Don't bet on these yet.")

    parlays, slate = data.todays_model_parlays()
    if not parlays:
        st.info("No model parlays yet today. Use the buttons below or wait for the daily run.")
    else:
        st.caption(f"Slate: {slate}")
        for p in parlays:
            with st.container(border=True):
                left, mid, right = st.columns(3)
                left.subheader(f"{p['type']} parlay")
                mid.metric("Stake", money(p["stake"]))
                right.metric("Odds", p["american"], f"pays {money(p['payout'])}", delta_color="off")
                st.dataframe(pd.DataFrame(p["legs"]), hide_index=True, use_container_width=True)
                st.caption(f"Model chance {p['model_chance']:.0%} vs book chance {p['book_chance']:.0%}")

    injuries = data.injuries_today()
    if not injuries.empty:
        with st.expander(f"Today's injury report ({len(injuries)} players)"):
            st.dataframe(injuries[["matchup", "team", "player", "status", "reason"]],
                         hide_index=True, use_container_width=True)

    st.divider()
    st.subheader("Refresh")
    one, two = st.columns(2)
    if one.button("Rebuild picks from saved data (fast)"):
        with st.spinner("Running the model..."):
            st.session_state["run_output"] = data.run_scripts(["simple_model.py", "parlay.py"])
        st.rerun()
    if two.button("Full refresh: new stats, lines, injuries (about 2 minutes)"):
        with st.spinner("Downloading and running everything..."):
            st.session_state["run_output"] = data.run_scripts(
                ["get_stats.py", "save_props.py", "get_injuries.py", "simple_model.py",
                 "grade.py", "parlay.py"])
        st.rerun()
    if st.session_state.get("run_output"):
        with st.expander("Output of the last refresh"):
            st.code(st.session_state["run_output"])


# ---------------------------------------------------------------------------
# Log a bet
# ---------------------------------------------------------------------------
elif page == "Log a bet":
    st.title("Log a bet you placed")

    kind = st.selectbox("Bet type", data.BET_TYPES, index=1)
    default_legs = {"Single bet": 1, "2-leg": 2, "3-leg": 3}.get(kind, 2)
    leg_count = st.number_input("Number of legs", min_value=1, max_value=12, value=default_legs, step=1)
    suggested = data.suggested_stake(kind)
    stake = st.number_input("Stake you actually bet ($)", min_value=0.0, value=float(suggested), step=5.0)
    st.caption(f"This week's suggested stake for this bet type: {money(suggested)}")
    if stake > suggested + 0.01:
        st.warning(f"{money(stake)} is {money(stake - suggested)} more than this week's suggested "
                   f"{money(suggested)}. It will still be logged at {money(stake)}.")
    game_date = st.date_input("Game date", value=date.today())

    legs, problems = [], []
    for n in range(1, int(leg_count) + 1):
        with st.container(border=True):
            st.markdown(f"**Leg {n}**")
            leg_type = st.radio("Type", ["NBA player prop", "NBA moneyline", "NBA spread", "Other sport"],
                                horizontal=True, key=f"type{n}")
            cols = st.columns(4)
            if leg_type == "NBA player prop":
                player = cols[0].text_input("Player", key=f"player{n}")
                stat = cols[1].selectbox("Stat", data.PROP_STATS, key=f"stat{n}")
                side = cols[2].selectbox("Over / Under", ["Over", "Under"], key=f"side{n}")
                line = cols[3].number_input("Line", value=0.5, step=1.0, key=f"line{n}")
                leg = {"player": player.strip(), "market": data.LABEL_TO_MARKET[stat],
                       "side": side, "line": line}
            elif leg_type == "NBA moneyline":
                team = cols[0].selectbox("Team", data.TEAMS, key=f"team{n}")
                leg = {"player": team, "market": "moneyline", "side": "Win", "line": ""}
            elif leg_type == "NBA spread":
                team = cols[0].selectbox("Team", data.TEAMS, key=f"team{n}")
                spread = cols[1].number_input("Spread (e.g. -4.5)", value=-1.5, step=1.0, key=f"spread{n}")
                leg = {"player": team, "market": "spread", "side": "Spread", "line": spread}
            else:
                sport = cols[0].text_input("Sport", placeholder="Soccer", key=f"sport{n}")
                text = cols[1].text_input("Bet", placeholder="Barcelona to win", key=f"text{n}")
                leg = {"player": text.strip(), "market": "other", "side": sport.strip() or "Other", "line": ""}
            price = st.number_input("Odds (American, e.g. -140 or 120)", value=-110, step=5, key=f"price{n}")
            leg["price"] = int(price)
            if abs(int(price)) < 100:
                problems.append(f"Leg {n}: odds must be -100 or lower, or +100 or higher.")
            if not leg["player"]:
                problems.append(f"Leg {n}: fill in the player, team or bet.")
            legs.append(leg)

    if legs and not problems:
        decimal = 1.0
        for leg in legs:
            decimal *= data.parlay.to_decimal(leg["price"])
        st.info(f"{kind}: {money(stake)} at {data.parlay.to_american(decimal)}, "
                f"pays {money(stake * decimal)} if it hits.")
    for problem in problems:
        st.error(problem)

    if st.button("Save bet", type="primary", disabled=bool(problems) or stake <= 0):
        data.save_bet(kind, stake, game_date, legs)
        st.success("Saved. NBA legs are graded automatically; settle other-sport legs on the Settle page.")


# ---------------------------------------------------------------------------
# Settle
# ---------------------------------------------------------------------------
elif page == "Settle":
    st.title("Settle other-sport legs")
    st.caption("NBA legs are graded automatically. Mark the others here once their games are over.")
    waiting = data.pending_other_legs()
    if waiting.empty:
        st.success("Nothing waiting.")
    for index, leg in waiting.iterrows():
        with st.container(border=True):
            st.markdown(f"**{leg['player']}** ({leg['side']}, {int(leg['price']):+d}): "
                        f"{leg['parlay_type']} on {leg['slate_date']}")
            cols = st.columns(4)
            for col, result in zip(cols, ["Won", "Lost", "Push", "Void"]):
                if col.button(result, key=f"{index}-{result}"):
                    data.settle_leg(index, result)
                    st.rerun()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
elif page == "Records":
    st.title("Records and bankroll")
    cols = st.columns(2)
    for col, source, name in [(cols[0], "me", "You"), (cols[1], "model", "Model (paper)")]:
        now, stakes, monday = data.bankroll_and_stakes(source)
        col.metric(f"{name}: bankroll", money(now), f"{now - data.parlay.STARTING_BANKROLL:+,.2f} since start")
        col.caption(f"This week's stakes: 2-leg {money(stakes['2-leg'])}, 3-leg {money(stakes['3-leg'])}, "
                    f"underdog {money(stakes['Underdog'])}")

    chart = data.bankroll_over_time()
    if len(chart) > 1:
        st.subheader("Bankroll over time")
        st.line_chart(chart)

    for source, name in [("me", "Your bets"), ("model", "Model's paper parlays")]:
        summary, done = data.record_summary(source)
        st.subheader(name)
        if done.empty:
            st.caption("No finished bets yet.")
            continue
        shown = money_columns(money_columns(summary, ["Staked"]), ["Profit"], signed=True)
        st.dataframe(shown, hide_index=True, use_container_width=True)
        with st.expander("Every finished bet"):
            bets = money_columns(money_columns(done.sort_values("date", ascending=False), ["stake"]),
                                 ["profit"], signed=True)
            st.dataframe(bets, hide_index=True, use_container_width=True)
    st.caption("Parlays swing a lot: judge a record only after 100+ bets.")


# ---------------------------------------------------------------------------
# Props explorer
# ---------------------------------------------------------------------------
elif page == "Props explorer":
    st.title("Props explorer")
    props = data.todays_props()
    if props.empty:
        st.info("No upcoming props saved. Run a refresh from the Today page.")
    else:
        cols = st.columns(4)
        player = cols[0].text_input("Player contains")
        stats = cols[1].multiselect("Stat", sorted(props["Stat"].unique()))
        books = cols[2].multiselect("Sportsbook", sorted(props["bookmaker"].unique()), default=["fanduel"]
                                    if "fanduel" in set(props["bookmaker"]) else None)
        main_only = cols[3].checkbox("Main lines only", value=True)

        view = props
        if player:
            view = view[view["player"].str.contains(player, case=False, na=False)]
        if stats:
            view = view[view["Stat"].isin(stats)]
        if books:
            view = view[view["bookmaker"].isin(books)]
        if main_only:
            view = view[view["line_type"] == "main"]

        table = pd.DataFrame({
            "Game": view["Game"], "Tip (ET)": view["Tip (ET)"], "Player": view["player"],
            "Stat": view["Stat"], "Book": view["bookmaker"], "Line": view["line"],
            "Over": view["price_over"], "Under": view["price_under"],
            "Prediction": view["prediction"],
            "Book %": view["book_over_chance"] * 100,
            "Market %": view["market_over_chance"] * 100,
            "vs": view.get("market_source", ""),
            "Model %": view["model_over_chance"] * 100,
            "Final %": view["final_over_chance"] * 100,
            "Edge %": view["difference"] * 100,
            "Leans": view["model_leans"],
            "Injury": view["injury_status"].fillna(""),
        })
        st.caption(f"{len(table):,} props. All % = chance of going Over. Market = Pinnacle when it has "
                   "the same line, otherwise the other books' average. Final = 70% market + 30% model.")
        table = table.sort_values("Edge %", key=abs, ascending=False)
        for c in ["Book %", "Market %", "Model %", "Final %"]:
            table[c] = table[c].round(0)
        table["Edge %"] = table["Edge %"].round(1)
        st.dataframe(table, hide_index=True, use_container_width=True, height=600)
