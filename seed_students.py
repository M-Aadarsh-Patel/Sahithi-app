"""Seed the two users (§3.1) and the student roster (§3.2) from a CSV.

The whole batch is checked before MongoDB is touched, so a bad CSV fails on your
laptop instead of half-way through writing to Atlas.

    python seed_students.py [students.csv]
    python seed_students.py students.csv --dry-run   # validate the CSV, no database
    python seed_students.py --self-check             # self-test the validator, no CSV, no database
"""

import csv
import sys
from datetime import datetime
from typing import Any

from ist import IST

# §3.1 — exactly two. Passwords live in env vars, not the database, so these
# documents carry no password field.
USERS: list[dict[str, str]] = [
    {"name": "Sahithi", "username": "sahithi", "role": "admin"},
    {"name": "Nithya", "username": "nithya", "role": "teacher"},
]

# The CSV names teachers by username, and case is not the proofreader's problem.
TEACHERS = {u["username"] for u in USERS}
# No SLOTS set. §3.2: slot is free text, not an enum — the real roster holds
# seven distinct timings, so anything that validated against a fixed list would
# reject the whole file.
REQUIRED = ("roll_no", "name", "teacher", "slot", "enrollment_date", "class")


def validate(rows: list[dict[str, str]]) -> list[str]:
    """Every problem in the batch as readable lines. Empty list means safe to write.

    Collects all problems rather than dying on the first one: fixing a CSV at one
    error per run is what makes people give up and hand-edit the database instead.
    """
    problems: list[str] = []
    first_seen: dict[int, int] = {}  # roll number -> the line that claimed it

    for line, row in enumerate(rows, start=2):  # line 1 is the CSV header
        def field(name: str) -> str:
            return (row.get(name) or "").strip()

        for name in REQUIRED:
            if not field(name):
                problems.append(f"line {line}: {name} is blank")

        for name, value in row.items():
            if "???" in (value or ""):
                problems.append(f"line {line}: {name} still holds a ??? placeholder")

        roll = field("roll_no")
        if roll:
            # Stored as an integer so .sort("roll_no") puts 2 before 10. isascii()
            # keeps out the digits isdigit() accepts but int() rejects, like "²".
            number = int(roll) if roll.isascii() and roll.isdigit() else None
            if number is None or number < 1:
                problems.append(
                    f"line {line}: roll_no {roll!r} is not a positive whole number"
                )
            elif number in first_seen:
                problems.append(
                    f"line {line}: roll_no {roll} already used on line {first_seen[number]}"
                )
            else:
                first_seen[number] = line

        # Matched lowercase against users.username, so "Sahithi" and "sahithi"
        # are the same teacher.
        teacher = field("teacher").lower()
        if teacher and teacher not in TEACHERS:
            problems.append(
                f"line {line}: unknown teacher {teacher!r}, expected one of {sorted(TEACHERS)}"
            )

        date = field("enrollment_date")
        if date:
            # strptime rejects both the wrong shape and impossible days like 2026-02-30.
            try:
                datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                problems.append(
                    f"line {line}: enrollment_date {date!r} is not a real YYYY-MM-DD date"
                )

    return problems


def seed(rows: list[dict[str, str]]) -> None:
    # Imported here rather than at module top: db.py opens the connection on
    # import, and the point of this script is that validation happens first.
    from pymongo import ReturnDocument

    from db import db

    now = datetime.now(IST)

    # §11 item 5 — unique indexes exist before the first write, not after.
    db.users.create_index("username", unique=True)
    db.students.create_index("roll_no", unique=True)
    db.students.create_index([("teacher_id", 1), ("is_active", 1)])
    db.entries.create_index([("student_id", 1), ("date", 1)], unique=True)

    teacher_ids: dict[str, Any] = {}
    for user in USERS:
        doc = db.users.find_one_and_update(
            {"username": user["username"]},
            {
                "$set": {"name": user["name"], "role": user["role"]},
                "$setOnInsert": {"is_active": True, "created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        # upsert=True with ReturnDocument.AFTER always returns a document;
        # the narrowing is for the type checker, not a real branch.
        assert doc is not None
        teacher_ids[user["username"]] = doc["_id"]

    inserted = updated = 0
    for row in rows:
        teacher_id = teacher_ids[row["teacher"].strip().lower()]
        result = db.students.update_one(
            # int, not str. validate() has already proved this parses.
            {"roll_no": int(row["roll_no"].strip())},
            {
                "$set": {
                    "name": row["name"].strip(),
                    "teacher_id": teacher_id,
                    "class": row["class"].strip(),
                    "slot": row["slot"].strip(),
                    "enrollment_date": row["enrollment_date"].strip(),
                    "parent_phone": (row.get("parent_phone") or "").strip() or None,
                    "notes": (row.get("notes") or "").strip() or None,
                    "updated_at": now,
                },
                # Never in $set. Re-running the seed must not revive a student who
                # was deactivated, nor wipe the date they left on — §4.5 needs
                # deactivated_at to answer "was this student enrolled on 15 July?"
                "$setOnInsert": {
                    "is_active": True,
                    "deactivated_at": None,
                    "onboarded_by": teacher_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        if result.upserted_id:
            inserted += 1
        else:
            updated += 1

    print(f"users:    {len(USERS)} seeded")
    print(f"students: {inserted} inserted, {updated} updated")


def _self_check() -> None:
    good: dict[str, str] = {
        "roll_no": "1",
        "name": "Aditya Reddy",
        "teacher": "sahithi",
        "slot": "6 - 8:30",
        "enrollment_date": "2026-08-01",
        "class": "10",
    }
    assert validate([good]) == []
    assert validate([{**good, "teacher": "Sahithi"}]) == [], "teacher case must not matter"
    # §3.2 — free text. A fixed list of timings would reject the real roster.
    assert validate([{**good, "slot": "8 - 10"}]) == [], "any slot must be accepted"
    assert validate([good, good]), "duplicate roll_no must be caught"
    assert validate([{**good, "name": ""}]), "blank required field must be caught"
    assert validate([{**good, "class": ""}]), "blank class must be caught"
    assert validate([{**good, "name": "???"}]), "placeholder marker must be caught"
    assert validate([{**good, "teacher": "Priya"}]), "unknown teacher must be caught"
    assert validate([{**good, "roll_no": "R001"}]), "non-numeric roll_no must be caught"
    assert validate([{**good, "roll_no": "0"}]), "roll_no must be positive"
    assert validate([{**good, "enrollment_date": "01-07-2026"}]), "wrong date shape"
    assert validate([{**good, "enrollment_date": "2026-02-30"}]), "impossible date"
    print("validate(): all checks pass")


def main() -> None:
    if "--self-check" in sys.argv:
        _self_check()
        return

    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = paths[0] if paths else "students.csv"
    with open(path, newline="", encoding="utf-8") as f:
        # DictReader is typed as dict[str | Any, str | Any]; the header row is
        # plain strings, so pin it to what it actually is.
        rows: list[dict[str, str]] = [dict(row) for row in csv.DictReader(f)]

    problems = validate(rows)
    if problems:
        print(f"{len(problems)} problem(s) in {path}. Nothing was written.\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)

    # Stops here, before seed() imports db — so --dry-run never opens a connection.
    if "--dry-run" in flags:
        print(f"{len(rows)} rows validated. Nothing was written.")
        return

    seed(rows)


if __name__ == "__main__":
    main()
