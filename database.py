import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client['QuizBotDB']

users_col = db['users']
groups_col = db['groups']
scores_col = db['scores']
permissions_col = db['permissions']
bans_col = db['bans']
mutes_col = db['mutes']
custom_questions_col = db['custom_questions']
global_settings_col = db['global_settings']
force_join_col = db['force_join']
welcome_col = db['custom_welcome']
settings_col = db['group_settings']

def save_user(user_id, username, first_name):
    users_col.update_one({"user_id": user_id}, {"$set": {"username": username, "first_name": first_name}}, upsert=True)

def save_group(chat_id, title):
    groups_col.update_one({"chat_id": chat_id}, {"$set": {"title": title}}, upsert=True)
    settings_col.update_one({"chat_id": chat_id}, {"$setOnInsert": {"speed": 15, "limit": 0, "auto_delete": False, "schedule": None}}, upsert=True)

def add_score(user_id, chat_id, username, points, time_taken):
    scores_col.update_one(
        {"user_id": user_id, "chat_id": "global"},
        {"$inc": {"score": points, "correct": 1}, "$set": {"username": username}, "$min": {"fastest_time": time_taken}}, 
        upsert=True
    )
    scores_col.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"$inc": {"score": points, "correct": 1}, "$set": {"username": username}}, 
        upsert=True
    )

def get_user_profile(user_id, chat_id="global"):
    user = scores_col.find_one({"user_id": user_id, "chat_id": chat_id})
    return user if user else {"score": 0, "correct": 0}

def get_top_10(chat_id="global"):
    return list(scores_col.find({"chat_id": chat_id}).sort("score", -1).limit(10))

def get_top_100(chat_id="global"):
    return list(scores_col.find({"chat_id": chat_id}).sort("score", -1).limit(100))

def get_top_10_fastest():
    return list(scores_col.find({"chat_id": "global", "fastest_time": {"$exists": True}}).sort("fastest_time", 1).limit(10))

def get_top_20_fastest():
    return list(scores_col.find({"chat_id": "global", "fastest_time": {"$exists": True}}).sort("fastest_time", 1).limit(20))

def get_user_rank(user_id):
    global_profile = scores_col.find_one({"user_id": user_id, "chat_id": "global"})
    if not global_profile: return 0
    user_score = global_profile.get('score', 0)
    return scores_col.count_documents({"chat_id": "global", "score": {"$gt": user_score}}) + 1

def get_total_users(): return users_col.count_documents({})
def get_total_groups(): return groups_col.count_documents({})
def get_all_groups(): return list(groups_col.find({}))
def get_all_users(): return list(users_col.find({}))

def add_permission(user_id, chat_id): permissions_col.update_one({"user_id": user_id, "chat_id": chat_id}, {"$set": {"status": True}}, upsert=True)
def remove_permission(user_id, chat_id): permissions_col.delete_one({"user_id": user_id, "chat_id": chat_id})
def check_permission(user_id, chat_id): return bool(permissions_col.find_one({"user_id": user_id, "chat_id": chat_id}))

def ban_user(user_id): bans_col.update_one({"user_id": user_id}, {"$set": {"banned": True}}, upsert=True)
def unban_user(user_id): bans_col.delete_one({"user_id": user_id})
def is_banned(user_id): return bool(bans_col.find_one({"user_id": user_id}))

def mute_user(user_id, chat_id): mutes_col.update_one({"user_id": user_id, "chat_id": chat_id}, {"$set": {"muted": True}}, upsert=True)
def unmute_user(user_id, chat_id): mutes_col.delete_one({"user_id": user_id, "chat_id": chat_id})
def is_muted(user_id, chat_id): return bool(mutes_col.find_one({"user_id": user_id, "chat_id": chat_id}))

def reset_group_scores(chat_id): scores_col.delete_many({"chat_id": chat_id})
def reset_all_scores(): scores_col.delete_many({})

def get_group_settings(chat_id):
    settings = settings_col.find_one({"chat_id": chat_id})
    return settings if settings else {"speed": 15, "limit": 0, "auto_delete": False, "schedule": None}

def update_group_setting(chat_id, key, value):
    settings_col.update_one({"chat_id": chat_id}, {"$set": {key: value}}, upsert=True)

def set_global_state(state): global_settings_col.update_one({"_id": "global_quiz"}, {"$set": {"state": state}}, upsert=True)
def get_global_state():
    state = global_settings_col.find_one({"_id": "global_quiz"})
    return state['state'] if state else True

def add_custom_question(category, question, answer):
    custom_questions_col.update_one({"category": category, "question": question}, {"$set": {"answer": answer}}, upsert=True)
def del_custom_question(category, question):
    result = custom_questions_col.delete_one({"category": category, "question": question})
    return result.deleted_count > 0
def get_custom_questions(category): return list(custom_questions_col.find({"category": category}))

def set_force_join(channel_id): force_join_col.update_one({"_id": "channel"}, {"$set": {"channel_id": channel_id}}, upsert=True)
def remove_force_join(): force_join_col.delete_one({"_id": "channel"})
def get_force_join():
    channel = force_join_col.find_one({"_id": "channel"})
    return channel['channel_id'] if channel else None

def set_custom_welcome(chat_id, text, media_id=None, media_type=None):
    welcome_col.update_one({"chat_id": chat_id}, {"$set": {"text": text, "media_id": media_id, "media_type": media_type}}, upsert=True)
def get_custom_welcome(chat_id): return welcome_col.find_one({"chat_id": chat_id})
def remove_custom_welcome(chat_id): welcome_col.delete_one({"chat_id": chat_id})

# Delete Commands (Owner)
def delete_group_score_by_id(group_id):
    res = scores_col.delete_many({"chat_id": int(group_id)})
    return res.deleted_count > 0

def delete_user_score_by_rank(rank):
    top_users = get_top_100()
    if rank <= 0 or rank > len(top_users): return False
    target = top_users[rank - 1]
    scores_col.delete_many({"user_id": target['user_id']})
    return target['username']

def delete_fast_by_rank(rank):
    top_fast = get_top_20_fastest()
    if rank <= 0 or rank > len(top_fast): return False
    target = top_fast[rank - 1]
    scores_col.update_one({"user_id": target['user_id'], "chat_id": "global"}, {"$unset": {"fastest_time": ""}})
    return target['username']
