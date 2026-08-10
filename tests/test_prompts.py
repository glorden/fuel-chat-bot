from llm.prompts import build_user_content


def test_build_user_content_without_previous_message_returns_text_as_is():
    assert build_user_content("95 есть на Газпроме?") == "95 есть на Газпроме?"
    assert build_user_content("95 есть на Газпроме?", previous_message=None) == "95 есть на Газпроме?"
    assert build_user_content("95 есть на Газпроме?", previous_message="") == "95 есть на Газпроме?"


def test_build_user_content_with_previous_message_labels_both_parts():
    result = build_user_content("большая очередь?", previous_message="95 есть на Газпроме?")
    assert "95 есть на Газпроме?" in result
    assert "большая очередь?" in result
    assert result.index("95 есть на Газпроме?") < result.index("большая очередь?")
