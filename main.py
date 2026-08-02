from datetime import datetime
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Importing db also fails a missing or malformed MONGO_URI at startup rather
# than on the first query.
from db import db
from ist import IST, today_ist

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# v0.5 stands in for login (§8 item 7). Replaced by the session user in v1.
TEACHER_ID = ObjectId("6a6e1a8cb67feefbf03c2404")  # Sahithi


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/entries")
def entries_today():
    """Bare /entries lands on today, so the URL always names the date being written to."""
    return RedirectResponse(f"/entries/{today_ist()}")


@app.get("/entries/{date}")
def entries(request: Request, date: str):
    students = list(db.students.find({"teacher_id": TEACHER_ID, "is_active": True}).sort("roll_no"))
    # One extra query, not one per row. A student with no document here is simply
    # missing from the map, which is what renders the row grey.
    existing = db.entries.find({"date": date, "student_id": {"$in": [s["_id"] for s in students]}})
    status_by_id = {e["student_id"]: e["status"] for e in existing}
    return templates.TemplateResponse(
        request,
        "entries.html",
        {"date": date, "students": students, "status_by_id": status_by_id},
    )


@app.post("/entries")
def save_entry(
    request: Request,
    student_id: Annotated[str, Form()],
    date: Annotated[str, Form()],
    # Literal rejects anything that is not exactly one of these two, with a 422,
    # before the handler body runs. §3.4: unmarked is the absence of a document,
    # not a third status, so there is no value here that means "not marked".
    status: Annotated[Literal["present", "absent"], Form()],
):
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
            "$set": {"status": status, "teacher_id": TEACHER_ID, "updated_at": now},
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
