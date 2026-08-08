"""Onboarding a student writes the same document the seed script writes.

The whole point of the form is that a student added from a phone and a student
seeded from the CSV are the same kind of thing. If they drift, §4.5's membership
query and the attendance denominator start disagreeing about students depending
on how they arrived — which is the sort of bug nobody finds for a month.

**This writes**, like test_absent_clears.py. Every document it creates is keyed
to a roll number far outside the real roster's 1-41, and it deletes them at the
end. The cleanup is asserted, not assumed.

    python test_onboard.py
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Any

from itsdangerous import TimestampSigner

from db import db
from ist import IST, today_ist
# Reuse the harness rather than restating it: same free port, same real HTTP,
# same signed-cookie forgery. Importing is safe — test_session guards its own
# entry point behind __main__.
from test_session import forge, free_port, request, start_server

ROLL = "99001"  # far outside the real roster
FUTURE = (datetime.now(IST) + timedelta(days=3)).strftime("%Y-%m-%d")

ok = True


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok
    ok = ok and condition
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def form(**overrides: str) -> dict[str, str]:
    return {
        "roll_no": ROLL,
        "name": "ZZ Test Student",
        "class": "10",
        "slot": "6 - 9",
        "enrollment_date": today_ist(),
        "parent_phone": "",
        "notes": "",
        **overrides,
    }


def session_cookie(headers: dict[str, str], fallback: str) -> str:
    """The session cookie out of a Set-Cookie header, if the response set one.

    The success redirect carries the flash in this cookie, so following it with
    the old one would land on a blank form and the modal would never be seen.
    """
    raw = headers.get("set-cookie", "")
    if not raw.startswith("session="):
        return fallback
    return raw.split(";", 1)[0].removeprefix("session=")


def run_checks(base: str, cookie: str, teacher: dict[str, Any]) -> None:
    def post(data: dict[str, str]) -> tuple[int, dict[str, str], str]:
        return request(base, "POST", "/students/new", cookie=cookie, data=data)

    print("1. the form writes one student")
    status, headers, _ = post(form())
    check("redirects rather than rendering", status == 303, f"status={status}")
    check("back to the form", headers.get("location") == "/students/new")
    check("exactly one document", db.students.count_documents({"roll_no": int(ROLL)}) == 1)

    student = db.students.find_one({"roll_no": int(ROLL)})
    assert student is not None, "the student was never created"

    print("\n2. it is the document the seed script writes")
    seeded = db.students.find_one({"roll_no": 1})
    assert seeded is not None, "seed the roster first: python seed_students.py students.csv"
    check("same fields as a seeded student",
          sorted(student.keys()) == sorted(seeded.keys()),
          f"extra={sorted(set(student) - set(seeded))} missing={sorted(set(seeded) - set(student))}")
    # A roll number stored as a string sorts "10" before "2", which would quietly
    # scramble the register.
    check("roll_no is an int", isinstance(student["roll_no"], int), f"{student['roll_no']!r}")
    check("is_active true", student["is_active"] is True)
    check("deactivated_at null", student["deactivated_at"] is None)
    check("assigned to the logged-in teacher", student["teacher_id"] == teacher["_id"])
    check("onboarded_by the same", student["onboarded_by"] == teacher["_id"])
    check("enrollment_date is today", student["enrollment_date"] == today_ist(),
          f"{student['enrollment_date']!r}")
    # "" would claim a phone number was recorded. §3.2 has both as optional.
    check("blank phone stored as None", student["parent_phone"] is None,
          f"{student['parent_phone']!r}")
    check("blank notes stored as None", student["notes"] is None, f"{student['notes']!r}")

    print("\n3. the success modal shows once, then never again")
    after = session_cookie(headers, cookie)
    _, headers, body = request(base, "GET", "/students/new", cookie=after)
    check("modal rendered on the redirect target", "Welcome to HR Academy!" in body)
    check("the student is named back", "ZZ Test Student" in body)
    # Carrying the cookie forward is the whole test: the flash is popped from the
    # session, so it is the *updated* cookie that no longer holds it. Replaying
    # the old one is not a refresh, it is a different browser.
    _, _, body = request(base, "GET", "/students/new", cookie=session_cookie(headers, after))
    check("gone on a refresh", "Welcome to HR Academy!" not in body)

    print("\n4. a duplicate roll number is refused")
    status, _, body = post(form(name="ZZ Impostor"))
    check("rejected", status == 400, f"status={status}")
    check("names who holds it", "ZZ Test Student" in body)
    check("still exactly one document", db.students.count_documents({"roll_no": int(ROLL)}) == 1)
    check("the impostor was not written", db.students.count_documents({"name": "ZZ Impostor"}) == 0)
    # The banner alone leaves her hunting seven boxes for the one it means.
    check("the roll number field is marked", 'name="roll_no"' in body
          and 'aria-invalid="true"' in body.split('name="roll_no"')[1].split(">")[0])
    check("what she typed is still there", 'value="ZZ Impostor"' in body)

    print("\n5. a future enrollment date is refused")
    status, _, body = post(form(roll_no="99002", enrollment_date=FUTURE))
    check("rejected", status == 400, f"status={status}")
    check("says why", "future" in body.lower())
    check("nothing written", db.students.count_documents({"roll_no": 99002}) == 0)

    print("\n6. a roll number that is not a number is refused")
    status, _, body = post(form(roll_no="R014"))
    check("rejected rather than 422", status == 400, f"status={status}")
    check("nothing written", db.students.count_documents({"name": "ZZ Test Student"}) == 1)
    # validate() writes for a CSV proofreader, in the CSV's column names. The
    # label above the box on this form reads "Roll number" and never "roll_no".
    check("said in the form's own words", "Roll number must be a whole number" in body,
          "roll_no" if "roll_no is" in body or "roll_no '" in body else "")
    check("no raw field names leak through", "roll_no '" not in body)

    print("\n6b. a blank required field names the field, not the column")
    status, _, body = post(form(name="", roll_no="99003"))
    check("rejected", status == 400, f"status={status}")
    check("reads 'Name is required.'", "Name is required." in body)
    check("nothing written", db.students.count_documents({"roll_no": 99003}) == 0)


def main() -> None:
    port = free_port()
    server = start_server(port)
    try:
        signer = TimestampSigner(str(os.environ["SESSION_SECRET"]))
        nithya = db.users.find_one({"username": "nithya"})
        assert nithya is not None, "seed the users first: python seed_students.py"
        run_checks(f"http://127.0.0.1:{port}", forge(signer, nithya["_id"], 0), nithya)
    finally:
        server.terminate()
        server.wait(timeout=10)
        removed = db.students.delete_many({"roll_no": {"$in": [99001, 99002, 99003]}}).deleted_count
        print("\n7. cleanup")
        check("throwaway students deleted",
              db.students.count_documents({"roll_no": {"$in": [99001, 99002, 99003]}}) == 0,
              f"removed {removed}")

    print("\nALL PASS" if ok else "\nFAILURES ABOVE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
