from dogbrain.memory import Memory


def test_remember_recall_forget(tmp_path):
    m = Memory(tmp_path / "m.db")
    assert m.remember("Chris's birthday is May 3") == "remembered"
    assert m.remember("Chris's birthday is May 3") == "already knew that"
    m.remember("The cat is called Pixel")
    assert m.recall("when is Chris birthday") == ["Chris's birthday is May 3"]
    assert m.recall("cat name") == ["The cat is called Pixel"]
    assert m.forget("Pixel") == 1 and m.recall("cat") == []


def test_sightings(tmp_path):
    m = Memory(tmp_path / "m.db")
    assert m.saw("Chris") is None
    assert m.saw("Chris") is not None
