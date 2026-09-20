"""JRA公式サイトのオッズを、発走前の複数時点でスナップショット収集するメインスクリプト。
GitHub Actionsから1日2回(早朝2トリガー)起動され、それぞれ「自分が実際に
起動した時刻」から18:30 JSTまでを担当ウィンドウとしてsleepしながら定点観測する。

設計の経緯:
1. 当初はam/pm 2ジョブ(8:00/13:00 JST起動)。pmトリガー(4:00 UTC)は
   cron遅延が平均4.1〜4.4時間に達し、実開始が17時台(全レース終了後)に
   なって2026-09-05/06の2日間とも収集0件だった
2. 1日1回・6:00 JST起動・単一の全日ウィンドウ(8:00-18:30)に統合したが、
   2026-09-12/13の本番運用で、GitHub Actions側にtimeout-minutes設定とは
   別の**ジョブ実行時間の暗黙の上限(実測ちょうど6時間)**があることが判明。
   6:00起動+遅延1.9時間で実開始7:55頃→6時間後の13:55に強制終了され、
   24レース中4〜5レースぶんの終盤(15時台以降の重賞含む)を丸ごと
   取りこぼした(それ以外は561件/日を欠測・エラーなく取得できていた)
3. UTC 21:00と01:00の2トリガー(約4時間差)に戻し、各ジョブの担当ウィンドウ開始を
   「自分の実起動時刻」にした。しかし2026-09-19(土)、01:00 UTCトリガーが
   4.5時間遅延(14:29 JST起動)し、21:00 UTCトリガーのジョブは自前の
   355分タイムアウトで13:50に終了したため、**13:50〜14:35の45分間はどちらの
   ジョブも動いておらず**、120時点中11時点(中山11R/阪神11Rの90分前を含む)を
   取りこぼした。cron遅延は予測できないため、2つ目のcronに頼る設計自体を
   やめた
4. 現行設計(リレー方式): 最初の1本はGASのトリガーがworkflow_dispatchで起動する
   (cronは使わない、2026-09-20〜)。ジョブは5時間30分経過した
   時点で自分の後継ジョブをworkflow_dispatchで起動し(workflow_dispatchの
   起動遅延は数秒〜十数秒でcronと違い小さい)、5時間45分で担当を引き継いで
   終了する。後継は自分の起動時刻から18:30 JSTまでを担当する。重なる数分間だけ
   同じ項目を両ジョブが取得しうる(重複行は欠測より無害)。標準出力は
   行バッファ化しており、強制終了時にログが欠落しない(9/12-19のログは
   ブロックバッファのため一部欠落していた)

使い方: python collector.py
"""
import sys
import os
import re
import json
import time
import subprocess
import datetime
import zoneinfo

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from playwright.sync_api import sync_playwright

import navigator
import parser as odds_parser
import sheets_writer

JST = zoneinfo.ZoneInfo("Asia/Tokyo")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

OFFSETS_MIN = [90, 60, 30, 10, 2]  # 発走何分前に取得するか

# 同時刻帯で複数件が重なった場合の取得優先度(E1/E3で必要な単勝複勝・馬連を優先)
BETTYPE_PRIORITY = {"単勝複勝": 0, "馬連": 1, "ワイド": 2, "枠連": 3, "馬単": 4, "3連複": 5, "3連単": 6}

# JRAのレースは概ね10時〜16時半に行われる。GitHub Actionsの実行時間上限
# (実測6時間)に収まりつつ全日をカバーするため、ウィンドウ終了は固定18:30、
# 開始は「このジョブが実際に起動した時刻」を動的に使う(下記run()参照)。
WINDOW_END = datetime.time(18, 30)

# リレー: 後継ジョブの起動(セットアップに約4分)と引き継ぎ。ジョブ自体は355分
# (=21300秒)でタイムアウトするので、スクリプト起動が数分遅れることを見込んで余裕を持つ。
# 動作確認用に環境変数で短縮できる(workflow_dispatchのrelay_testで使用)。
RELAY_DISPATCH_SEC = int(os.environ.get("RELAY_DISPATCH_SEC") or 5 * 3600 + 30 * 60)
RELAY_STOP_SEC = int(os.environ.get("RELAY_STOP_SEC") or 5 * 3600 + 45 * 60)


def now_jst():
    return datetime.datetime.now(JST)


def now_jst_iso():
    return now_jst().isoformat()


def parse_post_time(post_time_str, base_date_jst):
    # 実サイトの表記は"10時40分"(漢字区切り)であり、開発時に想定していた
    # "10:40"(コロン区切り)ではなかった。2026-09-05の本番初日、全レースが
    # 発走時刻取得不可としてスキップされ続けた根本原因。念のためコロン区切りも
    # フォールバックとして受け付ける。
    s = post_time_str.strip()
    m = re.match(r"^(\d{1,2})時(\d{2})分$", s)
    if not m:
        m = re.match(r"^(\d{1,2}):(\d{2})$", s)
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


def dispatch_successor(sh):
    """後継ジョブをworkflow_dispatchで起動する。成功したらTrue。"""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    cmd = ["gh", "workflow", "run", "collect.yml"] + (["--repo", repo] if repo else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr).strip()[:300]
    except Exception as e:
        ok, detail = False, str(e)[:300]
    sheets_writer.append_log(
        sh, "collection_log", "relay_dispatch_ok" if ok else "relay_dispatch_failed",
        detail, now_jst_iso(),
    )
    print(f"  リレー: 後継ジョブ起動 {'成功' if ok else '失敗'} {detail}")
    return ok


def run():
    if os.environ.get("FORCE_FAIL"):
        raise RuntimeError("FORCE_FAIL: 自動再起動の動作確認用の意図的な失敗")
    today = now_jst()
    # ウィンドウ開始は「このジョブが実際に起動した時刻」そのもの。cron遅延の
    # 大小によらず、起動後は直ちに担当範囲に入る(固定時刻を待って寝ない)。
    window_start = today
    window_end = today.replace(hour=WINDOW_END.hour, minute=WINDOW_END.minute, second=0, microsecond=0)

    sh = sheets_writer.open_sheet(os.environ["ODDS_SPREADSHEET_ID"])
    sheets_writer.append_log(sh, "collection_log", "job_start", "window=full", now_jst_iso())

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

        schedule = None
        for build_try in range(3):
            try:
                schedule = build_schedule(page, today)
                break
            except Exception as e:
                print(f"  [retry] build_schedule {build_try + 1}/3 失敗: {str(e)[:120]}")
                if build_try == 2:
                    raise
                time.sleep(30 * (build_try + 1))
        planned = len(schedule)
        done = 0
        skipped_past = 0

        job_t0 = time.monotonic()
        relayed = False
        relay_attempts = 0

        for item in schedule:
            in_window = window_start <= item["target_dt"] <= window_end
            if not in_window:
                continue

            wait_sec = (item["target_dt"] - now_jst()).total_seconds()
            if wait_sec < -120:
                skipped_past += 1
                continue

            # リレー: 実行時間の上限(実測6時間)が近い、または次の項目が担当時間の
            # 終わりより後なら、後継ジョブを起動して残りを引き継ぐ。
            elapsed = time.monotonic() - job_t0
            due_after = elapsed + max(wait_sec, 0)
            if (elapsed >= RELAY_DISPATCH_SEC or due_after >= RELAY_STOP_SEC)                     and not relayed and relay_attempts < 3:
                relay_attempts += 1
                relayed = dispatch_successor(sh)
            if relayed and due_after >= RELAY_STOP_SEC:
                sheets_writer.append_log(
                    sh, "collection_log", "relay_handoff",
                    f"elapsed={elapsed:.0f}s next_due={item['target_dt'].isoformat()}",
                    now_jst_iso(),
                )
                print(f"  リレー: 引き継ぎのため終了(次の項目 {item['target_dt']:%H:%M})")
                break

            if wait_sec > 0:
                time.sleep(min(wait_sec, 6 * 3600))

            for attempt in range(3):
                try:
                    ok = retry_with_refresh(page, item, sh, attempt)
                except Exception as e:
                    # 1項目の失敗(Sheetsの瞬断、ページ遷移の失敗等)でジョブ全体を落とさない
                    print(f"  [error] {item['meeting']['meeting_label']} {item['race']['race_no']}R "
                          f"{item['bet_type']} attempt={attempt}: {str(e)[:150]}")
                    ok = False
                if ok:
                    done += 1
                    break
                time.sleep(5)
            time.sleep(1.5)  # サーバー負荷配慮のため最低1秒以上の間隔を空ける

        browser.close()

    sheets_writer.append_log(
        sh, "collection_log", "job_end",
        f"planned={planned} done={done} skipped_past={skipped_past}",
        now_jst_iso(),
    )
    print(f"完了: planned={planned} done={done} skipped_past={skipped_past}")


if __name__ == "__main__":
    run()
