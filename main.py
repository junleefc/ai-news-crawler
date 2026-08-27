"""오케스트레이터:
0) 어제 발송분 이모지 평가 수거 → 시트 기록 → 취향 프로필 생성
1) 넓게 수집 → 2) 중복 제거 → 3) 평가·선별(관심적합도 반영)
4) 원문 긁기 → 5) 심층요약(원문 기반만) → 6) 슬랙 발송(스레드+평가 이모지) → 7) 시트 저장
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

import crawler
import evaluator
import fetcher
import feedback
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
    require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = require_env("SHEET_ID")
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_CHANNEL_ID") or cfg.get("slack_channel_id", "")
    date_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")

    ws = sheets_store.open_worksheet(sheet_id, cfg.get("worksheet_name", "news"))

    profile = ""
    if bot_token and channel:
        print("0) 이모지 평가 수거...")
        feedback.collect_ratings(bot_token, channel, ws, sheets_store,
                                 cfg.get("rating_emojis", {}), cfg.get("feedback_wait_days", 2))
    manual = sheets_store.read_manual_directives(sheet_id)
    if manual:
        print(f"   직접 지시 {len(manual)}건 반영")
    profile = feedback.build_profile(ws, sheets_store,
                                     cfg.get("interests", []), cfg.get("uninterests", []),
                                     manual=manual, weights=cfg.get("rating_weights"),
                                     api_key=key, model=cfg.get("filter_model"))
    # 취향 프로필을 시트 'profile' 탭에 기록 (사용자가 눈으로 확인/수정)
    sheets_store.write_profile(sheet_id, profile, feedback.build_stats(ws, sheets_store))

    print("1) 넓게 수집...")
    items = crawler.crawl(feeds, lookback_hours=cfg.get("lookback_hours", 28),
                          max_per_feed=cfg.get("max_per_feed", 20))
    print(f"   수집 {len(items)}건")

    print("2) 중복 제거...")
    seen = sheets_store.existing_urls(ws)
    items = [it for it in items if it["url"] not in seen][: cfg.get("candidate_cap", 90)]
    print(f"   신규 후보 {len(items)}건")

    if not items:
        _post_empty(bot_token, channel, webhook, date_str)
        return

    print("3) 평가·선별 (관심적합도 반영)...")
    items = evaluator.evaluate(items, key, cfg.get("filter_model"), profile=profile)
    want = cfg.get("curated_count", 30)
    # 중복 제거로 빠지는 만큼 다른 기사가 채워지도록 넉넉히 뽑은 뒤 잘라낸다.
    pool = evaluator.select(items, cfg.get("keep_types", []),
                            cfg.get("min_insight_score", 3), want * 2,
                            min_fit=cfg.get("min_fit_score", 3))
    print(f"   1차 선별 {len(pool)}건 → 같은 사건 묶는 중...")
    pool = evaluator.dedupe_stories(pool, key, cfg.get("filter_model"))
    # 최근 발송분과 같은 사건이면서 새 정보 없는 재탕 제외
    recent = sheets_store.recent_items(ws, cfg.get("stale_lookback_days", 7))
    # (a) 제목 유사도 기반 결정적 제거 — 같은 실행 내부 + 과거 발송분
    pool = evaluator.drop_near_duplicates(pool, recent,
                                          cfg.get("title_dup_threshold", 0.6))
    # (b) 남은 것 중 '같은 사건 + 새 정보 없음'을 LLM으로 판별
    if recent:
        pool = evaluator.filter_stale(pool, recent, key, cfg.get("filter_model"))
    pool = evaluator.cap_per_source(pool, cfg.get("max_per_source"),
                                   cfg.get("source_caps"))
    selected = pool[:want]
    print(f"   최종 선별 {len(selected)}건")

    if not selected:
        _post_empty(bot_token, channel, webhook, date_str)
        return

    print("4) 원문 긁기...")
    cap = cfg.get("firecrawl_daily_cap", 20)
    for it in selected:
        it["fulltext"] = fetcher.fetch_fulltext(it["url"], firecrawl_cap=cap)
    if os.environ.get("FIRECRAWL_API_KEY"):
        print(f"   firecrawl 사용: {fetcher.firecrawl_used()}/{cap} 크레딧")

    print("5) 심층요약 (원문 기반만)...")
    selected = summarizer.summarize(selected, key, cfg.get("summary_model"),
                                    min_body_chars=cfg.get("min_body_chars", 700),
                                    verify_model=cfg.get("verify_model"))
    dropped = [it for it in selected if it.get("_thin")]
    selected = [it for it in selected if not it.get("_thin") and it.get("summary_bullets")]
    if dropped:
        print(f"   원문 확보 실패로 {len(dropped)}건 제외 (환각 방지)")
    if not selected:
        _post_empty(bot_token, channel, webhook, date_str)
        return

    print("6) 슬랙 발송...")
    if bot_token and channel:
        selected = slack_notify.send_digest_bot(bot_token, channel, selected, date_str, sheet_id)
    elif webhook:
        msgs = slack_notify.build_messages(selected, date_str, sheet_id,
                                           per_message=cfg.get("slack_items_per_message", 6))
        slack_notify.post_all(webhook, msgs)

    print("7) 시트 저장...")
    sheets_store.append_items(ws, selected)

    print(f"완료 ✅  ({len(selected)}건)")


def _post_empty(bot_token, channel, webhook, date_str):
    try:
        if bot_token and channel:
            slack_notify.post_empty_bot(bot_token, channel, date_str)
        elif webhook:
            slack_notify.post_empty(webhook, date_str)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 빈 알림 실패: {e}")
    print("발송할 항목 없음 → 종료")


if __name__ == "__main__":
    main()
