"""오케스트레이터: 넓게수집 → 1단계 평가·선별 → 원문 긁기 → 2단계 심층요약 → 시트+슬랙."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

import crawler
import evaluator
import fetcher
import summarizer
import sheets_store
import slack_notify

load_dotenv()


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def require_env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"[error] 환경변수 {name} 가 설정되지 않았습니다.")
    return v


def main():
    cfg = load_yaml("config.yaml")
    feeds = load_yaml("sources.yaml").get("feeds", [])

    key = require_env("ANTHROPIC_API_KEY")
    webhook = require_env("SLACK_WEBHOOK_URL")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = require_env("SHEET_ID")
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    print("1) 넓게 수집...")
    items = crawler.crawl(feeds, lookback_hours=cfg.get("lookback_hours", 28),
                          max_per_feed=cfg.get("max_per_feed", 20))
    print(f"   수집 {len(items)}건")

    print("2) 중복 제거...")
    ws = sheets_store.open_worksheet(sheet_id, cfg.get("worksheet_name", "news"))
    seen = sheets_store.existing_urls(ws)
    items = [it for it in items if it["url"] not in seen][: cfg.get("candidate_cap", 90)]
    print(f"   신규 후보 {len(items)}건")

    if not items:
        slack_notify.post_empty(webhook, date_str)
        return

    print("3) 1단계 평가(유형·인사이트 점수)...")
    items = evaluator.evaluate(items, key, cfg.get("filter_model"))
    selected = evaluator.select(items, cfg.get("keep_types", []),
                                cfg.get("min_insight_score", 3), cfg.get("curated_count", 30))
    print(f"   선별 {len(selected)}건")

    if not selected:
        slack_notify.post_empty(webhook, date_str)
        return

    print("4) 원문 긁기...")
    for it in selected:
        it["fulltext"] = fetcher.fetch_fulltext(it["url"], fallback=it.get("snippet", ""))

    print("5) 2단계 심층요약...")
    selected = summarizer.summarize(selected, key, cfg.get("summary_model"))
    dropped = [it for it in selected if it.get("_thin")]
    selected = [it for it in selected if not it.get("_thin") and it.get("summary_bullets")]
    if dropped:
        print(f"   본문 확보 실패로 {len(dropped)}건 제외 (구글뉴스 리다이렉트 등)")
    if not selected:
        slack_notify.post_empty(webhook, date_str)
        return

    print("6) 시트 저장...")
    sheets_store.append_items(ws, selected)

    print("7) 슬랙 발송...")
    msgs = slack_notify.build_messages(selected, date_str, sheet_id,
                                       per_message=cfg.get("slack_items_per_message", 6))
    slack_notify.post_all(webhook, msgs)

    print(f"완료 ✅  ({len(selected)}건)")


if __name__ == "__main__":
    main()
