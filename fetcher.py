"""기사 원문 본문 추출 (무료 라이브러리 trafilatura). 실패 시 RSS 스니펫으로 폴백."""
import trafilatura


def fetch_fulltext(url, fallback="", max_chars=6000):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text and len(text) > 200:
                return text[:max_chars]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 본문 추출 실패 {url}: {e}")
    return fallback
