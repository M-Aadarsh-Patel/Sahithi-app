import os
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not set")

client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URI)

db = client.get_default_database("hr_academy")

if __name__ == "__main__":
    print(db.command("ping"), db.name)
