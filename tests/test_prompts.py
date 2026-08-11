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


def test_build_user_content_without_quoted_context_returns_text_as_is():
    assert build_user_content("Да", quoted_context=None) == "Да"
    assert build_user_content("Да", quoted_context="") == "Да"


def test_build_user_content_with_quoted_context_labels_both_parts():
    result = build_user_content("Да", quoted_context="А Татнефть на Шуйском есть 95?")
    assert "А Татнефть на Шуйском есть 95?" in result
    assert result.index("А Татнефть на Шуйском есть 95?") < result.index("Да")
    # "Текущее сообщение" — именно про own_text, не про цитату с "?".
    assert result.rstrip().endswith("Да")


def test_build_user_content_with_both_previous_message_and_quoted_context():
    result = build_user_content(
        "Да", previous_message="какое-то предыдущее", quoted_context="цитата с вопросом?"
    )
    assert "какое-то предыдущее" in result
    assert "цитата с вопросом?" in result
    assert result.rstrip().endswith("Да")
    # own_text ("Да") встречается ровно один раз — не задвоен и не потерян
    # среди блоков контекста.
    assert result.count("Да") == 1
