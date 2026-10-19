# Operation and local data

Launch `build/bin/hexgod.exe`, use the desktop shortcut, or run `scripts/start.ps1`. Keep the full project folder together; the EXE locates the root by project metadata. The window and overlay remain read-only assistants and never send game input.

Personal files are under `var`:

- `var/state/settings.json`: hero/window preferences.
- `var/state/connection-status.json`: current app/input/window health.
- `var/logs/matches/<session>/summary.json`: hero, evidence kind, observed panels, clicks and confirmed selections.
- `var/logs/matches/<session>/events.jsonl`: OCR, geometry, rerolls and confirmation events.
- `var/logs/matches/<session>/input-health.jsonl`: Raw Input and independent button-state counters.
- `var/logs/backend.log`, `runtime.log`, `overlay-events.log`: process/UI/recommendation diagnostics.

Use the UI's “打开本局日志” button to open the active journal. Raw Input registration by itself is not success: compare packet/button-state counters over time and check that a delivered card click is followed by `selection_confirmed` and a nonempty `confirmed` list.

The 0.18 directory migration moves old logs and settings without rewriting their original timestamps, version numbers or evidence. Historical file paths in tracebacks remain forensic data. `var`, `build`, `artifacts`, the interpreter and model binaries are ignored by Git. The configured origin is https://github.com/1341524165/HexGod. Logs remain excluded from Git and are not uploaded.

Production statistics are the verified ARAMGG snapshot in `resources/client-data`. Current item data do not contain selected-augment/equipment conditional win rates. The synergy column lists mechanism candidates only; the regular column displays observed win rate and sample size with sample-aware ranking. New data are fetched only by the explicit synchronization command.
