"""ADK v1.26.0용 단계별 관측 probe.

각 실행 단계(agent 진입/종료, 모델 호출 전/후, 도구 실행 전/후)에서
  ① 해당 단계 도달 여부, ② 그 시점의 프로세스 메모리(RSS), ③ 들어온 요청 내용
을 한 줄 로그로 남긴다. AgentTool 내부의 자식 에이전트까지 관측하는 것이 목적.

두 가지 사용 방식 (병행 가능):

  A) 에이전트 트리 일괄 주입 — Runner 구성을 제어할 수 없을 때 (예: adk web)
     from stage_probes import attach_stage_probes
     attach_stage_probes(root_agent)   # root_agent 정의 직후 1줄

  B) Plugin — Runner/App을 직접 구성할 때 (모델/도구 오류 훅까지 커버)
     from stage_probes import StageProbePlugin
     app = App(name=..., root_agent=root_agent, plugins=[StageProbePlugin()])
     # AgentTool(include_plugins=True 기본)이 자식 Runner까지 상속시킨다.

로그 형식:
  [  12.34s rss= 123.4MB] >>MODEL  agent=child_agent step=7 | contents=13건 req=0.02MB | last=user:"..."
"""

from __future__ import annotations

import resource
import sys
import time
from typing import Any, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.agent_tool import AgentTool

_T0 = time.time()
_STEP_COUNTS: dict[str, int] = {}


# ── 공통 유틸 ────────────────────────────────────────────────────────────────
def rss_mb() -> float:
  v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  return v / (1024 * 1024) if sys.platform == "darwin" else v / 1024


def _log(stage: str, agent: str, detail: str) -> None:
  print(f"[{time.time()-_T0:7.2f}s rss={rss_mb():7.1f}MB] "
        f"{stage:<9} agent={agent} | {detail}", flush=True)


def _clip(text: Any, n: int = 80) -> str:
  s = str(text).replace("\n", "\\n")
  return s if len(s) <= n else s[:n] + "…"


def _summarize_content(content) -> str:
  """Content 1건을 'role:내용요약' 문자열로."""
  if content is None or not getattr(content, "parts", None):
    return "(없음)"
  bits = []
  for p in content.parts:
    if p.text:
      bits.append(f'text:"{_clip(p.text, 60)}"')
    elif p.function_call is not None:
      bits.append(f"fc:{p.function_call.name}({_clip(p.function_call.args, 60)})")
    elif p.function_response is not None:
      bits.append(f"fr:{p.function_response.name}→{_clip(p.function_response.response, 60)}")
  return f"{content.role}:[{', '.join(bits) or '(빈 parts)'}]"


def _summarize_request(llm_request) -> str:
  """② 메모리 판단용 크기 + ③ 방금 들어온 요청(마지막 content) 요약."""
  n = len(llm_request.contents or [])
  size = len(llm_request.model_dump_json(exclude_none=True))
  last = llm_request.contents[-1] if llm_request.contents else None
  return f"contents={n}건 req={size/1e6:.2f}MB | last={_summarize_content(last)}"


# ── A) 에이전트 콜백 (트리 일괄 주입용) ──────────────────────────────────────
def probe_before_agent(*, callback_context):
  _log(">>AGENT", callback_context.agent_name,
       f"진입 | user_content={_summarize_content(callback_context.user_content)}")
  return None


def probe_after_agent(*, callback_context):
  _log("<<AGENT", callback_context.agent_name, "종료")
  return None


def probe_before_model(*, callback_context, llm_request):
  name = callback_context.agent_name
  _STEP_COUNTS[name] = _STEP_COUNTS.get(name, 0) + 1
  _log(">>MODEL", name,
       f"step={_STEP_COUNTS[name]} | {_summarize_request(llm_request)}")
  return None


def probe_after_model(*, callback_context, llm_response):
  content = getattr(llm_response, "content", None)
  _log("<<MODEL", callback_context.agent_name,
       f"응답={_summarize_content(content)}")
  return None


def probe_before_tool(*, tool, args, tool_context):
  _log(">>TOOL", tool_context.agent_name, f"{tool.name}({_clip(args, 90)})")
  return None


def probe_after_tool(*, tool, args, tool_context, tool_response):
  err = ""
  if isinstance(tool_response, dict) and "error" in tool_response:
    err = f" ★오류 {tool_response.get('error_code')}"
  _log("<<TOOL", tool_context.agent_name,
       f"{tool.name} → {_clip(tool_response, 80)}{err}")
  return None


def _as_list(v):
  return [] if v is None else (list(v) if isinstance(v, list) else [v])


def attach_stage_probes(agent, _visited: Optional[set] = None) -> None:
  """에이전트 트리 전체(sub_agents + AgentTool로 감싼 자식 포함)에 probe 일괄 주입."""
  _visited = _visited if _visited is not None else set()
  if id(agent) in _visited:
    return
  _visited.add(id(agent))

  if isinstance(agent, LlmAgent):
    agent.before_agent_callback = _as_list(agent.before_agent_callback) + [probe_before_agent]
    agent.after_agent_callback = _as_list(agent.after_agent_callback) + [probe_after_agent]
    agent.before_model_callback = _as_list(agent.before_model_callback) + [probe_before_model]
    agent.after_model_callback = _as_list(agent.after_model_callback) + [probe_after_model]
    agent.before_tool_callback = _as_list(agent.before_tool_callback) + [probe_before_tool]
    agent.after_tool_callback = _as_list(agent.after_tool_callback) + [probe_after_tool]
    for t in (agent.tools or []):
      if isinstance(t, AgentTool):          # AgentTool 경계를 넘어 자식까지
        attach_stage_probes(t.agent, _visited)
  for sub in (agent.sub_agents or []):
    attach_stage_probes(sub, _visited)


# ── B) Plugin 방식 (Runner/App 구성 가능 시; 오류 훅 포함) ───────────────────
class StageProbePlugin(BasePlugin):
  """모든 에이전트(+AgentTool 자식 Runner 상속)에 적용되는 단계 probe.

  agent 콜백 방식과 달리 on_model_error / on_tool_error 까지 관측한다.
  probe 전용이므로 모든 훅은 None을 반환(실행 흐름 무개입)하고 close는 no-op.
  """

  def __init__(self, name: str = "stage_probe"):
    super().__init__(name=name)

  async def before_agent_callback(self, *, agent, callback_context):
    _log(">>AGENT", agent.name,
         f"진입 | user_content={_summarize_content(callback_context.user_content)}")
    return None

  async def after_agent_callback(self, *, agent, callback_context):
    _log("<<AGENT", agent.name, "종료")
    return None

  async def before_model_callback(self, *, callback_context, llm_request):
    name = callback_context.agent_name
    _STEP_COUNTS[name] = _STEP_COUNTS.get(name, 0) + 1
    _log(">>MODEL", name,
         f"step={_STEP_COUNTS[name]} | {_summarize_request(llm_request)}")
    return None

  async def after_model_callback(self, *, callback_context, llm_response):
    _log("<<MODEL", callback_context.agent_name,
         f"응답={_summarize_content(getattr(llm_response, 'content', None))}")
    return None

  async def before_tool_callback(self, *, tool, tool_args, tool_context):
    _log(">>TOOL", tool_context.agent_name, f"{tool.name}({_clip(tool_args, 90)})")
    return None

  async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
    err = ""
    if isinstance(result, dict) and "error" in result:
      err = f" ★오류 {result.get('error_code')}"
    _log("<<TOOL", tool_context.agent_name,
         f"{tool.name} → {_clip(result, 80)}{err}")
    return None

  async def on_model_error_callback(self, *, callback_context, llm_request, error):
    _log("!!MODEL", callback_context.agent_name, f"모델 오류: {_clip(error, 120)}")
    return None

  async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
    _log("!!TOOL", tool_context.agent_name,
         f"{tool.name} 오류: {_clip(error, 120)}")
    return None

  async def close(self) -> None:
    # AgentTool 자식 Runner의 close가 부모 공유 인스턴스를 닫을 수 있으므로 무해해야 함
    return None
