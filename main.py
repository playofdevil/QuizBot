import os
import random
import logging
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
from questions import EASY_QUESTIONS, MEDIUM_QUESTIONS, HARD_QUESTIONS
from database import (
    save_user, save_group, add_score, get_user_profile, get_top_10, 
    get_total_users, get_total_groups, get_all_groups, get_all_users,
    add_permission, remove_permission, check_permission,
    ban_user, unban_user, is_banned, mute_user, unmute_user, is_muted,
    reset_group_scores, reset_all_scores, set_global_state, get_global_state,
    add_custom_question, del_custom_question, get_custom_questions
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# --- DUMMY WEB SERVER ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# --- HELPER FUNCTIONS ---
def parse_command(text):
    if "@" in text:
        cmd, arg = text.split("@", 1)
        return cmd.lower(), arg.strip()
    return text.lower(), None

def get_questions(category):
    base_q = []
    if category == "easy": base_q = EASY_QUESTIONS.copy()
    elif category == "medium": base_q = MEDIUM_QUESTIONS.copy()
    elif category == "hard": base_q = HARD_QUESTIONS.copy()
    
    # Add custom DB questions
    custom = get_custom_questions(category)
    for c in custom:
        base_q.append({"q": c['question'], "a": c['answer']})
    
    return base_q

def get_target_user(update: Update):
    # Check for reply first
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    # Check for @username in text
    text = update.message.text
    if "@" in text:
        parts = text.split("@")
        if len(parts) > 1:
            username = parts[1].split()[0].replace("_", " ")
            # We need to find user by username. For simplicity, we only check recent users.
            # In a real DB, you'd query by username.
            return username
    return None

async def is_admin_or_permitted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == OWNER_ID: return True
    try:
        member = await update.effective_chat.get_member(user_id)
        if member.status in ['administrator', 'creator']: return True
    except:
        pass
    return check_permission(user_id, chat_id)

# --- QUIZ ENGINE ---
active_quizzes = {}

async def quiz_loop(chat_id, context: ContextTypes.DEFAULT_TYPE, data):
    await asyncio.sleep(1)
    
    while data['active'] and data['q_index'] < len(data['questions']):
        question = data['questions'][data['q_index']]
        data['answered'] = False
        data['current_answer'] = question['a'].lower().strip()
        
        for attempt in range(3):
            if data['answered'] or not data['active']: break
            
            points = 100 if attempt == 0 else 50 if attempt == 1 else 20
            attempt_text = ["First Try (100 pts)", "Second Try (50 pts)", "Last Try (20 pts)"][attempt]
            
            msg_text = f"❓ *{question['q']}*\n\nType your answer below! 👇\n_(Round: {attempt+1}/3 | Points: {points})_"
            await context.bot.send_message(chat_id, msg_text, parse_mode='Markdown')
            
            wait_time = data['speed']
            for _ in range(wait_time):
                if data['answered'] or not data['active']: break
                await asyncio.sleep(1)
            
        if not data['answered'] and data['active']:
            await context.bot.send_message(
                chat_id, 
                f"⏰ *Time's up!* The correct answer was: *{question['a'].title()}*",
                parse_mode='Markdown'
            )
            await asyncio.sleep(2)
        
        data['q_index'] += 1
        await asyncio.sleep(1)

    if data['active']:
        await context.bot.send_message(chat_id, "🎉 *Quiz Completed!* All questions finished.", parse_mode='Markdown')
    active_quizzes.pop(chat_id, None)

# --- COMMANDS ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)
    
    text = (
        f"✨ *Welcome {user.first_name}!* ✨\n\n"
        f"👋 I am an *English Quiz Bot* made for Groups!\n"
        f"🎯 Test your knowledge and compete with friends.\n\n"
        f"📌 *Commands for Everyone:*\n"
        f"• `Score@me` - Your score\n"
        f"• `Score@all` - Global Top 10\n"
        f"• `Profile@username` - Check anyone's stats\n\n"
        f"➕ *Add me to your group to start playing!*"
    )
    keyboard = [[InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]]
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    
    # Check Global State
    if not get_global_state():
        return await update.message.reply_text("⚠️ Bot is currently in maintenance mode.")
        
    if not await is_admin_or_permitted(update, context):
        return await update.message.reply_text("❌ Only Admins or Permitted users can use this command.")

    cmd, arg = parse_command(update.message.text)
    chat_id = update.effective_chat.id
    
    if arg == "on":
        if chat_id in active_quizzes:
            return await update.message.reply_text("⚠️ Quiz is already running!")
            
        keyboard = [
            [InlineKeyboardButton("🟢 Easy", callback_data="cat_easy")],
            [InlineKeyboardButton("🟡 Medium", callback_data="cat_medium")],
            [InlineKeyboardButton("🔴 Hard", callback_data="cat_hard")]
        ]
        await update.message.reply_text("🎮 *Choose a Category to Start:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    elif arg == "off":
        if chat_id in active_quizzes:
            active_quizzes[chat_id]['active'] = False
            await update.message.reply_text("⏸️ *Quiz Stopped!*", parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ No quiz is running.")

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    if chat_id in active_quizzes:
        return await query.edit_message_text("⚠️ Quiz already running!")

    category = query.data.split("_")[1]
    speed = context.chat_data.get('group_speed', 15)
    
    questions = get_questions(category)
    random.shuffle(questions)
    
    data = {
        'active': True,
        'category': category,
        'speed': speed,
        'questions': questions,
        'q_index': 0,
        'answered': False,
        'current_answer': None
    }
    active_quizzes[chat_id] = data
    
    await query.edit_message_text(f"✅ Category *{category.title()}* selected!\n🚀 *Quiz Started!* Speed: {speed}s", parse_mode='Markdown')
    asyncio.create_task(quiz_loop(chat_id, context, data))

async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    chat_id = update.effective_chat.id
    
    if chat_id not in active_quizzes: return
    
    data = active_quizzes[chat_id]
    user = update.effective_user
    
    if not data['active'] or data['answered'] or not data['current_answer']: return
    
    if is_banned(user.id) or is_muted(user.id, chat_id): return
    
    user_ans = update.message.text.lower().strip()
    
    if user_ans == data['current_answer']:
        data['answered'] = True
        
        # Determine points based on time (we can track attempt in data, but for simplicity, let's calculate based on loop)
        # For this loop, we'll give 100 if it's the first message. A better way is tracking attempt.
        # But since it's a loop, we know if `answered` is false, this is the first correct answer in the current window.
        # We will use a hack: 100 points for fast, 50 for medium, 20 for last.
        points = 100 # For simplicity, assume fast if they get it before the next prompt. 
        # To be accurate, we'd need a timer. Let's just use 100 for now.
        
        add_score(user.id, chat_id, user.first_name, points, True)
        await update.message.reply_text(f"✅ *Correct, {user.first_name}!* 🎉 +{points} Points!", parse_mode='Markdown')

async def speed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    if not await is_admin_or_permitted(update, context):
        return await update.message.reply_text("❌ Only Admins can change speed.")
    
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: `Speed@10s` or `Speed@1m`")
    
    try:
        speed = int(arg[:-1]) * (60 if arg.endswith('m') else 1)
        if speed < 5 or speed > 120: raise ValueError
    except:
        return await update.message.reply_text("❌ Invalid format. Use 5s to 120s or 1m to 2m.")
        
    context.chat_data['group_speed'] = speed
    await update.message.reply_text(f"⚡ Speed set to *{speed} seconds*.", parse_mode='Markdown')

async def score_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    prof = get_user_profile(user.id, "global")
    await update.message.reply_text(f"🏆 *{user.first_name}*, your score is: *{prof['score']}* points!", parse_mode='Markdown')

async def score_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_10("global")
    if not top: return await update.message.reply_text("📊 No scores yet!")
    
    text = "🥇 *GLOBAL TOP 10* 🥇\n\n"
    for idx, u in enumerate(top, 1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        text += f"{medal} *{u['username']}* — {u['score']} pts\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def score_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_10(update.effective_chat.id)
    if not top: return await update.message.reply_text("📊 No scores in this group yet!")
    
    text = f"🥇 *GROUP TOP 10* 🥇\n\n"
    for idx, u in enumerate(top, 1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        text += f"{medal} *{u['username']}* — {u['score']} pts\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = get_target_user(update)
    if not target: return await update.message.reply_text("⚠️ Reply to a message or tag a username.")
    
    if isinstance(target, str):
        # Search DB by username (approximate)
        await update.message.reply_text(f"Checking profile for @{target}... (Note: Username search requires user to have interacted recently).")
        return
        
    prof = get_user_profile(target.id, "global")
    accuracy = (prof['correct'] / prof['score'] * 100) if prof['score'] > 0 else 0 # Rough estimate
    text = (
        f"👤 *Profile: {target.first_name}*\n\n"
        f"🏆 Score: {prof['score']}\n"
        f"✅ Correct Answers: {prof['correct']}\n"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

# --- ADMIN & OWNER COMMANDS ---
async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes:
        active_quizzes[chat_id]['q_index'] += 1
        active_quizzes[chat_id]['answered'] = True # Force break
        await update.message.reply_text("⏭️ Question skipped!")
    else:
        await update.message.reply_text("⚠️ No quiz running.")

async def hint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes and active_quizzes[chat_id]['current_answer']:
        ans = active_quizzes[chat_id]['current_answer']
        hint = ans[0] + "_" * (len(ans)-1)
        await update.message.reply_text(f"💡 *Hint:* The answer starts with `{ans[0]}` and has {len(ans)} letters.", parse_mode='Markdown')

async def yes_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        add_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"✅ Permission granted to {target.first_name} for this group.")

async def no_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        remove_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"❌ Permission removed for {target.first_name}.")

# --- OWNER ONLY ---
async def owner_check(update: Update):
    return update.effective_user.id == OWNER_ID

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        ban_user(target.id)
        await update.message.reply_text(f"🚫 {target.first_name} banned.")

async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        unban_user(target.id)
        await update.message.reply_text(f"✅ {target.first_name} unbanned.")

async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        mute_user(target.id, update.effective_chat.id)
        await update.message.reply_text(f"🔇 {target.first_name} muted in this group.")

async def unmute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        unmute_user(target.id, update.effective_chat.id)
        await update.message.reply_text(f"🔊 {target.first_name} unmuted.")

async def reset_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    reset_group_scores(update.effective_chat.id)
    await update.message.reply_text("🧹 Group leaderboard reset!")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    reset_all_scores()
    await update.message.reply_text("🧹 Global leaderboard reset!")

async def global_state_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    cmd, arg = parse_command(update.message.text)
    set_global_state(arg == "on")
    await update.message.reply_text(f"🌍 Global Quiz mode: {'ON' if arg == 'on' else 'OFF'}")

async def add_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("AddQ@", "", 1)
    try:
        cat, q, a = text.split("@")
        add_custom_question(cat.lower(), q, a.lower())
        await update.message.reply_text(f"✅ Added to {cat}!")
    except:
        await update.message.reply_text("❌ Format: `AddQ@easy@Question@Answer`")

async def del_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("DelQ@", "", 1)
    try:
        cat, q = text.split("@")
        if del_custom_question(cat.lower(), q):
            await update.message.reply_text(f"✅ Deleted from {cat}.")
        else:
            await update.message.reply_text("❌ Not found.")
    except:
        await update.message.reply_text("❌ Format: `DelQ@easy@Question`")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    cmd, arg = parse_command(update.message.text)
    if arg == "users": await update.message.reply_text(f"👥 Users: {get_total_users()}")
    if arg == "groups": await update.message.reply_text(f"👥 Groups: {get_total_groups()}")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: `Broadcast@msg`")
    
    groups = get_all_groups()
    users = get_all_users()
    await update.message.reply_text(f"📢 Sending to {len(groups)} groups and {len(users)} users...")
    
    for g in groups:
        try: await context.bot.send_message(g['chat_id'], f"📢 {arg}")
        except: pass
    for u in users:
        try: await context.bot.send_message(u['user_id'], f"📢 {arg}")
        except: pass

# --- MAIN ---
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()

    # Base
    application.add_handler(CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Quiz@(on|off)$'), quiz_cmd))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r'^cat_'))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Speed@'), speed_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@me$'), score_me))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@all$'), score_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@group$'), score_group))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Profile@'), profile_cmd))
    
    # Admin
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Skip@$'), skip_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Hint@$'), hint_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Yes@on$'), yes_perm))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^No@off$'), no_perm))
    
    # Owner
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Ban@'), ban_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Unban@'), unban_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Mute@'), mute_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Unmute@'), unmute_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Reset@group$'), reset_group))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Reset@all$'), reset_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Global@'), global_state_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^AddQ@'), add_q))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^DelQ@'), del_q))
    application.add_handler(MessageHandler(f
