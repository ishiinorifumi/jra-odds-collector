"""Googleスプレッドシートへの書き込み。サービスアカウントのJSON鍵を
環境変数 GOOGLE_SERVICE_ACCOUNT_JSON (中身の文字列)から読み込む。

スプレッドシート自体は事前にユーザー自身のGoogleアカウントで作成し、
サービスアカウントに編集者権限を共有した上で、そのIDを環境変数
ODDS_SPREADSHEET_ID として渡す(open_by_key)。サービスアカウントは
Google WorkspaceのShared Drive配下でない限りDrive上の保存容量を
一切持たないため、サービスアカウント自身によるスプレッドシート新規作成
(client.create())は "Drive storage quota exceeded" で必ず失敗する。
これはAPI有効化や権限設定では回避できない構造的な制約であり、
月次の自動ローテーションはこの理由で採用していない
(セル数上限との関係は README 参照)。
"""
import os
import json
import time
import gspread
import requests
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

TANPUKU_HEADER = [
    "captured_at", "target_label", "meeting_label", "course_code", "kaiji", "nichiji",
    "date", "race_no", "post_time", "bet_type", "capture_status", "odds_status_label",
    "umaban", "horse_name", "tan_odds", "fuku_min", "fuku_max",
    "age", "weight", "weight_diff", "kinryo", "jockey", "trainer",
]

RAW_DUMP_HEADER = [
    "captured_at", "target_label", "race_key", "bet_type", "capture_status",
    "odds_status_label", "raw_json",
]

LOG_HEADER = ["captured_at", "event", "detail"]


TRANSIENT_HTTP = {429, 500, 502, 503, 504}


def _with_retry(fn, *args, attempts=6, **kwargs):
    """Sheets APIの一時的な失敗(429/5xx/接続断)を指数バックオフで再試行する。
    2026-09-06にAPIError 503でジョブが即死した。1回の瞬断でその日の収集が
    止まらないようにする。"""
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(e, "code", None)
            if code is None:
                code = getattr(getattr(e, "response", None), "status_code", None)
            if code not in TRANSIENT_HTTP or i == attempts - 1:
                raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if i == attempts - 1:
                raise
        time.sleep(min(5 * 2 ** i, 60))


def get_client():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


_ws_cache = {}


def get_or_create_worksheet(sh, title, header):
    # worksheet()は毎回メタデータ取得のAPI呼び出しを伴うため、プロセス内でキャッシュして
    # 読み取りクォータ(60回/分/ユーザー)への圧迫を減らす
    key = (sh.id, title)
    if key in _ws_cache:
        return _ws_cache[key]
    try:
        ws = _with_retry(sh.worksheet, title)
    except gspread.exceptions.WorksheetNotFound:
        ws = _with_retry(sh.add_worksheet, title=title, rows=1000, cols=len(header) + 2)
        _with_retry(ws.append_row, header)
    _ws_cache[key] = ws
    return ws


def open_sheet(spreadsheet_id):
    client = get_client()
    return _with_retry(client.open_by_key, spreadsheet_id)


def estimate_cell_usage(sh):
    """行データが乗っているシートの概算セル数を返す(セル数上限1,000万の早期警告用)。
    実データ行数は列Aの非空セル数で数える(row_countはシートの割当グリッド数で
    実データ数と厳密には一致しないため)。"""
    total = 0
    for title, header in [("tanpuku_odds", TANPUKU_HEADER), ("other_odds_raw", RAW_DUMP_HEADER)]:
        try:
            ws = _with_retry(sh.worksheet, title)
        except gspread.exceptions.WorksheetNotFound:
            continue
        nrows = len(_with_retry(ws.col_values, 1))
        total += nrows * len(header)
    return total


def append_tanpuku_rows(sh, sheet_title, rows):
    """rows: TANPUKU_HEADERの順に対応するリストのリスト"""
    ws = get_or_create_worksheet(sh, sheet_title, TANPUKU_HEADER)
    if rows:
        _with_retry(ws.append_rows, rows, value_input_option="USER_ENTERED")


def append_log(sh, sheet_title, event, detail, captured_at_jst):
    """captured_at_jst: 呼び出し側でJSTのisoformat文字列を渡すこと
    (ランナーの標準時(UTC)と混在させないため)。"""
    ws = get_or_create_worksheet(sh, sheet_title, LOG_HEADER)
    _with_retry(ws.append_row, [captured_at_jst, event, detail], value_input_option="USER_ENTERED")


def append_raw_dump(sh, sheet_title, rows):
    """馬連・ワイド等、汎用行ダンプ用。可変長なので1セルにJSON文字列で保存する。"""
    ws = get_or_create_worksheet(sh, sheet_title, RAW_DUMP_HEADER)
    if rows:
        _with_retry(ws.append_rows, rows, value_input_option="USER_ENTERED")
