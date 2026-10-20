"""
app.py: the NBA model app. Opens in your browser.

One-time install:
    pip3 install streamlit
Start it (from the nba-model folder):
    streamlit run app.py
Stop it: press Ctrl+C in that Terminal window.

All data access is in app_data.py; this file only draws the screens.
Colors live in .streamlit/config.toml; fonts and the bet-slip look in STYLE below.
"""

import html
from datetime import date

import pandas as pd
import streamlit as st

import app_data as data

st.set_page_config(page_title="NBA Model", page_icon="🏀", layout="wide")

# Court navy, floorboard blue, chalk, basketball orange, net grey, win green, loss red.
NAVY, BOARD, CHALK, ORANGE, NET, WIN, LOSS = (
    "#13233B", "#1B2F4D", "#EEF1F4", "#F26B1D", "#8A97A8", "#3DBE7A", "#E5484D")

STYLE = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&display=swap');

.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stMarkdown {{
  font-family: 'Barlow', sans-serif;
}}
.stApp h1, .stApp h2, .stApp h3,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] p {{
  font-family: 'Barlow Condensed', sans-serif;
  letter-spacing: 0.01em;
}}
.stApp h1 {{ font-size: 2.6rem; font-weight: 700; line-height: 1.05; }}
.stApp h2 {{ font-size: 1.7rem; font-weight: 600; }}
.stApp h3 {{ font-size: 1.3rem; font-weight: 600; }}
[data-testid="stMetricValue"] {{ font-weight: 600; }}
[data-testid="stSidebar"] {{ background: {BOARD}; }}

.note {{ color: {NET}; margin: -0.4rem 0 1.4rem; font-size: 1rem; }}

/* Scoreboard row */
.board {{
  display: flex; flex-wrap: wrap; gap: 0; margin: 0 0 1.6rem;
  border: 1px solid #2A4166; border-radius: 6px; overflow: hidden;
}}
.board > div {{ flex: 1 1 140px; padding: 0.7rem 1rem; border-right: 1px solid #2A4166; }}
.board > div:last-child {{ border-right: none; }}
.board .k {{ display: block; color: {NET}; font-size: 0.85rem; }}
.board .v {{ display: block; font-family: 'Barlow Condensed', sans-serif; font-size: 1.7rem;
             font-weight: 600; color: {CHALK}; line-height: 1.1; }}

/* Bet slips */
.slip {{
  position: relative; background: {BOARD}; border-radius: 10px;
  padding: 1rem 1.15rem 0.9rem; margin-bottom: 1rem;
}}
.slip-head {{ display: flex; justify-content: space-between; align-items: baseline; }}
.slip-type {{ font-family: 'Barlow Condensed', sans-serif; font-size: 1.45rem; font-weight: 600; color: {CHALK}; }}
.slip-odds {{ font-family: 'Barlow Condensed', sans-serif; font-size: 1.9rem; font-weight: 700; color: {ORANGE}; }}
.leg {{ display: flex; align-items: center; gap: 0.6rem; padding: 0.45rem 0;
        border-bottom: 1px solid #2A4166; font-size: 0.98rem; color: {CHALK}; }}
.legs .leg:last-child {{ border-bottom: none; }}
.leg .what {{ flex: 1; }}
.leg .price {{ color: {NET}; font-variant-numeric: tabular-nums; }}
.pill {{ font-size: 0.78rem; padding: 0.1rem 0.5rem; border-radius: 999px; border: 1px solid {NET}; color: {NET}; }}
.pill.won {{ border-color: {WIN}; color: {WIN}; }}
.pill.lost {{ border-color: {LOSS}; color: {LOSS}; }}
/* The tear line: dashed rule with a notch cut into each side */
.tear {{ position: relative; border-top: 2px dashed #34507A; margin: 0.8rem -1.15rem 0.7rem; }}
.tear::before, .tear::after {{
  content: ""; position: absolute; top: -11px; width: 20px; height: 20px;
  border-radius: 50%; background: {NAVY};
}}
.tear::before {{ left: -10px; }}
.tear::after {{ right: -10px; }}
.slip-foot {{ display: flex; justify-content: space-between; }}
.slip-foot .k {{ display: block; color: {NET}; font-size: 0.85rem; }}
.slip-foot .v {{ display: block; font-family: 'Barlow Condensed', sans-serif; font-size: 1.6rem;
                 font-weight: 600; color: {CHALK}; }}
.slip-foot .v.pay {{ color: {ORANGE}; text-align: right; }}
.slip-foot div:last-child .k {{ text-align: right; }}
.slip-chance {{ color: {NET}; font-size: 0.88rem; margin-top: 0.4rem; }}
</style>
"""
st.markdown(STYLE, unsafe_allow_html=True)


def money(value):
    """Dollar amount for Streamlit text (the backslash stops $...$ being read as math)."""
    return f"\\${value:,.2f}"


def money_plain(value):
    """Dollar amount for metrics and tables."""
    return f"${value:,.2f}"


def money_html(value):
    """Dollar amount inside HTML blocks (&#36; is a $ that Markdown leaves alone)."""
    return f"&#36;{value:,.2f}"


def money_columns(df, columns, signed=False):
    df = df.copy()
    for c in columns:
        if signed:
            df[c] = df[c].map(lambda v: f"{'+' if v >= 0 else '-'}${abs(v):,.2f}")
        else:
            df[c] = df[c].map(lambda v: f"${v:,.2f}")
    return df


def slip_html(p):
    legs = []
    for leg in p["legs"]:
        result = leg["Result"].lower()
        css = "won" if result == "won" else "lost" if result == "lost" else ""
        legs.append(
            f'<div class="leg"><span class="what">{html.escape(leg["Leg"])}</span>'
            f'<span class="price">{html.escape(leg["Odds"])}</span>'
            f'<span class="pill {css}">{html.escape(leg["Result"].capitalize())}</span></div>')
    return (
        f'<div class="slip">'
        f'<div class="slip-head"><span class="slip-type">{html.escape(p["type"])} parlay</span>'
        f'<span class="slip-odds">{html.escape(p["american"])}</span></div>'
        f'<div class="legs">{"".join(legs)}</div>'
        f'<div class="tear"></div>'
        f'<div class="slip-foot"><div><span class="k">Stake</span><span class="v">{money_html(p["stake"])}</span></div>'
        f'<div><span class="k">Pays</span><span class="v pay">{money_html(p["payout"])}</span></div></div>'
        f'<div class="slip-chance">Chance all legs hit: model {p["model_chance"]:.0%}, '
        f'sportsbook {p["book_chance"]:.0%}</div>'
        f'</div>')


PAGES = ["Today", "Game lines", "Log a bet", "Settle", "Records", "Props explorer"]
st.sidebar.markdown("## NBA Model")
page = st.sidebar.radio("Go to", PAGES, label_visibility="collapsed")
last = data.last_run_summary()
st.sidebar.caption(f"Daily routine last ran {last}" if last else "The daily routine hasn't run yet.")


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------
if page == "Today":
    st.title(date.today().strftime("%A, %B %-d"))
    st.markdown('<p class="note">Paper picks. The model is still being tested, so these '
                "aren't for real bets yet.</p>", unsafe_allow_html=True)

    tonight = data.tonight_summary()
    bankroll, stakes, _ = data.bankroll_and_stakes("me")
    st.markdown(
        '<div class="board">'
        f'<div><span class="k">Games tonight</span><span class="v">{tonight["games"]}</span></div>'
        f'<div><span class="k">First tip-off</span><span class="v">{tonight["first_tip"] or "None left"}</span></div>'
        f'<div><span class="k">Your bankroll</span><span class="v">{money_html(bankroll)}</span></div>'
        f'<div><span class="k">This week\'s 2-leg stake</span><span class="v">{money_html(stakes["2-leg"])}</span></div>'
        '</div>', unsafe_allow_html=True)

    parlays, slate = data.todays_model_parlays()
    if not parlays:
        st.info("No model parlays yet today. Rebuild picks below, or wait for the 1pm run.")
    else:
        for col, p in zip(st.columns(len(parlays)), parlays):
            col.markdown(slip_html(p), unsafe_allow_html=True)

    injuries = data.injuries_today()
    if not injuries.empty:
        out = injuries[injuries["status"].isin(["Out", "Doubtful"])]
        with st.expander(f"Injury report: {len(out)} out or doubtful, {len(injuries) - len(out)} other"):
            st.dataframe(injuries[["matchup", "team", "player", "status", "reason"]].rename(
                columns=str.capitalize), hide_index=True, use_container_width=True)

    st.subheader("Refresh")
    one, two = st.columns(2)
    if one.button("Rebuild picks from saved data", use_container_width=True):
        with st.spinner("Running the model..."):
            st.session_state["run_output"] = data.run_scripts(["simple_model.py", "parlay.py"])
        st.rerun()
    if two.button("Download new data and rebuild (about 2 min)", use_container_width=True):
        with st.spinner("Downloading stats, lines and injuries..."):
            st.session_state["run_output"] = data.run_scripts(
                ["get_stats.py", "save_props.py", "get_injuries.py", "ml_model.py", "simple_model.py",
                 "grade.py", "parlay.py"])
        st.rerun()
    if st.session_state.get("run_output"):
        with st.expander("Details of the last refresh"):
            st.code(st.session_state["run_output"])


# ---------------------------------------------------------------------------
# Game lines
# ---------------------------------------------------------------------------
elif page == "Game lines":
    st.title("Game lines")
    st.markdown('<p class="note">FanDuel\'s moneylines, spreads and totals next to Pinnacle\'s fair '
                "chance (its price with the cut removed). Edge is Pinnacle's fair chance minus the chance "
                "FanDuel's price implies. Around -2 to -3 is normal; positive means FanDuel's price is "
                "better than fair.</p>", unsafe_allow_html=True)
    lines = data.game_lines_table()
    if lines.empty:
        st.info("No game lines with both FanDuel and Pinnacle prices yet. Download new data from the Today page.")
    else:
        best = lines[lines["edge"] > 0]
        if len(best):
            st.success(f"{len(best)} FanDuel price(s) better than Pinnacle's fair line right now.")
        for market in ["Moneyline", "Spread", "Total"]:
            part = lines[lines["market"] == market].sort_values(["tip", "game"])
            if part.empty:
                continue
            st.subheader(market)
            st.dataframe(pd.DataFrame({
                "Game": part["game"], "Tip-off": part["Tip (ET)"], "Bet": part["bet"],
                "FanDuel": part["price"], "FanDuel %": part["fanduel_chance"] * 100,
                "Pinnacle fair %": part["fair"] * 100, "Edge": part["edge"] * 100,
            }), hide_index=True, use_container_width=True, column_config={
                "FanDuel": st.column_config.NumberColumn(format="%+d"),
                "FanDuel %": st.column_config.NumberColumn(format="%.1f%%"),
                "Pinnacle fair %": st.column_config.NumberColumn(format="%.1f%%"),
                "Edge": st.column_config.NumberColumn("Edge (points)", format="%+.1f"),
            })


# ---------------------------------------------------------------------------
# Log a bet
# ---------------------------------------------------------------------------
elif page == "Log a bet":
    st.title("Log a bet")
    st.markdown('<p class="note">Record a bet you placed. NBA legs are graded automatically.</p>',
                unsafe_allow_html=True)

    top = st.columns(4)
    kind = top[0].selectbox("Bet type", data.BET_TYPES, index=1)
    default_legs = {"Single bet": 1, "2-leg": 2, "3-leg": 3}.get(kind, 2)
    leg_count = top[1].number_input("Legs", min_value=1, max_value=12, value=default_legs, step=1)
    suggested = data.suggested_stake(kind)
    stake = top[2].number_input("Stake ($)", min_value=0.0, value=float(suggested), step=5.0)
    game_date = top[3].date_input("Game date", value=date.today())
    st.caption(f"Suggested stake for this bet type this week: {money(suggested)}")
    if stake > suggested + 0.01:
        st.warning(f"{money(stake)} is {money(stake - suggested)} over this week's suggested "
                   f"{money(suggested)}. It will still be logged at {money(stake)}.")

    legs, missing = [], []
    for n in range(1, int(leg_count) + 1):
        with st.container(border=True):
            leg_type = st.radio(f"Leg {n}", ["NBA player prop", "NBA moneyline", "NBA spread", "Other sport"],
                                horizontal=True, key=f"type{n}")
            cols = st.columns([3, 2, 2, 2, 2])
            if leg_type == "NBA player prop":
                player = cols[0].text_input("Player", key=f"player{n}")
                stat = cols[1].selectbox("Stat", data.PROP_STATS, key=f"stat{n}")
                side = cols[2].selectbox("Over or under", ["Over", "Under"], key=f"side{n}")
                line = cols[3].number_input("Line", value=0.5, step=1.0, key=f"line{n}")
                leg = {"player": player.strip(), "market": data.LABEL_TO_MARKET[stat], "side": side, "line": line}
            elif leg_type == "NBA moneyline":
                team = cols[0].selectbox("Team to win", data.TEAMS, key=f"team{n}")
                leg = {"player": team, "market": "moneyline", "side": "Win", "line": ""}
            elif leg_type == "NBA spread":
                team = cols[0].selectbox("Team", data.TEAMS, key=f"team{n}")
                spread = cols[1].number_input("Spread", value=-1.5, step=1.0, key=f"spread{n}")
                leg = {"player": team, "market": "spread", "side": "Spread", "line": spread}
            else:
                text = cols[0].text_input("Bet", placeholder="Barcelona to win", key=f"text{n}")
                sport = cols[1].text_input("Sport", placeholder="Soccer", key=f"sport{n}")
                leg = {"player": text.strip(), "market": "other", "side": sport.strip() or "Other", "line": ""}
            price = cols[4].number_input("Odds", value=-110, step=5, key=f"price{n}",
                                         help="American odds, e.g. -140 or 120")
            leg["price"] = int(price)
            if not leg["player"]:
                missing.append(f"leg {n}")
            elif abs(int(price)) < 100:
                missing.append(f"odds on leg {n} (must be -100 or lower, or 100 or higher)")
            legs.append(leg)

    ready = not missing and stake > 0
    if ready:
        decimal = 1.0
        for leg in legs:
            decimal *= data.parlay.to_decimal(leg["price"])
        st.markdown(f"**{kind}: {money(stake)} at {data.parlay.to_american(decimal)}, "
                    f"pays {money(stake * decimal)} if it hits.**")
    else:
        st.caption("Still to fill in: " + ", ".join(missing) if missing else "Enter a stake above zero.")

    if st.button("Save bet", type="primary", disabled=not ready):
        data.save_bet(kind, stake, game_date, legs)
        st.success("Bet saved. Settle any other-sport legs on the Settle page after the games.")


# ---------------------------------------------------------------------------
# Settle
# ---------------------------------------------------------------------------
elif page == "Settle":
    st.title("Settle bets")
    st.markdown('<p class="note">NBA legs are graded automatically. Mark the others here '
                "once their games are over.</p>", unsafe_allow_html=True)
    waiting = data.pending_other_legs()
    if waiting.empty:
        st.success("Nothing to settle right now.")
    for index, leg in waiting.iterrows():
        with st.container(border=True):
            info, *buttons = st.columns([4, 1, 1, 1, 1])
            info.markdown(f"**{leg['player']}** ({leg['side']}, {int(leg['price']):+d})  \n"
                          f"{leg['parlay_type']}, {leg['slate_date']}")
            for col, result in zip(buttons, ["Won", "Lost", "Push", "Void"]):
                if col.button(result, key=f"{index}-{result}", use_container_width=True,
                              type="primary" if result == "Won" else "secondary"):
                    data.settle_leg(index, result)
                    st.rerun()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
elif page == "Records":
    st.title("Records")
    cols = st.columns(2)
    for col, source, name in [(cols[0], "me", "Your bankroll"), (cols[1], "model", "Model's paper bankroll")]:
        now, stakes, _ = data.bankroll_and_stakes(source)
        col.metric(name, money_plain(now), f"{now - data.parlay.STARTING_BANKROLL:+,.2f} since start")
        col.caption(f"Stakes this week: 2-leg {money(stakes['2-leg'])}, 3-leg {money(stakes['3-leg'])}, "
                    f"underdog {money(stakes['Underdog'])}")

    chart = data.bankroll_over_time()
    if len(chart) > 1:
        st.subheader("Bankroll over time")
        st.line_chart(chart, color=[ORANGE, NET])
    else:
        st.caption("The bankroll chart appears after the first bets are graded.")

    st.subheader("Closing line value")
    clv_table = data.clv_summary()
    if clv_table.empty:
        st.caption("Appears once picks have closing lines (saved 10-20 minutes before each tip-off).")
    else:
        st.dataframe(pd.DataFrame({
            "Picks": clv_table["group"], "Count": clv_table["picks"],
            "Average CLV (points)": (clv_table["avg_clv"] * 100).round(1),
            "Beat the closing line": (clv_table["beat_close"] * 100).round(0).astype(int).astype(str) + "%",
        }), hide_index=True, use_container_width=True)
        st.caption("How much better each price was than where the market closed. Positive over a few "
                   "hundred picks is the clearest early sign of a real edge; FanDuel's own margin "
                   "means about -3 is where an edge-less pick lands.")

    for source, name in [("me", "Your bets"), ("model", "Model's paper parlays")]:
        summary, done = data.record_summary(source)
        st.subheader(name)
        if done.empty:
            st.caption("No finished bets yet.")
            continue
        shown = money_columns(money_columns(summary, ["Staked"]), ["Profit"], signed=True)
        st.dataframe(shown, hide_index=True, use_container_width=True)
        with st.expander("Every finished bet"):
            bets = done.sort_values("date", ascending=False).rename(columns=str.capitalize)
            bets = money_columns(money_columns(bets, ["Stake"]), ["Profit"], signed=True)
            st.dataframe(bets, hide_index=True, use_container_width=True)
    st.caption("Parlays swing a lot. Judge a record only after 100 or more bets.")


# ---------------------------------------------------------------------------
# Props explorer
# ---------------------------------------------------------------------------
elif page == "Props explorer":
    st.title("Props explorer")
    props = data.todays_props()
    if props.empty:
        st.info("No upcoming props saved. Download new data from the Today page.")
    else:
        st.markdown('<p class="note">Every saved prop for tonight. Percentages are the chance of '
                    "going over: the sportsbook's, the market's (Pinnacle when it has the same line, "
                    "otherwise the other books' average), the model's, and the final blend of 70% "
                    "market and 30% model.</p>", unsafe_allow_html=True)
        cols = st.columns([3, 3, 3, 2])
        player = cols[0].text_input("Player", placeholder="Search by name")
        stats = cols[1].multiselect("Stat", sorted(props["Stat"].unique()))
        books = cols[2].multiselect("Sportsbook", sorted(props["bookmaker"].unique()),
                                    default=["fanduel"] if "fanduel" in set(props["bookmaker"]) else None)
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
            "Player": view["player"], "Stat": view["Stat"], "Line": view["line"],
            "Book": view["bookmaker"], "Over": view["price_over"], "Under": view["price_under"],
            "Prediction": view["prediction"],
            "Book %": view["book_over_chance"] * 100,
            "Market %": view["market_over_chance"] * 100,
            "Model %": view["model_over_chance"] * 100,
            "Final %": view["final_over_chance"] * 100,
            "Edge": view["difference"] * 100,
            "Leans": view["model_leans"],
            "Injury": view["injury_status"].fillna(""),
            "Game": view["Game"], "Tip-off": view["Tip (ET)"],
        }).sort_values("Edge", key=abs, ascending=False)
        pct = st.column_config.NumberColumn(format="%.0f%%")
        st.caption(f"{len(table):,} props")
        st.dataframe(
            table, hide_index=True, use_container_width=True, height=620,
            column_config={
                "Book %": pct, "Market %": pct, "Model %": pct, "Final %": pct,
                "Edge": st.column_config.NumberColumn("Edge (points)", format="%+.1f"),
                "Over": st.column_config.NumberColumn(format="%+d"),
                "Under": st.column_config.NumberColumn(format="%+d"),
                "Prediction": st.column_config.NumberColumn(format="%.1f"),
            })
