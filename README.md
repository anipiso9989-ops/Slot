# Slot v1.1 · Mono edition

A local scheduling app built with Python, SQLite, Google OR-Tools CP-SAT, Flask,
and a single HTML dashboard. No accounts, cloud APIs, or frontend build tools.

## Monochrome interface

This edition uses Geist Mono throughout the app, square corners, thin white
borders, black backgrounds, and a subtle graph-paper grid. Statuses and energy
types remain distinguishable through text and border patterns. Forms, dialogs,
metrics, and the mobile layout use the same visual system.

The variable font is bundled in `static/fonts/GeistMono-Variable.woff2` and served
locally by Flask. No external font service or frontend build is needed. Copy the
**entire `static` folder** when upgrading or uploading the source to GitHub.
If the font is missing, the browser falls back to its default monospace font.

Font source: [Vercel Geist](https://github.com/vercel/geist-font), distributed
through the official `geist` npm package, version 1.7.2. The original SIL Open
Font License is included in `static/fonts/OFL.txt`.

## Upgrade from an earlier ZIP

1. Stop Slot with **Ctrl+C** in its terminal.
2. Copy your existing `slot.db` to a safe backup location while Slot is stopped.
3. Extract this ZIP to a separate folder, then copy its files into your existing
   `slot` folder, replacing the old source files and `templates/index.html` and
   adding the new `static` folder, including the font and its license.
   Keep your existing `slot.db` and `.venv` folder. No database is included here.
4. Start Slot with the same command as before. It automatically upgrades the
   existing database without deleting tasks or completed blocks.
5. Refresh the browser with **Ctrl+F5** to load the updated dashboard.

Dependencies are unchanged. The upgrade adds a Trash flag and stores original
block labels for history. Pre-upgrade labels are captured as they exist at
upgrade time; old labels already changed by v1 cannot be reconstructed.
If reverting to v1, restore the matching pre-upgrade database backup too: the old
app does not understand deleted tasks. See `FEATURE_AUDIT.md` for the full audit.
The Mono edition makes no further database changes beyond v1.1.

## Start on Windows

1. Extract this ZIP. Open the extracted `slot` folder (the one containing `app.py`).
2. Open a terminal in that folder: click File Explorer's address bar, type `cmd`,
   and press Enter.
3. Check Python, then create an isolated environment and install the dependencies:

   ```bat
   py --version
   py -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe app.py
   ```

4. Open **http://127.0.0.1:5000** in your browser. Keep the terminal open.
5. Stop the app with **Ctrl+C** in the terminal.

Python **3.11 or newer** is required; 64-bit Python 3.12 is the tested runtime.
Install Python from [python.org](https://www.python.org/downloads/) if needed.
If `py` is unavailable but `python --version` works, use `python` for the first
two Python commands. No PowerShell activation or execution-policy change is needed.

After the first setup, start it again with:

```bat
.venv\Scripts\python.exe app.py
```

The first dependency installation needs internet access. Running Slot afterward
does not. Dependencies are installed locally; your tasks are never uploaded.

## Start on macOS or Linux

Open a terminal in the extracted `slot` directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

Then open **http://127.0.0.1:5000**. Some Linux distributions require installing
their `python3-venv` package before creating the environment.

## Use it

1. **Choose a date.** New tasks belong to that date. Unfinished tasks stay there
   until you edit their assigned date or push them forward.
2. **Set usable hours.** The default is 09:00–17:00. Multiple windows support
   split days. Remove all windows and save to mark a day off. `24:00` is allowed
   as a window end. Adjacent windows count as continuous usable time.
3. **Add commitments.** Meetings, meals, and other fixed blocks exclude work.
   Commitments can cross midnight and last up to seven days. Overlapping
   commitments are rejected with a clear error. Use **Edit** on a commitment to
   change its name, start, or end; it can also be moved to another date.
4. **Add tasks**, individually or using the bulk parser below.
5. Click **Generate schedule**. Review unslotted tasks and their explanations.
6. Mark each work block **Complete** or **Skip**. A task becomes completed when
   all of its minutes have been completed. Skipping a block gives no completion
   credit; regenerate to try placing its unfinished time again. **Skip task**
   pauses that whole task until you restore it or push it to tomorrow.
   **Complete remaining Xm** records all remaining scheduled blocks at once;
   it appears only when the task's entire remaining workload is scheduled.
7. **Push unfinished to tomorrow** moves all unfinished tasks on the selected
   date, including skipped and unslotted tasks, to the next calendar day and
   generates that day's schedule. Partial completion carries over as credit.
   Completed and skipped block records stay on their original dates.

**Deadlines do not move automatically.** Work pushed past its deadline is
reported as unslotted. Use **Edit** to change the deadline if the real deadline
has changed. Edit also changes the name, priority, and energy tag. The API can
change these same properties. The dashboard now also edits total duration,
minimum/maximum chunk lengths, and assigned date, even after partial completion.

**Edit completed tasks too.** Completed blocks retain their original times,
names, priorities, and energy tags. Raising a completed task's duration reopens
it with only the extra minutes remaining. Reducing the duration to exactly the
completed amount marks an unfinished task complete and removes its planned work.

The only progress-related edit constraints are that total duration cannot be
less than minutes already completed, and any remaining minutes must fit the new
chunk limits. New limits apply only to future work. For example, after completing
30 minutes of a 90-minute task, changing its chunk limits to 40–60 is valid:
the remaining 60 minutes fit, and the original 30-minute block stays unchanged.

**Delete anytime.** Every task has a Delete button, including completed, skipped,
and unslotted tasks. Delete moves it to **Trash**, removes its planned blocks,
and excludes it from scheduling, rollover, and unfinished-task limits. Historical
completed/skipped blocks remain on their dates; completed minutes still count
as work actually done and reserve their original time. The timeline identifies
completed blocks belonging to a deleted task. Trash is recoverable, with no
permanent purge control. **Restore task** brings back its original assigned date
and status. Generate a plan again if it has unfinished work.

Tomorrow keeps any explicitly saved hours. If it has no saved hours yet, Push
copies the selected day's hours. Existing tomorrow tasks are included when that
day is replanned. Pushing the same source date again does not duplicate tasks.

Changes to scheduling properties, task additions, saved hours, and commitment
changes clear planned blocks for affected dates. Moving a task invalidates both
its old and new date. A name-only edit keeps its plan and updates upcoming block
labels. Completed and skipped history is retained. Generate again when ready.
Status changes update the displayed load immediately after the server confirms
them. **Refresh** reloads the selected day. Multiple browser tabs do not have
background synchronization; refresh to see changes made elsewhere.

## Bulk entry

One task per line, using five comma-separated fields:

```text
Write research notes, 90m, high, 2026-10-01 17:00, deep
Answer email, 30m, low, 2026-10-01 18:00, shallow
```

Optional sixth and seventh fields specify minimum and maximum chunk lengths:

```text
Practice mathematics, 120m, high, 2026-10-01 17:00, deep, 25m, 60m
"Read, annotate, summarize", 60m, medium, 2026-10-01 17:00, deep, 20m, 30m
```

Use quotes around a task name containing commas; do not put quotes around the
entire line. Blank lines are ignored. An invalid line rejects the entire import
and reports its line number. Change these example deadlines to suit your date.

Without explicit chunk limits, minimum is `min(25, duration)` and maximum is
`min(60, duration)`. In the individual task form, chunk limits are explicit;
adjust them when adding a task shorter than 25 minutes. Durations must be exactly
divisible into some combination of valid chunks; e.g. 65 minutes with limits
40–60 is rejected, while 90 minutes with limits 25–60 can become 60 + 30.

## Scheduling policy

- Resolution: **one minute**, using local wall-clock dates and times, without
  time-zone conversions or daylight-saving adjustments.
- Tasks are considered by **priority (high, medium, low), earliest deadline,
  then insertion ID**. Energy affects placement, not admission order.
- Each task's entire **remaining** duration must fit in the selected day. Slot
  may split it into any number of valid chunks, subject to the model limit.
  It never silently schedules only part of an oversized remaining workload.
- Each candidate is admitted only when CP-SAT finds a feasible schedule for it
  together with all already admitted tasks. Earlier admitted tasks can move or
  split differently to make room. A rejected task does not prevent later,
  smaller tasks from being considered.
- CP-SAT's `NoOverlap` constraint covers work intervals and fixed exclusion
  ranges (outside usable hours, commitments, and completed work).
- After admission, a placement objective strongly favors early first chunks
  for deep work, also favors earlier starts generally, and penalizes excess
  chunks. Shallow work fills the remaining space. Energy is a **heuristic**;
  it never overrides hard deadlines or exclusions.
- This is a priority-first admission policy, **not a guarantee of maximum total
  task count or minutes**. For example, one high-priority task can displace
  several lower-priority tasks.
- One solver worker, a fixed random seed, stable task ordering, and
  deterministic work budgets make the same input produce the same schedule
  in the tested runtime. Reproducibility is scoped to the same Python/OR-Tools
  build and platform. Upgrading solver versions may change placements.
- Admission uses 0.5 deterministic time units per candidate; energy placement
  uses 2.0. These are solver work budgets, **not wall-clock seconds**. A hard
  problem may take longer. When feasibility search stops without an answer,
  the task is labeled **unknown/search limit**, not proven impossible. If
  energy optimization stops, the last feasible schedule is retained.
- Limits: 100 unfinished tasks per date, 1–1,440 minutes per task, at most
  12 usable windows, and 2,000 possible chunk intervals per candidate model.
  Larger minimum chunks reduce model size. Use modest daily task lists.

Slot plans the **whole selected day**, including times that may already have
passed; it does not read the current clock while solving. To replan only the
rest of today, change today's usable hours to start at the current time, then
generate again. Completed history remains visible even outside revised hours.

## Load metric

`usable minutes = usable-window minutes − fixed-commitment minutes inside those windows`

`load = (planned + completed work minutes inside usable windows) / usable minutes`

Skipped work is excluded. Commitments outside usable hours do not reduce the
denominator. A day with zero usable minutes shows 0%. Completed work outside
revised hours is preserved and shown in the completed total, but is not counted
against the revised window's load. Unslotted minutes include unfinished work on
active tasks assigned to the displayed date; paused/skipped tasks are excluded.

## Data, safety, and recovery

- `slot.db` is created beside `app.py` on first run. SQLite transactions make
  each write, schedule replacement, bulk import, and rollover atomic. A failure
  rolls the transaction back. Concurrent writers are serialized.
- Completed block records are immutable; task properties can be edited and tasks
  can be deleted to recoverable Trash. Stale block IDs cannot be reused to
  complete new work.
- Stop Slot before copying `slot.db` to make a simple backup. Restore by stopping
  Slot and replacing that file with the backup. Do not delete your database to
  fix an installation issue. If copying while running, SQLite's WAL sidecar
  files may also contain data; stopping first avoids that complication.
- Optional: set `SLOT_DB` to an absolute path before launching to choose another
  database location. Tests always use temporary databases.
- The server binds to `127.0.0.1`, with debugging disabled. Browser writes from
  other origins and unexpected hostnames are rejected. There is no account or
  authentication layer: keep it local and do not expose it to the internet.

## If the browser keeps loading

Keep the server terminal open. In another Command Prompt, run:

```bat
curl.exe --noproxy "*" --max-time 5 http://127.0.0.1:5000/api/health
```

A healthy v1.1 server returns JSON identifying `Slot`, version `1.1`, and status
`ok`. This health check does not wait for the scheduler's database transaction.
If it fails, the browser cannot reach this Slot server at that address, or the
server has not started. Inspect the server terminal for an error. If health
works but the dashboard does not, confirm JavaScript is enabled and use Ctrl+F5.

To try a different local port, stop Slot with Ctrl+C, then run:

```bat
.venv\Scripts\python.exe app.py --port 5001
```

Open **http://127.0.0.1:5001**. The startup message always prints the selected URL.
This is an alternative-port check, not a diagnosis of a specific connection fault.

The dashboard now shows loading and connection errors. Reads time out after
10 seconds, writes after 120 seconds. A timed-out write may still finish on the
server; use Refresh to inspect its result before repeating the action. Do not
delete the database as a troubleshooting step.

## Run tests

Windows:

```bat
.venv\Scripts\python.exe -m pytest -q
```

macOS/Linux:

```sh
.venv/bin/python -m pytest -q
```

Tests cover variable chunk packing, priority admission, deadlines, energy order,
repeatability, no-overlap invariants across generated scenarios, load arithmetic,
cross-midnight commitments, transaction rollback, bulk-import atomicity,
partial completion, repeated rollover, stale IDs, persistent data, validation,
and API request handling. Additional tests cover full task editing, arbitrary
date moves, editing partial/completed tasks, exact remainder validation, original
history labels, deletion in every task state, Trash restoration, commitment
edits, migration from v1, and rollback on errors. No real user data is modified.

Delivery verification: 62 pytest cases passed on Python 3.12 with the pinned
dependencies. Dashboard DOM checks against the running Flask API also verified
entry, bulk import, safe text rendering, hours, commitments, editing, scheduling,
completion, rollover, skipping, and restoration. The v1.1 checks additionally
exercise editing every task property, date moves, editing commitments,
whole-task completion, deletion and restoration of completed/unfinished tasks,
and reopening completed work. JavaScript syntax passed.
The Mono edition passed the same dashboard interaction checks; the bundled font
was also verified to be served correctly by Flask.
An actual browser visual check was blocked by the build environment's localhost
access restriction; pixel-level layout has not been independently verified.

## Files

```text
slot/
├── engine.py
├── app.py
├── test_slot.py
├── requirements.txt
├── README.md
├── FEATURE_AUDIT.md
├── .gitignore
├── static/
│   └── fonts/
│       ├── GeistMono-Variable.woff2
│       └── OFL.txt
└── templates/
    └── index.html
```

## API reference

All writes use JSON objects and `Content-Type: application/json`. API responses
are JSON. Invalid input returns 400, missing items 404, and cross-origin writes
403. Timestamps accept either a space or `T` between date and time.

| Method | Route | Body / purpose |
| --- | --- | --- |
| GET | `/api/day?date=YYYY-MM-DD` | Tasks, blocks, commitments, windows, load |
| GET | `/api/health` | App name, version and readiness without a database read |
| POST | `/api/tasks` | `name`, `duration`, `priority`, `deadline`, `energy`, `target_date`; optional `min_chunk`, `max_chunk` |
| POST | `/api/tasks/bulk` | `day`, `text` |
| PATCH | `/api/tasks/<id>` | `name`, `duration`, `priority`, `deadline`, `energy`, `min_chunk`, `max_chunk`, `target_date`; only changed fields required |
| DELETE | `/api/tasks/<id>` | `{}`; move task to Trash, removing planned blocks |
| POST | `/api/tasks/<id>/restore` | `{}`; restore original assigned date/status from Trash |
| POST | `/api/tasks/<id>/status` | `status`: `planned`, `skipped`, or `completed`; completion requires all remaining minutes scheduled |
| POST | `/api/blocks/<id>/status` | `status`: `completed` or `skipped`; only planned blocks can transition |
| PUT | `/api/windows` | `day`, `windows`: e.g. `[[540,720],[780,1020]]` |
| POST | `/api/commitments` | `name`, `start`, `end` |
| PATCH | `/api/commitments/<id>` | Any of `name`, `start`, `end` |
| DELETE | `/api/commitments/<id>` | `{}` |
| POST | `/api/plan` | `day` |
| POST | `/api/push` | `day`; response includes destination `day` and `moved` count |

### Implementation references

The interval model follows Google's [CP-SAT scheduling documentation](https://developers.google.com/optimization/scheduling/job_shop).
Solver work budgets and single-worker settings are documented in Google's
[solver parameters](https://github.com/google/or-tools/blob/stable/ortools/sat/sat_parameters.proto).
