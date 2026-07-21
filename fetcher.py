"""기사 원문 본문 추출.
1) trafilatura(무료) 시도 → 2) 실패 시 firecrawl(키 있으면) 폴백.
둘 다 실패하면 빈 문자열 반환 (스니펫으로 대체하지 않음 → 환각 방지)."""
import os
import requests
import trafilatura

FIRECRAWL_API = "https://api.firecrawl.dev/v1/scrape"


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


def _firecrawl(url, key):
    try:
        r = requests.post(
            FIRECRAWL_API, timeout=45,
            headers={"Authorization": f"Bearer {key}"},
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        )
        if r.ok:
            return ((r.json().get("data") or {}).get("markdown") or "").strip()
        print(f"[warn] firecrawl {url}: HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] firecrawl {url}: {e}")
    return ""


def fetch_fulltext(url, max_chars=8000):
    """원문 본문만 반환. 못 얻으면 '' (요약 단계에서 걸러짐)."""
    text = _trafilatura(url)
    if len(text) < 400:  # 추출 실패/부실 → firecrawl 폴백 (FIRECRAWL_API_KEY 있을 때만)
        key = os.environ.get("FIRECRAWL_API_KEY")
        if key:
            fc = _firecrawl(url, key)
            if len(fc) > len(text):
                text = fc
    return text[:max_chars]
