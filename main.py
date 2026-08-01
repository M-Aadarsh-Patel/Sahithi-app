from bson import ObjectId
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Importing db also fails a missing or malformed MONGO_URI at startup rather
# than on the first query.
from db import db
from ist import today_ist

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
    students = db.students.find({"teacher_id": TEACHER_ID, "is_active": True}).sort("roll_no")
    return templates.TemplateResponse(
        request, "entries.html", {"date": date, "students": list(students)}
    )
