# AGENTS.md — Shresth Masala Industry Management System

Offline-first desktop app (pywebview + FastAPI + SQLite) for a masala factory.
Python 3.10+ (`pyproject.toml`), Windows. Shell is PowerShell 5.1 — no `&&`,
no `head`, no `grep -E`; use `;` chaining and `Select-String`.

## Entry points

- `python main_window.py` — desktop window, picks a free port 8000–8005.
- `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` — dev server.
  Bind `127.0.0.1` only (no auth in front of most endpoints).
- First launch ever: no users; register an `admin` account, then sign in.

## Layout (non-obvious parts)

- Route handlers live in `backend/app/routes/*.py`; `backend/main.py` assembles
  the app and `include_router`s them. `backend/app/api/` is empty — do not add
  routers there.
- **Route registration order decides matching.** Static paths must be declared
  above parameterized ones (e.g. `/import/undo` before `/import/{import_type}`).
- Frontend is static pages (`frontend/pages/*.html` + `frontend/js/`), served
  same-origin at `/frontend`. No bundler, no framework. File downloads go through
  the `DesktopApi.save_file` JS bridge (`main_window.py`) because WebView2
  ignores blob-URL anchor downloads — keep the browser fallback in
  `components.js` for dev-server use.
- Auth is middleware (`rbac_guard` in `main.py`), not per-route decorators.
  Open paths: `/frontend`, `/share`, `/auth/login`, docs, first-user
  `/auth/register`. Role floors and the admin-only prefix list are hardcoded in
  `main.py`; the configurable permission catalogue is
  `backend/app/core/permissions.py`, seeded by `init_db()`. Extend the prefix
  list for a new admin-only route.
- **Mutating methods (PUT/PATCH/DELETE) require the editor claim.** Plain login
  starts with it OFF — enable via `POST /auth/editor-mode {"enabled":true}`.

## Frontend conventions

- Filter/search state persists via `UI.saveFilterState`/`UI.loadFilterState`
  (localStorage `mf_filter_<key>`). Any Reset UI must also **rewrite the saved
  state blank** — clearing only the DOM lets a reload resurrect the filters.
- `components.js` is referenced from `frontend/index.html` as
  `components.js?v=N` (single reference). Bump `N` when `components.js` changes.
- Every date column: `UI.dateWithTime` display + hidden `UI.tsKey(business,
  createdAt)` sort key; money: `UI.money`.
- `dashboard.html fetchStats` guards on the current hash segment (`seg !== "dashboard"` returns early) so an in-flight call can't write to a swapped-out page.
- `settings.html` field names must match the backend: backup uses `enabled`,
  `schedule_hour`, `retention_days` (NOT `auto_hour`/`keep_count`); email uses
  `smtp_user`, `from_email`, `alert_recipient`, `use_tls`, `report_hour` (|| 8),
  `report_sections` (|| []).

## Universal date rule (pinned by tests)

1. Timezone is India, everywhere. Backend writes `_ist_wall()` (naive IST);
   frontend parses naive values as IST (`asUtcDate`). Never `datetime.now`,
   `datetime.utcnow`, or `func.now` in persistence paths (`test_ist_everywhere.py`).
   Non-persistence uses (JWT expiry, PDF stamp) fine.
2. Every add/edit stamps a full timestamp. `created_at` defaults `_ist_wall` at
   insert and is never reassigned by updates; `updated_at` tracks edits.
3. Date order is newest-first, ties broken by time then id:
   `date.desc(), id.desc()`. Frontend date cells use `dateWithTime` + hidden
   `UI.tsKey(businessDate, createdAt)` with `sortValue` wiring — same-day rows
   would otherwise tie at midnight.

## Database — read before touching

- Live DB resolves per env (`backend/app/core/config.py`): repo `data/` from
  source, next to the `.exe` when frozen. SQLite, FK enforcement ON per
  connection (`PRAGMA foreign_keys = ON` in `database.py`) — deleting a
  referenced parent raises `IntegrityError`, so soft-delete instead: `is_active`
  on masters, `is_archived` on documents (production, challans, invoices,
  payments, expenses). Archiving does **not** reverse stock/receivable effect;
  only hard delete is a clean DRAFT challan.
- **Schema changes are hand-rolled, not Alembic.** No `alembic/`; Alembic is a
  dev dep but unused. Add columns to the `pending` list in `_ensure_columns()`
  (indexes/unique guards via its helpers) in `database.py`; `init_db()` applies
  them idempotently on every start.
- **Money is rounded at flush.** The `before_flush` hook quantizes every
  `Numeric(_, 2)` column HALF_UP; scale-3 quantities are deliberately untouched.
  Choose column scale deliberately; don't hand-round values.
- **Challan stock has one writer:** `services/stock_posting.py`
  `post_challan_sales_movements` (idempotent), called by confirm and the Excel
  importer. Never create challan `SALES` movements ad hoc.
- **Tests must never touch `data/masala_factory.db`.** Use in-memory `db`/
  `make_db` fixtures (schema built once in `conftest.py`); `copy_of_real_db`
  for schema/upgrade tests. An autouse tripwire fails any test modifying the
  real file. Throwaway scripts target a **copy**, never the live path.
- `import backend.main` runs `init_db()` as a side effect; the auto-backup
  scheduler starts in the lifespan handler (serve time), not on import.

## Change-reason rule

- `_change_reason(request, payload=None)` (`backend/app/core/shared.py`) requires
  a change reason ≥ `_CHANGE_NOTE_MIN` (5) chars, read from `payload.reason`
  or the `?reason=` query param. It raises HTTP 400 when missing/short.
- It is mandatory only on **money endpoints** (customer/supplier payments,
  payroll, expenses, cash void/restore). Non-money edits (products, customers,
  employees, production, purchase, raw materials, suppliers) do NOT require it.
- Read reason from query param for PUT routes with no body (`?reason=<text>`);
  from payload body when one is sent.

## main.py test aliases

`backend/main.py` re-exports route functions so tests can call them directly:
`list_products`, `list_customers`, `list_suppliers`, `reports_summary`,
`recent_transactions`, `suggest_hsn_code`, `_production_to_dict`,
`set_editor_mode`. Tests call route/service functions directly with an explicit
session, e.g. `main.finished_stock_balance(db)`. No `TestClient`.

## Route fixes to know

- `/raw-material-categories` is `GET` (line ~105 in `raw_materials.py`), not POST.
- Employee sub-routes exist as `/{employee_id}/attendance`, `advances`,
  `deductions`, `leaves` (GET+POST). `/payroll/payments` is GET with an
  `include_archived` filter, ordered by id desc.
- `_ChallanRow` (`sales.py`) passes `None` (not `""`) for null `status`;
  `SalesChallanSchema.challan_date`/`status`/`created_at` are optional.

## Tests

- `python -m pytest` (full suite), `python -m pytest backend/tests/test_x.py`
  (one file), `::test_name` (one test). Config in `pyproject.toml`:
  `testpaths = ["backend/tests"]`, `addopts = "-q --strict-markers
  --strict-config"`, `filterwarnings = ["error::DeprecationWarning:backend.*"]`.
  Fix the warning, don't silence it.
- Ruff is informational in CI (`continue-on-error`); pytest is the gate.
  Per-file-ignores in `pyproject.toml` are triaged with reasons — fix real
  findings, don't widen ignores.

## Build (`scripts/build.py`)

- `python scripts/build.py` →
  `dist/ShresthMasalaIndustry/ShresthMasalaIndustry.exe` (onedir).
  **Close the running app first.** Script stashes `dist/.../data` before wiping
  `dist/` and restores it after — a failed/locked build strands live user data;
  verify the restore.
- `data/` is never bundled (created fresh next to the exe); only `frontend/`
  ships as data — do not `--add-data backend/`.
- Keep `reportlab` + `PIL` in hidden imports; never exclude `PIL`:
  `reportlab.lib.utils` does `from PIL import Image` unconditionally, so a
  frozen exe without PIL crashes every PDF export.

## Deploy (`dist/` → real machines; never copy `dist/` itself)

- Deploy targets are the **deployment folders, not `dist/`**: at minimum
  `C:\Shresth_Masala_Application_New\ShresthMasala-Laptop` and one or more
  `D:\ShresthMasala-Laptop*` copies on the removable drive (user renames to
  dated names; discover fresh via
  `Get-ChildItem D:\ -Filter "ShresthMasala*"`; freshest `app.log` shows which
  copy is running).
- Swap = `ShresthMasalaIndustry.exe` + `_internal\` + `run-*.bat` +
  `HELP.txt`. Frontend ships inside `_internal\frontend\` (verify new code by
  grepping that path — `dist\...\ShresthMasalaIndustry\frontend` does not
  exist). `stop-server.bat` is not shipped; leave it.
- **Immediately before copying: `Get-Process ShresthMasalaIndustry*` — the app
  must be closed.** Swapping into a running `_internal` has corrupted a build
  mid-swap. Never touch `data\` inside any deployment folder.
- After the swap, verify: packaged pages contain the new markers,
  `components.js?v=N` cache-buster matches, and `data\` timestamps untouched.
- `python scripts/build_release.py` builds clean into `release/` and fails if a
  database or secret leaked. Operator setup guide: `docs/SETUP_NEW_LAPTOP.md`.

## Live-data repairs & QA servers

- Data bugs are often data, not code. Inspect by copying a deployed
  `data\masala_factory.db` to a temp path and querying the copy — never the
  live file.
- Repair scripts (e.g. `scripts/fix_size_weight.py`): dry-run by default;
  `--apply` refuses without `--backup`. Confirm intended final state with the
  user before touching the ledger.
- `database disk image is malformed`: `scripts/fix_corrupt_db.py` (dry-run by
  default; keeps original as `<db>.corrupt`; clears stale `-wal`/`-shm`;
  `--fallback` + `--salvage rows.json` rebuild rows a dead table can't yield).
  Verify an auto-backup with `PRAGMA integrity_check` before restoring.
- QA/dev server: temp workdir, `$env:DATABASE_URL='sqlite:///...\qa.db'`,
  then `python -m uvicorn backend.main:app --app-dir <repo> --port <p>` from
  that temp dir (auto-backups land in cwd). Bootstrap:
  `POST /auth/register` (username ≥ 3 chars) →
  `POST /auth/login?username=&password=` → response field is `access_token`.
  For browser QA seed `localStorage.mf_token` + `mf_user`.

## Graphify (inactive)

- `graphify-out/` is absent; ignore `/graphify` unless
  `graphify-out/graph.json` reappears. If it does: `graphify query`/`path`/
  `explain` beat grep, and `graphify update .` (AST-only) runs after code
  changes.
