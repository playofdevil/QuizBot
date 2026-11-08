import os
import random
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
)
from questions import EASY_QUESTIONS, MEDIUM_QUESTIONS, HARD_QUESTIONS
from database import (
    save_user, save_group, add_score, get_user_score, 
    get_top_10, get_total_users, get_total_groups, get_all_groups, get_all_users
)

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# --- DUMMY WEB SERVER FOR RENDER (To prevent Port Scan Timeout) ---
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
        return cmd.lower(), arg.lower()
    return text.lower(), None

def get_questions(category):
    if category == "easy": return EASY_QUESTIONS
    if category == "medium": return MEDIUM_QUESTIONS
    if category == "hard": return HARD_QUESTIONS
    return []

# --- QUIZ ENGINE LOGIC ---
async def send_question(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    data = job.data
    
    q_index = data['q_index']
    repeat_count = data['repeat']
    question_list = get_questions(data['category'])
    
    if q_index >= len(question_list):
        await context.bot.send_message(chat_id, "🎉 *Quiz Completed!* All questions finished. Use `Quiz@on` to play again.", parse_mode='Markdown')
        context.chat_data.pop('quiz_active', None)
        return

    question = question_list[q_index]

    if repeat_count >= 3:
        await context.bot.send_message(
            chat_id, 
            f"⏰ *Time's up!* The correct answer was: *{question['a'].title()}*\n\nStarting next question... 🔄",
            parse_mode='Markdown'
        )
        data['q_index'] += 1
        data['repeat'] = 0
        job.data = data
        context.job_queue.run_once(send_question, 2, chat_id=chat_id, data=data)
        return

    msg = await context.bot.send_message(
        chat_id, 
        f"❓ *{question['q']}*\n\nType your answer below! 👇\n_(Time: {data['speed']}s | Repeat: {repeat_count+1}/3)_",
        parse_mode='Markdown'
    )
    data['repeat'] += 1
    job.data = data
    
    # Schedule the next repeat if no one answers
    context.job_queue.run_once(send_question, data['speed'], chat_id=chat_id, data=data)

async def start_quiz(chat_id, context: ContextTypes.DEFAULT_TYPE, category: str, speed: int = 15):
    questions = get_questions(category)
    if not questions:
        await context.bot.send_message(chat_id, "❌ No questions found for this category.")
        return

    random.shuffle(questions)
    
    data = {
        'category': category,
        'speed': speed,
        'q_index': 0,
        'repeat': 0,
        'answer_guessed': False
    }
    
    context.chat_data['quiz_active'] = True
    context.chat_data['quiz_data'] = data
    
    await context.bot.send_message(
        chat_id, 
        f"🚀 *Quiz Started!*\nCategory: *{category.title()}*\nSpeed: {speed}s | Repeat: 3 times\nLet's go! 🔥",
        parse_mode='Markdown'
    )
    
    # Send first question immediately
    context.job_queue.run_once(send_question, 1, chat_id=chat_id, data=data)

# --- COMMAND HANDLERS ---

async def private_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.first_name)
    
    welcome_text = (
        f"🎉 *Welcome to English Quiz Bot!* 🎉\n\n"
        f"Hey {user.first_name}! 👋\n"
        f"I am an *English Quiz Bot* — made for *Groups* only! 🎯\n\n"
        f"📌 *How to use me:*\n"
        f"➕ Add me to your group\n"
        f"▶️ `Quiz@on` — Start the quiz\n"
        f"⏸️ `Quiz@off` — Stop the quiz\n"
        f"⚡ `Speed@10s` — Set speed (Admin only)\n"
        f"🏆 `Score@me` — Check your score\n"
        f"🥇 `Score@all` — Top 10 Leaderboard\n\n"
        f"Add me to your group and enjoy the quiz! 🚀🔥"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("❌ This command only works in Groups! Add me to a group to play. 👥")
        return
    
    text = update.message.text
    cmd, arg = parse_command(text)
    
    if arg == "on":
        if context.chat_data.get('quiz_active'):
            await update.message.reply_text("⚠️ A quiz is already running in this group! Use `Quiz@off` to stop it first.")
            return
        
        keyboard = [
            [InlineKeyboardButton("🟢 Easy", callback_data="cat_easy")],
            [InlineKeyboardButton("🟡 Medium", callback_data="cat_medium")],
            [InlineKeyboardButton("🔴 Hard", callback_data="cat_hard")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🎮 *Choose a Category to Start:*", reply_markup=reply_markup, parse_mode='Markdown')

    elif arg == "off":
        if not context.chat_data.get('quiz_active'):
            await update.message.reply_text("⚠️ No quiz is currently running.")
            return
        context.chat_data.pop('quiz_active', None)
        context.chat_data.pop('quiz_data', None)
        await update.message.reply_text("⏸️ *Quiz Stopped!* Thanks for playing. 🎉", parse_mode='Markdown')

async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if context.chat_data.get('quiz_active'):
        await query.edit_message_text("⚠️ Quiz is already running!")
        return

    category = query.data.split("_")[1]
    speed = context.chat_data.get('group_speed', 15)
    
    await query.edit_message_text(f"✅ Category *{category.title()}* selected!\nStarting quiz at {speed}s speed... 🚀", parse_mode='Markdown')
    await start_quiz(query.message.chat_id, context, category, speed)

async def speed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']:
        return
    
    try:
        member = await update.effective_chat.get_member(update.effective_user.id)
        if member.status not in ['administrator', 'creator']:
            await update.message.reply_text("❌ Only Group Admins can change the speed! 🛑")
            return
    except Exception:
        await update.message.reply_text("❌ Could not verify admin status.")
        return

    text = update.message.text
    cmd, arg = parse_command(text)
    
    if not arg:
        await update.message.reply_text("⚠️ Usage: `Speed@10s` or `Speed@1m`")
        return
        
    try:
        if arg.endswith('s'):
            speed = int(arg[:-1])
            if speed < 5 or speed > 60: raise ValueError
        elif arg.endswith('m'):
            speed = int(arg[:-1]) * 60
            if speed > 120: raise ValueError
        else:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid speed! Use between `5s` to `60s` or `1m` to `2m`.")
        return

    context.chat_data['group_speed'] = speed
    await update.message.reply_text(f"⚡ Speed set to *{speed} seconds* for this group! (Repeat time updated) ⏱️", parse_mode='Markdown')

async def score_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    score = get_user_score(user.id)
    await update.message.reply_text(f"🏆 *{user.first_name}*, your score is: *{score}* points! 🌟", parse_mode='Markdown')

async def score_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_users = get_top_10()
    if not top_users:
        await update.message.reply_text("📊 No scores yet! Start playing to get on the leaderboard. 🚀")
        return
    
    text = "🥇 *TOP 10 LEADERBOARD* 🥇\n\n"
    for idx, user in enumerate(top_users, 1):
        name = user.get('username', 'Unknown')
        score = user.get('score', 0)
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        text += f"{medal} *{name}* — {score} points\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def stats_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("❌ This command is only for the Bot Owner! 👑")
    count = get_total_users()
    await update.message.reply_text(f"👥 *Total Users:* {count}", parse_mode='Markdown')

async def stats_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("❌ This command is only for the Bot Owner! 👑")
    count = get_total_groups()
    await update.message.reply_text(f"👥 *Total Groups:* {count}", parse_mode='Markdown')

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("❌ This command is only for the Bot Owner! 👑")
    
    text = update.message.text
    cmd, arg = parse_command(text)
    
    if not arg:
        return await update.message.reply_text("⚠️ Usage: `Broadcast@Your message here`")

    groups = get_all_groups()
    users = get_all_users()
    
    success = 0
    failed = 0
    
    status_msg = await update.message.reply_text("📢 Broadcasting started...")
    
    for group in groups:
        try:
            await context.bot.send_message(group['chat_id'], f"📢 *Broadcast from Owner:*\n\n{arg}", parse_mode='Markdown')
            success += 1
        except Exception:
            failed += 1
            
    for user in users:
        try:
            await context.bot.send_message(user['user_id'], f"📢 *Broadcast from Owner:*\n\n{arg}", parse_mode='Markdown')
            success += 1
        except Exception:
            failed += 1
            
    await status_msg.edit_text(f"✅ *Broadcast Complete!*\n\nSent: {success}\nFailed: {failed}", parse_mode='Markdown')

async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.chat_data.get('quiz_active'):
        return
    
    data = context.chat_data.get('quiz_data')
    if not data or data.get('answer_guessed'):
        return
    
    user_answer = update.message.text.lower().strip()
    category = data['category']
    q_index = data['q_index']
    
    questions = get_questions(category)
    if q_index >= len(questions): return
    
    correct_answer = questions[q_index]['a'].lower()
    
    if user_answer == correct_answer:
        user = update.effective_user
        add_score(user.id, user.first_name, 1)
        data['answer_guessed'] = True
        context.chat_data['quiz_data'] = data
        
        await update.message.reply_text(f"✅ *Correct, {user.first_name}!* 🎉 +1 Point!\nAnswer: *{correct_answer.title()}*", parse_mode='Markdown')
        
        # Cancel pending repeat jobs
        for job in context.job_queue.get_jobs_by_name(f"quiz_{update.effective_chat.id}"):
            job.schedule_removal()
            
        data['q_index'] += 1
        data['repeat'] = 0
        data['answer_guessed'] = False
        
        # Move to next question in 2 seconds
        context.job_queue.run_once(send_question, 2, chat_id=update.effective_chat.id, data=data, name=f"quiz_{update.effective_chat.id}")

# --- MAIN BOT SETUP ---
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", private_welcome, filters=filters.ChatType.PRIVATE))
    
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Quiz@(on|off)$'), quiz_command))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Speed@'), speed_command))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@me$'), score_me))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@all$'), score_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Stats@users$'), stats_users))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Stats@groups$'), stats_groups))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Broadcast@'), broadcast))
    
    application.add_handler(CallbackQueryHandler(category_selected, pattern=r'^cat_'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer_handler))
    application.add_handler(MessageHandler(filters.ALL, lambda u, c: save_group(u.effective_chat.id, u.effective_chat.title) if u.effective_chat.type in ['group', 'supergroup'] else None))

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
