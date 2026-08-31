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

# 同時刻帯で複数件が重なった場合の取得優先度(E1/E3で必要な単勝複勝・馬連を優先)
BETTYPE_PRIORITY = {"単勝複勝": 0, "馬連": 1, "ワイド": 2, "枠連": 3, "馬単": 4, "3連複": 5, "3連単": 6}

WINDOW_RANGES = {
    # JRAのレースは概ね10時〜16時半に行われる。前半/後半で2ジョブに分割する。
    "am": (datetime.time(8, 0), datetime.time(13, 0)),
    "pm": (datetime.time(13, 0), datetime.time(18, 0)),
}


def now_jst():
    return datetime.datetime.now(JST)


def now_jst_iso():
    return now_jst().isoformat()


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
                        "cname_extracted_at": now_jst(),
                    })
    # 同時刻帯では馬券種の優先度順(単勝複勝→馬連→ワイド→その他)で取得する
    schedule.sort(key=lambda x: (x["target_dt"], BETTYPE_PRIORITY.get(x["bet_type"], 99)))
    return schedule


def capture(page, item, sh):
    path, cname = item["path"], item["cname"]
    meeting, race = item["meeting"], item["race"]
    captured_at = now_jst_iso()
    target_label = f"{item['offset']}min_before"
    race_key = f"{meeting['course_code']}{meeting['kaiji']}{meeting['nichiji']}{race['race_no']}{meeting['date']}"

    t0 = time.monotonic()
    try:
        navigator.do_action(page, path, cname)
        html = page.content()
        status = "OK"
    except Exception as e:
        sheets_writer.append_log(sh, "collection_log", "capture_error", f"{race_key} {item['bet_type']} {e}", now_jst_iso())
        return False
    elapsed = time.monotonic() - t0
    sheets_writer.append_log(
        sh, "collection_log", "capture_timing",
        f"{race_key} {item['bet_type']} {target_label} elapsed={elapsed:.2f}s",
        now_jst_iso(),
    )

    odds_status = odds_parser.get_odds_status_label(html)

    # 生HTMLはローカルに保存(GitHub Actions artifactとしてジョブ終了時にまとめてアップロード)
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
                race["post_time"], item["bet_type"], status, odds_status,
                r["umaban"], r["horse_name"], r["tan_odds"], r["fuku_min"], r["fuku_max"],
                r["age"], r["weight"], r["weight_diff"], r["kinryo"], r["jockey"], r["trainer"],
            ] for r in rows]
            sheets_writer.append_tanpuku_rows(sh, "tanpuku_odds", sheet_rows)
    else:
        tables = odds_parser.parse_combination_table(html)
        sheets_writer.append_raw_dump(sh, "other_odds_raw", [[
            captured_at, target_label, race_key, item["bet_type"], status, odds_status,
            json.dumps(tables, ensure_ascii=False)[:45000],
        ]])

    print(f"  captured: {race_key} {item['bet_type']} ({target_label}) {elapsed:.2f}s")
    return True


def retry_with_refresh(page, item, sh, attempt):
    """2回目以降のリトライでは、cnameのchecksumが失効している可能性を考慮し、
    開催選択→レース選択を辿り直して最新のcnameを取得してから再試行する。"""
    if attempt == 0:
        return capture(page, item, sh)

    fresh = navigator.refresh_race_action(
        page, item["meeting"], item["race"]["race_no"], item["bet_type"]
    )
    if fresh is None:
        sheets_writer.append_log(
            sh, "collection_log", "cname_refresh_failed",
            f"{item['meeting']['meeting_label']} {item['race']['race_no']}R {item['bet_type']}",
            now_jst_iso(),
        )
        return capture(page, item, sh)  # 取り直せなければ元のcnameでダメ元リトライ

    age_sec = (now_jst() - item["cname_extracted_at"]).total_seconds()
    item["path"], item["cname"] = fresh
    sheets_writer.append_log(
        sh, "collection_log", "cname_refreshed",
        f"{item['meeting']['meeting_label']} {item['race']['race_no']}R {item['bet_type']} age={age_sec:.0f}s",
        now_jst_iso(),
    )
    return capture(page, item, sh)


def run(window):
    start_t, end_t = WINDOW_RANGES[window]
    today = now_jst()
    window_start = today.replace(hour=start_t.hour, minute=start_t.minute, second=0, microsecond=0)
    window_end = today.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)

    sh = sheets_writer.open_sheet(os.environ["ODDS_SPREADSHEET_ID"])
    sheets_writer.append_log(sh, "collection_log", "job_start", f"window={window}", now_jst_iso())

    approx_cells = sheets_writer.estimate_cell_usage(sh)
    if approx_cells > 8_000_000:
        sheets_writer.append_log(
            sh, "collection_log", "cell_budget_warning",
            f"approx_cells={approx_cells} (上限1000万に接近。新しいスプレッドシートへの切替を検討)",
            now_jst_iso(),
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA).new_page()

        schedule = build_schedule(page, today)
        planned = len(schedule)
        done = 0
        skipped_past = 0

        for item in schedule:
            # 13:00ちょうどの境界がam/pm両方で二重取得されないよう、
            # pm側の下限は厳密な不等号にする。
            if window == "pm":
                in_window = window_start < item["target_dt"] <= window_end
            else:
                in_window = window_start <= item["target_dt"] <= window_end
            if not in_window:
                continue

            wait_sec = (item["target_dt"] - now_jst()).total_seconds()
            if wait_sec < -120:
                skipped_past += 1
                continue
            if wait_sec > 0:
                time.sleep(min(wait_sec, 6 * 3600))

            for attempt in range(3):
                ok = retry_with_refresh(page, item, sh, attempt)
                if ok:
                    done += 1
                    break
                time.sleep(5)
            time.sleep(1.5)  # サーバー負荷配慮のため最低1秒以上の間隔を空ける

        browser.close()

    sheets_writer.append_log(
        sh, "collection_log", "job_end",
        f"window={window} planned={planned} done={done} skipped_past={skipped_past}",
        now_jst_iso(),
    )
    print(f"完了: planned={planned} done={done} skipped_past={skipped_past}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", choices=["am", "pm"], required=True)
    args = ap.parse_args()
    run(args.window)
