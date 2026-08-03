import os
import secrets
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from bson import ObjectId
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# Importing db also loads .env and fails a missing or malformed MONGO_URI at
# startup rather than on the first query.
from db import db
from ist import IST, today_ist

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
    return RedirectResponse("/entries", status_code=303)


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
    # missing from the map, which is what renders the row grey.
    existing = db.entries.find(
        {"date": date, "student_id": {"$in": [s["_id"] for s in students]}}
    )
    status_by_id = {e["student_id"]: e["status"] for e in existing}
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
            "status_by_id": status_by_id,
        },
    )


@app.post("/entries")
def save_entry(
    request: Request,
    user: Annotated[dict[str, Any], Depends(current_user)],
    student_id: Annotated[str, Form()],
    date: Annotated[str, Form()],
    # Literal rejects anything that is not exactly one of these two, with a 422,
    # before the handler body runs. §3.4: unmarked is the absence of a document,
    # not a third status, so there is no value here that means "not marked".
    status: Annotated[Literal["present", "absent"], Form()],
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

    now = datetime.now(IST)
    db.entries.update_one(
        # §3.4 compound key. On insert Mongo copies these two fields out of the
        # filter into the new document, so they never need setting explicitly.
        {"student_id": student["_id"], "date": date},
        {
            "$set": {"status": status, "teacher_id": user["_id"], "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    # saved=True is what draws the tick. A GET never sets it, so the tick means
    # "just written", not "has a value".
    return templates.TemplateResponse(
        request,
        "row.html",
        {"student": student, "date": date, "status": status, "saved": True},
    )
