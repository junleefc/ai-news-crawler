"""요약이 원문에 근거하는지 감사 — 불릿 단위까지 집계."""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets_store, fetcher
from anthropic import Anthropic

# 자격증명: 환경변수 우선, 없으면 로컬 키 파일 (있을 때만)
if not os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
    _key = os.path.expanduser("~/Downloads/analytical-rig-501911-d2-5eef3042066e.json")
    if os.path.exists(_key):
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = open(_key).read()
    else:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON 환경변수를 설정하세요.")
SHEET_ID = os.environ.get("SHEET_ID", "1eK_ODwe0rK72HkP7rZNxL5xiXpDYsU6cPz78e76D8Sg")
DATE = sys.argv[1] if len(sys.argv) > 1 else __import__("datetime").datetime.now().strftime("%Y-%m-%d")
LAST = int(sys.argv[2]) if len(sys.argv) > 2 else 999
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYS = ("너는 엄격한 사실 검증자다. 기사 원문과 요약 불릿들이 주어진다. "
       "각 불릿이 원문에 실제로 적혀 있는지 판정하라. "
       "번역·압축·의역은 통과. 원문에 없는 사실·수치·발언·해석이 섞이면 탈락. "
       "원문 전체를 끝까지 확인한 뒤 판정하라.")

ws = sheets_store.open_worksheet(SHEET_ID, "news")
rows = [r for r in ws.get_all_values()[1:] if r[0] == DATE][-LAST:]
print("검사 대상: %s %d건\n" % (DATE, len(rows)), flush=True)

items_ok = items_bad = skipped = 0
bul_total = bul_bad = 0
for i, r in enumerate(rows, 1):
    title, summary, url = r[4], r[6], r[8]
    bullets = [b.strip("• ").strip() for b in summary.split("\n") if b.strip()]
    body = fetcher.fetch_fulltext(url)
    if len(body) < 500:
        skipped += 1
        print("[%2d] -- 원문 재수집 실패(%d자) %s" % (i, len(body), title[:38]), flush=True)
        continue
    listed = "\n".join("%d. %s" % (j, b) for j, b in enumerate(bullets))
    prompt = ("===== 원문 =====\n%s\n===== 원문 끝 =====\n\n"
              "===== 검증할 불릿 =====\n%s\n\n"
              'JSON만: {"bullets":[{"index":0,"ok":true,"why":"근거 없으면 사유"}]}' % (body, listed))
    try:
        resp = client.messages.create(model="claude-sonnet-5", max_tokens=4000,
                                      system=SYS, messages=[{"role": "user", "content": prompt}])
        raw = next(b.text for b in resp.content if hasattr(b, "text"))
        t = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
        d = json.loads(t[t.find("{"):t.rfind("}") + 1])
    except Exception as e:
        print("[%2d] 검증오류 %s" % (i, str(e)[:70]), flush=True)
        continue
    bad = [b for b in d.get("bullets", []) if not b.get("ok")]
    bul_total += len(bullets); bul_bad += len(bad)
    if bad:
        items_bad += 1
        print("[%2d] X %s" % (i, title[:44]), flush=True)
        for b in bad[:2]:
            print("       - %s" % str(b.get("why", ""))[:88], flush=True)
    else:
        items_ok += 1
        print("[%2d] O %s" % (i, title[:44]), flush=True)

print("\n===== 결과 =====")
n = items_ok + items_bad
print("기사 단위: 정상 %d / 문제 %d  (문제율 %d%%)" % (items_ok, items_bad, items_bad*100//max(n,1)))
print("불릿 단위: 전체 %d개 중 문제 %d개  (오류율 %d%%)" % (bul_total, bul_bad, bul_bad*100//max(bul_total,1)))
if skipped: print("재수집 실패로 제외: %d건" % skipped)
