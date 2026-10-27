# Shresth Masala Industry — Management System
### Help File: How to Run and Use the Application

Local, offline-first desktop application for a masala factory:
customers, suppliers, products, purchase invoices, sales challans,
payments, production, expenses, employees, reports, backup — plus Excel
import/export and role-based logins.

---

## 1. Run the easy way (recommended): the `.exe`

1. Open this folder: `dist\ShresthMasalaIndustry\`
2. Double-click **`ShresthMasalaIndustry.exe`**.
   - Keep `ShresthMasalaIndustry.exe` together with its `_internal` folder.
     The app will not start if the `.exe` is moved alone.
   - Windows SmartScreen/antivirus may warn on first run because the file
     is unsigned (normal for PyInstaller apps). Choose
     *More info → Run anyway* if you trust this copy.
3. The app window opens automatically (it also serves
   http://127.0.0.1:8000/ — you can use a browser instead if you prefer).

**First launch ever:** there are no users yet, so go to **Register** and
create your **admin** account, then sign in.

**Your data lives here:** `dist\ShresthMasalaIndustry\data\masala_factory.db`
(backups go to `dist\ShresthMasalaIndustry\data\backups\`).
Back up this `data` folder before moving PCs — copy the whole
`ShresthMasalaIndustry` folder to migrate everything.

To update to a newer version later: rebuild (`python scripts/build.py`)
and replace everything **except** the `data` folder.

---

## 2. Run from source (developers)

Requirements: **Python 3.12+** installed.

```powershell
cd C:\Shresth_Masala_Application
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000** in a browser.
For the desktop window instead: `python main_window.py`.

Bind to `127.0.0.1`, not `0.0.0.0`. This app has no authentication in front
of most of its endpoints, so exposing the port publishes the whole factory
database to the local network. Only the desktop window may choose a port, and
it picks a free one from 8000-8005.

To rebuild the `.exe` after code changes:

```powershell
python scripts/build.py
```

The build deletes `dist\`, so **close the running app first**. Afterwards copy
`data\masala_factory.db` and `data\backups\` back into
`dist\ShresthMasalaIndustry\data\` - the source database is the master.

### Running the tests

```powershell
pip install pytest
python -m pytest                                   # all 237 tests
python -m pytest backend/tests/test_production_workflow.py   # one file
python backend/tests/test_production_workflow.py             # same, directly
```

The suite never touches the real database. Database tests use the in-memory
`db` fixture; a test that needs the live schema uses `copy_of_real_db`, which
copies the file first. An autouse tripwire in `backend/tests/conftest.py` fails
any test that modifies `data\masala_factory.db`, so an accidental write becomes
a loud failure rather than silent data loss.

If you need to exercise a live server, run the app against a **copy** of the
database. Do not point throwaway scripts at `data\masala_factory.db`; scripts
like that have already caused real damage in this project.

---

## 3. First-time setup (do this once, in order)

1. **Sign in** as admin (register first account if needed).
2. **Masters**: Customers → Suppliers → Products (add packet sizes +
   opening stock) → Raw Materials → Employees.
   - Shortcut: **Import / Export** page → download is not needed; use the
     sample templates in `templates\` as the column format, fill your data,
     then **Preview → Import** in this order:
     customers → suppliers → raw materials → products → purchase invoices
     → sales challans → expenses → employees.
3. Record supplier **purchase invoices** (raw stock rises automatically)
   and customer **opening balances**.

Use **⚙ Customize** on the dashboard to show only the sections you need.

---

## 4. Daily workflow

- **Sales**: Challans → New (pick customer, packet sizes, quantities) →
  **Confirm** → **Dispatch** (transporter/vehicle) → **Mark Delivered** →
  **Payment** button as money arrives (overpayments are blocked).
- **Purchases**: Invoices → New (supplier, materials, GST) → **Pay**
  button as you pay suppliers.
- **Production**: New entry (product, packet size, stages, materials
  consumed — stock is validated) → Start → Complete.
- **Expenses**: add with category (auto-created if new).
- Watch the dashboard: **Attention Required**, red negative stock,
  low-stock badges, pending/outstanding cards.

---

## 5. Safety: backup, users, reports

- **Backup page** → *Backup Now*; **Download** copies off-machine.
  **Restore** always saves a `pre-restore_*` safety copy first.
  **Settings → Automatic Backups** for the daily schedule.
- **Users page** (admin): add staff as Manager / Data Entry / Viewer.
  Users only see and do what their role allows. Forgot password? Admin
  uses **Reset PW** on the Users page.
- **Reports**: summary + custom builder (save your own), Stock Analysis,
  Monthly Sales, all with Excel export and Print/PDF.

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| App window is blank / old screens show | Hard refresh: `Ctrl+Shift+R`. The server sends no-cache headers, so one refresh fixes stale files. |
| `Port 8000 already in use` / two copies running | Close the other copy (check Task Manager for `ShresthMasalaIndustry` or `python.exe` running uvicorn). Only one copy can hold the port. |
| Login fails / "Not authenticated" on saves | Sign out and sign in again (session expired). Check caps lock. Admin can reset passwords on Users page. |
| Excel import shows failures | Use **Preview** first; the result lists the exact row + reason. Column headers must match `templates\sample_*.xlsx`. Import masters before transactions. |
| Negative stock in red | Real signal, not a bug: more sold than produced/recorded. Record the missing production or opening stock. |
| Email alerts fail (Gmail) | Use an **App password**, not your Gmail password (Google Account → Security → 2-Step Verification → App passwords). Pair STARTTLS↔587 or SSL↔465. Errors now spell this out. |
| `.exe` was working, now crashes on start | Look at `app.log` next to the `.exe` and share its last lines when asking for help. |
| Moving to a new PC | Copy the whole `dist\ShresthMasalaIndustry` folder (exe + `_internal` + `data`). Nothing else is needed. |

Data file layout (all under the app folder → `data\`):
`masala_factory.db` (everything), `backups\` (manual + auto `.db` copies).
