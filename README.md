# AI 뉴스 데일리 크롤러

매일 아침 8시(KST) 해외 AI 뉴스를 자동 수집 → Claude로 한글 요약 → **구글 시트에 저장** + **슬랙 #ai_news로 발송**.
GitHub Actions 클라우드에서 실행되므로 **내 컴퓨터가 꺼져 있어도 매일 돕니다.**

```
크롤링(RSS/구글뉴스) → 한글요약(Claude) → 구글시트 저장 → 슬랙 발송
                     └─ 매일 08:00 KST, GitHub Actions cron ─┘
```

## 파일 구조
| 파일 | 역할 |
|---|---|
| `sources.yaml` | **뉴스 소스 목록** (여기만 고치면 소스 추가/삭제) |
| `config.yaml` | 동작 설정(수집 시간범위, 모델, 개수 등) |
| `crawler.py` | RSS/구글뉴스 수집 |
| `summarizer.py` | Claude API 한글 요약 + 중요도 |
| `sheets_store.py` | 구글 시트 저장 |
| `slack_notify.py` | 슬랙 발송 |
| `main.py` | 전체 오케스트레이터 |
| `.github/workflows/daily.yml` | 매일 8시 실행 스케줄 |

## 세팅 (4개 준비물)

### 1. Anthropic API 키
console.anthropic.com → API Keys → 발급 → `sk-ant-...`

### 2. 슬랙 Webhook (#ai_news 연결)
api.slack.com/apps → Create App → **Incoming Webhooks** 켜기 → Add New Webhook → `#ai_news` 선택 → URL 복사

### 3. 구글 시트 + 서비스 계정
1. 구글 시트 새로 만들기 → URL의 `/d/` 와 `/edit` 사이가 **SHEET_ID**
2. console.cloud.google.com → 프로젝트 생성 → **Google Sheets API** 사용 설정
3. 서비스 계정 생성 → 키(JSON) 다운로드
4. 그 JSON 안의 `client_email`(…@….iam.gserviceaccount.com)을 **시트에 편집자로 공유**

### 4. GitHub 저장소 + 시크릿
1. 이 폴더를 GitHub 저장소로 push (아래 명령)
2. 저장소 → Settings → Secrets and variables → Actions → 아래 4개 등록:

| Secret 이름 | 값 |
|---|---|
| `ANTHROPIC_API_KEY` | sk-ant-… |
| `SLACK_WEBHOOK_URL` | https://hooks.slack.com/services/… |
| `SHEET_ID` | 구글 시트 ID |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스계정 JSON **전체 내용** |

## 실행
- **자동**: 매일 08:00 KST 자동 실행
- **수동 테스트**: GitHub 저장소 → Actions → "Daily AI News Digest" → Run workflow
- **로컬 테스트**: `.env.example`를 `.env`로 복사 후 값 채우고 `python main.py`

## 소스 추가하는 법
`sources.yaml`에 항목 추가 후 push 하면 끝:
```yaml
  - name: 사이트이름
    category: 뉴스
    type: rss                # RSS 있으면 rss, 없으면 googlenews
    url: https://.../feed/    # rss일 때
    # query: 검색어           # googlenews일 때 (url 대신)
```
