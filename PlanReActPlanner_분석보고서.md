# PlanReActPlanner 동작 분석 보고서 (ADK Python v1.26.0)

> 분석 대상: `google/adk-python` **v1.26.0** 태그(`427a983b`)
> 검증: 실제 소스 + 단위테스트 `tests/unittests/flows/llm_flows/test_nl_planning.py`

## 0. 핵심 결론 먼저 (TL;DR)

- **PlanReActPlanner는 "모델 내장 thinking"을 쓰지 않는** 순수 프롬프트 기반 플래너입니다. 태그(`/*PLANNING*/`, `/*REASONING*/`, `/*ACTION*/`, `/*REPLANNING*/`, `/*FINAL_ANSWER*/`)로 응답 구조를 강제합니다.
- 플래너가 개입하는 지점은 **딱 두 군데**입니다.
  1. **요청 전처리** — system_instruction 끝에 "NL 플래너 지침"을 **매 스텝 붙임** + 히스토리의 `thought` 마커 제거.
  2. **응답 후처리** — 모델 출력 중 계획/추론 텍스트를 `thought=True`로 마킹해 답변 텍스트와 분리.
- **system 프롬프트(지침)는 스텝마다 내용이 동일**합니다(정적). **실제로 변하는 것은 `contents`(대화 히스토리)** 이며, 매 스텝 세션 이벤트로부터 새로 조립됩니다.
- 가장 미묘한 트릭: 계획/추론 텍스트는 **사용자에겐 "생각(thought)"으로 접히지만, 다음 스텝에서 모델에겐 일반 텍스트로 다시 노출**됩니다(마킹 → 언마킹의 2단 구조).

---

## 1. 등장 인물 (핵심 파일)

| 파일 | 역할 |
|---|---|
| `src/google/adk/runners.py` | 사용자 메시지를 세션에 저장, 에이전트 실행, 이벤트를 세션에 append |
| `src/google/adk/agents/llm_agent.py` | `planner` 필드 보유, `SingleFlow`/`AutoFlow` 선택 (`_llm_flow`, L700) |
| `src/google/adk/flows/llm_flows/base_llm_flow.py` | **LLM 호출 루프**(`run_async` → `_run_one_step_async`) |
| `src/google/adk/flows/llm_flows/single_flow.py` | 요청/응답 프로세서 **순서** 정의 |
| `src/google/adk/flows/llm_flows/_nl_planning.py` | 플래너를 실제로 호출하는 request/response 프로세서 |
| `src/google/adk/planners/plan_re_act_planner.py` | **PlanReActPlanner 본체** (지침 생성 + 응답 파싱) |
| `src/google/adk/flows/llm_flows/contents.py` | 세션 이벤트 → `llm_request.contents` 재조립 |
| `src/google/adk/flows/llm_flows/instructions.py` · `identity.py` | system_instruction의 앞부분 구성 |

---

## 2. 전체 호출 체인 (사용자 요청 → 최종 답변)

러너→에이전트→모델→툴 분기→loop 흐름에 코드를 매핑하면:

```
[사용자 요청]  new_message = UserContent("주사위 굴리고 소수인지 알려줘")
      │
      ▼
[Runner.run_async]  runners.py
  └ _append_new_message_to_session()   ← 세션에 'user' 이벤트 저장 (L856~924)
  └ _find_agent_to_run() → agent.run_async(ctx)
      │
      ▼
[Agent._run_async_impl]  llm_agent.py:450
  └ self._llm_flow.run_async(ctx)      ← planner 유무와 무관하게 SingleFlow/AutoFlow
      │
      ▼
[BaseLlmFlow.run_async]  base_llm_flow.py:745   ◀────────────┐  (툴이 있으면 여기로 loop)
  while True:                                                 │
    _run_one_step_async(ctx)   ← "1 스텝 = LLM 1회 호출"       │
      ├─ (A) _preprocess_async  : 요청 프로세서들 순차 실행     │
      │        └ llm_request 조립 (★플래너 지침 여기서 주입)   │
      ├─ (B) _call_llm_async    : [모델 호출]                  │
      └─ (C) _postprocess_async : 응답 프로세서 + 함수콜 처리   │
               ├ process_planning_response (★응답 파싱/마킹)   │
               └ 함수콜 있으면 → handle_function_calls_async ──┘  (툴 실행 후 loop 재진입)
  break 조건: last_event.is_final_response() == True  (함수콜 없음 = 최종 답변)
```

- **루프 주체**는 `BaseLlmFlow.run_async`(`base_llm_flow.py:749`)의 `while True`입니다. 한 바퀴 = LLM 1회 호출 + (있으면) 툴 실행.
- **툴 분기 판정**은 `_postprocess_async`(`base_llm_flow.py:919`)에서 `model_response_event.get_function_calls()`로 이뤄집니다. 함수콜이 있으면 툴을 실행하고 그 응답을 세션에 넣은 뒤 루프를 계속, 없으면 `is_final_response()==True`(`events/event.py:82`)가 되어 루프가 종료됩니다.
- **매 스텝 `llm_request = LlmRequest()`로 완전히 새로 만들어집니다**(`base_llm_flow.py:765`). 즉 프롬프트는 매번 "세션 이벤트로부터 처음부터 재조립"됩니다. 스텝 간에 request를 이어붙이는 게 아닙니다.

---

## 3. 플래너가 개입하는 두 지점

### 3-1. 요청 전처리 — `_NlPlanningRequestProcessor` (`_nl_planning.py:39`)

```python
elif isinstance(planner, PlanReActPlanner):
    if planning_instruction := planner.build_planning_instruction(...):
        llm_request.append_instructions([planning_instruction])   # ① 지침 주입
    _remove_thought_from_request(llm_request)                     # ② thought 마커 제거
```

- **① 지침 주입**: `build_planning_instruction`은 정적인 NL 플래너 지침 문자열(`_build_nl_planner_instruction`, `plan_re_act_planner.py:153`)을 반환하고, `append_instructions`가 이를 `system_instruction` 끝에 `\n\n`로 이어붙입니다(`llm_request.py:227~231`).
- **② thought 제거**: 히스토리의 모든 파트에 대해 `part.thought = None`으로 되돌립니다(`_nl_planning.py:125`). → 5절의 핵심.

> **프로세서 순서가 중요합니다.** `single_flow.py`에서 `_nl_planning`은 반드시 `contents` **뒤에** 배치됩니다. `contents`가 `llm_request.contents`를 통째로 재할당하기 때문에, thought 언마킹은 그 뒤에 일어나야 효과가 남습니다(single_flow.py 주석에 명시).

**system_instruction 최종 구성 순서** (요청 프로세서 실행 순서대로):
```
[agent.instruction]           ← instructions.py (사용자가 준 지침)
"\n\n"
[identity]  "You are an agent. Your internal name is "..."."   ← identity.py
"\n\n"
[PlanReAct NL 플래너 지침]     ← _nl_planning.py (★플래너가 붙이는 부분)
```

### 3-2. 응답 후처리 — `_NlPlanningResponse` (`_nl_planning.py:69`)

`process_planning_response`(`plan_re_act_planner.py:50`)가 모델 출력 파트를 순회하며:
- **첫 함수콜 그룹**을 만나면 그것을 보존하고, 그 뒤에 연속된 함수콜들만 추가로 보존(병렬 함수콜 지원, L74~81).
- **텍스트 파트**는 `_handle_non_function_call_parts`로 처리:
  - `/*FINAL_ANSWER*/` 포함 시 → 마지막 태그 기준으로 분리. 앞쪽(추론)은 `thought=True` 마킹, 뒤쪽(최종 답변)은 **일반 텍스트**로 보존(L111~124).
  - 태그(`PLANNING`/`REASONING`/`ACTION`/`REPLANNING`)로 **시작**하는 텍스트 → `thought=True` 마킹(L129~140).

---

## 4. 단계별 프롬프트 변화 (핵심)

시나리오: 툴 `roll_die(sides)` 보유, `planner=PlanReActPlanner()`. 사용자: **"6면체 주사위 굴리고 소수인지 알려줘"**

### T0 — 사용자 요청 도착
`Runner`가 세션에 user 이벤트 저장:
```
session.events = [ user: "6면체 주사위 굴리고 소수인지 알려줘" ]
```

### 스텝 1 — 첫 LLM 호출

**모델이 보는 프롬프트:**
```
── system_instruction ──────────────────────────────
[agent.instruction]                         (사용자 지정 지침)
You are an agent. Your internal name is "...".   (identity)
<PlanReAct NL 플래너 지침>                   ← ★신규 주입
  · 답변 시 도구를 우선 활용하라
  · (1) 자연어로 계획 → (2) 도구 실행 + 사이사이 추론 → (3) 최종 답변
  · 계획=/*PLANNING*/, 도구코드=/*ACTION*/, 추론=/*REASONING*/, 최종=/*FINAL_ANSWER*/
  · 실패 시 /*REPLANNING*/ 로 계획 수정 ...
── contents ────────────────────────────────────────
user: "6면체 주사위 굴리고 소수인지 알려줘"
```
`_remove_thought_from_request`는 이 시점엔 히스토리에 thought가 없어 no-op.

**모델 응답(예):**
```
Part(text="/*PLANNING*/\n1. roll_die(6) 호출\n2. 결과로 소수 판정")
Part(function_call = roll_die(sides=6))
```

**응답 후처리 결과 →** `[ text("/*PLANNING*/...", thought=True), function_call ]` → 함수콜 존재 → `is_final_response=False` → **툴 실행 분기**.
`roll_die(6)=4` → function_response 이벤트 저장.
```
session.events = [ user, model(PLANNING[thought]+fc), user(function_response=4) ]
```

### 스텝 2 — 툴 결과 후 LLM 재호출 (loop)

`contents`가 세션 이벤트로부터 **재조립**됩니다. 스텝 1 대비 **바뀐 부분만** 강조:

```
── system_instruction ──────────────────────────────
(스텝 1과 완전히 동일 — 매 스텝 같은 지침이 다시 붙음)
── contents ────────────────────────────────────────
user:  "6면체 주사위 굴리고 소수인지 알려줘"
model: "/*PLANNING*/\n1. roll_die(6) 호출\n2. ..."   ← ★추가 (thought 마커는 제거되어 일반 텍스트로 노출!)
       function_call: roll_die(sides=6)               ← ★추가
user:  function_response: roll_die → 4                ← ★추가
```

여기서 두 가지 변화가 동시에 일어납니다:
1. **contents 증가**: 모델의 이전 계획+함수콜, 그리고 함수 응답이 새로 붙습니다.
2. **thought 언마킹**: 스텝 1에서 `thought=True`로 저장됐던 `/*PLANNING*/` 텍스트가 `_remove_thought_from_request`로 `thought=None`이 되어 **모델에게 일반 텍스트로 다시 보입니다.** → 모델이 "자신이 세운 계획"을 이어서 참조 가능.

**모델 응답(예):**
```
Part(text="/*REASONING*/ 주사위=4. 이제 소수 판정. 4=2×2 → 소수 아님. /*FINAL_ANSWER*/ 4가 나왔고, 4는 소수가 아닙니다.")
```

**응답 후처리:** `/*FINAL_ANSWER*/` 기준 분리 →
- 앞부분 `"/*REASONING*/ ... /*FINAL_ANSWER*/"` → `thought=True`
- 뒷부분 `" 4가 나왔고, 4는 소수가 아닙니다."` → **일반 텍스트(최종 답변)**

함수콜 없음 → `is_final_response=True` → **루프 종료**, 최종 답변 반환.

### 스텝 간 프롬프트 변화 요약표

| 구성요소 | 스텝1 | 스텝2 | 변화 성격 |
|---|---|---|---|
| system: agent instruction | 있음 | 있음 | 불변 |
| system: identity | 있음 | 있음 | 불변 |
| system: **PlanReAct 지침** | 붙음 | 붙음 | **매 스텝 동일하게 재부착 (정적)** |
| contents | user 1개 | user + model(계획+fc) + tool응답 | **누적 증가 (동적)** |
| 이전 계획 텍스트의 thought | — | `True→None` 언마킹 | **모델에 재노출** |

---

## 5. 가장 미묘한 부분 — `thought` 마킹/언마킹의 2단 구조

이 플래너를 이해하는 열쇠입니다.

- **응답 후처리(마킹, `_mark_as_thought`)**: 계획·추론 텍스트에 `thought=True`를 찍어 **최종 답변과 분리**합니다. 이 마커는 세션 이벤트에 그대로 저장됩니다. UI/`is_final_response` 관점에서 "생각"은 접히고, 순수 답변만 최종 텍스트로 취급됩니다.
- **다음 스텝 요청 전처리(언마킹, `_remove_thought_from_request`)**: 히스토리를 모델에 보낼 때 `thought=None`으로 되돌립니다. **모델은 자신의 전체 추론 궤적(계획+추론)을 일반 텍스트로 계속 봅니다.**

즉 **"사용자 화면에서는 접히지만, 모델은 계속 본다"** 가 성립합니다. 이것이 내장 thinking(BuiltInPlanner)과의 결정적 차이입니다 — PlanReAct는 thinking 미지원 모델에서도 동작하도록, 사고 과정을 일반 텍스트 대화 이력으로 왕복시킵니다. (테스트 `test_remove_thought_from_request_with_thoughts`로 확인됨)

### 관련 주의점 — 순수 추론 파트는 히스토리에서 소거될 수 있음
`contents.py`의 `_is_part_invisible`(L232)은 `thought=True`이면서 함수콜/응답이 아닌 파트를 "invisible" 처리하고, `_contains_empty_content`(L261)는 **모든** 파트가 invisible인 이벤트를 컨텍스트에서 제외합니다. PlanReAct에서는 계획/추론이 보통 **같은 이벤트 안의 function_call 또는 final_answer 텍스트와 묶여** 있어 이벤트 자체는 살아남고, deepcopy로 계획 텍스트도 함께 실려 갑니다(그 뒤 언마킹). 다만 모델이 함수콜/최종답 없이 **계획 텍스트만** 단독 출력하면 그 이벤트는 `is_final_response=True`로 간주되어 루프가 조기 종료될 수 있는 구조적 특성이 있습니다(엣지 케이스).

---

## 6. BuiltInPlanner와의 대비 (참고)

`_nl_planning.py:51`에서 플래너 타입으로 분기됩니다.

| | PlanReActPlanner | BuiltInPlanner |
|---|---|---|
| 개입 방식 | system_instruction에 **텍스트 지침** 주입 | `config.thinking_config` 설정 (`apply_thinking_config`) |
| 사고 표현 | `/*태그*/` 기반 일반 텍스트 | 모델 **내장 thinking** |
| 응답 후처리 | 계획/추론 마킹 + 답변 분리 | **없음** (`process_planning_response`가 `None` 반환) |
| thought 언마킹 | **함** (`_remove_thought_from_request`) | 안 함 |
| 모델 요구사항 | thinking 불필요 | thinking 지원 모델 필요 |

또한 **`agent.planner`가 설정되지 않으면**(`_get_planner`가 `None` 반환) 플래닝 프로세서는 즉시 반환하여 아무 개입도 하지 않습니다. PlanReAct는 명시적으로 `planner=PlanReActPlanner()`를 줘야 활성화됩니다.

---

## 7. 한눈에 보는 종합 다이어그램

```
사용자 요청
  │  Runner: 세션에 user 이벤트 저장
  ▼
┌─ BaseLlmFlow.run_async  (while: LLM 1회 = 1스텝) ───────────────┐
│                                                                 │
│  (A) 전처리  ── 요청 프로세서 순서 ──                            │
│      instructions → identity → contents → _nl_planning          │
│      · system  = [agent지침]+[identity]+[★PlanReAct지침(정적)]   │
│      · contents = 세션 이벤트에서 재조립 (매 스텝 새로)          │
│      · ★thought 마커 제거 → 이전 계획/추론을 일반텍스트로 재노출 │
│                                                                 │
│  (B) 모델 호출                                                  │
│                                                                 │
│  (C) 후처리  process_planning_response                          │
│      · 계획/추론 텍스트 → thought=True 마킹                      │
│      · /*FINAL_ANSWER*/ 뒤쪽만 일반 답변으로 분리                │
│      · 함수콜 있나?                                              │
│         ├ 예 → 툴 실행 → function_response 세션 저장 → loop ▲    │
│         └ 아니오(is_final_response) → 종료 ▼                     │
└─────────────────────────────────────────────────────────────────┘
  ▼
최종 답변 (/*FINAL_ANSWER*/ 뒤 텍스트)
```

**요약 한 문장**: PlanReActPlanner는 (1) system_instruction에 정적 태그 규칙을 매 스텝 재부착하고, (2) 모델의 계획/추론을 `thought`로 마킹했다가 다음 스텝에 다시 언마킹해 히스토리로 왕복시키는 방식으로, 툴 실행 루프가 반복되는 동안 **system 지침은 고정하되 `contents`(대화 궤적)만 누적·재노출**시키며 최종 답변에 도달합니다.
