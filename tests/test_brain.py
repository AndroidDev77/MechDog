from dogbrain.brain import Brain, speakable
from dogbrain.link import DogLink
from dogbrain.memory import Memory
from dogbrain.tools import Body


class FakeProvider:
    def __init__(self):
        self.systems = []

    def respond(self, system, history, user, execute):
        self.systems.append(system)
        if "sit" in user:
            result = execute("do_action", {"action": "sit"})
            return f"*wags tail* **Okay!** I {result.split()[0]} it 🐶"
        return f"You said {user}"


def test_brain_runs_tools_and_cleans_speech(cfg, tmp_path):
    link = DogLink.sim()
    mem = Memory(tmp_path / "m.db")
    mem.remember("Chris likes fetch")
    brain = Brain(cfg, Body(link, None, mem, cfg), None, mem, provider=FakeProvider(), log=lambda *a: None)
    assert brain.respond("please sit") == "Okay! I did it"
    assert ["12", "sit_dowm"] in link.t.log
    brain.respond("does Chris like fetch?")
    assert "Chris likes fetch" in brain.provider.systems[-1]
    assert len(brain.history) == 4
    link.close()


def test_history_is_trimmed(cfg, tmp_path):
    cfg["brain"]["history_turns"] = 2
    brain = Brain(cfg, Body(DogLink.sim(), None, None, cfg), None, None,
                  provider=FakeProvider(), log=lambda *a: None)
    for i in range(5):
        brain.respond(f"hi {i}")
    assert len(brain.history) == 4 and brain.history[-2]["content"] == "hi 4"


def test_speakable():
    assert speakable("*tilts head* Sure, [link](http://x) `ok` 😀") == "Sure, link ok"
