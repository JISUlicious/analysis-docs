# [단계 4] 루프 종료와 최종답변 확정

> 대상: ADK Python **v1.26.0**
> 코드:
> - FINAL_ANSWER split: `src/google/adk/planners/plan_re_act_planner.py`의 `_handle_non_function_call_parts` (L111~124), `_split_by_last_pattern` (L85~99)
> - 최종 판정: `src/google/adk/events/event.py`의 `is_final_response()` (L82)
> - 루프 종료: `src/google/adk/flows/llm_flows/base_llm_flow.py`의 `run_async()` (L749~758)
> - 세션 저장: `src/google/adk/runners.py`의 `_should_append_event()` (L705) → `session_service.append_event`

## 1. 이 단계의 트리거

어느 스텝에서 모델이 텍스트 파트에 `/*FINAL_ANSWER*/`를 포함하고 **함수콜을 내지 않으면**, 그 스텝이 마지막이 된다.

## 2. FINAL_ANSWER split (응답 후처리)

`_handle_non_function_call_parts`가 `/*FINAL_ANSWER*/`를 **포함(`in`)** 감지하면 `_split_by_last_pattern`으로 **마지막 태그 기준** 2조각으로 분할:

```python
def _split_by_last_pattern(self, text, separator):
    index = text.rfind(separator)                 # 마지막 /*FINAL_ANSWER*/ 위치
    if index == -1:
        return text, ''
    return text[: index + len(separator)],        # ① 추론 슬라이스 (태그 '포함')
           text[index + len(separator):]          # ② 최종답변 슬라이스 (태그 '이후')
```

- ① 추론 슬라이스 → `types.Part(text=...)` 새로 만들고 **`thought=True` 마킹** 후 보존
- ② 최종답변 슬라이스 → `types.Part(text=...)`로 **평문(thought 없음)** 보존
- **주의**: `/*FINAL_ANSWER*/` 태그 문자열은 **①(추론) 쪽에 붙어 남는다.** 따라서 **사용자에게 보이는 최종답변(②)에는 태그가 없다.** (앞부분에 split로 인한 선행 개행이 남을 수 있음)

예:
```python
# LLM 반환 원본 (thought 없음)
[ Part(text="/*REASONING*/ 주사위=4, 4=2×2 → 소수 아님.\n/*FINAL_ANSWER*/\n4가 나왔고, 소수가 아닙니다.") ]

# process_planning_response 후
[ Part(text="/*REASONING*/ ... /*FINAL_ANSWER*/", thought=True),   # ① 추론(태그 포함) → 접힘
  Part(text="\n4가 나왔고, 소수가 아닙니다.") ]                     # ② 최종답변(태그 없음) → 노출
```

## 3. `is_final_response` 판정 (event.py:82)

```python
def is_final_response(self):
    if self.actions.skip_summarization or self.long_running_tool_ids:
        return True
    return (not self.get_function_calls()
            and not self.get_function_responses()
            and not self.partial
            and not self.has_trailing_code_execution_result())
```

- 위 최종 이벤트는 파트가 `[추론(thought), 최종답변(text)]`뿐 → **함수콜/함수응답 없음, partial 아님, 코드실행결과 없음** → **`True`**.
- `thought` 파트 유무는 판정에 **영향 없음** (텍스트만 있으면 최종답변 자격).

## 4. 루프 종료 (base_llm_flow.py:749)

```python
async def run_async(self, ctx):
    while True:
        last_event = None
        async for event in self._run_one_step_async(ctx):
            last_event = event
            yield event
        if not last_event or last_event.is_final_response() or last_event.partial:
            break        # ← 최종답변 이벤트에서 여기서 탈출
```

- 이 스텝은 함수콜이 없으므로 `_postprocess_async`가 **툴 실행 분기로 가지 않고** 최종 이벤트만 yield.
- `last_event.is_final_response() == True` → **`break`. 추가 LLM 호출 없음.**

## 5. 세션 저장 (runner)

- runner의 실행 루프가 yield된 각 이벤트를 `_should_append_event`(non-live면 항상 `True`) 확인 후 `session_service.append_event`로 저장.
- **최종 이벤트도 `[추론(thought=True), 최종답변(평문)]` 그대로(=thought 마커째로) 세션에 저장**된다.

최종 세션 상태 예:
```
session.events = [
   user:  "6면체 주사위 굴리고 소수인지 알려줘",
   model: [ text("/*PLANNING*/...", thought=True), function_call(roll_die) ],
   user:  [ function_response(roll_die → 4) ],
   model: [ text("/*REASONING*/.../*FINAL_ANSWER*/", thought=True), text("\n4가 나왔고, 소수가 아닙니다.") ],  # ← 최종
]
```

## 6. 사용자가 실제로 받는 것

- 최종 이벤트 안에서 **평문 파트(②)만 답변으로 노출**되고, 추론 파트(①)는 `thought=True`라 UI에서 접힘.
- 노출되는 답변에는 `/*FINAL_ANSWER*/` 태그가 **없음**(②는 태그 뒤쪽이므로).

## 7. 다음 사용자 턴으로의 연속성

이후 사용자가 새 메시지를 보내면, 다음 invocation이 세션 전체 이벤트로 contents를 재조립한다:
- 최종 이벤트는 평문 파트(②)가 visible이라 **이벤트 포함**됨 → deepcopy로 추론 파트(①)도 함께 실림.
- 직후 `_remove_thought_from_request`가 thought 언마킹 → **①추론 + ②최종답변이 모두 평문으로 모델에 재노출**.
→ 모델은 이전 대화의 추론 과정과 답변을 온전히 참조하며 다음 턴을 이어간다.

## 8. 엣지 케이스

- 모델이 `/*FINAL_ANSWER*/`를 **텍스트 맨 끝**에 두어 뒤쪽 내용이 비면 → ②가 `''`이라 `if final_answer_text:`가 거짓 → **최종답변 파트가 생성되지 않음**. 이벤트는 추론(thought)만 남아 사용자에겐 **빈 답변**처럼 보이고(접힘), `is_final_response`는 여전히 `True`라 루프는 정상 종료. (다음 턴 contents 재조립 시엔 이 이벤트가 all-thought → `_contains_empty_content`로 제외될 수 있음.)

## 9. 1사이클 종합

```
사용자 요청 → [스텝1] 계획+함수콜 → 툴 실행 → [스텝2] 추론+FINAL_ANSWER
   → split(추론=thought / 답변=평문) → is_final_response=True → 루프 break
   → runner가 최종 이벤트 세션 저장 → 사용자에게 평문 답변 노출
```
- 변하는 것: **contents 누적**(단계 3) + 마지막에 **답변 파트 분리**.
- 안 변하는 것: **system_instruction(PlanReAct 지침)**, 그리고 이미 쌓인 **태그 블록들의 내용**(append-only, 동결).
