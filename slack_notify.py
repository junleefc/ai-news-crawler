"""슬랙 발송.
- 봇 토큰(SLACK_BOT_TOKEN) 있으면: 헤더 1개 + 스레드에 뉴스 1건씩 + 평가 이모지(🔥👍👎) 시드.
  각 item에 slack_ts 를 심어서 반환 → 시트에 저장 → 다음날 리액션 수거에 사용.
- 없으면: 기존 Incoming Webhook 방식으로 폴백 (평가 기능 없음).
"""
import time

import requests

API = "https://slack.com/api"
TYPE_EMOJI = {"심층분석": ":green_circle:", "오피니언·전략": ":large_blue_circle:", "실전케이스": ":large_purple_circle:"}
SEED_REACTIONS = ["five", "four", "three", "two", "one"]  # 5️⃣최고 … 1️⃣최악


def _stars(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return "★" * n + "☆" * (5 - n)


def _sheet_link(sheet_id):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def _item_block(it, rank):
    headline = it.get("ko_headline") or it.get("title", "")
    emoji = TYPE_EMOJI.get(it.get("type"), ":white_circle:")
    lines = [f"*{rank}. {headline}*  {emoji} {it.get('type','')} {_stars(it.get('insight'))}"]
    if it.get("why"):
        lines.append(f"_{it['why']}_")
    for b in it.get("summary_bullets", [])[:3]:
        lines.append(f"• {b}")
    if it.get("keywords"):
        lines.append("🏷 " + " · ".join(it["keywords"]))
    lines.append(f"🔗 <{it['url']}|{it['source']}>")
    return "\n".join(lines)


# ---------- 봇 모드 (스레드 + 평가) ----------

def _bot_post(token, channel, text, thread_ts=None):
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    r = requests.post(f"{API}/chat.postMessage", json=payload,
                      headers={"Authorization": f"Bearer {token}"}, timeout=20)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage 실패: {data.get('error')}")
    return data["ts"]


def _bot_react(token, channel, ts, name):
    requests.post(f"{API}/reactions.add",
                  json={"channel": channel, "timestamp": ts, "name": name},
                  headers={"Authorization": f"Bearer {token}"}, timeout=15)


def send_digest_bot(token, channel, items, date_str, sheet_id):
    """헤더 + 스레드 1건씩 발송, 각 item에 slack_ts 기록."""
    items = sorted(items, key=lambda x: x.get("insight", 0), reverse=True)
    header = (
        f":robot_face: *오늘의 해외 AI 인사이트* ({date_str})\n"
        f"_엄선 {len(items)}건 — 스레드에서 1건씩 확인하고 숫자로 평가해주세요_\n"
        f":five: 최고  :four: 좋음  :three: 보통  :two: 별로  :one: 최악  (다음날 취향 학습에 반영)\n"
        f":bar_chart: <{_sheet_link(sheet_id)}|전체 아카이브(구글시트)>"
    )
    head_ts = _bot_post(token, channel, header)
    for i, it in enumerate(items, 1):
        try:
            ts = _bot_post(token, channel, _item_block(it, i), thread_ts=head_ts)
            it["slack_ts"] = ts
            for name in SEED_REACTIONS:
                _bot_react(token, channel, ts, name)
                time.sleep(0.35)  # 이모지 5개 연속 → rate limit 회피
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 슬랙 발송 실패({it.get('url')}): {e}")
            it["slack_ts"] = ""
        time.sleep(1.1)  # rate limit (1 msg/sec)
    return items


# ---------- 웹훅 폴백 (기존 방식) ----------

def build_messages(items, date_str, sheet_id, per_message=6):
    items = sorted(items, key=lambda x: x.get("insight", 0), reverse=True)
    msgs = []
    header = (
        f":robot_face: *오늘의 해외 AI 인사이트* ({date_str})\n"
        f"_엄선 {len(items)}건 · 전체 요약은 구글시트_\n"
        f":bar_chart: <{_sheet_link(sheet_id)}|구글시트 아카이브 열기>"
    )
    for i in range(0, len(items), per_message):
        chunk = items[i : i + per_message]
        body = "\n\n".join(_item_block(it, i + j + 1) for j, it in enumerate(chunk))
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


def post_empty_bot(token, channel, date_str):
    _bot_post(token, channel, f":robot_face: *오늘의 해외 AI 인사이트* ({date_str})\n_기준을 통과한 새 글이 없습니다._")
