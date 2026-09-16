"""Slot's local database and deterministic, minute-resolution CP-SAT planner."""
from __future__ import annotations

import csv
import io
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path

from ortools.sat.python import cp_model

PRIORITIES = {"high": 0, "medium": 1, "low": 2}
DEFAULT_WINDOWS = [[540, 1020]]


class ValidationError(ValueError):
    pass


def day_value(value):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        raise ValidationError("Use a date in YYYY-MM-DD format.") from None


def timestamp(value):
    try:
        if not isinstance(value, str) or len(value) != 16:
            raise ValueError
        return datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        raise ValidationError("Use local timestamps in YYYY-MM-DD HH:MM format (whole minutes).") from None


def integer(value, label, low=1, high=1440):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValidationError(f"{label} must be an integer from {low} to {high}.")
    return value


def name_value(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValidationError("Name must contain 1–200 characters.")
    return value.strip()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          duration INTEGER NOT NULL CHECK(duration BETWEEN 1 AND 1440),
          priority TEXT NOT NULL CHECK(priority IN ('high','medium','low')),
          deadline TEXT NOT NULL, energy TEXT NOT NULL CHECK(energy IN ('deep','shallow')),
          min_chunk INTEGER NOT NULL CHECK(min_chunk>0),
          max_chunk INTEGER NOT NULL CHECK(max_chunk>=min_chunk),
          target_date TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','completed','skipped')),
          reason TEXT
        );
        CREATE TABLE IF NOT EXISTS blocks (
          id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL REFERENCES tasks(id),
          day TEXT NOT NULL, start INTEGER NOT NULL CHECK(start>=0),
          end INTEGER NOT NULL CHECK(end>start AND end<=1440),
          status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','completed','skipped'))
        );
        CREATE INDEX IF NOT EXISTS blocks_day ON blocks(day);
        CREATE INDEX IF NOT EXISTS blocks_task ON blocks(task_id);
        CREATE TABLE IF NOT EXISTS commitments (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL CHECK(end>start)
        );
        CREATE TABLE IF NOT EXISTS day_settings (day TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS windows (
          day TEXT NOT NULL REFERENCES day_settings(day) ON DELETE CASCADE,
          start INTEGER NOT NULL, end INTEGER NOT NULL CHECK(end>start), PRIMARY KEY(day,start)
        );
        """)
        # Additive migration: existing v1 databases keep tasks and work history.
        c.execute("BEGIN IMMEDIATE")
        if "deleted_at" not in {r[1] for r in c.execute("PRAGMA table_info(tasks)")}:
            c.execute("ALTER TABLE tasks ADD COLUMN deleted_at TEXT")
        block_columns = {r[1] for r in c.execute("PRAGMA table_info(blocks)")}
        for column in ("task_name", "task_energy", "task_priority"):
            if column not in block_columns:
                c.execute(f"ALTER TABLE blocks ADD COLUMN {column} TEXT")
        c.execute("""UPDATE blocks SET
          task_name=COALESCE(task_name,(SELECT name FROM tasks WHERE id=blocks.task_id)),
          task_energy=COALESCE(task_energy,(SELECT energy FROM tasks WHERE id=blocks.task_id)),
          task_priority=COALESCE(task_priority,(SELECT priority FROM tasks WHERE id=blocks.task_id))
          WHERE task_name IS NULL OR task_energy IS NULL OR task_priority IS NULL""")
        c.execute("PRAGMA user_version=2")


@contextmanager
def database(path, write=False):
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        c.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def invalidate(c, day):
    c.execute("DELETE FROM blocks WHERE day=? AND status='planned'", (day,))
    c.execute("UPDATE tasks SET reason=NULL WHERE target_date=?", (day,))


def get_task(c, task_id, include_deleted=False):
    row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None or (row["deleted_at"] and not include_deleted):
        raise LookupError("Task not found.")
    return dict(row)


def completed_minutes(c, task_id):
    return c.execute("SELECT COALESCE(SUM(end-start),0) FROM blocks WHERE task_id=? AND status='completed'", (task_id,)).fetchone()[0]


def validate_task(data, completed=0):
    if not isinstance(data, dict):
        raise ValidationError("Task must be a JSON object.")
    duration = integer(data.get("duration"), "Duration")
    minimum = integer(data.get("min_chunk", min(25, duration)), "Minimum chunk")
    maximum = integer(data.get("max_chunk", min(60, duration)), "Maximum chunk")
    if minimum > maximum:
        raise ValidationError("Minimum chunk cannot exceed maximum chunk.")
    if duration < completed:
        raise ValidationError(f"Duration cannot be less than the {completed} minutes already completed.")
    remaining = duration - completed
    if remaining and math.ceil(remaining / maximum) > remaining // minimum:
        raise ValidationError(f"The {remaining} remaining minutes cannot be split exactly using these chunk limits.")
    if not isinstance(data.get("priority"), str) or data["priority"] not in PRIORITIES or data.get("energy") not in ("deep", "shallow"):
        raise ValidationError("Choose high/medium/low priority and deep/shallow energy.")
    return dict(name=name_value(data.get("name")), duration=duration, priority=data["priority"],
                deadline=timestamp(data.get("deadline")), energy=data["energy"],
                min_chunk=minimum, max_chunk=maximum, target_date=day_value(data.get("target_date")))


def add_task(c, data):
    task = validate_task(data)
    if c.execute("SELECT COUNT(*) FROM tasks WHERE target_date=? AND status!='completed' AND deleted_at IS NULL", (task["target_date"],)).fetchone()[0] >= 100:
        raise ValidationError("Limit: 100 unfinished tasks per day.")
    cursor = c.execute("""INSERT INTO tasks(name,duration,priority,deadline,energy,min_chunk,max_chunk,target_date)
                          VALUES(:name,:duration,:priority,:deadline,:energy,:min_chunk,:max_chunk,:target_date)""", task)
    invalidate(c, task["target_date"])
    return cursor.lastrowid


def parse_bulk(text, day):
    day_value(day)
    if not isinstance(text, str) or len(text) > 50000:
        raise ValidationError("Bulk text must be at most 50,000 characters.")
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = next(csv.reader(io.StringIO(line), skipinitialspace=True, strict=True))
            if len(row) not in (5, 7):
                raise ValueError("expected 5 fields, or 7 with minimum and maximum chunk lengths")
            name, length, priority, deadline, energy = [v.strip() for v in row[:5]]
            def minutes(v):
                if not v.lower().endswith("m"):
                    raise ValueError("durations must end in m")
                return int(v[:-1])
            task = dict(name=name, duration=minutes(length), priority=priority.lower(),
                        deadline=deadline, energy=energy.lower(), target_date=day)
            if len(row) == 7:
                task.update(min_chunk=minutes(row[5].strip()), max_chunk=minutes(row[6].strip()))
            result.append(validate_task(task))
        except (ValueError, csv.Error) as exc:
            raise ValidationError(f"Line {number}: {exc}") from None
    if not result or len(result) > 100:
        raise ValidationError("Enter between 1 and 100 tasks.")
    return result


def get_windows(c, day):
    if not c.execute("SELECT 1 FROM day_settings WHERE day=?", (day,)).fetchone():
        return [w[:] for w in DEFAULT_WINDOWS]
    return [list(r) for r in c.execute("SELECT start,end FROM windows WHERE day=? ORDER BY start", (day,))]


def set_windows(c, day, windows):
    day_value(day)
    if not isinstance(windows, list) or len(windows) > 12:
        raise ValidationError("Use at most 12 operating windows; an empty list closes the day.")
    clean = []
    for w in windows:
        if not isinstance(w, list) or len(w) != 2:
            raise ValidationError("Each window needs start and end minutes.")
        start, end = integer(w[0], "Start", 0, 1439), integer(w[1], "End", 1, 1440)
        if start >= end:
            raise ValidationError("A window must end after it starts, within the same day.")
        clean.append([start, end])
    clean.sort()
    if any(a[1] > b[0] for a, b in zip(clean, clean[1:])):
        raise ValidationError("Operating windows cannot overlap.")
    c.execute("INSERT OR IGNORE INTO day_settings(day) VALUES(?)", (day,))
    c.execute("DELETE FROM windows WHERE day=?", (day,))
    c.executemany("INSERT INTO windows(day,start,end) VALUES(?,?,?)", [(day, *w) for w in clean])
    invalidate(c, day)


def merge_ranges(ranges):
    result = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def day_commitments(c, day):
    midnight = datetime.combine(date.fromisoformat(day), time())
    tomorrow = (midnight + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    rows = c.execute("SELECT * FROM commitments WHERE start<? AND end>? ORDER BY start,id", (tomorrow, day + " 00:00"))
    result = []
    for r in rows:
        item = dict(r)
        item["start_minute"] = max(0, int((datetime.fromisoformat(r["start"]) - midnight).total_seconds() // 60))
        item["end_minute"] = min(1440, int((datetime.fromisoformat(r["end"]) - midnight).total_seconds() // 60))
        result.append(item)
    return result


def save_commitment(c, data, commitment_id=None):
    old = None
    if commitment_id is not None:
        old = c.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
        if old is None:
            raise LookupError("Commitment not found.")
        if set(data) - {"name", "start", "end"}:
            raise ValidationError("Only name, start and end can be edited.")
        data = {**dict(old), **data}
    name, start, end = name_value(data.get("name")), timestamp(data.get("start")), timestamp(data.get("end"))
    if start >= end or datetime.fromisoformat(end) - datetime.fromisoformat(start) > timedelta(days=7):
        raise ValidationError("Commitments must last more than zero minutes and no longer than 7 days.")
    if c.execute("SELECT 1 FROM commitments WHERE start<? AND end>? AND id!=?", (end, start, commitment_id or -1)).fetchone():
        raise ValidationError("This overlaps another fixed commitment.")
    affected = set()
    day = date.fromisoformat(start[:10])
    while day <= date.fromisoformat(end[:10]):
        midnight = datetime.combine(day, time())
        a = int((datetime.fromisoformat(start)-midnight).total_seconds()//60)
        b = int((datetime.fromisoformat(end)-midnight).total_seconds()//60)
        if c.execute("SELECT 1 FROM blocks WHERE day=? AND status='completed' AND start<? AND end>?", (day.isoformat(), b, a)).fetchone():
            raise ValidationError("This overlaps completed work. Completed history is preserved.")
        affected.add(day.isoformat())
        day += timedelta(days=1)
    if old:
        day = date.fromisoformat(old["start"][:10])
        while day <= date.fromisoformat(old["end"][:10]):
            affected.add(day.isoformat())
            day += timedelta(days=1)
    for changed_day in sorted(affected):
        invalidate(c, changed_day)
    if commitment_id is not None:
        c.execute("UPDATE commitments SET name=?,start=?,end=? WHERE id=?", (name,start,end,commitment_id))
        return commitment_id
    return c.execute("INSERT INTO commitments(name,start,end) VALUES(?,?,?)", (name,start,end)).lastrowid


def add_commitment(c, data):
    return save_commitment(c, data)


def update_commitment(c, commitment_id, data):
    return save_commitment(c, data, commitment_id)


def delete_commitment(c, commitment_id):
    row = c.execute("SELECT * FROM commitments WHERE id=?", (commitment_id,)).fetchone()
    if not row:
        raise LookupError("Commitment not found.")
    day = date.fromisoformat(row["start"][:10])
    while day <= date.fromisoformat(row["end"][:10]):
        invalidate(c, day.isoformat())
        day += timedelta(days=1)
    c.execute("DELETE FROM commitments WHERE id=?", (commitment_id,))


def solve(tasks, blocked, optimize=False, budget=0.5):
    """Solve full remaining workloads; absence is handled by stable admission order."""
    model = cp_model.CpModel()
    intervals, records, costs = [], [], []
    for i, (a, b) in enumerate(blocked):
        intervals.append(model.new_fixed_size_interval_var(a, b-a, f"fixed_{i}"))
    for task in tasks:
        count = task["remaining"] // task["min_chunk"]
        previous = None
        sizes = []
        for i in range(count):
            prefix = f"t{task['id']}_{i}"
            present = model.new_bool_var(prefix + "_present")
            start = model.new_int_var(0, 1440, prefix + "_start")
            end = model.new_int_var(0, 1440, prefix + "_end")
            size = model.new_int_var(0, task["max_chunk"], prefix + "_size")
            model.add(size >= task["min_chunk"]).only_enforce_if(present)
            for value in (start, end, size):
                model.add(value == 0).only_enforce_if(present.Not())
            model.add(end <= task["latest"]).only_enforce_if(present)
            intervals.append(model.new_optional_interval_var(start, size, end, present, prefix))
            if previous:
                model.add(present <= previous[0])
                model.add(start >= previous[1]).only_enforce_if(present)
            previous = (present, end)
            sizes.append(size)
            records.append((task["id"], present, start, end))
            # First deep chunk gets the strongest early-start preference.
            costs.append(start * (10000 if task["energy"] == "deep" and i == 0 else 1))
            costs.append(present * 1441)
        model.add(sum(sizes) == task["remaining"])
    model.add_no_overlap(intervals)
    if optimize:
        model.minimize(sum(costs))
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_deterministic_time = budget
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return status, []
    return status, sorted([(tid, solver.value(a), solver.value(b)) for tid, p, a, b in records if solver.value(p)], key=lambda r: (r[1], r[0]))


def plan_day(c, day):
    day_value(day)
    windows = get_windows(c, day)
    commitments = day_commitments(c, day)
    blocked, cursor = [], 0
    for a, b in windows:
        blocked.append([cursor, a])
        cursor = b
    blocked.append([cursor, 1440])
    blocked += [[r["start_minute"], r["end_minute"]] for r in commitments]
    blocked += [list(r) for r in c.execute("SELECT start,end FROM blocks WHERE day=? AND status='completed'", (day,))]
    blocked = merge_ranges(blocked)
    capacity = 1440 - sum(b-a for a,b in blocked)
    candidates = [dict(r) for r in c.execute("SELECT * FROM tasks WHERE target_date=? AND status='planned' AND deleted_at IS NULL", (day,))]
    candidates.sort(key=lambda t: (PRIORITIES[t["priority"]], t["deadline"], t["id"]))
    selected, solution, reasons = [], [], {}
    midnight = datetime.fromisoformat(day)
    for task in candidates:
        task["remaining"] = task["duration"] - completed_minutes(c, task["id"])
        task["latest"] = min(1440, int((datetime.fromisoformat(task["deadline"]) - midnight).total_seconds() // 60))
        reason = None
        if task["latest"] <= 0:
            reason = "Deadline is before this day's usable time. Edit the deadline if it has changed."
        elif task["remaining"] > capacity - sum(t["remaining"] for t in selected):
            reason = "Insufficient remaining capacity after higher-ranked tasks and fixed blocks."
        elif sum(t["remaining"] // t["min_chunk"] for t in selected + [task]) > 2000:
            reason = "Model limit reached (2,000 possible chunks). Increase minimum chunk lengths."
        else:
            status, trial = solve(selected + [task], blocked)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                selected.append(task)
                solution = trial
            elif status == cp_model.INFEASIBLE:
                reason = "Cannot fit valid chunks before the deadline around fixed blocks and higher-ranked tasks."
            elif status == cp_model.MODEL_INVALID:
                raise RuntimeError("Invalid scheduling model; previous plan was preserved.")
            else:
                reason = "Solver search limit reached; feasibility is unknown. Try larger minimum chunks."
        if reason:
            reasons[task["id"]] = reason
    quality = "empty"
    if selected:
        status, improved = solve(selected, blocked, optimize=True, budget=2.0)
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solution = improved
        quality = "optimal_placement" if status == cp_model.OPTIMAL else "feasible_placement"
    invalidate(c, day)
    by_id = {t["id"]:t for t in selected}
    c.executemany("""INSERT INTO blocks(task_id,day,start,end,task_name,task_energy,task_priority)
                     VALUES(?,?,?,?,?,?,?)""",
                  [(tid,day,a,b,by_id[tid]["name"],by_id[tid]["energy"],by_id[tid]["priority"]) for tid,a,b in solution])
    c.executemany("UPDATE tasks SET reason=? WHERE id=?", [(reason,tid) for tid,reason in reasons.items()])
    return {"placement": quality, "scheduled_tasks": len(selected), "unslotted_tasks": len(reasons)}


def set_block_status(c, block_id, status):
    if status not in ("completed", "skipped"):
        raise ValidationError("A planned block can be completed or skipped.")
    row = c.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
    if not row:
        raise LookupError("Block not found; refresh the dashboard.")
    if row["status"] == status:
        return
    if row["status"] != "planned":
        raise ValidationError("Historical blocks are immutable.")
    c.execute("UPDATE blocks SET status=? WHERE id=?", (status,block_id))
    task = get_task(c, row["task_id"])
    if completed_minutes(c, task["id"]) == task["duration"]:
        c.execute("UPDATE tasks SET status='completed',reason=NULL WHERE id=?", (task["id"],))


def set_task_status(c, task_id, status):
    task = get_task(c, task_id)
    if status == "completed":
        remaining = task["duration"] - completed_minutes(c, task_id)
        planned = c.execute("SELECT COALESCE(SUM(end-start),0) FROM blocks WHERE task_id=? AND status='planned'", (task_id,)).fetchone()[0]
        if planned != remaining:
            raise ValidationError("Schedule all remaining work first, then mark it complete; unslotted minutes cannot be recorded as timed work.")
        c.execute("UPDATE blocks SET status='completed' WHERE task_id=? AND status='planned'", (task_id,))
        c.execute("UPDATE tasks SET status='completed',reason=NULL WHERE id=?", (task_id,))
        return
    if task["status"] == "completed":
        raise ValidationError("All task minutes are already completed. Increase the duration to reopen it while preserving history.")
    if status not in ("planned", "skipped"):
        raise ValidationError("Complete individual scheduled blocks; tasks finish when all minutes are complete.")
    if status == "skipped":
        c.execute("UPDATE blocks SET status='skipped' WHERE task_id=? AND status='planned'", (task_id,))
    c.execute("UPDATE tasks SET status=?,reason=NULL WHERE id=?", (status,task_id))


def update_task(c, task_id, data):
    old = get_task(c, task_id)
    allowed = {"name", "duration", "priority", "deadline", "energy", "min_chunk", "max_chunk", "target_date"}
    if set(data) - allowed:
        raise ValidationError("Unknown task property; change task status using its status control.")
    completed = completed_minutes(c, task_id)
    task = validate_task({**old, **data}, completed=completed)
    status = "completed" if task["duration"] == completed else ("planned" if old["status"] == "completed" else old["status"])
    if status != "completed":
        count = c.execute("SELECT COUNT(*) FROM tasks WHERE target_date=? AND id!=? AND status!='completed' AND deleted_at IS NULL", (task["target_date"],task_id)).fetchone()[0]
        if count >= 100:
            raise ValidationError("The selected day already has 100 unfinished tasks.")
    c.execute("""UPDATE tasks SET name=:name,duration=:duration,priority=:priority,deadline=:deadline,
                 energy=:energy,min_chunk=:min_chunk,max_chunk=:max_chunk,target_date=:target_date,
                 status=:status,reason=NULL WHERE id=:id""", {**task,"id":task_id,"status":status})
    if any(task[k] != old[k] for k in allowed - {"name"}):
        # A move must invalidate both dates without moving completed history.
        for day in sorted({old["target_date"], task["target_date"]}):
            invalidate(c, day)
    else:
        c.execute("UPDATE blocks SET task_name=? WHERE task_id=? AND status='planned'", (task["name"],task_id))


def delete_task(c, task_id):
    """Delete from active work. Trash permits recovery; completed history stays put."""
    task = get_task(c, task_id, include_deleted=True)
    if task["deleted_at"]:
        return
    c.execute("DELETE FROM blocks WHERE task_id=? AND status='planned'", (task_id,))
    c.execute("UPDATE tasks SET deleted_at=?,reason=NULL WHERE id=?", (datetime.now().isoformat(timespec="seconds"),task_id))


def restore_task(c, task_id):
    task = get_task(c, task_id, include_deleted=True)
    if not task["deleted_at"]:
        return
    if task["status"] != "completed":
        count = c.execute("SELECT COUNT(*) FROM tasks WHERE target_date=? AND status!='completed' AND deleted_at IS NULL", (task["target_date"],)).fetchone()[0]
        if count >= 100:
            raise ValidationError("That day already has 100 unfinished tasks. Move another task first.")
    c.execute("UPDATE tasks SET deleted_at=NULL,reason=NULL WHERE id=?", (task_id,))


def list_trash(c):
    return [dict(r) for r in c.execute("""SELECT id,name,target_date,status,deleted_at
                                         FROM tasks WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC,id DESC""")]


def push_tomorrow(c, day):
    day_value(day)
    tomorrow = (date.fromisoformat(day)+timedelta(days=1)).isoformat()
    count = c.execute("SELECT COUNT(*) FROM tasks WHERE target_date=? AND status!='completed' AND deleted_at IS NULL", (day,)).fetchone()[0]
    existing = c.execute("SELECT COUNT(*) FROM tasks WHERE target_date=? AND status!='completed' AND deleted_at IS NULL", (tomorrow,)).fetchone()[0]
    if count + existing > 100:
        raise ValidationError("Tomorrow would exceed the 100 unfinished task limit.")
    if count:
        c.execute("DELETE FROM blocks WHERE task_id IN (SELECT id FROM tasks WHERE target_date=? AND status!='completed' AND deleted_at IS NULL) AND status='planned'", (day,))
        c.execute("UPDATE tasks SET target_date=?,status='planned',reason=NULL WHERE target_date=? AND status!='completed' AND deleted_at IS NULL", (tomorrow,day))
        # Carry today's windows only if tomorrow has never been configured.
        if not c.execute("SELECT 1 FROM day_settings WHERE day=?", (tomorrow,)).fetchone():
            set_windows(c, tomorrow, get_windows(c, day))
        plan_day(c, tomorrow)
    return {"moved":count,"day":tomorrow}


def snapshot(c, day):
    day_value(day)
    windows, commitments = get_windows(c, day), day_commitments(c, day)
    tasks = []
    for row in c.execute("SELECT * FROM tasks WHERE deleted_at IS NULL AND (target_date=? OR id IN (SELECT task_id FROM blocks WHERE day=?)) ORDER BY id", (day,day)):
        t = dict(row)
        t["completed_minutes"] = completed_minutes(c,t["id"])
        t["remaining_minutes"] = t["duration"]-t["completed_minutes"]
        t["scheduled_minutes"] = c.execute("SELECT COALESCE(SUM(end-start),0) FROM blocks WHERE task_id=? AND day=? AND status='planned'", (t["id"],day)).fetchone()[0]
        t["unslotted_minutes"] = max(0,t["remaining_minutes"]-t["scheduled_minutes"]) if t["target_date"]==day and t["status"]=="planned" else 0
        tasks.append(t)
    blocks = [dict(r) for r in c.execute("""SELECT b.*,
        COALESCE(b.task_name,t.name) AS name, COALESCE(b.task_energy,t.energy) AS energy,
        COALESCE(b.task_priority,t.priority) AS priority, t.deleted_at IS NOT NULL AS task_deleted
        FROM blocks b JOIN tasks t ON t.id=b.task_id WHERE day=? ORDER BY start,b.id""", (day,))]
    committed = merge_ranges([[r["start_minute"],r["end_minute"]] for r in commitments])
    def overlap(a,b):
        return sum(max(0,min(b,y)-max(a,x)) for x,y in windows)
    usable = sum(b-a for a,b in windows)-sum(overlap(a,b) for a,b in committed)
    scheduled = sum(overlap(b["start"],b["end"]) for b in blocks if b["status"] in ("planned","completed"))
    return {"day":day,"windows":windows,"commitments":commitments,"tasks":tasks,"blocks":blocks,"trash":list_trash(c),
            "load":{"scheduled_minutes":scheduled,"usable_minutes":usable,
                    "percent":round(100*scheduled/usable,1) if usable else 0,
                    "unslotted_minutes":sum(t["unslotted_minutes"] for t in tasks),
                    "completed_minutes":sum(b["end"]-b["start"] for b in blocks if b["status"]=="completed")}}
