"""1단계 평가: 제목+스니펫으로 유형/인사이트/관심적합도(fit)를 매겨 선별.
fit은 사용자 취향 프로필(기본 관심사 + 이모지 평가 학습)에 대한 적합도."""
import json
import re

from anthropic import Anthropic

SYSTEM = (
    "너는 특정 사용자를 위해 해외 AI 뉴스를 선별하는 개인화 큐레이터다. "
    "각 기사를 (1) 유형 분류, (2) 인사이트 점수, (3) 사용자 관심 적합도(fit)로 평가한다.\n"
    "유형: 심층분석 / 오피니언·전략 / 실전케이스 / 단순소식 / 신기술발표 / 광고성\n"
    "- 심층분석: 데이터·근거로 현상을 파고드는 글\n"
    "- 오피니언·전략: 업계 흐름 해석·주장·전략적 관점\n"
    "- 실전케이스: 실제 기업/팀의 도입·구현 사례\n"
    "- 단순소식: 사실 전달 위주의 짧은 뉴스\n"
    "- 신기술발표: 새 모델/제품/논문 출시 발표\n"
    "- 광고성: 후원(Presented by)·보도자료·홍보\n"
    "insight(1~5): 읽을 가치의 깊이. 단순 발표·홍보는 낮게.\n"
    "fit(1~5): 아래 제공되는 사용자 프로필과의 적합도. 관심사에 정면으로 맞으면 5, "
    "비관심 영역(예: 개발자 전용 딥테크)이면 깊어도 1~2. 프로필의 실제 평가 데이터를 최우선으로 반영."
)


def _extract_json(text):
    text = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    s, e = text.find("["), text.rfind("]")
    return json.loads(text[s : e + 1] if s != -1 else text)


def evaluate(items, api_key, model, profile="", batch_size=25):
    """items 각각에 type/insight/fit 부여."""
    client = Anthropic(api_key=api_key)
    for i, it in enumerate(items):
        it["_idx"] = i
    results = {}

    profile_block = f"===== 사용자 프로필 =====\n{profile}\n=====\n\n" if profile else ""

    for b in range(0, len(items), batch_size):
        batch = items[b : b + batch_size]
        lines = "\n".join(
            json.dumps({"index": it["_idx"], "title": it["title"], "source": it["source"],
                        "snippet": it["snippet"][:300]}, ensure_ascii=False)
            for it in batch
        )
        prompt = (
            profile_block
            + "다음 기사들을 평가하라(JSON Lines 입력).\n" + lines +
            '\n\nJSON 배열만 출력: [{"index":0,"type":"심층분석","insight":4,"fit":5}]'
        )
        try:
            resp = client.messages.create(
                model=model, max_tokens=2000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            for r in _extract_json(next(b.text for b in resp.content if hasattr(b,'text'))):
                results[r["index"]] = r
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 평가 실패(배치 {b}): {e}")

    for it in items:
        r = results.get(it["_idx"], {})
        it["type"] = r.get("type", "단순소식")
        it["insight"] = r.get("insight", 2) if isinstance(r.get("insight"), int) else 2
        it["fit"] = r.get("fit", 3) if isinstance(r.get("fit"), int) else 3
        it.pop("_idx", None)
    return items


DEDUP_SYSTEM = (
    "너는 뉴스 편집자다. 여러 매체가 보도한 기사 목록에서 '같은 사건/발표를 다룬 기사'를 하나의 그룹으로 묶는다. "
    "제목이 달라도 동일한 사건(같은 회사의 같은 발표·같은 사고·같은 조치)이면 같은 그룹이다. "
    "주제가 비슷할 뿐 서로 다른 사건이면 절대 묶지 마라."
)


def dedupe_stories(items, api_key, model):
    """같은 사건을 다룬 기사들을 묶어 대표 1건만 남긴다 (fit→insight 높은 순)."""
    if len(items) < 2:
        return items
    client = Anthropic(api_key=api_key)
    lines = "\n".join(
        json.dumps({"index": i, "title": it["title"], "source": it["source"]}, ensure_ascii=False)
        for i, it in enumerate(items)
    )
    prompt = (
        "다음 기사들을 같은 사건끼리 묶어라.\n" + lines +
        '\n\n같은 사건인 그룹만 JSON 배열로 출력(단독 기사는 넣지 마라): '
        '[{"group":[0,3,7]},{"group":[2,5]}]  없으면 []'
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=1500, system=DEDUP_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        groups = _extract_json(next(b.text for b in resp.content if hasattr(b,'text')))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 중복 사건 묶기 실패: {e}")
        return items

    drop = set()
    for g in groups:
        idxs = [i for i in g.get("group", []) if isinstance(i, int) and 0 <= i < len(items)]
        if len(idxs) < 2:
            continue
        # 대표: fit → insight 높은 것
        best = max(idxs, key=lambda i: (items[i].get("fit", 3), items[i].get("insight", 0)))
        for i in idxs:
            if i != best:
                drop.add(i)
        print(f"   중복 사건 {len(idxs)}건 → 1건: {items[best]['title'][:45]}")
    if drop:
        print(f"   중복 제거 {len(drop)}건")
    return [it for i, it in enumerate(items) if i not in drop]


STALE_SYSTEM = (
    "너는 뉴스 편집자다. 최근 며칠간 이미 독자에게 보낸 기사 목록이 주어진다. "
    "새로 들어온 기사가 그중 어떤 사건과 '같은 사건'이면서 '새로운 사실이 없는 재탕'인지 판단한다.\n"
    "- 같은 사건이어도 새 전개·새 수치·새 당사자·새 조치 등 이전에 없던 정보가 있으면 재탕이 아니다(통과).\n"
    "- 같은 사건을 관점만 바꿔 다시 정리한 수준이면 재탕이다(제외).\n"
    "- 이전 목록에 없는 사건이면 당연히 통과."
)


def filter_stale(items, recent, api_key, model):
    """최근 발송분과 같은 사건이면서 새 정보가 없는 기사를 제외."""
    if not items or not recent:
        return items
    client = Anthropic(api_key=api_key)
    prev = "\n".join(
        f"- {r['title']} ({r['keywords']})" for r in recent[-60:]
    )
    news = "\n".join(
        json.dumps({"index": i, "title": it["title"], "snippet": it.get("snippet", "")[:200]},
                   ensure_ascii=False)
        for i, it in enumerate(items)
    )
    prompt = (
        f"===== 최근 며칠간 이미 보낸 기사 =====\n{prev}\n\n"
        f"===== 새로 들어온 기사 =====\n{news}\n\n"
        '재탕(같은 사건 + 새 정보 없음)인 것만 JSON으로 출력: '
        '[{"index":0,"reason":"어제 보낸 X와 동일"}]  없으면 []'
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=1200, system=STALE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        stale = _extract_json(next(b.text for b in resp.content if hasattr(b,'text')))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 재탕 판별 실패: {e}")
        return items

    drop = set()
    for s in stale:
        i = s.get("index")
        if isinstance(i, int) and 0 <= i < len(items):
            drop.add(i)
            print(f"   재탕 제외: {items[i]['title'][:42]} ({s.get('reason','')[:30]})")
    if drop:
        print(f"   이전 발송과 중복 {len(drop)}건 제외")
    return [it for i, it in enumerate(items) if i not in drop]


def select(items, keep_types, min_insight, limit, min_fit=1):
    """유형·인사이트·적합도로 거르고, fit 우선 + insight 순으로 상위 N개."""
    passed = [
        it for it in items
        if it["type"] in keep_types and it["insight"] >= min_insight and it.get("fit", 3) >= min_fit
    ]
    passed.sort(key=lambda x: (x.get("fit", 3), x["insight"]), reverse=True)
    return passed[:limit]
