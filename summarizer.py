"""2단계 심층요약: 원문 전체를 읽고 상세 요약 + 키워드 + '왜 중요' 생성."""
import json
import re

from anthropic import Anthropic

SYSTEM = (
    "너는 해외 AI 뉴스를 한국어로 깊이 있게 정리하는 전문 에디터다. "
    "기사 원문을 읽고 아래를 만든다:\n"
    "- ko_headline: 자연스러운 한국어 제목\n"
    "- why: '왜 중요한지' 한 문장 (맥락/함의)\n"
    "- summary: 핵심을 담은 상세 요약. 불릿 4~6개, 각 불릿은 한 문장. "
    "수치·데이터·전략적 함의·리스크를 반드시 포함. 단순 사실 나열이 아니라 '그래서 뭐가 중요한가'가 드러나게.\n"
    "- keywords: 핵심 키워드 3개\n"
    "과장 없이 사실 기반으로 쓴다."
)


def _extract_json(text):
    text = re.sub(r"^```(?:json)?", "", text.strip()).strip()
    text = re.sub(r"```$", "", text).strip()
    s, e = text.find("{"), text.rfind("}")
    return json.loads(text[s : e + 1] if s != -1 else text)


def summarize_one(item, api_key, model, client=None):
    client = client or Anthropic(api_key=api_key)
    body = item.get("fulltext") or item.get("snippet") or item["title"]
    prompt = (
        f"제목: {item['title']}\n출처: {item['source']}\n\n원문:\n{body}\n\n"
        '아래 JSON만 출력:\n'
        '{"ko_headline":"...","why":"한 문장","summary":["불릿1","불릿2","불릿3","불릿4"],'
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
        item["ko_headline"] = item["title"]
        item["why"] = ""
        item["summary_bullets"] = [item.get("snippet", "")[:150]]
        item["keywords"] = []
    return item


def summarize(items, api_key, model):
    client = Anthropic(api_key=api_key)
    return [summarize_one(it, api_key, model, client=client) for it in items]
