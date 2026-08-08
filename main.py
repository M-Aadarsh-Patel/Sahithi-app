import os
import secrets
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from bson import ObjectId
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.middleware.sessions import SessionMiddleware

# Importing db also loads .env and fails a missing or malformed MONGO_URI at
# startup rather than on the first query.
from db import db
from ist import IST, today_ist

# The onboarding form is one CSV row by another name — same field names, same
# rules — so it validates through the same function the seed script does rather
# than growing a second, drifting copy of "what is a valid student".
from seed_students import validate

# validate() writes for whoever is proofreading a CSV, in the CSV's own column
# names. On the form those are not words anyone has seen: the label above the box
# reads "Roll number", never "roll_no". Same check, said in the form's language.
FIELD_LABELS = {
    "roll_no": "Roll number",
    "name": "Name",
    "class": "Class",
    "slot": "Slot",
    "enrollment_date": "Enrollment date",
}


def readable(problem: str) -> tuple[str | None, str]:
    """One validate() line as (field it is about, sentence to show her).

    Only three of validate()'s shapes can reach a form: a blank required field, a
    roll number that is not a number, and a date that is not a date. The rest —
    duplicate rows, an unknown teacher, a ??? marker — need a CSV to happen. An
    unrecognised shape is passed through rather than mangled into a guess.
    """
    field = problem.split(" ", 1)[0]
    label = FIELD_LABELS.get(field)
    if label is None:
        return None, problem[:1].upper() + problem[1:]
    if problem.endswith("is blank"):
        return field, f"{label} is required."
    if "is not a positive whole number" in problem:
        return field, f"{label} must be a whole number, like 42."
    if "is not a real" in problem:
        return field, f"{label} is not a real date."
    return field, f"{label}: {problem.split(' ', 1)[1]}"

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is not set")

app = FastAPI()
# Starlette always sets httponly on this cookie. max_age=None omits Max-Age
# altogether, making it a browser-session cookie instead of one that survives a
# fixed 12 hours. It also stops itsdangerous expiring the signature by age, so
# IDLE_TIMEOUT below is the only thing that ends a session.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=None,
    https_only=os.environ.get("RENDER") is not None,
)
templates = Jinja2Templates(directory="templates")
# A mount, not a route: nothing under /static goes through current_user, which is
# what the login page needs — it shows the logo before anyone has a session.
# Relative path, matching Jinja2Templates above; both resolve against the working
# directory Render starts uvicorn from.
app.mount("/static", StaticFiles(directory="static"), name="static")

# Phones routinely keep session cookies alive across a tab close, and some restore
# them after a reboot, so the cookie's own lifetime protects nothing on the device
# this app is used on. Inactivity is what ends a session.
IDLE_TIMEOUT = timedelta(minutes=30)


def parse_date(raw: str) -> date:
    """A date string from the URL, as a real date. Rejects anything unusable.

    Only the canonical "YYYY-MM-DD" spelling passes. strptime on its own also
    accepts "2026-7-2", and entries are keyed by this exact string — a second
    spelling of the same day would quietly key a second document.
    """
    day: date | None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        day = None
    if day is None or day.isoformat() != raw:
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    # Both sides are canonical ISO now, so comparing strings compares dates.
    if raw > today_ist():  # §4.2 — future is blocked for everyone
        raise HTTPException(status_code=400, detail="That date is in the future")
    return day


def login_required(request: Request) -> HTTPException:
    """The right flavour of "go and log in" for whoever is asking.

    htmx swaps a response body into one row, so a plain 303 would follow the
    redirect and drop the entire login page inside a single student's <li>.
    HX-Redirect tells htmx to navigate the whole page instead — it acts on that
    header before it looks at the status or swaps anything, so nothing is written
    into the row on the way out.

    For an ordinary page load, 303 rather than 307 so the browser retries as a
    GET — a 307 would re-send a POST body to /login. /health deliberately does
    not depend on any of this: the §11 cron pinger has no session.
    """
    if request.headers.get("HX-Request"):
        return HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
    return HTTPException(status_code=303, headers={"Location": "/login"})


def current_user(request: Request) -> dict[str, Any]:
    """The logged-in user, or a redirect to /login.

    Every authenticated request stamps last_seen, so the 30 minutes is idle time,
    not time since login — she is never signed out mid-entry while working.
    """
    now = datetime.now(IST)
    last_seen = request.session.get("last_seen")
    if last_seen and now - datetime.fromisoformat(last_seen) > IDLE_TIMEOUT:
        # Clearing means the stale cookie is actively deleted rather than left to
        # be re-rejected on every later request.
        request.session.clear()
        raise login_required(request)

    user_id = request.session.get("user_id")
    user = db.users.find_one({"_id": ObjectId(user_id)}) if user_id else None
    if user is None:
        request.session.clear()
        raise login_required(request)

    request.session["last_seen"] = now.isoformat()
    return user


async def submitted_fields(request: Request) -> set[str]:
    """Which field names the request actually carried.

    FastAPI cannot answer this on its own. For a Form parameter with a default it
    treats an empty value as missing and hands back the default, so "" and "not
    sent" both arrive as None — and clearing a topic would be indistinguishable
    from a status save that never mentioned it. Starlette caches the parsed form,
    so reading it here costs nothing extra.

    async because request.form() is, while save_entry stays sync so its blocking
    pymongo calls keep running in the threadpool rather than on the event loop.
    """
    return set((await request.form()).keys())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login")
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    user = db.users.find_one({"username": username, "is_active": True})
    expected = os.environ.get(f"{username.upper()}_PASSWORD", "")
    # compare_digest is constant time, where == returns early on the first wrong
    # byte. §4.4: one message for every failure, so an unknown username and a
    # wrong password are indistinguishable from outside.
    if user is None or not expected or not secrets.compare_digest(password, expected):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect username or password."},
            status_code=401,
        )
    # Signed by SESSION_SECRET, so the browser can read this but not forge it.
    request.session["user_id"] = str(user["_id"])
    # Started here, not on the first page load. Otherwise a login left untouched
    # overnight would still be valid in the morning, because the first request
    # would find no last_seen to measure against and simply stamp a fresh one.
    request.session["last_seen"] = datetime.now(IST).isoformat()
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request) -> RedirectResponse:
    """No current_user dependency on purpose: logging out of an already-expired
    session must still clear the cookie rather than bounce off the guard."""
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/dashboard")
def dashboard(
    request: Request, user: Annotated[dict[str, Any], Depends(current_user)]
) -> Response:
    # The one new query. count_documents rather than fetching the roster, since
    # only the number is shown — it is served straight off the
    # (teacher_id, is_active) index the seed script creates.
    student_count = db.students.count_documents(
        {"teacher_id": user["_id"], "is_active": True}
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "student_count": student_count,
            # Same IST clock and the same format the entries date bar shows, so
            # the two screens never disagree about what day it is.
            "today": datetime.now(IST).strftime("%a %d %b"),
        },
    )


def student_form(
    request: Request,
    user: dict[str, Any],
    *,
    form: dict[str, str] | None = None,
    problems: list[str] | None = None,
    invalid: set[str] | None = None,
    added: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """The onboarding page in each of its three states: blank, rejected, just saved.

    `form` is what she typed, echoed back so a rejected save never costs her the
    other six fields. `invalid` names the fields the problems are about, so the
    banner is not the only thing pointing at them — reading five sentences and
    then hunting seven boxes for the one they mean is the failure this avoids.
    """
    return templates.TemplateResponse(
        request,
        "students_new.html",
        {
            "user": user,
            # Both the date field's default and its max — §4.2 blocks the future
            # everywhere else in the app, and an enrollment date is no different.
            "today": today_ist(),
            # Suggestions for the two datalists, not constraints. §3.2 is explicit
            # that slot is free text and never an enum, and class follows it: the
            # roster may hold a value nobody has typed yet.
            #
            # Sorted by length first, so numeric classes run 6, 7, 8, 9, 10 rather
            # than the 10, 11, 12, 6 a plain string sort gives.
            "classes": sorted(db.students.distinct("class"), key=lambda c: (len(c), c)),
            "slots": sorted(db.students.distinct("slot")),
            "form": form or {},
            "problems": problems or [],
            "invalid": invalid or set(),
            "added": added,
        },
        status_code=status_code,
    )


@app.get("/students/new")
def new_student(
    request: Request, user: Annotated[dict[str, Any], Depends(current_user)]
) -> Response:
    # Popped, not read: the success modal belongs to the one page load that
    # followed the save. A refresh afterwards is an ordinary blank form.
    return student_form(request, user, added=request.session.pop("added", None))


@app.post("/students/new")
def create_student(
    request: Request,
    user: Annotated[dict[str, Any], Depends(current_user)],
    # str, not int. An int here would hand a typo to FastAPI, which answers with a
    # 422 of JSON — a dead end on a phone. Taken as text, a bad roll number comes
    # back as a sentence on the form she is already looking at.
    #
    # Every one of these defaults to "", including the five that are required, for
    # the same reason. FastAPI reads an empty form value as *missing* (the gotcha
    # submitted_fields() documents), so a required parameter with no default 422s
    # on a blank box before this function runs — which would make validate()'s
    # blank checks dead code and answer a cleared field with raw JSON. Defaulted,
    # a blank arrives as "" and comes back as "Name is required." on the form.
    roll_no: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    # `class` is a Python keyword, so the parameter cannot carry the field's own
    # name. The alias is what keeps the form field named for the thing it stores.
    student_class: Annotated[str, Form(alias="class")] = "",
    slot: Annotated[str, Form()] = "",
    enrollment_date: Annotated[str, Form()] = "",
    parent_phone: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    row = {
        "roll_no": roll_no.strip(),
        "name": name.strip(),
        "class": student_class.strip(),
        "slot": slot.strip(),
        "enrollment_date": enrollment_date.strip(),
        # §5.5 — the student is this teacher's. Not a form field, so it cannot be
        # anything else; validate() wants the key, and it is hers by construction.
        "teacher": user["username"],
    }

    # validate() reports against CSV line numbers, which mean nothing on a form,
    # so the prefix goes and readable() says the rest in the form's own words.
    problems: list[str] = []
    invalid: set[str] = set()
    for raw in validate([row]):
        field, sentence = readable(raw.split(": ", 1)[1])
        problems.append(sentence)
        if field:
            invalid.add(field)
    # The one rule the seed script has no reason to hold: a CSV of past enrollments
    # is ordinary, a teacher enrolling someone next Tuesday is not. Both sides are
    # canonical ISO by now — validate() proved the shape — so string order is date
    # order, and a malformed date has already been caught above rather than
    # tripping this a second time.
    if row["enrollment_date"] > today_ist():
        problems.append("Enrollment date cannot be in the future.")
        invalid.add("enrollment_date")

    if problems:
        return student_form(
            request, user, form=row, problems=problems, invalid=invalid, status_code=400
        )

    now = datetime.now(IST)
    # The same document seed_students.py writes, field for field, so a student
    # added here and a student seeded from the CSV are the same kind of thing.
    student = {
        # int, exactly as the seed stores it. validate() has already proved this
        # parses, and .sort("roll_no") needs it to put 2 before 10.
        "roll_no": int(row["roll_no"]),
        "name": row["name"],
        "teacher_id": user["_id"],
        "class": row["class"],
        "slot": row["slot"],
        "enrollment_date": row["enrollment_date"],
        # None, never "". §3.2 has these as optional fields, and an empty string is
        # a value that claims a phone number was recorded.
        "parent_phone": parent_phone.strip() or None,
        "notes": notes.strip() or None,
        "is_active": True,
        # §3.2 — nullable from the first write. is_active alone cannot answer "was
        # this student enrolled on 15 July?"
        "deactivated_at": None,
        "onboarded_by": user["_id"],
        "created_at": now,
        "updated_at": now,
    }
    try:
        db.students.insert_one(student)
    except DuplicateKeyError:
        # The unique index on roll_no is the authority, so this asks it rather than
        # checking first and hoping nothing lands in between. Naming who holds the
        # number is what makes the message actionable — the query runs only here.
        taken = db.students.find_one({"roll_no": student["roll_no"]})
        held_by = f" already belongs to {taken['name']}" if taken else " is already in use"
        return student_form(
            request,
            user,
            form=row,
            problems=[f"Roll number {row['roll_no']}{held_by}."],
            invalid={"roll_no"},
            status_code=400,
        )

    # Post/Redirect/Get. Rendering the modal straight from this POST would make a
    # refresh offer to add the student a second time. Through the session rather
    # than the query string, so a child's name never enters a URL or the browser's
    # history — the same care .gitignore takes with students.csv.
    request.session["added"] = {
        "name": student["name"],
        "roll_no": student["roll_no"],
        "class": student["class"],
        # Formatted here rather than in the template: the same "%d %b" the
        # dashboard and the date bar use, so every date in the app reads alike.
        "enrollment_date": datetime.strptime(row["enrollment_date"], "%Y-%m-%d").strftime("%d %b"),
    }
    return RedirectResponse("/students/new", status_code=303)


@app.get("/entries")
def entries_today() -> RedirectResponse:
    """Bare /entries lands on today, so the URL always names the date being written to."""
    return RedirectResponse(f"/entries/{today_ist()}")


@app.get("/entries/{date}")
def entries(
    request: Request, date: str, user: Annotated[dict[str, Any], Depends(current_user)]
) -> Response:
    day = parse_date(date)
    students = list(
        db.students.find({"teacher_id": user["_id"], "is_active": True}).sort("roll_no")
    )
    # One extra query, not one per row. A student with no document here is simply
    # missing from the map, which is what renders the row grey and its fields
    # empty. The whole document is kept now, not just the status, because the row
    # renders the topics and the remark too.
    existing = db.entries.find(
        {"date": date, "student_id": {"$in": [s["_id"] for s in students]}}
    )
    entry_by_id = {e["student_id"]: e for e in existing}
    return templates.TemplateResponse(
        request,
        "entries.html",
        {
            "date": date,
            "formatted": day.strftime("%a %d %b"),
            "prev_date": (day - timedelta(days=1)).isoformat(),
            "next_date": (day + timedelta(days=1)).isoformat(),
            "today": today_ist(),
            "students": students,
            "entry_by_id": entry_by_id,
        },
    )


@app.post("/entries")
def save_entry(
    request: Request,
    user: Annotated[dict[str, Any], Depends(current_user)],
    student_id: Annotated[str, Form()],
    date: Annotated[str, Form()],
    # Which keys were really in the body. See submitted_fields — these four all
    # arrive as None when cleared, so the values alone cannot say what was sent.
    present: Annotated[set[str], Depends(submitted_fields)],
    # Every field below is optional because each control saves on its own: the
    # toggle sends status and nothing else, a topic input sends that topic and
    # nothing else.
    #
    # Literal still rejects anything that is not exactly one of these two, with a
    # 422 before the handler body runs. §3.4: unmarked is the absence of a
    # document, not a third status, so there is no value here meaning "unmarked".
    status: Annotated[Literal["present", "absent"] | None, Form()] = None,
    slot_1: Annotated[str | None, Form()] = None,
    slot_2: Annotated[str | None, Form()] = None,
    remark: Annotated[str | None, Form()] = None,
    # §3.4 — nullable integers. int|None rather than str, so the coercion is
    # Pydantic's; the inputs are type=number, so the browser will not submit
    # anything that could fail it.
    score: Annotated[int | None, Form()] = None,
    max_marks: Annotated[int | None, Form()] = None,
) -> Response:
    # Guard only — the write keys off the raw string. Runs before the student
    # lookup so a bad date costs no query.
    parse_date(date)

    student = (
        db.students.find_one({"_id": ObjectId(student_id)})
        if ObjectId.is_valid(student_id)
        else None
    )
    if student is None:
        raise HTTPException(status_code=404, detail="No such student")

    # Only what the request actually carried. A topic save therefore cannot
    # overwrite status, and a status save cannot wipe a topic, because a field
    # nobody sent never reaches $set at all.
    changes: dict[str, Any] = {}
    for field, value in (("slot_1", slot_1), ("slot_2", slot_2), ("remark", remark)):
        if field in present:
            # None here means she emptied the box, which is a real edit. Blank is
            # never validated and never rejected — §6, topics are free text.
            changes[field] = value or ""
    # Nullable, so an emptied box writes null rather than "". Same presence rule:
    # a request that never mentions score cannot wipe one.
    for field, value in (("score", score), ("max_marks", max_marks)):
        if field in present:
            changes[field] = value
    # Not keyed off `present`: an empty status is not a state a row can be in, so
    # it is written only when it arrived as one of the two literals.
    if status is not None:
        changes["status"] = status
    # Absent clears the rest of the row. Those five fields are only on screen for
    # a present student, so a topic or a score left behind on an absent row would
    # be data the app holds and can no longer show or reach.
    #
    # After the two loops above, so a request carrying both an absent status and
    # a field value resolves to the clear. "" and None split the same way they do
    # up there: topics and the remark are free text that empties to "", the two
    # marks are nullable and empty to None.
    if status == "absent":
        changes.update(
            {"slot_1": "", "slot_2": "", "remark": "", "score": None, "max_marks": None}
        )

    if not changes:
        # Nothing to write. Without this an empty POST would upsert a document
        # holding no data at all, and §3.4 has no room for one.
        raise HTTPException(status_code=400, detail="Nothing to save")

    now = datetime.now(IST)
    entry = db.entries.find_one_and_update(
        # §3.4 compound key. On insert Mongo copies these two fields out of the
        # filter into the new document, so they never need setting explicitly.
        {"student_id": student["_id"], "date": date},
        {
            "$set": {**changes, "teacher_id": user["_id"], "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        # The merged document, in the same round trip. The row renders every
        # field, so it needs the fields this request did not touch as well.
        return_document=ReturnDocument.AFTER,
    )
    # saved=True is what draws the tick. A GET never sets it, so the tick means
    # "just written", not "has a value".
    return templates.TemplateResponse(
        request,
        "row.html",
        {"student": student, "date": date, "entry": entry, "saved": True},
    )
