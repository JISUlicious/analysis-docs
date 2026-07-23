# [단계 1] PlanReActPlanner가 최초 LLM 요청 시 주입하는 실제 프롬프트

> 대상: ADK Python **v1.26.0**
> 코드: `src/google/adk/planners/plan_re_act_planner.py`의 `_build_nl_planner_instruction()` (L153~210)
> 주입 경로: `src/google/adk/flows/llm_flows/_nl_planning.py:54` → `llm_request.append_instructions([...])`

## 1. 주입되는 실제 프롬프트 원문 (태그 상수 치환 완료)

아래 문자열이 `system_instruction`의 **맨 끝에 `\n\n`로 이어 붙습니다.** (소스가 영어이므로 모델이 실제로 보는 원문 그대로)

```text
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

> 참고: preamble들 사이에 빈 줄이 여러 개 보이는 이유 — 각 블록이 앞뒤로 `\n`을 가진 상태에서 다시 `'\n\n'.join()`으로 결합되기 때문입니다(위 원문은 실제 공백까지 반영).

## 2. 구조 해설 — 6개 preamble의 `'\n\n'.join()`

| # | preamble 변수 | 핵심 내용 |
|---|---|---|
| 1 | `high_level_preamble` | **큰 틀**: "기억보다 도구 우선" + 3단계 프로세스(계획→도구실행+추론 interleave→최종답변) + 4개 태그 포맷 규칙 |
| 2 | `planning_preamble` | **계획 요건**: 접근 가능한 도구만 쓰는 일관된 번호 매긴 단계 목록. 실패 시 `/*REPLANNING*/`으로 재계획 |
| 3 | `reasoning_preamble` | **추론 요건**: 현재 궤적 요약 + 다음 단계 지시 |
| 4 | `final_answer_preamble` | **최종답변 요건**: 정밀하게. 답변 불가 시 이유 설명 후 추가정보 요청 |
| 5 | `tool_code_without_python_libraries_preamble` | **도구 코드 요건**: import 금지, 컨텍스트에 없는 것 금지, `라이브러리명.함수명()` 형식 |
| 6 | `user_input_preamble` | **최우선 지시**: 정보 부족 시 되묻기 + 반복 도구호출보다 컨텍스트 활용 |

## 3. 주입 메커니즘 요약

- 위 문자열은 **최초 요청뿐 아니라 모든 스텝마다 동일하게** `system_instruction` 끝에 다시 붙습니다(정적, 스텝 간 불변).
- 붙는 위치(순서): `[agent.instruction]` → `[identity]` → **`[위 PlanReAct 지침]`**. 각각 `\n\n`로 연결.
- 코드 경로: `_nl_planning.py:54` `build_planning_instruction()` → `llm_request.append_instructions([...])` → `llm_request.py:227` `"\n\n".join(...)`.
- 5개 태그 상수(`plan_re_act_planner.py:28~32`):
  - `/*PLANNING*/`, `/*REPLANNING*/`, `/*REASONING*/`, `/*ACTION*/`, `/*FINAL_ANSWER*/`

## 4. 이 프롬프트가 노리는 응답 형태

모델은 이 지침에 따라 **평문 텍스트(태그 포함) + 도구 호출**을 산출하도록 유도됩니다. 예:

```text
/*PLANNING*/
1. roll_die(6) 호출
2. 결과로 소수 판정
/*ACTION*/        ← (ADK에서는 실제 동작이 function_call 파트로 나감)
... 이후 /*REASONING*/, 최종적으로 /*FINAL_ANSWER*/ 뒤에 답변 ...
```

이 응답을 ADK가 어떻게 파싱/가공하는지는 **[단계 2] 문서**에서 다룬다.
