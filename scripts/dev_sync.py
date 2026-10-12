#!/usr/bin/env python3
"""Stage development files for the independent Blender timer bridge (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "build" / "dev-sync"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(source):
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if not path.is_file() or any(part.startswith(".") or part in {"__pycache__", "Release"} for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".zip", ".tmp", ".swp"}:
            continue
        files[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def publish(source, files, rebuild_layout, timeout):
    connection = read_json(CONTROL / "connection.json")
    if time.time() - connection["heartbeat"] > 5:
        raise RuntimeError("Blender bridge is offline; run blender_dev_bridge.py in Blender first")
    request_path = CONTROL / "request.json"
    if request_path.exists():
        raise RuntimeError("An update is already pending (see build/dev-sync/status.json)")
    request_id = uuid.uuid4().hex
    stage = CONTROL / "stages" / request_id
    try:
        for name, digest in files.items():
            path = source / name
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError("Source changed while staging; retry after saving")
            if path.suffix == ".py":
                compile(data, str(path), "exec")  # No tests, imports or pycache writes.
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        write_json(request_path, {"id": request_id, "instance": connection["instance"],
                                 "files": files, "rebuild_layout": rebuild_layout})
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"[{request_id[:8]}] staged {len(files)} files", flush=True)
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            status = read_json(CONTROL / "status.json")
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.25)
            continue
        if status.get("id") == request_id:
            message = (status["state"], status.get("message", ""))
            if message != last:
                print(f"[{request_id[:8]}] {message[0]}: {message[1]}", flush=True)
                last = message
            if status["state"] in {"reloaded", "unchanged", "restart_required", "failed"}:
                return status["state"] in {"reloaded", "unchanged"}
        time.sleep(0.25)
    raise RuntimeError("Timed out; update remains pending. Finish Blender interaction or stop the bridge to cancel it")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--rebuild-layout", action="store_true", help="Rebuild the Studio layout when restoring the project")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    source = ROOT / "quick_sdf_blender"
    previous = None
    while True:
        try:
            files = snapshot(source)
            try:
                connection = read_json(CONTROL / "connection.json")
                instance = connection["instance"] if time.time() - connection["heartbeat"] <= 5 else None
            except (OSError, ValueError, KeyError):
                instance = None
            revision = (files, instance)
            if revision != previous:
                if args.watch:
                    time.sleep(0.8)
                    if snapshot(source) != files:
                        continue
                success = publish(source, files, args.rebuild_layout, args.timeout)
                previous = revision
                if not args.watch:
                    return 0 if success else 1
        except (OSError, ValueError, RuntimeError, SyntaxError) as exc:
            print(f"Sync error: {exc}", flush=True)
            if not args.watch:
                return 1
            # Retry after the next edit, rather than flooding Blender on failure.
            previous = revision if 'revision' in locals() else None
        if not args.watch:
            return 0
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Watcher stopped (Blender bridge remains active).")
