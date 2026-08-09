# 구현 명세: ADK 웹서버 잔존 실행(run) 및 세션 객체 관측 체계

> **대상**: google-adk **1.26.0** 기반 FastAPI 서버 (AdkWebServer 또는 자체 조립 서버)
> **목적**: ① 응답 종료 후에도 살아 있는 invocation(버려진 `/run_sse` 실행 등)을 탐지하고,
> ② 세션 관련 객체의 잔존 여부를 RSS가 아닌 **객체 수 기준**으로 진단할 수 있는 관측 체계를 구현한다.
> **비고**: 본 명세의 핵심 전제들은 소스 분석·실측으로 검증된 사실이다(§7).

---

## 1. 배경 (요약)

- `/run_sse`(1.26.0)는 클라이언트 끊김을 감지하지 않는다. 실행이 끝나지 않는 한 해당
  invocation과 그 세션 객체는 메모리에 살아 있다. 세션 객체의 해제 시점은
  "응답 전송 완료"가 아니라 "**invocation 종료**"다.
- 정상 종료된 요청의 `Session`/`Event`/`InvocationContext`는 전부 회수됨이 실측으로
  확인됐다(잔존 0개). 따라서 **응답이 모두 끝난 시점에 이 객체들이 남아 있다면
  그 자체가 이상 신호**이며, 1순위 용의자는 잔존 실행이다.
- plugin의 `after_run_callback`은 **정상 완료 시에만** 호출된다(finally 아님,
  `runners.py:852`). 이 성질을 잔존/중단 판정에 그대로 사용한다.

## 2. 구성요소 개요

| ID | 구성요소 | 역할 |
|---|---|---|
| C1 | `RunTrackerPlugin` | invocation 시작/종료 추적, 잔존·중단 실행 레지스트리 (상시) |
| C2 | 객체 카운트 프로브 | gc 기반 `Session`/`InvocationContext` 잔존 수 진단 (진단 시) |
| C3 | 디버그 API | C1/C2 결과와 asyncio 태스크 스택을 노출하는 내부 엔드포인트 |
| C4 | 배선(wiring) | plugin 등록·엔드포인트 마운트·주기 스윕 태스크 |
| C5 | 주기 메모리 census | 점진적 메모리 증가의 **귀속(attribution)** — RSS·객체수·타입분포·할당지점 주기 기록 |

---

## 3. 상세 명세

### C1. `RunTrackerPlugin` (BasePlugin 서브클래스)

**데이터 모델** — `active: dict[str, RunEntry]`, key = `invocation_id`

```python
@dataclass
class RunEntry:
    task: asyncio.Task          # before_run 시점의 current_task
    app_name: str
    user_id: str
    session_id: str
    started_at: float           # time.time()
    agent_name: str
```

**동작 규칙**

| 훅 | 규칙 |
|---|---|
| `before_run_callback(*, invocation_context)` | `active[invocation_id] = RunEntry(...)` 등록 후 `return None` |
| `after_run_callback(*, invocation_context)` | `active.pop(invocation_id, None)`; `completed` 카운터 증가; `return None` |
| `close()` | **no-op** (필수 — AgentTool 자식 Runner가 공유 plugin의 close를 조기 호출할 수 있음) |

**스윕 규칙** (`sweep()` — C4의 주기 태스크가 호출):

- `entry.task.done() == True` → 정상 종료가 아닌데 태스크가 끝난 것(취소/예외로 after_run 미호출).
  레지스트리에서 제거하고 `aborted` 카운터 증가.
- `entry.task.done() == False`이고 `age > WARN_AGE_SEC` → **잔존 실행 후보**로 경고 로그.
  `age > CRIT_AGE_SEC` → 경고 수위 상향(알람 연동 지점).

**필수 제약 (NFR)**

1. **레지스트리에 `invocation_context`·`session`·`event` 등 대형 객체를 저장 금지.**
   스칼라 필드와 task 참조만 저장한다. (관측 도구가 스스로 누수원이 되는 것 방지 —
   과거 logging plugin 사고의 교훈. 특히 재귀 직렬화 금지.)
2. 모든 훅은 반드시 `None`을 반환한다(값 반환 시 해당 단계를 오버라이드함).
3. 훅 본문은 O(1), 예외를 밖으로 던지지 않는다(try/except로 자체 흡수 후 로그).
4. 단일 이벤트 루프 전제이므로 별도 락 불필요. 멀티 루프/워커 구성이면 워커별 독립 인스턴스.

**메트릭** (내부 카운터, C3에서 노출): `started`, `completed`, `aborted`,
`active_count`(gauge), `oldest_age_sec`(gauge).

**AgentTool 상속 동작**: `include_plugins=True`(기본)면 AgentTool 자식 Runner에도
같은 인스턴스가 상속되어 **자식 invocation도 별도 entry로 등록**된다. 이는 의도된
기능이다(자식 폭주 관측). 판독 시 부모/자식 entry가 함께 보일 수 있음을 문서화한다.

### C2. 객체 카운트 프로브

```python
def count_leak_indicators() -> dict:
    import gc
    from google.adk.sessions.session import Session
    from google.adk.agents.invocation_context import InvocationContext
    gc.collect()
    counts = {"Session": 0, "InvocationContext": 0}
    for o in gc.get_objects():
        if isinstance(o, Session): counts["Session"] += 1
        elif isinstance(o, InvocationContext): counts["InvocationContext"] += 1
    return counts
```

**판독 매트릭스** (모든 응답이 끝난 idle 시점 기준):

| InvocationContext | Session | 판정 |
|---|---|---|
| 0 | 0 | 정상 (RSS가 높아 보여도 allocator 고수위일 뿐) |
| >0 | ≥0 | **실행이 아직 살아 있음** → C1 레지스트리·C3 태스크 덤프로 특정 |
| 0 | >0 | 실행은 끝났는데 참조 보관 (앱 코드 버퍼·traceback·직렬화 큐) → `gc.get_referrers()` 역추적 |

**제약**: `gc.get_objects()` 전수 순회는 비싸다(수십 ms~). **진단 시점에만** 호출하고
요청 경로·고빈도 폴링에 넣지 않는다. InMemorySessionService 사용 시 마스터 세션이
Session 카운트에 포함되므로 기대 기준값은 0이 아니라 "세션 저장소 크기"임을 명시.

**판독 보정 규칙 (실측으로 확정)**:

1. **기대 카운트 ≈ 실행 중 invocation당 `Session 1 + InvocationContext 2`**
   (Runner 레벨 컨텍스트 + agent 레벨 복사본). 카운트를 "잔존 실행 수 추정"으로
   환산할 때 이 배수를 사용한다.
2. **취소/종료 직후의 일시 잔존은 오탐** — 실행이 취소된 직후 몇 이벤트 루프 턴 동안
   정리 지연으로 카운트가 남아 있다가 0으로 떨어진다(실측: 취소 직후 1/2 → 루프
   수 턴 후 0/0). 따라서 **단일 스냅샷으로 판정하지 말고, 짧은 간격(예: 1초)으로
   2회 측정해 감소 추세면 정상 정리 중, 유지되면 잔존 실행**으로 판정한다.

### C3. 디버그 API (내부 전용)

공통: `/debug/*` prefix. **외부 노출 금지** — 내부 네트워크 바인딩, 또는 토큰 헤더
검사 미들웨어 필수. 응답은 JSON.

| 엔드포인트 | 응답 명세 |
|---|---|
| `GET /debug/active_runs` | `{"active": [{"invocation_id", "app_name", "user_id", "session_id", "agent_name", "age_sec", "task_state": "running"}], "metrics": {started, completed, aborted, active_count, oldest_age_sec}}` — C1의 `sweep()` 실행 후 스냅샷 |
| `GET /debug/objects` | C2 결과 + `{"note": "RSS 아님, 파이썬 객체 수 기준"}` |
| `GET /debug/tasks` | 실행 관련 asyncio 태스크와 현재 스택: `[{"task_name", "stack": [qualname...]}]` — 아래 필터 규칙 적용 |

**/debug/tasks 구현 규칙 (⚠ 검증으로 확정된 방식 — 아래 두 함정 때문에 반드시 이 방식 사용)**:

- ~~`task.get_stack()`~~ 사용 금지: suspended 태스크에서 **최외곽 프레임 1개만** 반환하여
  await 체인 내부(`generate_content_async` 등)가 보이지 않는다 (검증 T3에서 확인).
- ~~FrameType gc 열거~~ 사용 금지: CPython 3.11+의 lazy frame 때문에 suspended 코루틴의
  프레임 객체는 접근 전까지 실체화되지 않아 `gc.get_objects()`에 잡히지 않는다.
- **정답**: 코루틴/비동기 제너레이터 **객체**를 gc로 열거하고, `cr_frame`/`ag_frame`
  접근으로 정지 라인을 실체화한다:

```python
def dump_run_frames():
    import gc, types
    out = []
    for o in gc.get_objects():
        if isinstance(o, types.CoroutineType):
            code, frame = o.cr_code, o.cr_frame
        elif isinstance(o, types.AsyncGeneratorType):
            code, frame = o.ag_code, o.ag_frame
        else:
            continue
        if frame is None:            # 종료된 코루틴
            continue
        qn = getattr(code, "co_qualname", code.co_name)
        if any(m in qn for m in MARKERS):
            out.append(f"{qn}:{frame.f_lineno}")
    return out
```

MARKERS = `{"run_async", "event_generator", "BaseLlmFlow", "_call_llm_async",
"_run_one_step_async", "handle_function_call", "generate_content_async"}`.
판독: `_call_llm_async`/`generate_content_async`=모델 대기, `handle_function_calls*`=툴 실행 중
(AgentTool 자식 폭주 포함). 검증 시 실제 출력 예:
`Runner.run_async.<locals>._run_with_trace.<locals>.execute:539 → BaseAgent.run_async:295 →
LlmAgent._run_async_impl:470 → BaseLlmFlow.run_async:752`.

### C4. 배선

1. **plugin 등록**: 서버 조립 지점에서
   `App(name=..., root_agent=..., plugins=[RunTrackerPlugin()])` → `Runner(app=app, ...)`
   (또는 `Runner(..., plugins=[...])`). AgentTool 자식은 자동 상속.
2. **주기 스윕**: FastAPI `lifespan`에서 백그라운드 태스크 생성 —
   `SWEEP_INTERVAL_SEC`(기본 30s)마다 `tracker.sweep()` 호출, 결과를 구조화 로그로.
   lifespan 종료 시 태스크 cancel.
3. **엔드포인트 마운트**: 자체 조립 서버면 FastAPI `APIRouter`로 추가.
   `adk web` CLI 사용 등 앱 객체에 접근 불가한 경우의 폴백: `SIGUSR1` 핸들러에서
   C1/C2 스냅샷을 로그로 덤프.
4. **설정값** (env 또는 상수): `WARN_AGE_SEC=120`, `CRIT_AGE_SEC=600`,
   `SWEEP_INTERVAL_SEC=30`, `DEBUG_API_TOKEN`.

### C5. 주기 메모리 census (점진 증가 귀속용)

**전제**: 과거 관측치는 수정 전 logging plugin의 영향이 섞였을 수 있으므로,
**plugin 수정 후 베이스라인을 재측정**한 뒤 census를 시작한다.

**주기 기록 항목** (기본 10분 간격, 구조화 로그 1줄):

| 항목 | 소스 |
|---|---|
| RSS (MB) | `resource.getrusage` (darwin=bytes, linux=KB 주의) |
| `Session` / `InvocationContext` 카운트 | C2 |
| `active_count` / `aborted` / `oldest_age_sec` | C1 메트릭 |
| 타입 분포 top-N | 아래 `type_census()` — 직전 스냅샷과 diff |
| (선택) 할당 지점 top-10 | tracemalloc — 활성화 시에만 |

**타입 census** (범인 타입을 모를 때의 범용 탐지기):

```python
import gc, collections

def type_census(top=20) -> list[tuple[str, int]]:
    gc.collect()
    return collections.Counter(
        type(o).__name__ for o in gc.get_objects()).most_common(top)
```

- 두 시점 diff에서 **단조 증가하는 타입명**이 곧 증가의 정체다
  (예: artifact 누적이면 `Part`/`Blob`, HTTP 계층이면 관련 클라이언트 타입).
- 오버헤드: `gc.get_objects()` 전수 순회(수십 ms) — census 주기에만 실행.

**tracemalloc 규칙** (타입만으로 부족할 때, 할당 파일:라인 지목):

```python
import tracemalloc
# 부팅 시(env flag로 게이트): tracemalloc.start(5)   # 오버헤드: 메모리 ~2배·CPU 증가 주의
snap = tracemalloc.take_snapshot()                    # census 주기마다
top = snap.compare_to(prev_snap, "lineno")[:10]       # 직전 대비 증가 상위
```

- **상시 활성화 금지** — env flag(`MEM_CENSUS_TRACEMALLOC=1`)로 조사 기간에만 켠다.

**판정 표** (census 시계열 기준):

| 패턴 | 결론 → 다음 행동 |
|---|---|
| RSS↑ · 객체/타입 평탄 · active=0 | allocator 고수위 랫칫 또는 네이티브 계층 → plateau 수렴 여부 관찰; 수렴하면 정상 |
| RSS↑ · `active_count`↑ 또는 `oldest_age_sec` 지속 증가 | **잔존 실행 누적** → C1 entry로 세션 특정, C3로 정지 지점 → SSE 끊김 가드 도입 |
| RSS↑ · 특정 타입 카운트 단조 증가 | 파이썬 레벨 누수 → 해당 타입 `gc.get_referrers` 역추적 / tracemalloc으로 할당 지점 |
| RSS↑ · `aborted` 급증 | 취소 빈발 (그 자체는 정리됨) → 취소 원인(클라이언트/타임아웃) 조사 |

**주의**: InMemory 계열 서비스(`InMemoryArtifactService`/`InMemoryMemoryService`)를 함께
쓰는 구성이면 그쪽의 무한 누적이 census에 타입 증가로 나타난다 — 세션만 DB로 옮긴 경우
흔한 잔여 원인이므로 서버 구성 점검 항목에 포함할 것.

---

## 4. 판정·대응 가이드 (운영 절차)

1. 평시: `active_count`·`aborted` 추이 모니터링. `aborted` 증가는 클라이언트 끊김/취소가
   있었다는 뜻(그 자체는 정리된 것이므로 정상).
2. `oldest_age_sec > WARN_AGE_SEC`: `/debug/active_runs`로 세션 특정 →
   `/debug/tasks`로 정지 지점 확인.
3. 잔존 실행 확인 시 근본 조치: `/run_sse` 핸들러에 끊김 가드 도입 —
   이벤트 yield 루프마다 `await request.is_disconnected()` 확인 후 `return`
   (Aclosing이 실행 체인 정리) + 실행당 wall-time 상한. (본 명세 범위 외의 후속 작업)

## 5. 테스트 계획 (수용 기준)

| # | 시나리오 | 기대 결과 |
|---|---|---|
| T1 | mock 모델로 정상 완료 실행 1회 | before_run에서 entry 생성, after_run에서 제거. 종료 후 `active_count=0`, `completed=1` |
| T2 | 실행 태스크를 도중에 `cancel()` | after_run 미호출 → entry 잔존 → `sweep()`이 `task.done()` 감지, `aborted=1`로 정리 |
| T3 | 느린 mock(스텝 사이 `asyncio.sleep`)으로 장기 실행 방치 | `/debug/active_runs`에 age 증가하는 "running" entry 노출, `/debug/tasks`에 해당 스택 노출 |
| T4 | AgentTool(자식) 포함 구성으로 T3 반복 | 부모·자식 invocation entry가 **각각** 등록·노출 |
| T5 | 정상 완료 후 `/debug/objects` | (DB 세션 서비스 기준) `Session=0, InvocationContext=0` |
| T6 | 모든 훅에서 반환값 검사 | 전부 `None` (plugin이 실행 흐름에 개입하지 않음) |

## 6. 완료 기준 (DoD)

- [ ] C1~C4 구현 및 T1~T6 통과
- [ ] 디버그 API가 내부 전용으로 보호됨 (토큰 또는 네트워크 제한)
- [ ] plugin 훅이 대형 객체를 보관하지 않음을 코드 리뷰로 확인
- [ ] 운영 문서에 §4 판정·대응 절차 반영

## 7. 검증 결과 (2026-08-09, google-adk==1.26.0 / Python 3.12 / DatabaseSessionService(sqlite))

§5의 테스트를 참조 구현으로 실행하여 **전체 통과 (9/9)**:

| # | 결과 |
|---|---|
| T1 | 정상 완료 → entry 등록·해제, `completed=1` ✅ |
| T2 | 취소 → after_run 미호출 → `sweep()`이 `task.done()` 감지, `aborted` 정리 ✅ |
| T3 | 장기 방치 → `running ⚠age초과` entry 노출 + 코루틴 열거로 정지 체인 포착 (`BaseLlmFlow.run_async:752` 등) ✅ |
| T4 | AgentTool 구성에서 부모(`parent_agent`)·자식(`child_agent`) invocation **각각 등록** ✅ |
| T5 | 종료 후 `Session=0, InvocationContext=0` ✅ |
| T6 | 전 훅 None 반환 (6회) ✅ |

검증 과정에서 발견되어 명세에 반영된 구현 함정 2건: `Task.get_stack()`의 최외곽 프레임 한정,
CPython 3.11+ lazy frame (→ C3의 코루틴 객체 열거 방식이 확정 구현).
참조 구현: [`test_runtracker.py`](test_runtracker.py).

## 8. 근거 (검증된 사실)

| 전제 | 근거 |
|---|---|
| `after_run`은 정상 완료 시에만 호출 (finally 아님) | `runners.py:852` — `async for` 완료 후 호출 |
| plugin 훅 시그니처 (`before_run(*, invocation_context)` 등) | `plugins/base_plugin.py` |
| 정상 종료 시 Session/Event/InvocationContext 전부 회수 | weakref + `gc.get_objects` 전수 실측 (DB·InMemory 양쪽, 잔존 0) |
| `/run_sse`에 끊김 감지 없음 (1.26.0) | `cli/adk_web_server.py` `/run_sse` 핸들러; upstream `6a533573`("Abort runs on client drops to avoid leaks")는 v1.26.0 미포함 |
| AgentTool 자식 Runner로 plugin 상속 | `tools/agent_tool.py` `include_plugins=True` 기본값, 동작 실측 |
| AgentTool 자식 Runner의 close가 공유 plugin의 `close()` 조기 호출 가능 | `agent_tool.py` `runner.close()` → `plugin_manager.close()` |
