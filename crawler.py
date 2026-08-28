"""RSS / 구글뉴스 기반 AI 뉴스 수집기."""
import re
import calendar
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus

import time

import feedparser

USER_AGENT = "Mozilla/5.0 (compatible; ai-news-crawler/1.0)"


def _google_news_rss(query, lang="en", country="US"):
    """RSS가 없는 소스를 구글뉴스 검색 RSS로 대체."""
    q = quote_plus(f"{query} when:2d")
    return f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={country}&ceid={country}:{lang}"


def _entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def _clean(text, limit=500):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)      # HTML 태그 제거
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def crawl(feeds, lookback_hours=26, max_per_feed=15):
    """설정된 피드들을 돌며 최근 기사만 수집. dict 리스트 반환."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items = []
    seen_titles = set()

    for feed in feeds:
        name = feed.get("name", "?")
        ftype = feed.get("type", "rss")
        url = _google_news_rss(feed["query"]) if ftype == "googlenews" else feed.get("url", "")
        if not url:
            print(f"[warn] {name}: url/query 없음 → 건너뜀")
            continue

        # 유튜브 RSS 등은 일시적 500/빈응답이 종종 있어 한 번 쉬었다 재시도한다.
        # (재시도 없이는 그날 그 채널이 통째로 0건이 됨)
        parsed = None
        for attempt in range(2):
            try:
                parsed = feedparser.parse(url, agent=USER_AGENT)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] {name}: 파싱 오류 {e}")
            if parsed is not None and parsed.entries:
                break
            if attempt == 0:
                time.sleep(3)
        if parsed is None or not parsed.entries:
            print(f"[warn] {name}: 항목 없음 ({url})")
            continue

        count = 0
        for entry in parsed.entries:
            if count >= max_per_feed:
                break
            published = _entry_time(entry)
            if published and published < cutoff:
                continue  # 오래된 기사 제외 (날짜 없으면 일단 포함)

            title = _clean(entry.get("title", ""), 300)
            link = entry.get("link", "")
            if "youtube.com/shorts/" in link:  # 쇼츠는 심층 콘텐츠가 아니라 제외
                continue
            if not title or not link:
                continue

            key = title.lower()[:80]
            if key in seen_titles:
                continue  # 여러 소스 중복 기사 제거
            seen_titles.add(key)

            items.append({
                "title": title,
                "url": link,
                "source": name,
                "category": feed.get("category", "기타"),
                "published": published.isoformat() if published else "",
                "snippet": _clean(entry.get("summary", ""), 500),
            })
            count += 1

        print(f"[ok] {name}: {count}건")

    # 최신순 정렬 (날짜 없는 항목은 뒤로)
    items.sort(key=lambda x: x["published"], reverse=True)
    return items
