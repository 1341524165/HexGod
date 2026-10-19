# Architecture

`hexgod` is a Windows WPF desktop shell and a Python JSON-lines service. Its current root has no application `.py`, `.cs`, copied audit scripts or versioned EXEs.

```text
src/
  desktop/          WPF UI, passive input sources, session/view state, process client
  hexgod/
    api/            JSON-lines service and request dispatch
    recommendation/ ARAMGG provider, mechanism planner, purchase policy, legacy fixture provider
    vision/         capture, card detection, OCR, title matching, OCR worker
    tracking/       click/card correlation and match journals
    game/           read-only Live Client / LCU adapters
    platform/       Windows display and process discovery
    data/           verified ARAMGG dataset synchronization
    common.py       pure shared normalization and validation
    paths.py        central project/resource/writable paths
tests/
  unit/             state, paths, game parsing, evidence classification
  integration/      production backend, real OCR and replay chains
  desktop/          actual WPF and input-state tests
  fixtures/         upstream screenshots and recorded card geometry
scripts/            supported build, verify, audit and synchronization commands
resources/          client data, rules, hero metadata, snapshots, legacy test data
assets/models/      local OCR models and provenance
docs/history/       version-specific reports and archived one-off tools
runtime/            bundled interpreter and dependencies (local, ignored)
build/              desktop EXEs and compiler outputs (ignored)
artifacts/          test captures, reports, caches (ignored)
var/                personal state and immutable historical logs (ignored)
```

The WPF shell finds the project root from `pyproject.toml`, not its working directory, and launches `runtime/python.exe -m hexgod`. `hexgod.paths` resolves resources relative to the installed source layout. The bundled interpreter gets a relocatable `.pth` through `scripts/bootstrap_runtime.py`; normal development uses an editable package install.

`api.Backend` selects the ARAMGG provider by default. The historical recommendation provider is imported only for explicit legacy fixtures. Shared numerical/lookup behavior is in `common`, so production no longer imports an entire old provider to reuse three methods.

Input delivery, selection confirmation and recommendations remain distinct stages. Raw Input / button-state capture delivers a physical click; tracking correlates it with a known card and two later panel-free frames; only a confirmed selection enters the owned list; the planner then derives item candidates. Merely recognizing an offered augment does not select it.

The OCR pipeline is now package code, including its worker and recognizer CLI. Native code is built through a shared PowerShell compiler specification, with an additional Visual Studio solution/project for navigation and normal IDE builds. WPF XAML stays next to the desktop source.
