# Live-DB reset + supplier/customer field parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wipe all business entries from the live DB while keeping users, company profile, footers and settings; bring supplier entry to full parity with customer (Contact Number 2, Bank Name, Notes) on both forms.

**Architecture:** Workstream A is an ops task (backup → sanitize → verify, no code changes). Workstream B is a UI + export gap closure — backend schemas, models, and both importers already handle the three fields, so only surfaces change. No DB migration (columns already exist on both tables).

**Tech Stack:** SQLite (file `data/masala_factory.db`), FastAPI/Pydantic v2, openpyxl round-trip, pytest with `db` fixture, pywebview frontend.

**Spec:** User decisions recorded in-session: wipe live DB with the branch-package KEEP set; delete `data/backups/*.db` too; add Contact 2 + Bank Name + Notes inputs to supplier form AND customer form; customer field-removal request dropped.

## Global Constraints

- The live database must never be written by automated tests (enforced by `backend/tests/conftest.py` tripwires).
- Export header labels must equal import lookup keys or round-trip breaks.
- Name + Contact Number stay the only mandatory customer/supplier fields; new fields optional.
- The app must be closed before any direct DB file operation (locked-DB failure precedent).

## Review Focus

- Wipe run while app open → locked DB / partial delete. Test: app-process check before backup step.
- Backup glob accidentally deleting the fresh pre-reset backup. Test: exclude it by name, verify it survives.
- Export/import header label drift on the two new supplier columns. Test: Task B2 round-trip.
- `saveSupplier`/`fillForm` missing one of the three fields → silent data loss on edit. Test: on-screen save/edit round-trip per field.
- Mandatory-field validation unchanged. Test: existing suite + manual required-attribute check.

---

### Task A1: Pre-reset backup

**Files:**
- Create: `data/backups/pre-reset-<date>.db` (+ `.secret_key` copy alongside, same stamp)
- Modify: none

- [ ] **Step 1: Confirm the app is closed**

Run: `Get-Process SMIMasalaFactory, python`
Expected: no output (nothing holding the DB).

- [ ] **Step 2: Copy live DB + secret key to timestamped backup**

Run: copy `data/masala_factory.db` → `data/backups/pre-reset-<yyyymmdd-hhmm>.db` and `.secret_key` likewise.
Expected: both files exist with matching sizes to the originals.

### Task A2: Sanitize live DB

**Files:**
- Modify: `data/masala_factory.db` (in place)
- Keep tables: `users`, `roles`, `permissions`, `role_permissions`, `dashboard_preferences`, `application_settings`, `print_footers`, `document_sequences`
- Wipe: every other table, then `VACUUM`. Then delete `data/backups/*.db` except the Step-A1 backup.

- [ ] **Step 1: Sanitize**

Run: temp script — `DELETE FROM` each non-KEEP table, `VACUUM`.
Expected: script prints kept counts + all wiped tables at 0.

- [ ] **Step 2: Verify counts**

Run: row-count probe.
Expected: `users`=3, `application_settings`=9, `permissions`=34, `dashboard_preferences`=1; all business tables 0; company name still `Shresth Masala Industry`.

- [ ] **Step 3: Rollback ready**

If Step 2 deviates, restore the Step-A1 backup over `data/masala_factory.db`. Report counts to user either way.

### Task B1: Supplier export columns

**Files:**
- Modify: `backend/app/services/excel_export.py` (`export_suppliers`, headers + row mapping)
- Test: `backend/tests/test_supplier_parity.py`

**Interfaces:**
- Consumes: `export_suppliers(db) -> (title, headers, rows)` existing signature.
- Produces: headers include `"Contact Number 2"`, `"Bank Name"`, `"Notes"` mapped from `s.contact_number_2`, `s.bank_detail`, `s.notes`.

- [ ] **Step 1: Write the failing test**

```python
def test_supplier_export_has_customer_parity_columns(db):
    _, headers, _ = export_suppliers(db)
    assert "Contact Number 2" in headers
    assert "Bank Name" in headers
    assert "Notes" in headers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_supplier_parity.py -v`
Expected: FAIL (2 of 3 headers missing).

- [ ] **Step 3: Implement mapping in `export_suppliers`**

Mirror `export_customers` (`excel_export.py:77-115`): insert the two headers after "Contact Number" and map the row dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_supplier_parity.py -v`
Expected: PASS.

### Task B2: Supplier round-trip test

**Files:**
- Test: `backend/tests/test_supplier_parity.py` (append)

- [ ] **Step 1: Write the test**

```python
def test_supplier_round_trip_keeps_new_fields(make_db):
    db1, db2 = make_db(), make_db()
    # import supplier xlsx with Contact 2 / Bank Name / Notes into db1,
    # export via run_export, re-import into db2, assert all three survive
```

- [ ] **Step 2: Run test**

Run: `pytest backend/tests/test_supplier_parity.py -v`
Expected: PASS (importer already reads these keys); if FAIL, fix the importer key lookup.

### Task B3: Forms (both) + detail/view + endpoint passthrough

**Files:**
- Modify: `frontend/pages/suppliers.html` (form inputs + `saveSupplier` payload + `fillForm` + detail grid + view card)
- Modify: `frontend/pages/customers.html` (same three additions)
- Verify-only: `backend/main.py` `create_supplier`/`update_supplier` + customer equivalents persist the fields

- [ ] **Step 1: Implement supplier form + views**

Add Contact 2, Bank Name, Notes inputs (same `form-row` pattern), payload keys `contact_number_2`, `bank_detail`, `notes`; load them in `fillForm`; add Bank Name/Notes rows to `supDetailGrid` + `viewSupplier` card.

- [ ] **Step 2: Implement customer form + views**

Same three additions in `saveCustomer` payload + `fillForm` + `detailGrid` + `viewCustomer` card.

- [ ] **Step 3: Endpoint passthrough check**

Read `create_supplier`/`update_supplier` (+ customer equivalents) in `backend/main.py`; if any of the three fields is dropped, map it through. Re-run Task B2 tests.

- [ ] **Step 4: On-screen check**

Save + edit one supplier and one customer with all three fields filled; confirm values persist after reload.

### Task B4: Full verification

- [ ] **Step 1: Run the full suite**

Run: `pytest backend/tests/ -q`
Expected: all green.

- [ ] **Step 2: Lint touched files**

Run: `python -m ruff check` on touched files.
Expected: no new violations (6 pre-existing in `excel_import.py` stay).

- [ ] **Step 3: Rebuild exe**

Only on explicit user go-ahead (app closed first, live-data preserve path in `scripts/build.py`).
