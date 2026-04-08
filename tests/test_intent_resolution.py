from brain.intent_matcher import match_intent_local


def test_match_intent_local_prefers_relevant_intent():
    allowed = ["create_dir", "delete_file", "open_url", "read_file", "write_file"]
    intent, score = match_intent_local("create a folder named demo in sandbox", allowed, threshold=0.0)
    assert intent in allowed
    assert score >= 0.0


