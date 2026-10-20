"""
settle_bet.py: mark your other-sport legs (soccer, NFL, tennis...) as won or lost.

NBA legs are graded automatically from box scores. Other sports aren't, so
after those games finish, run:
    python3 settle_bet.py

It shows each waiting leg and asks: w = won, l = lost, p = push, v = void
(cancelled / player didn't play), or Enter to skip it for now.
Then it prints your updated record.
"""

import os

import pandas as pd

from parlay import HISTORY_FILE, report

ANSWERS = {"w": "Won", "l": "Lost", "p": "Push", "v": "Void"}


def main():
    if not os.path.exists(HISTORY_FILE):
        print("No bets logged yet. Use  python3 log_bet.py  first.")
        return
    history = pd.read_csv(HISTORY_FILE)
    history["leg_result"] = history["leg_result"].fillna("").astype(str)

    waiting = history[(history["market"] == "other") & (history["leg_result"] == "")]
    if waiting.empty:
        print("No other-sport legs waiting for a result.")
        return

    print(f"{len(waiting)} leg(s) waiting. w = won, l = lost, p = push, v = void, Enter = skip.\n")
    for i, leg in waiting.iterrows():
        while True:
            answer = input(f"   {leg['slate_date']}  {leg['player']} ({leg['side']}, "
                           f"{int(leg['price']):+d}): ").strip().lower()
            if answer == "" or answer in ANSWERS:
                break
            print("   Type w, l, p, v, or press Enter to skip.")
        if answer:
            history.at[i, "leg_result"] = ANSWERS[answer]

    history.to_csv(HISTORY_FILE, index=False)
    print("\nSaved.\n")
    report(history)


if __name__ == "__main__":
    main()
