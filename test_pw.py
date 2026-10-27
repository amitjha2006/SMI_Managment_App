from passlib.hash import pbkdf2_sha256
hash = "$pbkdf2-sha256$29000$IITw3jtnzDknBMD4P2dsLQ$GcG5ivdv5UaoCzMI4XIGgPeB6gy5YHc9X42jjHckjS0"
for pw in ['admin', 'Admin', 'password', 'Password', '123456', 'admin123', 'Admin123', 'shresth', 'Shresth', 'amit', 'Amit']:
    if pbkdf2_sha256.verify(pw, hash):
        print('Admin password:', pw)
        break