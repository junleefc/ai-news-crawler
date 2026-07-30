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
            for r in _extract_json(resp.content[0].text):
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


def select(items, keep_types, min_insight, limit, min_fit=1):
    """유형·인사이트·적합도로 거르고, fit 우선 + insight 순으로 상위 N개."""
    passed = [
        it for it in items
        if it["type"] in keep_types and it["insight"] >= min_insight and it.get("fit", 3) >= min_fit
    ]
    passed.sort(key=lambda x: (x.get("fit", 3), x["insight"]), reverse=True)
    return passed[:limit]
