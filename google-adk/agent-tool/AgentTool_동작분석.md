# AgentTool이 LlmAgent를 감쌌을 때의 동작 — 일반 agent 흐름과의 차이점

> 대상: ADK Python **v1.26.0**
> 코드: `src/google/adk/tools/agent_tool.py`, `src/google/adk/tools/_forwarding_artifact_service.py`
> 전제: 일반 `LlmAgent`의 실행 흐름(runner→flow→LLM→tool loop)은 이미 이해한 상태. 여기서는 **AgentTool로 감쌌을 때 달라지는 지점**만 집중.

---

## 0. 한 줄 요약

`AgentTool(agent=some_llm_agent)`은 **에이전트를 부모 LLM의 "함수(tool)"로 노출**하고, 호출되면 **별도의 격리된 Runner/세션에서 자식 에이전트를 처음부터 끝까지 완주**시킨 뒤 **마지막 응답 텍스트만 function_response로 부모에게 돌려준다.** 부모는 제어권을 넘기지 않고, 자식의 내부 진행은 부모 대화에 보이지 않는 **블랙박스**다.

---

## 1. 가장 중요한 대비: "sub-agent 전송" vs "AgentTool"

에이전트를 다른 에이전트에서 부르는 방법은 두 가지이고, 동작이 근본적으로 다르다.

| 구분 | **sub-agent 전송** (AutoFlow `transfer_to_agent`) | **AgentTool** (도구로 래핑) |
|---|---|---|
| 부모 LLM에 보이는 형태 | `transfer_to_agent(agent_name=...)` 함수콜 | **에이전트 이름의 일반 function call** |
| 실행 컨텍스트 | **같은** `invocation_context` / 같은 세션 | **새 `Runner` + 새 `InMemorySessionService`** (격리) |
| 대화 히스토리 | 부모 세션 이벤트 **공유** (자식이 이전 대화를 봄) | **격리** — 자식은 부모 대화 이력을 **못 봄** |
| 제어권 | 자식에게 **넘어감** (대화 주체가 바뀜) | 부모가 **계속 보유** (자식은 결과만 반환) |
| 자식 내부 이벤트 | 부모 이벤트 스트림/세션에 **그대로 노출** | 부모 세션에 **안 남음** (function_call+response만) |
| 입력 | 기존 대화를 그대로 인계 | **합성된 user 메시지**(request 문자열 또는 input_schema JSON) |
| 출력 | 자식 응답이 대화의 일부가 됨 | 자식 **마지막 응답 텍스트**가 tool 결과가 됨 |
| 요약 | 없음(직접) | 부모 LLM이 결과를 **한 번 더 요약**(단, `skip_summarization`로 생략 가능) |

→ **AgentTool은 "위임 후 결과 회수", 전송은 "대화 주체 교체"** 라고 이해하면 된다.

---

## 2. 부모 LLM에게 어떻게 노출되는가 — `_get_declaration()`

AgentTool은 `BaseTool`이므로 부모의 tool 목록에 **function declaration**으로 등록된다.

- `name` = **감싼 에이전트의 `agent.name`**
- `description` = **`agent.description`** (부모 LLM이 "이 도구가 뭘 하는지" 판단하는 근거 → description이 중요)
- **parameters**:
  - 감싼 에이전트에 `input_schema`가 있으면 → 그 Pydantic 스키마로 파라미터 생성
  - 없으면 → **`{ "request": string }` (required)** 단일 파라미터
- **response schema** (비-GEMINI_API variant 한정): `output_schema` 있으면 object, 없으면 string

```
# input_schema 없는 경우 부모가 보는 함수 시그니처
<agent.name>(request: string) -> string
```

> 참고: 감싼 것이 복합 에이전트면 `_get_input_schema`는 **첫 sub-agent**, `_get_output_schema`는 **마지막 sub-agent**에서 스키마를 재귀 추출한다. LlmAgent를 직접 감싸면 그 에이전트의 `input_schema`/`output_schema`를 그대로 사용.

---

## 3. 호출 시 실제 동작 — `run_async()` 단계별

부모 LLM이 이 도구를 함수콜하면 `functions.handle_function_calls_async` → `AgentTool.run_async`가 실행된다.

```
1) skip_summarization=True 이면
   tool_context.actions.skip_summarization = True   (부모의 추가 요약 LLM 호출 억제)

2) 입력 content 합성 (role='user')
   - input_schema 있음 → args를 스키마 검증 후 model_dump_json() 을 텍스트로
   - 없음            → args['request'] 문자열을 텍스트로

3) 새 Runner 생성 (★격리의 핵심)
   Runner(
     app_name        = 부모 app_name (없으면 agent.name),
     agent           = self.agent,                     # 감싼 LlmAgent
     artifact_service= ForwardingArtifactService(tool_context),  # 아티팩트는 부모로 전달
     session_service = InMemorySessionService(),        # ★새 격리 세션
     memory_service  = InMemoryMemoryService(),         # ★새 격리 메모리
     credential_service = 부모 credential_service,       # 공유
     plugins         = 부모 plugins (include_plugins=True 기본) or None,
   )

4) 부모 state 복사 → 자식 세션 초기 state
   state_dict = { k:v for parent.state  if not k.startswith('_adk') }  # 내부 상태 제외
   session = create_session(user_id=부모 user_id, state=state_dict)

5) 자식 에이전트 완전 실행 (자체 loop 포함)
   async for event in runner.run_async(new_message=합성 content):
       if event.actions.state_delta:
           tool_context.state.update(event.actions.state_delta)   # ★자식 state 변경을 부모로 역전파
       last_content = event.content                                # 마지막 content 추적

6) runner.close()   (MCP 세션 등 정리)

7) 반환값(tool_result) 생성
   merged_text = 마지막 content의 text 파트들 join   ← thought 파트(p.thought)는 제외!
   - output_schema 있음 → merged_text를 JSON 파싱·검증 후 dict 반환
   - 없음            → merged_text(문자열) 반환
```

이 반환값이 **function_response**로 부모 대화에 삽입되고, 부모 LLM이 이어서 처리한다.

---

## 4. 핵심 차이점 상세

### 4-1. 세션·히스토리 격리 (가장 큰 차이)
- 자식은 **새 `InMemorySessionService`**에서 실행 → **부모의 대화 이력을 전혀 보지 못한다.**
- 자식이 보는 `contents`는 **합성된 user 메시지 1건**뿐(+ 복사된 state). 부모가 `DatabaseSessionService`를 쓰든 상관없이 자식은 **휘발성 in-memory** 세션.
- 대조적으로 sub-agent 전송은 부모 세션 이벤트를 공유한다.

### 4-2. 제어권 & 이벤트 가시성
- 부모는 제어권을 유지. 자식 실행은 `run_async` **내부에서 소비**되며 부모 이벤트 스트림으로 **re-yield 되지 않는다.**
- 따라서 부모 세션에는 **function_call(부모) + function_response(자식 결과)** 만 남고, 자식의 계획/툴콜/중간 응답은 **안 보인다**(블랙박스).

### 4-3. 상태(state) — 복사 in / 델타 out (양방향)
- **호출 시작 시**: 부모 state(단, `_adk*` 내부키 제외)를 자식 세션에 **복사**해 넣음 → 자식 instruction의 `{state_var}` 주입 등에 사용 가능.
- **자식 실행 중**: 자식이 낸 `state_delta`를 **부모 `tool_context.state`로 즉시 전파** → 자식이 부모 state를 갱신할 수 있음.
- (테스트 `test_update_state`가 "read and change parent state"로 검증)

### 4-4. 자식이 보는 프롬프트
- 자식 LLM의 `system_instruction`은 **자식 에이전트 기준으로 새로 조립**된다: 자식의 `instruction` + `identity("You are an agent. Your internal name is \"<agent.name>\"")` + (자식에 planner 있으면 planner 지침) + (자식 tools/sub-agents) 등.
- `contents`는 **합성 user 메시지 하나**로 시작. 부모의 PlanReAct 궤적이나 이전 turn은 포함되지 않음.

### 4-5. 출력 처리 & 요약
- 반환 텍스트는 **마지막 content의 non-thought 텍스트**만 합쳐 만든다(자식이 PlanReAct 등으로 thought를 남겨도 결과에서 제외).
- 기본적으로 부모 LLM이 function_response를 받아 **한 번 더 요약/정리**한다. `skip_summarization=True`면 `actions.skip_summarization`이 서므로 function_response가 `is_final_response`로 처리되어 **추가 요약 없이** 그대로 쓰인다.

### 4-6. 아티팩트 / 자격증명 / 메모리 / 플러그인
- **아티팩트**: `ForwardingArtifactService`로 자식의 save/load가 **부모 tool_context로 포워딩** → 부모와 공유.
- **자격증명(credential_service)**: 부모 것 **공유**.
- **메모리**: 새 `InMemoryMemoryService` → **격리**(자식 `load_memory`가 부모 메모리 접근 못 함).
- **플러그인**: `include_plugins=True`(기본) 이면 부모 플러그인 상속, `False`면 격리.

---

## 5. 자식이 LlmAgent일 때의 요약 다이어그램

```
부모 에이전트 (loop 중)
  │  부모 LLM: function_call  <agent.name>(request="...")   ← AgentTool이 tool로 노출
  ▼
AgentTool.run_async
  ├ 입력 content 합성 (request / input_schema JSON)
  ├ 새 Runner + InMemorySession(격리) + 부모 state 복사
  ├ ┌─ 자식 LlmAgent 실행 (자체 flow/loop, 자체 LLM 호출, 자체 tools) ─┐  ← 부모엔 안 보임
  │ └  events → state_delta는 부모로 전파, last_content 추적          ┘
  ├ runner.close()
  └ 반환: last_content의 non-thought 텍스트 (output_schema면 dict)
  ▼
function_response 로 부모 대화에 삽입
  │  (skip_summarization=False면) 부모 LLM이 결과 요약/후속 진행
  ▼
부모 loop 계속
```

---

## 6. 언제 무엇을 쓰나

- **AgentTool**: 특정 하위 작업을 **격리된 전문가에게 위임하고 결과만 받아** 부모가 계속 진행할 때. (부모가 오케스트레이터로 남음, 자식 이력 노출 원치 않음, 병렬적 도구처럼 사용)
- **sub-agent 전송**: 대화 주체 자체를 **다른 에이전트로 넘겨** 그 에이전트가 이어서 사용자와 상호작용하게 할 때. (컨텍스트 공유 필요)

---

## 7. 코드 참조

| 관심사 | 위치 |
|---|---|
| tool 선언(부모 LLM 노출) | `agent_tool.py: AgentTool._get_declaration` |
| input/output schema 추출(재귀) | `agent_tool.py: _get_input_schema / _get_output_schema` |
| 실행·격리 Runner·state 전파·반환 | `agent_tool.py: AgentTool.run_async` |
| 아티팩트 포워딩 | `_forwarding_artifact_service.py: ForwardingArtifactService` |
| 설정(YAML) 로딩 | `agent_tool.py: from_config / AgentToolConfig` |
| 검증 테스트 | `tests/unittests/tools/test_agent_tool.py` (state/스키마/plugins/app_name/artifact) |
