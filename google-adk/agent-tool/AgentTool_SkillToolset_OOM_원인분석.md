# AgentTool + SkillToolset OOM 원인 분석 (ADK Python v1.26.0)

> **증상**: `LlmAgent`(+`SkillToolset`)를 `AgentTool`로 감싸 상위 에이전트에 붙이고, 이 AgentTool이 호출되는 순간 메모리가 계속 증가하다 OOM.
> **분석 방법**: 코드 정적 분석 (실행 재현 없이). v1.26.0 태그(`427a983b`) 소스 + upstream(main) 후속 수정 커밋 교차 검증.

---

## 0. TL;DR

**v1.26.0에는 "skill 도구 오류 → 모델 무한 재시도"를 끊는 안전장치가 하나도 없고, AgentTool은 그 루프를 밖에서 보이지 않게 감추면서 오류마저 삼켜 `''`를 반환한다.** 그 결과:

1. **자식 레벨 루프**: 자식 모델이 `load_skill`/`load_skill_resource`에서 오류(`SKILL_NOT_FOUND`/`RESOURCE_NOT_FOUND` 등)를 받으면, 매번 다른 이름/경로를 지어내며 **무한 재시도** (LLM↔tool 핑퐁). 이 루프는 upstream이 이후 커밋에서 명시적으로 인정하고 수정한 실제 버그다.
2. **부모 레벨 루프(증폭)**: 자식이 빈 응답/오류로 끝나면 v1.26.0 AgentTool은 **`''`를 반환** → 부모 모델이 빈 결과를 보고 **AgentTool을 다시 호출** → 매 호출마다 자식 전체 invocation이 통째로 반복.
3. **메모리 증폭기**: 매 스텝 전체 히스토리 deepcopy(`_get_contents`) + 요청 전체 JSON 직렬화(telemetry) + 스킬 본문이 function_response로 반복 내장 → 루프 반복에 따라 **초선형(super-linear)으로 메모리 소비**.
4. 이 모든 것이 **AgentTool 내부에서 조용히** 일어난다(자식 이벤트가 부모 스트림에 노출되지 않음) → 사용자에겐 "호출 순간 멈춘 채 메모리만 폭증"으로 보인다.

---

## 1. 원인 사슬 (코드 근거)

### 1-1. 자식 flow는 함수콜이 나오는 한 무한히 돈다

`base_llm_flow.py:749` — 종료 조건은 오직 `is_final_response()`(함수콜/응답 없는 이벤트) 뿐:

```python
while True:
    ...
    if not last_event or last_event.is_final_response() or last_event.partial:
        break
```

모델이 매 스텝 함수콜을 내면 루프는 끝나지 않는다. 유일한 상한은 `RunConfig.max_llm_calls`(§4).

### 1-2. v1.26.0 SkillToolset에는 재시도 차단 장치가 전무

`tools/skill_toolset.py` (v1.26.0):

- `LoadSkillTool` → 스킬 없으면 `{"error": ..., "error_code": "SKILL_NOT_FOUND"}` **그냥 반환**.
- `LoadSkillResourceTool` → 리소스 없으면 `RESOURCE_NOT_FOUND` / 경로 형식 오류면 `INVALID_RESOURCE_PATH` **그냥 반환**.
- `DEFAULT_SKILL_SYSTEM_INSTRUCTION`(3개 규칙뿐)에는 **"오류 시 재시도하지 말라"는 규칙도, "load 후 턴을 끝내지 말라"는 규칙도 없음**.

→ 모델이 오류 dict를 보면 "다른 이름/경로로 다시 시도"하는 것이 자연스러운 행동이고, **매번 다른 인자를 지어내므로 동일-호출 dedup도 불가능**. LLM↔tool 핑퐁이 계속된다.

### 1-3. upstream이 이 루프를 실제 버그로 인정하고 수정 (전부 v1.26.0 미포함)

| 커밋 (main) | 내용 | v1.26.0 포함 여부 |
|---|---|---|
| `bc45ee67` (#5652) | **"terminate infinite retry loop in LoadSkillResourceTool on RESOURCE_NOT_FOUND"** — invocation당 실패 카운터(`temp:_adk_skill_resource_not_found_count_*`)를 두고 2회째부터 `RESOURCE_NOT_FOUND_FATAL` 반환. 커밋 본문: *"the guard fires even when the LLM hallucinates a different path on each retry"* | ❌ 미포함 |
| `89d9bda7` | 동일 취지 — `RunSkillScriptTool` SCRIPT_NOT_FOUND 무한 재시도 종료 | ❌ 미포함 |
| `7c792309` (#6225) | **"prevent empty responses after load_skill"** — Gemini가 `load_skill` 직후 **빈 응답**으로 턴을 끝내는 비율이 유의미하게 높음(프로덕션 관측) → "load는 턴 종료가 아니다" 규칙 추가 | ❌ 미포함 |
| `e737f229` | **"surface sub-agent RPC errors from AgentTool"** — v1.26.0 AgentTool은 자식 실패 시 error_message를 버리고 **`''` 반환** → 수정에서 error_message 폴백 추가 | ❌ 미포함 |
| `b983fcf9` | AgentTool + skip_summarization 시 텍스트 출력 보장 (빈 출력 문제) | ❌ 미포함 |

`git merge-base --is-ancestor`로 확인: 위 커밋 모두 v1.26.0 조상이 아니며, v1.26.0 소스 직접 열람으로도 가드 부재 확인.

### 1-4. AgentTool이 문제를 증폭·은폐하는 방식

`tools/agent_tool.py: run_async` (v1.26.0):

```python
async for event in runner.run_async(...):   # 자식 전체 invocation을 여기서 소비
    ...
if last_content is None or last_content.parts is None:
    return ''                                # ★ 오류/빈 결과 → 빈 문자열
merged_text = '\n'.join(p.text for p in last_content.parts if p.text and not p.thought)
```

1. **은폐**: 자식 이벤트는 부모 이벤트 스트림으로 re-yield되지 않는다. 자식이 수백 스텝을 돌아도 밖에서는 **아무것도 안 보인다** — "호출 순간 멈춤 + 메모리 증가"로 관측되는 이유.
2. **중단 불가**: 도구 호출은 자식이 스스로 끝나야 반환된다. 스트리밍 UI에서 사람이 개입해 멈출 기회가 없다.
3. **오류 삼킴 → 부모 재시도 루프**: 자식이 (a) 빈 응답으로 끝나거나(#6225, load_skill 후 빈 응답은 관측된 고빈도 패턴) (b) 예외 없이 error_message 이벤트로 끝나면, AgentTool은 **`''`를 반환**. 부모 모델은 빈 도구 결과를 받고 → **같은 AgentTool을 다시 호출**(모델의 자연스러운 재시도) → **자식 invocation 전체가 반복 생성**. 부모 1 invocation 안에서 `부모 max_llm_calls × 자식 invocation` 만큼 곱셈적으로 증가.
   - 참고: 자식이 `LlmCallsLimitExceededError` 등 **예외**로 죽으면 부모까지 전파되어 크래시(OOM 아님). OOM 시나리오는 예외가 아니라 **빈/무의미 결과로 "정상 종료"를 반복**하는 경로다.

### 1-5. 메모리 증폭기 (루프 1회당 비용이 히스토리 길이에 비례)

루프가 N스텝 진행됐을 때, N+1번째 스텝은:

| 증폭기 | 코드 | 비용 |
|---|---|---|
| 전체 히스토리 deepcopy | `contents.py:522` `copy.deepcopy(event.content)` — **매 스텝, 모든 이벤트** | O(전체 히스토리) |
| 요청 전체 JSON 직렬화 | `telemetry/tracing.py:299` `_safe_json_serialize(_build_llm_request_for_trace(llm_request))` — **LLM 호출마다**, OTel 미설정이어도 인자는 항상 평가됨 | O(전체 요청) |
| 스킬 본문 반복 내장 | `load_skill` 성공 시 **SKILL.md 전문**(instructions)이 function_response 이벤트로 세션에 저장 — 루프 중 반복 load되면 대형 페이로드가 계속 쌓임 | O(스킬 크기 × load 횟수) |
| 세션 이벤트 누적 | 자식 `InMemorySessionService`에 매 스텝 이벤트 append | 단조 증가 |

→ 총 비용은 스텝 수에 대해 **누적 O(N²)** 성격. 스킬 리소스가 크면(참조 문서 동봉이 일반적) 수백 스텝 내에 GB 단위 도달이 현실적이다. 부모 레벨 재시도까지 겹치면 부모 세션에도 function_call/response가 계속 쌓인다.

---

## 2. 관측 증상과의 대응

| 관측 | 설명 |
|---|---|
| "AgentTool이 **호출되는 순간**부터" | 자식의 첫 행동이 skill 도구 호출(시스템 지침이 그렇게 유도). 스킬 이름/경로 불일치 시 **스텝 2부터 즉시 루프 시작** |
| "메모리를 **무수히** 잡아먹음" | §1-5 증폭기: 스텝당 비용이 히스토리에 비례해 증가 + 부모 재시도 곱셈 |
| 응답/로그가 안 보임 | §1-4 은폐: 자식 이벤트 미노출 |
| 단독 실행(에이전트 직접)에선 문제 인지 어려움/없음 | 단독 실행 시 이벤트 스트림에 재시도 스팸이 **보여서** 사람이 중단 가능. AgentTool 안에서는 불가시 |

---

## 3. 배제한 가설들 (정적 검증 결과)

| 가설 | 결론 |
|---|---|
| `Runner.__init__` / `_infer_agent_origin` / `App` 래핑에서 폭주 | ❌ 유한 작업만 수행 (deep copy·클론 없음) |
| `AgentTool._get_declaration` 스키마 재귀 | ❌ input_schema 미사용 시 `request: string` 고정. (순환 스키마 사용 시엔 별개 이슈 — upstream `7de5bc54`가 순환 감지 추가) |
| parent_agent 순환으로 `root_agent`/`canonical_model` 무한 순회 | ❌ AgentTool은 parent를 설정하지 않음. 또한 해당 루프는 할당이 없어 OOM이 아닌 행(hang) 유형 |
| `runner.close()` 미호출(예외 경로) 리소스 누수 | ❌ 만성 누수 유형 — 단발 호출 즉시 OOM과 불일치 (별도 리스크로는 유효) |
| 툴셋 선언 중복 등록/`get_tools_with_prefix` 래핑 중첩 | ❌ prefix 미사용 시 원본 반환. 스텝당 1회 |
| `InMemorySessionService.get_session`의 세션 deepcopy | ❌ invocation당 1회 (다만 §1-5의 누적과 결합하면 비용 가중 요인) |

---

## 4. 왜 `max_llm_calls=500` 상한이 못 막았나

`RunConfig.max_llm_calls` 기본 500, `invocation_context.py`의 `increment_and_enforce_llm_calls_limit`가 초과 시 예외를 던진다. 그러나:

1. **상한은 invocation 단위**다. AgentTool 호출마다 자식은 **새 InvocationContext**(카운터 0부터) → 부모가 재시도할 때마다 자식 500이 리셋. 이론상 부모 500 × 자식 500 = **최대 25만 LLM 스텝**이 "정상 동작".
2. 자식이 상한에 걸려도 **§1-5의 누적 메모리는 그 전에** 위험 수위에 도달할 수 있다(스킬 페이로드가 크면 수백 스텝이면 충분).
3. `max_llm_calls<=0`으로 운용 중이었다면 문자 그대로 무한.

---

## 5. 격리 환경 실측 검증 결과 ✅ (재현 성공)

**환경**: PyPI `google-adk==1.26.0`, Python 3.12, mock LLM(네트워크 없음), RSS 워치독 700MB(macOS에서 RLIMIT_AS가 무시되므로 워치독 방식) + 스텝 백스톱 + alarm. 스크립트: `$CLAUDE_JOB_DIR/tmp/repro_oom.py` (시나리오 s1~s4).

| # | 시나리오 | 결과 |
|---|---|---|
| **s1** | 자식이 skill 리소스 오류 시 매번 다른 경로로 재시도 (작은 페이로드) | 자식 **500 LLM 스텝 + 500 도구 재시도** 루프 확인. 그동안 **부모에게 보인 이벤트 = 1개**(부모 자신의 함수콜)뿐 — 완전 침묵 후 `LlmCallsLimitExceededError`로 크래시 |
| **s2** | 동일 루프 + 스킬 본문 8MB (현실적 크기) | **RSS 116MB → 707MB, 152스텝(~3.4분)에 워치독 차단**. 자식이 보는 요청 페이로드 600MB까지 증가. 스텝 처리시간도 2차 함수적 증가(스텝25=2.4s→스텝150=198s). **max_llm_calls=500 상한 도달 전에 메모리가 먼저 터짐 = OOM 재현** |
| **s3** | 자식이 load_skill 후 빈 응답으로 종료 (#6225 패턴) | AgentTool이 **`''` 반환** → 부모 모델이 빈 결과 보고 **AgentTool 재호출 ×3** (`child_invocations=3`). 재호출마다 자식 상한 카운터 리셋 — 곱셈 구조 실증 |
| **s4** | **대조군**: 동일 자식을 sub-agent(transfer)로 실행 | 자식의 fc/fr 이벤트 **1,000개가 호출자에게 실시간 스트리밍**(author=child_agent로 전부 가시). 상한도 **부모와 공유**되어 총합 500(자식 499+부모 1)에서 정지 |

핵심 수치 재해석: s2에서 8MB 스킬 하나가 3분여 만에 ~700MB. 스킬이 더 크거나 여러 개면, 그리고 s3의 부모 재호출이 겹치면 무한정 — 관측된 "무수히 잡아먹으면서 OOM"과 정확히 일치.

## 5-1. 사람이 눈으로 확인하는 방법 (probe)

**자식 에이전트 콜백에 probe를 심는 방식이 정답이다** — 자식 이벤트가 부모 스트림에 노출되지 않으므로, 자식 invocation **내부**에서 실행되는 훅이 유일한 관측 창구다. s1~s4 실측에 사용한 probe:

```python
def probe_before_tool(*, tool, args, tool_context):     # 도구 재시도 스팸 관측
    print(f"[PROBE] {tool.name}({args})")
    return None

def probe_before_model(*, callback_context, llm_request):  # 스텝 수·페이로드 크기 관측
    print(f"[PROBE] contents={len(llm_request.contents)}건")
    return None

child = Agent(..., before_tool_callback=probe_before_tool,
              before_model_callback=probe_before_model)
```

- 이 콜백들은 AgentTool이 만든 자식 Runner 안에서도 정상 호출된다(실측 확인). `before_tool`이 같은 도구를 다른 인자로 연타하는 패턴, `before_model`의 contents 건수 증가가 즉시 보인다.
- **자식 코드를 못 건드릴 때**: `include_plugins=True`(기본)로 부모 plugin이 자식 Runner에 상속되므로, `before_tool_callback`/`before_model_callback`을 가진 **plugin**을 부모 Runner에 달면 자식 내부까지 관측된다.
- 보조 수단: `logging.getLogger('google_adk').setLevel(logging.DEBUG)` (flow가 요청/이벤트를 로깅), OTel 트레이싱 구성 시 `execute_tool`/`call_llm` span.

## 5-2. 왜 sub-agent일 때는 문제가 없고 AgentTool일 때만 터지나 (실측 근거)

같은 루프 성향이라도 **구조적 차이 3가지**가 결과를 가른다:

| | sub-agent (transfer) | AgentTool |
|---|---|---|
| ① 가시성 | 자식 이벤트가 부모 스트림으로 **전부 실시간 노출** (s4: 1,000개 스트리밍) → 사람이/UI가 재시도 스팸을 보고 즉시 개입 가능 | 자식 이벤트 **완전 은폐** (s1: 이벤트 1개 후 침묵) → 개입 불가 |
| ② LLM 호출 상한 | **같은 InvocationContext 공유** → `max_llm_calls` 카운터가 부모+자식 합산, **전체 500에서 확실히 정지** (s4: 499+1) | AgentTool 호출마다 **새 Runner + 새 InvocationContext** → 카운터 리셋. 부모 재호출 시 500×N으로 곱셈 (s3 실증) |
| ③ 오류/빈 결과의 행방 | 자식의 마지막 이벤트가 그대로 대화 결과가 됨 — "빈 도구 결과 → 재호출" 계약 자체가 없음 | 자식 실패/빈 응답이 **`''` 도구 결과로 변환** → 부모 모델의 자연스러운 반응이 재호출 (s3 실증) |

즉 sub-agent 모드는 같은 결함(무한 재시도 성향)이 있어도 **보이고, 합산 상한에 걸리고, 재호출 루프가 없어** 사고로 이어지지 않는다. AgentTool 모드는 세 안전판이 전부 사라진다.

---

## 6. upstream 현황 — 공식 인지 여부와 수정 버전

**그렇다. 세 결함 모두 google/adk-python GitHub에 공식적으로 기록·수정된 내용이다:**

| 결함 | upstream 근거 | 수정 포함 릴리스 (태그 검증) |
|---|---|---|
| skill 리소스 오류 무한 재시도 | **Issue #5652 / PR #5651** (`bc45ee67`) — "terminate infinite retry loop in LoadSkillResourceTool on RESOURCE_NOT_FOUND". 커밋 본문에 "LLM이 매 재시도마다 다른 경로를 hallucinate해도 가드가 작동하도록 invocation당 실패 카운터" 명시 | **1.x 전체 ❌** (v1.27~v1.36.2 표본검사) · v2.0/2.1 ❌ · **v2.2.0부터 ✅** |
| load_skill 후 빈 응답 | **Issue #6225 / PR #6226** (`7c792309`) — Verizon 프로덕션(ADK+Gemini)에서 빈 응답 스파이크 관측 후 지침 규칙 추가 | **모든 릴리스 ❌ (1.x·2.x 포함)** · **main만 ✅** |
| AgentTool이 자식 오류를 `''`로 삼킴 | `e737f229` — "surface sub-agent RPC errors from AgentTool" (자식 error_message 폴백 반환) | **모든 릴리스 ❌ (1.x·2.x 포함)** · **main만 ✅** |

- **1.x 라인은 v1.36.2(최신 1.x)까지 존재하지만 세 수정 모두 백포트되지 않았다.** v1.36.2의 `LoadSkillResourceTool`은 v1.26.0과 동일하게 무가드로 `RESOURCE_NOT_FOUND`를 반환한다(어떤 형태의 재시도 차단 코드도 없음 — retry/fatal/fail_count 계열 문자열 전무 확인). **즉 1.x에 머무는 한 이 문제는 버전 업그레이드로 해결되지 않는다.**
- 검증 방법: `git show <tag>:src/google/adk/tools/skill_toolset.py`로 v1.27.0 / v1.30.0 / v1.33.0 / v1.36.2 / v2.1.0~v2.5.0 각각 확인.
- 현재(2026-07-30) 최신 릴리스는 **v2.5.0**: 핵심 트리거인 재시도 가드는 포함(최초 도입 v2.2.0), 나머지 둘은 main에만 있음.
- v2.5.0 AgentTool에는 `mode='single_turn'` 관련 개선도 들어가 있음(자식을 1턴으로 제한하는 방향 — 이 문제의 폭주 반경을 줄이는 데 유효).
- 참고: 1.26.0→2.x는 메이저 업그레이드이므로 breaking change 검토 필요.

## 7. v1.26.0에서의 완화책 (업그레이드 전)

1.26.x에 머무는 동안은 **백포트 성격의 방어**가 필요:

1. **자식 에이전트에 skill-오류 차단 콜백** (bc45ee67 백포트, 가장 효과적):
   ```python
   def stop_skill_retry(tool, args, tool_context):  # before_tool_callback
       key = f"temp:skill_fail_{tool_context.invocation_id}"
       if tool.name in ("load_skill", "load_skill_resource", "list_skills"):
           n = int(tool_context.state.get(key) or 0)
           if n >= 2:
               return {"error": "Skill lookup failed repeatedly. STOP retrying and report to the user.",
                       "error_code": "SKILL_LOOKUP_FATAL"}
       return None
   ```
   + `after_tool_callback`에서 결과에 `error`가 있으면 카운터 증가.
2. **프롬프트 가드 백포트** — 자식 instruction에 upstream 규칙 추가:
   - "If a skill tool returns any error, do NOT retry. Report the error and stop." (bc45ee67)
   - "Loading a skill does NOT complete your turn; continue and produce a reply. Never end with an empty response." (7c792309)
3. **AgentTool 오류 가시화** — AgentTool 서브클래스에서 `run_async`를 감싸 `event.error_message` 추적, 빈 결과면 오류 문자열 반환 (e737f229 백포트). 부모의 빈-결과 재시도 루프를 차단.
4. **상한 강화** — 부모 `RunConfig(max_llm_calls=50)` 수준으로 축소, 부모 instruction에 "도구가 빈 결과/오류를 반환하면 재호출하지 말고 사용자에게 보고" 명시.
5. 스킬 정합성 사전 점검 — `SkillToolset`에 넣는 skill name(kebab-case)과 리소스 경로가 실제 존재하는지 로드 시 검증(불일치가 루프의 트리거).

---

## 8. 코드 참조 요약

| 항목 | 위치 (v1.26.0) |
|---|---|
| flow 무한 루프 구조 | `flows/llm_flows/base_llm_flow.py:749` |
| skill 도구 오류 반환(가드 없음) | `tools/skill_toolset.py` `LoadSkillTool`/`LoadSkillResourceTool.run_async` |
| AgentTool 자식 소비 + `''` 반환 | `tools/agent_tool.py:run_async` |
| 히스토리 deepcopy | `flows/llm_flows/contents.py:522` |
| 요청 직렬화 | `telemetry/tracing.py:299` |
| LLM 호출 상한 | `agents/run_config.py:309`, `agents/invocation_context.py` |
| upstream 수정 | main: `bc45ee67`, `89d9bda7`, `7c792309`, `e737f229`, `b983fcf9` |
