import os
import time
import json
from datetime import datetime, timedelta

import anthropic
import requests
from dotenv import load_dotenv

# --- [설정 영역] ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHANNEL_ID")

if not all([ANTHROPIC_API_KEY, NEWSAPI_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
        "NEWSAPI_KEY": NEWSAPI_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ────────────────────────────────────────────────
# 검색 쿼리 정의
# ────────────────────────────────────────────────
MACRO_QUERIES = {
    "central_bank": {
        "label": "🏦 중앙은행 & 통화정책",
        "queries_en": [
            "Federal Reserve interest rate monetary policy",
            "ECB European Central Bank interest rate",
            "Bank of Korea BOK monetary policy",
            "PBOC China central bank liquidity",
            "central bank rate hike cut 2026",
        ],
        "queries_ko": ["연방준비제도 금리", "한국은행 기준금리", "ECB 금리", "중국 인민은행"],
    },
    "geopolitics": {
        "label": "🌍 지정학 & 정치",
        "queries_en": [
            "US China trade war tariff 2026",
            "geopolitical risk Middle East",
            "Europe political election",
            "Japan economy policy",
            "Korea US relations",
            "Russia Ukraine war economy",
        ],
        "queries_ko": ["미중 무역전쟁", "중동 지정학", "한미 관계", "유럽 정치"],
    },
    "investment_events": {
        "label": "📅 주요 투자 이벤트",
        "queries_en": [
            "earnings report results 2026 major company",
            "bio pharma conference ASCO FDA approval 2026",
            "IPO major listing 2026",
            "economic data CPI GDP unemployment 2026",
            "FOMC meeting schedule 2026",
        ],
        "queries_ko": ["실적발표 2026", "바이오 학회 임상", "FOMC 일정", "CPI GDP 발표"],
    },
    "war_safehaven": {
        "label": "⚔️ 전쟁 & 안전자산",
        "queries_en": [
            "war conflict military escalation 2026",
            "safe haven gold price rally",
            "US Treasury bond yield safe haven",
            "geopolitical risk gold oil",
        ],
        "queries_ko": ["전쟁 위기", "안전자산 금", "금 가격", "국채 금리 전쟁"],
    },
}


# ────────────────────────────────────────────────
# 뉴스 수집
# ────────────────────────────────────────────────
def fetch_newsapi(query: str, hours: int = 24) -> list:
    try:
        from_dt = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = "https://newsapi.org/v2/everything"
        params = {
            "q":        query,
            "from":     from_dt,
            "sortBy":   "relevancy",
            "language": "en",
            "pageSize": 8,
            "apiKey":   NEWSAPI_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        articles = r.json().get("articles", [])
        return [
            {
                "title":   a.get("title", ""),
                "summary": (a.get("description") or "")[:200],
                "url":     a.get("url", ""),
                "source":  a.get("source", {}).get("name", ""),
                "date":    (a.get("publishedAt") or "")[:10],
            }
            for a in articles
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception as e:
        print(f"    NewsAPI 오류 ({query[:20]}): {e}")
        return []


def fetch_google_news_rss(query: str, max_age_hours: int = 24) -> list:
    try:
        import feedparser
        from time import mktime
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        encoded = requests.utils.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        results = []
        for e in feed.entries[:8]:
            pub = getattr(e, "published_parsed", None)
            if pub:
                pub_dt = datetime.utcfromtimestamp(mktime(pub))
                if pub_dt < cutoff:
                    continue
            results.append({
                "title":   e.get("title", ""),
                "summary": e.get("summary", "")[:200],
                "url":     e.get("link", ""),
                "source":  "Google News",
                "date":    e.get("published", "")[:10],
            })
        return results
    except Exception as e:
        print(f"    Google RSS 오류: {e}")
        return []


def fetch_naver_rss(query: str, max_age_hours: int = 24) -> list:
    try:
        import feedparser
        from time import mktime
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        encoded = requests.utils.quote(query)
        url = f"https://search.naver.com/rss?where=news&query={encoded}&field=1"
        feed = feedparser.parse(url)
        results = []
        for e in feed.entries[:8]:
            pub = getattr(e, "published_parsed", None)
            if pub:
                pub_dt = datetime.utcfromtimestamp(mktime(pub))
                if pub_dt < cutoff:
                    continue
            results.append({
                "title":   e.get("title", "").replace("<b>", "").replace("</b>", ""),
                "summary": e.get("summary", "").replace("<b>", "").replace("</b>", "")[:200],
                "url":     e.get("link", ""),
                "source":  "네이버뉴스",
                "date":    e.get("published", "")[:10],
            })
        return results
    except Exception as e:
        print(f"    네이버 RSS 오류: {e}")
        return []


def collect_category_news(category_key: str) -> list:
    """카테고리별 뉴스 전체 수집 + 중복 제거"""
    cat = MACRO_QUERIES[category_key]
    all_news = []

    for q in cat["queries_en"]:
        all_news += fetch_newsapi(q)
        time.sleep(0.3)

    for q in cat["queries_ko"]:
        all_news += fetch_google_news_rss(q)
        all_news += fetch_naver_rss(q)
        time.sleep(0.3)

    # 중복 제거
    seen = set()
    unique = []
    for n in all_news:
        key = n["title"][:40]
        if key and key not in seen:
            seen.add(key)
            unique.append(n)

    print(f"    ✓ {len(unique)}개 뉴스 수집")
    return unique[:30]  # 최대 30개만 Claude에 전달


# ────────────────────────────────────────────────
# Claude 분석
# ────────────────────────────────────────────────
def analyze_macro_with_claude(category_key: str, news_list: list) -> dict:
    cat = MACRO_QUERIES[category_key]
    label = cat["label"]

    if not news_list:
        return {"top5": [], "summary": "수집된 뉴스 없음.", "investment_implication": ""}

    news_text = "\n\n".join([
        f"[{i+1}] {n['date']} | {n['source']}\n제목: {n['title']}\n요약: {n['summary']}"
        for i, n in enumerate(news_list[:25])
    ])

    prompt = f"""다음은 [{label}] 관련 최근 뉴스임.

{news_text}

투자 관점에서 핵심 뉴스 Top 5 선별 후 아래 JSON으로만 답변. JSON만 출력.

규칙:
- 모든 문장은 음슴체(~음, ~임)로 간결하게
- N/A, 없음 등 대체 표현 사용 금지. 없으면 빈 문자열 ""
- investment_point: 1문장, 50자 이내
- summary: 150자 이내
- investment_implication: 150자 이내

{{
  "top5": [
    {{
      "rank": 1,
      "title": "뉴스 제목 (한국어)",
      "source": "출처",
      "date": "날짜",
      "url": "URL",
      "investment_point": "핵심 포인트 50자 이내, 음슴체",
      "sentiment": "긍정/중립/부정",
      "impact": "높음/중간/낮음"
    }}
  ],
  "summary": "종합 요약 150자 이내, 음슴체",
  "investment_implication": "한국 시장 영향 150자 이내, 음슴체"
}}"""

    try:
        message = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"    Claude 오류: {e}")
        return {"top5": [], "summary": "분석 실패함.", "investment_implication": ""}


# ────────────────────────────────────────────────
# 텔레그램 발송
# ────────────────────────────────────────────────
SENTIMENT_EMOJI = {"긍정": "🟢", "중립": "🟡", "부정": "🔴"}
IMPACT_EMOJI    = {"높음": "🔥", "중간": "📌", "낮음": "💤"}


def send_telegram(message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"    ✈️  텔레그램 발송 완료")
            return True
        else:
            # 메시지 너무 길면 분할 발송
            if r.status_code == 400 and "message is too long" in r.text:
                chunks = [message[i:i+3500] for i in range(0, len(message), 3500)]
                for chunk in chunks:
                    payload["text"] = chunk
                    requests.post(url, json=payload, timeout=10)
                    time.sleep(0.5)
                return True
            print(f"    ❌ 텔레그램 오류: {r.text[:100]}")
            return False
    except Exception as e:
        print(f"    ❌ 텔레그램 오류: {e}")
        return False


def _clean(v):
    """N/A, None, 빈값 제거"""
    if v is None:
        return ""
    s = str(v).strip()
    if not s or s.upper() in ("N/A", "NONE", "NULL"):
        return ""
    return s


def format_macro_message(category_key: str, analysis: dict) -> str:
    """매크로 뉴스 메시지 포맷팅 (간결, 음슴체)"""
    cat   = MACRO_QUERIES[category_key]
    label = cat["label"]
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{label} {now}",
        "─────────────────────",
    ]

    for item in analysis.get("top5", []):
        sentiment = SENTIMENT_EMOJI.get(item.get("sentiment", "중립"), "🟡")
        impact    = IMPACT_EMOJI.get(item.get("impact", "중간"), "📌")
        pt = _clean(item.get("investment_point"))[:80]
        date_src = f"{_clean(item.get('date'))} | {_clean(item.get('source'))}".strip(" |")
        title = _clean(item.get("title"))
        url = _clean(item.get("url"))

        lines.append(f"\n*#{item['rank']} {sentiment}{impact}* {date_src}")
        if title:
            lines.append(f"*{title}*")
        if pt:
            lines.append(f"💡 {pt}")
        if url:
            lines.append(url)

    summary = _clean(analysis.get("summary"))[:150]
    impl = _clean(analysis.get("investment_implication"))[:150]
    lines += [
        "\n─────────────────────",
        f"📊 *요약* {summary}" if summary else "📊 *요약* (없음)",
        f"🇰🇷 *한국시장* {impl}" if impl else "🇰🇷 *한국시장* (없음)",
    ]

    return "\n".join(l for l in lines if l)


# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
def run_macro_news():
    for category_key in MACRO_QUERIES.keys():
        label = MACRO_QUERIES[category_key]["label"]
        print(f"\n{'='*60}")
        print(f"📡 {label} 수집 중...")
        print("=" * 60)

        news     = collect_category_news(category_key)
        print(f"  🤖 Claude 분석 중...")
        analysis = analyze_macro_with_claude(category_key, news)
        msg      = format_macro_message(category_key, analysis)

        print(msg)
        send_telegram(msg)
        time.sleep(3)


if __name__ == "__main__":
    try:
        import feedparser
    except ImportError:
        os.system("pip3 install feedparser")

    run_macro_news()
