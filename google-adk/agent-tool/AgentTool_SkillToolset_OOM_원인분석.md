# AgentTool + SkillToolset 폭주 루프 → OOM 원인 분석 (ADK Python v1.26.0)

> **증상**: `LlmAgent`(+`SkillToolset`)를 `AgentTool`로 감싸 상위 에이전트에 붙이고 호출하는 순간,
> 루프와 함께 메모리가 계속 증가하다 OOM.
> **분석 대상**: v1.26.0 태그(`427a983b`) = PyPI `google-adk==1.26.0` (동일성 확인).
> 본 문서는 **코드에서 확인된 사실, 격리 재현 실측, upstream 공식 기록, 관측 환경 사실**만 기술한다.

---

## 1. 결론 요약

v1.26.0에는 **skill 도구 오류에 대한 재시도 차단 장치가 없고**(코드 사실, upstream이 후속 버전에서 수정),
모델이 오류를 받으면 재시도하는 한 **flow 루프는 무한히 돈다**(코드 사실). `AgentTool`은 이 루프를
**외부에서 보이지 않게 만들고, 호출 횟수 상한을 리셋시키며, 실패를 빈 문자열로 바꿔 부모의 재호출을
유발**한다(코드 사실 + 실측). 동일 조건의 sub-agent 구성에는 이 세 문제가 없다(실측).
루프가 무한하고 스텝당 비용이 0이 아니므로, 종료 조건에 도달하기 전에 프로세스 메모리가 고갈될 수 있다.

## 2. 관측 환경 (보고된 사실)

- ADK Python **1.26.0**
- 모델: **GLM5.1 FP8, 로컬 SGLang 서빙** → ADK의 **LiteLlm 래퍼 경로** (Gemini 경로 아님)
- 스킬: 소형 (Anthropic 가이드라인 준수, 토큰 절약 목적)
- 세션 state: **첫 요청 시점 비어 있음**
- 동일 자식을 **sub-agent로 붙였을 때는 1Gi k8s 리소스에서 정상 동작**
- AgentTool로 전환 후 **첫 요청에서 루프와 함께 즉시 OOM**

## 3. 코드에서 확인된 결함 사슬 (v1.26.0)

### 3-1. flow는 함수콜이 나오는 한 무한 루프

`flows/llm_flows/base_llm_flow.py:749`:

```python
while True:
    ...
    if not last_event or last_event.is_final_response() or last_event.partial:
        break
```

종료 조건은 "함수콜/함수응답이 없는 이벤트"뿐. 모델이 매 스텝 함수콜을 내면 루프는 끝나지 않는다.
유일한 상한은 `RunConfig.max_llm_calls`(기본 500, **invocation 단위**).

### 3-2. skill 도구 오류는 예외가 아니라 정상 응답이며, 재시도 차단 장치가 없다

`tools/skill_toolset.py` (v1.26.0):

- `LoadSkillTool` → 스킬 없으면 `{"error": ..., "error_code": "SKILL_NOT_FOUND"}` 반환
- `LoadSkillResourceTool` → `RESOURCE_NOT_FOUND` / `INVALID_RESOURCE_PATH` 등 반환
- 오류 dict는 **정상적인 function_response 이벤트로 세션에 쌓이고 flow는 계속 진행**된다
- 실패 카운터·재시도 차단 코드 없음. 시스템 지침(3개 규칙)에도 "오류 시 재시도 금지",
  "load는 턴 종료 아님" 규칙 없음
- 모델이 매번 다른 인자(경로/이름)를 지어내며 재시도하면 동일-호출 dedup도 불가능

→ upstream이 이 재시도 루프를 실제 버그로 인정하고 수정했다(§5).

### 3-3. AgentTool 구조가 제거하는 안전판 — sub-agent와의 차이

`tools/agent_tool.py: run_async` (v1.26.0) 기준 코드 사실:

| # | 코드 사실 | 결과 |
|---|---|---|
| ① | 자식 이벤트를 부모 스트림에 re-yield하지 않고 내부에서 소비 | 루프가 **완전히 은폐**됨. 외부에서 관측·개입 불가 |
| ② | 호출마다 새 `Runner` + 새 `InvocationContext` 생성 | `max_llm_calls` 카운터가 **호출마다 리셋**. 부모가 재호출하면 상한이 곱셈으로 늘어남 |
| ③ | 자식의 마지막 content에 텍스트 파트가 없으면 `return ''` | 자식이 오류/빈 응답으로 끝나면 부모 모델은 **빈 도구 결과**를 받고, 자연스러운 반응으로 **재호출** |
| ④ | 새 `InMemorySessionService`에 **합성 request 문자열 1건**으로 자식 시작 | 자식은 부모 대화·사용자 원 질문의 원문 컨텍스트를 **보지 못한다** (sub-agent는 세션 전체 공유) |

sub-agent(transfer) 구성은 ①~④가 모두 반대다: 이벤트가 부모 스트림에 그대로 노출되고,
같은 `InvocationContext`를 공유해 상한이 **부모+자식 합산**이며, 빈-결과→재호출 계약 자체가 없고,
자식이 전체 대화 히스토리를 본다.

### 3-4. 스텝당 비용은 히스토리에 비례한다

- `flows/llm_flows/contents.py:522` — 매 스텝, 세션의 **모든** 이벤트 content를 `copy.deepcopy`
- `telemetry/tracing.py:299` — LLM 호출마다 요청 전체를 JSON 직렬화 (OTel 미구성이어도 인자는 평가됨)
- 세션 이벤트는 스텝당 +2건(함수콜+함수응답)으로 단조 증가

→ 루프 N스텝의 누적 처리 비용은 O(N²) 성격. 스텝당 절대량은 이벤트 크기에 비례한다(§4-1 실측).

## 4. 격리 재현 실측 (mock LLM, 네트워크 없음)

환경: PyPI `google-adk==1.26.0`, Python 3.12, RSS 워치독 + 스텝 백스톱.
스크립트: [`repro/repro_oom.py`](repro/repro_oom.py). mock 모델이 "오류를 받으면 매번 다른
경로로 재시도"라는, upstream #5652에 기록된 실제 모델 행동을 결정론적으로 재현한다.

| # | 시나리오 | 실측 결과 |
|---|---|---|
| **s1** | AgentTool(자식+SkillToolset), 리소스 오류 재시도 | 자식 **500 LLM 스텝 + 500 도구 재시도** 루프. 그동안 **부모에게 보인 이벤트 1개**(부모 자신의 함수콜). `LlmCallsLimitExceededError` 예외로 종료. contents는 1→999건 선형 증가, RSS +8MB |
| **s3** | 자식이 load_skill 후 빈 응답으로 종료 (#6225 패턴) | AgentTool이 **`''` 반환** → 부모가 **재호출 ×3**, 재호출마다 자식 상한 카운터 리셋 |
| **s4** | **대조군**: 동일 자식을 sub-agent로 | 자식 이벤트 **1,000개가 호출자에게 실시간 노출**. 상한은 합산되어 **총 500**(자식 499+부모 1)에서 정지 |
| **s5** | s1 + 3중 방어 가드(§6) | 자식 LLM 스텝 **500 → 4**, 부모 재호출 0회 |

### 4-1. 실측이 말해주는 것과 말해주지 않는 것

- s1이 증명: **루프 구조, 은폐성, 상한 예외 종료 경로**. 소형 오류 dict만 오갈 때 ADK 북키핑
  자체의 비용은 스텝당 ~16KB 수준(500스텝 +8MB)이다.
- s3이 증명: **`''` → 부모 재호출 → 상한 리셋**의 곱셈 구조.
- s4가 증명: **sub-agent에서 같은 루프 성향이 사고로 이어지지 않는 구조적 이유**.
- 실측이 확정하지 않은 것: **관측 환경에서 스텝당 실제 메모리 소모량의 출처** (§7).
  본 재현은 Gemini/LiteLLM 실경로 없이 mock 모델을 사용했으므로, 모델 클라이언트 경로
  (LiteLlm 래퍼→litellm→로컬 SGLang)의 스텝당 비용은 포함되어 있지 않다.

### 4-2. 관측 증상과의 부합

- "호출되는 순간부터": 자식의 첫 행동이 skill 도구 호출(시스템 지침이 유도). 호출 실패 시
  스텝 2부터 즉시 루프.
- "sub-agent에선 문제없음": §3-3 ①~④. 특히 ④(컨텍스트 격리)는 AgentTool 전환 시에만
  생기는 조건 변화로, 자식의 도구 호출 정확도에 영향을 줄 수 있는 **구조적 차이**다.
- "첫 요청에 즉시 OOM": 로컬 서빙 모델은 스텝 지연이 작아 루프 회전이 빠르고,
  1Gi 예산에서는 종료 조건(상한 예외) 도달 전 메모리 한계에 먼저 닿을 수 있다.
  스텝당 소모량의 정확한 출처는 §7의 계측으로 확정해야 한다.

## 5. upstream 공식 기록과 수정 버전 (태그 소스 검증)

세 결함 모두 google/adk-python에 공식 기록되어 있다:

| 결함 | upstream 근거 | 수정 포함 릴리스 |
|---|---|---|
| skill 리소스 오류 무한 재시도 | **Issue #5652 / PR #5651** — "terminate infinite retry loop in LoadSkillResourceTool on RESOURCE_NOT_FOUND". invocation당 실패 카운터로 2회째부터 `RESOURCE_NOT_FOUND_FATAL` 반환. 커밋 본문: "LLM이 매 재시도마다 다른 경로를 hallucinate해도 가드가 작동" | **1.x 전체 ❌**(v1.27.0/1.30.0/1.33.0/**1.36.2** 확인) · v2.0/2.1 ❌ · **v2.2.0부터 ✅** |
| load_skill 후 빈 응답 | **Issue #6225 / PR #6226** — 프로덕션(ADK+Gemini)에서 load_skill 직후 빈 응답 스파이크 관측, "load는 턴 종료가 아님" 규칙 추가 | 모든 릴리스 ❌ (v2.5.0까지) · **main만 ✅** |
| AgentTool이 자식 오류를 `''`로 삼킴 | 커밋 `e737f229` — "surface sub-agent RPC errors from AgentTool". 자식 `error_message` 폴백 반환 추가 | 모든 릴리스 ❌ (v2.5.0까지) · **main만 ✅** |

- **1.x 라인은 최신(v1.36.2)까지 세 수정 모두 백포트되지 않았다.** v1.36.2의
  `LoadSkillResourceTool`도 무가드로 오류를 반환한다(retry/fatal/fail_count 계열 코드 전무 확인).
  **1.x 내 업그레이드로는 해결되지 않는다.**
- 검증 방법: `git show <tag>:src/google/adk/tools/skill_toolset.py` 직접 비교.

## 6. 완화책 — 3중 방어 (s5로 실측 검증)

콜백의 능력 범위 (v1.26.0 코드 기준):

| 하려는 것 | 가능? | 메커니즘 |
|---|---|---|
| after_tool에서 응답 dict 교체 | ✅ | dict 반환 시 tool_response 대체 |
| before_tool에서 실행 스킵 + 대체 응답 | ✅ | dict 반환 시 실제 실행 건너뜀 |
| 텍스트 파트 직접 주입 | ❌ | function_response 내용물만 변경 가능 |
| 루프 강제 종료 | ✅ | `tool_context.actions.skip_summarization = True` → 해당 이벤트가 `is_final_response()=True` → flow 루프 즉시 break |

응답 문구 교체만으로는 부족하다 — s5 실측에서 mock 모델은 강화된 오류 문구를 받고도
재시도했다. **카운터 + 차단 + 하드스톱**이 필요하다 (upstream #5652 수정의 본체도 카운터).

**① 자식 콜백 (또는 plugin — `include_plugins=True` 기본값으로 자식 Runner에 상속됨):**

```python
SKILL_TOOLS = {"load_skill", "load_skill_resource", "list_skills"}
MAX_FAILURES = 2

def guard_count_fail(*, tool, args, tool_context, tool_response):  # after_tool
    if tool.name in SKILL_TOOLS and isinstance(tool_response, dict) \
       and "error" in tool_response:
        key = f"temp:skill_fail_{tool_context.invocation_id}"
        n = int(tool_context.state.get(key) or 0) + 1
        tool_context.state[key] = n
        if n >= MAX_FAILURES:
            return {**tool_response, "error_code": "SKILL_LOOKUP_FATAL",
                    "error": tool_response["error"] +
                             " Do NOT retry any path. Report this failure and stop."}
    return None

def guard_block_fail(*, tool, args, tool_context):  # before_tool
    key = f"temp:skill_fail_{tool_context.invocation_id}"
    if tool.name in SKILL_TOOLS and int(tool_context.state.get(key) or 0) > MAX_FAILURES:
        tool_context.actions.skip_summarization = True   # 하드스톱
        return {"error": "Skill lookup failed repeatedly; aborted.",
                "error_code": "SKILL_LOOKUP_FATAL"}
    return None
```

**② SafeAgentTool — `''` 반환 방지 + wall-time 절단** (upstream `e737f229` 취지 백포트):

```python
class SafeAgentTool(AgentTool):
    async def run_async(self, *, args, tool_context):
        result = await asyncio.wait_for(
            super().run_async(args=args, tool_context=tool_context), timeout=120)
        if not result:
            return ("Child agent failed to produce output (skill lookup errors). "
                    "Do NOT call this tool again; report the failure to the user.")
        return result
```

**③ 부모 안전판** — `RunConfig(max_llm_calls=50)` 수준 축소 + 부모 instruction에
"도구가 빈 결과/오류를 반환하면 재호출하지 말고 사용자에게 보고" 명시.

**검증 (s5)**: s1과 동일한 폭주 조건에서 ①+②만으로 자식 LLM 스텝 **500 → 4**, 부모 재호출 0회.

보조: 자식 instruction에 upstream 규칙 백포트("오류 시 재시도 금지" #5652 / "load는 턴 종료 아님"
#6225), 스킬 name·리소스 경로 정합성 로드 시점 검증.

## 7. 미확정 — 스텝당 메모리 소모량의 출처 (환경 내 계측 필요)

루프·은폐·재호출 구조는 확정이지만, **관측 환경(LiteLlm→SGLang)에서 스텝당 실제 소모량이
얼마이고 어디서 오는지**는 mock 재현 범위 밖이다. 실환경에서 아래 두 지점을 계측하면 확정된다:

```python
def probe_request_size(*, callback_context, llm_request):   # 자식 before_model
    size = len(llm_request.model_dump_json(exclude_none=True))
    print(f"[MEM] contents={len(llm_request.contents)}건 request={size/1e6:.2f}MB "
          f"rss={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}")
    return None
```

- 스텝당 `request` 크기와 RSS 증가율을 함께 기록하면, 질량이 **요청 데이터**(히스토리/도구 응답)에서
  오는지 **모델 클라이언트 경로**(LiteLlm/litellm/HTTP 계층)에서 오는지 분리된다.
- 병렬 함수콜 여부(스텝당 도구 호출 수)도 같은 probe에서 확인 가능.

## 8. 코드 참조

| 항목 | 위치 (v1.26.0) |
|---|---|
| flow 무한 루프 | `flows/llm_flows/base_llm_flow.py:749` |
| skill 도구 무가드 오류 반환 | `tools/skill_toolset.py` (`LoadSkillTool`/`LoadSkillResourceTool.run_async`) |
| AgentTool 자식 소비·`''` 반환·컨텍스트 격리 | `tools/agent_tool.py:run_async` |
| 히스토리 deepcopy | `flows/llm_flows/contents.py:522` |
| 요청 직렬화 | `telemetry/tracing.py:299` |
| LLM 호출 상한 | `agents/run_config.py:309`, `agents/invocation_context.py` |
| LiteLlm 래퍼 (비 Gemini 경로) | `models/lite_llm.py` |
| upstream 수정 | main: `bc45ee67`(#5652), `7c792309`(#6225), `e737f229` |
