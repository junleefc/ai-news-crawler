"""오케스트레이터: 크롤링 → 중복제거 → 한글요약 → 시트저장 → 슬랙발송."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

import crawler
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
    config = load_yaml("config.yaml")
    feeds = load_yaml("sources.yaml").get("feeds", [])

    anthropic_key = require_env("ANTHROPIC_API_KEY")
    slack_webhook = require_env("SLACK_WEBHOOK_URL")
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = require_env("SHEET_ID")

    date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    print("1) 크롤링...")
    items = crawler.crawl(
        feeds,
        lookback_hours=config.get("lookback_hours", 26),
        max_per_feed=config.get("max_per_feed", 15),
    )
    print(f"   수집 {len(items)}건")

    print("2) 시트 연결 + 중복 제거...")
    ws = sheets_store.open_worksheet(sheet_id, config.get("worksheet_name", "news"))
    seen = sheets_store.existing_urls(ws)
    new_items = [it for it in items if it["url"] not in seen][: config.get("max_items_per_run", 20)]
    print(f"   신규 {len(new_items)}건")

    if len(new_items) < config.get("min_items_to_post", 1):
        if config.get("post_when_empty", True):
            slack_notify.post_empty(slack_webhook, date_str)
        print("신규 기사 없음 → 종료")
        return

    print("3) 한글 요약 (Claude)...")
    new_items = summarizer.summarize(new_items, anthropic_key, config.get("model"))

    print("4) 시트에 저장...")
    sheets_store.append_items(ws, new_items)

    print("5) 슬랙 발송...")
    msg = slack_notify.build_message(new_items, date_str, sheet_id, max_items=config.get("slack_max_items", 8))
    slack_notify.post(slack_webhook, msg)

    print("완료 ✅")


if __name__ == "__main__":
    main()
