from llm.client import analyze
from pipeline.extract import extract
from pipeline.resolve_station import resolve_station
from tests.fixtures import OFF_TOPIC_OR_NO_SIGNAL, QUESTIONS, REPORTS, REPORTS_WITH_UNKNOWN_STATION


def _rule_based(text: str) -> str:
    result = extract(text)
    station_id = resolve_station(text) if result.message_type != "irrelevant" else None
    reports = [(r.grade, r.status) for r in result.reports]
    return f"type={result.message_type} station={station_id} reports={reports} q_grades={result.question_grades} queue={result.queue_note!r}"


def _llm_based(text: str) -> str:
    result = analyze(text)
    if result is None:
        return "LLM FAILED (None) -> откат на rule-based"
    er = result.extract_result
    reports = [(r.grade, r.status) for r in er.reports]
    return f"type={er.message_type} station={result.station_id} reports={reports} q_grades={er.question_grades} queue={er.queue_note!r}"


def main() -> None:
    all_texts = REPORTS + QUESTIONS + REPORTS_WITH_UNKNOWN_STATION + OFF_TOPIC_OR_NO_SIGNAL
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
