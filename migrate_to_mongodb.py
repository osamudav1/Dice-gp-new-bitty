import json
import os
from pymongo import MongoClient

MONGODB_URL = os.environ.get("MONGODB_URL")
DB_FILE = "database.json"
DICE_DB_NAME = "dice_gp"

def migrate():
    if not MONGODB_URL:
        print("❌ MONGODB_URL not found in environment variables.")
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
        client = MongoClient(MONGODB_URL)
        db = client[DICE_DB_NAME]
        collection = db["users"]

        count = 0
        for user_id_str, user_data in users.items():
            user_id = int(user_id_str)
            # Prepare document
            doc = {
                "user_id": user_id,
                "name": user_data.get("name", "Unknown"),
                "mention": user_data.get("mention", ""),
                "total_bet": user_data.get("total_bet", 0),
                "total_win": user_data.get("total_win", 0),
                "balance": user_data.get("balance", 0)
            }
            # Upsert
            collection.update_one({"user_id": user_id}, {"$set": doc}, upsert=True)
            count += 1

        print(f"✅ Successfully migrated {count} users to MongoDB.")
        client.close()
    except Exception as e:
        print(f"❌ MongoDB migration error: {e}")

if __name__ == "__main__":
    migrate()
