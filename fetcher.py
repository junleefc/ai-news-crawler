"""기사 원문 본문 추출.
1) trafilatura(무료) 시도 → 2) 실패 시 firecrawl(키 있으면) 폴백.
둘 다 실패하면 빈 문자열 반환 (스니펫으로 대체하지 않음 → 환각 방지)."""
import glob
import html
import os
import re
import subprocess
import tempfile
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


def _firecrawl(url, key, attempts=3, main_only=True):
    """일시적 실패(408/429/5xx/타임아웃)는 재시도. 원문이 빈 채로 넘어가면
    검증 단계가 멀쩡한 요약을 '근거 없음'으로 오판하므로 여기서 최대한 확보한다."""
    for i in range(attempts):
        try:
            r = requests.post(
                FIRECRAWL_API, timeout=60,
                headers={"Authorization": f"Bearer {key}"},
                json={"url": url, "formats": ["markdown"], "onlyMainContent": main_only},
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


def _firecrawl_youtube_transcript(url, cap=20):
    """firecrawl로 유튜브 페이지를 긁으면 '## Transcript' 섹션에 전체 자막이 온다.
    설명·리소스 링크 등 자막이 아닌 부분은 잘라낸다."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return ""
    global _fc_used
    if _fc_used >= cap:
        print(f"[info] firecrawl 상한({cap}) 도달 → 유튜브 자막 건너뜀: {url[:60]}")
        return ""
    _fc_used += 1
    md = _firecrawl(url, key, attempts=2, main_only=False)  # True면 Transcript가 잘림
    if not md:
        return ""
    i = md.find("## Transcript")
    if i == -1:
        return ""
    text = md[i + len("## Transcript"):]
    # 다음 헤더가 나오면 거기까지만 (자막 뒤에 다른 섹션이 붙는 경우)
    j = text.find("\n## ")
    if j != -1:
        text = text[:j]
    # 전체 페이지 모드라 플레이어 UI·썸네일·링크 잡동사니가 섞여 온다 → 대사만 남김
    text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", " ", text)   # 이미지·링크 마크다운
    text = re.sub(r"https?://\S+", " ", text)              # 남은 URL
    text = re.sub(r"\[music\]|\[Music\]|\[Applause\]|>>|NaN / NaN", " ", text)
    return " ".join(text.split())


def _youtube_transcript(url, max_chars, firecrawl_cap=20):
    """유튜브 영상은 본문 대신 자막(오디오 내용)을 가져온다.
    yt-dlp로 영어/한글 자막(자동 생성 포함)을 받아 텍스트로 변환."""
    base = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
            "--sub-langs", "en.*,ko.*", "--sub-format", "vtt",
            "--sleep-subtitles", "2"]  # 언어별 요청 사이 대기 (429 방지)
    # 클라우드(데이터센터 IP)에서 유튜브가 봇으로 보고 막는 경우가 있어
    # 브라우저 위장 → 안드로이드 클라이언트 순으로 재시도한다.
    attempts = [
        base,
        base + ["--impersonate", "chrome"],
        base + ["--extractor-args", "youtube:player_client=android"],
    ]
    with tempfile.TemporaryDirectory() as td:
        vtts = []
        for i, cmd in enumerate(attempts):
            try:
                subprocess.run(cmd + ["-o", f"{td}/sub{i}", url],
                               capture_output=True, timeout=120)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 자막 다운로드 실패: {e} ({url[:60]})")
                continue
            vtts = sorted(glob.glob(f"{td}/sub{i}*.vtt"))
            if vtts:
                if i > 0:
                    print(f"[info] 자막 우회 성공(시도 {i + 1}): {url[:60]}")
                break
            time.sleep(2)
        if not vtts:
            # 클라우드 IP는 유튜브가 봇으로 보고 전부 차단함(전 클라이언트 확인).
            # firecrawl은 자체 프록시로 접근하므로 유일하게 뚫리는 경로.
            fc = _firecrawl_youtube_transcript(url, cap=firecrawl_cap)
            if fc:
                print(f"[info] 자막을 firecrawl로 확보: {url[:60]}")
                return fc[:max_chars]
            print(f"[info] 자막 없음: {url[:60]}")
            return ""
        raw = open(vtts[0], encoding="utf-8", errors="ignore").read()
        lines = []
        for l in raw.split("\n"):
            l = re.sub(r"<[^>]+>", "", l.strip())
            if (not l or "-->" in l or l.startswith(("WEBVTT", "Kind:", "Language:"))
                    or re.match(r"^\d+$", l)):
                continue
            if lines and lines[-1] == l:  # VTT 특유의 중복 줄 제거
                continue
            lines.append(l)
        return html.unescape(" ".join(lines))[:max_chars]


def fetch_fulltext(url, max_chars=8000, firecrawl_cap=20):
    """원문 본문만 반환. 못 얻으면 '' (요약 단계에서 걸러짐).
    유튜브 → 자막 / 일반 웹 → 1) trafilatura(무료) → 2) firecrawl 폴백(상한 내)."""
    global _fc_used
    if "youtube.com/watch" in url or "youtu.be/" in url or "youtube.com/shorts/" in url:
        return _youtube_transcript(url, max_chars, firecrawl_cap)
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
