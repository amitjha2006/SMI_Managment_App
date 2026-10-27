import sqlite3
conn = sqlite3.connect(r'C:\Shresth_Masala_Application_New\ShresthMasala-Laptop\data\masala_factory_24_oct.db')
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table" ORDER BY name')
for row in cursor.fetchall():
    print(row[0])

# Check key table counts
for table in ['users', 'customers', 'suppliers', 'raw_materials', 'products', 'production_entries', 'packaging_sizes']:
    try:
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        count = cursor.fetchone()[0]
        print(f'{table}: {count}')
    except:
        print(f'{table}: N/A')