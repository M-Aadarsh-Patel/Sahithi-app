"""Marking a student absent clears the rest of their row.

The five detail fields — slot_1, slot_2, remark, score, max_marks — are only on
screen for a present student. If marking absent left them in the document, the
app would be holding data that nothing in the UI can show or reach, which is the
whole reason the fields used to sit behind a "Details" disclosure instead.

**This one writes**, unlike test_session.py. It creates its own throwaway student,
keys every entry to that student's id on a date far outside real use, and deletes
both at the end — so it never touches a real roster or a real day. The cleanup is
asserted, not assumed.

    python test_absent_clears.py
"""

import sys
from typing import Any

from bson import ObjectId

from test_session import forge, free_port, request, start_server
from db import db
from itsdangerous import TimestampSigner
import os

TEST_DATE = "2020-01-01"
FIELDS = ("slot_1", "slot_2", "remark", "score", "max_marks")

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def entry_for(student_id: ObjectId) -> dict[str, Any] | None:
    return db.entries.find_one({"student_id": student_id, "date": TEST_DATE})


def run_checks(base: str, student_id: ObjectId, cookie: str) -> None:
    post = lambda data: request(
        base, "POST", "/entries", cookie=cookie,
        data={"student_id": str(student_id), "date": TEST_DATE, **data},
    )

    print("1. a present student with every field filled")
    status, _, _ = post({"status": "present"})
    check("marked present", status == 200, f"status={status}")
    for field, value in (("slot_1", "Algebra"), ("slot_2", "Geometry"),
                         ("remark", "arrived late"), ("score", "7"), ("max_marks", "10")):
        code, _, _ = post({field: value})
        check(f"saved {field}", code == 200, f"status={code}")

    entry = entry_for(student_id)
    assert entry is not None, "the entry was never created"
    check("all five are in the document", all(entry.get(f) not in (None, "") for f in FIELDS),
          str({f: entry.get(f) for f in FIELDS}))
    check("score stored as an int", entry.get("score") == 7, f"score={entry.get('score')!r}")

    print("\n2. marking absent clears them")
    status, _, _ = post({"status": "absent"})
    check("marked absent", status == 200, f"status={status}")

    entry = entry_for(student_id)
    assert entry is not None
    check("status is absent", entry.get("status") == "absent")
    check("slot_1 cleared", entry.get("slot_1") == "", f"slot_1={entry.get('slot_1')!r}")
    check("slot_2 cleared", entry.get("slot_2") == "", f"slot_2={entry.get('slot_2')!r}")
    check("remark cleared", entry.get("remark") == "", f"remark={entry.get('remark')!r}")
    check("score nulled", entry.get("score") is None, f"score={entry.get('score')!r}")
    check("max_marks nulled", entry.get("max_marks") is None, f"max={entry.get('max_marks')!r}")

    print("\n3. a status save still cannot wipe a field on its own")
    post({"status": "present"})
    post({"slot_1": "Trigonometry"})
    post({"status": "present"})
    entry = entry_for(student_id)
    assert entry is not None
    check("present does not clear", entry.get("slot_1") == "Trigonometry",
          f"slot_1={entry.get('slot_1')!r}")


def main() -> None:
    student_id = db.students.insert_one(
        {"name": "ZZ Test Student", "roll_no": 99999, "is_test_fixture": True}
    ).inserted_id
    print(f"created throwaway student {student_id}\n")

    port = free_port()
    server = start_server(port)
    try:
        signer = TimestampSigner(str(os.environ["SESSION_SECRET"]))
        sahithi = db.users.find_one({"username": "sahithi"})
        assert sahithi is not None, "seed the users first: python seed_students.py"
        run_checks(f"http://127.0.0.1:{port}", student_id, forge(signer, sahithi["_id"], 0))
    finally:
        server.terminate()
        server.wait(timeout=10)
        entries = db.entries.delete_many({"student_id": student_id}).deleted_count
        students = db.students.delete_one({"_id": student_id}).deleted_count
        print("\n4. cleanup")
        check("throwaway entries deleted", db.entries.count_documents(
            {"student_id": student_id}) == 0, f"removed {entries}")
        check("throwaway student deleted", db.students.count_documents(
            {"_id": student_id}) == 0, f"removed {students}")

    print("\nALL PASS" if ok else "\nFAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
