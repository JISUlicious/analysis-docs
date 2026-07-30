"""ADK v1.26.0: AgentTool + SkillToolset OOM 재현 (mock LLM, 네트워크 없음).

시나리오:
  s1  자식 skill 무한 재시도 루프 (작은 페이로드) → max_llm_calls=500 상한에서 예외로 종료됨을 확인
  s2  큰 스킬(8MB) + 동일 루프 → 메모리 폭증, 워치독(700MB)이 OOM 직전에 차단
  s3  자식이 load_skill 후 빈 응답으로 종료 → AgentTool이 '' 반환 → 부모가 재호출(곱셈 구조)
  s4  [대조군] 동일 자식을 sub-agent(transfer)로 실행 → 이벤트 가시성 + 공유 상한 확인

안전장치: RSS 워치독(700MB 초과 시 즉시 종료), 스텝 백스톱, signal.alarm.
"""

import asyncio
import os
import resource
import signal
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore")

from google.adk.agents.llm_agent import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.skills import models as sk
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.skill_toolset import SkillToolset
from google.genai import types

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "s1"
RSS_CAP_MB = 700
STEP_CAP = 650
T0 = time.time()
COUNTS = {"child_llm": 0, "parent_llm": 0, "child_tool": 0, "events_seen": 0,
          "child_invocations": 0}


def rss_mb() -> float:
  v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  return v / (1024 * 1024) if sys.platform == "darwin" else v / 1024


def log(msg: str) -> None:
  print(f"[{time.time()-T0:7.2f}s rss={rss_mb():7.1f}MB] {msg}", flush=True)


def watchdog() -> None:
  while True:
    time.sleep(0.05)
    if rss_mb() > RSS_CAP_MB:
      print(f"\n### WATCHDOG: RSS {rss_mb():.0f}MB > {RSS_CAP_MB}MB — "
            f"OOM 경로 확인, 프로세스 차단. child_llm_calls={COUNTS['child_llm']} "
            f"elapsed={time.time()-T0:.1f}s ###", flush=True)
      os._exit(97)
    if COUNTS["child_llm"] > STEP_CAP:
      print(f"\n### BACKSTOP: child steps > {STEP_CAP} ###", flush=True)
      os._exit(96)


threading.Thread(target=watchdog, daemon=True).start()
signal.alarm(280)


def fc_part(name: str, args: dict) -> types.Part:
  return types.Part(function_call=types.FunctionCall(name=name, args=args))


def model_content(parts: list) -> types.Content:
  return types.Content(role="model", parts=parts)


# ── mock 모델들 ──────────────────────────────────────────────────────────────
class ChildRetryLlm(BaseLlm):
  """실측된 버그 행동 재현: 리소스 오류를 받으면 매번 다른 경로를 지어내 재시도."""
  model: str = "mock-child"

  async def generate_content_async(self, llm_request, stream=False):
    COUNTS["child_llm"] += 1
    n = COUNTS["child_llm"]
    if SCENARIO == "s2" and n % 2 == 1:
      # 대형 스킬 본문(8MB)을 세션에 적재시키는 성공 호출
      yield LlmResponse(content=model_content(
          [fc_part("load_skill", {"name": "demo-skill"})]))
    else:
      yield LlmResponse(content=model_content(
          [fc_part("load_skill_resource",
                   {"skill_name": "demo-skill",
                    "path": f"references/guess_{n}.md"})]))


class ChildEmptyEndLlm(BaseLlm):
  """s3: load_skill 성공 후 빈 응답으로 턴 종료(#6225 재현)."""
  model: str = "mock-child-empty"

  async def generate_content_async(self, llm_request, stream=False):
    COUNTS["child_llm"] += 1
    if COUNTS["child_llm"] % 2 == 1:
      COUNTS["child_invocations"] += 1
      yield LlmResponse(content=model_content(
          [fc_part("load_skill", {"name": "demo-skill"})]))
    else:
      yield LlmResponse()  # content 없음 → flow가 이벤트 없이 종료


class ParentLlm(BaseLlm):
  """부모: 처음엔 AgentTool 호출. s3에서는 빈 결과를 보면 재호출(최대 3회)."""
  model: str = "mock-parent"

  async def generate_content_async(self, llm_request, stream=False):
    COUNTS["parent_llm"] += 1
    last = llm_request.contents[-1] if llm_request.contents else None
    got_empty_result = False
    if last and last.parts:
      for p in last.parts:
        if p.function_response is not None:
          result = (p.function_response.response or {}).get("result")
          log(f"부모 모델이 본 도구 결과: {result!r}")
          if not result:
            got_empty_result = True
    if COUNTS["parent_llm"] == 1 or (got_empty_result
                                     and COUNTS["parent_llm"] <= 3):
      log(f"부모 → AgentTool 호출 #{COUNTS['parent_llm']}")
      yield LlmResponse(content=model_content(
          [fc_part("child_agent", {"request": "do the task"})]))
    else:
      yield LlmResponse(content=model_content(
          [types.Part(text="(부모 최종 응답) giving up")]))


class ParentTransferLlm(BaseLlm):
  """s4: sub-agent transfer 방식."""
  model: str = "mock-parent-transfer"

  async def generate_content_async(self, llm_request, stream=False):
    COUNTS["parent_llm"] += 1
    yield LlmResponse(content=model_content(
        [fc_part("transfer_to_agent", {"agent_name": "child_agent"})]))


# ── probe: 사람이 눈으로 확인하는 방법 (자식 콜백에 심는 관측 지점) ─────────
def probe_before_tool(*, tool, args, tool_context):
  COUNTS["child_tool"] += 1
  n = COUNTS["child_tool"]
  if n <= 5 or n % 50 == 0:
    log(f"  [PROBE:자식 before_tool #{n}] {tool.name}({args})")
  return None


def probe_before_model(*, callback_context, llm_request):
  n = COUNTS["child_llm"] + 1
  if n <= 3 or n % 25 == 0:
    sz = sum(len(p.text or "") + len(str(p.function_response.response)
             if p.function_response else "")
             for c in llm_request.contents for p in (c.parts or []))
    log(f"  [PROBE:자식 before_model step{n}] contents={len(llm_request.contents)}건"
        f" payload≈{sz/1e6:.1f}MB")
  return None


# ── 에이전트 구성 ────────────────────────────────────────────────────────────
def build_skill() -> sk.Skill:
  body = "A" * (8_000_000 if SCENARIO == "s2" else 200)
  return sk.Skill(
      frontmatter=sk.Frontmatter(name="demo-skill",
                                 description="A demo skill for repro"),
      instructions=body,
      resources=sk.Resources(references={"real.md": "actual content"}),
  )


def build_child(model) -> Agent:
  return Agent(
      name="child_agent",
      description="child agent wrapped by AgentTool",
      model=model,
      instruction="Use skills to answer.",
      tools=[SkillToolset(skills=[build_skill()])],
      before_tool_callback=probe_before_tool,
      before_model_callback=probe_before_model,
  )


async def main() -> None:
  log(f"=== 시나리오 {SCENARIO} 시작 (adk 1.26.0, mock LLM) ===")

  if SCENARIO in ("s1", "s2"):
    child = build_child(ChildRetryLlm(model="mock-child"))
    parent = Agent(name="parent_agent", model=ParentLlm(model="mock-parent"),
                   instruction="Delegate to child.",
                   tools=[AgentTool(agent=child)])
  elif SCENARIO == "s3":
    child = build_child(ChildEmptyEndLlm(model="mock-child-empty"))
    parent = Agent(name="parent_agent", model=ParentLlm(model="mock-parent"),
                   instruction="Delegate to child.",
                   tools=[AgentTool(agent=child)])
  elif SCENARIO == "s4":
    child = build_child(ChildRetryLlm(model="mock-child"))
    parent = Agent(name="parent_agent",
                   model=ParentTransferLlm(model="mock-parent-transfer"),
                   instruction="Transfer to child.", sub_agents=[child])
  else:
    raise SystemExit(f"unknown scenario {SCENARIO}")

  runner = InMemoryRunner(agent=parent, app_name="repro")
  await runner.session_service.create_session(
      app_name="repro", user_id="u", session_id="s")

  try:
    async for ev in runner.run_async(
        user_id="u", session_id="s",
        new_message=types.Content(role="user",
                                  parts=[types.Part(text="go")])):
      COUNTS["events_seen"] += 1
      n = COUNTS["events_seen"]
      if n <= 6 or n % 100 == 0:
        kind = ("fc" if ev.get_function_calls() else
                "fr" if ev.get_function_responses() else "text")
        log(f"[부모에게 보인 이벤트 #{n}] author={ev.author} kind={kind}")
  except Exception as e:
    log(f"!!! 예외로 종료: {type(e).__name__}: {str(e)[:120]}")

  log(f"=== 종료 요약: parent_llm={COUNTS['parent_llm']} "
      f"child_llm={COUNTS['child_llm']} child_tool={COUNTS['child_tool']} "
      f"child_invocations={COUNTS['child_invocations']} "
      f"visible_events={COUNTS['events_seen']} peak_rss={rss_mb():.0f}MB ===")


asyncio.run(main())
