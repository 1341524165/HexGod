# Development

Use Windows x64 with .NET Framework 4.x and Python 3.12. The full local folder already includes its interpreter and models.

```powershell
# Run from the repository root.
.\runtime\python.exe -X utf8 -B scripts\bootstrap_runtime.py
.\scripts\verify.ps1
.\scripts\start.ps1
```

The verification command builds `build/bin/hexgod-verify.exe`, so it does not overwrite an already running desktop application. For a release update, close the application and run `scripts/build.ps1`.

`bootstrap_runtime.py` only writes a relative source registration into the bundled interpreter and creates ignored output directories. It does not access the network or change global Python configuration. A normal Python environment can instead use `python -m pip install -e .`; package dependencies and the version live in `pyproject.toml` / `src/hexgod/__init__.py`.

- `scripts/build.ps1`: desktop build to `build/bin/hexgod.exe`.
- `scripts/smoke_overlay.ps1`: legacy/production WPF scenarios and passive input-state tests; it never opens a game or injects input.
- `scripts/audit_mechanics.py`: all-hero/augment/inventory mechanism checks; writes `artifacts/reports`.
- `scripts/audit_recordings.py`: historical summaries separated by evidence kind; writes `artifacts/reports/recording-audit.json`.
- `scripts/sync_data.py`: explicitly download and validate current ARAMGG data; it does not upload matches.
- `Hexgod.sln`: desktop solution; IDE builds should install the .NET Framework 4.8 targeting pack. The PowerShell build uses the system Framework compiler, so a full IDE/developer pack is optional for the supported local command.

Put new Python behavior in the corresponding package and tests in the matching test layer. Do not add a root script to expose a feature: add a package entrypoint or a supported `scripts` command. Share filesystem paths through `hexgod.paths` / desktop `ProjectPaths`.

`resources/legacy` is a frozen test provider, not production data. `docs/history/legacy-tools` contains archived source as text; those old scripts are not supported commands. Model binaries and `runtime` remain available locally but are not Git source files; their pinned identities are in `resources/manifests/asset-manifest.json`.

Before a commit, run the verification command, inspect `git diff`, and ensure generated output and private logs were not staged. Recommendation changes also need the mechanism audit. A passing replay only proves the chain after event delivery; new live-input behavior needs actual-game evidence.
