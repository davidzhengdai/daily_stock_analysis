from src.services.simtrade.signal_service import SignalService


def test_parse_llm_response_handles_empty_response():
    assert SignalService._parse_llm_response(None) == {}
    assert SignalService._parse_llm_response("") == {}
    assert SignalService._parse_llm_response("   ") == {}


def test_parse_llm_response_extracts_fenced_json():
    raw = """```json
{"signal":"skip","confidence":0.1}
```"""

    parsed = SignalService._parse_llm_response(raw)

    assert parsed["signal"] == "skip"
    assert parsed["confidence"] == 0.1
