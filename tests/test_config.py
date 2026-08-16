import pytest

from config import _parse_allowed_peer_ids


def test_parses_comma_separated_peer_ids():
    assert _parse_allowed_peer_ids("2000000001,2000000002") == frozenset({2000000001, 2000000002})


def test_tolerates_whitespace_and_trailing_comma():
    assert _parse_allowed_peer_ids("  2000000001 , 2000000002 , ") == frozenset(
        {2000000001, 2000000002}
    )


@pytest.mark.parametrize("raw", [None, "", "   ", ",", " , , "])
def test_missing_or_empty_value_is_a_startup_error(raw):
    # Дефолта нет намеренно: пустое значение пришлось бы трактовать как
    # "любая беседа" — ровно та дыра, которую закрывает allowlist (F2).
    # Лучше громкое падение на старте, чем тихо открытый бот.
    with pytest.raises(ValueError, match="ALLOWED_PEER_IDS is required"):
        _parse_allowed_peer_ids(raw)


@pytest.mark.parametrize("raw", ["2000000001,абв", "все беседы", "2000000001;2000000002"])
def test_non_integer_value_is_a_startup_error(raw):
    with pytest.raises(ValueError, match="comma-separated list of integers"):
        _parse_allowed_peer_ids(raw)
