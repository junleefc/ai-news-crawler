"""슬랙 이모지 평가 수거 → 시트 기록 → 취향 프로필 생성.

- 봇이 발송 시 🔥👍👎를 1개씩 미리 달아둠 → 사용자가 탭하면 count가 2가 됨.
  따라서 count >= 2 인 이모지가 사용자의 평가.
- 발송 후 feedback_wait_days 지나도 반응 없으면 '무반응'으로 확정.
  무반응은 '의견 없음'이지 '싫음'이 아니므로 취향 학습에 반영하지 않는다(가중치 0).
  싫다는 신호는 사용자가 1점/2점으로 명시적으로 준 것만 쓴다.
"""
import time
import re
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API = "https://slack.com/api"
# 여러 개 눌렀으면 극단값 우선 (5/1이 3/4보다 강한 의사표시)
PRIORITY = ["1-최악", "5-최고", "2-별로", "4-좋음", "3-보통"]


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
                # 반응 없음 → wait_days 지났으면 '무반응'(의견 없음)으로 확정. 학습엔 미반영.
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


# 무반응은 0 — 안 누른 건 '싫다'가 아니라 '의견 없음'이다.
# 싫다는 표시는 사용자가 1점/2점으로 명시적으로 준다.
DEFAULT_WEIGHTS = {"5-최고": 3, "4-좋음": 1, "3-보통": 0, "2-별로": -2, "1-최악": -4, "무반응": 0}


def build_profile(ws, sheets_store, interests, uninterests, max_terms=15, manual=None,
                  weights=None, api_key=None, model=None):
    """평가 데이터 + 기본 관심사 + 사용자 직접 지시로 취향 프로필 텍스트 생성.

    핵심 원칙: 4·5점은 그 '사건'이 아니라 그런 '카테고리'의 콘텐츠가 좋다는 뜻이다.
    그래서 개별 키워드(예: 'OpenAI 해킹')를 그대로 쓰지 않고, AI로 한 단계
    추상화한 카테고리(예: 'AI 보안 사고의 심층 분석')로 바꿔 학습한다.
    이렇게 해야 같은 사건 기사가 반복 선정되는 것을 막을 수 있다."""
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
        W = weights or DEFAULT_WEIGHTS
        topic, source = Counter(), Counter()
        for r in rated:
            w = W.get(r["rating"], 0)
            for k in (x.strip() for x in r["keywords"].split(",")):
                if k:
                    topic[k] += w
            if r["source"]:
                source[r["source"]] += w
        liked = [t for t, s in topic.most_common() if s > 0][:max_terms]
        # 사용자가 직접 선언한 관심사와 겹치는 말은 '싫어함'으로 넘기지 않는다.
        # (개별 기사가 별로였을 뿐인데 관심 분야 전체가 배제되는 것을 막음)
        interest_text = " ".join(interests).lower()
        disliked = [t for t, s in sorted(topic.items(), key=lambda x: x[1])
                    if s < 0 and not _overlaps(t, interest_text)][:max_terms]
        # 키워드 → 카테고리 추상화 (실패 시 키워드 그대로 폴백)
        cats = _generalize_categories(rated, liked, disliked, api_key, model)
        if cats:
            if cats.get("liked"):
                lines.append("[선호 카테고리 — 이런 '종류'의 콘텐츠] " + ", ".join(cats["liked"]))
            if cats.get("disliked"):
                lines.append("[비선호 카테고리] " + ", ".join(cats["disliked"]))
            lines.append("[중요] 위 선호는 카테고리 수준의 신호다. 특정 회사나 특정 사건 자체를"
                         " 좋아한다는 뜻이 아니다. 이미 여러 번 다뤄진 사건·소재의 반복 기사는"
                         " 선호 카테고리에 속하더라도 새 관점이나 새 정보가 없으면 fit을 낮게 줘라.")
        else:
            if liked:
                lines.append("[좋아한 주제] " + ", ".join(liked))
            if disliked:
                lines.append("[싫어한 주제] " + ", ".join(disliked))
        src_like = [s for s, v in source.most_common() if v > 0][:5]
        if src_like:
            lines.append("[선호 출처(약한 신호)] " + ", ".join(src_like))
        n_real = sum(1 for r in rated if r["rating"] != "무반응")
        lines.append(f"(취향 학습에 쓰인 명시 평가 {n_real}건 기준. 무반응은 의견 없음으로 보고 제외)")
    return "\n".join(lines)


def _generalize_categories(rated, liked_kw, disliked_kw, api_key, model):
    """좋아한/싫어한 기사 제목·키워드를 상위 카테고리로 추상화한다.
    '허깅페이스 해킹' → 'AI 보안 사고 심층 분석' 같은 식."""
    if not api_key or not model:
        return None
    liked_titles = [r["title"] for r in rated if r["rating"] in ("5-최고", "4-좋음")][-30:]
    bad_titles = [r["title"] for r in rated if r["rating"] in ("1-최악", "2-별로")][-30:]
    if not liked_titles:
        return None
    prompt = (
        "사용자가 뉴스레터에서 좋다고 평가한 기사와 별로라고 평가한 기사 목록이다.\n\n"
        "[좋아한 기사]\n" + "\n".join(f"- {t}" for t in liked_titles) +
        "\n(관련 키워드: " + ", ".join(liked_kw[:15]) + ")\n\n"
        "[별로였던 기사]\n" + "\n".join(f"- {t}" for t in bad_titles) +
        "\n(관련 키워드: " + ", ".join(disliked_kw[:15]) + ")\n\n"
        "개별 사건·회사 이름이 아니라, 사용자가 좋아하는 콘텐츠의 '종류'를 추상화하라.\n"
        "예: '허깅페이스 해킹 사건'(X) → 'AI 보안 사고의 실제 사례 심층 분석'(O)\n"
        '선호 5~8개, 비선호 3~6개를 JSON으로: {"liked":["..."],"disliked":["..."]}'
    )
    try:
        from anthropic import Anthropic
        from evaluator import _extract_json
        resp = Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}])
        r = _extract_json(next(b.text for b in resp.content if getattr(b, "text", None)),
                          default={})
        if isinstance(r, dict) and r.get("liked"):
            return {"liked": r.get("liked", [])[:8], "disliked": r.get("disliked", [])[:6]}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 카테고리 추상화 실패(키워드로 폴백): {e}")
    return None


def _overlaps(term, interest_text):
    """키워드가 선언된 관심사 문구와 실질적으로 겹치는지."""
    t = term.strip().lower()
    if len(t) < 2:
        return False
    if t in interest_text:
        return True
    words = [w for w in re.split(r"[^0-9a-z가-힣]+", t) if len(w) > 1]
    return bool(words) and all(w in interest_text for w in words)


def rated_clean(rows):
    """테스트/시스템 행은 학습에서 제외."""
    return [r for r in rows
            if r.get("source") != "System" and "테스트" not in (r.get("title") or "")]


def build_stats(ws, sheets_store, top=10):
    """profile 탭에 함께 기록할 평가 통계 (사람이 보기 좋은 형태)."""
    rated = sheets_store.rated_rows(ws)
    if not rated:
        return ["[평가 현황] 아직 평가 데이터가 없습니다. 슬랙에서 5️⃣~1️⃣을 눌러주세요."]
    cnt = Counter(r["rating"] for r in rated)
    out = ["[평가 현황]",
           f"- 5️⃣최고 {cnt.get('5-최고',0)} / 4️⃣좋음 {cnt.get('4-좋음',0)} / "
           f"3️⃣보통 {cnt.get('3-보통',0)} / 2️⃣별로 {cnt.get('2-별로',0)} / "
           f"1️⃣최악 {cnt.get('1-최악',0)} / 무반응 {cnt.get('무반응',0)}  (총 {len(rated)}건)"]
    src = Counter()
    for r in rated:
        if r["rating"] in ("5-최고", "4-좋음"):
            src[r["source"]] += 1
    if src:
        out.append("[좋아한 출처 TOP] " + ", ".join(f"{s}({c})" for s, c in src.most_common(top)))
    liked = [r["title"] for r in rated if r["rating"] == "5-최고"][:5]
    if liked:
        out.append("[5️⃣ 최고로 꼽은 기사]")
        out += [f"  · {t}" for t in liked]
    return out
