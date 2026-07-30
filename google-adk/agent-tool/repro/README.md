# AgentTool + SkillToolset 폭주 루프 재현 번들 (ADK v1.26.0)

`AgentTool(LlmAgent + SkillToolset)` 호출 시 발생하는 **무한 재시도 루프와 그 증폭 구조**의
격리 재현 스크립트·실측 결과·검증된 완화책. 원인 분석 본문은
[`../AgentTool_SkillToolset_OOM_원인분석.md`](../AgentTool_SkillToolset_OOM_원인분석.md) 참고.

---

## 1. 셋업

```bash
# Python 3.10+ (실측은 3.12)
uv venv --python 3.12 venv126
uv pip install --python venv126/bin/python "google-adk==1.26.0"

venv126/bin/python repro_oom.py s1   # 시나리오: s1 | s3 | s4 | s5
```

- **네트워크/API 키 불필요** — `BaseLlm` mock이 버그 트리거 응답을 결정론적으로 생성.
- **안전장치 내장**: RSS 워치독(700MB 초과 시 즉시 종료; macOS에선 `RLIMIT_AS`가 무시되므로
  워치독 방식), 스텝 백스톱(650), `signal.alarm(280)`.

## 2. 시나리오

| # | 구성 | 재현 대상 |
|---|---|---|
| **s1** | 부모(AgentTool) → 자식(SkillToolset). 자식 mock은 리소스 오류를 받으면 **매번 다른 경로를 지어내** `load_skill_resource` 재시도 — upstream **#5652에 기록된 실제 모델 행동** | 무한 재시도 루프 + AgentTool의 은폐성 |
| **s3** | 자식이 `load_skill` 후 **빈 응답**으로 턴 종료 — upstream **#6225 패턴**. 부모 mock은 빈 도구 결과를 보면 재호출(최대 3회) | AgentTool의 `''` 반환 → 부모 재호출 곱셈 구조 |
| **s4** | **대조군** — 동일 자식을 `sub_agents`(transfer)로 실행 | sub-agent 모드의 구조적 안전판 |
| **s5** | s1과 동일한 폭주 조건 + §4의 3중 방어 장착 | 가드의 루프 차단 검증 |

핵심 mock (발췌):

```python
class ChildRetryLlm(BaseLlm):
    """#5652의 모델 행동 재현: 오류를 받으면 매번 다른 경로를 지어내 재시도."""
    async def generate_content_async(self, llm_request, stream=False):
        n = next_step()
        yield LlmResponse(content=model_content([fc_part(
            "load_skill_resource",
            {"skill_name": "demo-skill", "path": f"references/guess_{n}.md"})]))
```

관측 probe — **자식 에이전트 콜백에 심는다** (자식 이벤트는 부모 스트림에 노출되지 않으므로
자식 invocation 내부 훅이 유일한 실시간 관측 창구):

```python
child = Agent(...,
    before_tool_callback=probe_before_tool,     # 도구 재시도 스팸 관측
    before_model_callback=probe_before_model)   # 스텝 수·contents 크기 관측
```

자식 코드를 수정할 수 없으면 `include_plugins=True`(기본)로 부모 plugin이 자식 Runner에
상속되므로, 관측 콜백을 가진 **plugin**을 부모 Runner에 달아도 자식 내부가 보인다.

## 3. 실측 결과 (macOS, Python 3.12, PyPI google-adk==1.26.0)

| # | 결과 |
|---|---|
| **s1** | 자식 **500 LLM 스텝 + 500 도구 재시도**(매번 `guess_N.md`로 경로 변경). 그동안 **부모에게 보인 이벤트 = 1개**(부모 자신의 함수콜) — 완전 침묵 후 `LlmCallsLimitExceededError`. contents 1→999건 선형 누적, RSS +8MB, ~5초 |
| **s3** | AgentTool이 `''` 반환 → 부모가 **AgentTool 재호출 ×3**(`child_invocations=3`), 재호출마다 자식 상한 카운터 리셋 |
| **s4** | 자식 fc/fr 이벤트 **1,000개가 호출자에게 실시간 스트리밍**(author=child_agent). 상한은 부모와 **합산 공유**되어 총 500(자식 499+부모 1)에서 정지 |
| **s5** | 자식 LLM 스텝 **500 → 4**로 차단. 시퀀스: 실패#1 → 실패#2(문구 강화 — **mock 모델은 무시하고 또 시도**) → 실패#3 → before_tool 차단+`skip_summarization` 하드스톱 → SafeAgentTool이 `''`를 오류 메시지로 변환 → 부모 재호출 0회(parent_llm=2) |

대표 로그 (s1 — 루프 구조와 은폐성):

```
[0.00s] 부모 → AgentTool 호출 #1
[0.00s] [부모에게 보인 이벤트 #1] kind=fc          ← 이후 5초간 부모에게 아무것도 안 보임
[0.00s]   [PROBE:자식 before_model step1] contents=1건
[0.00s]   [PROBE:자식 before_tool #1] load_skill_resource(guess_1.md)
[0.00s]   [PROBE:자식 before_model step2] contents=3건   ← 오류가 정상 응답으로 쌓이고 루프 계속
        ... guess_2, guess_3, ... guess_500 ...
[5.06s] !!! LlmCallsLimitExceededError: limit 500 exceeded
=== parent_llm=1 child_llm=500 visible_events=1 ===
```

### 해석 — sub-agent와의 구조 차이 (s1 vs s4)

| | sub-agent (transfer) | AgentTool |
|---|---|---|
| 가시성 | 자식 이벤트 전부 실시간 노출 → 개입 가능 | 완전 은폐 → 개입 불가 |
| LLM 상한 | InvocationContext 공유 → **합산 500에서 정지** | 호출마다 새 컨텍스트 → **카운터 리셋**, 부모 재호출 시 500×N |
| 오류/빈 결과 | 그대로 대화 결과 — 재호출 계약 없음 | `''` 도구 결과로 변환 → 부모가 재호출 |
| 컨텍스트 | 자식이 전체 대화 히스토리를 봄 | 자식은 **합성 request 문자열 1건**만 봄 |

## 4. 검증된 완화책 — 3중 방어 (s5)

콜백 능력 범위와 전체 레시피는 본문 §6 참고. 요지:

1. **자식 콜백** — `guard_count_fail`(after_tool: 실패 카운터+문구 강화) +
   `guard_block_fail`(before_tool: 임계 초과 시 실행 차단 + `skip_summarization` 하드스톱).
   문구 교체만으로는 부족함이 s5에서 실측됨(강화 문구 후에도 재시도 발생) — 루프를 끊은 것은
   차단+하드스톱.
2. **SafeAgentTool** — 빈 결과를 오류 메시지로 변환(부모 재호출 차단) + `asyncio.wait_for`
   wall-time 절단.
3. **부모 안전판** — `max_llm_calls` 축소 + "빈 결과/오류 시 재호출 금지" instruction.

코드는 `repro_oom.py`의 `guard_count_fail` / `guard_block_fail` / `SafeAgentTool` 참고
(그대로 복사해 사용 가능).

## 5. 실환경 계측 probe (스텝당 메모리 질량 출처 확정용)

mock 재현은 모델 클라이언트 경로(예: LiteLlm→로컬 서빙)를 포함하지 않는다. 실환경에서
스텝당 소모량의 출처를 확정하려면 자식 before_model에:

```python
def probe_request_size(*, callback_context, llm_request):
    size = len(llm_request.model_dump_json(exclude_none=True))
    print(f"[MEM] contents={len(llm_request.contents)}건 request={size/1e6:.2f}MB")
    return None
```

스텝당 `request` 크기와 프로세스 RSS 증가율을 함께 기록하면, 질량이 요청 데이터(히스토리/도구
응답)에서 오는지 모델 클라이언트 계층에서 오는지 분리된다.
