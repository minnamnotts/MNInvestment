import os
import time
from datetime import datetime, timedelta

import anthropic
import gspread
import pandas as pd
import ta as ta_lib
import yfinance as yf
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from pykrx import stock

# --- [설정 영역] ---
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    print("❌ 오류: ANTHROPIC_API_KEY를 로드할 수 없습니다.")
    exit(1)

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 분석할 종목 (종목명: 종목코드 수동 입력)
TARGET_STOCKS = {
  "인벤티지랩": "239670",
}


def get_last_trading_day() -> str:
    """가장 최근 거래일 반환 (주말/공휴일 자동 처리)"""
    date = datetime.today()
    for _ in range(10):
        if date.weekday() < 5:  # 월~금
            d = date.strftime("%Y%m%d")
            try:
                df = stock.get_market_ohlcv(d, d, "005930")
                if df is not None and not df.empty:
                    return d
            except Exception:
                pass
        date -= timedelta(days=1)
    return (datetime.today() - timedelta(days=3)).strftime("%Y%m%d")


# 기간 설정 (get_last_trading_day 기준으로 run_analysis 시작 시 설정됨)
END_DATE: str = ""
START_DATE: str = ""


# ────────────────────────────────────────────────
# 1) pykrx: 주가 + 기술적 지표 + 수급
# ────────────────────────────────────────────────
def get_price_and_technicals(ticker: str, name: str) -> dict:
    """pykrx로 주가 히스토리 가져와서 기술적 지표 계산"""
    print(f"  📈 주가 데이터 수집 중... ({ticker})")
    try:
        df = stock.get_market_ohlcv(START_DATE, END_DATE, ticker)
        if df is None or df.empty:
            return {"error": "주가 데이터 없음"}

        df.columns = ["Open", "High", "Low", "Close", "Volume", "Change"]

        # 기술적 지표 계산 (ta 라이브러리)
        df["RSI"]    = ta_lib.momentum.RSIIndicator(df["Close"], window=14).rsi()

        bb_ind       = ta_lib.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        df["BB_upper"] = bb_ind.bollinger_hband()
        df["BB_lower"] = bb_ind.bollinger_lband()
        df["BB_mid"]   = bb_ind.bollinger_mavg()

        macd_ind     = ta_lib.trend.MACD(df["Close"])
        df["MACD"]        = macd_ind.macd()
        df["MACD_signal"] = macd_ind.macd_signal()

        df["MA20"]   = ta_lib.trend.SMAIndicator(df["Close"], window=20).sma_indicator()
        df["MA60"]   = ta_lib.trend.SMAIndicator(df["Close"], window=60).sma_indicator()
        df["MA120"]  = ta_lib.trend.SMAIndicator(df["Close"], window=120).sma_indicator()

        latest       = df.iloc[-1]
        current_price = int(latest["Close"])

        # 52주 고/저
        high_52w = int(df["High"].max())
        low_52w  = int(df["Low"].min())
        pct_from_high = round((current_price - high_52w) / high_52w * 100, 1)

        # 볼린저밴드 위치
        bb_upper = latest.get("BB_upper", None)
        bb_lower = latest.get("BB_lower", None)
        bb_mid   = latest.get("BB_mid", None)
        if bb_upper and bb_lower:
            bb_position = round((current_price - bb_lower) / (bb_upper - bb_lower) * 100, 1)
        else:
            bb_position = None

        # 골든/데드크로스
        ma20  = latest.get("MA20")
        ma60  = latest.get("MA60")
        ma120 = latest.get("MA120")
        cross_signal = "골든크로스(MA20>MA60)" if (ma20 and ma60 and ma20 > ma60) else "데드크로스(MA20<MA60)"

        # 거래량 (최근 5일 평균 대비)
        avg_vol_5d    = int(df["Volume"].tail(5).mean())
        today_vol     = int(latest["Volume"])
        vol_ratio     = round(today_vol / avg_vol_5d, 2) if avg_vol_5d else None

    except Exception as e:
        return {"error": f"주가 데이터 오류: {e}"}

    # 외국인/기관 수급
    print(f"  👥 외국인/기관 수급 수집 중...")
    try:
        supply_df = stock.get_market_trading_volume_by_investor(
            (datetime.today() - timedelta(days=20)).strftime("%Y%m%d"),
            END_DATE, ticker
        )
        foreign_net   = int(supply_df.loc["외국인합계", "순매수"] if "외국인합계" in supply_df.index else 0)
        institute_net = int(supply_df.loc["기관합계", "순매수"]   if "기관합계"   in supply_df.index else 0)
    except Exception:
        foreign_net   = None
        institute_net = None

    # PER/PBR/배당수익률은 yfinance(financials)에서 수집

    return {
        "current_price":  current_price,
        "high_52w":       high_52w,
        "low_52w":        low_52w,
        "pct_from_high":  pct_from_high,
        "rsi":            round(float(latest["RSI"]), 1) if pd.notna(latest.get("RSI")) else None,
        "bb_position":    bb_position,
        "bb_upper":       round(float(bb_upper), 0) if bb_upper else None,
        "bb_lower":       round(float(bb_lower), 0) if bb_lower else None,
        "macd":           round(float(latest.get("MACD", 0)), 2),
        "macd_signal":    round(float(latest.get("MACD_signal", 0)), 2),
        "ma20":           round(float(ma20), 0) if ma20 else None,
        "ma60":           round(float(ma60), 0) if ma60 else None,
        "ma120":          round(float(ma120), 0) if ma120 else None,
        "cross_signal":   cross_signal,
        "volume_ratio":   vol_ratio,
        "foreign_net_20d":  foreign_net,
        "institute_net_20d": institute_net,
    }


# ────────────────────────────────────────────────
# 2) 동종업계(섹터) 벤치마크
# ────────────────────────────────────────────────
# yfinance Sector 매핑용 참조 벤치마크 (PER/PBR/ROE 중앙값 근사치)
SECTOR_BENCHMARKS = {
    "Healthcare":           {"per": 28, "pbr": 2.5, "roe": 12, "op_margin": 15, "revenue_growth": 10},
    "Technology":           {"per": 22, "pbr": 2.8, "roe": 15, "op_margin": 18, "revenue_growth": 12},
    "Financial Services":   {"per": 8,  "pbr": 0.6, "roe": 10, "op_margin": 25, "revenue_growth": 5},
    "Consumer Defensive":   {"per": 18, "pbr": 1.5, "roe": 12, "op_margin": 8,  "revenue_growth": 4},
    "Consumer Cyclical":    {"per": 15, "pbr": 1.2, "roe": 10, "op_margin": 6,  "revenue_growth": 6},
    "Industrials":          {"per": 16, "pbr": 1.4, "roe": 11, "op_margin": 8,  "revenue_growth": 5},
    "Basic Materials":      {"per": 12, "pbr": 1.0, "roe": 8,  "op_margin": 10, "revenue_growth": 3},
    "Energy":               {"per": 10, "pbr": 1.0, "roe": 8,  "op_margin": 12, "revenue_growth": 2},
    "Real Estate":          {"per": 14, "pbr": 0.9, "roe": 6,  "op_margin": 20, "revenue_growth": 4},
    "Communication":        {"per": 15, "pbr": 1.2, "roe": 10, "op_margin": 15, "revenue_growth": 5},
}

# 업종 키워드 → 섹터 매핑 (yfinance industry가 다양할 때)
INDUSTRY_TO_SECTOR = {
    "pharmaceutical": "Healthcare", "biotechnology": "Healthcare", "drug": "Healthcare",
    "제약": "Healthcare", "바이오": "Healthcare", "의료": "Healthcare",
}


def get_sector_benchmarks(ticker: str, fin: dict | None = None) -> tuple[dict, str]:
    """동종업계(섹터) 평균 벤치마크 반환. (benchmark_dict, sector_name)"""
    sector = (fin or {}).get("sector") or "N/A"
    industry = (fin or {}).get("industry") or ""

    if sector in SECTOR_BENCHMARKS:
        return SECTOR_BENCHMARKS[sector].copy(), str(sector)

    industry_lower = (industry or "").lower()
    for kw, sec in INDUSTRY_TO_SECTOR.items():
        if kw in industry_lower:
            return SECTOR_BENCHMARKS.get(sec, {}).copy(), sec

    return {}, sector


# ────────────────────────────────────────────────
# 3) yfinance: 재무제표
# ────────────────────────────────────────────────
def get_financials_yfinance(ticker: str) -> dict:
    """yfinance로 재무 건전성 지표 수집 (KRX 종목코드 + .KS)"""
    print(f"  📊 재무제표 수집 중... (yfinance)")
    try:
        yf_ticker = ticker + ".KS"
        t = yf.Ticker(yf_ticker)
        info = t.info

        div_yield = round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else None
        return {
            "market_cap":       info.get("marketCap"),
            "per":              round(info.get("forwardPE", 0), 2) if info.get("forwardPE") else None,
            "pbr":              round(info.get("priceToBook", 0), 2) if info.get("priceToBook") else None,
            "div_yield":        div_yield,
            "roe":              round(info.get("returnOnEquity", 0) * 100, 1) if info.get("returnOnEquity") else None,
            "roa":              round(info.get("returnOnAssets", 0) * 100, 1) if info.get("returnOnAssets") else None,
            "debt_to_equity":   round(info.get("debtToEquity", 0), 1) if info.get("debtToEquity") else None,
            "operating_margin": round(info.get("operatingMargins", 0) * 100, 1) if info.get("operatingMargins") else None,
            "revenue_growth":   round(info.get("revenueGrowth", 0) * 100, 1) if info.get("revenueGrowth") else None,
            "current_ratio":    round(info.get("currentRatio", 0), 2) if info.get("currentRatio") else None,
            "ps_ratio":         round(info.get("priceToSalesTrailing12Months", 0), 2) if info.get("priceToSalesTrailing12Months") else None,
            "ev_ebitda":        round(info.get("enterpriseToEbitda", 0), 2) if info.get("enterpriseToEbitda") else None,
            "sector":           info.get("sector", "N/A"),
            "industry":         info.get("industry", "N/A"),
        }
    except Exception as e:
        return {"error": f"yfinance 오류: {e}"}


# ────────────────────────────────────────────────
# 4) Claude: 통합 분석
# ────────────────────────────────────────────────
def analyze_with_claude(name: str, tech: dict, fin: dict, bench: dict, sector: str, max_retries=3) -> str:
    """수집한 데이터를 Claude에게 넘겨 투자 오버뷰 생성"""

    bench_text = "없음"
    if bench:
        bench_text = f"""동종업계({sector}) 벤치마크:
- PER: {bench.get('per', 'N/A')} / PBR: {bench.get('pbr', 'N/A')} / ROE: {bench.get('roe', 'N/A')}%
- 영업이익률: {bench.get('op_margin', 'N/A')}% / 매출성장률: {bench.get('revenue_growth', 'N/A')}%
(본 종목 PER {fin.get('per')} vs 업계 {bench.get('per')} | ROE {fin.get('roe')}% vs 업계 {bench.get('roe')}%)"""

    prompt = f"""다음은 {name}의 종합 투자 데이터입니다. 투자자 관점에서 분석해주세요.

## 📈 기술적 분석
- 현재가: {tech.get('current_price') or 'N/A'}원
- 52주 고점: {tech.get('high_52w') or 'N/A'}원 / 저점: {tech.get('low_52w') or 'N/A'}원 (고점 대비 {tech.get('pct_from_high') or 'N/A'}%)
- RSI(14): {tech.get('rsi') or 'N/A'} (30↓ 과매도 / 70↑ 과매수)
- 볼린저밴드 위치: {tech.get('bb_position') or 'N/A'}% (0%=하단, 100%=상단)
- MACD: {tech.get('macd') or 'N/A'} / Signal: {tech.get('macd_signal') or 'N/A'}
- 이동평균: MA20={tech.get('ma20') or 'N/A'} / MA60={tech.get('ma60') or 'N/A'} / MA120={tech.get('ma120') or 'N/A'}
- 크로스 신호: {tech.get('cross_signal') or 'N/A'}
- 거래량 (5일평균 대비): {tech.get('volume_ratio') or 'N/A'}배

## 💼 수급 분석 (최근 20일)
- 외국인 순매수: {tech.get('foreign_net_20d') or 'N/A'}주
- 기관 순매수: {tech.get('institute_net_20d') or 'N/A'}주

## 💰 밸류에이션
- PER: {fin.get('per') or 'N/A'} / PBR: {fin.get('pbr') or 'N/A'} / PSR: {fin.get('ps_ratio') or 'N/A'}
- EV/EBITDA: {fin.get('ev_ebitda') or 'N/A'} / 배당수익률: {fin.get('div_yield') or 'N/A'}%

## 📊 재무 건전성
- 시가총액: {fin.get('market_cap') or 'N/A'}원 (추정)
- ROE: {fin.get('roe')}% / ROA: {fin.get('roa')}%
- 부채비율: {fin.get('debt_to_equity')} / 유동비율: {fin.get('current_ratio')}
- 영업이익률: {fin.get('operating_margin')}% / 매출성장률(YoY): {fin.get('revenue_growth')}%

## 🎯 동종업계(섹터) 벤치마크
{bench_text}

---
아래 형식으로 분석해주세요:

### 1. 기술적 분석 요약
(RSI, 볼린저밴드, MACD, 이동평균 상태를 종합해서 현재 기술적 포지션 판단)

### 2. 밸류에이션 평가 (동종업계 대비)
(현재 PER/PBR/ROE가 동종업계 벤치마크 대비 저평가/적정/고평가인지 구체적으로 비교)

### 3. 재무 건전성 (동종업계 대비)
(부채비율, 영업이익률, 매출성장률 등을 동종업계 평균과 비교하여 평가)

### 4. 수급 신호
(외국인/기관 수급 방향성 해석)

### 5. 종합 투자 의견
(단기/중기 관점에서 매수/관망/주의 의견과 근거, 주목할 가격대)
"""

    for attempt in range(max_retries):
        try:
            message = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                print(f"   ⏳ API 할당량 초과. 35초 후 재시도...")
                time.sleep(35)
            else:
                raise
        except anthropic.APIError as e:
            raise


# ────────────────────────────────────────────────
# 4) Google Sheets 업로드
# ────────────────────────────────────────────────
def upload_to_google_sheet(results: list, sheet_name: str, key_file="google_key.json"):
    try:
        key_path = os.path.join(_script_dir, key_file)
        scope    = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds    = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
        client   = gspread.authorize(creds)
        sh = client.open(sheet_name)
        date_str = datetime.now().strftime("%Y/%m/%d")
        analysis_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        for r in results:
            if "error" in r.get("technicals", {}) or r.get("analysis") == "종목 조회 실패":
                continue

            tab_title = f"{r['name']}_{date_str} Analysis"
            if len(tab_title) > 100:  # 구글 시트 탭 이름 100자 제한
                tab_title = tab_title[:97] + "..."

            try:
                ws = sh.worksheet(tab_title)
            except Exception:
                ws = sh.add_worksheet(title=tab_title, rows=500, cols=5)

            ws.clear()

            t = r.get("technicals", {})
            f = r.get("financials", {})
            macd_dir = "▲" if (t.get("macd", 0) or 0) > (t.get("macd_signal", 0) or 0) else "▼"

            bench = r.get("benchmarks", {})
            sector = r.get("sector", "")

            # 1) 요약 테이블 (동종업계 벤치마크 비교 포함)
            headers = ["종목명", "섹터", "현재가", "RSI", "BB위치%", "PER", "업계PER", "PBR", "ROE%", "업계ROE%",
                       "영업이익률%", "업계영업이익률%", "매출성장%", "외국인순매수", "기관순매수", "분석일시"]
            row_data = [
                r["name"],
                sector,
                t.get("current_price", ""),
                t.get("rsi", ""),
                t.get("bb_position", ""),
                f.get("per", ""),
                bench.get("per", ""),
                f.get("pbr", ""),
                f.get("roe", ""),
                bench.get("roe", ""),
                f.get("operating_margin", ""),
                bench.get("op_margin", ""),
                f.get("revenue_growth", ""),
                t.get("foreign_net_20d", ""),
                t.get("institute_net_20d", ""),
                analysis_time,
            ]
            ws.update([headers, row_data])

            # 2) Claude 분석 전문 (요약 아래 공백 두고)
            ws.update_acell("A4", "Claude 투자 오버뷰")
            ws.update_acell("A5", r.get("analysis", ""))

            print(f"  ✓ 탭 생성: {tab_title}")

        print(f"🌐 구글 시트 업데이트 완료: {sheet_name}")
    except Exception as e:
        print(f"❌ 구글 시트 업로드 실패: {e}")


# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
def run_analysis(upload=True, sheet_name="StockAnalysis"):
    global END_DATE, START_DATE
    last_day = get_last_trading_day()
    END_DATE = last_day
    START_DATE = (datetime.strptime(last_day, "%Y%m%d") - timedelta(days=365)).strftime("%Y%m%d")
    print(f"📅 기준일: {last_day} (최근 거래일)")

    results = []
    total   = len(TARGET_STOCKS)

    for i, (name, ticker) in enumerate(TARGET_STOCKS.items(), 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{total}] 🔍 {name} ({ticker}) 분석 중...")
        print("=" * 60)
        tech = get_price_and_technicals(ticker, name)
        time.sleep(1)  # pykrx 부하 방지

        fin  = get_financials_yfinance(ticker)
        time.sleep(1)

        if "error" in tech:
            print(f"⚠️  {name}: {tech['error']}")
            results.append({"name": name, "ticker": ticker, "technicals": {}, "financials": fin, "benchmarks": {}, "sector": "", "analysis": tech["error"]})
            continue

        bench, sector = get_sector_benchmarks(ticker, fin)
        if bench:
            print(f"  📊 동종업계 벤치마크: {sector} (PER {bench.get('per')}, ROE {bench.get('roe')}%)")

        print(f"  🤖 Claude 분석 중...")
        analysis = analyze_with_claude(name, tech, fin, bench, sector)
        print(f"\n{'─'*40}")
        print(f"[{name} 투자 오버뷰]")
        print(analysis)

        results.append({
            "name":       name,
            "ticker":     ticker,
            "technicals": tech,
            "financials": fin,
            "benchmarks": bench,
            "sector":     sector,
            "analysis":   analysis,
        })

        if i < total:
            time.sleep(2)

    if upload and results:
        upload_to_google_sheet(results, sheet_name)

    return results


if __name__ == "__main__":
    print("🚀 스크립트 시작")
    SHEET_NAME = "Single Stock Analysis"
    print(f"📋 분석 종목: {TARGET_STOCKS}")
    run_analysis(upload=True, sheet_name=SHEET_NAME)
    print("✅ 완료")