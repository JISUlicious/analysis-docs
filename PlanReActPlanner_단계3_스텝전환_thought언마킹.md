# [단계 3] 스텝 전환 — 툴 실행 → contents 재조립 → thought 언마킹

> 대상: ADK Python **v1.26.0**
> 코드:
> - 툴 실행: `src/google/adk/flows/llm_flows/base_llm_flow.py:919, 1044` → `functions.handle_function_calls_async`
> - contents 재조립: `src/google/adk/flows/llm_flows/contents.py`의 `_get_contents()` (L409)
> - thought 언마킹: `src/google/adk/flows/llm_flows/_nl_planning.py:59, 125` (`_remove_thought_from_request`)

## 1. 이 단계에서 벌어지는 일 (한 줄 요약)

스텝 1의 모델 응답 이벤트(계획 텍스트는 `thought=True`로 저장됨) + 툴 응답 이벤트가 세션에 쌓인 뒤, **다음 스텝에서 프롬프트가 세션 이벤트로부터 통째로 재조립**되고, 이때 **저장돼 있던 `thought=True` 마커가 전부 제거되어 계획/추론이 모델에게 평문으로 다시 노출**된다.

## 2. 전환 시퀀스

```
[스텝1 응답 후처리 결과]
  model 이벤트 = [ text("/*PLANNING*/...", thought=True), function_call(roll_die, sides=6) ]
      │
      ▼  function_call 존재 → 툴 실행 분기 (base_llm_flow.py:919)
[툴 실행]  functions.handle_function_calls_async → roll_die(6) = 4
      │
      ▼  function_response 이벤트 생성 & 세션 저장
  session.events = [
     user:  "6면체 주사위 굴리고 소수인지 알려줘",
     model: [ text("/*PLANNING*/...", thought=True), function_call(roll_die) ],
     user:  [ function_response(roll_die → 4) ],
  ]
      │
      ▼  루프 재진입: run_async while → _run_one_step_async → llm_request = LlmRequest()  (새 객체!)
[스텝2 전처리]
  (1) contents 프로세서: _get_contents 로 위 세션 이벤트 → llm_request.contents 재조립
  (2) _nl_planning 프로세서: system에 지침 재부착 + _remove_thought_from_request (★thought=None)
```

## 3. contents 재조립 시 `thought` 파트의 운명 (핵심)

`contents.py`의 `_get_contents`(L409) 동작:

1. **이벤트 포함 여부** — `_should_include_event_in_context` → `_contains_empty_content`(L261). 이벤트의 **모든** 파트가 invisible일 때만 이벤트가 제외됨.
   - `_is_part_invisible`(L232): `function_call`/`function_response`는 **절대 invisible 아님**. 그래서 `[thought 텍스트 + function_call]` 이벤트는 함수콜 덕분에 **포함**된다.
2. **파트 보존** — 포함된 이벤트는 `content = copy.deepcopy(event.content)`(L522)로 복사됨. 이때 **thought 텍스트 파트도 같이 복사되어** contents에 들어간다(아직 `thought=True` 상태).

→ 즉 계획 텍스트는 "같은 이벤트 안의 function_call에 업혀서" 다음 컨텍스트까지 살아남는다.

> ⚠️ 만약 모델이 **함수콜/최종답 없이 순수 계획 텍스트만** 한 이벤트로 뱉었다면, 그 이벤트는 모든 파트가 thought(invisible)라서 `_contains_empty_content=True` → **다음 컨텍스트에서 통째로 제외**된다. (게다가 그 응답은 function_call이 없으니 `is_final_response=True`가 되어 루프도 조기 종료될 수 있음 — 구조적 엣지 케이스.)

## 4. thought 언마킹 (`_remove_thought_from_request`, `_nl_planning.py:125`)

```python
def _remove_thought_from_request(llm_request):
    for content in llm_request.contents:
        for part in content.parts or []:
            part.thought = None
```

- contents 재조립 **직후**에 실행되어, 방금 실려온 `thought=True` 파트들을 전부 `None`으로 되돌린다.
- **프로세서 순서 이유**: `single_flow.py`에서 `_nl_planning`은 반드시 `contents` **뒤**에 있어야 한다. contents가 `llm_request.contents`를 통째로 재할당하므로, 언마킹이 그 뒤여야 효과가 남는다(코드 주석에 명시).
- 검증: 단위테스트 `test_remove_thought_from_request_with_thoughts`.

## 5. 스텝2에서 모델이 실제로 보는 프롬프트

```
── system_instruction ──────────────────────────────
(스텝1과 완전히 동일: [agent지침] + [identity] + [PlanReAct 지침])   ← 정적, 안 변함
── contents ────────────────────────────────────────
user:  "6면체 주사위 굴리고 소수인지 알려줘"
model: "/*PLANNING*/\n1. roll_die(6) 호출\n2. 소수 판정\n/*ACTION*/"   ← ★신규 (thought=None 으로 언마킹됨 → 평문!)
       function_call: roll_die(sides=6)                                ← ★신규
user:  function_response: roll_die → 4                                 ← ★신규
```

### 스텝1 → 스텝2 변화 요약

| 구성요소 | 변화 |
|---|---|
| system_instruction | **불변** (같은 지침 재부착) |
| contents: user 질의 | 유지 |
| contents: model(계획+함수콜) | **추가** — 계획 텍스트가 `thought=True`→`None`으로 **평문화되어 모델에 노출** |
| contents: 툴 응답 | **추가** |

핵심: **"프롬프트가 변한다"의 실체는 system 지침이 아니라 `contents`의 누적**이며, 여기에 더해 **이전 사고(계획)가 평문으로 되살아나 모델이 자기 궤적을 계속 참조**한다는 점이다.

## 6. "사용자 화면 vs 모델" 이중성

- **사용자/세션**: 계획·추론은 `thought=True`로 저장 → UI에서 접힘, `is_final_response` 판정에서 최종답변 취급 안 됨.
- **모델(다음 스텝)**: `_remove_thought_from_request`로 평문화 → 모델은 전체 계획/추론 궤적을 계속 봄.

→ **"사용자에겐 접히지만, 모델은 계속 본다."** 이것이 thinking 미지원 모델에서도 ReAct 궤적을 유지하는 PlanReAct의 메커니즘.
