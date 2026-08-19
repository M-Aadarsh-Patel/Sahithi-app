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

from db import db
from ist import IST, today_ist

from seed_students import validate

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
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=None,
    https_only=os.environ.get("RENDER") is not None,
)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

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
    if raw > today_ist():
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
    if user is None or not expected or not secrets.compare_digest(password, expected):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Incorrect username or password."},
            status_code=401,
        )
    request.session["user_id"] = str(user["_id"])
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
    student_count = db.students.count_documents(
        {"teacher_id": user["_id"], "is_active": True}
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "student_count": student_count,
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
            "today": today_ist(),
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
    return student_form(request, user, added=request.session.pop("added", None))


@app.post("/students/new")
def create_student(
    request: Request,
    user: Annotated[dict[str, Any], Depends(current_user)],
    roll_no: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
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
        "teacher": user["username"],
    }

    problems: list[str] = []
    invalid: set[str] = set()
    for raw in validate([row]):
        field, sentence = readable(raw.split(": ", 1)[1])
        problems.append(sentence)
        if field:
            invalid.add(field)
    if row["enrollment_date"] > today_ist():
        problems.append("Enrollment date cannot be in the future.")
        invalid.add("enrollment_date")

    if problems:
        return student_form(
            request, user, form=row, problems=problems, invalid=invalid, status_code=400
        )

    now = datetime.now(IST)
    student = {
        "roll_no": int(row["roll_no"]),
        "name": row["name"],
        "teacher_id": user["_id"],
        "class": row["class"],
        "slot": row["slot"],
        "enrollment_date": row["enrollment_date"],
        "parent_phone": parent_phone.strip() or None,
        "notes": notes.strip() or None,
        "is_active": True,
        "deactivated_at": None,
        "onboarded_by": user["_id"],
        "created_at": now,
        "updated_at": now,
    }
    try:
        db.students.insert_one(student)
    except DuplicateKeyError:
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

    request.session["added"] = {
        "name": student["name"],
        "roll_no": student["roll_no"],
        "class": student["class"],
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
    present: Annotated[set[str], Depends(submitted_fields)],
    status: Annotated[Literal["present", "absent"] | None, Form()] = None,
    slot_1: Annotated[str | None, Form()] = None,
    slot_2: Annotated[str | None, Form()] = None,
    remark: Annotated[str | None, Form()] = None,
    score: Annotated[int | None, Form()] = None,
    max_marks: Annotated[int | None, Form()] = None,
) -> Response:
    parse_date(date)

    student = (
        db.students.find_one({"_id": ObjectId(student_id)})
        if ObjectId.is_valid(student_id)
        else None
    )
    if student is None:
        raise HTTPException(status_code=404, detail="No such student")

    changes: dict[str, Any] = {}
    for field, value in (("slot_1", slot_1), ("slot_2", slot_2), ("remark", remark)):
        if field in present:
            changes[field] = value or ""
    for field, value in (("score", score), ("max_marks", max_marks)):
        if field in present:
            changes[field] = value
    if status is not None:
        changes["status"] = status
    if status == "absent":
        changes.update(
            {"slot_1": "", "slot_2": "", "remark": "", "score": None, "max_marks": None}
        )

    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to save")

    now = datetime.now(IST)
    entry = db.entries.find_one_and_update(
        {"student_id": student["_id"], "date": date},
        {
            "$set": {**changes, "teacher_id": user["_id"], "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return templates.TemplateResponse(
        request,
        "row.html",
        {"student": student, "date": date, "entry": entry, "saved": True},
    )
