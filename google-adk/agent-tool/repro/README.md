# AgentTool + SkillToolset OOM 재현 번들 (ADK v1.26.0)

`AgentTool(LlmAgent + SkillToolset)` 호출 시 메모리가 무한 증가하다 OOM에 이르는 문제의
**격리 재현 스크립트·셋업·실측 결과·개선안**. 원인 분석 본문은
[`../AgentTool_SkillToolset_OOM_원인분석.md`](../AgentTool_SkillToolset_OOM_원인분석.md) 참고.

---

## 1. 테스트 셋업

```bash
# Python 3.10+ 필요 (실측은 3.12)
uv venv --python 3.12 venv126
uv pip install --python venv126/bin/python "google-adk==1.26.0"

venv126/bin/python repro_oom.py s1   # 시나리오 지정 (s1~s4)
```

- **네트워크/API 키 불필요** — `BaseLlm`을 상속한 mock 모델이 버그 트리거 응답(함수콜)을 결정론적으로 생성.
- **안전장치** (기기 보호, 스크립트 내장):
  - RSS 워치독 스레드: 700MB 초과 시 `os._exit(97)` — macOS에서는 `RLIMIT_AS`가 사실상 무시되므로 워치독 방식 사용.
  - 스텝 백스톱(650 스텝), `signal.alarm(280)`.

## 2. 테스트 시나리오 (`repro_oom.py`)

| # | 구성 | 재현 대상 |
|---|---|---|
| **s1** | 부모(AgentTool) → 자식(SkillToolset). 자식 mock 모델은 리소스 오류를 받으면 **매번 다른 경로를 지어내** `load_skill_resource` 재시도 (upstream #5652에 기록된 실제 모델 행동) | 자식 무한 재시도 루프 + AgentTool의 은폐성 |
| **s2** | s1과 동일하되 스킬 본문 8MB. 자식이 `load_skill`(성공, 8MB 응답)과 리소스 오류를 번갈아 발생 | **OOM 경로** — 메모리 초선형 증가 |
| **s3** | 자식이 `load_skill` 후 **빈 응답**으로 턴 종료 (upstream #6225 패턴). 부모 mock은 빈 도구 결과를 보면 재호출(최대 3회) | AgentTool의 `''` 반환 → 부모 재호출 곱셈 구조 |
| **s4** | **대조군** — 동일 자식을 `sub_agents`(transfer)로 실행 | sub-agent 모드에선 왜 사고가 안 나는지 |
| **s5** | **개선안 검증** — s1과 동일한 폭주 조건(고집스런 재시도 모델) + §5의 3중 방어 장착 | 가드가 루프를 몇 스텝에 끊고 부모 재호출까지 막는지 |

핵심 mock (발췌):

```python
class ChildRetryLlm(BaseLlm):
    """리소스 오류를 받으면 매번 다른 경로를 지어내 재시도 — #5652의 모델 행동 재현."""
    async def generate_content_async(self, llm_request, stream=False):
        n = next_step()
        yield LlmResponse(content=model_content([fc_part(
            "load_skill_resource",
            {"skill_name": "demo-skill", "path": f"references/guess_{n}.md"})]))
```

관측 probe — **자식 에이전트 콜백에 심는다** (자식 이벤트는 부모 스트림에 노출되지 않으므로 이것이 유일한 실시간 관측 창구):

```python
child = Agent(...,
    before_tool_callback=probe_before_tool,     # 도구 재시도 스팸 관측
    before_model_callback=probe_before_model)   # 스텝 수·contents 크기 관측
```

자식 코드를 수정할 수 없다면: `include_plugins=True`(기본)로 부모 plugin이 자식 Runner에
상속되므로, 관측 콜백을 가진 **plugin**을 부모 Runner에 달아도 자식 내부가 보인다.

## 3. 실측 결과 (2026-07-30, macOS, Python 3.12, google-adk==1.26.0)

| # | 결과 |
|---|---|
| **s1** | 자식 **500 LLM 스텝 + 500 도구 재시도**(매번 `guess_N.md`로 경로 변경) 확인. 그동안 **부모에게 보인 이벤트 = 1개**(부모 자신의 함수콜) — 완전 침묵 후 `LlmCallsLimitExceededError` 크래시. 소요 ~5초 |
| **s2** | **RSS 116MB → 707MB, 152스텝(~3.4분)에 워치독 차단.** 자식이 보는 요청 페이로드 600MB까지 증가. 스텝 처리시간 2차 함수적 증가(스텝25=2.4s → 스텝100=50s → 스텝150=198s). **max_llm_calls=500 도달 전에 메모리가 먼저 폭주 = OOM 재현** |
| **s3** | AgentTool이 `''` 반환 → 부모가 **AgentTool 재호출 ×3**(`child_invocations=3`), 재호출마다 자식 상한 카운터 리셋 — 곱셈 구조 실증 |
| **s4** | 자식 fc/fr 이벤트 **1,000개가 호출자에게 실시간 스트리밍**(author=child_agent). 상한도 부모와 **합산 공유**되어 총 500(자식 499 + 부모 1)에서 정지 |
| **s5** | **가드 작동 확인**: 자식 LLM 스텝 **500 → 4**로 즉시 차단. 시퀀스 = 실패#1 → 실패#2(문구 강화) → 실패#3 → before_tool 차단+`skip_summarization` 하드스톱 → SafeAgentTool이 `''`를 오류 메시지로 변환 → 부모가 재호출 없이 종료(parent_llm=2). peak RSS 109MB |

대표 로그 (s2):

```
[   0.03s rss=  131.8MB] [PROBE:자식 before_model step2]   contents=3건   payload≈8.0MB
[  11.16s rss=  308.2MB] [PROBE:자식 before_model step50]  contents=99건  payload≈200.0MB
[  50.23s rss=  499.7MB] [PROBE:자식 before_model step100] contents=199건 payload≈400.0MB
[ 198.01s rss=  691.3MB] [PROBE:자식 before_model step150] contents=299건 payload≈600.0MB
### WATCHDOG: RSS 707MB > 700MB — OOM 경로 확인. child_llm_calls=152 elapsed=201.8s ###
```

### 해석 — sub-agent와의 차이 (s1/s2 vs s4)

| | sub-agent (transfer) | AgentTool |
|---|---|---|
| 가시성 | 자식 이벤트 전부 실시간 노출 → 개입 가능 | 완전 은폐 → 개입 불가 |
| LLM 상한 | InvocationContext 공유 → **합산 500에서 정지** | 호출마다 새 컨텍스트 → **카운터 리셋**, 부모 재호출 시 500×N |
| 오류/빈 결과 | 그대로 대화 결과 — 재호출 계약 없음 | `''` 도구 결과로 변환 → 부모가 재호출 |

## 4. 버전 현황 (태그 소스 검증)

| 수정 | 1.x (≤v1.36.2) | 2.x 릴리스 | main |
|---|---|---|---|
| skill 리소스 재시도 가드 (#5652) | **전부 ❌** | v2.0/2.1 ❌ / **v2.2.0부터 ✅** | ✅ |
| load_skill 후 빈 응답 가드 (#6225) | 전부 ❌ | v2.5.0까지 ❌ | ✅ |
| AgentTool 오류 노출 (`e737f229`) | 전부 ❌ | v2.5.0까지 ❌ | ✅ |

**→ 1.x에 머무는 한 업그레이드로 해결 불가. 아래 백포트 방어 필요.**

## 5. 개선안

### 5-1. 즉시 적용: 3중 방어 (1.x 유지 시 백포트) — **s5로 실측 검증됨**

콜백의 능력 범위를 먼저 이해해야 한다:

| 하려는 것 | 가능? | 메커니즘 |
|---|---|---|
| after_tool에서 응답 dict 교체 | ✅ | dict 반환 시 tool_response **대체** |
| before_tool에서 도구 실행 스킵 + 가짜 응답 | ✅ | dict 반환 시 실제 실행 **건너뜀** |
| 텍스트 part 직접 주입 | ❌ | function_response 내용물만 변경 가능 |
| 루프 강제 종료 | ✅ | `tool_context.actions.skip_summarization = True` → 해당 이벤트가 `is_final_response()=True` → **flow while 루프 즉시 break** |

응답 문구 교체만으로는 "설득"이라 모델이 계속 재시도할 수 있다. **카운터 + 차단 + 하드스톱** 조합이 필요하다 (upstream `bc45ee67`의 본체도 문구가 아니라 실패 카운터).

**① 자식 콜백 — 카운터·차단·하드스톱** (`repro_oom.py`의 `guard_count_fail`/`guard_block_fail`):

```python
SKILL_TOOLS = {"load_skill", "load_skill_resource", "list_skills"}
MAX_FAILURES = 2

def guard_count_fail(*, tool, args, tool_context, tool_response):  # after_tool
    """실패 카운터 + 임계 도달 시 오류 문구 강화(설득 단계)."""
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
    """임계 초과 시 도구 실행 차단 + skip_summarization 하드스톱."""
    key = f"temp:skill_fail_{tool_context.invocation_id}"
    if tool.name in SKILL_TOOLS and int(tool_context.state.get(key) or 0) > MAX_FAILURES:
        tool_context.actions.skip_summarization = True   # ★ 자식 flow 즉시 종료
        return {"error": "Skill lookup failed repeatedly; aborted.",
                "error_code": "SKILL_LOOKUP_FATAL"}
    return None

child = Agent(..., before_tool_callback=guard_block_fail,
              after_tool_callback=guard_count_fail)
```

**② SafeAgentTool — `''` 반환 방지 + wall-time 절단** (`e737f229`·`b983fcf9` 백포트).
①의 하드스톱으로 끝나면 마지막 이벤트가 function_response뿐이라 v1.26.0 AgentTool은
`''`를 반환하고, 부모가 재호출하는 s3 루프가 남는다. 이를 차단:

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

**검증 (s5)**: s1과 동일한 폭주 조건에서 ①+②만으로
자식 LLM 스텝 **500 → 4**, 부모 재호출 0회, peak RSS 109MB로 즉시 종료.

보조 조치:
- **프롬프트 가드 백포트** — 자식 instruction에: "If a skill tool returns any error, do
  NOT retry. Report the error and stop." (#5652) / "Loading a skill does NOT complete
  your turn — continue and produce a reply." (#6225)
- **스킬 정합성 사전 검증** — skill name(kebab-case)·리소스 경로가 실제 존재하는지 로드
  시점에 assert (불일치가 루프의 트리거).
- 자식 코드를 못 건드리면 ①을 **plugin**으로 구현 — `include_plugins=True`(기본)로
  자식 Runner까지 상속된다.

### 5-2. 운영 관측 (재발 감지)

- 자식 콜백/plugin probe를 상시 장착해 "동일 skill 도구 N회 이상 호출" 알람.
- AgentTool 호출 wall-time 상한(asyncio.timeout 래핑) — 자식 폭주 시 강제 절단.

### 5-3. 구조적 대안

- 자식이 1턴만 필요하면 AgentTool 대신 **sub-agent(transfer)** 구성 검토 — 가시성·합산 상한이 공짜로 따라온다 (§3 대조군 참고).
- upstream v2.x의 `AgentTool(mode='single_turn')` 방향처럼 자식 turn 수 자체를 제한하는 래퍼.
