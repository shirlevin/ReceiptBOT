import psycopg2
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'telegramdb')
}
# Connect to your RDS database
conn = psycopg2.connect(DB_CONFIG)
print("connected")

cursor = conn.cursor()
# Verify the table was created
cursor.execute("""
    SELECT * from payments
""")

columns = cursor.fetchall()
print(columns)

cursor.close()
conn.close()
print("Database connection closed.")