"""구글 시트 저장 (서비스 계정 인증). 평가(피드백) 열 포함."""
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["날짜", "출처", "유형", "인사이트", "제목", "왜 중요", "요약", "키워드", "링크", "메시지TS", "평가", "원제"]
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
            # ts는 반드시 문자열로 (USER_ENTERED면 시트가 숫자로 반올림해 뒷자리 유실 → 리액션 조회 실패)
            f"'{it.get('slack_ts', '')}" if it.get("slack_ts") else "", "",
            it.get("title", ""),   # 원제(영문) — 결정적 중복 판별용
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def rows_pending_rating(ws):
    """슬랙 ts가 있고 아직 평가가 비어있는 행 반환: [(row_index, date, ts, keywords, type, source, title)]"""
    values = ws.get_all_values()
    pending = []
    for idx, r in enumerate(values[1:], start=2):
        ts = r[TS_COL - 1].strip().lstrip("'") if len(r) >= TS_COL else ""
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


PROFILE_SHEET = "profile"
MANUAL_MARK = "[직접 지시]"


def open_profile_sheet(sheet_id):
    """취향 프로필 탭 (없으면 생성). 사용자가 직접 읽고 수정할 수 있는 문서."""
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    try:
        return sh.worksheet(PROFILE_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=PROFILE_SHEET, rows=200, cols=4)
        ws.update(range_name="A1", values=[
            ["내 취향 프로필 (매일 아침 자동 갱신)"],
            [""],
            ["아래 '직접 지시' 칸(B열)에 원하는 내용을 적으면 다음날 선별에 반영됩니다."],
            ["예: 국내 AI 소식도 넣어줘 / 채용·인사 관련은 빼줘"],
            [""],
        ])
        return ws


def read_manual_directives(sheet_id):
    """profile 탭 B열에 사용자가 직접 적은 지시문 수집."""
    try:
        ws = open_profile_sheet(sheet_id)
        col = ws.col_values(2)[1:]  # 1행은 헤더라 제외
        skip = ("직접 지시", "여기에 적으면")
        return [c.strip() for c in col
                if c.strip() and not any(s in c for s in skip)]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 직접 지시 읽기 실패: {e}")
        return []


def write_profile(sheet_id, profile_text, stats_lines=None):
    """생성된 취향 프로필을 profile 탭에 기록 (사용자가 눈으로 확인)."""
    try:
        ws = open_profile_sheet(sheet_id)
        updated = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M")
        manual = read_manual_directives(sheet_id)
        rows = [["내 취향 프로필 (매일 아침 자동 갱신)", "직접 지시 (여기에 적으면 반영됨)"],
                [f"마지막 갱신: {updated}", ""],
                ["", ""]]
        for line in profile_text.split("\n"):
            rows.append([line, ""])
        if stats_lines:
            rows.append(["", ""])
            for s in stats_lines:
                rows.append([s, ""])
        ws.clear()
        ws.update(range_name="A1", values=rows)
        # 사용자가 적어둔 직접 지시는 지워지지 않게 복원
        if manual:
            ws.update(range_name="B2", values=[[m] for m in manual])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 프로필 기록 실패: {e}")


def recent_items(ws, days=4):
    """최근 N일간 발송된 기사 요약 정보 (재탕 판별용)."""
    from datetime import timedelta
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    cutoff = today - timedelta(days=days)
    out = []
    for r in ws.get_all_values()[1:]:
        try:
            d = datetime.strptime(r[0], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            continue
        if d >= cutoff:
            out.append({
                "title": r[4] if len(r) > 4 else "",
                "why": r[5] if len(r) > 5 else "",
                "keywords": r[7] if len(r) > 7 else "",
                "orig_title": r[11] if len(r) > 11 else "",
            })
    return out


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
