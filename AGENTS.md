# Repository maintenance rules

- Keep the root for project metadata, the solution, README and the user-facing launch command/shortcut. Python application code belongs in `src/hexgod`, WPF code in `src/desktop`, operational commands in `scripts`, and tests in their respective `tests` layer.
- Use package imports and `python -m hexgod`. Do not add `sys.path` mutations, flat compatibility modules, absolute installation paths or implicit imports from the current working directory.
- Python paths are owned by `hexgod.paths`; desktop paths by `ProjectPaths`. Read-only data belongs in `resources`, model assets in `assets/models`, personal state/logs in `var`, and generated output in `build` or `artifacts`.
- Keep production and legacy fixture providers separate. Production must not import the legacy provider. Do not copy sparse combo statistics into single-item or conditional win rates.
- Run `scripts/verify.ps1` for application changes. Use `scripts/audit_mechanics.py` when recommendation rules change. Test behavior and meaningful boundaries rather than duplicating implementation details.
- A replay or a local fixture is not proof of successful live input capture. Preserve `evidenceKind`, log hero/context, and never rewrite historical player logs to add inferred selections.
- Keep `runtime`, model binaries, personal settings, logs, build output and caches out of Git. Preserve upstream fixture/model provenance and source-data hashes.
- Keep historical material under `docs/history`; do not revive old one-off migration scripts as production entrypoints.
- Use the existing local Git repository. Do not configure remotes or publish without the user's instruction.
