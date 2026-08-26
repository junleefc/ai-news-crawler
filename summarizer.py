"""2단계 심층요약: 원문 전체를 읽고 상세 요약 + 키워드 + '왜 중요' 생성.
★환각 방지: 원문에 실제로 있는 내용만 사용. 원문 부실하면 요약 안 함."""
import json
import re

from anthropic import Anthropic

SYSTEM = (
    "너는 해외 AI 뉴스를 한국어로 옮기는 번역·요약가다. 해설가가 아니다.\n"
    "★절대 규칙: 원문에 문장으로 적혀 있는 내용만 쓴다. 각 불릿은 원문의 특정 문단을 그대로 옮긴 것이어야 한다.\n"
    "다음은 원문에 명시돼 있지 않으면 절대 쓰지 마라:\n"
    "- 권고·제언 ('~해야 한다', '~가 필요하다')\n"
    "- 전망·시사점 ('~할 것으로 보인다', '이는 ~를 의미한다')\n"
    "- 배경 지식·업계 상식·유사 사례\n"
    "- 원문에 없는 인물 발언, 수치, 날짜, 기관명\n"
    "불릿 개수를 채우려고 내용을 지어내지 마라. 원문이 짧으면 불릿 2개여도 된다.\n"
    "출력 항목:\n"
    "- ko_headline: 원문 제목에 대응하는 한국어 제목\n"
    "- why: 원문이 직접 말하는 핵심 한 문장 (원문에 없는 해석 금지)\n"
    "- summary: 원문 문단을 옮긴 불릿 2~5개\n"
    "- keywords: 원문에 나오는 핵심어 3개"
)

VERIFY_SYSTEM = (
    "너는 엄격한 사실 검증자다. 기사 원문과 요약 불릿들이 주어진다. "
    "각 불릿이 원문에 실제로 적혀 있는 내용인지 판정하라.\n"
    "- 원문 문장을 번역·압축한 것 → 통과(true)\n"
    "- 원문에 없는 권고·전망·해석·배경지식·수치·발언이 섞임 → 탈락(false)\n"
    "의심스러우면 탈락시켜라. 관대하게 판정하지 마라."
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
            model=model, max_tokens=8000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        r = _extract_json(next(b.text for b in resp.content if hasattr(b,'text')))
        item["ko_headline"] = r.get("ko_headline") or item["title"]
        item["why"] = r.get("why", "")
        item["summary_bullets"] = r.get("summary") or []
        item["keywords"] = r.get("keywords") or []
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 심층요약 실패 {item['url']}: {e}")
        item.update(ko_headline=item["title"], why="", summary_bullets=[], keywords=[], _thin=True)
    return item


def verify_one(item, model, client, min_kept=2):
    """요약 불릿을 원문과 대조해 근거 없는 것을 제거. 남는 게 너무 적으면 항목 자체를 버림."""
    bullets = item.get("summary_bullets") or []
    body = (item.get("fulltext") or "").strip()
    if not bullets or not body:
        return item
    listed = "\n".join(f"{i}. {b}" for i, b in enumerate(bullets))
    prompt = (
        f"===== 원문 =====\n{body}\n===== 원문 끝 =====\n\n"
        f"===== 검증할 요약 =====\n제목: {item.get('ko_headline','')}\n"
        f"한줄평: {item.get('why','')}\n불릿:\n{listed}\n\n"
        '각 불릿과 한줄평이 원문에 근거하는지 JSON으로 판정:\n'
        '{"bullets":[{"index":0,"ok":true},{"index":1,"ok":false}],"why_ok":true}'
    )
    try:
        resp = client.messages.create(
            model=model, max_tokens=6000, system=VERIFY_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next(b.text for b in resp.content if hasattr(b, "text"))
        r = _extract_json(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 검증 실패(원본 유지) {item.get('url','')}: {e}")
        return item

    keep, dropped = [], 0
    oks = {b.get("index"): b.get("ok") for b in r.get("bullets", []) if isinstance(b, dict)}
    for i, b in enumerate(bullets):
        if oks.get(i, True):
            keep.append(b)
        else:
            dropped += 1
    if not r.get("why_ok", True):
        item["why"] = ""
    if dropped:
        print(f"   근거없는 불릿 {dropped}개 제거: {item.get('ko_headline','')[:38]}")
    item["summary_bullets"] = keep
    # 남은 근거가 너무 적으면 신뢰할 수 없는 항목으로 간주하고 제외
    if len(keep) < min_kept:
        item["_thin"] = True
        print(f"   근거 부족으로 항목 제외: {item.get('ko_headline','')[:38]}")
    return item


# 본문을 못 읽은 페이지를 모델이 "내용 없음"이라고 요약해버리는 경우가 있다.
# 그대로 발송되면 빈 껍데기 뉴스가 되므로 여기서 걸러낸다.
_EMPTY_SIGNS = (
    "본문 내용 없음", "본문이 없", "내용 없음", "내비게이션만", "원문에 기사",
    "제공된 원문에", "실제 내용 없", "실제 기사 내용", "확인할 수 없",
    "접근할 수 없", "로그인이 필요", "페이지를 찾을 수 없", "내용을 확인",
)


def looks_empty(item):
    """제목/한줄평이 '본문 없음'을 말하고 있으면 껍데기로 판단."""
    text = (item.get("ko_headline", "") or "") + " " + (item.get("why", "") or "")
    return any(sign in text for sign in _EMPTY_SIGNS)


def summarize(items, api_key, model, min_body_chars=700, verify_model=None):
    client = Anthropic(api_key=api_key)
    out = []
    for it in items:
        summarize_one(it, api_key, model, min_body_chars=min_body_chars, client=client)
        if not it.get("_thin"):
            if looks_empty(it):
                it["_thin"] = True
                print(f"   빈 껍데기 제외: {it.get('ko_headline','')[:42]}")
            else:
                verify_one(it, verify_model or model, client)
        out.append(it)
    return out
