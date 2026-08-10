from llm.client import analyze
from pipeline.extract import BreakInfo, extract
from pipeline.resolve_station import resolve_station
from tests.fixtures import BREAK_REPORTS, OFF_TOPIC_OR_NO_SIGNAL, QUESTIONS, REPORTS, REPORTS_WITH_UNKNOWN_STATION


def _break_repr(break_info: BreakInfo | None) -> str:
    if break_info is None:
        return "None"
    return f"(kind={break_info.kind!r} until={break_info.until!r} duration={break_info.duration_note!r})"


def _rule_based(text: str) -> str:
    result = extract(text)
    station_id = resolve_station(text) if result.message_type != "irrelevant" else None
    reports = [(r.grade, r.status) for r in result.reports]
    return (
        f"type={result.message_type} station={station_id} reports={reports} "
        f"q_grades={result.question_grades} queue={result.queue_note!r} break={_break_repr(result.break_info)}"
    )


def _llm_based(text: str) -> str:
    result = analyze(text)
    if result is None:
        return "LLM FAILED (None) -> откат на rule-based"
    er = result.extract_result
    reports = [(r.grade, r.status) for r in er.reports]
    return (
        f"type={er.message_type} station={result.station_id} reports={reports} "
        f"q_grades={er.question_grades} queue={er.queue_note!r} break={_break_repr(er.break_info)}"
    )


def main() -> None:
    all_texts = REPORTS + QUESTIONS + REPORTS_WITH_UNKNOWN_STATION + OFF_TOPIC_OR_NO_SIGNAL + BREAK_REPORTS
    seen: set[str] = set()
    for text in all_texts:
        if text in seen:
            continue
        seen.add(text)
        print("=" * 80)
        print(f"TEXT: {text!r}")
        print(f"  rule-based: {_rule_based(text)}")
        print(f"  llm:        {_llm_based(text)}")


if __name__ == "__main__":
    main()
