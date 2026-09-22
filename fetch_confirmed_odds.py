"""JRA公式サイトから、既に終了した開催の確定オッズ(単勝・複勝)を取得する。

collector.py(発走前の定点観測)とは別の、単発実行用スクリプト。
navigator.py/parser.pyの既存コードをそのまま流用できる
(開催選択ページの同一cname構造が、終了後は "pw15orl10..." (10=開催済)、
開催前は "pw15orl00..." (00=未開催) に変わるだけで、レース選択・オッズ
表示ページの構造・doAction()呼び出し方は全く同じ)。

使い方: python fetch_confirmed_odds.py 20260919 20260921 20260922
出力: confirmed_odds_<実行日時>.csv (race_id, umaban, tan_odds, fuku_min,
      fuku_max, odds_status_label, race_no, meeting_label)
"""
import sys
import csv
import time
import datetime

from playwright.sync_api import sync_playwright

import navigator
import parser as odds_parser

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_dates(page, target_dates):
    rows = []
    navigator.goto_odds_top(page)
    html_top = page.content()
    meetings = navigator.list_meetings(html_top)
    todays = [m for m in meetings if m["date"] in target_dates]
    print(f"対象開催: {[m['meeting_label'] + ' ' + m['date'] for m in todays]}")

    for meeting in todays:
        html_races = navigator.get_html(page, meeting["path"], meeting["cname"])
        races = navigator.list_races(html_races)
        for race in races:
            bettype = race["bettypes"].get("単勝複勝")
            if bettype is None:
                print(f"  [skip] {meeting['meeting_label']} {race['race_no']}R 単勝複勝リンクなし")
                continue
            path, cname = bettype
            html = navigator.get_html(page, path, cname)
            status = odds_parser.get_odds_status_label(html)
            horses = odds_parser.parse_tanpuku(html)
            race_id = (meeting["date"] + meeting["course_code"] + meeting["kaiji"] +
                       meeting["nichiji"] + race["race_no"])
            if not horses:
                print(f"  [warn] {meeting['meeting_label']} {race['race_no']}R パース結果0件 status={status}")
                continue
            for h in horses:
                rows.append({
                    "race_id": race_id, "meeting_label": meeting["meeting_label"],
                    "race_no": race["race_no"], "post_time": race["post_time"],
                    "status": status, **h,
                })
            print(f"  captured: {meeting['meeting_label']} {race['race_no']}R status={status} n={len(horses)}")
            time.sleep(1.5)
    return rows


def main():
    target_dates = sys.argv[1:]
    if not target_dates:
        print("使い方: python fetch_confirmed_odds.py YYYYMMDD [YYYYMMDD ...]")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA).new_page()
        rows = fetch_dates(page, set(target_dates))
        browser.close()

    if not rows:
        print("取得0件")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"confirmed_odds_{ts}.csv"
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)}行を{out_path}に保存しました")


if __name__ == "__main__":
    main()
