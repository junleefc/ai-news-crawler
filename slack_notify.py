"""슬랙 Incoming Webhook 발송 (엄선 기사, 여러 메시지로 분할)."""
import requests

TYPE_EMOJI = {"심층분석": ":green_circle:", "오피니언·전략": ":large_blue_circle:", "실전케이스": ":large_purple_circle:"}


def _stars(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return "★" * n + "☆" * (5 - n)


def _sheet_link(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def _item_block(it, rank):
    emoji = TYPE_EMOJI.get(it.get("type"), ":white_circle:")
    headline = it.get("ko_headline") or it.get("title", "")
    lines = [f"*{rank}. {headline}*  {emoji} {it.get('type','')} {_stars(it.get('insight'))}"]
    if it.get("why"):
        lines.append(f"_{it['why']}_")
    for b in it.get("summary_bullets", [])[:3]:   # 슬랙엔 핵심 3개 (전체는 시트에)
        lines.append(f"• {b}")
    if it.get("keywords"):
        lines.append("🏷 " + " · ".join(it["keywords"]))
    lines.append(f"🔗 <{it['url']}|{it['source']}>")
    return "\n".join(lines)


def build_messages(items, date_str, sheet_id, per_message=6):
    """엄선 기사들을 per_message개씩 나눠 메시지 리스트로 반환."""
    items = sorted(items, key=lambda x: x.get("insight", 0), reverse=True)
    msgs = []
    header = (
        f":robot_face: *오늘의 해외 AI 인사이트* ({date_str})\n"
        f"_엄선 {len(items)}건 · 심층분석/전략/실전케이스 위주 · 전체 요약은 구글시트_\n"
        f":bar_chart: <{_sheet_link(sheet_id)}|구글시트 아카이브 열기>"
    )
    for i in range(0, len(items), per_message):
        chunk = items[i : i + per_message]
        blocks = [_item_block(it, i + j + 1) for j, it in enumerate(chunk)]
        body = "\n\n".join(blocks)
        msgs.append(f"{header}\n\n{body}" if i == 0 else body)
    return msgs


def post(webhook_url, text):
    resp = requests.post(webhook_url, json={"text": text}, timeout=20)
    resp.raise_for_status()
    return resp


def post_all(webhook_url, messages):
    for m in messages:
        post(webhook_url, m)


def post_empty(webhook_url, date_str):
    post(webhook_url, f":robot_face: *오늘의 해외 AI 인사이트* ({date_str})\n_기준을 통과한 새 글이 없습니다._")
