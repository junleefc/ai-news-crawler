"""Claude API로 기사들을 한국어 요약 + 중요도 판정."""
import json
import re

from anthropic import Anthropic

SYSTEM = (
    "너는 해외 AI 뉴스를 한국어로 정리하는 전문 에디터다. "
    "각 기사에 대해 (1) 자연스러운 한국어 헤드라인, (2) 2~3문장 핵심 요약, "
    "(3) 중요도(높음/중간/낮음)를 만든다. "
    "요약은 과장 없이 사실 기반으로 간결하게 쓴다. "
    "단순 홍보/보도자료성 기사는 중요도를 '낮음'으로 판단한다."
)


def _build_prompt(batch):
    lines = [
        json.dumps(
            {"index": it["_idx"], "title": it["title"], "source": it["source"], "snippet": it["snippet"]},
            ensure_ascii=False,
        )
        for it in batch
    ]
    joined = "\n".join(lines)
    return (
        "다음은 해외 AI 뉴스 기사 목록(JSON Lines)이다.\n"
        f"{joined}\n\n"
        "각 기사에 대해 아래 형식의 JSON 배열만 출력하라. 코드블록이나 설명 없이 JSON만.\n"
        '[{"index": 0, "ko_headline": "한국어 제목", "ko_summary": "2~3문장 한국어 요약", "importance": "높음"}]'
    )


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def summarize(items, api_key, model, batch_size=12):
    client = Anthropic(api_key=api_key)
    for i, it in enumerate(items):
        it["_idx"] = i

    results = {}
    for b in range(0, len(items), batch_size):
        batch = items[b : b + batch_size]
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=3000,
                system=SYSTEM,
                messages=[{"role": "user", "content": _build_prompt(batch)}],
            )
            arr = _extract_json(resp.content[0].text)
            for r in arr:
                results[r["index"]] = r
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 요약 실패(배치 {b}): {e}")

    enriched = []
    for it in items:
        r = results.get(it["_idx"], {})
        it["ko_headline"] = r.get("ko_headline") or it["title"]
        it["ko_summary"] = r.get("ko_summary") or it["snippet"][:150]
        it["importance"] = r.get("importance") if r.get("importance") in ("높음", "중간", "낮음") else "중간"
        it.pop("_idx", None)
        enriched.append(it)
    return enriched
