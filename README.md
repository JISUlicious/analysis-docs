# Analysis Docs

코드/프레임워크 동작 분석 문서 모음. 주제별로 디렉터리를 나눠 정리한다.

```
google-adk/
├── planreact-planner/   PlanReActPlanner — 프롬프트가 단계별로 어떻게 변하는가
└── agent-tool/          AgentTool — 에이전트를 도구로 감쌌을 때의 동작
```

---

# Google ADK (Python)

분석 기준 버전: **v1.26.0** (`google/adk-python`, 태그 `427a983b`)

## 1. PlanReActPlanner — 단계별 프롬프트 변화

📁 [`google-adk/planreact-planner/`](google-adk/planreact-planner/)

사용자 요청이 들어온 순간부터 최종 답변까지, **LLM이 보는 프롬프트가 단계별로 어떻게 변하는지** 소스 코드 기준으로 추적한 분석.

**읽는 순서**

| # | 문서 | 내용 |
|---|---|---|
| 0 | [분석보고서](google-adk/planreact-planner/PlanReActPlanner_분석보고서.md) | 전체 개요 · 호출 체인 · 종합 요약 (여기부터) |
| 1 | [단계 1 · 주입 프롬프트](google-adk/planreact-planner/PlanReActPlanner_단계1_주입프롬프트.md) | planner가 system_instruction에 주입하는 실제 프롬프트 원문 |
| 2 | [단계 2 · 응답 파싱](google-adk/planreact-planner/PlanReActPlanner_단계2_응답파싱.md) | LLM 응답의 태그 파싱과 `thought=True` 마킹 (누가 만드는가) |
| 3 | [단계 3 · 스텝 전환 / thought 언마킹](google-adk/planreact-planner/PlanReActPlanner_단계3_스텝전환_thought언마킹.md) | 툴 실행 → contents 재조립 → thought 언마킹 |
| 4 | [단계 4 · 루프 종료 / 최종답변](google-adk/planreact-planner/PlanReActPlanner_단계4_루프종료_최종답변.md) | `/*FINAL_ANSWER*/` split → 루프 종료 → 세션 저장 |
| 5 | [단계 5 · 두 번째 사이클](google-adk/planreact-planner/PlanReActPlanner_단계5_두번째사이클.md) | 다음 사용자 턴에서의 재진행 |
| 6 | [부록 · 단계별 프롬프트/응답 트레이스](google-adk/planreact-planner/PlanReActPlanner_단계별_프롬프트_응답_트레이스.md) | 실제 ADK instruction 포함 프롬프트 전문 + 스텝별 예상 응답 + 1·2턴 태그 내용 변화 |

**다이어그램** (draw.io — [app.diagrams.net](https://app.diagrams.net) 또는 VS Code Draw.io Integration 확장으로 열기)

| 파일 | 내용 |
|---|---|
| [전체 흐름도](google-adk/planreact-planner/PlanReActPlanner_flow.drawio) | 요청 → 전처리/주입 → LLM → 후처리/파싱 → 툴 loop → 최종답변 → 다음 사이클 |
| [태그 내용 변화표](google-adk/planreact-planner/PlanReActPlanner_태그내용변화.drawio) | 1·2턴에 걸친 각 태그(PLANNING/ACTION/REASONING/REPLANNING/FINAL_ANSWER)의 append-only 누적 |

### 핵심 요약

- PlanReActPlanner는 **모델 내장 thinking 없이** 태그(`/*PLANNING*/`, `/*REASONING*/`, `/*ACTION*/`, `/*REPLANNING*/`, `/*FINAL_ANSWER*/`) 기반으로 동작.
- **system 지침은 매 스텝 동일하게 재부착(정적)**, 실제로 변하는 것은 **contents(대화 히스토리) 누적**.
- 계획/추론 텍스트는 **사용자에겐 thought로 접히지만, 다음 스텝에서 모델에겐 평문으로 재노출**(마킹 → 언마킹 2단 구조).
- 프레임워크는 태그 블록 내용을 **편집하지 않음**(append-only, 동결).
- system_instruction 조립 순서: `agent 지침 → identity → planning → sub-agent(전송대상) 목록 → skills`.

## 2. AgentTool — 에이전트를 도구로 감쌌을 때

📁 [`google-adk/agent-tool/`](google-adk/agent-tool/)

| 문서 | 내용 |
|---|---|
| [AgentTool 동작 분석](google-adk/agent-tool/AgentTool_동작분석.md) | `AgentTool(LlmAgent)` 의 동작을 일반 agent 흐름 및 sub-agent 전송과 **대비**해 정리 |

### 핵심 요약

- AgentTool은 에이전트를 부모 LLM의 **함수(tool)** 로 노출하고, 호출되면 **격리된 Runner/세션에서 자식을 완주**시킨 뒤 **마지막 응답 텍스트만 function_response로 반환**한다 (블랙박스 위임).
- **sub-agent 전송과의 차이**: 전송은 *대화 주체 교체*(컨텍스트 공유·제어권 이전), AgentTool은 *위임 후 결과 회수*(세션 격리·부모가 제어권 유지).
- 상태는 **양방향** — 시작 시 부모 state 복사 입력, 실행 중 자식 `state_delta`를 부모로 역전파.
- **tool 이름 = `agent.name`**, 설명 = `agent.description` (별도 rename 옵션 없음).
- 리소스: 아티팩트·자격증명은 공유, **메모리·세션은 격리**, 플러그인은 `include_plugins`로 선택.
