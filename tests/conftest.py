import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import helpers

import pytest  # noqa: E402

from crewbots import runner  # noqa: E402


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    """A turn that failed for a passing reason is tried again at once in tests, not after minutes."""
    monkeypatch.setattr(runner, "RETRY_WAITS_S", (0.0, 0.0, 0.0))
