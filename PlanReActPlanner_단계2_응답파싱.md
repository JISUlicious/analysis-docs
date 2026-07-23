# [단계 2] LLM 응답과 파싱 — `thought=True`는 누가 만드는가

> 대상: ADK Python **v1.26.0**
> 코드: `src/google/adk/planners/plan_re_act_planner.py`의 `process_planning_response()` (L50~83), `_handle_non_function_call_parts()` (L101~141), `_mark_as_thought()` (L143~151)
> 호출 지점: `src/google/adk/flows/llm_flows/_nl_planning.py:90` (`_NlPlanningResponse` 응답 프로세서)

## 1. 핵심 결론

**LLM은 `thought`를 반환하지 않는다. ADK가 `.text` 문자열을 파싱해서 `thought=True`를 직접 찍는다.**

- **LLM이 실제 반환하는 것**: 평범한 **text 파트**(안에 `/*PLANNING*/` 등 태그가 **문자열로** 포함) + **function_call 파트**. 이 시점엔 `thought` 필드가 전부 `None`.
- **ADK가 하는 일**: `process_planning_response`가 각 파트의 `.text`를 검사해 태그로 시작/포함하는지 보고, 해당 파트에 `thought=True`를 세팅(`_mark_as_thought`).

> BuiltInPlanner와의 결정적 차이: BuiltInPlanner는 모델 **네이티브 thinking**을 사용 → 모델이 직접 `thought=True` 파트를 반환하고 ADK는 파싱/마킹을 하지 않음(`process_planning_response`가 `None` 반환). PlanReAct는 thinking 없는 모델도 되게 하려고 **평문 태그를 ADK가 파싱**.

## 2. 파싱 규칙 (문자열 매칭)

`_handle_non_function_call_parts` 기준:

| 조건 (text 문자열에 대해) | 처리 | thought |
|---|---|---|
| `/*FINAL_ANSWER*/` 를 **포함**(`in`) | 마지막 태그 기준 `_split_by_last_pattern` → 앞(태그 포함)=추론 / 뒤=최종답변 | 앞부분만 `True`, **최종답변은 평문** |
| `/*PLANNING*/`·`/*REASONING*/`·`/*ACTION*/`·`/*REPLANNING*/` 중 하나로 **시작**(`startswith`) | 파트 전체를 사고로 마킹 | `True` |
| 그 외 텍스트 | 그대로 보존 | 없음 |
| `function_call` 파트 | 그대로 보존(첫 함수콜 + 뒤에 연속된 함수콜들) | 없음(절대 thought 안 됨) |

### 주의점
- 계획/추론/액션 태그는 **`startswith`** 로 판정 → 태그가 텍스트 맨 앞에 있어야 함. 앞에 공백·줄바꿈이 있으면 마킹 안 됨(취약 지점).
- `/*FINAL_ANSWER*/` 만 **`in`(포함)** 으로 판정 → 텍스트 중간에 있어도 잡힘.
- `/*ACTION*/`는 프롬프트상 "도구 코드"용 태그지만, **ADK의 실제 동작은 네이티브 `function_call` 파트**로 나감. 모델이 `/*ACTION*/` 텍스트를 뱉으면 그냥 thought로 마킹될 뿐이고, 진짜 액션은 function_call.

## 3. `process_planning_response`의 파트 순회 로직 (L50~83)

```
preserved_parts = []
first_fc_part_index = -1
for i, part in enumerate(response_parts):
    if part.function_call:
        if not part.function_call.name:   # 이름 없는 함수콜은 스킵
            continue
        preserved_parts.append(part)      # 첫 함수콜 보존
        first_fc_part_index = i
        break                             # 첫 함수콜에서 순회 중단
    _handle_non_function_call_parts(part, preserved_parts)   # 텍스트 파트 파싱/마킹

if first_fc_part_index > 0:               # 첫 함수콜 뒤에 연속된 함수콜(병렬 호출)만 추가 보존
    j = first_fc_part_index + 1
    while j < len(response_parts) and response_parts[j].function_call:
        preserved_parts.append(response_parts[j]); j += 1
return preserved_parts
```

- 첫 함수콜을 만나면 순회를 멈추고, 그 뒤로는 **연속된 함수콜만** 추가로 챙긴다(중간에 텍스트가 끼면 거기서 끊김).

## 4. 구체적 파트 배열 워크스루

### ① 도구를 부르는 스텝 — LLM 반환 원본 (thought 없음)
```python
parts = [
  Part(text="/*PLANNING*/\n1. roll_die(6) 호출\n2. 소수 판정\n/*ACTION*/"),   # thought=None
  Part(function_call=FunctionCall(name="roll_die", args={"sides": 6})),        # thought=None
]
```
처리 후:
```python
preserved = [
  Part(text="/*PLANNING*/\n1. ...\n/*ACTION*/", thought=True),   # startswith('/*PLANNING*/') → ADK가 마킹
  Part(function_call=roll_die(sides=6)),                          # 그대로
]
```
→ function_call 존재 → `is_final_response=False` → 툴 실행 분기.

### ② 마지막 스텝 — LLM 반환 원본
```python
parts = [
  Part(text="/*REASONING*/ 주사위=4, 4=2×2 → 소수 아님.\n/*FINAL_ANSWER*/\n4가 나왔고, 소수가 아닙니다."),  # thought=None
]
```
처리 후 (`/*FINAL_ANSWER*/` 포함 → split):
```python
preserved = [
  Part(text="/*REASONING*/ ... /*FINAL_ANSWER*/", thought=True),  # 앞부분(태그 포함)=추론 → 마킹
  Part(text="\n4가 나왔고, 소수가 아닙니다."),                      # 뒷부분=최종답변 → 평문
]
```
→ function_call 없음 → `is_final_response=True`(`events/event.py:82`) → 루프 종료. 사용자에게 보이는 최종 답변은 **평문 파트**뿐(추론 파트는 thought라 접힘).

## 5. 마킹된 파트의 행방
`process_planning_response`가 반환한 `preserved`가 `llm_response.content.parts`를 교체(`_nl_planning.py:93`) → 이벤트로 세션에 저장(thought 마커째로) → 사용자에겐 thought 접힘 → **다음 스텝에서 `_remove_thought_from_request`로 다시 평문화되어 모델에 재노출**(→ [단계 3]).
