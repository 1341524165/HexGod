# Historical confirmed audit — 2026-10-20

The available snapshot has 236 summaries: 233 historical untagged sessions, two documented local desktop-fixture sessions, and one offline session.

No explicitly live or historical untagged session contains a confirmed selection. The only nonempty summary is `20260929-011443-1`, containing 超凡邪恶 and 神射法师; `docs/history/HOTFIX-0.11.md` explicitly identifies that run as a LOCAL_FIXTURE test. It is not real-game success evidence.

The exact script count is 33 historical untagged sessions where 虚幻武器 was observed and zero clicks were delivered. Thus the input failure is not confined to the user-reported Xin Zhao match. Other saved panels include the Death's Dance upgrade and the Heartsteel task, now part of the replay suite.

Old logs do not reliably identify the original hero, so test contexts are explicitly marked instead of invented. The 0.18 journal records hero and evidence kind going forward. The audit command preserves original files and produces a separate report.

```powershell
.\runtime\python.exe -X utf8 -B scripts\audit_recordings.py
```

This audit proves the past absence of recorded confirmations, not why the operating system stopped a particular input channel. The 0.17 input fallback has replay/state tests; successful actual-game delivery still needs live evidence.
