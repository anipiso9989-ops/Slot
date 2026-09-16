"""Behavioral tests. Run: python -m pytest -q"""
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

import engine as e
from app import create_app

DAY = "2026-10-01"
NEXT = "2026-10-02"


@pytest.fixture
def path(tmp_path):
    p = tmp_path / "test.db"
    e.init_db(p)
    return p


def task(**kw):
    return {"name":"Write", "duration":60, "priority":"high", "deadline":DAY+" 17:00",
            "energy":"deep", "min_chunk":25,"max_chunk":60,"target_date":DAY, **kw}


def blocks(c, day=DAY):
    return [dict(r) for r in c.execute("SELECT * FROM blocks WHERE day=? AND status!='skipped' ORDER BY start", (day,))]


def assert_valid(c, day=DAY):
    snap = e.snapshot(c,day)
    active = blocks(c,day)
    for first, second in zip(active,active[1:]):
        assert first["end"] <= second["start"]
    windows = e.merge_ranges(snap["windows"])
    for b in active:
        t = e.get_task(c,b["task_id"], include_deleted=True)
        if b["status"] == "planned":
            assert t["deleted_at"] is None
            assert t["min_chunk"] <= b["end"]-b["start"] <= t["max_chunk"]
            assert any(a <= b["start"] < b["end"] <= z for a,z in windows)
            assert datetime.fromisoformat(day)+timedelta(minutes=b["end"]) <= datetime.fromisoformat(t["deadline"])
        for fixed in snap["commitments"]:
            assert b["end"] <= fixed["start_minute"] or b["start"] >= fixed["end_minute"]
    for t in snap["tasks"]:
        allocated = c.execute("SELECT COALESCE(SUM(end-start),0) FROM blocks WHERE task_id=? AND status IN ('planned','completed')", (t["id"],)).fetchone()[0]
        assert allocated <= t["duration"]
        if t["scheduled_minutes"]:
            assert allocated == t["duration"]
    assert 0 <= snap["load"]["percent"] <= 100


def test_flexible_chunks_fit_fragmented_gaps(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,600],[630,660]])
        tid=e.add_task(c,task(duration=90,min_chunk=25,max_chunk=60))
        e.plan_day(c,DAY)
        actual=blocks(c)
        assert sum(b["end"]-b["start"] for b in actual)==90
        assert all(b["task_id"]==tid for b in actual)
        assert_valid(c)


def test_solver_can_rechunk_and_move_previously_admitted_tasks(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,630]])
        first=e.add_task(c,task(duration=60,min_chunk=30,max_chunk=60,deadline=DAY+" 10:30"))
        second=e.add_task(c,task(name="Early",duration=30,min_chunk=30,max_chunk=30,priority="low",deadline=DAY+" 09:30"))
        e.plan_day(c,DAY)
        b=blocks(c)
        assert {x["task_id"] for x in b}=={first,second}
        assert b[0]["task_id"]==second
        assert_valid(c)


def test_priority_overload_and_exact_deadline(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,600]])
        low=e.add_task(c,task(priority="low"))
        high=e.add_task(c,task(deadline=DAY+" 10:00"))
        result=e.plan_day(c,DAY)
        assert result["unslotted_tasks"]==1
        assert {b["task_id"] for b in blocks(c)}=={high}
        assert e.get_task(c,low)["reason"]
        assert_valid(c)


def test_deadline_and_incompatible_gaps_reported(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,560],[600,620],[660,680]])
        tid=e.add_task(c,task(min_chunk=30,max_chunk=60))
        past=e.add_task(c,task(deadline="2026-09-30 17:00"))
        e.plan_day(c,DAY)
        assert not blocks(c)
        assert "chunks" in e.get_task(c,tid)["reason"]
        assert "Deadline" in e.get_task(c,past)["reason"]


def test_deep_before_shallow_and_deterministic(path):
    with e.database(path,True) as c:
        shallow=e.add_task(c,task(energy="shallow"))
        deep=e.add_task(c,task())
        signatures=[]
        for _ in range(3):
            e.plan_day(c,DAY)
            b=blocks(c)
            signatures.append([(x["task_id"],x["start"],x["end"]) for x in b])
            assert b[0]["task_id"]==deep
            assert_valid(c)
        assert signatures[0]==signatures[1]==signatures[2]
        assert shallow!=deep


def test_partial_completion_rollover_and_history(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,660]])
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30,deadline=NEXT+" 17:00"))
        e.plan_day(c,DAY)
        b=blocks(c)[0]
        e.set_block_status(c,b["id"],"completed")
        history=dict(c.execute("SELECT * FROM blocks WHERE id=?",(b["id"],)).fetchone())
        e.plan_day(c,DAY)
        assert dict(c.execute("SELECT * FROM blocks WHERE id=?",(b["id"],)).fetchone())==history
        assert_valid(c)
        assert e.push_tomorrow(c,DAY)=={"moved":1,"day":NEXT}
        assert e.completed_minutes(c,tid)==30
        assert sum(x["end"]-x["start"] for x in blocks(c,NEXT))==60
        assert blocks(c)==[history]
        assert e.get_task(c,tid)["deadline"]==NEXT+" 17:00"
        assert e.push_tomorrow(c,DAY)["moved"]==0
        assert_valid(c,NEXT)
        for x in blocks(c,NEXT):
            e.set_block_status(c,x["id"],"completed")
        assert e.get_task(c,tid)["status"]=="completed"
        assert e.push_tomorrow(c,NEXT)["moved"]==0


def test_skipped_blocks_do_not_count_and_push_restores_task(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(deadline=NEXT+" 17:00"))
        e.plan_day(c,DAY)
        b=blocks(c)[0]
        e.set_block_status(c,b["id"],"skipped")
        assert e.completed_minutes(c,tid)==0
        e.set_task_status(c,tid,"skipped")
        e.plan_day(c,DAY)
        assert not blocks(c)
        e.push_tomorrow(c,DAY)
        assert e.get_task(c,tid)["status"]=="planned"
        assert sum(x["end"]-x["start"] for x in blocks(c,NEXT))==60
        assert c.execute("SELECT status FROM blocks WHERE id=?",(b["id"],)).fetchone()[0]=="skipped"


def test_deadlines_not_extended_by_push(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task())
        e.plan_day(c,DAY)
        e.push_tomorrow(c,DAY)
        assert not blocks(c,NEXT)
        assert e.get_task(c,tid)["deadline"]==DAY+" 17:00"
        assert "Deadline" in e.get_task(c,tid)["reason"]


def test_completed_history_outside_changed_hours(path):
    with e.database(path,True) as c:
        e.add_task(c,task())
        e.plan_day(c,DAY)
        first=blocks(c)[0]
        e.set_block_status(c,first["id"],"completed")
        e.set_windows(c,DAY,[])
        e.plan_day(c,DAY)
        snap=e.snapshot(c,DAY)
        assert snap["load"]["usable_minutes"]==0
        assert snap["load"]["percent"]==0
        assert snap["load"]["completed_minutes"]==first["end"]-first["start"]


def test_commitments_clipped_and_load(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,720],[780,1020]])
        e.add_commitment(c,dict(name="Overnight",start="2026-09-30 23:00",end=DAY+" 10:00"))
        e.add_commitment(c,dict(name="Lunch",start=DAY+" 11:30",end=DAY+" 13:30"))
        e.add_task(c,task(duration=120))
        e.plan_day(c,DAY)
        snap=e.snapshot(c,DAY)
        assert snap["load"]["usable_minutes"]==300
        assert snap["load"]["scheduled_minutes"]==120
        assert snap["load"]["percent"]==40
        assert_valid(c)


def test_reject_commitment_overlap_completed_and_preserve_plan(path):
    with e.database(path,True) as c:
        e.add_task(c,task())
        e.plan_day(c,DAY)
        b=blocks(c)[0]
        e.set_block_status(c,b["id"],"completed")
    with pytest.raises(e.ValidationError),e.database(path,True) as c:
        e.add_commitment(c,dict(name="Conflict",start=DAY+" 09:00",end=DAY+" 17:00"))
    with e.database(path) as c:
        assert c.execute("SELECT COUNT(*) FROM commitments").fetchone()[0]==0
        assert c.execute("SELECT status FROM blocks WHERE id=?",(b["id"],)).fetchone()[0]=="completed"


def test_commitments_cannot_overlap(path):
    with e.database(path,True) as c:
        e.add_commitment(c,dict(name="One",start=DAY+" 10:00",end=DAY+" 11:00"))
        with pytest.raises(e.ValidationError):
            e.add_commitment(c,dict(name="Two",start=DAY+" 10:30",end=DAY+" 12:00"))


@pytest.mark.parametrize("change",[
    {"duration":0},{"duration":True},{"duration":60.5},{"min_chunk":70},
    {"duration":65,"min_chunk":40,"max_chunk":60}, {"priority":"urgent"},
    {"energy":"super"},{"deadline":DAY+" 25:00"},{"deadline":DAY+"T12:00:30"},
    {"target_date":"no"},{"name":"   "},{"max_chunk":0},{"priority":[]},
])
def test_invalid_task(change):
    with pytest.raises(e.ValidationError):
        e.validate_task(task(**change))


def test_bulk_quotes_optional_chunks_and_line_errors():
    parsed=e.parse_bulk('"Read, annotate", 60m, high, 2026-10-01 17:00, deep, 20m, 30m\n\nEmail, 15m, low, 2026-10-01 18:00, shallow',DAY)
    assert parsed[0]["name"]=="Read, annotate"
    assert parsed[0]["min_chunk"]==20
    assert parsed[1]["min_chunk"]==15
    with pytest.raises(e.ValidationError,match="Line 2"):
        e.parse_bulk('Valid, 60m, high, 2026-10-01 17:00, deep\nInvalid',DAY)


def test_transaction_rolls_back_changes_and_invalidation(path):
    with e.database(path,True) as c:
        e.add_task(c,task())
        e.plan_day(c,DAY)
        before=blocks(c)
    with pytest.raises(RuntimeError),e.database(path,True) as c:
        e.add_task(c,task(name="Should vanish"))
        raise RuntimeError("Simulated failure")
    with e.database(path) as c:
        assert blocks(c)==before
        assert c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]==1


def test_foreign_keys_enabled(path):
    with pytest.raises(sqlite3.IntegrityError),e.database(path,True) as c:
        c.execute("INSERT INTO blocks(task_id,day,start,end) VALUES(999,?,540,600)",(DAY,))


def test_stale_block_ids_never_reused(path):
    with e.database(path,True) as c:
        e.add_task(c,task())
        e.plan_day(c,DAY)
        old=blocks(c)[0]["id"]
        e.plan_day(c,DAY)
        assert all(b["id"]!=old for b in blocks(c))
        with pytest.raises(LookupError):
            e.set_block_status(c,old,"completed")


def test_partial_task_can_change_duration_without_erasing_progress(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30))
        e.plan_day(c,DAY)
        e.set_block_status(c,blocks(c)[0]["id"],"completed")
        e.update_task(c,tid,{"duration":120})
        e.update_task(c,tid,{"deadline":NEXT+" 17:00"})
        assert e.completed_minutes(c,tid)==30
        e.plan_day(c,DAY)
        assert sum(b["end"]-b["start"] for b in blocks(c) if b["status"]=="planned")==90
        assert_valid(c)


def test_edit_every_task_attribute_and_move(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task())
        e.add_task(c,task(name="Tomorrow task",target_date=NEXT,deadline=NEXT+" 17:00"))
        e.plan_day(c,DAY)
        e.plan_day(c,NEXT)
        e.update_task(c,tid,dict(name="Revised",duration=90,priority="low",energy="shallow",
                               deadline=NEXT+" 12:00",min_chunk=15,max_chunk=45,target_date=NEXT))
        saved=e.get_task(c,tid)
        assert (saved["name"],saved["duration"],saved["priority"],saved["energy"])==("Revised",90,"low","shallow")
        assert (saved["min_chunk"],saved["max_chunk"],saved["target_date"],saved["deadline"])==(15,45,NEXT,NEXT+" 12:00")
        assert not blocks(c) and not blocks(c,NEXT)
        e.plan_day(c,NEXT)
        assert_valid(c,NEXT)


def test_new_chunk_limits_apply_only_to_remaining_work(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30))
        e.plan_day(c,DAY)
        first=blocks(c)[0]
        e.set_block_status(c,first["id"],"completed")
        # Total 90 is not splittable into 40–60m chunks, but remaining 60 is.
        e.update_task(c,tid,{"min_chunk":40,"max_chunk":60})
        e.plan_day(c,DAY)
        assert e.completed_minutes(c,tid)==30
        assert [b["end"]-b["start"] for b in blocks(c) if b["status"]=="planned"]==[60]
        assert_valid(c)


@pytest.mark.parametrize("change",[{"duration":20},{"duration":70,"min_chunk":45,"max_chunk":60}])
def test_invalid_partial_edit_rolls_back_task_and_plan(path,change):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30))
        e.plan_day(c,DAY)
        e.set_block_status(c,blocks(c)[0]["id"],"completed")
        before=e.snapshot(c,DAY)
    with pytest.raises(e.ValidationError),e.database(path,True) as c:
        e.update_task(c,tid,change)
    with e.database(path) as c:
        assert e.snapshot(c,DAY)==before


def test_rename_and_energy_edits_preserve_original_history_labels(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30))
        e.plan_day(c,DAY)
        e.set_block_status(c,blocks(c)[0]["id"],"completed")
        old=e.snapshot(c,DAY)["blocks"][0]
        e.update_task(c,tid,{"name":"New name"})
        after=e.snapshot(c,DAY)["blocks"]
        assert after[0]==old
        assert all(b["name"]=="New name" for b in after if b["status"]=="planned")
        e.update_task(c,tid,{"energy":"shallow","priority":"low","target_date":NEXT,"deadline":NEXT+" 17:00"})
        assert e.snapshot(c,DAY)["blocks"]==[old]
        e.plan_day(c,NEXT)
        assert all(b["energy"]=="shallow" for b in e.snapshot(c,NEXT)["blocks"])
        assert_valid(c,NEXT)


def test_completed_task_edit_reopen_and_reduce_to_completed(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(min_chunk=30,max_chunk=30))
        e.plan_day(c,DAY)
        e.set_task_status(c,tid,"completed")
        original=e.snapshot(c,DAY)["blocks"]
        e.update_task(c,tid,{"name":"Renamed complete task","min_chunk":90,"max_chunk":120})
        assert e.get_task(c,tid)["status"]=="completed"
        e.update_task(c,tid,{"duration":150,"target_date":NEXT,"deadline":NEXT+" 17:00"})
        assert e.get_task(c,tid)["status"]=="planned"
        assert e.completed_minutes(c,tid)==60
        e.plan_day(c,NEXT)
        assert sum(b["end"]-b["start"] for b in blocks(c,NEXT))==90
        assert e.snapshot(c,DAY)["blocks"]==original
        e.update_task(c,tid,{"duration":60})
        assert e.get_task(c,tid)["status"]=="completed"
        assert not blocks(c,NEXT)


@pytest.mark.parametrize("state",["unslotted","planned","partial","skipped","completed"])
def test_delete_any_task_state_restore_and_preserve_history(path,state):
    with e.database(path,True) as c:
        tid=e.add_task(c,task(duration=90,min_chunk=30,max_chunk=30,deadline=NEXT+" 17:00"))
        if state!="unslotted":
            e.plan_day(c,DAY)
        if state=="partial":
            e.set_block_status(c,blocks(c)[0]["id"],"completed")
        if state=="skipped":
            e.set_task_status(c,tid,"skipped")
        if state=="completed":
            e.set_task_status(c,tid,"completed")
        history=[dict(r) for r in c.execute("SELECT * FROM blocks WHERE status!='planned'")]
        e.delete_task(c,tid)
        e.delete_task(c,tid)  # Repeat deletion is harmless.
        assert e.snapshot(c,DAY)["tasks"]==[]
        assert len(e.snapshot(c,DAY)["trash"])==1
        assert c.execute("SELECT COUNT(*) FROM blocks WHERE status='planned'").fetchone()[0]==0
        e.plan_day(c,DAY)
        assert e.push_tomorrow(c,DAY)["moved"]==0
        assert [dict(r) for r in c.execute("SELECT * FROM blocks")]==history
        with pytest.raises(LookupError):
            e.update_task(c,tid,{"name":"No"})
        e.restore_task(c,tid)
        e.restore_task(c,tid)
        assert e.snapshot(c,DAY)["trash"]==[]
        assert e.get_task(c,tid)["target_date"]==DAY
        expected="planned" if state in ("unslotted","planned","partial") else state
        assert e.get_task(c,tid)["status"]==expected
        e.plan_day(c,DAY)
        assert_valid(c)


def test_deleted_completed_work_still_blocks_calendar_and_counts_load(path):
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[540,600]])
        tid=e.add_task(c,task())
        e.plan_day(c,DAY)
        e.set_task_status(c,tid,"completed")
        e.delete_task(c,tid)
        other=e.add_task(c,task())
        e.plan_day(c,DAY)
        assert e.get_task(c,other)["reason"]
        snap=e.snapshot(c,DAY)
        assert snap["load"]["completed_minutes"]==60
        assert snap["load"]["percent"]==100
        assert snap["blocks"][0]["task_deleted"]==1
        assert_valid(c)


def test_task_limit_on_move_reopen_restore_and_deleted_exclusion(path):
    with e.database(path,True) as c:
        ids=[e.add_task(c,task(name=f"Task {i}",target_date=NEXT)) for i in range(100)]
        tid=e.add_task(c,task())
        with pytest.raises(e.ValidationError):
            e.update_task(c,tid,{"target_date":NEXT})
        e.delete_task(c,ids[0])
        e.update_task(c,tid,{"target_date":NEXT})
        with pytest.raises(e.ValidationError):
            e.restore_task(c,ids[0])
        assert e.get_task(c,ids[0],include_deleted=True)["deleted_at"]


def test_mark_whole_task_completed_requires_allocated_work(path):
    with e.database(path,True) as c:
        tid=e.add_task(c,task())
        with pytest.raises(e.ValidationError,match="Schedule"):
            e.set_task_status(c,tid,"completed")
        e.plan_day(c,DAY)
        e.set_task_status(c,tid,"completed")
        e.set_task_status(c,tid,"completed")
        assert e.completed_minutes(c,tid)==60
        assert e.get_task(c,tid)["status"]=="completed"


def test_commitment_edit_in_place_and_across_dates(path):
    with e.database(path,True) as c:
        cid=e.add_commitment(c,dict(name="Lunch",start=DAY+" 12:00",end=DAY+" 13:00"))
        e.add_task(c,task())
        e.add_task(c,task(target_date=NEXT,deadline=NEXT+" 17:00"))
        e.plan_day(c,DAY)
        e.plan_day(c,NEXT)
        e.update_commitment(c,cid,{"name":"New lunch","start":NEXT+" 12:30","end":NEXT+" 13:30"})
        assert e.day_commitments(c,DAY)==[]
        assert e.day_commitments(c,NEXT)[0]["id"]==cid
        assert not blocks(c) and not blocks(c,NEXT)
        e.update_commitment(c,cid,{"name":"Renamed"})
        assert e.day_commitments(c,NEXT)[0]["name"]=="Renamed"


def test_invalid_commitment_edit_is_atomic(path):
    with e.database(path,True) as c:
        cid=e.add_commitment(c,dict(name="Lunch",start=DAY+" 12:00",end=DAY+" 13:00"))
        e.add_task(c,task())
        e.plan_day(c,DAY)
        e.set_task_status(c,1,"completed")
        before=e.snapshot(c,DAY)
    with pytest.raises(e.ValidationError),e.database(path,True) as c:
        e.update_commitment(c,cid,{"start":DAY+" 09:00"})
    with e.database(path) as c:
        assert e.snapshot(c,DAY)==before


def test_legacy_database_migration_preserves_records_and_is_repeatable(tmp_path):
    path=tmp_path/"legacy.db"
    with sqlite3.connect(path) as c:
        c.executescript("""
          CREATE TABLE tasks(id INTEGER PRIMARY KEY, name TEXT NOT NULL, duration INTEGER NOT NULL,
            priority TEXT NOT NULL, deadline TEXT NOT NULL, energy TEXT NOT NULL,
            min_chunk INTEGER NOT NULL,max_chunk INTEGER NOT NULL,target_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',reason TEXT);
          CREATE TABLE blocks(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER NOT NULL REFERENCES tasks(id),
            day TEXT NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'planned');
          INSERT INTO tasks VALUES(7,'Original name',90,'high','2026-10-02 17:00','deep',30,30,'2026-10-01','planned',NULL);
          INSERT INTO blocks VALUES(12,7,'2026-10-01',540,570,'completed');
          INSERT INTO blocks VALUES(13,7,'2026-10-01',570,600,'planned');
          INSERT INTO blocks VALUES(14,7,'2026-10-01',600,630,'planned');
        """)
    e.init_db(path)
    with e.database(path) as c:
        before=e.snapshot(c,DAY)
        assert before["tasks"][0]["id"]==7
        assert before["tasks"][0]["completed_minutes"]==30
        assert before["blocks"][0]["name"]=="Original name"
        assert c.execute("PRAGMA user_version").fetchone()[0]==2
    e.init_db(path)
    with e.database(path,True) as c:
        assert e.snapshot(c,DAY)==before
        e.update_task(c,7,{"name":"New name","duration":120,"target_date":NEXT})
        e.plan_day(c,NEXT)
        assert e.snapshot(c,DAY)["blocks"][0]["name"]=="Original name"
        assert_valid(c,NEXT)


def test_search_unknown_is_not_called_infeasible(path,monkeypatch):
    with e.database(path,True) as c:
        tid=e.add_task(c,task())
        monkeypatch.setattr(e,"solve",lambda *args,**kw:(e.cp_model.UNKNOWN,[]))
        e.plan_day(c,DAY)
        assert "unknown" in e.get_task(c,tid)["reason"]


def test_window_validation_and_midnight(path):
    with e.database(path,True) as c:
        with pytest.raises(e.ValidationError):
            e.set_windows(c,DAY,[[540,700],[600,800]])
        e.set_windows(c,DAY,[[1380,1440]])
        e.add_task(c,task(deadline=NEXT+" 00:00"))
        e.plan_day(c,DAY)
        assert blocks(c)[-1]["end"]==1440
        assert_valid(c)


def test_reopen_persistence(path):
    with e.database(path,True) as c:
        e.add_task(c,task())
        e.plan_day(c,DAY)
        before=e.snapshot(c,DAY)
    e.init_db(path)
    with e.database(path) as c:
        assert e.snapshot(c,DAY)==before


@pytest.mark.parametrize("seed",range(6))
def test_generated_schedules_hold_invariants(path,seed):
    rng=random.Random(seed)
    with e.database(path,True) as c:
        e.set_windows(c,DAY,[[480,720],[780,1080]])
        e.add_commitment(c,dict(name="Meeting",start=DAY+" 10:10",end=DAY+" 10:50"))
        for i in range(8):
            e.add_task(c,task(name=f"Task {i}",duration=rng.choice([30,60,90]),min_chunk=15,
                              max_chunk=rng.choice([30,45,60]),priority=rng.choice(list(e.PRIORITIES)),
                              energy=rng.choice(["deep","shallow"]),deadline=DAY+f" {rng.choice([10,12,15,18]):02}:00"))
        e.plan_day(c,DAY)
        assert_valid(c)


@pytest.fixture
def client(path):
    app=create_app(path)
    app.config["TESTING"]=True
    return app.test_client()


def test_api_complete_flow(client):
    assert client.get("/").status_code==200
    assert client.post("/api/tasks",json=task(deadline=NEXT+" 17:00")).status_code==201
    assert client.post("/api/plan",json={"day":DAY}).status_code==200
    snap=client.get("/api/day?date="+DAY).json
    bid=snap["blocks"][0]["id"]
    assert client.post(f"/api/blocks/{bid}/status",json={"status":"completed"}).status_code==200
    assert client.post("/api/push",json={"day":DAY}).status_code==200
    assert client.get("/api/day?date="+DAY).json["load"]["completed_minutes"]>0


def test_api_bulk_atomic(client):
    response=client.post("/api/tasks/bulk",json={"day":DAY,"text":"Good, 60m, high, 2026-10-01 17:00, deep\nBad"})
    assert response.status_code==400
    assert client.get("/api/day?date="+DAY).json["tasks"]==[]


def test_api_errors_and_cross_site_writes(client):
    assert client.post("/api/tasks",json=[]).status_code==400
    assert client.post("/api/tasks",data="{}").status_code==415
    assert client.post("/api/plan",json={"day":DAY},headers={"Origin":"https://evil.example"}).status_code==403
    assert client.get("/",base_url="http://evil.example").status_code==403
    assert client.get("/api/day?date=bad").status_code==400
    assert client.post("/api/tasks/999/status",json={"status":"skipped"}).status_code==404
    assert client.post("/api/tasks",data="{",content_type="application/json").status_code==400


def test_api_edit_and_fixed_commitments(client):
    tid=client.post("/api/tasks",json=task()).json["id"]
    assert client.patch(f"/api/tasks/{tid}",json={"name":"Revised"}).status_code==200
    cid=client.post("/api/commitments",json={"name":"Lunch","start":DAY+" 12:00","end":DAY+" 13:00"}).json["id"]
    assert client.put("/api/windows",json={"day":DAY,"windows":[[540,1020]]}).status_code==200
    assert client.post("/api/plan",json={"day":DAY}).status_code==200
    assert client.get("/api/day?date="+DAY).json["load"]["usable_minutes"]==420
    assert client.delete(f"/api/commitments/{cid}",json={}).status_code==200
    assert client.get("/api/day?date="+DAY).json["blocks"]==[]


def test_api_full_edit_delete_restore_and_health(client):
    assert client.get("/api/health").json=={"app":"Slot","version":"1.1","status":"ok"}
    tid=client.post("/api/tasks",json=task()).json["id"]
    details={"duration":90,"min_chunk":30,"max_chunk":45,"target_date":NEXT,"deadline":NEXT+" 17:00"}
    assert client.patch(f"/api/tasks/{tid}",json=details).status_code==200
    assert client.delete(f"/api/tasks/{tid}",json={}).status_code==200
    snap=client.get("/api/day?date="+NEXT).json
    assert snap["tasks"]==[] and len(snap["trash"])==1
    assert client.post(f"/api/tasks/{tid}/restore",json={}).status_code==200
    assert client.get("/api/day?date="+NEXT).json["tasks"][0]["duration"]==90
    cid=client.post("/api/commitments",json={"name":"Lunch","start":NEXT+" 12:00","end":NEXT+" 13:00"}).json["id"]
    assert client.patch(f"/api/commitments/{cid}",json={"start":NEXT+" 12:30"}).status_code==200
    assert client.patch(f"/api/commitments/{cid}",json={"start":NEXT+" 14:00"}).status_code==400
    assert client.delete("/api/tasks/9999",json={}).status_code==404


def test_api_unexpected_errors_return_json_and_rollback(client,monkeypatch):
    def broken(c,day):
        e.add_task(c,task())
        raise RuntimeError("simulated")
    monkeypatch.setattr(e,"plan_day",broken)
    response=client.post("/api/plan",json={"day":DAY})
    assert response.status_code==500 and "error" in response.json
    assert client.get("/api/day?date="+DAY).json["tasks"]==[]
