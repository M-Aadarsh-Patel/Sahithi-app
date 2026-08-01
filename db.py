import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not set")

# One client per process. MongoClient is thread-safe and owns its own connection
# pool, so it is built once at import and shared, never per request.
client = MongoClient(MONGO_URI)

# Uses the database named in the URI; falls back to "hr_academy" if it has none.
db = client.get_default_database("hr_academy")

if __name__ == "__main__":
    print(db.command("ping"), db.name)
