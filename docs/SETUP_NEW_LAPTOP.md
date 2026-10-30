# Shresth Masala Industry — Management System
## New-laptop setup + database reading guide (step by step)

Everything below is **100% free** — no licence, subscription or paid tool
anywhere in this setup:

| What | Free choice | Why |
|---|---|---|
| Language | Python 3.12+ (python.org) | open source |
| Database | SQLite (built into Python) | public domain, single file |
| Server/UI | FastAPI + pywebview/WebView2 | MIT/BSD; WebView2 Runtime is a free Microsoft download |
| Read the database | DB Browser for SQLite (sqlitebrowser.org) | free, open source |
| Copy PC → PC | USB pen-drive + the `ShresthMasala-Laptop` folder | — |

> Safety first: the clean package contains **no business data** — it starts
> with an empty database. Never copy a live `data\masala_factory.db` from
> another PC unless you intend to move the whole business over.
>
> If your folder already contains `ShresthMasalaIndustry.exe` and a filled
> `data\` folder, skip steps 2–3 and Part B: double-click the exe (or
> `run-app.bat`) and sign in — your data is already inside.

---

## Part A — Set up the new laptop (do once, in order)

1. **Copy the folder.** Plug in the USB drive, copy `ShresthMasala-Laptop`
   to the new laptop, e.g. `C:\ShresthMasala-Laptop\`. Keep all files
   together — `run-server.bat` only works inside this folder.
2. **Install Python.** From https://www.python.org/downloads/ get
   **Python 3.12 or newer**, and on the first installer screen tick
   **“Add python.exe to PATH”**. Verify: open a terminal and run
   `python --version`.
3. **Install the app's libraries** (one time, needs internet):
   ```powershell
   cd C:\ShresthMasala-Laptop
   pip install -r requirements.txt
   ```
4. **Start the app.** Double-click **`run-server.bat`**.
   `run-server.bat --help` shows usage. It starts the server on
   `http://127.0.0.1:8000/` and opens your browser there.
   - Port busy? Close the other copy, or run `set PORT=8001 & run-server.bat`.
   - To stop: close the black `ShresthMasala` server window (or Ctrl+C in it).
5. **First run ever:** the database is empty, so click **Register**, create
   your **admin** account, then sign in. Add staff later on the Users page.
6. **Masters, in this order:** Customers → Suppliers → Raw Materials →
   Products (packet sizes + opening stock) → Employees. Shortcut: the
   **Import** page with the `templates\sample_*.xlsx` column formats.

## Part B — Read the application database (step by step)

The whole business lives in **one file**:

```
C:\ShresthMasala-Laptop\data\masala_factory.db      (frozen .exe: next to the .exe, in data\)
```

Backups live in `data\backups\` (manual + automatic `.db` copies).

1. **Close the app first** (server window / exe). SQLite hates two writers.
2. **Work on a COPY, never the live file.** Copy `masala_factory.db` to
   your Desktop and open the copy.
3. **Install DB Browser for SQLite** (free, sqlitebrowser.org) and open the
   copy with it: **Browse Data** tab → pick a table.
4. **Key tables** (every business row carries `created_at`/`updated_at`):
   - `customers`, `suppliers` — masters (`is_active = 0` means removed,
     rows are never hard-deleted).
   - `raw_materials` — `current_stock` is the live figure.
   - `products` + `packaging_sizes` — catalogue and packs.
   - `purchase_invoices` + `purchase_invoice_items` — buying.
   - `sales_challans` + `sales_challan_items` + `customer_payments` — selling.
   - `production_entries` + `production_stage_entries` + `production_outputs`.
   - `product_formulations` — recipe percentages (sum to 100 per product).
   - `expenses`, `employees`, `users` (logins), `audit_logs` (who did what).
   - `backup_records` — backup history.
5. **Reads are safe; writes are not.** Look all you like in the copy.
   Never edit, delete or “compact” the live file by hand — a wrong click
   bypasses every validation the app enforces. Changes belong inside the
   app (or its Import page).

## Part C — Moving between PCs later

- **Fresh start:** use the clean package again (empty database).
- **Move the business:** copy the live `data\` folder (or use the app's
  **Backup → Download**, then **Restore** on the new PC). Restore always
  keeps a `pre-restore_*` safety copy first.
- Never mix: a package folder (empty DB) and a live `data\` folder look
  identical — check **Dashboard → Inventory Snapshot** after any move to
  confirm you see your real figures.

---

## Part D — Packaged .exe installation (Windows 10 and Windows 11)

This part is for running the **pre-built .exe** (no Python, no source code
needed on the target machine).

### What's in the package

```
ShresthMasala-Laptop\
  ShresthMasalaIndustry.exe   (the app)
  _internal\                  (bundled backend + frontend)
    frontend\                 (static HTML/JS/CSS pages)
  run-app.bat                 (double-click launcher)
  run-server.bat              (server-only mode)
  run-browser.bat             (open in browser)
  HELP.txt                    (quick reference)
  data\                       (created on first run — your database lives here)
```

### Step-by-step install

1. **Copy the folder to the target PC.**
   Plug in the USB drive, copy the whole `ShresthMasala-Laptop` folder to the
   new laptop, e.g. `C:\ShresthMasala-Laptop\`. Keep all files together.

2. **Install Microsoft Edge WebView2 Runtime (required once).**
   The app uses WebView2 to render its window. On Windows 10 and 11 it is
   usually pre-installed, but if the window stays blank:
   - Download: https://aka.ms/WebView2Bootstrapper
   - Run the installer, then restart the app.
   - Verify: `edge://version` in Edge shows WebView2 version.

3. **Start the app.**
   Double-click **`ShresthMasalaIndustry.exe`** (or `run-app.bat`).
   The first launch may take 10–20 seconds while the local server starts.

4. **First run ever: register an admin account.**
   The database is empty. Click **Register**, create your `admin` account,
   then sign in. Add staff later on the Users page.

5. **Masters, in this order:**
   Customers → Suppliers → Raw Materials → Products → Employees.
   Shortcut: use the **Import** page with `templates\sample_*.xlsx`.

### Where your data lives

- Database: `C:\ShresthMasala-Laptop\data\masala_factory.db` (next to the exe)
- Backups: `C:\ShresthMasala-Laptop\data\backups\`
- App log (diagnostics): `C:\ShresthMasala-Laptop\app.log`
  - If the folder next to the exe isn't writable, the log falls back to
    `%LOCALAPPDATA%\ShresthMasala\app.log`

### Where your data lives

### Updating to a new version

1. Close the app (`Get-Process ShresthMasalaIndustry* | Stop-Process`).
2. Replace `ShresthMasalaIndustry.exe` and `_internal\` with the new build.
3. **Never touch `data\`** — your database is preserved automatically.
4. Restart the app.

### Troubleshooting

| Problem | Fix |
|---|---|
| Window blank / won't open | Install WebView2 Runtime (step 2 above). |
| "Port already in use" | Close any other copy of the app, then retry. |
| `app.log` not created | The app now falls back to `%LOCALAPPDATA%\ShresthMasala\app.log`. |
| Database errors | Never edit `data\masala_factory.db` by hand. Work on a copy. |
| App won't start on Windows 10 | Ensure you're on Windows 10 version 1607 or later (WebView2 requirement). |

