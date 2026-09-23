import psycopg2
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()

conn = psycopg2.connect(
    dbname="medilink"
)

cursor = conn.cursor()

users = [
    (
        "Dr. Demo",
        "doctor@medilink.demo",
        password_hash.hash("doctor123"),
        "doctor"
    ),
    (
        "Receptionist Demo",
        "reception@medilink.demo",
        password_hash.hash("reception123"),
        "receptionist"
    )
]

for user in users:
    cursor.execute(
        """
        INSERT INTO users (
            full_name,
            email,
            password_hash,
            role
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (email) DO NOTHING;
        """,
        user
    )

conn.commit()

cursor.close()
conn.close()

print("Demo users created successfully.")