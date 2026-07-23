# Analysis Docs

코드/프레임워크 동작 분석 문서 모음.

## Google ADK — PlanReActPlanner 동작 분석 (v1.26.0)

`google/adk-python` v1.26.0 기준으로, 사용자 요청이 들어온 순간부터 최종 답변까지 **LLM이 보는 프롬프트가 단계별로 어떻게 변하는지** 소스 코드 기준으로 추적한 분석입니다.

| 문서 | 내용 |
|---|---|
| [분석보고서](PlanReActPlanner_분석보고서.md) | 전체 개요 · 호출 체인 · 종합 요약 |
| [단계 1 · 주입 프롬프트](PlanReActPlanner_단계1_주입프롬프트.md) | planner가 system_instruction에 주입하는 실제 프롬프트 원문 |
| [단계 2 · 응답 파싱](PlanReActPlanner_단계2_응답파싱.md) | LLM 응답의 태그 파싱과 `thought=True` 마킹 (누가 만드는가) |
| [단계 3 · 스텝 전환 / thought 언마킹](PlanReActPlanner_단계3_스텝전환_thought언마킹.md) | 툴 실행 → contents 재조립 → thought 언마킹 |
| [단계 4 · 루프 종료 / 최종답변](PlanReActPlanner_단계4_루프종료_최종답변.md) | `/*FINAL_ANSWER*/` split → 루프 종료 → 세션 저장 |
| [단계 5 · 두 번째 사이클](PlanReActPlanner_단계5_두번째사이클.md) | 다음 사용자 턴에서의 재진행 |
| [흐름도 (draw.io)](PlanReActPlanner_flow.drawio) | 요청→응답→툴 loop→최종답변 전체 흐름 다이어그램 ([app.diagrams.net](https://app.diagrams.net)에서 열기) |

### 핵심 요약

- PlanReActPlanner는 **모델 내장 thinking 없이** 태그(`/*PLANNING*/`, `/*REASONING*/`, `/*ACTION*/`, `/*REPLANNING*/`, `/*FINAL_ANSWER*/`) 기반으로 동작.
- **system 지침은 매 스텝 동일하게 재부착(정적)**, 실제로 변하는 것은 **contents(대화 히스토리) 누적**.
- 계획/추론 텍스트는 **사용자에겐 thought로 접히지만, 다음 스텝에서 모델에겐 평문으로 재노출**(마킹 → 언마킹 2단 구조).
- 프레임워크는 태그 블록 내용을 **편집하지 않음**(append-only, 동결).
