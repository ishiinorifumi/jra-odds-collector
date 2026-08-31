"""JRA公式サイトのオッズを、発走前の複数時点でスナップショット収集するメインスクリプト。
GitHub Actionsから1日2回(午前/午後)起動され、それぞれのウィンドウ内で対象レースの
発走時刻に応じてsleepしながら定点観測する。

使い方: python collector.py --window am|pm
"""
import sys
import os
import re
import json
import time
import argparse
import datetime
import zoneinfo

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import navigator
import parser as odds_parser
import sheets_writer

JST = zoneinfo.ZoneInfo("Asia/Tokyo")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

OFFSETS_MIN = [90, 60, 30, 10, 2]  # 発走何分前に取得するか

SPREADSHEET_ID = os.environ.get("ODDS_SPREADSHEET_ID", "")

WINDOW_RANGES = {
    # JRAのレースは概ね10時〜16時半に行われる。前半/後半で2ジョブに分割する。
    "am": (datetime.time(8, 0), datetime.time(13, 0)),
    "pm": (datetime.time(13, 0), datetime.time(18, 0)),
}


def now_jst():
    return datetime.datetime.now(JST)


def parse_post_time(post_time_str, base_date_jst):
    m = re.match(r"^(\d{1,2}):(\d{2})$", post_time_str.strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return base_date_jst.replace(hour=hh, minute=mm, second=0, microsecond=0)


def build_schedule(page, today_jst):
    """今日の全開催・全レースの発走時刻とcnameを収集し、
    (target_datetime, meeting, race, bet_type, path, cname) のリストを返す。"""
    navigator.goto_odds_top(page)
    html_top = page.content()
    meetings = navigator.list_meetings(html_top)

    today_str = today_jst.strftime("%Y%m%d")
    todays_meetings = [m for m in meetings if m["date"] == today_str]
    print(f"本日({today_str})の開催: {[m['meeting_label'] for m in todays_meetings]}")

    schedule = []
    for meeting in todays_meetings:
        html_races = navigator.get_html(page, meeting["path"], meeting["cname"])
        races = navigator.list_races(html_races)
        for race in races:
            post_dt = parse_post_time(race["post_time"], today_jst)
            if post_dt is None:
                print(f"  [skip] {meeting['meeting_label']} {race['race_no']}R 発走時刻取得不可({race['post_time']!r})")
                continue
            for bet_type, (path, cname) in race["bettypes"].items():
                for offset in OFFSETS_MIN:
                    target = post_dt - datetime.timedelta(minutes=offset)
                    schedule.append({
                        "target_dt": target,
                        "offset": offset,
                        "meeting": meeting,
                        "race": race,
                        "bet_type": bet_type,
                        "path": path,
                        "cname": cname,
                    })
    schedule.sort(key=lambda x: x["target_dt"])
    return schedule


def capture(page, item, sh):
    path, cname = item["path"], item["cname"]
    meeting, race = item["meeting"], item["race"]
    captured_at = now_jst().isoformat()
    target_label = f"{item['offset']}min_before"
    race_key = f"{meeting['course_code']}{meeting['kaiji']}{meeting['nichiji']}{race['race_no']}{meeting['date']}"

    try:
        navigator.do_action(page, path, cname)
        html = page.content()
        status = "OK"
    except Exception as e:
        sheets_writer.append_log(sh, "collection_log", "capture_error", f"{race_key} {item['bet_type']} {e}")
        return False

    # 生HTMLはローカルに保存(後続でまとめて圧縮しアップロードする運用)
    raw_dir = "raw_html"
    os.makedirs(raw_dir, exist_ok=True)
    fname = f"{raw_dir}/{race_key}_{item['bet_type']}_{target_label}_{captured_at.replace(':','')}.html"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)

    if item["bet_type"] == "単勝複勝":
        rows = odds_parser.parse_tanpuku(html)
        if rows:
            sheet_rows = [[
                captured_at, target_label, meeting["meeting_label"], meeting["course_code"],
                meeting["kaiji"], meeting["nichiji"], meeting["date"], race["race_no"],
                race["post_time"], item["bet_type"], status,
                r["umaban"], r["horse_name"], r["tan_odds"], r["fuku_min"], r["fuku_max"],
                r["age"], r["weight"], r["weight_diff"], r["kinryo"], r["jockey"], r["trainer"],
            ] for r in rows]
            sheets_writer.append_tanpuku_rows(sh, "tanpuku_odds", sheet_rows)
    else:
        tables = odds_parser.parse_combination_table(html)
        sheets_writer.append_raw_dump(sh, "other_odds_raw", [[
            captured_at, target_label, race_key, item["bet_type"], status,
            json.dumps(tables, ensure_ascii=False)[:45000],
        ]])

    print(f"  captured: {race_key} {item['bet_type']} ({target_label})")
    return True


def run(window):
    start_t, end_t = WINDOW_RANGES[window]
    today = now_jst()
    window_start = today.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
    window_end = today.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)

    sh = sheets_writer.open_sheet(SPREADSHEET_ID)
    sheets_writer.append_log(sh, "collection_log", "job_start", f"window={window}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA).new_page()

        schedule = build_schedule(page, today)
        planned = len(schedule)
        done = 0
        skipped_past = 0

        for item in schedule:
            if not (window_start <= item["target_dt"] <= window_end):
                continue
            wait_sec = (item["target_dt"] - now_jst()).total_seconds()
            if wait_sec < -120:
                skipped_past += 1
                continue
            if wait_sec > 0:
                time.sleep(min(wait_sec, 6 * 3600))

            for attempt in range(3):
                ok = capture(page, item, sh)
                if ok:
                    done += 1
                    break
                time.sleep(5)
            time.sleep(1.5)  # サーバー負荷配慮のため最低1秒以上の間隔を空ける

        browser.close()

    sheets_writer.append_log(
        sh, "collection_log", "job_end",
        f"window={window} planned={planned} done={done} skipped_past={skipped_past}",
    )
    print(f"完了: planned={planned} done={done} skipped_past={skipped_past}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["am", "pm"], required=True)
    args = ap.parse_args()
    run(args.window)
