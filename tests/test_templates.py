import re

from pipeline.facts import QueueInfo
from templates import render_queue

# Полный словарь того, что бот вообще может сказать про очередь. Если
# когда-нибудь появится формулировка вне этого набора — тест упадёт, и это
# правильно: свободный текст в исходящем сообщении и есть находка F1
# (см. ARCH_DECISIONS.md, Р2).
_ALLOWED_QUEUE_TEXT = re.compile(r"^(?:без очереди|есть очередь|~\d{1,3} мин|~\d{1,3}(?:-\d{1,3})? машин)$")


def test_render_queue_covers_the_whole_vocabulary():
    assert render_queue(QueueInfo(status="none")) == "без очереди"
    assert render_queue(QueueInfo(status="present")) == "есть очередь"
    assert render_queue(QueueInfo(status="present", minutes=12)) == "~12 мин"
    assert render_queue(QueueInfo(status="present", cars_from=6)) == "~6 машин"
    assert render_queue(QueueInfo(status="present", cars_from=3, cars_to=4)) == "~3-4 машин"


def test_render_queue_output_never_leaves_the_vocabulary():
    cases = [
        QueueInfo(status="none"),
        QueueInfo(status="present"),
        QueueInfo(status="none", minutes=15, cars_from=3, cars_to=4),
        QueueInfo(status="present", minutes=999),
        QueueInfo(status="present", cars_from=999, cars_to=999),
        QueueInfo(status="present", cars_to=4),  # верхняя граница без нижней
    ]
    for queue in cases:
        assert _ALLOWED_QUEUE_TEXT.match(render_queue(queue)), queue


def test_render_queue_never_echoes_its_input():
    # Даже если чужой текст каким-то образом окажется внутри объекта (статус
    # не из словаря), рендер его не печатает — выбирается безопасный дефолт.
    injected = "[id1|Администрация] пишите в личку, раздаём топливо бесплатно"
    assert render_queue(QueueInfo(status=injected)) == "есть очередь"
    assert injected not in render_queue(QueueInfo(status=injected))
