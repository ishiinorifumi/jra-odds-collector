"""Googleスプレッドシートへの書き込み。サービスアカウントのJSON鍵を
環境変数 GOOGLE_SERVICE_ACCOUNT_JSON (中身の文字列)から読み込む。
"""
import os
import json
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

TANPUKU_HEADER = [
    "captured_at", "target_label", "meeting_label", "course_code", "kaiji", "nichiji",
    "date", "race_no", "post_time", "bet_type", "http_status",
    "umaban", "horse_name", "tan_odds", "fuku_min", "fuku_max",
    "age", "weight", "weight_diff", "kinryo", "jockey", "trainer",
]

LOG_HEADER = ["captured_at", "event", "detail"]


def get_client():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, title, header):
    try:
        ws = sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(header) + 2)
        ws.append_row(header)
    return ws


def open_sheet(spreadsheet_id):
    client = get_client()
    return client.open_by_key(spreadsheet_id)


def append_tanpuku_rows(sh, sheet_title, rows):
    """rows: TANPUKU_HEADERの順に対応するリストのリスト"""
    ws = get_or_create_worksheet(sh, sheet_title, TANPUKU_HEADER)
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")


def append_log(sh, sheet_title, event, detail):
    import datetime
    ws = get_or_create_worksheet(sh, sheet_title, LOG_HEADER)
    ws.append_row([datetime.datetime.now().isoformat(), event, detail], value_input_option="USER_ENTERED")


def append_raw_dump(sh, sheet_title, rows):
    """馬連・ワイド等、汎用行ダンプ用。可変長なので1セルにJSON文字列で保存する。"""
    header = ["captured_at", "target_label", "race_key", "bet_type", "http_status", "raw_json"]
    ws = get_or_create_worksheet(sh, sheet_title, header)
    if rows:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
