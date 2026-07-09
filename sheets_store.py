"""구글 시트 저장 (서비스 계정 인증)."""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["날짜", "출처", "유형", "인사이트", "제목", "왜 중요", "요약", "키워드", "링크"]
URL_COL = 9  # 링크가 들어가는 열(I)


def _client():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def open_worksheet(sheet_id, worksheet_name):
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=2000, cols=len(HEADER))
    if ws.row_values(1)[: len(HEADER)] != HEADER:
        ws.update(range_name="A1", values=[HEADER])
    return ws


def existing_urls(ws):
    col = ws.col_values(URL_COL)
    return {u.strip() for u in col[1:] if u.strip()}


def append_items(ws, items):
    if not items:
        return
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    rows = []
    for it in items:
        summary = "\n".join(f"• {b}" for b in it.get("summary_bullets", []))
        rows.append([
            today, it["source"], it.get("type", ""), it.get("insight", ""),
            it.get("ko_headline", it["title"]), it.get("why", ""),
            summary, ", ".join(it.get("keywords", [])), it["url"],
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
