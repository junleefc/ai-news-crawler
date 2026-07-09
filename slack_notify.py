"""슬랙 Incoming Webhook으로 다이제스트 발송."""
import requests

FIRE = {"높음": " :fire:", "중간": "", "낮음": ""}
NUM = ["", ":one:", ":two:", ":three:", ":four:", ":five:",
       ":six:", ":seven:", ":eight:", ":nine:", ":keycap_ten:"]
ORDER = {"높음": 0, "중간": 1, "낮음": 2}


def _sheet_link(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def build_message(items, date_str, sheet_id, max_items=8):
    total = len(items)
    items_sorted = sorted(items, key=lambda x: ORDER.get(x.get("importance"), 1))
    top = items_sorted[:max_items]

    lines = [
        f":robot_face: *오늘의 해외 AI 뉴스* ({date_str})",
        f"_새 소식 {total}건 · 구글시트에 자동 저장됨_",
        "",
    ]
    for i, it in enumerate(top, 1):
        num = NUM[i] if i < len(NUM) else f"{i}."
        lines.append(f"{num} *{it['ko_headline']}*{FIRE.get(it.get('importance'), '')}")
        lines.append(it["ko_summary"])
        lines.append(f":link: <{it['url']}|{it['source']}>")
        lines.append("")

    remaining = total - len(top)
    if remaining > 0:
        lines.append(f"_...외 {remaining}건은 구글시트에서 확인_")
    lines.append(f":bar_chart: <{_sheet_link(sheet_id)}|전체 아카이브(구글시트) 열기>")
    return "\n".join(lines)


def post(webhook_url, text):
    resp = requests.post(webhook_url, json={"text": text}, timeout=20)
    resp.raise_for_status()
    return resp


def post_empty(webhook_url, date_str):
    text = (
        f":robot_face: *오늘의 해외 AI 뉴스* ({date_str})\n"
        "_지난 하루 새로 수집된 소식이 없습니다._"
    )
    return post(webhook_url, text)
