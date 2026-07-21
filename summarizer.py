"""2단계 심층요약: 원문 전체를 읽고 상세 요약 + 키워드 + '왜 중요' 생성.
★환각 방지: 원문에 실제로 있는 내용만 사용. 원문 부실하면 요약 안 함."""
import json
import re

from anthropic import Anthropic

SYSTEM = (
    "너는 해외 AI 뉴스를 한국어로 정리하는 전문 에디터다.\n"
    "★가장 중요한 규칙: 오직 아래 제공된 '원문'에 실제로 적혀 있는 내용만 사용한다. "
    "원문에 없는 사실·수치·인물·배경지식·업계 일반론을 절대 추가하거나 추론하지 마라. "
    "그럴듯해 보여도 원문에 없으면 쓰지 않는다. 원문의 핵심 주장을 있는 그대로 충실히 옮긴다.\n"
    "출력 항목:\n"
    "- ko_headline: 원문 내용과 정확히 일치하는 한국어 제목\n"
    "- why: 이 소식의 핵심 의미 한 문장 (원문에 근거)\n"
    "- summary: 원문의 핵심을 담은 불릿 3~5개. 각 불릿은 원문에 실제로 있는 사실만.\n"
    "- keywords: 원문 핵심을 나타내는 키워드 3개"
)


def _extract_json(text):
    text = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s : e + 1] if s != -1 else text)


def summarize_one(item, api_key, model, min_body_chars=700, client=None):
    client = client or Anthropic(api_key=api_key)
    body = (item.get("fulltext") or "").strip()
    # 원문을 충분히 못 얻으면 억지 요약(=환각) 대신 제외 대상으로 표시.
    if len(body) < min_body_chars:
        item.update(ko_headline=item["title"], why="", summary_bullets=[], keywords=[], _thin=True)
        return item
    prompt = (
        f"제목: {item['title']}\n출처: {item['source']}\n\n"
        f"===== 원문 (이 안의 내용만 사용) =====\n{body}\n===== 원문 끝 =====\n\n"
        "위 원문에 실제로 있는 내용만으로 아래 JSON을 채워라. 원문에 없는 건 절대 넣지 마라.\n"
        '{"ko_headline":"...","why":"한 문장","summary":["불릿1","불릿2","불릿3"],'
        '"keywords":["키워드1","키워드2","키워드3"]}'
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=1200, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        r = _extract_json(resp.content[0].text)
        item["ko_headline"] = r.get("ko_headline") or item["title"]
        item["why"] = r.get("why", "")
        item["summary_bullets"] = r.get("summary") or []
        item["keywords"] = r.get("keywords") or []
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 심층요약 실패 {item['url']}: {e}")
        item.update(ko_headline=item["title"], why="", summary_bullets=[], keywords=[], _thin=True)
    return item


def summarize(items, api_key, model, min_body_chars=700):
    client = Anthropic(api_key=api_key)
    return [summarize_one(it, api_key, model, min_body_chars=min_body_chars, client=client) for it in items]
