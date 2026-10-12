# Live development sync (Windows / Blender 5.1–5.2)

Development-only, standard-library scripts. They synchronize the installed
extension copy, not the release ZIP. `build_extension.ps1` remains the release
gate and is not invoked by this workflow. Run only one watcher and one bridge
per repository; use a disposable development Blender session.

## Start

1. Enable the installed Quick SDF extension in Blender.
2. In Blender's Python Console, run:

   ```python
   qsdf_dev = __import__('runpy').run_path(r'F:\Git\QuickSDFBlender\scripts\blender_dev_bridge.py')
   ```

   The bridge discovers the loaded extension's module name and install path.
   It refuses to overwrite a source checkout. Adjust the script path if the
   repository is elsewhere. Switching the Console back to 3D View is safe.

3. From the repository root, run:

   ```powershell
   python .\scripts\dev_sync.py --watch
   ```

   The first update synchronizes current files. Later saves trigger updates
   after approximately 800ms of quiet time. The watcher stages a stable
   snapshot and compiles Python before requesting a reload. Changes made
   during a reload are picked up in the next snapshot.

For a single update:

```powershell
python .\scripts\dev_sync.py
```

For workspace construction changes (for example, the default brush-shelf
visibility), request a layout rebuild explicitly:

```powershell
python .\scripts\dev_sync.py --rebuild-layout
```

The bridge otherwise preserves the existing Studio editor layout. A changed
`WORKSPACE_LAYOUT_VERSION` also triggers rebuilding. Normal artist opens of
Quick SDF retain their existing default rebuild behavior.

## Safety and status

- All Blender APIs run on a main-thread timer, independent of the plugin.
  MCP is not required after bootstrap.
- Painting, modal interaction (including gizmo drags), pending Studio opens,
  key preparation and bake/export defer reloading. Auxiliary-mask editing
  and temporary Canvas sessions also defer: return to a committed key first.
- Dirty authoring images are flushed before the OLD plugin unregisters.
  The project is restored by UUID; no `.blend` is automatically saved.
- Plugin unregister clears Quick SDF's in-memory Undo history. This is not a
  transparent continuation of an editing session or a guarantee of Blender
  Undo compatibility. Save important work before enabling watch mode.
- Only previously managed files are removed. The first sync adopts the source
  file list; it does not remove unknown files from the installation.
- `model.py`, manifest and native-library changes stop with
  `restart_required`, applying **no files**. This deliberately treats all
  model edits as possible RNA schema changes. Close Blender, install/copy the
  updated extension, then restart it and the bridge. Native C++ edits require
  an explicit native build first; this watcher does not compile C++ or unload
  DLLs.
- Registration/copy failures restore previous installed files and attempt to
  re-enable the old plugin. Asynchronous Studio restoration failure is reported
  separately; installed files remain updated. Process crashes cannot be rolled
  back by an in-process timer.

Runtime files are under ignored `build/dev-sync/`:

- `connection.json`: target module/path, instance token and heartbeat.
- `request.json`: pending update; `stages/`: immutable staged files.
- `status.json`: update ID, state and full error traceback.
- `managed.json`: synchronization-owned file list.

Success is `reloaded` (registration and Studio restoration completed), or
`unchanged`. `syncing`, `waiting` and `restoring` are not success acknowledgements.
A request waits up to 120 seconds in the CLI by default. On timeout it remains
queued, rather than silently cancelling a Blender update; check status, finish
the interaction, or stop the bridge to cancel it.

## Stop

Stop the watcher with Ctrl+C. In Blender's Console:

```python
qsdf_dev['stop']()
```

Stopping the bridge cancels a queued update. Restart the bridge after restarting
Blender. The persistent timer can survive loading another `.blend`, but remains
bound to the same installed extension and repository. Nothing is added to
Blender startup files or saved preferences.
