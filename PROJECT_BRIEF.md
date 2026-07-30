# HR Academy Coaching Center Student Records Interface

There are 2 users that are going to be using this program for now
2 Teachers: Sahithi (teacher, admin), Nithya (teacher)
We need to design the system to store the data of 100 students

This app is going to replace a pen and paper register, each evening a teacher records, for every student assigned
to her

##  Structure of the center
1. There are two batches of students, defined by time 6 to 8 pm and 8 to 10 pm, each batch runs two slots, slot 1 and slot 2

2. Each teacher is assigned a set of students who may come from either batch. Batch and teacher are independent, a student has exactly one teacher and exactly one batch.

3. Concequence of point 2, both teachers may have students in the same batch. Therefore slot topics and test details belong to the teacher, not to the batch, each teacher records what her own student covered. Tow teachers may record different topics for the same batch on the same date, and that is correct not a conflict.

4. Concsequence of point 2, holiday/ no-class status belongs to the batch, not to the teacher, a cancelled 6 to 8 class affects both teachers' students in that batch ans must be recorded once, shared.

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python + FastAPI | Auditable by the developer; single language |
| Templating | Jinja2, server-rendered | One codebase, one deploy, no build step, no CORS |
| Interactivity | HTMX | Per-row auto-save without writing JavaScript |
| Styling | Tailwind (CDN) | Good visual ceiling, no build pipeline |
| Database | MongoDB Atlas (free tier) | Ample for this data volume |
| Driver | pymongo (synchronous) | Simple, well understood |
| Auth | Starlette `SessionMiddleware`, signed cookie | 3 users; JWT is the wrong tool here |
| Passwords | `passlib[bcrypt]` | Never hand-roll hashing |


All FastAPI routes are declared `def` not `async def`, pymongo is synchronous; inside `async def` it blocks the event loop. FastAPI runs plain def routes in a threadpool automatically.

No React, no Next.js, no seperate front end project, no npm build step.

Pymongo connection string, session secret key and all passwords come from environment variables

## Data Model

Five collections, All dates are IST date strings in `DD-MM-YYYY` format never a datetime object, never UTC

### `USERS`:
```
{
  _id,
  username:       "sahithi" | "nithya" | "admin",
  display_name:   "Sahithi",
  password_hash:  "<bcrypt>",
  role:           "teacher" | "admin",
  active:         true,
}
```

Users will be seeded manually there will be no registration screen or login screen but every time the user closes the browser they will be required to enter their profile's password.

Teachers have no batch field.

### `STUDENTS`
 
```
{
  _id,
  roll_no:         "R014",                  // unique, human-facing identifier
  name:            "…",
  batch:           "6-8" | "8-10",
  teacher_id:      <users._id>,
  enrollment_date: "2026-07-14",            // IST date string
  status:          "active" | "left",
  left_date:       "2026-09-30" | null,
  created_at, created_by
}
```


`roll_no` is required and is unique setting the student's roll_no to _id would be better. Two students will share a first name, the roll number is what disambiguates them everywhere in the UI

`enrollment_date` is required. It is the start of the student's attendance denominator.

`teacher_id` and `batch` are editable after creation

### SESSIONS
ignore this for now

### ENTRIES

These are the things that the teacher is going to enter for each student every day.

```
{
  _id,
  student_id:   <students._id>,
  date:         "2026-07-28",
  attendance:   "present" | "absent",       // written only when the teacher marks it
  remark:       "…" | null,                 // optional
  score:        Whole number | null,
  absent:       true | false,               // true ⇒ score was auto-zeroed
  slot_1:       some string,
  slot_2:       some string,
  updated_at, updated_by,
  edits: [ { by, at, field, old_value, new_value } ]
}
```

Unique index on `(student_id, date)`. Index on `(date, teacher_id)`

All writes are upserts, `update_one({student_id, date}, …, upsert=True)`, not insert_one, double_taps and
network retries are guaranteed; without upserts plus the unique index, duplicate rows will corrupt every attendance fraction.

Attendance values are full strings `"present"` and `"absent"`.

There is no third stored value for "unmarked". An unmarked student simply has no `attendance` field written yet
the grey UI state (mentioned further in the doc) represents absence of data, not a status

#### Score Schematics -> all four cases must be distinguishable:

| Situation | `session.test.conducted` | `entries.score` | `entries.absent` |
|---|---|---|---|
| No test that day | `false` | `null` | `false` |
| Test held, not yet entered | `true` | `null` | `false` |
| Test held, student absent | `true` | `0` | `true` |
| Test held, student scored zero | `true` | `0` | `false` |


When a student is marked absent on a day with a test, the system writes `score: null` and `absent: true` automatically

`batch` and `teacher_id` are denormalised onto the entry so that historical
records stay attached to the batch and teacher that actually applied on that date, even
if the student is later reassigned.

## Core Rules (govern correctness, not appearance)

1. Application timezone is Asia/Kolkata. "Today" is computed in IST, never from the server's system clock in UTC. The hosting server will run UTC; between IST midnight and 5:30 a naive `date.today()` returns yesterday, which would break both the roster default and edit lock.

2. All dates are stored and compared as `DD-MM-YYYY` strings

3. The attendance fraction, one definition, written once
  for a given student and month:
  ```
  denominator = count of `class_days` where
                batch  = student's batch
                status = "held"
                date  >= student.enrollment_date
                date  <= student.left_date (if set)
                date within the selected month
 
  numerator   = count of that student's `entries` in that range
                where attendance = "present"
```

Holidays are excluded from both sides. Days never recorded are excluded from both sides.

Display the fraction first - `18 / 22 days` for example and the display line reads: `18 present 4 absent 22 class days`

Editing and locking:
A teacher may edit a day's entries only on that same IST calendar date. At IST midnight the day locks
An admin may unlock any past date, make corrections and re-lock.
The no editing rule applies only to daily entries only. Student records (name, roll number, batch, teacher, status) remain editable at any time. Do not conflate these two

## Authentication and roles

1. Two seeded accounts: `sahithi` (teacher, admin), `nithya` (teacher)

2. Session cookie is httponly, samesite=lax, and secure in production.

3. Session lifetime is long — 30 days. Teachers should not have to log in every evening.

4. A teacher can read and write entries only for students where teacher_id = <her id>. Enforce this in the query itself, not in the template.

5. Exception to point 4: class_days (holiday status) is shared and writable by either teacher, because it is a batch-level fact affecting both.

6. An admin can view and edit everything.

7. Every route except the login page requires an authenticated session. Unauthenticated requests redirect to login.

