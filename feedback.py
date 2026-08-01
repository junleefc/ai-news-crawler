"""슬랙 이모지 평가 수거 → 시트 기록 → 취향 프로필 생성.

- 봇이 발송 시 🔥👍👎를 1개씩 미리 달아둠 → 사용자가 탭하면 count가 2가 됨.
  따라서 count >= 2 인 이모지가 사용자의 평가.
- 발송 후 feedback_wait_days 지나도 반응 없으면 '무반응'으로 확정 (약한 부정 신호).
"""
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API = "https://slack.com/api"
PRIORITY = ["최고", "좋다", "별로"]  # 여러 개 눌렀으면 이 순서로 우선


def collect_ratings(token, channel, ws, sheets_store, rating_emojis, wait_days=2):
    """평가 대기 행들의 슬랙 리액션을 읽어 시트에 기록."""
    pending = sheets_store.rows_pending_rating(ws)
    if not pending:
        print("   평가 대기 항목 없음")
        return 0
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    results = {}
    for p in pending:
        try:
            r = requests.get(f"{API}/reactions.get",
                             params={"channel": channel, "timestamp": p["ts"]},
                             headers={"Authorization": f"Bearer {token}"}, timeout=15).json()
            if not r.get("ok"):
                continue
            reactions = (r.get("message") or {}).get("reactions") or []
            found = set()
            for rx in reactions:
                label = rating_emojis.get(rx.get("name"))
                if label and rx.get("count", 0) >= 2:  # 시드 1 + 사용자 탭 = 2
                    found.add(label)
            if found:
                results[p["row"]] = next(l for l in PRIORITY if l in found)
            else:
                # 반응 없음 → 발송 후 wait_days 지났으면 무반응 확정
                try:
                    sent = datetime.strptime(p["date"], "%Y-%m-%d").date()
                    if (today - sent).days >= wait_days:
                        results[p["row"]] = "무반응"
                except ValueError:
                    pass
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 리액션 조회 실패(ts={p['ts']}): {e}")
        time.sleep(0.5)
    sheets_store.write_ratings(ws, results)
    print(f"   평가 기록: {len(results)}건 (확인 대기 {len(pending) - len(results)}건)")
    return len(results)


def build_profile(ws, sheets_store, interests, uninterests, max_terms=15, manual=None):
    """평가 데이터 + 기본 관심사 + 사용자 직접 지시로 취향 프로필 텍스트 생성."""
    lines = ["[사용자 관심사]"]
    lines += [f"- 관심: {i}" for i in interests]
    lines += [f"- 비관심: {u}" for u in uninterests]
    if manual:
        lines.append("[사용자가 직접 적은 지시 — 최우선 반영]")
        lines += [f"- {m}" for m in manual]

    rated = [r for r in rated_clean(sheets_store.rated_rows(ws))]
    if rated:
        # 주제(키워드)와 출처를 분리 집계하고, 순점수(좋아요-싫어요)로 판단.
        # 양쪽에 다 나오는 항목은 상쇄돼 사라짐 → 노이즈 제거, 확실한 신호만 남김.
        W = {"최고": 2, "좋다": 1, "별로": -2, "무반응": -1}
        topic, source = Counter(), Counter()
        for r in rated:
            w = W.get(r["rating"], 0)
            for k in (x.strip() for x in r["keywords"].split(",")):
                if k:
                    topic[k] += w
            if r["source"]:
                source[r["source"]] += w
        liked = [t for t, s in topic.most_common() if s > 0][:max_terms]
        disliked = [t for t, s in sorted(topic.items(), key=lambda x: x[1]) if s < 0][:max_terms]
        if liked:
            lines.append("[좋아한 주제] " + ", ".join(liked))
        if disliked:
            lines.append("[싫어한 주제] " + ", ".join(disliked))
        src_like = [s for s, v in source.most_common() if v > 0][:5]
        if src_like:
            lines.append("[선호 출처(약한 신호)] " + ", ".join(src_like))
        n_real = sum(1 for r in rated if r["rating"] != "무반응")
        lines.append(f"(평가 데이터: 명시 평가 {n_real}건, 무반응 포함 {len(rated)}건 기반)")
    return "\n".join(lines)


def rated_clean(rows):
    """테스트/시스템 행은 학습에서 제외."""
    return [r for r in rows
            if r.get("source") != "System" and "테스트" not in (r.get("title") or "")]


def build_stats(ws, sheets_store, top=10):
    """profile 탭에 함께 기록할 평가 통계 (사람이 보기 좋은 형태)."""
    rated = sheets_store.rated_rows(ws)
    if not rated:
        return ["[평가 현황] 아직 평가 데이터가 없습니다. 슬랙에서 🔥👍👎를 눌러주세요."]
    cnt = Counter(r["rating"] for r in rated)
    out = ["[평가 현황]",
           f"- 🔥최고 {cnt.get('최고',0)} / 👍좋다 {cnt.get('좋다',0)} / "
           f"👎별로 {cnt.get('별로',0)} / 무반응 {cnt.get('무반응',0)}  (총 {len(rated)}건)"]
    src = Counter()
    for r in rated:
        if r["rating"] in ("최고", "좋다"):
            src[r["source"]] += 1
    if src:
        out.append("[좋아한 출처 TOP] " + ", ".join(f"{s}({c})" for s, c in src.most_common(top)))
    liked = [r["title"] for r in rated if r["rating"] == "최고"][:5]
    if liked:
        out.append("[🔥 최고로 꼽은 기사]")
        out += [f"  · {t}" for t in liked]
    return out
