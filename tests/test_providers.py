"""Check both providers' tool loops against fake API clients built from the real SDK types."""
import pytest

from dogbrain.brain import ClaudeProvider, OllamaProvider


def test_ollama_tool_loop(cfg):
    ollama = pytest.importorskip("ollama")
    from ollama import ChatResponse, Message

    calls = []

    class FakeClient:
        def chat(self, model, messages, tools, options):
            calls.append(messages)
            if len(calls) == 1:
                tc = Message.ToolCall(function=Message.ToolCall.Function(
                    name="turn", arguments={"degrees": 90}))
                return ChatResponse(message=Message(role="assistant", content="", tool_calls=[tc]))
            assert messages[-1]["role"] == "tool" and messages[-1]["content"] == "turned left"
            return ChatResponse(message=Message(role="assistant", content="Done, I turned."))

    p = OllamaProvider.__new__(OllamaProvider)
    p.client, p.model, p.tools = FakeClient(), "m", []
    executed = []
    out = p.respond("sys", [], "turn left", lambda n, a: executed.append((n, a)) or "turned left")
    assert out == "Done, I turned." and executed == [("turn", {"degrees": 90})]


def test_claude_tool_loop(cfg):
    pytest.importorskip("anthropic")
    from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

    def msg(content, stop):
        return Message(id="m", type="message", role="assistant", model="x", content=content,
                       stop_reason=stop, stop_sequence=None,
                       usage=Usage(input_tokens=1, output_tokens=1))

    class FakeMessages:
        n = 0

        def create(self, **kw):
            FakeMessages.n += 1
            if FakeMessages.n == 1:
                return msg([ToolUseBlock(id="t1", type="tool_use", name="look", input={})],
                           "tool_use")
            assert kw["messages"][-1]["content"][0]["content"] == "I see: a cup"
            return msg([TextBlock(type="text", text="I see a cup.")], "end_turn")

    p = ClaudeProvider.__new__(ClaudeProvider)
    p.client = type("C", (), {"messages": FakeMessages()})()
    p.model, p.tools = "x", []
    assert p.respond("sys", [], "what do you see", lambda n, a: "I see: a cup") == "I see a cup."
