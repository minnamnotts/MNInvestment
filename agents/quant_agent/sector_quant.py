import requests
from bs4 import BeautifulSoup
import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import json
from datetime import datetime
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# 환경변수 로드
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.expanduser("~/.env"))

SECTOR_SHEET_ID = os.environ.get("SECTOR_SHEET_ID")
GOOGLE_KEY_PATH = os.path.join(_script_dir, "google_key.json")

# ────────────────────────────────────────────────
# 섹터 설정 (업종코드: 네이버 금융 upjong no)
# 나중에 섹터 추가할 때 여기에 줄만 추가하면 됨
# ────────────────────────────────────────────────
SECTORS = {
    "반도체/IT": {
        "반도체·장비":     278,
        "전자장비·부품":   282,
        "IT서비스":        267,
    },
    "바이오/제약": {
        "제약":            261,
        "생물공학":        286,
        "생명과학도구":    262,
    },
    "자동차/2차전지": {
        "자동차":          273,
        "자동차부품":      270,
        "전기장비":        306,
    },
    "방산/항공우주": {
        "우주항공·국방":   284,
    },
}

TOP_N = 30  # 세부섹터별 시총 상위 N개
TOP_SCORE = 10  # 스코어링 TOP N개

# ────────────────────────────────────────────────
# 벤치마크 PER / PBR (온도계 기준값)
# 데이터 쌓이면 나중에 자동 역사적 평균으로 교체 가능
# ────────────────────────────────────────────────
BENCHMARK = {
    "반도체·장비":   {"PER": 20, "PBR": 2.0},
    "전자장비·부품": {"PER": 15, "PBR": 1.5},
    "IT서비스":      {"PER": 18, "PBR": 2.0},
    "제약":          {"PER": 25, "PBR": 3.0},
    "생물공학":      {"PER": 40, "PBR": 5.0},
    "생명과학도구":  {"PER": 30, "PBR": 4.0},
    "자동차":        {"PER": 10, "PBR": 0.8},
    "자동차부품":    {"PER": 12, "PBR": 1.0},
    "전기장비":      {"PER": 15, "PBR": 1.5},
    "우주항공·국방": {"PER": 20, "PBR": 2.5},
}

# 반도체만 돌릴 때: ["반도체/IT"] / 전체: None 또는 []
RUN_ONLY_SECTORS = ["반도체/IT"]
if RUN_ONLY_SECTORS:
    SECTORS = {k: v for k, v in SECTORS.items() if k in RUN_ONLY_SECTORS}


# ────────────────────────────────────────────────
# 1) 네이버 금융 - 업종별 종목 코드 수집
# ────────────────────────────────────────────────
def get_upjong_codes(upjong_no: int) -> set:
    """업종 페이지에서 종목 코드 세트 반환"""
    url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={upjong_no}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        codes = set()
        for a in soup.select("table.type_5 tr td:first-child a"):
            href = a.get("href", "")
            if "code=" in href:
                codes.add(href.split("code=")[-1].strip())
        return codes
    except Exception as e:
        print(f"    ⚠️  업종 {upjong_no} 수집 실패: {e}")
        return set()


# ────────────────────────────────────────────────
# 2) 네이버 금융 - 시총 순위 전체 수집 (KOSPI+KOSDAQ)
# ────────────────────────────────────────────────
def get_marcap_ranking() -> dict:
    """시총 순위 딕셔너리 {종목코드: 시가총액(억)} 반환"""
    marcap = {}
    headers = {"User-Agent": "Mozilla/5.0"}

    for sosok in ["0", "1"]:  # 0=KOSPI, 1=KOSDAQ
        page = 1
        while True:
            url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
            try:
                r = requests.get(url, headers=headers, timeout=10)
                soup = BeautifulSoup(r.text, "html.parser")
                rows = soup.select("table.type_2 tr")
                found = 0
                for row in rows:
                    cols = row.select("td")
                    if len(cols) < 7:
                        continue
                    a = cols[1].select_one("a")
                    if not a:
                        continue
                    href = a.get("href", "")
                    code = href.split("code=")[-1].strip() if "code=" in href else ""
                    cap_text = cols[6].text.strip().replace(",", "")
                    if code and cap_text.isdigit():
                        marcap[code] = int(cap_text)
                        found += 1
                if found == 0:
                    break
                page += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    ⚠️  시총 페이지 {page} 오류: {e}")
                break

    print(f"  ✓ 시총 데이터 {len(marcap)}개 수집")
    return marcap


# ────────────────────────────────────────────────
# 3) 네이버 금융 - 개별 종목 PER/PBR/EPS/배당
# ────────────────────────────────────────────────
def get_naver_finance(code: str) -> dict:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        def parse_float(selector):
            el = soup.select_one(selector)
            if not el:
                return None
            try:
                return float(el.text.strip().replace(",", ""))
            except:
                return None

        return {
            "PER":    parse_float("#_per"),
            "PBR":    parse_float("#_pbr"),
            "EPS":    parse_float("#_eps"),
            "배당수익률": parse_float("#_dvr"),
        }
    except Exception as e:
        return {"PER": None, "PBR": None, "EPS": None, "배당수익률": None}


# ────────────────────────────────────────────────
# 4) yfinance - ROE/부채비율/영업이익률/매출성장률
# ────────────────────────────────────────────────
def get_yfinance_data(code: str) -> dict:
    try:
        ticker = yf.Ticker(f"{code}.KS")
        info = ticker.info
        if not info or info.get("quoteType") == "NONE":
            ticker = yf.Ticker(f"{code}.KQ")
            info = ticker.info

        def safe(key, default=None):
            v = info.get(key)
            return round(v * 100, 2) if v and isinstance(v, float) and "ratio" not in key.lower() and key in ["returnOnEquity", "returnOnAssets", "operatingMargins", "revenueGrowth"] else (round(v, 2) if v else default)

        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        return {
            "현재가":     round(current_price, 0) if current_price is not None else None,
            "ROE":        round(info.get("returnOnEquity", 0) * 100, 2) if info.get("returnOnEquity") else None,
            "ROA":        round(info.get("returnOnAssets", 0) * 100, 2) if info.get("returnOnAssets") else None,
            "영업이익률":  round(info.get("operatingMargins", 0) * 100, 2) if info.get("operatingMargins") else None,
            "매출성장률":  round(info.get("revenueGrowth", 0) * 100, 2) if info.get("revenueGrowth") else None,
            "부채비율":    round(info.get("debtToEquity", 0), 2) if info.get("debtToEquity") else None,
            "유동비율":    round(info.get("currentRatio", 0), 2) if info.get("currentRatio") else None,
            "시가총액_USD": info.get("marketCap"),
            "종목명_en":   info.get("shortName", ""),
        }
    except Exception as e:
        return {k: None for k in ["현재가", "ROE", "ROA", "영업이익률", "매출성장률", "부채비율", "유동비율", "시가총액_USD", "종목명_en"]}


# ────────────────────────────────────────────────
# 5) 종목명 가져오기 (네이버)
# ────────────────────────────────────────────────
def get_stock_name(code: str) -> str:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        name = soup.select_one("div.wrap_company h2 a")
        return name.text.strip() if name else code
    except:
        return code


# ────────────────────────────────────────────────
# 6) Z-score 계산 (섹터 내 상대적 위치)
# ────────────────────────────────────────────────
def calc_zscore(series: pd.Series) -> pd.Series:
    """Z-score: 0이 평균, +1이면 평균보다 1표준편차 위"""
    s = series.dropna()
    if len(s) < 2 or s.std() == 0:
        return pd.Series([None] * len(series), index=series.index)
    return (series - s.mean()) / s.std()


# ────────────────────────────────────────────────
# 7) 섹터별 데이터 수집
# ────────────────────────────────────────────────
def collect_sector_data(marcap_dict: dict) -> dict:
    """전체 섹터 데이터 수집 → {섹터명: {세부섹터명: DataFrame}}"""
    all_data = {}

    for sector_name, subsectors in SECTORS.items():
        print(f"\n{'='*60}")
        print(f"📊 {sector_name}")
        print("=" * 60)
        all_data[sector_name] = {}

        for sub_name, upjong_no in subsectors.items():
            print(f"\n  📂 {sub_name} (업종 {upjong_no})")

            # 업종 종목 코드 수집
            codes = get_upjong_codes(upjong_no)
            print(f"    ✓ 업종 종목 {len(codes)}개")

            # 시총 순위로 정렬 후 상위 TOP_N개
            ranked = sorted(
                [(c, marcap_dict.get(c, 0)) for c in codes],
                key=lambda x: x[1], reverse=True
            )
            top_codes = [c for c, _ in ranked if _ > 0][:TOP_N]
            print(f"    ✓ 시총 상위 {len(top_codes)}개 선정")

            rows = []
            for i, code in enumerate(top_codes):
                print(f"    [{i+1}/{len(top_codes)}] {code} 수집 중...")

                name = get_stock_name(code)
                naver = get_naver_finance(code)
                yf_data = get_yfinance_data(code)
                marcap = marcap_dict.get(code, 0)

                row = {
                    "순위":     i + 1,
                    "종목코드": code,
                    "종목명":   name,
                    "시가총액(억)": marcap,
                    **naver,
                    **{k: v for k, v in yf_data.items() if k not in ["시가총액_USD", "종목명_en"]},
                }
                rows.append(row)
                time.sleep(0.5)

            df = pd.DataFrame(rows)

            # Z-score 추가
            for col in ["PER", "PBR", "ROE", "영업이익률", "부채비율"]:
                if col in df.columns:
                    df[f"{col}_Z"] = calc_zscore(df[col]).round(2)

            # 키맞추기 종합점수: -PER_Z - PBR_Z + ROE_Z + 영업이익률_Z - 부채비율_Z (높을수록 저평가+우량)
            for zcol in ["PER_Z", "PBR_Z", "ROE_Z", "영업이익률_Z", "부채비율_Z"]:
                if zcol not in df.columns:
                    df[zcol] = 0
            df["종합점수"] = (
                -df["PER_Z"].fillna(0) - df["PBR_Z"].fillna(0)
                + df["ROE_Z"].fillna(0) + df["영업이익률_Z"].fillna(0)
                - df["부채비율_Z"].fillna(0)
            ).round(2)

            all_data[sector_name][sub_name] = df
            print(f"    ✅ {sub_name} 완료 ({len(df)}개 종목)")

    return all_data


# ────────────────────────────────────────────────
# 8) 엑셀 출력
# ────────────────────────────────────────────────
# ────────────────────────────────────────────────
# 구글시트 연동
# ────────────────────────────────────────────────
def get_gsheet_client():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_KEY_PATH, scopes=scopes)
    return gspread.authorize(creds)


def save_to_gsheet(all_data: dict):
    """구글시트에 날짜 탭으로 데이터 저장"""
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n📤 구글시트 업로드 중...")

    gc = get_gsheet_client()
    sh = gc.open_by_key(SECTOR_SHEET_ID)

    # ── 요약 탭 ──
    summary_tab = f"요약_{today}"
    try:
        ws = sh.worksheet(summary_tab)
        sh.del_worksheet(ws)
    except:
        pass
    ws_summary = sh.add_worksheet(title=summary_tab, rows=200, cols=20)

    summary_headers = ["대섹터", "세부섹터", "종목수", "평균PER(배)", "평균PBR(배)",
                       "평균ROE(%)", "평균영업이익률(%)", "평균부채비율(%)", "평균배당수익률(%)"]
    summary_rows = [["※ 모든 지표 원화(KRW) 기준"], summary_headers]

    for sector_name, subsectors in all_data.items():
        for sub_name, df in subsectors.items():
            if df.empty:
                continue
            def avg(col):
                if col in df.columns:
                    vals = pd.to_numeric(df[col], errors="coerce").dropna()
                    return round(float(vals.mean()), 2) if len(vals) > 0 else ""
                return ""
            summary_rows.append([
                sector_name, sub_name, len(df),
                avg("PER"), avg("PBR"), avg("ROE"),
                avg("영업이익률"), avg("부채비율"), avg("배당수익률")
            ])

    ws_summary.update(summary_rows, "A1")
    print(f"  ✅ 요약 탭 업로드 완료")
    time.sleep(1)

    # ── 섹터별 탭 (모든 가격·지표 원화 기준) ──
    COL_KEYS = ["순위", "종목코드", "종목명", "시가총액(억)", "현재가", "PER", "PBR",
                "EPS", "배당수익률", "ROE", "ROA", "영업이익률",
                "매출성장률", "부채비율", "유동비율",
                "PER_Z", "PBR_Z", "ROE_Z", "영업이익률_Z", "부채비율_Z", "종합점수"]
    COLUMNS = ["순위", "종목코드", "종목명", "시가총액(억원)", "현재가(원)", "PER(배)", "PBR(배)",
               "EPS(원)", "배당수익률(%)", "ROE(%)", "ROA(%)", "영업이익률(%)",
               "매출성장률(%)", "부채비율(%)", "유동비율(배)",
               "PER_Z", "PBR_Z", "ROE_Z", "영업이익률_Z", "부채비율_Z", "종합점수(키맞추기)"]

    for sector_name, subsectors in all_data.items():
        tab_name = f"{sector_name}_{today}"[:100]
        try:
            ws = sh.worksheet(tab_name)
            sh.del_worksheet(ws)
        except:
            pass

        ws = sh.add_worksheet(title=tab_name, rows=500, cols=len(COLUMNS)+2)
        all_rows = [[f"📊 {sector_name} | 기준일: {today}"]]
        all_rows.append(["※ 모든 가격·지표 원화(KRW) 기준"])
        all_rows.append(["📌 지표 의미 (키맞추기)"])
        all_rows.append(["  PER_Z: 이익 대비 주가 → 낮을수록 저평가"])
        all_rows.append(["  PBR_Z: 자산 대비 주가 → 낮을수록 저평가"])
        all_rows.append(["  ROE_Z: 자본 수익성 → 높을수록 좋음"])
        all_rows.append(["  영업이익률_Z: 본업 수익성 → 높을수록 좋음"])
        all_rows.append(["  부채비율_Z: 재무 건전성 → 낮을수록 안전"])
        all_rows.append(["  종합점수(키맞추기): 높을수록 저평가+우량"])
        all_rows.append([])  # 빈 줄

        for sub_name, df in subsectors.items():
            if df.empty:
                continue
            all_rows.append([f"📂 {sub_name} (시총 상위 {len(df)}종목)"])
            all_rows.append(COLUMNS)

            for _, r in df.iterrows():
                row_data = []
                for col in COL_KEYS:
                    val = r.get(col, "")
                    if pd.isna(val) if isinstance(val, float) else val is None:
                        val = ""
                    row_data.append(val)
                all_rows.append(row_data)

            # 평균 행
            avg_row = ["평균", "", ""]
            for col in COL_KEYS[3:]:
                if col in df.columns:
                    vals = pd.to_numeric(df[col], errors="coerce").dropna()
                    avg_row.append(round(float(vals.mean()), 2) if len(vals) > 0 else "")
                else:
                    avg_row.append("")
            all_rows.append(avg_row)
            all_rows.append([])  # 빈 줄

        ws.update(all_rows, "A1")
        print(f"  ✅ {sector_name} 탭 업로드 완료")
        time.sleep(1)

    # ── 스코어링 탭 (전체 섹터 TOP10) ──
    scoring_tab = f"스코어링_{today}"
    try:
        ws = sh.worksheet(scoring_tab)
        sh.del_worksheet(ws)
    except:
        pass
    ws_scoring = sh.add_worksheet(title=scoring_tab, rows=500, cols=12)

    scoring_rows = [
        [f"🏆 섹터별 종합점수 TOP{TOP_SCORE} | 기준일: {today}"],
        ["※ 종합점수 = -PER_Z - PBR_Z + ROE_Z + 영업이익률_Z - 부채비율_Z (높을수록 저평가+우량)"],
        [],
        ["대섹터", "세부섹터", "순위", "종목코드", "종목명",
         "종합점수", "PER", "PBR", "ROE(%)", "영업이익률(%)", "부채비율(%)", "시가총액(억)"]
    ]
    for sector_name, subsectors in all_data.items():
        for sub_name, df in subsectors.items():
            if df.empty or "종합점수" not in df.columns:
                continue
            df_valid = df.dropna(subset=["종합점수"]).copy()
            top_n = df_valid.nlargest(TOP_SCORE, "종합점수")
            for rank, (_, r) in enumerate(top_n.iterrows(), 1):
                def v(col):
                    val = r.get(col, "")
                    return "" if (pd.isna(val) if isinstance(val, float) else val is None) else val
                scoring_rows.append([
                    sector_name, sub_name, rank,
                    v("종목코드"), v("종목명"),
                    v("종합점수"), v("PER"), v("PBR"),
                    v("ROE"), v("영업이익률"), v("부채비율"), v("시가총액(억)")
                ])
        scoring_rows.append([])
    ws_scoring.update(scoring_rows, "A1")
    print(f"  ✅ 스코어링 탭 업로드 완료")
    time.sleep(1)

    # ── 사분면 분석 탭 ──
    quad_tab = f"사분면_{today}"
    try:
        ws = sh.worksheet(quad_tab)
        sh.del_worksheet(ws)
    except:
        pass
    ws_quad = sh.add_worksheet(title=quad_tab, rows=500, cols=10)

    def get_quadrant(pbr_z, roe_z):
        if pbr_z is None or roe_z is None:
            return "데이터없음"
        if pbr_z < 0 and roe_z > 0:
            return "💎 보물 (저PBR+고ROE)"
        elif pbr_z >= 0 and roe_z > 0:
            return "🚀 성장 (고PBR+고ROE)"
        elif pbr_z < 0 and roe_z <= 0:
            return "⚠️ 주의 (저PBR+저ROE)"
        else:
            return "🚨 함정 (고PBR+저ROE)"

    quad_rows = [
        [f"🔲 사분면 분석 (PBR_Z × ROE_Z) | 기준일: {today}"],
        ["💎 보물=저PBR+고ROE  🚀 성장=고PBR+고ROE  ⚠️ 주의=저PBR+저ROE  🚨 함정=고PBR+저ROE"],
        [],
        ["대섹터", "세부섹터", "종목코드", "종목명", "PBR_Z", "ROE_Z", "종합점수", "사분면", "PBR", "ROE(%)"]
    ]
    for sector_name, subsectors in all_data.items():
        for sub_name, df in subsectors.items():
            if df.empty:
                continue
            for _, r in df.iterrows():
                def v(col):
                    val = r.get(col, "")
                    return "" if (pd.isna(val) if isinstance(val, float) else val is None) else val
                pbr_z = r.get("PBR_Z")
                roe_z = r.get("ROE_Z")
                pbr_z_val = None if (pd.isna(pbr_z) if isinstance(pbr_z, float) else pbr_z is None) else pbr_z
                roe_z_val = None if (pd.isna(roe_z) if isinstance(roe_z, float) else roe_z is None) else roe_z
                quad_rows.append([
                    sector_name, sub_name,
                    v("종목코드"), v("종목명"),
                    v("PBR_Z"), v("ROE_Z"), v("종합점수"),
                    get_quadrant(pbr_z_val, roe_z_val),
                    v("PBR"), v("ROE")
                ])
        quad_rows.append([])
    ws_quad.update(quad_rows, "A1")
    print(f"  ✅ 사분면 탭 업로드 완료")
    time.sleep(1)

    # ── 온도계 탭 ──
    thermo_tab = f"온도계_{today}"
    try:
        ws = sh.worksheet(thermo_tab)
        sh.del_worksheet(ws)
    except:
        pass
    ws_thermo = sh.add_worksheet(title=thermo_tab, rows=200, cols=11)

    def get_temp(current, benchmark):
        if current is None or benchmark is None or benchmark == 0:
            return "데이터없음"
        diff = (current - benchmark) / benchmark * 100
        if diff > 20:
            return f"🔴 과열 (+{diff:.1f}%)"
        elif diff < -20:
            return f"🟢 저평가 ({diff:.1f}%)"
        else:
            return f"🟡 적정 ({diff:+.1f}%)"

    thermo_rows = [
        [f"🌡️ 섹터 온도계 | 기준일: {today}"],
        ["※ 과열🔴: 현재 > 벤치마크 20%↑  /  적정🟡: ±20%  /  저평가🟢: 현재 < 벤치마크 20%↓"],
        [],
        ["대섹터", "세부섹터", "종목수",
         "현재PER", "벤치PER", "PER괴리율(%)", "PER온도",
         "현재PBR", "벤치PBR", "PBR괴리율(%)", "PBR온도"]
    ]
    for sector_name, subsectors in all_data.items():
        for sub_name, df in subsectors.items():
            if df.empty:
                continue
            def avg(col):
                if col in df.columns:
                    vals = pd.to_numeric(df[col], errors="coerce").dropna()
                    return round(float(vals.mean()), 2) if len(vals) > 0 else None
                return None
            cur_per = avg("PER")
            cur_pbr = avg("PBR")
            bench = BENCHMARK.get(sub_name, {})
            ben_per = bench.get("PER")
            ben_pbr = bench.get("PBR")
            per_diff = round((cur_per - ben_per) / ben_per * 100, 1) if cur_per and ben_per else ""
            pbr_diff = round((cur_pbr - ben_pbr) / ben_pbr * 100, 1) if cur_pbr and ben_pbr else ""
            thermo_rows.append([
                sector_name, sub_name, len(df),
                cur_per, ben_per, per_diff, get_temp(cur_per, ben_per),
                cur_pbr, ben_pbr, pbr_diff, get_temp(cur_pbr, ben_pbr)
            ])
        thermo_rows.append([])
    ws_thermo.update(thermo_rows, "A1")
    print(f"  ✅ 온도계 탭 업로드 완료")
    time.sleep(1)

    print(f"\n🎉 구글시트 업로드 완료!")
    print(f"🔗 https://docs.google.com/spreadsheets/d/{SECTOR_SHEET_ID}")

# ────────────────────────────────────────────────
# 메인 실행
# ────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 섹터 퀀트 키맞추기 시작")
    print(f"📅 기준일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 시총 순위 수집
    print("\n📈 시총 순위 수집 중...")
    marcap_dict = get_marcap_ranking()

    # 섹터별 데이터 수집
    all_data = collect_sector_data(marcap_dict)

    # 구글시트 저장
    save_to_gsheet(all_data)
