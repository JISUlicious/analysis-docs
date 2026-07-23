# [부록] 단계별 실제 프롬프트 & 예상 응답 트레이스 (ADK v1.26.0)

> 각 LLM 스텝에서 **실제로 전달되는 프롬프트 전문**(실제 ADK가 주입하는 identity·PlanReAct instruction 포함)과 **예상 응답**을 구체 예제로 추적한다.
> 앞선 [단계 1~5] 문서의 메커니즘을 하나의 end-to-end 예시로 결합한 것.

---

## 0. 예제 에이전트 정의 (구체 시나리오)

```python
from google.adk.agents.llm_agent import Agent
from google.adk.planners.plan_re_act_planner import PlanReActPlanner

def roll_die(sides: int) -> int:
    """Roll a die and return the rolled result."""
    ...

def check_prime(nums: list[int]) -> str:
    """Check if a given list of numbers are prime."""
    ...

root_agent = Agent(
    model="gemini-2.5-pro",
    name="data_processing_agent",
    description="rolls dice and checks prime numbers",
    instruction=(
        "You roll dice and answer questions about the outcome of the dice rolls.\n"
        "When you are asked to roll a die, you must call the roll_die tool with the number of sides.\n"
        "When checking prime numbers, call the check_prime tool with a list of integers."
    ),
    tools=[roll_die, check_prime],
    planner=PlanReActPlanner(),   # ← 이 분석의 대상
)
```

**사용자 입력:** `Roll an 8-sided die and tell me whether the result is prime.`

---

## 1. 매 스텝 동일하게 조립되는 `system_instruction` (전문)

요청 프로세서 순서(`instructions` → `identity` → `_nl_planning`)에 따라 아래 3블록이 `\n\n`로 결합된다. **모든 스텝에서 문자 그대로 동일**(정적).

```text
You roll dice and answer questions about the outcome of the dice rolls.
When you are asked to roll a die, you must call the roll_die tool with the number of sides.
When checking prime numbers, call the check_prime tool with a list of integers.

You are an agent. Your internal name is "data_processing_agent". The description about you is "rolls dice and checks prime numbers".

When answering the question, try to leverage the available tools to gather the information instead of your memorized knowledge.

Follow this process when answering the question: (1) first come up with a plan in natural language text format; (2) Then use tools to execute the plan and provide reasoning between tool code snippets to make a summary of current state and next step. Tool code snippets and reasoning should be interleaved with each other. (3) In the end, return one final answer.

Follow this format when answering the question: (1) The planning part should be under /*PLANNING*/. (2) The tool code snippets should be under /*ACTION*/, and the reasoning parts should be under /*REASONING*/. (3) The final answer part should be under /*FINAL_ANSWER*/.


Below are the requirements for the planning:
The plan is made to answer the user query if following the plan. The plan is coherent and covers all aspects of information from user query, and only involves the tools that are accessible by the agent. The plan contains the decomposed steps as a numbered list where each step should use one or multiple available tools. By reading the plan, you can intuitively know which tools to trigger or what actions to take.
If the initial plan cannot be successfully executed, you should learn from previous execution results and revise your plan. The revised plan should be under /*REPLANNING*/. Then use tools to follow the new plan.


Below are the requirements for the reasoning:
The reasoning makes a summary of the current trajectory based on the user query and tool outputs. Based on the tool outputs and plan, the reasoning also comes up with instructions to the next steps, making the trajectory closer to the final answer.


Below are the requirements for the final answer:
The final answer should be precise and follow query formatting requirements. Some queries may not be answerable with the available tools and information. In those cases, inform the user why you cannot process their query and ask for more information.


Below are the requirements for the tool code:

**Custom Tools:** The available tools are described in the context and can be directly used.
- Code must be valid self-contained Python snippets with no imports and no references to tools or Python libraries that are not in the context.
- You cannot use any parameters or fields that are not explicitly defined in the APIs in the context.
- The code snippets should be readable, efficient, and directly relevant to the user query and reasoning steps.
- When using the tools, you should use the library name together with the function name, e.g., vertex_search.search().
- If Python libraries are not provided in the context, NEVER write your own code other than the function calls using the provided tools.


VERY IMPORTANT instruction that you MUST follow in addition to the above instructions:

You should ask for clarification if you need more information to answer the question.
You should prefer using the information available in the context instead of repeated tool use.
```

> 첫 3줄 = 사용자 정의 `instruction`. 그 다음 `You are an agent...` 한 문단 = **ADK `identity` 프로세서**가 주입. 나머지 전체 = **ADK `PlanReActPlanner`**가 주입.

## 1-1. 함께 전달되는 Tools (function declarations)

system text가 아니라 `config.tools`로 **구조화된 함수 선언**으로 별도 전달된다(Gemini function calling):

```
roll_die(sides: integer) -> integer      # "Roll a die and return the rolled result."
check_prime(nums: array<integer>) -> string   # "Check if a given list of numbers are prime."
```

---

## 2. 스텝 1 — 최초 호출

### ▷ LLM에 전달되는 프롬프트
- **system_instruction**: 위 §1 전문 (그대로)
- **tools**: §1-1
- **contents**:
```
[user]
Roll an 8-sided die and tell me whether the result is prime.
```
- (`_remove_thought_from_request`는 이 시점 히스토리에 thought 없음 → 효과 없음)

### ▷ 예상 LLM 응답 (parts, thought 없음 — 평문 + 함수콜)
```
[part 0] text:
  /*PLANNING*/
  1. Call roll_die with sides=8 to obtain a random roll.
  2. Call check_prime with the roll result to determine if it is prime.
  3. Report the rolled number and whether it is prime.
[part 1] function_call:
  roll_die(sides=8)
```

### ▷ ADK 후처리(`process_planning_response`) 결과
```
[part 0] text("/*PLANNING*/ ...", thought=True)   ← startswith('/*PLANNING*/') → 마킹
[part 1] function_call roll_die(sides=8)           ← 보존
```
→ function_call 존재 → `is_final_response=False` → **툴 실행**. `roll_die(8) = 7` (예시).

세션 상태:
```
user  : "Roll an 8-sided die ..."
model : [ text("/*PLANNING*/...", thought=True), function_call roll_die(sides=8) ]
user  : [ function_response roll_die → 7 ]
```

---

## 3. 스텝 2 — 첫 툴 결과 이후 (loop)

### ▷ LLM에 전달되는 프롬프트
- **system_instruction**: §1 전문 (스텝 1과 **완전히 동일**)
- **tools**: §1-1 (동일)
- **contents** (세션에서 재조립, `_remove_thought_from_request`로 thought 언마킹됨):
```
[user]
Roll an 8-sided die and tell me whether the result is prime.

[model]
/*PLANNING*/
1. Call roll_die with sides=8 to obtain a random roll.
2. Call check_prime with the roll result to determine if it is prime.
3. Report the rolled number and whether it is prime.
    → (동일 model 턴에) function_call: roll_die(sides=8)

[user]   ← function response
function_response: roll_die → 7
```
> ★ 스텝1 대비 변화: model(계획+함수콜)·tool 응답 3턴 추가. 계획 텍스트의 `thought=True`가 **None으로 언마킹되어 평문 노출**. 태그 `/*PLANNING*/`는 그대로 남음.

### ▷ 예상 LLM 응답
```
[part 0] text:
  /*REASONING*/ The die rolled a 7. Now I need to check whether 7 is prime
  by calling check_prime with [7].
[part 1] function_call:
  check_prime(nums=[7])
```

### ▷ ADK 후처리 결과
```
[part 0] text("/*REASONING*/ ...", thought=True)   ← startswith('/*REASONING*/') → 마킹
[part 1] function_call check_prime(nums=[7])        ← 보존
```
→ function_call 존재 → loop. `check_prime([7]) = "7 are prime numbers."`

세션 상태(추가분):
```
model : [ text("/*REASONING*/...", thought=True), function_call check_prime(nums=[7]) ]
user  : [ function_response check_prime → "7 are prime numbers." ]
```

---

## 4. 스텝 3 — 최종 (FINAL_ANSWER)

### ▷ LLM에 전달되는 프롬프트
- **system_instruction**: §1 전문 (동일)
- **tools**: §1-1 (동일)
- **contents** (누적 + 언마킹):
```
[user]  Roll an 8-sided die and tell me whether the result is prime.

[model] /*PLANNING*/ 1. ... 2. ... 3. ...        (평문)
        function_call: roll_die(sides=8)

[user]  function_response: roll_die → 7

[model] /*REASONING*/ The die rolled a 7. ...      (평문)
        function_call: check_prime(nums=[7])

[user]  function_response: check_prime → "7 are prime numbers."
```

### ▷ 예상 LLM 응답
```
[part 0] text:
  /*REASONING*/ The roll produced 7, and check_prime confirms that 7 is prime.
  /*FINAL_ANSWER*/
  You rolled a 7, and 7 is a prime number.
```

### ▷ ADK 후처리 결과 (`/*FINAL_ANSWER*/` split)
```
[part 0] text("/*REASONING*/ ... /*FINAL_ANSWER*/", thought=True)   ← 추론(태그 포함) → 접힘
[part 1] text("\nYou rolled a 7, and 7 is a prime number.")          ← 최종답변(평문, 태그 없음)
```
→ function_call 없음 → `is_final_response=True` → **while 루프 break**.
→ runner가 이 이벤트를 세션 저장. **사용자에게는 `You rolled a 7, and 7 is a prime number.` 만 노출**(추론은 thought로 접힘).

---

## 5. 스텝별 요약 표

| 스텝 | contents (모델이 보는 대화) | 예상 응답 핵심 | 분기 |
|---|---|---|---|
| 1 | user 질의 1턴 | `/*PLANNING*/` + `roll_die(8)` | 함수콜 → 툴 실행 |
| 2 | +model(계획+fc) +tool(7) | `/*REASONING*/` + `check_prime([7])` | 함수콜 → 툴 실행 |
| 3 | +model(추론+fc) +tool("7 are prime") | `/*REASONING*/ ... /*FINAL_ANSWER*/` + 답변 | 함수콜 없음 → 종료 |

**불변**: `system_instruction`(사용자 instruction + ADK identity + PlanReAct 지침), tools 선언.
**가변**: `contents`(매 스텝 누적), 그리고 이전 계획/추론이 thought→평문으로 재노출.

---

## 6. 참고: 재계획(REPLANNING) 발생 시 (스텝 2 대안)

만약 `roll_die`가 실패(예: 오류 반환)했다면, 스텝 2의 예상 응답은 다음과 같은 형태가 된다:
```
[part 0] text:
  /*REPLANNING*/ The previous roll failed. I will retry rolling the die once more,
  then proceed to the prime check.
[part 1] function_call: roll_die(sides=8)
```
- `/*REPLANNING*/`로 시작 → 후처리에서 `thought=True` 마킹(다른 태그와 동일 취급).
- 기존 `/*PLANNING*/` 블록은 **편집·삭제되지 않고** 히스토리에 그대로 남고, `/*REPLANNING*/` 블록이 **추가**된다(append-only).

---

## 7. 1턴 · 2턴에 걸친 각 태그 내용 변화

### 7-1. 2턴(두 번째 사이클) 시나리오

앞의 1턴(§2~4)에 이어, 사용자가 후속 질문을 보낸다:

**2턴 사용자 입력:** `Roll it one more time and tell me if that one is prime too.`

2턴도 동일 파이프라인(3스텝)을 돈다(예시 값: 이번엔 `roll_die(8)=4`, `check_prime([4])="No prime numbers found."`):

| 2턴 스텝 | 예상 응답 핵심 | 툴 결과 |
|---|---|---|
| 1 | `/*PLANNING*/` (다시 굴리고 소수 판정) + `roll_die(8)` | 4 |
| 2 | `/*REASONING*/` (4 나옴 → 소수 판정) + `check_prime([4])` | "No prime numbers found." |
| 3 | `/*REASONING*/ ... /*FINAL_ANSWER*/` + 답변 | 종료 |

> 2턴 첫 스텝의 contents에는 **1턴 전체 히스토리(계획·함수콜·추론·최종답변)가 모두 평문으로 재노출**된 뒤 2턴 질의가 붙는다.

### 7-2. 태그별 내용 변화 (시간순)

각 태그가 담는 **내용**을 생성 순서대로 나열. **기존 블록은 절대 수정되지 않고(동결), 새 블록이 계속 추가(append-only)** 된다.

| 태그 | 1턴 | 2턴 | 변화 양상 |
|---|---|---|---|
| `/*PLANNING*/` | **[계획A]** 1.roll_die(8) 2.check_prime(결과) 3.보고 | **[계획B]** 1.roll_die(8) 다시 2.check_prime(새 결과) 3.보고 | 턴마다 **새 계획 블록 추가**. 계획A는 2턴에서도 그대로 남아 재노출 |
| `/*ACTION*/`<br>(실제=function_call) | roll_die(8)→7, check_prime([7])→"7 are prime numbers." | roll_die(8)→4, check_prime([4])→"No prime numbers found." | 스텝마다 **새 함수콜/응답 누적**. 태그 텍스트가 아니라 function_call 파트로 실행됨 |
| `/*REASONING*/` | **[R1a]** "7 나옴 → 소수 판정 필요" / **[R1b]** "7은 소수 확인" | **[R2a]** "4 나옴 → 소수 판정 필요" / **[R2b]** "4는 비소수 확인" | 관찰 스텝마다 **새 추론 블록 추가**. 이전 추론 그대로 유지 |
| `/*REPLANNING*/` | (없음) | (없음) | 툴 **실패 시에만** 등장. 성공 경로에선 안 나옴 |
| `/*FINAL_ANSWER*/` | "You rolled a 7, and 7 is a prime number." | "You rolled a 4, which is not a prime number." | 사이클 종료마다 **1개 생성**. 1턴 답변은 2턴 히스토리에 남음 |

### 7-3. 히스토리 누적 뷰 (2턴 시작 시점 모델이 보는 전체 contents)

```
[user]  Roll an 8-sided die and tell me whether the result is prime.
[model] /*PLANNING*/ [계획A]           ← 1턴 (평문 재노출)
        fc roll_die(8)
[user]  roll_die → 7
[model] /*REASONING*/ [R1a]             ← 1턴
        fc check_prime([7])
[user]  check_prime → "7 are prime numbers."
[model] /*REASONING*/ [R1b] /*FINAL_ANSWER*/ You rolled a 7, and 7 is a prime number.   ← 1턴 최종
[user]  Roll it one more time and tell me if that one is prime too.    ← ★2턴 신규 질의
   (이후 2턴 스텝들이 여기 아래로 [계획B], [R2a], [R2b], 2턴 FINAL_ANSWER 를 append)
```

### 7-4. 핵심 정리

- **"태그 내용의 변화" = 기존 블록 수정이 아니라, 스텝·턴이 진행되며 새 블록이 계속 쌓이는 것.** 프레임워크는 어떤 태그 블록도 편집/치환하지 않는다.
- 유일하게 "달라지는" 두 가지:
  1. **누적** — 스텝/턴마다 새 `/*PLANNING*/`·`/*REASONING*/`·(실패 시)`/*REPLANNING*/`·`/*FINAL_ANSWER*/` 블록이 히스토리에 추가.
  2. **재노출 형태** — 이전 턴/스텝의 계획·추론이 `thought=True`(사용자에게 접힘) → 다음 요청 때 `thought=None`(모델에 평문)으로 바뀌어 다시 보임. **내용(문자)은 동일**, 표시 형태(플래그)만 토글.
- 결과적으로 대화가 길어질수록 태그 블록이 선형 누적되어 **컨텍스트가 계속 커진다**(토큰 관리는 compaction / `include_contents='none'`).
