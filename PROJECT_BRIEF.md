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
parent_phone      optional
notes             optional
onboarded_by      → users._id. record-keeping only, never used for access.
created_at
updated_at
```
 
Indexes: unique `roll_no`; plain `(teacher_id, is_active)`.

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
test_score          student_score (integer) / maximum marks
teacher_id          who recorded it
remark              optional free text
created_at
updated_at
```
 
> **Unmarked is the absence of a document, not a third status.** Never write a document for an unmarked student. Grey in the UI means "no document exists."

> slot_1 and slot_2 are entered by the users these are just the topics that the students have read in both of the slots

> test score is a fraction which will be entered by the user both the maximum marks and the score of the student will be entered by the user
 
### 3.5 AUDIT_LOG

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
 
> A student's attendance is **days marked present ÷ days that were class days, had at least one entry recorded, and fall on or after that student's enrollment date.**
 
Holidays are excluded from both sides. Dates nobody entered are excluded from both sides. Put this sentence verbatim as a comment above the function.
 
Display as a **fraction first — "18 / 22 days"** Fractions are honest about sample size; percentages are not.

### 4.2 Who can edit what


| Date | Teacher | Admin |
|---|---|---|
| Today | Edit | Edit |
| Yesterday | Edit | Edit |
| Older | Read-only → request override | Read-only → override |
| Future | Blocked for everyone | Blocked for everyone |
 
The two-day window exists because class ends at 10 PM and next-morning entry is routine, not exceptional.

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

**Progress line:** *"12 of 19 marked."*

**Student rows**, sorted by roll number, active students only. Two lines each:
- Line 1: student name (left), roll number (right, monospace, muted)
- Line 2: `[ present ]` `[ absent ]` toggle at **40px minimum height**, then `slot_1` and `slot_2` text fields and remark icon

Toggle states: **grey** = unmarked (never written to the database), **green** = present, **red** = absent.

Auto-save per row via HTMX. Tick on success. On failure the row goes red-bordered with a retry — **never a silent failure**.

**"Finish day"** button at the bottom. Behaviour in §6.
 
**When the date is locked:** the toggles are replaced entirely by plain text ("present" / "absent"). Do not render disabled buttons — a greyed-out control on a phone reads as a rendering bug, not a permission.

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

`teacher_id` and `onboarded_by` both set to the logged-in user. `is_active` defaults true.

### 5.6 Student data
 
That teacher's students, active by default with a toggle for inactive. Search bar. Each row: name, roll number, slot, attendance fraction. **Export CSV** button.
 
Tapping a student opens the detail page:
 
- All fields, plus attendance as **"18 / 22 days"** with a month-by-month breakdown
- **Edit** — all fields, plus **change assigned teacher**, plus **deactivate**
- **View test scores** → placeholder in v1

Deactivating confirms with: *"This student will no longer appear in daily entries. Past records are kept."*

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
 
### v0.5 — Saturday 1 August
 
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
 
### v1 — week two
 
Calendar with colour coding · onboarding form · student detail and stats · edit/deactivate · CSV export · holiday marking · audit log · supervisor override · real auth with bcrypt · slot topics (last)
 
**Tell her today that Saturday is the attendance version and the rest lands the following week.** A revised date given in advance is fine. A missed date discovered on the day is what damages the favour.
 
---
 
### 9 Build vertical slices, never layers
 
Do not build "the backend" then "the frontend." Build one feature all the way through — database to screen and back — then the next. The first slice is:
 
> Open a URL → see the real student list from MongoDB → tap "present" → it writes to MongoDB → reload → it's still there.
 
When that works, the app exists. Everything after is repetition of a proven pattern.

## 10. Acceptance checks - for Aadarsh
 
Run manually. Tick them off on paper.
 
1. Log in as Nithya — only her students appear.
2. Log in as Sahithi — only her students, none of Nithya's.
3. Mark a student present. Reload. Still present.
4. Double-tap the toggle rapidly. **Exactly one** document exists in Atlas.
5. Mark present, then absent. `updated_at` changes, no duplicate document.
6. Open yesterday — editable, no password.
7. Open last week — read-only, "Edit this date" visible.
8. Wrong password → rejected, stays locked, message reads "Incorrect password."
9. Correct admin password → that date unlocks. Edit one entry.
10. Navigate away and back → locked again.
11. `audit_log` contains the step-9 edit with `authorised_by` set.
12. Mark a date as not a class day → its calendar cell turns grey.
13. A holiday appears in no student's denominator.
14. Onboard with a duplicate roll number → rejected with a clear message.
15. Onboard a student dated today → attendance reads "0 / 0", not "0%" or an error.
16. Deactivate a student → gone from today's list, still in last month's records.
17. Reassign a student between teachers → moves lists, past entries intact.
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
 
## 12. Risks accepted
 
**12.1 — Free instance cold starts.** The pinger covers 17:15–22:30. Outside it, the first request takes ~30 seconds.
 
**12.2 — Segregation blocks cover.** When one teacher is absent the other cannot mark her students. Workaround: Sahithi reassigns students temporarily via the student edit form. Manual, and someone will forget to reverse it. Accepted deliberately — revisit if it happens twice.
 
**12.3 — No password reset UI.** You are the reset mechanism. Fine at two users, untenable at five.
 
**12.4 — Three-month deletion policy.** Make it a deliberate script you run, not a TTL index that silently eats data. The policy is a bigger risk than the 512 MB limit, which is years away.

