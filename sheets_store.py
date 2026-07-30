"""구글 시트 저장 (서비스 계정 인증). 평가(피드백) 열 포함."""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["날짜", "출처", "유형", "인사이트", "제목", "왜 중요", "요약", "키워드", "링크", "메시지TS", "평가"]
URL_COL = 9    # I열: 링크
TS_COL = 10    # J열: 슬랙 메시지 ts
RATING_COL = 11  # K열: 평가 (최고/좋다/별로/무반응)


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
            it.get("slack_ts", ""), "",
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def rows_pending_rating(ws):
    """슬랙 ts가 있고 아직 평가가 비어있는 행 반환: [(row_index, date, ts, keywords, type, source, title)]"""
    values = ws.get_all_values()
    pending = []
    for idx, r in enumerate(values[1:], start=2):
        ts = r[TS_COL - 1].strip() if len(r) >= TS_COL else ""
        rating = r[RATING_COL - 1].strip() if len(r) >= RATING_COL else ""
        if ts and not rating:
            pending.append({
                "row": idx, "date": r[0], "ts": ts,
                "keywords": r[7] if len(r) > 7 else "",
                "type": r[2] if len(r) > 2 else "",
                "source": r[1] if len(r) > 1 else "",
                "title": r[4] if len(r) > 4 else "",
            })
    return pending


def write_ratings(ws, row_ratings):
    """row_ratings: {row_index: '최고'|'좋다'|'별로'|'무반응'} 일괄 기록."""
    if not row_ratings:
        return
    cells = [gspread.Cell(row, RATING_COL, rating) for row, rating in row_ratings.items()]
    ws.update_cells(cells)


def rated_rows(ws):
    """평가가 기록된 행들: [(rating, keywords, type, source, title)]"""
    values = ws.get_all_values()
    out = []
    for r in values[1:]:
        rating = r[RATING_COL - 1].strip() if len(r) >= RATING_COL else ""
        if rating:
            out.append({
                "rating": rating,
                "keywords": r[7] if len(r) > 7 else "",
                "type": r[2] if len(r) > 2 else "",
                "source": r[1] if len(r) > 1 else "",
                "title": r[4] if len(r) > 4 else "",
            })
    return out
