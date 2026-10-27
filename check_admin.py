import sqlite3
conn = sqlite3.connect(r'C:\Shresth_Masala_Application_New\ShresthMasala-Laptop\data\masala_factory.db')
cursor = conn.cursor()
cursor.execute('SELECT username, hashed_password FROM users WHERE username="Admin"')
row = cursor.fetchone()
print(row[0], row[1][:30])