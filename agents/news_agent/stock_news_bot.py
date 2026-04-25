import os
import time
from datetime import datetime, timedelta

import ollama
import requests
from dotenv import load_dotenv

# --- [설정 영역] ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

NEWSAPI_KEY       = os.environ.get("NEWSAPI_KEY")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHANNEL_ID")

if not all([NEWSAPI_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
    missing = [k for k, v in {
        "NEWSAPI_KEY": NEWSAPI_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"❌ 누락된 환경변수: {', '.join(missing)}")
    exit(1)

# 분석할 종목 (종목명: 영문 검색어)
TARGET_STOCKS = {
    "기가비스":    "GigaBase",
}

# ────────────────────────────────────────────────
# 1) 뉴스 수집 (NewsAPI + 네이버 RSS + Google News RSS)
# ────────────────────────────────────────────────
def fetch_newsapi(query: str, days: int = 7) -> list:
    """NewsAPI로 해외 뉴스 수집"""
    try:
        from_date = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        url = "https://newsapi.org/v2/everything"
        params = {
            "q":        query,
            "from":     from_date,
            "sortBy":   "relevancy",
            "language": "en",
            "pageSize": 10,
            "apiKey":   NEWSAPI_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        articles = r.json().get("articles", [])
        return [
            {
                "title":   a.get("title", ""),
                "summary": a.get("description", "") or "",
                "url":     a.get("url", ""),
                "source":  a.get("source", {}).get("name", ""),
                "date":    a.get("publishedAt", "")[:10],
                "lang":    "en",
            }
            for a in articles if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception as e:
        print(f"    NewsAPI 오류: {e}")
        return []


def fetch_naver_rss(query: str) -> list:
    """네이버 금융 뉴스 RSS 수집"""
    try:
        import feedparser
        encoded = requests.utils.quote(query)
        url = f"https://search.naver.com/rss?where=news&query={encoded}&field=1"
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:10]:
            results.append({
                "title":   entry.get("title", "").replace("<b>", "").replace("</b>", ""),
                "summary": entry.get("summary", "").replace("<b>", "").replace("</b>", ""),
                "url":     entry.get("link", ""),
                "source":  entry.get("source", {}).get("title", "네이버뉴스") if hasattr(entry.get("source", {}), "get") else "네이버뉴스",
                "date":    entry.get("published", "")[:10],
                "lang":    "ko",
            })
        return results
    except Exception as e:
        print(f"    네이버 RSS 오류: {e}")
        return []


def fetch_google_news_rss(query: str) -> list:
    """Google News RSS 수집"""
    try:
        import feedparser
        encoded = requests.utils.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        feed = feedparser.parse(url)
        results = []
        for entry in feed.entries[:10]:
            results.append({
                "title":   entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "url":     entry.get("link", ""),
                "source":  entry.get("source", {}).get("title", "Google News") if hasattr(entry.get("source", {}), "get") else "Google News",
                "date":    entry.get("published", "")[:10],
                "lang":    "ko",
            })
        return results
    except Exception as e:
        print(f"    Google News RSS 오류: {e}")
        return []


def fetch_all_news(corp_name_ko: str, corp_name_en: str) -> list:
    """세 소스에서 뉴스 수집 후 합치기"""
    print(f"    📡 뉴스 수집 중...")
    news = []
    news += fetch_newsapi(corp_name_en)
    news += fetch_naver_rss(corp_name_ko)
    news += fetch_google_news_rss(corp_name_ko)

    # 중복 제거 (제목 기준)
    seen = set()
    unique = []
    for n in news:
        title = n["title"][:50]
        if title not in seen:
            seen.add(title)
            unique.append(n)

    print(f"    ✓ {len(unique)}개 뉴스 수집 (해외+국내)")
    return unique


# ────────────────────────────────────────────────
# 2) Ollama: Top 3 투자 관련 뉴스 선별 & 요약
# ────────────────────────────────────────────────
def analyze_news_with_claude(corp_name: str, news_list: list) -> dict:
    """로컬 Ollama가 투자 관점에서 Top 3 뉴스 선별 & 요약"""
    if not news_list:
        return {"top3": [], "overall_summary": "수집된 뉴스 없음."}

    news_text = "\n\n".join([
        f"[{i+1}] {n['date']} | {n['source']}\n제목: {n['title']}\n요약: {n['summary'][:200]}\nURL: {n['url']}"
        for i, n in enumerate(news_list[:20])
    ])

    prompt = f"""다음은 {corp_name}에 관한 최근 뉴스 목록임.

{news_text}

투자 관점에서 핵심 뉴스 Top 3 선별 후 아래 JSON으로 답변. JSON만 출력.

규칙:
- 모든 문장은 음슴체(~음, ~임)로 간결하게
- investment_point: 1문장, 50자 이내
- overall_summary: 150자 이내, 한 문장으로 결론만

{{
  "top3": [
    {{
      "rank": 1,
      "title": "뉴스 제목",
      "source": "출처",
      "date": "날짜",
      "url": "URL",
      "investment_point": "핵심 포인트 1문장 (50자 이내, 음슴체)",
      "sentiment": "긍정/중립/부정",
      "impact": "높음/중간/낮음"
    }}
  ],
  "overall_summary": "종합 결론 150자 이내, 음슴체"
}}"""

    try:
        import json

        response = ollama.chat(
            model="gemma4:26b",
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response["message"]["content"] or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"    Ollama 분석 오류: {e}")
        return {"top3": [], "overall_summary": "분석 실패"}


# ────────────────────────────────────────────────
# 3) 텔레그램 발송
# ────────────────────────────────────────────────
SENTIMENT_EMOJI = {"긍정": "🟢", "중립": "🟡", "부정": "🔴"}
IMPACT_EMOJI    = {"높음": "🔥", "중간": "📌", "낮음": "💤"}

def format_telegram_message(corp_name: str, analysis: dict) -> str:
    """텔레그램 메시지 포맷팅 (간결, 음슴체)"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📰 *{corp_name}* {now}",
        "─────────────────────",
    ]

    for item in analysis.get("top3", []):
        sentiment = SENTIMENT_EMOJI.get(item.get("sentiment", "중립"), "🟡")
        impact    = IMPACT_EMOJI.get(item.get("impact", "중간"), "📌")
        pt = (item.get("investment_point", "") or "")[:80]
        lines += [
            f"\n*#{item['rank']} {sentiment}{impact}* {item.get('date', '')} | {item.get('source', '')}",
            f"*{item['title']}*",
            f"💡 {pt}",
            item.get("url", ""),
        ]

    summary = (analysis.get("overall_summary", "") or "")[:150]
    lines += [
        "\n─────────────────────",
        f"📊 *결론* {summary}",
    ]

    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    """텔레그램 메시지 발송"""
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
            print(f"    ❌ 텔레그램 오류: {r.text}")
            return False
    except Exception as e:
        print(f"    ❌ 텔레그램 오류: {e}")
        return False


# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
def run_stock_news(stocks: dict = None, send_telegram_msg: bool = True):
    """종목별 뉴스 수집 → Ollama 분석 → 텔레그램 발송"""
    stocks = stocks or TARGET_STOCKS
    total  = len(stocks)

    # 시작 메시지
    if send_telegram_msg:
        send_telegram(f"📰 *종목 뉴스 브리핑 시작*\n분석 종목: {', '.join(stocks.keys())}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        time.sleep(1)

    for i, (name_ko, name_en) in enumerate(stocks.items(), 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total}] 📰 {name_ko} 뉴스 분석 중...")
        print("=" * 60)

        news  = fetch_all_news(name_ko, name_en)
        if not news:
            print(f"  ⚠️  뉴스 없음")
            continue

        print(f"  🤖 Ollama Top 3 선별 중...")
        analysis = analyze_news_with_claude(name_ko, news)

        msg = format_telegram_message(name_ko, analysis)
        print(msg)

        if send_telegram_msg:
            send_telegram(msg)
            time.sleep(2)  # 텔레그램 rate limit 방지

    print("\n✅ 전체 뉴스 브리핑 완료")


if __name__ == "__main__":
    # feedparser 설치 확인
    try:
        import feedparser
    except ImportError:
        print("📦 feedparser 설치 중...")
        os.system("pip3 install feedparser")

    run_stock_news()
