# 🔒 Analysis Agent — PARKED

## 구현 예정 기능
- 퀀트 결과 → Claude API → 버티컬 인사이트 리포트
- 보물/성장 종목 자동 추출 → DART 분석 → 종합 리포트
- 텔레그램 채널 자동 발송

## 의존성
- agents/quant_agent/sector_quant.py 결과 활용
- agents/dart_agent/ 연동
- Claude API (claude-sonnet-4-5-20251001)

## 구현 우선순위
1. 버티컬 인사이트 (섹터 분석)
2. 개별종목 원스탑 리포트
