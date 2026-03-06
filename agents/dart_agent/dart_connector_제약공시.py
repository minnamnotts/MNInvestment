import os
import time

import OpenDartReader
import anthropic
import gspread
import pandas as pd
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials

# --- [설정 영역] ---
# .env 로드: 스크립트 폴더 → 상위(홈) → ~/.env
_script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_script_dir, ".env"))
load_dotenv(os.path.join(_script_dir, "..", ".env"))
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY")

if not ANTHROPIC_API_KEY or not DART_API_KEY:
    print("❌ 오류: API 키를 로드할 수 없습니다. .env 파일을 확인하세요.")
    print("   필요한 변수: ANTHROPIC_API_KEY, DART_API_KEY")
    exit(1)

# 클라이언트 초기화
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
dart = OpenDartReader(DART_API_KEY)


def get_latest_disclosure(corp_name):
    """특정 기업의 가장 최신 공시 본문을 가져옵니다."""
    try:
        list_df = dart.list(corp_name, kind="A")
    except (ValueError, Exception) as e:
        return f"{corp_name}의 공시를 찾을 수 없습니다. (DART 검색 실패: {e})"

    if list_df is None or list_df.empty:
        return f"{corp_name}의 공시를 찾을 수 없습니다."

    latest_rcept_no = list_df.iloc[0]["rcept_no"]
    report_title = list_df.iloc[0]["report_nm"]

    print(f"📄 분석 대상 보고서: {report_title} ({latest_rcept_no})")

    content = dart.document(latest_rcept_no)

    if content is None:
        return f"{corp_name} 보고서 본문을 가져올 수 없습니다."

    text = str(content) if not isinstance(content, str) else content
    return text[:10000]


def analyze_with_claude(text, max_retries=3):
    """공시 내용을 Claude로 분석 (계약, 임상, 기술수출). 오류 시 재시도."""
    prompt = f"""다음 바이오 기업 공시 내용을 분석해주세요. 아래 형식으로 답변해주세요.

## 분석 요청
1. **새로운 계약**: 새로 체결된 계약이 있는지
2. **임상 단계**: 임상 단계 (전임상, Phase 1/2/3, BLA/NDA 등)
3. **기술 수출 여부**: 기술 수출·라이선스 아웃 등
4. **향후 주가 전망**: 계약 이후 주가 전망 등

## 공시 내용
{text}
"""
    for attempt in range(max_retries):
        try:
            message = claude_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text

        except Exception as e:
            if "rate" in str(e).lower() or "429" in str(e):
                wait_sec = 35
                if attempt < max_retries - 1:
                    print(f"   ⏳ API 할당량 초과. {wait_sec}초 후 재시도 ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_sec)
                else:
                    raise
            else:
                raise


def analyze_multiple_corps(corp_list, save_to_file=None):
    """여러 종목을 순차 분석하고 결과를 반환/저장합니다."""
    results = []
    for i, corp_name in enumerate(corp_list, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(corp_list)}] 🔍 {corp_name} 공시 분석 중...")
        print("=" * 60)

        disclosure_text = get_latest_disclosure(corp_name)

        if "찾을 수 없습니다" in disclosure_text or "가져올 수 없습니다" in disclosure_text:
            print(disclosure_text)
            results.append({"corp": corp_name, "success": False, "content": disclosure_text})
        else:
            result = analyze_with_claude(disclosure_text)
            print(f"\n[AI 분석 리포트 - {corp_name}]")
            print(result)
            results.append({"corp": corp_name, "success": True, "content": result})

        if i < len(corp_list):
            time.sleep(2)

    if save_to_file:
        with open(save_to_file, "w", encoding="utf-8") as f:
            for r in results:
                f.write(f"\n{'='*60}\n[{r['corp']}]\n{'='*60}\n")
                f.write(r["content"] + "\n")
        print(f"\n📁 결과 저장: {save_to_file}")

    return results


def upload_to_google_sheet(df, sheet_name, key_file="google_key.json"):
    """Pandas 데이터를 구글 시트로 업로드합니다."""
    try:
        key_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), key_file)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
        client = gspread.authorize(creds)

        worksheet = client.open(sheet_name).sheet1
        worksheet.clear()
        worksheet.update([df.columns.values.tolist()] + df.values.tolist())

        print(f"🌐 구글 시트 업데이트 완료: {sheet_name}")
    except Exception as e:
        print(f"❌ 구글 시트 업로드 실패: {e}")


# --- [실행] ---
if __name__ == "__main__":
    TARGET_CORPS = [
        "리가켐바이오",
        "삼천당제약",
        "알테오젠",
        "에이비엘바이오",
        "에이프릴바이오"
    ]

    print(f"📋 총 {len(TARGET_CORPS)}개 종목 분석 예정: {', '.join(TARGET_CORPS)}")

    results = analyze_multiple_corps(
        TARGET_CORPS,
        save_to_file="disclosure_results.txt",
    )

    UPLOAD_TO_SHEET = True
    SHEET_NAME = "Dartanalysis_Bio"
    if UPLOAD_TO_SHEET and results:
        df = pd.DataFrame(results)
        upload_to_google_sheet(df, SHEET_NAME)