# ADK 2.x의 그래프와 에이전트 루프 — 무엇을 어디에 쓰나

> 대상: ADK Python **v2.14.0**(`main` @ `1cb926d`)과 에이전트 하네스 네 종 —
> Claude Code(공식 문서), Codex `0.167.0`, pi `v1.5.0`, Hermes `v0.23.0`(각 `main`, 2026-11-09).
> 작성: 2026-11-09. 앞 문서 [ADK 1.x와 2.x의 설계 비교](ADK_1x_2x_설계비교.md)가 "무엇이 바뀌었나"라면,
> 이 문서는 "그래서 그래프와 루프를 각각 언제 쓰나"를 다룬다.
> 근거는 공개 출처만 쓴다. 해석은 해석이라고 표시한다.

---

## 0. 결론

1. **ADK 2.x의 그래프는 에이전트 하네스의 코어 구조가 아니라, 특정 유즈케이스를 위한 층이다.**
   그 유즈케이스는 "구조를 미리 아는 다단계 작업 + 며칠짜리 사람 승인 대기 + 서버에서 도는 에이전트 앱"이다.
   ADK 자신도 `LlmAgent` 노드 **안**에는 모델 주도 루프(`flows/llm_flows`)를 그대로 두었다.
2. **절차를 강제하고 싶을 때 필요한 것은 노드 그래프가 아니라 거친 상태 기계다.** explore→plan→code→verify는
   되돌아가는 상태 기계이고, 필요한 것은 "단계 = 정책 묶음, 전이 = 하네스가 판정하는 가드"다.
   그래프 런타임은 무인·장기·감사형 실행일 때 정당하다.
3. **에이전트 생성(spawning)은 선언할 대상이 아니라 기록할 대상이다.** 필요한 자료구조는 수명 규칙이 있는
   소유 트리다. 선언형 구조는 fan-out 모양을 미리 알 때만 값을 한다.
4. **하네스가 ADK 2.x에서 가져갈 것은 그래프 문법이 아니라 실행 저널이다.** durable execution의 조건은
   "주소 지정 가능한 작업 단위 + 원자적 커밋 + 결정적 재생"이고, 정적 그래프는 그것을 얻는 한 방법이다.
5. **두 층이 공통으로 필요로 하는 원시는 "저널 + 스텝 경계 훅"이다.** 하네스가 통제해야 하는 단위는 노드가
   아니라 스텝 경계(모든 툴 결과가 기록된 뒤, 다음 모델 호출 전)다. 그래프 런타임은 그 경계를 노드 경계로
   드러내고, `LlmAgent`에서는 콜백으로 닿는다.
6. **루트를 `Workflow`로 바꾸는 것은 문법 변경이 아니라 위임 의미론 변경이다.** 노드 안에서 `transfer_to_agent`를
   내면 그 노드가 끝나고, 대상 에이전트는 스케줄러가 다음 노드 실행으로 이어 돌린다.

---

## 1. 다섯 런타임 비교

| | **ADK 2.x** | **Claude Code** | **Codex** | **pi** | **Hermes** |
|---|---|---|---|---|---|
| 코어 실행 단위 | 노드(`BaseNode`). `LlmAgent` 노드 안은 모델↔툴 루프 | agentic loop 하나 | `session/turn.rs`의 `run_turn` `loop {}` | 코딩 에이전트 루프 하나 | `AIAgent.run_conversation()` 루프 |
| 다음 단계를 정하는 주체 | 노드 안은 모델, **노드 사이는 선언된 edge** | 모델 — "직전 단계에서 배운 것으로 다음 단계를 정한다" | 모델 — 함수 호출이면 실행 후 다시 샘플링, 메시지만이면 턴 종료 | 모델 | 모델 — tool_calls면 처리 후 루프 |
| 다단계 조합 | `Workflow` edge 목록(**개발자**, 인스턴스화 시 10규칙 검증) | dynamic workflow — **Claude가 작업마다 쓰는 JS** | Code Mode `exec` — **모델이 쓴 JS**를 V8 isolate에서 | 내장 `codemode` 툴 — **모델이 쓴 JS**를 QuickJS/WASM에서 | **Python 스크립트**가 툴을 RPC로 호출(`execute_code`) |
| 다중 에이전트 | 노드, `LlmAgent.mode`, `AgentTool`, `ctx.run_node` | 서브에이전트(자체 컨텍스트, 요약 반환), agent teams(실험) | 모델이 호출하는 `spawn_agent`·`send_message`·`followup_task`·`wait_agent`·`interrupt_agent`·`list_agents` | 코어에는 없음 — "서브에이전트와 플랜 모드는 건너뛴다" | `delegate_task` |
| "그래프"의 정체 | **실행 전에 선언하는 제어·데이터 흐름** | 없음(워크플로는 명령형 스크립트) | `agent-graph-store` = "스레드가 생성한 에이전트의 부모/자식 토폴로지" | `pi-durable` Task Graph = 살아 있는 태스크의 소유 관계 **관측 뷰** | 없음 |
| 영속·재개 | 실행 저널 — 체크포인트, 중단 봉인, 완료 노드 재실행 안 함 | JSONL 트랜스크립트(resume/fork) + 파일 체크포인트. 워크플로는 저장된 결과를 결정적으로 재생 | rollout(세션 파일) | JSONL 세션 트리. `pi-durable`(실험)은 "보여주기 전에 커밋" | SQLite SessionDB |
| 사람 개입 | 인터럽트·재개가 1급(`RequestInput`, resumability) | 대화 자체가 HITL. **워크플로 안에서는 실행 중 입력 없음** | 승인·샌드박스 정책 | 코어에 권한 UI 없음 | 승인 콜백 |

### 1.1 비교에서 보이는 패턴

- **코어는 다섯 모두 루프다.** "그래프 대 루프"는 대립이 아니라 층위 차이다. Claude Code 문서는 모델에 툴을
  주고 모델이 보는 컨텍스트를 관리하는 이 주변층을 "agentic harness"라고 정의한다.
- **다단계의 결정적 조합은 코드로 수렴했다. 갈리는 것은 작성 주체다.**

  | 수준 | 사례 | 작성자 |
  |---|---|---|
  | 툴 호출 조합 | Codex `exec`, pi `codemode`, Hermes `execute_code` | 모델, 매번 |
  | 에이전트 오케스트레이션 | Claude Code dynamic workflow | 모델이 쓰고, 저장해 재실행 |
  | 앱 수준 흐름 | ADK `Workflow` | 개발자, 미리 |

  동기는 같다 — **중간 결과를 LLM 컨텍스트 밖에 둔다.** Claude Code 문서는 중간 결과가 "스크립트 변수"에
  있다고 하고, pi는 "중첩된 툴 호출은 LLM 컨텍스트에 들어가지 않는다", Hermes는 "컨텍스트 비용 0인 턴"이라고
  쓴다. Codex Code Mode와 pi codemode는 스크립트 헬퍼 이름(`tools`, `ALL_TOOLS`, `text`, `image`, `exit`,
  `store`, `load`)까지 같다(선후 관계는 확인하지 않았다).
- **하네스의 "그래프"는 실행의 결과물이지 설계도가 아니다.** 실행 전에 그래프를 그리는 것은 ADK뿐이다.
- **durable execution은 수렴 중이지만 정적 그래프 없이도 가능하다.** pi-durable은 태스크를 "매 단계
  체크포인트를 저장하는 상태 기계"로 만들고 에이전트 루프 자체를 `pi.generation` → `pi.tool` 태스크 사슬로
  표현한다. Claude Code 워크플로는 에이전트 결과를 저장해 두고 재생하며, 재생이 같은 호출을 반복하도록 스크립트
  안에서 `Date.now()`·`Math.random()`이 예외를 던진다.

---

## 2. 그래프와 루프의 자리

### 2.1 층위 — 경쟁이 아니라 포함 관계

ADK 2.x에서 루프와 그래프는 같은 층의 대안이 아니다. **루프는 노드 안**(열린 과업: 모델이 다음 스텝을 정함),
**그래프는 노드 사이**(정해진 흐름: 선언된 edge가 다음 노드를 정함)다. `LlmAgent` 하나를 루트로 쓰면 그래프
없이 루프만 돌고, `Workflow`를 루트로 쓰면 그 노드 중 일부가 루프를 품는다.

### 2.2 어느 쪽을 쓰나

| 상황 | 쓸 것 | 이유 |
|---|---|---|
| 대화형·열린 과업, 사람이 루프 안에 있음 | **루프** (`LlmAgent` + 플러그인·콜백) | 경로를 미리 모른다. 사람이 "다시 탐색해 봐"로 되돌릴 수 있어야 한다 |
| 단계를 강제하고 싶음 (계획 전 편집 금지, 검증 전 완료 금지) | **루프 + 단계별 정책 + 외부 게이트** | 필요한 것은 거친 상태 기계다(§3) |
| 모델이 판단해 위임·병렬 탐색 | **루프 + 생성/수집 툴** | 위임 시점과 모양을 모델이 정한다(§4) |
| 무인·모양 고정·반복 실행 (배터리, 배치, CI형 작업) | **그래프** (`Workflow`) | 사전 검증, 노드 단위 재실행, 비용 상한 |
| 단계 사이에 며칠짜리 사람 승인이 낌 | **그래프** (`RequestInput` + resumability) | 대기 중 프로세스가 사라져도 정확한 지점에서 재개 |
| 실행 전 검증·감사가 요구됨 | **그래프** | 인스턴스화 시점에 10개 규칙으로 구조를 검증 |
| 모양을 아는 fan-out | **그래프**(`parallel_worker`, `JoinNode`) 또는 스크립트 | 선언하는 것 자체가 이득(§4.3) |

### 2.3 루트를 `Workflow`로 바꾸면 위임 의미론이 바뀐다

`Workflow` 노드 안의 `LlmAgent`가 `transfer_to_agent`를 내면 다음이 일어난다(v2.14.0 소스).

1. `workflow/_llm_agent_wrapper.py` — 래퍼가 (resumable이면) 그 에이전트의 `end_of_agent` 상태 이벤트를 내고
   `transferred = True`로 **루프를 끝낸다**("LLM이 위임 없이 끝났거나 transfer로 떠났다 — 이 래퍼가 더 할 일은 없다").
2. `workflow/_node_runner.py` — 이벤트의 `transfer_to_agent`를 노드 컨텍스트로 복사한다.
3. `workflow/_dynamic_node_scheduler.py` — 스케줄러 docstring의 네 번째 책임 "Agent Transfer: 순차적 에이전트
   handoff를 종결 결과나 인터럽트에 닿을 때까지 루프 안에서 제자리로 몰고 간다".
4. `workflow/_workflow.py` — 재개 시 `_classify_static_replay`·`_is_held_transfer`가 기록된 transfer를 재생 순서에
   맞춰 다룬다.

즉 transfer는 "같은 런 안에서 다른 에이전트로 대화 주체를 넘기는 것"(1.x AutoFlow의 의미)이 아니라 **"이 노드를
끝내고 다음 노드 실행으로 이어 가는 것"** 이 된다. 런 안에서의 transfer를 전제로 짠 패턴 — 예컨대 전문가가 일을
마치면 코디네이터로 돌아와 이어 답하게 하는 handback 류 — 은 `Workflow` 루트로 그대로 옮겨지지 않는다.

> 참고: resumability 가이드의 "재개 메시지에 텍스트와 function response를 섞으면 `ValueError`" 제약은
> **`LlmAgent` 루트와 `Workflow` 루트 모두에** 적용된다. 루트 선택을 가르는 근거가 아니다.

### 2.4 두 층이 공유하는 원시 — 저널과 스텝 경계 훅

하네스가 실제로 통제해야 하는 단위는 **스텝 경계** — 직전 모델 응답의 툴 호출이 모두 실행·기록된 뒤, 다음 모델
호출 직전 — 다. 툴 호출과 그 응답 사이에 무엇을 끼워 넣으면 호출/응답 짝이 깨진다.

- Claude Code는 사용자가 작업 중에 보낸 메시지를 "실행 중인 툴 호출이 끝나는 대로, 같은 턴 안에서, 다음 단계
  전에" 읽는다(`how-claude-code-works`).
- ADK 압축은 범위가 툴 호출과 그 응답 사이에서 끝나지 않게 하고, 확인·인증 요청 id를 보호 대상으로 묶는다
  (`apps/compaction.py`).
- ADK 병렬 툴 호출은 모든 응답이 하나의 이벤트로 병합된 뒤에야 다음 스텝이 돈다(§4.2).

그래프 런타임은 이 경계를 **노드 경계로 명시**하고, `LlmAgent`에서는 `before_model`·`after_model` 같은 **콜백으로
닿는다**. 따라서 대화형 하네스가 절차·게이트·실행 중 메시지를 다루는 데 필요한 것은 그래프가 아니라 **스텝 경계
훅과 그 결과를 남기는 저널**이다.

---

## 3. 절차를 기본으로 둘 때 — explore → plan → code → verify

### 3.1 이 절차는 DAG가 아니라 되돌아가는 상태 기계다

실제 작업에서는 code 도중 explore로, verify 실패 시 code로(때로 plan으로) 돌아간다. Claude Code 문서도
세 단계가 "섞인다"고 쓴다. 정적 간선으로 그리면 되돌아가는 간선을 다 열어 사실상 구조가 없어지거나,
막아서 정당한 되돌아감을 차단한다. ADK도 순환은 지원한다(같은 노드 인스턴스 재사용). 어려운 것은 문법이
아니라 **어떤 되돌아감을 허용할지**라는 정책이다.

### 3.2 "강제한다"를 분해하면

| 강제하려는 것 | 최소 수단 | 공개 사례 |
|---|---|---|
| 계획 전 편집 금지 | 단계별 툴 정책 | Claude Code Explore·Plan 서브에이전트는 Write/Edit 거부. Codex plan 모드는 비변경 작업만 허용, `update_plan`은 오류 반환 |
| 단계를 누가 넘기나 | 하네스가 전이를 쥠 | Codex: "개발자 메시지가 명시적으로 끝낼 때까지 Plan Mode. 사용자의 의도·어조·명령형 표현으로는 바뀌지 않는다" |
| 검증 전 완료 금지 | 하네스가 판정하는 종료 게이트 | Claude Code Stop 훅 `decision: "block"`, 문서 예시 사유 "Test suite must pass before proceeding" |
| 단계 안 탐색 | 자유 루프 | 다섯 모두 |
| 며칠 대기 후 재개 | 실행 저널 | ADK `RequestInput` + resumability |

→ 필요한 것은 **"단계 = 정책 묶음(툴·권한·프롬프트), 전이 = 하네스가 판정하는 가드"** 다. 수학적으로는
4상태 그래프지만 런타임 구조로는 **루프 하나 + 정책 테이블 + 가드**다. 노드 하나가 스텝이 아니라 단계(안에
루프를 품은 단위)라는 **거친 입도**가 핵심이다.

### 3.3 게이트 판정은 외부여야 한다

"검증 통과"를 작업한 모델의 자기 보고로 판정하면 어떤 구조든 무력해진다. 판정은 테스트 종료 코드, 읽기 전용
툴만 가진 별도 검증자, 사람, ADK라면 함수 노드나 `ClassifierNode`가 맡아야 한다. 구조를 얼마나 넣느냐보다
판정을 누가 하느냐가 품질을 더 크게 좌우한다.

### 3.4 판정 기준

1. **사람이 루프 안에 있는가** — 있다면 모드 + 게이트로 충분하다. 노드 그래프는 사람이 "다시 탐색해 봐"라고
   할 때 비용이 된다.
2. **실패 시 되돌아가는 경로가 몇 개인가** — 하나 정도면 그래프도 깔끔하다. 임의로 오간다면 상태 기계 + 정책.
3. **단계 사이에 프로세스가 죽거나 며칠 쉬는가** — 그렇다면 전이 기록이 영속돼야 하고, 그래프 런타임이 이를
   기본으로 준다.

### 3.5 대화형 하드 게이트는 어디에 두나

**스텝 경계의 콜백에 두고, 루트는 바꾸지 않는다.** 한 가지 구현 형태는 이렇다 — `after_model_callback`에서
"근거 없는 완료 주장"(검사를 실행하지 않고 결과를 단언한 응답)을 감지하면, 그 응답을 검증 경로로 치환한다.
읽기 전용 검증 에이전트가 실제 검사를 돌리고, 그 판정을 받은 뒤에 원래 에이전트가 답한다. Claude Code의 Stop 훅
(`decision: "block"`)과 같은 층위의 장치다.

게이트에는 강도의 사다리를 두는 것이 실용적이다.

| 단계 | 장치 | 비용 |
|---|---|---|
| 끔 | — | — |
| 약 | 다음 모델 호출에 주의문을 덧붙임(`before_model`) | 사실상 0 — 어차피 일어날 호출에 실림 |
| 강 | 완료 주장을 외부 검증으로 치환(`after_model`) | 검증 에이전트 실행 비용 |

약한 단계는 **자기 보고**에 기대므로 효과가 제한적이고, 강한 단계는 판정이 외부라 실패를 실제로 잡는다(§3.3).
싼 단계부터 내보내 측정한 뒤 강한 단계로 올리는 순서가 맞다.

`Workflow` 루트로 옮기지 않는 이유는 둘이다. ① 위임 의미론이 바뀐다(§2.3). ② 대화형 절차는 되돌아감이 많아
간선 정책이 복잡해진다(§3.1). **무인 실행**(배치·헤드리스 작업)의 검증 루프만 `Workflow`로 — verify 노드의
`"pass"`/`"fail"` route와 실패 시 이전 노드 인스턴스 재사용으로 — 표현하는 것이 맞다.

---

## 4. 에이전트 생성(spawning)

### 4.1 선언할 대상이 아니라 기록할 대상이다

| 요구 | 공개 구현 |
|---|---|
| 생성 정책(역할·모델·툴·컨텍스트) | Codex `agent-roles` = 설정 레이어 + 설명, `fork_turns`(`"all"`/`"none"`). Claude Code fresh vs fork |
| 수명 결속(structured concurrency) | pi-durable: 호출이 중단되면 자식도 중단, "부모는 자식이 idle일 때만 idle", `background: true`가 경계 |
| 합류 | Codex `wait_agent`, ADK `JoinNode`, Claude Code `pipeline()`/`parallel()` |
| 재실행 시 중복 생성 방지 | pi-durable `replay: "safe"` — 소유 인덱스로 같은 자식 재사용, request ID로 중복 메시지 방지 |
| 폭주 방지 | Claude Code 깊이 3·동시 20(서브에이전트), 동시 16·런당 1,000·호출당 4,096(워크플로). Codex `max_concurrency` |
| 관측 | Codex `agent-graph-store`, pi `watchTaskGraph`, ADK `node_info.path` |

전부 **수명 규칙이 있는 트리**의 성질이다. 그래프 네이티브인 ADK조차 생성은 동적이다 — `ctx.run_node()`는
가이드 표현으로 "정적 간선 밖"이고, 결과는 `node_info.path`라는 트리 주소로 기록된다.

### 4.2 ADK에서 병렬 툴 호출은 "전부 끝나야" 모델이 본다

v2.14.0 `flows/llm_flows/tools/_batch_executor.py`는 병렬 툴 호출을 `asyncio.gather`로 모두 기다리고
(하나가 실패하면 나머지를 취소), 응답을 `merge_parallel_function_response_events`로 **이벤트 하나로 병합**한다.
따라서 자식 에이전트를 툴 호출 하나씩으로 띄우면 "일부만 돌아왔지만 이 정도면 충분하다"는 결정 지점이 없다.
조기 합류가 필요하면 **생성과 수집을 별도 툴로 분리**해야 한다(Codex의 `spawn_agent` / `wait_agent`가 그 형태다).

### 4.3 선언형 구조가 값을 하는 경우

fan-out 모양을 미리 알 때 — N개 파일 매핑, 고정 리뷰어 패널, 여러 각도의 계획안. 동시성 상한, 비용 추정
(Claude Code는 에이전트 25개 또는 예상 150만 토큰 초과 시 "Large workflow" 경고), 프롬프트 캐시 공유, 사전
검증을 얻는다. 이것은 "그래프냐"가 아니라 "선언하느냐"의 문제다 — Claude Code는 스크립트로, ADK는 그래프로
같은 이득을 얻는다.

### 4.4 수명 결속 · 이력 일관성 · 멱등 재개는 서로 다른 층이다

생성된 자식을 다룰 때 섞기 쉬운 세 가지가 있다.

| 층 | 해결하는 문제 | ADK 2.x가 주는 것 |
|---|---|---|
| **수명 결속** | 부모가 멈추면 자식도 멈추는가 | `ctx.run_node()`를 **직접 `await`할 때만**. docstring: "`asyncio.create_task()`로 감싸면 감독되지 않은 채 돌아 오류가 조용히 삼켜지고, 부모 노드가 (HITL 등으로) 중단돼도 취소되지 않는다" |
| **이력 일관성** | 중단된 런의 열린 호출이 이력에 매달려 남는가 | 중단 봉인(`events/_abort_events.py`) — 열린 호출에 합성 응답을 붙여 이력을 닫는다. **자식을 취소하지는 않는다** |
| **멱등 재개** | 재개했을 때 끝난 자식을 다시 돌리는가 | `ctx.run_node()`의 중복 제거(이전 턴 이벤트의 캐시된 출력 재생)와 `run_id` — 역시 기다리는 호출의 모양에서 |

따라서 **"지금 띄우고 나중에 수거"하는 생성 모양**(§4.2에서 조기 합류를 위해 필요한 모양)에서는 세 층 중
ADK가 네이티브로 주는 것이 봉인뿐이다. 나머지는 하네스가 채워야 하는데, 비어 있는 것은 그래프가 아니라
**저널**이다 — 자식이 끝나는 시점에 그 결과를 **안정적인 식별자**(자식 id + 과업 해시 등)를 키로 부모 세션
이벤트에 기록해 두면, 재개 때 같은 과업을 다시 띄우지 않고 기록을 재사용할 수 있고 크래시로 잃는 작업도 줄어든다.
pi-durable의 "소유 인덱스로 같은 자식을 재사용 + request ID로 중복 전송 방지"가 같은 원리의 공개 구현이다.

---

## 5. 근거 범위

- ADK: v2.14.0 소스(`BaseAgent`·`BaseNode` 계층, `flows/llm_flows`, `tools/_batch_executor.py`), 가이드
  (`workflow/workflow`, `workflow/graph`, `events/request_input`, `apps/resumability`).
- Claude Code: 공식 문서 `how-claude-code-works`, `workflows`, `agents`, `sub-agents`, `hooks`. **내부 구현은 보지 않았다.**
- Codex: `codex-rs/core/src/session/turn.rs`, `code-mode-protocol/src/description.rs`, `agent-graph-store`,
  `agent-roles`, `prompts/src/multi_agent_instructions.rs`, `collaboration-mode-templates/templates/plan.md`.
- pi: `packages/coding-agent/README.md`, `packages/codemode/README.md`, `packages/durable/README.md`.
- Hermes: `README.md`, `website/docs/developer-guide/architecture.md`.
- ADK(추가): `workflow/_llm_agent_wrapper.py`, `workflow/_node_runner.py`, `workflow/_dynamic_node_scheduler.py`,
  `workflow/_workflow.py`, `agents/context.py`(`run_node` docstring), `apps/compaction.py`,
  `docs/guides/apps/resumability`(제약 절).
- **동작 실측은 하지 않았다.** 2~4절의 권장은 비교와 소스 확인에서 도출한 설계 판단이다.

---

## 6. 교차 검토

이 결론은 ADK 위에서 대화형 하네스를 운영 중인 별도 프로젝트의 분석과 교차 검토해 만들었다(2026-11-09).

- **갈렸던 지점**: 대화형 검증 루프를 `Workflow` 노드로 옮길지. 결론은 §3.5 — 대화형 하드 게이트는 스텝 경계
  콜백에 두고, `Workflow`는 무인 표면에만 쓴다. 근거는 §2.3의 위임 의미론 변화이며, 이것은 교차 검토 과정에서
  ADK 소스로 확인됐다.
- **운영 경험에서 "대화형에서 그래프가 필요했던" 반례는 없었다.** 그래프처럼 보였던 세 경우가 모두 다른 층에서
  풀렸다 — 병렬 탐색은 생성/수집 툴과 모델 판단으로(§4.2), 실행 중 사용자 메시지는 스텝 경계 훅으로(§2.4),
  내구 턴은 턴 상태 저널로. 이것이 §0의 5번("저널 + 스텝 경계 훅")의 직접 근거다.
- **"게이트 판정은 외부여야"(§3.3)도 운영 측정이 뒷받침했다.** 자기 보고에 기대는 약한 단계보다 외부 검증자를
  쓰는 강한 단계가 실패를 실제로 잡았다.
- 그 프로젝트에 대한 적용 설계는 해당 프로젝트 저장소에서 다룬다. 이 문서는 공개 출처만 근거로 쓴다.
