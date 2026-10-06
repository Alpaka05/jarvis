import time

from core.agent import JarvisAgent
from core.llm.base import LLMResponse, ToolCall
from tests.test_agent import FakeProvider
from tools.base import BaseTool, ToolResult


class SlowTool(BaseTool):
    name = "slow"
    description = "braucht ewig"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> ToolResult:
        time.sleep(2.0)
        return ToolResult.ok("fertig")


def test_hanging_tool_is_cut_off():
    provider = FakeProvider(
        [
            LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="slow", arguments={})]),
            LLMResponse(text="Quelle nicht erreichbar."),
        ]
    )
    agent = JarvisAgent(provider=provider, tools=[SlowTool()])
    agent.TOOL_TIMEOUT = 0.3
    t = time.time()
    answer = agent.process_query("mach was langsames")
    assert time.time() - t < 1.5
    assert answer == "Quelle nicht erreichbar."
    tool_msg = [m for m in agent.history if m["role"] == "tool"][0]
    assert tool_msg["is_error"] is True and "nicht geantwortet" in tool_msg["content"]
