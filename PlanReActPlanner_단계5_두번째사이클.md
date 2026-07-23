# [단계 5] 두 번째 사이클 — 다음 사용자 턴이 시작되면

> 대상: ADK Python **v1.26.0**
> 용어: 여기서 "1사이클" = 사용자 메시지 1건에 대한 **하나의 invocation**(내부적으로 여러 LLM 스텝을 돌 수 있음). "두 번째 사이클" = **두 번째 사용자 메시지 → 새 invocation**.
> 코드:
> - 메시지 저장: `src/google/adk/runners.py`의 `_append_new_message_to_session()` (L856)
> - 에이전트 선택: `runners.py`의 `_find_agent_to_run()` (L1035)
> - contents 재조립: `src/google/adk/flows/llm_flows/contents.py:59~66` (`invocation_context.session.events` **전체** 사용)
> - thought 언마킹: `_nl_planning.py:125` (`_remove_thought_from_request`)

## 1. 핵심 결론

두 번째 사이클은 **1사이클과 완전히 동일한 파이프라인**을 다시 탄다. 다른 점은 단 하나 — **contents가 "1사이클의 모든 이벤트 + 새 사용자 메시지"로 훨씬 길어진 상태에서 시작**한다는 것. system_instruction(PlanReAct 지침 포함)은 여전히 **동일하게 재부착**되고, 1사이클에서 쌓인 태그 블록들은 **그대로 동결된 채** 다시 실린다.

## 2. 사이클 시작 시퀀스

```
[사용자 2번째 메시지]  runner.run_async(new_message="그럼 한 번 더 굴려줘")
   │
   ▼ runners.py: _append_new_message_to_session
   │   · 새 user 이벤트 저장 (새 invocation_id 부여)
   │   session.events = [ ...1사이클 전체..., user: "그럼 한 번 더 굴려줘" ]
   │
   ▼ _find_agent_to_run(session, root_agent)   (runners.py:1035)
   │   · 마지막이 function_response면 → 그 함수콜을 낸 agent
   │   · 아니면 마지막으로 응답한 (transfer 가능한) agent, 최종적으로 root agent
   │   · 단일 에이전트면 → root agent
   │
   ▼ agent.run_async(ctx)  →  BaseLlmFlow.run_async  →  _run_one_step_async
       llm_request = LlmRequest()   ← 또 새 객체 (매 스텝/사이클마다 새로)
```

> 참고: 각 `run_async(new_message=...)` 호출은 **새 invocation_id**를 만든다. 하지만 `contents` 프로세서는 `include_contents='default'`일 때 **세션 전체 이벤트**(`session.events`)를 쓰므로, 이전 사이클 내용이 전부 포함된다.

## 3. 2사이클 첫 스텝에서 모델이 보는 프롬프트

```
── system_instruction ──────────────────────────────
[agent.instruction] + [identity] + [PlanReAct 지침]      ← 1사이클과 100% 동일 (정적)
── contents ────────────────────────────────────────
user:  "6면체 주사위 굴리고 소수인지 알려줘"                       ┐
model: "/*PLANNING*/...\n/*ACTION*/"  + function_call(roll_die)   │ 1사이클 잔재
user:  function_response(roll_die → 4)                            │ (전부 재노출)
model: "/*REASONING*/.../*FINAL_ANSWER*/"  +  "\n4는 소수 아님"     ┘
user:  "그럼 한 번 더 굴려줘"                                ← ★2사이클 신규 입력
```

핵심 변화:
1. **contents가 1사이클 전체 + 새 질의로 길어짐** (누적).
2. **1사이클의 모든 thought 파트(PLANNING/REASONING/FINAL_ANSWER 추론)가 `_remove_thought_from_request`로 언마킹되어 평문으로 재노출**된다. → 모델은 지난 사이클의 계획·추론·답변 궤적을 **온전히** 본다.
3. **태그 문자열은 제거되지 않는다.** `/*PLANNING*/`, `/*REASONING*/`, `/*FINAL_ANSWER*/`가 히스토리 텍스트에 문자 그대로 남아 모델에 다시 보인다(포맷 지속·강화).

## 4. contents 재조립 시 적용되는 추가 처리 (1사이클 잔재 정리)

`_get_contents`(contents.py:409)는 세션 전체를 훑으며 다음을 수행:
- **함수콜/응답 페어링 정리**: `_rearrange_events_for_latest_function_response`, `_rearrange_events_for_async_function_responses_in_history` — 1사이클의 `function_call ↔ function_response`가 인접·정합하도록 재배열.
- **invisible 이벤트 제외**: 모든 파트가 thought(또는 무의미)인 이벤트는 제외(`_contains_empty_content`). 1사이클의 계획/추론은 보통 function_call·최종답변과 같은 이벤트에 묶여 있어 **살아남음**.
- **함수콜 ID 제거**(`remove_client_function_call_id`), **branch 필터**, **compaction/rewind** 등도 동일 적용.

## 5. 이후 진행 = 1사이클과 동일

2사이클 첫 스텝 이후는 [단계 2~4]와 **똑같이** 흘러간다:
- 모델이 새 `/*PLANNING*/`(또는 필요 시 `/*REPLANNING*/`) + 함수콜 산출 → `process_planning_response`가 파싱·thought 마킹 →
- 함수콜 있으면 툴 실행 후 loop, 없고 `/*FINAL_ANSWER*/`면 split → `is_final_response=True` → 사이클 종료.

즉 **PlanReAct 관점에서 "새 사이클"은 히스토리가 더 긴 상태에서 도는 동일 루프**일 뿐, 특별한 분기 로직이 없다.

## 6. 사이클 간 불변/가변 요약

| 항목 | 1사이클 → 2사이클 |
|---|---|
| system_instruction (PlanReAct 지침 포함) | **불변** (매번 동일 재부착) |
| 1사이클의 태그 블록 내용 | **불변/동결** (append-only, 편집 없음) |
| 1사이클 thought 파트의 모델 노출 | **평문으로 재노출** (언마킹) |
| 태그 마커(`/*...*/`) | **유지** (스트립 안 함) |
| contents 길이 | **증가** (전체 히스토리 + 새 질의) |
| invocation_id | **새로 발급** |
| 처리 에이전트 | `_find_agent_to_run`로 선택(단일이면 root) |

## 7. 주의점

- 히스토리가 길어질수록 **1사이클의 계획/추론/태그가 계속 프롬프트에 누적**된다 → 토큰 증가. 장기 대화에서는 `compaction`(단계별 요약)·`include_contents='none'`(현재 턴만) 등으로 관리 가능(별도 프로세서).
- `include_contents='none'`이면 2사이클은 **1사이클 히스토리를 빼고** 현재 턴 컨텍스트만으로 시작한다(`_get_current_turn_contents`, contents.py:530).
- 2사이클에서 모델이 "지난 결과(예: 이전 주사위 값)"를 참조하는 것은 **평문 재노출된 1사이클 궤적** 덕분이다(1사이클의 function_response도 히스토리에 있음).
