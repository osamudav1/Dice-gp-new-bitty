import json
import os
from pymongo import MongoClient

MONGODB_URL_LAST = os.environ.get("MONGODB_URL_LAST")
DB_FILE = "database.json"

def migrate():
    if not MONGODB_URL_LAST:
        print("❌ MONGODB_URL_LAST not found in environment variables.")
        return

    if not os.path.exists(DB_FILE):
        print(f"❌ {DB_FILE} not found. Nothing to migrate.")
        return

    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ Error reading {DB_FILE}: {e}")
        return

    users = data.get("users", {})
    if not users:
        print("ℹ️ No users found in JSON database.")
        return

    try:
        client = MongoClient(MONGODB_URL_LAST)
        db = client.get_default_database()
        collection = db["users"]

        count = 0
        for user_id_str, user_data in users.items():
            user_id = int(user_id_str)
            balance = user_data.get("balance", 0)
            
            # Prepare document (Balance only as requested)
            doc = {
                "user_id": user_id,
                "balance": balance
            }
            # Upsert
            collection.update_one({"user_id": user_id}, {"$set": doc}, upsert=True)
            count += 1

        print(f"✅ Successfully migrated {count} user balances to MongoDB (MONGODB_URL_LAST).")
        client.close()
    except Exception as e:
        print(f"❌ MongoDB migration error: {e}")

if __name__ == "__main__":
    migrate()
