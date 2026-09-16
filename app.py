"""Run with `python app.py`, then open http://127.0.0.1:5000."""
import argparse
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import engine


def create_app(database_path=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=100_000,
                      DATABASE=str(database_path or os.environ.get("SLOT_DB") or Path(__file__).with_name("slot.db")))
    engine.init_db(app.config["DATABASE"])

    @app.before_request
    def local_only():
        # Local app, no authentication. Reject cross-site browser writes and DNS rebinding.
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return jsonify(error="Use localhost or 127.0.0.1."), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin writes are not allowed."), 403
            if not request.is_json:
                return jsonify(error="Send application/json."), 415

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.errorhandler(engine.ValidationError)
    def validation(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(LookupError)
    def missing(exc):
        return jsonify(error=str(exc)), 404

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(exc):
        app.logger.exception("Database operation failed")
        return jsonify(error="Database is busy or unavailable. Wait for the current operation, then retry."), 503

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        app.logger.exception("Unexpected Slot error")
        return jsonify(error="Slot encountered an error. Check the terminal for details; the failed transaction was rolled back."), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise engine.ValidationError("Send a JSON object.")
        return value

    def db(write=False):
        return engine.database(app.config["DATABASE"], write)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        # Does not wait for database writes or scheduling.
        return jsonify(app="Slot", version="1.1", status="ok")

    @app.get("/api/day")
    def day():
        with db() as c:
            return jsonify(engine.snapshot(c, request.args.get("date")))

    @app.post("/api/tasks")
    def create_task():
        with db(True) as c:
            task_id = engine.add_task(c, body())
        return jsonify(id=task_id), 201

    @app.post("/api/tasks/bulk")
    def bulk():
        data = body()
        tasks = engine.parse_bulk(data.get("text"), data.get("day"))
        with db(True) as c:
            ids = [engine.add_task(c,t) for t in tasks]
        return jsonify(ids=ids), 201

    @app.patch("/api/tasks/<int:task_id>")
    def edit_task(task_id):
        with db(True) as c:
            engine.update_task(c,task_id,body())
        return jsonify(ok=True)

    @app.delete("/api/tasks/<int:task_id>")
    def remove_task(task_id):
        with db(True) as c:
            engine.delete_task(c, task_id)
        return jsonify(ok=True)

    @app.post("/api/tasks/<int:task_id>/restore")
    def restore_task(task_id):
        with db(True) as c:
            engine.restore_task(c, task_id)
        return jsonify(ok=True)

    @app.post("/api/tasks/<int:task_id>/status")
    def task_status(task_id):
        with db(True) as c:
            engine.set_task_status(c,task_id,body().get("status"))
        return jsonify(ok=True)

    @app.post("/api/blocks/<int:block_id>/status")
    def block_status(block_id):
        with db(True) as c:
            engine.set_block_status(c,block_id,body().get("status"))
        return jsonify(ok=True)

    @app.put("/api/windows")
    def windows():
        data = body()
        with db(True) as c:
            engine.set_windows(c,data.get("day"),data.get("windows"))
        return jsonify(ok=True)

    @app.post("/api/commitments")
    def commitment():
        with db(True) as c:
            item_id = engine.add_commitment(c,body())
        return jsonify(id=item_id), 201

    @app.patch("/api/commitments/<int:item_id>")
    def edit_commitment(item_id):
        with db(True) as c:
            engine.update_commitment(c, item_id, body())
        return jsonify(ok=True)

    @app.delete("/api/commitments/<int:item_id>")
    def remove_commitment(item_id):
        with db(True) as c:
            engine.delete_commitment(c,item_id)
        return jsonify(ok=True)

    @app.post("/api/plan")
    def plan():
        data = body()
        with db(True) as c:
            result = engine.plan_day(c,data.get("day"))
        return jsonify(result)

    @app.post("/api/push")
    def push():
        data = body()
        with db(True) as c:
            result = engine.push_tomorrow(c,data.get("day"))
        return jsonify(result)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Slot: local daily scheduling")
    parser.add_argument("--port", type=int, default=5000, help="Local port (default: 5000)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be from 1 to 65535.")
    app = create_app()
    print(f"Slot 1.1 · Open http://127.0.0.1:{args.port} · Keep this terminal open.", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False)
