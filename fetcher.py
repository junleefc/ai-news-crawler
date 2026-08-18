"""기사 원문 본문 추출.
1) trafilatura(무료) 시도 → 2) 실패 시 firecrawl(키 있으면) 폴백.
둘 다 실패하면 빈 문자열 반환 (스니펫으로 대체하지 않음 → 환각 방지)."""
import os
import time
import requests
import trafilatura

FIRECRAWL_API = "https://api.firecrawl.dev/v1/scrape"

# 무료 한도(월 1,000 크레딧) 보호: 1회 실행당 firecrawl 호출 상한
_fc_used = 0


def firecrawl_used():
    return _fc_used


def _trafilatura(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text:
                return text.strip()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] trafilatura {url}: {e}")
    return ""


def _firecrawl(url, key, attempts=3):
    """일시적 실패(408/429/5xx/타임아웃)는 재시도. 원문이 빈 채로 넘어가면
    검증 단계가 멀쩡한 요약을 '근거 없음'으로 오판하므로 여기서 최대한 확보한다."""
    for i in range(attempts):
        try:
            r = requests.post(
                FIRECRAWL_API, timeout=60,
                headers={"Authorization": f"Bearer {key}"},
                json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            )
            if r.ok:
                return ((r.json().get("data") or {}).get("markdown") or "").strip()
            if r.status_code not in (408, 429, 500, 502, 503, 504):
                print(f"[warn] firecrawl {url}: HTTP {r.status_code}")
                return ""
            print(f"[info] firecrawl 재시도({i+1}/{attempts}) HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            print(f"[info] firecrawl 재시도({i+1}/{attempts}): {e}")
        time.sleep(2 * (i + 1))
    print(f"[warn] firecrawl 최종 실패 {url[:60]}")
    return ""


def fetch_fulltext(url, max_chars=8000, firecrawl_cap=20):
    """원문 본문만 반환. 못 얻으면 '' (요약 단계에서 걸러짐).
    1) trafilatura(무료) → 2) 실패 시 firecrawl 폴백(상한 내에서만)."""
    global _fc_used
    text = _trafilatura(url)
    if len(text) < 400:  # 추출 실패/부실 → firecrawl 폴백
        key = os.environ.get("FIRECRAWL_API_KEY")
        if key and _fc_used < firecrawl_cap:
            _fc_used += 1
            fc = _firecrawl(url, key)
            if len(fc) > len(text):
                text = fc
        elif key:
            print(f"[info] firecrawl 상한({firecrawl_cap}) 도달 → 건너뜀: {url[:60]}")
    return text[:max_chars]
