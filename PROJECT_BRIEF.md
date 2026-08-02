# HR Academy Coaching Center Student Records Interface
 
There are 2 users that are going to be using this program for now
2 Teachers: Sahithi (teacher, admin), Nithya (teacher)
We need to design the system to store the data of 100 students
 
This app is going to replace a pen and paper register, each evening a teacher records, for every student assigned
to her
 
##  Structure of the center
 
- Teachers are assigned students to record their attendance their marks and the topics that they convered in their two study slots and are can also record the student's remarks or can record any comments on that student's behaviour that day.
- There are two teachers looking after the students and performing these actions, Sahithi and Nithya, Sahithi would have the admin account
## 2. Tech Stack
 
- **Backend:** FastAPI + Jinja2 templates
- **Interactivity:** HTMX (per-row auto-save)
- **CSS:** Tailwind via CDN
- **Database:** MongoDB Atlas free tier, `pymongo` (synchronous — not motor)
- **Auth:** Starlette `SessionMiddleware` + `bcrypt`
- **Hosting:** Render, Hobby workspace, Free instance, Singapore region
- **Timezone:** app runs `Asia/Kolkata`. All dates are IST strings, `"YYYY-MM-DD"`.
> **The host runs UTC.** Never call `date.today()` without an explicit timezone. Write one helper, `today_ist()`, and use it everywhere. A UTC/IST slip writes 10 PM entries to the wrong date and nobody notices for weeks.
 
---
 
## 3. Data model
 
Six collections. **Every write is an upsert.** Every compound key below has a unique index, created in Atlas *before* the first real write.
 
### 3.1 USERS
 
```
_id
name              "Sahithi"
username          lowercase, unique
password_hash     bcrypt, cost 12. plaintext never touches the database.
role              "admin" | "teacher"
is_active         bool
created_at
```
 
Seed exactly two: Sahithi (`admin`), Nithya (`teacher`).
 
### 3.2 STUDENTS
 
```
_id
roll_no           unique string, e.g. "R014"
name
teacher_id        → users._id. assignment. changeable.
slot              "6-9" | "7-10". display only. never groups anything.
enrollment_date   IST string. drives the attendance denominator.
is_active         bool, default true
deactivated_at    IST string or null. set when is_active flips to false.
parent_phone      optional
notes             optional
onboarded_by      → users._id. record-keeping only, never used for access.
created_at
updated_at
```
 
Indexes: unique `roll_no`; plain `(teacher_id, is_active)`.
 
> `deactivated_at` must exist from the first seed. `is_active` alone cannot answer "was this student enrolled on 15 July?" Adding a nullable field to forty documents later is trivial; reconstructing *when* someone left is impossible.
 
### 3.3 CLASS_DAYS
 
```
date              IST string. UNIQUE.
is_class_day      bool
label             "Sunday" | "Festival" | "Exam leave" | ""
set_by            → users._id
set_at
```
One document per date, global (not per teacher). Drives the calendar colour and the attendance denominator. **A date with no document is treated as a class day.**
 
### 3.4 ENTRIES
 
```
student_id + date   UNIQUE COMPOUND
status              "present" | "absent"   — full strings, not "p"/"a"
slot_1              just a string
slot_2              just a string
score               nullable integer
max_marks           nullable integer
teacher_id          who recorded it
remark              optional free text
created_at
updated_at
```
 
> **Unmarked is the absence of a document, not a third status.** Never write a document for an unmarked student. Grey in the UI means "no document exists."
 
> slot_1 and slot_2 are entered by the users these are just the topics that the students have read in both of the slots
 
> **Per student, deliberately.** Students read different material in the same room, so a shared per-date topic field would be wrong most evenings. This is accepted as a typing cost for now; after a week of real use the teachers will say whether they want it, and that feedback decides whether it stays per student, moves per date, or gains a copy-from-previous-row helper.
 
> test score is a fraction which will be entered by the user both the maximum marks and the score of the student will be entered by the user
 
> One test per day maximum. `max_marks` **pre-fills from the last value entered on that date** — the teacher types 25 on the first student and every subsequent row defaults to it, overridable per row. Both fields **disable and clear when the student is marked absent** — you cannot score someone who wasn't there. **Never validated**: blank scores never block "Finish day". No averages, no analysis, no separate test screen. Record, store, display, edit.
 
### 3.5 SESSIONS
 
```
teacher_id + date   UNIQUE COMPOUND
finished            bool, default false
finished_at
has_test            bool, default false
```
 
A `sessions` document is the record that **this teacher has finished her data entry for this date**. It is created the moment she saves her first entry for that date — including when she backfills days later.
 
`has_test` is the "Test today" / "No test" toggle above the student list (§5.3). When false, the score fields are hidden on every row.
 
> **`sessions` is not an input to any statistic.** It is a completion and UI-state record only. The attendance denominator is class days since enrollment (§4.1) and does not consult this collection. Do not wire it in.
 
### 3.6 AUDIT_LOG
 
```
_id
collection        "entries" | "students" | "class_days"
doc_key           e.g. {student_id, date}
field
old_value
new_value
edited_by         → users._id
authorised_by     → users._id. null unless a supervisor override was used.
edited_at
```
 
Append-only. Never updated, never deleted. Written for **every edit to a past date** and every change to a student's assigned teacher.
 
## 4. Rules
 
### 4.1 The attendance denominator — memorise this sentence
 
> A student's attendance is **days marked present ÷ class days that fall on or after that student's enrollment date.**
 
Holidays are excluded from both sides. Put this sentence verbatim as a comment above the function.
 
Class days come from the global `class_days` collection, so a holiday applies to every student regardless of teacher. Attendance does not depend on who recorded it: if a teacher is away, the assigned teacher still records her students, so a day with no entries yet is a day not yet entered, not a day that didn't happen. That day counts, and the students show as unmarked until it is filled in — see §5.6 for how pending days are surfaced.
 
Display as a **fraction first — "18 / 22 days"** Fractions are honest about sample size; percentages are not.
 
### 4.2 Who can edit what
 
 
| Date | Assigned teacher | Admin |
|---|---|---|
| Today | Edit | Edit |
| Yesterday | Edit | Edit |
| Older, **no entries exist**, within 30 days | Edit freely — no password | Edit freely |
| Older, **entries already exist** | Read-only → request override | Read-only → override |
| Older than 30 days | Read-only → request override | Read-only → override |
| Future | Blocked for everyone | Blocked for everyone |
 
The two-day window exists because class ends at 10 PM and next-morning entry is routine, not exceptional.
 
The empty-date rule exists because a teacher off sick for several days must be able to enter her own missing register without an admin present. **Filling blanks is not the dangerous operation; overwriting existing data is.** The 30-day cap stops it becoming an open hole.
 
### 4.3 Supervisor override
 
1. Teacher opens a locked date. A **visible "Edit this date"** button sits in the header. No hidden gestures.
2. Tapping it prompts: *"Admin password required to edit past entries."*
3. **The prompt appears for both users.** It checks the password, not who is logged in. Sahithi types her own password on Nithya's phone.
4. That **one date, on that teacher's own list**, becomes editable.
5. It **re-locks when the user navigates away from that date** — not at session end.
6. Every write under override goes to `audit_log` with `authorised_by` set.
> This is why the admin never needs visibility into the other teacher's students. Without it, Nithya's past mistakes could never be corrected by anyone: she can't unlock, and Sahithi can't see her students.
 
The only failure message is **"Incorrect password."** Never *"you do not have admin access."*
 
### 4.4 Auth
 
- bcrypt, cost 12. Password reset = you run a script that writes a new hash. You never see the plaintext.
- Session cookie, `httponly`, 12-hour expiry.
- One generic failure message. Don't distinguish "no such user" from "wrong password".
### 4.5 Which students appear on a given date
 
For **today**: the teacher's students where `is_active` is true.
 
For **any past date**, the union of:
 
- students where `enrollment_date <= date` **and** (`deactivated_at` is null **or** `deactivated_at > date`) **and** currently assigned to that teacher, and
- any student holding an `entries` document for that date where `entry.teacher_id` is that teacher
The second clause is the safety net: if a record exists, it must be visible, regardless of what the student flags currently say.
 
### 4.6 Reassignment does not rewrite history
 
**Entry wins for dates that already have entries.** `entries.teacher_id` records who recorded it, and that is what decides whose past list a student appears on. If Arjun moves from Nithya to Sahithi on 20 July, opening 15 July as Nithya still shows Arjun exactly as she recorded him; opening it as Sahithi does not show him, because he wasn't hers yet. Each teacher's history stays hers.
 
For a past date with **no** entry for that student, current assignment applies.
 
This needs no new field — `entries.teacher_id` is already in §3.4. The attendance denominator is unaffected by assignment either way, per §4.1.
 
---
 
## 5. Screens
 
### 5.1 Home
 
Academy logo. Beneath it, two profile cards: **Sahithi** and **Nithya**. Tapping one goes to login with that username pre-filled. Nothing else.
 
### 5.2 Dashboard
 
Three full-width stacked tap targets:
 
1. **Today's entries**
2. **Onboard a new student**
3. **Student data**
Below them, one line of plain text: *"12 of 19 students marked for today."* No charts.
 
Header shows the logged-in name and a logout link.
 
### 5.3 Daily entries
 
**Date bar - top left, unmissable
 
· **Thu 30 Jul · today** · calendar picker
 
The bar changes colour by state: **accent tint** for today, **warning tint** for a locked past date. Whoever is using it must know at a glance which day they are writing to.
 
**Search bar** beneath it — full width input, filters by name or roll number as you type. **No dropdown filter.** At forty students, search is sufficient.
 
**Test toggle:** *"Test today"* / *"No test"*, writing `sessions.has_test` (§3.5). Off by default. When off, the score fields are hidden on every row.
 
**Progress line:** *"12 of 19 marked."*
 
**Student rows**, sorted by roll number, membership per §4.5. Lines per row:
- Line 1: student name (left), roll number (right, monospace, muted)
- Line 2: `[ present ]` `[ absent ]` toggle at **40px minimum height**, then `slot_1` and `slot_2` text fields and remark icon
- Line 3 (test days only): `score` `/` `max_marks`, both numeric inputs, `max_marks` pre-filled from the previous row. Disabled when the student is marked absent.
Toggle states: **grey** = unmarked (never written to the database), **green** = present, **red** = absent.
 
Auto-save per row via HTMX. Tick on success. On failure the row goes red-bordered with a retry — **never a silent failure**.
 
**"Finish day"** button at the bottom. Behaviour in §6.
 
**When the date is locked:** the toggles are replaced entirely by plain text ("present" / "absent", and the score fraction). Do not render disabled buttons — a greyed-out control on a phone reads as a rendering bug, not a permission.
 
### 5.4 Calendar (v1)
 
Month grid. Tapping a date opens it.
 
| Colour | Meaning |
|---|---|
| Green | Class day, every active student marked |
| Amber | Class day, partially marked |
| Red Hue| Past class day, zero entries |
| Dark Grey| Not a class day |
| Light Grey| Future, not tappable |
| White with Blue ring | Today |
 
 
Colours are computed against **that teacher's own student list**, so a date can be green for Sahithi and amber for Nithya. That is correct, not a bug. Except if the whole day is marked as a holiday.
 
**Put the legend on the screen.** Six colours are not self-explanatory.
 
### 5.5 Onboard a new student
 
- Name — required
- Roll number — required, uniqueness checked before save
- Slot — The time that the student will be attending the academy
- Enrollment date — defaults to today, editable
- Parent phone — optional
- Notes — optional
`teacher_id` and `onboarded_by` both set to the logged-in user. `is_active` defaults true, `deactivated_at` null.
 
### 5.6 Student data
 
That teacher's students, active by default with a toggle for inactive. Search bar. Each row: name, roll number, slot, attendance fraction. **Export CSV** button.
 
Tapping a student opens the detail page:
 
- All fields, plus attendance as **"18 / 22 days"** with a month-by-month breakdown
- Where class days exist that nobody has entered yet, append a plain count: **"18 / 22 days · 2 days not yet entered."** This is honest about pending data without warping the arithmetic.
- Past test scores as a simple dated list of fractions — no averages
- **Edit** — all fields, plus **change assigned teacher**, plus **deactivate**
Deactivating confirms with: *"This student will no longer appear in daily entries. Past records are kept."* and sets `deactivated_at` to today.
 
### 5.7 All students — admin only (v1)
 
Visible to Sahithi only. **Separate screen from daily entries.** Every student in the centre, both teachers'.
 
- Roster view: name, roll number, slot, assigned teacher, active status
- **Change assigned teacher** — required for covering during illness or travel
- Deactivate / reactivate
- Export all
> This screen must never merge with §5.3. Sahithi's *entry* list stays her own students. This one is administration. If the two combine, the segregation she asked for is quietly undone.
 
## 6. Validation and highlighting
 
**Attendance** is the only mandatory field. Remarks and topics are never validated
 
Sequence:
 
1. Button reads **"Finish day."**
2. On tap with gaps: highlight **only** the unmarked rows and **scroll to the first one.** Highlighting alone means hunting through nineteen down to find three
3. The button becomes **"Finish day - 3 unmarked."**
4. A highlight clears the instant that row is marked.
5. If she taps again with gaps still present, confirm: *"3 students unmarked. Finish anyway?"*. On confirm, write `sessions.finished = true` and leave those entries genuinely absent from the database. They show as gaps, which is accurate.
Step 5 exists because a student who has quietly stopped attending but hasn't been deactivated would otherwise block "Finish day" forever.
 
## 8. Build phases
 
### v0.5 - completed
 
1. `students` collection + seed script with **real Telugu names and real roll numbers**
2. `GET /entries/{date}` — student list, unstyled HTML
3. `POST /entries` — write one attendance document
4. HTMX wiring: toggle → POST → save tick
5. Search bar
6. Date navigator: prev / next / today / picker — **no calendar yet**
7. Two hardcoded logins, list filtered by `teacher_id`
8. **No past-date editing at all.** Today and yesterday only.
Item 8 is what makes the rest fit. With no past-date editing there is no override flow, no password prompt and no audit log — the three fiddliest items, and they fall out together cleanly.
 
**This is a usable app.** A teacher can replace her register with it on Monday. That is the only bar that matters.
 
### v1
 
Calendar with colour coding · onboarding form · student detail and stats · edit/deactivate · admin roster (§5.7) · test scores · CSV export · holiday marking · audit log · supervisor override · backfill rules · real auth with bcrypt · slot topics (last)
 
**Tell her today that Saturday is the attendance version and the rest lands the following week.** A revised date given in advance is fine. A missed date discovered on the day is what damages the favour.
 
---
 
### 9 Build vertical slices, never layers
 
Do not build "the backend" then "the frontend." Build one feature all the way through — database to screen and back — then the next. The first slice is:
 
> Open a URL → see the real student list from MongoDB → tap "present" → it writes to MongoDB → reload → it's still there.
 
When that works, the app exists. Everything after is repetition of a proven pattern.
 
## 10. Acceptance checks
 
Run manually. Tick them off on paper.
 
1. Log in as Nithya — only her students appear.
2. Log in as Sahithi — only her students, none of Nithya's.
3. Mark a student present. Reload. Still present.
4. Double-tap the toggle rapidly. **Exactly one** document exists in Atlas.
5. Mark present, then absent. `updated_at` changes, no duplicate document.
6. Open yesterday — editable, no password.
7. Open an **empty** date from last week — editable, no password.
7a. Open a date from last week **that has entries** — read-only, "Edit this date" visible.
8. Wrong password → rejected, stays locked, message reads "Incorrect password."
9. Correct admin password → that date unlocks. Edit one entry.
10. Navigate away and back → locked again.
11. `audit_log` contains the step-9 edit with `authorised_by` set.
12. Mark a date as not a class day → its calendar cell turns grey.
13. A holiday appears in no student's denominator.
14. Onboard with a duplicate roll number → rejected with a clear message.
15. Onboard a student dated today → attendance reads "0 / 0", not "0%" or an error.
16. Deactivate a student → gone from today's list, **still present in last month's list and records.**
17. Reassign a student between teachers → they move on today's list. **Open a past date that already has their entry: they still appear on the old teacher's list, not the new one.**
17a. Turn on "Test today", enter 18 for the first student → the next row's `max_marks` pre-fills.
17b. Mark a student absent → their score fields disable and clear.
17c. Leave all scores blank → "Finish day" proceeds without complaint.
18. Search "R01" → matching students only.
19. Export CSV → opens in Excel with correct headers.
20. "Finish day" with one unmarked → highlights that row, scrolls to it, names the count.
21. "Finish day" again → offers "Finish anyway", and confirming writes `finished: true`.
22. **Set the server clock to UTC. At 11:30 PM IST the app still says today, not tomorrow.**
23. On a phone in portrait: every tap target reachable one-handed, nothing overflows horizontally.
**Check 22 is the one people skip and the one that silently corrupts a week of data.**
 
---
 
## 11. Deployment
 
1. Render → Hobby workspace → Free instance → Singapore
2. Env vars: `MONGO_URI`, `SESSION_SECRET`, `TZ=Asia/Kolkata`
3. Atlas → Network Access → allow Render's egress IPs, **not** `0.0.0.0/0`
4. Cron pinger on `/health` every 10 minutes, **17:15–22:30 IST only**
5. **Create all unique indexes before the first real write**, not after
6. Free-tier Atlas has no automated backup. Write a weekly `mongodump` script and run it manually. Old data is purged every three months — a mistake there is otherwise unrecoverable.
**Deploy a hello world to Render before writing a single feature.** People leave deployment until the end, hit an environment-variable wall at 11 PM, and lose the night. Prove the pipe works while it's empty.
 
---
 
