"""
공시 내용 분석기 - Google Gemini API를 사용하여 새로운 계약, 임상 단계, 기술 수출 여부를 분석합니다.
"""

import json
import os

from google import genai

# API 키 - 환경 변수 GEMINI_API_KEY 우선, 없거나 플레이스홀더면 아래 값 사용
_env_key = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_API_KEY = "REDACTED_API_KEY"  # 여기에 키 입력 (또는 export GEMINI_API_KEY='키')
GEMINI_API_KEY = _env_key if _env_key and _env_key != "YOUR_API_KEY_HERE" else DEFAULT_API_KEY

# Gemini API 클라이언트
client = genai.Client(api_key=GEMINI_API_KEY)


def analyze_disclosure(disclosure_text: str) -> dict:
    """
    공시 내용을 분석하여 새로운 계약, 임상 단계, 기술 수출 여부를 반환합니다.

    Args:
        disclosure_text: 분석할 공시 내용 텍스트

    Returns:
        분석 결과 딕셔너리
    """
    prompt = f"""
다음 공시 내용을 분석해주세요. 아래 형식으로 JSON 형태로만 답변해주세요.

## 공시 내용
{disclosure_text}

## 분석 요청
1. **새로운 계약**: 새로 체결된 계약이 있는지 분석해주세요.
   - 계약 유형(연구개발, 라이선스, 제조, 유통 등), 상대방, 금액, 주요 조건 등 요약
   - 없으면 "해당 없음" 또는 "언급 없음"

2. **임상 단계**: 해당 바이오/제약 기업의 임상 단계를 분석해주세요.
   - Pre-clinical (전임상), Phase 1, Phase 2, Phase 3, BLA/NDA 신청, 승인 등 구체적으로 파악
   - 여러 단계가 있으면 모두 나열

3. **기술 수출 여부**: 기술 수출이나 라이선스 아웃 계약 등이 언급되었는지 분석해주세요.
   - 수출/라이선스 아웃 계약이 있으면 상대방, 금액, 조건 등 요약
   - 없으면 "해당 없음" 또는 "언급 없음"

## 출력 형식 (JSON)
{{
    "new_contract": "분석 결과 (한국어)",
    "clinical_stage": "분석 결과 (한국어)",
    "technology_export": "분석 결과 (한국어)",
    "summary": "전체 요약 (1-2문장)"
}}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    # 응답 텍스트 추출
    result_text = response.text.strip()

    # JSON 블록 추출 시도
    if "```json" in result_text:
        result_text = result_text.split("```json")[1].split("```")[0].strip()
    elif "```" in result_text:
        result_text = result_text.split("```")[1].split("```")[0].strip()

    try:
        result = json.loads(result_text)
        return result
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 원문 반환
        return {
            "raw_response": result_text,
            "new_contract": "파싱 실패 - 원문 참조",
            "clinical_stage": "파싱 실패 - 원문 참조",
            "technology_export": "파싱 실패 - 원문 참조",
        }


def main():
    import sys

    # 사용법: python disclosure_analyzer.py [파일경로] 또는 python disclosure_analyzer.py "공시 텍스트"
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isfile(arg):
            with open(arg, "r", encoding="utf-8") as f:
                disclosure_text = f.read()
        else:
            disclosure_text = " ".join(sys.argv[1:])
    else:
        disclosure_text = """
○○바이오(주)는 자사의 항암제 후보물질 ABC-123에 대해
미국 FDA로부터 Phase 2 임상시험 허가를 받았으며,
중국 ○○제약과 5000만 달러 규모의 라이선스 아웃 계약을 체결했다고 밝혔다.
"""

    if GEMINI_API_KEY == "YOUR_API_KEY_HERE":
        print("오류: GEMINI_API_KEY를 설정해주세요.")
        print("  - 코드 내 GEMINI_API_KEY 변수 수정")
        print("  - 또는 환경 변수: export GEMINI_API_KEY='your-api-key'")
        return

    print("=" * 60)
    print("공시 내용 분석 결과")
    print("=" * 60)

    result = analyze_disclosure(disclosure_text)

    print("\n[새로운 계약]")
    print(result.get("new_contract", "N/A"))

    print("\n[임상 단계]")
    print(result.get("clinical_stage", "N/A"))

    print("\n[기술 수출 여부]")
    print(result.get("technology_export", "N/A"))

    if result.get("summary"):
        print("\n[요약]")
        print(result["summary"])

    return result


if __name__ == "__main__":
    main()
