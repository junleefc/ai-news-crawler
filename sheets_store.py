"""구글 시트 저장 (서비스 계정 인증)."""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["날짜", "출처", "카테고리", "제목", "요약", "중요도", "링크"]
URL_COL = 7  # 링크가 들어가는 열(G)


def _client():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def open_worksheet(sheet_id, worksheet_name):
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=2000, cols=len(HEADER))
    first = ws.row_values(1)
    if first[: len(HEADER)] != HEADER:
        ws.update(range_name="A1", values=[HEADER])
    return ws


def existing_urls(ws):
    col = ws.col_values(URL_COL)  # 헤더 포함
    return {u.strip() for u in col[1:] if u.strip()}


def append_items(ws, items):
    if not items:
        return
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    rows = [
        [today, it["source"], it["category"], it["ko_headline"], it["ko_summary"], it["importance"], it["url"]]
        for it in items
    ]
    ws.append_rows(rows, value_input_option="USER_ENTERED")
