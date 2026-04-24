from pymongo import MongoClient
from datetime import datetime
from bson import ObjectId
import certifi
from config import Config

client = MongoClient(Config.MONGO_URI, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
client.admin.command("ping")
print("MongoDB connected")

db = client[Config.MONGO_DB_NAME]
users_col = db["user_data"]
journals_col = db["journals"]
conversations_col = db["conversations"]
decisions_col = db["decisions"]
semantic_graph_col = db["semantic_graph"]
letters_col = db["letters"]

try:
    users_col.create_index("username", unique=True)
except Exception as e:
    print("Index warning:", e)


def create_user(username: str, password_hash: str) -> str:
    new_user = {
        "username": username,
        "password": password_hash,
        "created_at": datetime.utcnow(),
        "onboarding_complete": False,
        "big_five": None,
        "knowledge": [],
    }
    result = users_col.insert_one(new_user)
    return str(result.inserted_id)


def get_user_by_username(username: str):
    return users_col.find_one({"username": username})


def get_user_by_id(user_id: str):
    return users_col.find_one({"_id": ObjectId(user_id)})


def reset_onboarding(user_id: str):
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"onboarding_complete": False, "big_five": None, "voice_id": None}}
    )


def update_user_onboarding(user_id: str, big_five: dict, knowledge_entries: list, voice_id: str = None, voice_sample: str = None):
    now = datetime.utcnow()
    update = {
        "$set": {
            "big_five": big_five,
            "onboarding_complete": True,
            "updated_at": now,
        },
        "$push": {"knowledge": {"$each": knowledge_entries}},
    }
    if voice_id:
        update["$set"]["voice_id"] = voice_id
    if voice_sample:
        update["$set"]["voice_sample"] = voice_sample
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        update,
    )


def add_knowledge_entry(user_id: str, entry: dict):
    entry["created_at"] = datetime.utcnow()
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$push": {"knowledge": entry}}
    )


def get_user_knowledge(user_id: str):
    user = users_col.find_one({"_id": ObjectId(user_id)}, {"knowledge": 1})
    return user.get("knowledge", []) if user else []


def create_journal_entry(user_id: str, text: str, mood: str | None, source: str, embedding: list):
    entry = {
        "user_id": ObjectId(user_id),
        "text": text,
        "mood": mood,
        "source": source,
        "embedding": embedding,
        "created_at": datetime.utcnow(),
    }
    result = journals_col.insert_one(entry)
    return str(result.inserted_id)


def get_journal_entries(user_id: str, limit: int = 20, skip: int = 0):
    entries = journals_col.find(
        {"user_id": ObjectId(user_id)}
    ).sort("created_at", -1).skip(skip).limit(limit)
    return list(entries)


def delete_journal_entry(user_id: str, entry_id: str) -> bool:
    """Delete a journal entry and purge its linked knowledge row. Returns True on success."""
    entry = journals_col.find_one({"_id": ObjectId(entry_id), "user_id": ObjectId(user_id)})
    if not entry:
        return False
    journals_col.delete_one({"_id": ObjectId(entry_id), "user_id": ObjectId(user_id)})
    # Remove the matching knowledge entry from the user doc (match by text + source).
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$pull": {"knowledge": {"type": "journal_entry", "text": entry.get("text")}}}
    )
    return True


def create_conversation(user_id: str, mode: str):
    conv = {
        "user_id": ObjectId(user_id),
        "mode": mode,
        "messages": [],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = conversations_col.insert_one(conv)
    return str(result.inserted_id)


def add_message_to_conversation(conversation_id: str, role: str, content: str, audio_url: str | None = None):
    msg = {
        "role": role,
        "content": content,
        "audio_url": audio_url,
        "created_at": datetime.utcnow(),
    }
    conversations_col.update_one(
        {"_id": ObjectId(conversation_id)},
        {
            "$push": {"messages": msg},
            "$set": {"updated_at": datetime.utcnow()},
        }
    )


def get_conversation(conversation_id: str):
    return conversations_col.find_one({"_id": ObjectId(conversation_id)})


def get_user_conversations(user_id: str, limit: int = 10):
    return list(conversations_col.find(
        {"user_id": ObjectId(user_id)}
    ).sort("updated_at", -1).limit(limit))


def create_decision(user_id: str, title: str, description: str, branches: list):
    decision = {
        "user_id": ObjectId(user_id),
        "title": title,
        "description": description,
        "branches": branches,
        "created_at": datetime.utcnow(),
    }
    result = decisions_col.insert_one(decision)
    return str(result.inserted_id)


def get_user_decisions(user_id: str, limit: int = 20):
    return list(decisions_col.find(
        {"user_id": ObjectId(user_id)}
    ).sort("created_at", -1).limit(limit))


def get_decision(decision_id: str):
    return decisions_col.find_one({"_id": ObjectId(decision_id)})


def upsert_semantic_graph(user_id: str, nodes: list, edges: list):
    now = datetime.utcnow()
    semantic_graph_col.update_one(
        {"user_id": ObjectId(user_id)},
        {
            "$set": {
                "nodes": nodes,
                "edges": edges,
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": ObjectId(user_id),
                "created_at": now,
            }
        },
        upsert=True
    )


def get_semantic_graph(user_id: str):
    return semantic_graph_col.find_one({"user_id": ObjectId(user_id)})


# --- Letters (Future-Self) ---

def create_letter(user_id: str, subject: str, content: str, trigger: str = "auto") -> str:
    doc = {
        "user_id": ObjectId(user_id),
        "subject": subject,
        "content": content,
        "trigger": trigger,
        "read_at": None,
        "created_at": datetime.utcnow(),
    }
    result = letters_col.insert_one(doc)
    return str(result.inserted_id)


def get_user_letters(user_id: str, limit: int = 50):
    return list(
        letters_col.find({"user_id": ObjectId(user_id)})
        .sort("created_at", -1)
        .limit(limit)
    )


def get_letter(letter_id: str, user_id: str):
    return letters_col.find_one({
        "_id": ObjectId(letter_id),
        "user_id": ObjectId(user_id),
    })


def mark_letter_read(letter_id: str, user_id: str):
    letters_col.update_one(
        {"_id": ObjectId(letter_id), "user_id": ObjectId(user_id)},
        {"$set": {"read_at": datetime.utcnow()}}
    )


def get_last_letter_time(user_id: str):
    doc = letters_col.find_one(
        {"user_id": ObjectId(user_id)},
        sort=[("created_at", -1)]
    )
    return doc.get("created_at") if doc else None


def count_unread_letters(user_id: str) -> int:
    return letters_col.count_documents({
        "user_id": ObjectId(user_id),
        "read_at": None,
    })


def delete_letter(letter_id: str, user_id: str) -> bool:
    result = letters_col.delete_one({
        "_id": ObjectId(letter_id),
        "user_id": ObjectId(user_id),
    })
    return result.deleted_count > 0


# --- Integrations ---

def set_integration(user_id: str, provider: str, data: dict):
    """Stores or updates a user's integration blob at user.integrations.<provider>."""
    data = {**data, "updated_at": datetime.utcnow()}
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {f"integrations.{provider}": data}}
    )


def get_integration(user_id: str, provider: str):
    user = users_col.find_one(
        {"_id": ObjectId(user_id)},
        {f"integrations.{provider}": 1}
    )
    if not user:
        return None
    return (user.get("integrations") or {}).get(provider)


def get_all_integrations(user_id: str) -> dict:
    user = users_col.find_one({"_id": ObjectId(user_id)}, {"integrations": 1})
    return (user or {}).get("integrations", {}) if user else {}


def clear_integration(user_id: str, provider: str):
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$unset": {f"integrations.{provider}": ""}}
    )


def mark_integration_sync(user_id: str, provider: str, ingested_count: int = 0):
    users_col.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {
            f"integrations.{provider}.last_sync_at": datetime.utcnow(),
            f"integrations.{provider}.last_sync_count": ingested_count,
        }}
    )