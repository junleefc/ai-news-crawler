"""1단계 평가: 제목+스니펫만 보고 유형/인사이트 점수를 싸게 매겨 선별."""
import json
import re

from anthropic import Anthropic

# 통과 후보 유형과 제외 유형을 모델에 명확히 알려준다.
SYSTEM = (
    "너는 해외 AI 뉴스를 선별하는 큐레이터다. 각 기사를 아래 유형 중 하나로 분류하고 "
    "인사이트 점수(1~5)를 매긴다.\n"
    "유형: 심층분석 / 오피니언·전략 / 실전케이스 / 단순소식 / 신기술발표 / 광고성\n"
    "- 심층분석: 데이터·근거로 현상을 파고드는 글\n"
    "- 오피니언·전략: 업계 흐름 해석·주장·전략적 관점\n"
    "- 실전케이스: 실제 기업/팀의 도입·구현 사례\n"
    "- 단순소식: 사실 전달 위주의 짧은 뉴스\n"
    "- 신기술발표: 새 모델/제품/논문 출시 발표\n"
    "- 광고성: 후원(Presented by)·보도자료·홍보\n"
    "인사이트 점수는 '읽을 가치의 깊이'다. 단순 발표·홍보는 낮게."
)


def _extract_json(text):
    text = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    s, e = text.find("["), text.rfind("]")
    return json.loads(text[s : e + 1] if s != -1 else text)


def evaluate(items, api_key, model, batch_size=25):
    """items 각각에 type/insight 부여해서 반환."""
    client = Anthropic(api_key=api_key)
    for i, it in enumerate(items):
        it["_idx"] = i
    results = {}

    for b in range(0, len(items), batch_size):
        batch = items[b : b + batch_size]
        lines = "\n".join(
            json.dumps({"index": it["_idx"], "title": it["title"], "source": it["source"],
                        "snippet": it["snippet"][:300]}, ensure_ascii=False)
            for it in batch
        )
        prompt = (
            "다음 기사들을 분류하라(JSON Lines 입력).\n" + lines +
            '\n\nJSON 배열만 출력: [{"index":0,"type":"심층분석","insight":4}]'
        )
        try:
            resp = client.messages.create(
                model=model, max_tokens=1500, system=SYSTEM,
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
        it.pop("_idx", None)
    return items


def select(items, keep_types, min_insight, limit):
    """유형·점수로 거르고 인사이트 높은 순으로 상위 N개 선별."""
    passed = [it for it in items if it["type"] in keep_types and it["insight"] >= min_insight]
    passed.sort(key=lambda x: x["insight"], reverse=True)
    return passed[:limit]
