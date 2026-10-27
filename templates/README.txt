SAMPLE IMPORT TEMPLATES
=======================

Each file's first row holds the column headers - do not rename or remove them.
Delete the sample data rows and add your own; the header row must stay in row 1.

Import order (important):
  1. customers
  2. suppliers
  3. raw_materials
  4. products
  5. purchase_invoices (needs suppliers + raw_materials)
  6. sales_challans (needs customers + products)
  7. customer_payments (needs sales challans)
  8. expenses
  9. employees

General tips:
- Dates work in many formats: 2024-01-05, 05-01-2024, 05/01/2024
- Amounts tolerate currency symbols and commas (e.g. Rs 1,500)
- Coded IDs are optional - the system generates them when left blank.
- Re-running an import skips rows that already exist (matches on code/name/challan number).
