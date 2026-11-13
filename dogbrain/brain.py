"""
The brain: an LLM with a persona that can use the dog's tools.

Providers:
  ollama - local model with tool calling (default; nothing leaves the house)
  claude - Anthropic API (set ANTHROPIC_API_KEY; sends text and tool results, never audio)
"""
import json
import re
import threading
import time

from .tools import SPECS


class OllamaProvider:
    def __init__(self, cfg: dict):
        import ollama
        b = cfg["brain"]
        self.client = ollama.Client(host=b["ollama_host"])
        self.model = b["model"]
        self.tools = [{"type": "function", "function": s} for s in SPECS]

    def respond(self, system: str, history: list, user: str, execute) -> str:
        messages = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": user}]
        for _ in range(6):
            r = self.client.chat(model=self.model, messages=messages, tools=self.tools,
                                 options={"temperature": 0.6})
            msg = r.message
            if not msg.tool_calls:
                return (msg.content or "").strip()
            messages.append(msg)
            for call in msg.tool_calls:
                result = execute(call.function.name, dict(call.function.arguments or {}))
                messages.append({"role": "tool", "content": result,
                                 "tool_name": call.function.name})
        return "I got a bit tangled up there."


class ClaudeProvider:
    def __init__(self, cfg: dict):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = cfg["brain"]["claude_model"]
        self.tools = [{"name": s["name"], "description": s["description"],
                       "input_schema": s["parameters"]} for s in SPECS]

    def respond(self, system: str, history: list, user: str, execute) -> str:
        messages = [*history, {"role": "user", "content": user}]
        for _ in range(6):
            r = self.client.messages.create(model=self.model, max_tokens=400, system=system,
                                            messages=messages, tools=self.tools)
            if r.stop_reason != "tool_use":
                return " ".join(b.text for b in r.content if b.type == "text").strip()
            messages.append({"role": "assistant", "content": r.content})
            results = [{"type": "tool_result", "tool_use_id": b.id,
                        "content": execute(b.name, dict(b.input))}
                       for b in r.content if b.type == "tool_use"]
            messages.append({"role": "user", "content": results})
        return "I got a bit tangled up there."


def make_provider(cfg: dict):
    return ClaudeProvider(cfg) if cfg["brain"]["provider"] == "claude" else OllamaProvider(cfg)


def speakable(text: str) -> str:
    """Strip things that sound bad out loud: markdown, emoji, stage directions."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)             # **bold**
    text = re.sub(r"\*[^*]{1,40}\*", "", text)               # *wags tail*
    text = re.sub(r"[`#_>]|\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Brain:
    def __init__(self, cfg: dict, body, vision=None, memory=None, provider=None, log=print):
        self.cfg = cfg["brain"]
        self.body, self.vision, self.memory = body, vision, memory
        self.provider = provider or make_provider(cfg)
        self.history: list[dict] = []
        self.recent_events: list[tuple[float, str]] = []
        self.lock = threading.Lock()
        self.last_talk = 0.0
        self.log = log

    def note(self, event: str) -> None:
        """Something happened that the brain should know about next time it talks."""
        self.recent_events.append((time.time(), event))
        self.recent_events = self.recent_events[-10:]

    def _system(self, user: str) -> str:
        c = self.cfg
        lines = [c["persona"].format(name=c["name"], owner=c["owner"]), "",
                 f"Now: {time.strftime('%A %d %B %Y, %H:%M')}."]
        if self.vision is not None:
            lines.append(f"You currently see: {self.vision.summary()}.")
        s = self.body.link.status
        if s:
            lines.append(f"Clear distance ahead: {s.get('distance_mm', 0) // 10} cm. "
                         f"Body state: {s.get('state', '?')}.")
            if self.body.battery_low():
                lines.append("Your battery is low: you can talk but not walk.")
        now = time.time()
        ev = [f"{int((now - t) // 60)} min ago: {e}" for t, e in self.recent_events
              if now - t < 1800]
        if ev:
            lines.append("Recent events: " + "; ".join(ev) + ".")
        if self.memory is not None:
            facts = self.memory.recall(user, limit=4)
            if facts:
                lines.append("Things you remember that may be relevant: " + " | ".join(facts))
        return "\n".join(lines)

    def _execute(self, name: str, args: dict) -> str:
        result = self.body.run(name, args)
        self.log(f"  [tool] {name}({json.dumps(args)}) -> {result}")
        return result

    def respond(self, user: str) -> str:
        """Handle something said to the dog; returns what the dog should say."""
        with self.lock:
            reply = self.provider.respond(self._system(user), self.history, user, self._execute)
            reply = speakable(reply) or "Okay."
            self.history += [{"role": "user", "content": user},
                             {"role": "assistant", "content": reply}]
            self.history = self.history[-2 * self.cfg["history_turns"]:]
            self.last_talk = time.time()
            return reply

    def prompt(self, situation: str) -> str:
        """Let the dog react to something on its own (a greeting, a low battery...)."""
        return self.respond(f"[No one spoke. Situation: {situation} React in character, "
                            f"in one short sentence. Don't use tools unless it really helps.]")
