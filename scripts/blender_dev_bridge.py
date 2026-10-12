# SPDX-License-Identifier: GPL-3.0-or-later
"""Run once in Blender's Python Console; independent of the extension being reloaded."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import types
import uuid

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "build" / "dev-sync"
STATE_KEY = "_quicksdf_dev_bridge_state"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def status(state, request_id, outcome, message):
    write_json(CONTROL / "status.json", {"id": request_id, "state": outcome,
                                       "message": message, "time": time.time()})
    print(f"Quick SDF dev sync: {outcome}: {message}")


def busy(state):
    # Any native modal interaction includes paint strokes and gizmo drags.
    if any(tuple(getattr(window, "modal_operators", ())) for window in bpy.context.window_manager.windows):
        return "Finish the current modal interaction"
    studio = sys.modules.get(state.name + ".studio")
    operators = sys.modules.get(state.name + ".operators")
    if operators and (operators._BAKE_JOB or operators._EXPORT_JOB):
        return "Bake/export is running"
    if studio:
        if studio._PENDING or studio._SWITCH_PENDING or studio._PROVISIONAL_JOB:
            return "Studio is opening/switching or preparing a key"
        session = studio.current_session()
        if session and (session.stroke_brush_color is not None or session.stroke_unified_color is not None):
            return "Paint stroke cleanup is pending"
        if session and session.provisional_uuid:
            return "Select a committed key first; temporary Canvas must not be discarded"
        if session and session.editing_aux_mask_uuid:
            return "Leave auxiliary-mask editing first"
    return None


def clear_modules(name):
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]
    importlib.invalidate_caches()


def enable(name):
    errors = []
    addon_utils.enable(name, default_set=False, handle_error=lambda exc: errors.append(str(exc)))
    if errors or not addon_utils.check(name)[1]:
        raise RuntimeError("Registration failed: " + "; ".join(errors))


def restore(state, saved, rebuild):
    if not saved:
        return
    studio = importlib.import_module(state.name + ".studio")
    project = studio._resolve_project_uuid(saved["project"])
    window = studio.find_window(saved["window"])
    if project is None or window is None:
        raise RuntimeError("The Studio project/window disappeared during reload")
    with bpy.context.temp_override(window=window):
        options = {"preserve_layout": not rebuild} if "preserve_layout" in inspect.signature(studio.enter_studio).parameters else {}
        studio.enter_studio(bpy.context, project, **options)


def apply(state, request):
    request_id = request["id"]
    if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
        raise ValueError("Invalid request ID")
    stage = CONTROL / "stages" / request_id
    files = request["files"]
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Invalid relative path")
        data = (stage / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Staged checksum mismatch: {name}")
        if relative.suffix == ".py":
            compile(data, str(relative), "exec")
    managed_path = CONTROL / "managed.json"
    managed = read_json(managed_path) if managed_path.exists() else {}
    previous = set(managed.get("files", ())) if managed.get("destination") == str(state.destination) else set()
    changed = [name for name, digest in files.items()
               if not (state.destination / name).is_file()
               or hashlib.sha256((state.destination / name).read_bytes()).hexdigest() != digest]
    removed = sorted(previous - files.keys())
    if not changed and not removed and not request.get("rebuild_layout", False):
        write_json(managed_path, {"destination": str(state.destination), "files": sorted(files)})
        status(state, request_id, "unchanged", "Installed files already match")
        return
    restricted = [name for name in changed + removed
                  if name in {"model.py", "blender_manifest.toml"} or Path(name).suffix.lower() in {".dll", ".dylib", ".so"}]
    if restricted:
        status(state, request_id, "restart_required", "No files applied. Install with Blender closed and restart: " + ", ".join(restricted))
        return
    studio = sys.modules.get(state.name + ".studio")
    session = studio.current_session() if studio else None
    saved = {"project": session.project_uuid, "window": session.window_pointer} if session else None
    old_layout = getattr(studio, "WORKSPACE_LAYOUT_VERSION", None)
    backup = {name: (state.destination / name).read_bytes() if (state.destination / name).is_file() else None
              for name in changed + removed}
    status(state, request_id, "syncing", f"Applying {len(changed)} changed and {len(removed)} removed files")
    try:
        # Run OLD unregister before changing its files. Blender APIs stay on this timer's main thread.
        residency = importlib.import_module(state.name + ".residency")
        residency.flush_dirty()
        window = studio.find_window(saved["window"]) if saved else bpy.context.window
        with bpy.context.temp_override(window=window):
            addon_utils.disable(state.name, default_set=False)
        for name in changed:
            target = state.destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / name, target)
        for name in removed:
            (state.destination / name).unlink(missing_ok=True)
        # Avoid same-second/same-size Python bytecode hits after file replacement.
        for cache in list(state.destination.rglob("__pycache__")):
            shutil.rmtree(cache)
        clear_modules(state.name)
        enable(state.name)
        new_studio = importlib.import_module(state.name + ".studio")
        rebuild = request.get("rebuild_layout", False) or old_layout != new_studio.WORKSPACE_LAYOUT_VERSION
        restore(state, saved, rebuild)
        write_json(managed_path, {"destination": str(state.destination), "files": sorted(files)})
    except Exception:
        failure = traceback.format_exc()
        try:
            if addon_utils.check(state.name)[1]:
                addon_utils.disable(state.name, default_set=False)
            for name, data in backup.items():
                target = state.destination / name
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(data)
            for cache in list(state.destination.rglob("__pycache__")):
                shutil.rmtree(cache)
            clear_modules(state.name)
            enable(state.name)
            restore(state, saved, False)
            failure += "\nPrevious installed files restored."
        except Exception:
            failure += "\nRollback failed:\n" + traceback.format_exc()
        status(state, request_id, "failed", failure)
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    if saved:
        state.restoring = (request_id, saved, time.monotonic())
        status(state, request_id, "restoring", "Files synchronized and plugin registered; waiting for Studio")
    else:
        status(state, request_id, "reloaded", "Files synchronized and plugin registered")


def tick(state):
    try:
        write_json(CONTROL / "connection.json", {"instance": state.instance, "pid": os.getpid(),
                   "module": state.name, "destination": str(state.destination), "heartbeat": time.time()})
        if state.restoring:
            request_id, saved, started = state.restoring
            studio = sys.modules.get(state.name + ".studio")
            session = studio.current_session() if studio else None
            if studio and not studio._PENDING and session and session.project_uuid == saved["project"]:
                status(state, request_id, "reloaded", "Plugin registered and Studio restored; Undo history is reset")
                state.restoring = None
            elif (studio and not studio._PENDING) or time.monotonic() - started > 30:
                status(state, request_id, "failed", "Files applied and plugin registered, but Studio restoration failed")
                state.restoring = None
            return 0.25
        path = CONTROL / "request.json"
        if not path.exists():
            return 0.25
        request = read_json(path)
        if request.get("instance") != state.instance:
            raise RuntimeError("Request belongs to another bridge instance; retry sync")
        reason = busy(state)
        if reason:
            if state.wait_reason != (request["id"], reason):
                status(state, request["id"], "waiting", reason)
                state.wait_reason = (request["id"], reason)
            return 0.25
        try:
            apply(state, request)
        finally:
            path.unlink(missing_ok=True)
            request_id = request.get("id", "")
            if isinstance(request_id, str) and len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id):
                shutil.rmtree(CONTROL / "stages" / request_id, ignore_errors=True)
            state.wait_reason = None
    except Exception:
        request_id = "unknown"
        path = CONTROL / "request.json"
        if path.exists():
            try:
                request_id = read_json(path).get("id", "unknown")
            except Exception:
                pass
            path.unlink(missing_ok=True)
        status(state, request_id, "failed", traceback.format_exc())
    return 0.25


def stop():
    state = sys.modules.pop(STATE_KEY, None)
    if state and bpy.app.timers.is_registered(state.callback):
        bpy.app.timers.unregister(state.callback)
    path = CONTROL / "request.json"
    if path.exists():
        request = read_json(path)
        path.unlink()
        request_id = request.get("id", "unknown")
        status(state, request_id, "failed", "Bridge stopped; pending update cancelled")
        if isinstance(request_id, str) and len(request_id) == 32 and all(c in "0123456789abcdef" for c in request_id):
            shutil.rmtree(CONTROL / "stages" / request_id, ignore_errors=True)
    (CONTROL / "connection.json").unlink(missing_ok=True)


def start():
    CONTROL.mkdir(parents=True, exist_ok=True)
    if CONTROL.joinpath("connection.json").exists():
        connection = read_json(CONTROL / "connection.json")
        if connection.get("pid") != os.getpid() and time.time() - connection["heartbeat"] < 5:
            raise RuntimeError("Another Blender instance owns this repository's dev bridge")
    stop()
    names = [name for name in bpy.context.preferences.addons.keys()
             if name.split(".")[-1] == "quick_sdf_blender" and name in sys.modules]
    if len(names) != 1:
        raise RuntimeError("Enable exactly one Quick SDF extension before starting the bridge")
    state = types.ModuleType(STATE_KEY)
    state.name = names[0]
    state.destination = Path(sys.modules[state.name].__file__).resolve().parent
    if state.destination == (ROOT / "quick_sdf_blender").resolve():
        raise RuntimeError("Bridge requires an installed extension copy, not the source checkout")
    state.instance = uuid.uuid4().hex
    state.restoring = None
    state.wait_reason = None
    state.callback = lambda: tick(state)
    sys.modules[STATE_KEY] = state
    bpy.app.timers.register(state.callback, first_interval=0.1, persistent=True)
    print(f"Quick SDF dev bridge started: {state.destination}")


# runpy.run_path returns these functions for an optional explicit stop().
start()
