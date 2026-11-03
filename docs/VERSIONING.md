# Versioning: change one part, not the whole code

## Two levels

| Level | Where | Meaning |
|---|---|---|
| Project | `VERSION`, `reggraph.__version__` | what you tell customers ("0.1.0"); a test keeps the two equal |
| Module | `module_versions.json` | independent semantic version per module |

Modules: `core`, `jurisdictions`, `currency`, `i18n`, `security`, `storage`, `regulation`, `fx`,
`registry`, `api`, `app-shell`. Each owns specific paths. A **fingerprint** (SHA-256 of file names and
contents) is stored beside each version, so any edit, new file or deleted file is noticed.

## Day-to-day workflow

```bash
python tools/module_manifest.py status                 # which modules changed?
# ... edit src/reggraph/currency/format.py ...
python tools/module_manifest.py status                 # currency  0.1.0  CHANGED (needs bump)
python tools/module_manifest.py bump currency patch -m "Fix NNBSP before kr symbol in sv"
python tools/module_manifest.py check                  # CI runs this
```

Only `currency` moves to 0.1.1; every other module keeps its version, and CHANGELOG.md gains a
line under *Module history*.

| Change | Bump |
|---|---|
| Bug fix, wording, internal refactor with no behaviour change | `patch` |
| New backwards-compatible feature (new language, new currency, new route) | `minor` |
| Breaking change (route removed, response field renamed, schema change) | `major` |

## Updating, modifying and deleting

* **Modify** code → edit the file, bump that module.
* **Delete** code → delete it, bump that module (the fingerprint changes).
* **Add** a file → it must sit inside a module path; `check` fails for files that belong to no module.
* **Add a whole new module** → list it in `MODULES` in `tools/module_manifest.py`, then run
  `python tools/module_manifest.py add <module> -m "why"` (starts at 0.1.0).
* **Data you must never edit in place:** approved rules (create a new version), audit entries
  and FX rates (insert-only). The database refuses these edits.
* **Database schema:** add a numbered migration in `storage/sqlite.py`; never edit an old one.

## Releasing

1. All modules consistent: `make check`.
2. Update `VERSION` and `reggraph/__init__.py` together; add a section to CHANGELOG.md.
3. Tag the commit `v<version>`.

CI (`.github/workflows/ci.yml`) runs `make check` on every push, so an un-bumped change cannot merge.
