"""명세 검증: RunTrackerPlugin(C1) + 객체 프로브(C2) + 태스크 덤프(C3 로직) — adk 1.26.0.

T1 정상 완료  T2 취소(aborted)  T3 장기 방치(running 잔존)  T4 AgentTool 자식 등록
T5 객체 제로 베이스라인  T6 훅 무개입(None 반환)
"""
import asyncio, gc, time, warnings
from dataclasses import dataclass

warnings.filterwarnings("ignore")

from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions.database_session_service import DatabaseSessionService
from google.adk.sessions.session import Session
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

PASS = []


def check(name, cond, detail=""):
  print(f"  {'✅' if cond else '❌'} {name} {detail}")
  PASS.append(cond)


# ── C1: RunTrackerPlugin (명세 그대로) ──────────────────────────────────────
@dataclass
class RunEntry:
  task: asyncio.Task
  app_name: str
  user_id: str
  session_id: str
  started_at: float
  agent_name: str


class RunTrackerPlugin(BasePlugin):

  def __init__(self, warn_age_sec=2.0):
    super().__init__(name="run_tracker")
    self.active: dict[str, RunEntry] = {}
    self.started = self.completed = self.aborted = 0
    self.warn_age = warn_age_sec
    self.hook_returns = []          # T6 검증용

  async def before_run_callback(self, *, invocation_context):
    try:
      ic = invocation_context
      self.active[ic.invocation_id] = RunEntry(
          task=asyncio.current_task(), app_name=ic.app_name,
          user_id=ic.user_id, session_id=ic.session.id,
          started_at=time.time(), agent_name=ic.agent.name)
      self.started += 1
    except Exception as e:          # NFR-3: 예외 자체 흡수
      print(f"  (tracker error swallowed: {e})")
    self.hook_returns.append(None)
    return None

  async def after_run_callback(self, *, invocation_context):
    self.active.pop(invocation_context.invocation_id, None)
    self.completed += 1
    self.hook_returns.append(None)
    return None

  def sweep(self):
    now, rows = time.time(), []
    for inv, e in list(self.active.items()):
      if e.task.done():             # 취소/예외 종료 → after_run 미호출 케이스
        self.active.pop(inv, None)
        self.aborted += 1
        rows.append((inv, e.agent_name, now - e.started_at, "aborted"))
      else:
        state = "running" + (" ⚠age초과" if now - e.started_at > self.warn_age else "")
        rows.append((inv, e.agent_name, now - e.started_at, state))
    return rows

  async def close(self):            # 필수: no-op
    return None


# ── C2: 객체 카운트 프로브 ──────────────────────────────────────────────────
def count_leak_indicators():
  gc.collect()
  c = {"Session": 0, "InvocationContext": 0}
  for o in gc.get_objects():
    if isinstance(o, Session):
      c["Session"] += 1
    elif isinstance(o, InvocationContext):
      c["InvocationContext"] += 1
  return c


# ── C3(/debug/tasks) 로직 ───────────────────────────────────────────────────
_MARKERS = ("run_async", "event_generator", "BaseLlmFlow", "_call_llm_async",
            "_run_one_step_async", "handle_function_call", "generate_content_async")


def dump_run_tasks():
  """정지 지점 덤프. 구현 주의 2가지(검증으로 발견):
  ① Task.get_stack()은 suspended 태스크에서 최외곽 프레임만 반환 → await 체인 내부 안 보임
  ② 3.11+ lazy frame: FrameType은 접근 전까지 실체화 안 됨 → 프레임 열거 불가
  → 코루틴/비동기 제너레이터 '객체'를 gc로 열거하고 cr_frame/ag_frame으로 정지 라인 확인."""
  import types as pytypes
  out = []
  for o in gc.get_objects():
    if isinstance(o, pytypes.CoroutineType):
      code, frame = o.cr_code, o.cr_frame
    elif isinstance(o, pytypes.AsyncGeneratorType):
      code, frame = o.ag_code, o.ag_frame
    else:
      continue
    if frame is None:                 # 이미 종료된 것
      continue
    qn = getattr(code, "co_qualname", code.co_name)
    if any(m in qn for m in _MARKERS):
      out.append(f"{qn}:{frame.f_lineno}")
  return out


# ── mock 모델들 ─────────────────────────────────────────────────────────────
def _text(t):
  return LlmResponse(content=types.Content(role="model",
                                           parts=[types.Part(text=t)]))


class QuickLlm(BaseLlm):
  model: str = "quick"
  async def generate_content_async(self, llm_request, stream=False):
    yield _text("done")


class SlowLlm(BaseLlm):
  model: str = "slow"
  async def generate_content_async(self, llm_request, stream=False):
    await asyncio.sleep(60)         # 장기 실행 시뮬레이션 (T2/T3에서 취소됨)
    yield _text("late")


class SlowChildParentLlm(BaseLlm):  # T4: AgentTool(느린 자식) 호출
  model: str = "parent"
  async def generate_content_async(self, llm_request, stream=False):
    last = llm_request.contents[-1] if llm_request.contents else None
    if last and last.parts and any(p.function_response for p in last.parts):
      yield _text("(부모 최종)")
    else:
      yield LlmResponse(content=types.Content(role="model", parts=[
          types.Part(function_call=types.FunctionCall(
              name="child_agent", args={"request": "go"}))]))


def make_runner(tracker, agent, tag):
  svc = DatabaseSessionService(f"sqlite+aiosqlite:///./tracker_{tag}.db")
  return Runner(app_name="t", agent=agent, session_service=svc,
                plugins=[tracker])


async def drive(runner, sid, text="go"):
  await runner.session_service.create_session(app_name="t", user_id="u",
                                              session_id=sid)
  async for _ in runner.run_async(
      user_id="u", session_id=sid,
      new_message=types.Content(role="user", parts=[types.Part(text=text)])):
    pass


async def main():
  tracker = RunTrackerPlugin(warn_age_sec=1.0)

  print("── T1: 정상 완료 ──")
  r1 = make_runner(tracker, Agent(name="a1", model=QuickLlm(model="q"),
                                  instruction="x"), "t1")
  await drive(r1, "s1")
  check("entry 등록·해제", tracker.started == 1 and not tracker.active)
  check("completed=1", tracker.completed == 1)

  print("── T2: 취소 → after_run 미호출 → sweep이 aborted 판정 ──")
  r2 = make_runner(tracker, Agent(name="a2", model=SlowLlm(model="s"),
                                  instruction="x"), "t2")
  task = asyncio.create_task(drive(r2, "s2"))
  await asyncio.sleep(0.5)
  in_flight = len(tracker.active)
  task.cancel()
  try:
    await task
  except asyncio.CancelledError:
    pass
  rows = tracker.sweep()
  check("취소 전 running 등록", in_flight == 1)
  check("sweep이 aborted 정리", tracker.aborted == 1 and not tracker.active,
        f"(sweep={rows})")

  print("── T3: 장기 방치 → running 잔존 노출 + 태스크 스택 ──")
  r3 = make_runner(tracker, Agent(name="a3", model=SlowLlm(model="s"),
                                  instruction="x"), "t3")
  task3 = asyncio.create_task(drive(r3, "s3"))
  await asyncio.sleep(1.5)
  rows = tracker.sweep()
  running = [r for r in rows if "running" in r[3]]
  check("running entry 노출", len(running) == 1 and running[0][2] >= 1.4,
        f"(age={running[0][2]:.1f}s state={running[0][3]})" if running else "")
  frames = dump_run_tasks()
  hit = any("generate_content_async" in f or "_call_llm_async" in f
            for f in frames)
  check("/debug/tasks 로직이 정지 지점 포착", hit, f"(잔존 프레임={frames[:4]})")
  task3.cancel()
  try:
    await task3
  except asyncio.CancelledError:
    pass
  tracker.sweep()

  print("── T4: AgentTool 자식 invocation 별도 등록 ──")
  child = Agent(name="child_agent", description="c",
                model=SlowLlm(model="s"), instruction="x")
  parent = Agent(name="parent_agent",
                 model=SlowChildParentLlm(model="p"), instruction="x",
                 tools=[AgentTool(agent=child)])
  r4 = make_runner(tracker, parent, "t4")
  task4 = asyncio.create_task(drive(r4, "s4"))
  await asyncio.sleep(1.0)
  rows = tracker.sweep()
  agents_seen = sorted(r[1] for r in rows)
  check("부모+자식 entry 동시 등록", agents_seen == ["child_agent", "parent_agent"],
        f"({agents_seen})")
  task4.cancel()
  try:
    await task4
  except asyncio.CancelledError:
    pass
  await asyncio.sleep(0.2)
  tracker.sweep()

  print("── T5: 객체 제로 베이스라인 ──")
  counts = count_leak_indicators()
  check("Session=0, InvocationContext=0",
        counts == {"Session": 0, "InvocationContext": 0}, f"({counts})")

  print("── T6: 훅 무개입 ──")
  check("모든 훅 None 반환", all(v is None for v in tracker.hook_returns),
        f"({len(tracker.hook_returns)}회 호출)")

  print()
  m = tracker
  print(f"메트릭: started={m.started} completed={m.completed} "
        f"aborted={m.aborted} active={len(m.active)}")
  print(f"\n{'🎉 전체 통과' if all(PASS) else '❌ 실패 있음'} "
        f"({sum(PASS)}/{len(PASS)})")


asyncio.run(main())
import glob, os
for f in glob.glob("./tracker_*.db"):
  os.remove(f)
