from collections import Counter

from db.schema import get_connection


def main() -> None:
    conn = get_connection()
    rows = conn.execute("SELECT raw_text FROM unresolved_mention").fetchall()
    counts = Counter(row[0] for row in rows)
    if not counts:
        print("Нераспознанных упоминаний нет.")
        return
    for text, count in counts.most_common():
        print(f"{count:3d}  {text!r}")


if __name__ == "__main__":
    main()
