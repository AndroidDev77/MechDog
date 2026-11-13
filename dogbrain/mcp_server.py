"""
MCP server: lets Claude Desktop / Claude Code drive the dog with the same safe tools.

Add to Claude Desktop's config (Settings > Developer > Edit Config):
{
  "mcpServers": {
    "mechdog": {
      "command": "/path/to/MechDog/venv/bin/python",
      "args": ["-m", "dogbrain.mcp_server"],
      "cwd": "/path/to/MechDog"
    }
  }
}
Don't run this at the same time as `python -m dogbrain`: the dog takes one Bluetooth connection.
"""
try:
    from mcp.server.mcpserver import MCPServer as Server     # mcp 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server         # mcp 1.x

from .__main__ import connect_dog
from .config import load
from .memory import Memory
from .tools import ACTIONS, COLORS, Body

cfg = load()
_body: Body | None = None


def body() -> Body:
    """Connect lazily so the server starts even if the dog is off."""
    global _body
    if _body is None:
        vision = None
        if cfg["camera"]["enabled"]:
            from .vision import Vision
            vision = Vision(cfg)
            vision.start()
        _body = Body(connect_dog(cfg), vision, Memory(), cfg)
    return _body


mcp = Server("mechdog")


@mcp.tool()
def move(direction: str, distance_cm: float) -> str:
    """Walk 'forward' or 'backward' 5-100 cm. Stops early for obstacles."""
    return body().run("move", {"direction": direction, "distance_cm": distance_cm})


@mcp.tool()
def turn(degrees: float) -> str:
    """Turn in place. Positive = left, negative = right (-180..180)."""
    return body().run("turn", {"degrees": degrees})


@mcp.tool()
def stop() -> str:
    """Stop moving immediately."""
    return body().run("stop", {})


@mcp.tool(description=f"Perform a trick. One of: {', '.join(ACTIONS)}.")
def do_action(action: str) -> str:
    return body().run("do_action", {"action": action})


@mcp.tool()
def look(question: str = "") -> str:
    """Describe what the dog's camera sees; pass a question for a closer look."""
    return body().run("look", {"question": question})


@mcp.tool()
def find(target: str) -> str:
    """Turn around until a person (by name) or object is in view."""
    return body().run("find", {"target": target})


@mcp.tool()
def approach(target: str) -> str:
    """Find a person or object and walk up to it."""
    return body().run("approach", {"target": target})


@mcp.tool(description=f"Set the eye light colour: {', '.join(COLORS)}.")
def set_light(color: str) -> str:
    return body().run("set_light", {"color": color})


@mcp.tool()
def status() -> str:
    """Battery, clear distance ahead, camera state."""
    return body().run("status", {})


@mcp.tool()
def remember(fact: str) -> str:
    """Save a fact to the dog's long-term memory."""
    return body().run("remember", {"fact": fact})


@mcp.tool()
def recall(query: str) -> str:
    """Search the dog's long-term memory."""
    return body().run("recall", {"query": query})


if __name__ == "__main__":
    mcp.run()
