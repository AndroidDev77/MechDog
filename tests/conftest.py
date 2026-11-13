import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dogbrain.config import DEFAULTS  # noqa: E402


@pytest.fixture
def cfg():
    c = copy.deepcopy(DEFAULTS)
    c["dog"]["walk_speed_cm_s"] = 200.0     # make timed moves fast in tests
    c["dog"]["turn_rate_deg_s"] = 900.0
    return c
