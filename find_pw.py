from passlib.hash import pbkdf2_sha256
import sqlite3

hashes = {
    'Amitjha2006': '$pbkdf2-sha256$29000$33tvTYnxHkOo9b7XGgNAKA$MQHyGASdJMMxqwzcKj5horzc93H6AZoh39VkQ5Wz0h4',
    'amitjha2006': '$pbkdf2-sha256$29000$ZOx9zzkHgJDS.p.ztnZu7Q$zT1IU.eFfbo4rPecAB3KUMigaoYJVTr0vO.9zJO4gak',
    'Data': '$pbkdf2-sha256$29000$j3FuDYEwplRq7f0foxTivA$YqSYU.ai6B9RLHKfjvmuv0wAnO1y9QBNsw7b0C7ViPE',
    'Challan': '$pbkdf2-sha256$29000$fm/tfU9pTUnJOYfQmnNuDQ$2BdLhICyQIAO.WXs9rYtwqlBi.raaWbjlXlU4ocwYcQ',
    'Shresth': '$pbkdf2-sha256$29000$KsWY8763dq713rsX4pxzLg$Ag1Aeeq.uimdblhiVkEkhHkkRcnWNGNdJaCX4pnhS/A',
    'Admin': '$pbkdf2-sha256$29000$IITw3jtnzDknBMD4P2dsLQ$GcG5ivdv5UaoCzMI4XIGgPeB6gy5YHc9X42jjHckjS0',
}

pw_candidates = ['admin', 'Admin', 'password', 'Password', '123456', 'admin123', 'Admin123', 'shresth', 'Shresth', 'amit', 'Amit', 'data', 'Data', 'challan', 'Challan', 'amitjha2006', 'Amitjha2006', 'test', 'Test']

for user, hash_val in hashes.items():
    for pw in pw_candidates:
        if pbkdf2_sha256.verify(pw, hash_val):
            print(f'{user}: {pw}')
            break
    else:
        print(f'{user}: unknown')