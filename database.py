import os
from pymongo import MongoClient

# Render ke environment variables se link aayega
MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client['QuizBotDB']

# Collections (Tables)
users_col = db['users']
groups_col = db['groups']
scores_col = db['scores']

def save_user(user_id, username):
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"username": username}},
        upsert=True
    )

def save_group(chat_id, title):
    groups_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"title": title}},
        upsert=True
    )

def add_score(user_id, username, points=1):
    scores_col.update_one(
        {"user_id": user_id},
        {"$inc": {"score": points}, "$set": {"username": username}},
        upsert=True
    )

def get_user_score(user_id):
    user = scores_col.find_one({"user_id": user_id})
    return user['score'] if user else 0

def get_top_10():
    # Score ke hisaab se top 10 users
    top_users = scores_col.find().sort("score", -1).limit(10)
    return list(top_users)

def get_total_users():
    return users_col.count_documents({})

def get_total_groups():
    return groups_col.count_documents({})

def get_all_groups():
    return list(groups_col.find({}))

def get_all_users():
    return list(users_col.find({}))
