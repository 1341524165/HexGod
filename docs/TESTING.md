# Testing and evidence

`scripts/verify.ps1` executes Python unit/integration tests, actual WPF rendering tests, then the desktop compiler. Tests use the same service module and production item planner as the application.

Coverage includes:

- Upstream full/partial/no-match captures, monitor sizes, DPI geometry, UI scaling, occlusion and independent rerolls.
- Button-state edges, startup with a held button, Raw Input/polling deduplication, rapid second presses, stale frames, wrong windows and missing input.
- Four saved panels from different sessions/dates: Oct 19 Xin Zhao + spell-on-hit, a Death's Dance upgrade, a Heartsteel task, and an earlier spell-on-hit panel.
- Confirmation → journal → item synergy for every one of the 173 catalog heroes using the recorded spell-on-hit geometry.
- No click → no confirmed selection, even when a panel is readable and later disappears.
- Evidence classification: controlled fixtures, offline/replay and old untagged sessions do not count as verified live successes.

The original logs did not preserve hero names. Xin Zhao's identity comes from the user's report; heroes used for the other recorded panels are explicit test parameters (`heroIsTestParameter`), not claims about those original games. New journals persist both `hero` and `evidenceKind`.

Supported evidence kinds are `live`, `desktop_fixture`, `replay`, `offline`, `test` and `unclassified`. Historical sessions without a type remain `historical_untagged` in the audit; known old desktop fixtures are listed in `resources/diagnostics/controlled-sessions.json`. Do not alter old logs to add retrospective labels or selected augments.

A live capture source must actually deliver events while the real game is foreground. Offline replay, desktop fixture success and a running polling thread are separate pieces of evidence; none alone proves that delivery. The actual-game input-health journal is the acceptance record for that boundary.
