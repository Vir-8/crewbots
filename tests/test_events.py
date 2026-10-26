"""Events as the store keeps them, read back."""

from crewbots.events import Text, Usage, from_json


def test_a_stored_usage_event_reads_back_with_what_the_store_added():
    # The runner stores a turn's usage with its plan, model and effort; `crewbots watch` reads it back.
    data = {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0, "cost_usd": 0.01,
            "plan": "claude", "model": "haiku", "effort": None}

    assert from_json("usage", data) == Usage(input_tokens=10, output_tokens=2, cost_usd=0.01)
    assert from_json("text", {"text": "hi"}) == Text(text="hi")
    assert from_json("message", {"from": "user", "text": "hi"}) is None  # not a backend event
