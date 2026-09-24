from dotenv import load_dotenv
load_dotenv()
import os, psycopg2

conn = psycopg2.connect(
    dbname=os.getenv('MEDILINK_DB_NAME', 'medilink'),
    host=os.getenv('MEDILINK_DB_HOST', 'localhost'),
    port=int(os.getenv('MEDILINK_DB_PORT', '5432')),
    user=os.getenv('MEDILINK_DB_USER', 'postgres'),
    password=os.getenv('MEDILINK_DB_PASSWORD')
)
cur = conn.cursor()
cur.execute("SELECT * FROM patients WHERE id = 'PAT-001'")
row = cur.fetchone()
print('ROW=', row)
cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'patients' ORDER BY ordinal_position")
print('COLUMNS=', cur.fetchall())
cur.close(); conn.close()
