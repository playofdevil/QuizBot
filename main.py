import os
import random
import logging
import threading
import asyncio
import time
import datetime
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

from questions import EASY_QUESTIONS, MEDIUM_QUESTIONS, HARD_QUESTIONS
from database import (
    save_user, save_group, add_score, get_user_profile, get_top_10, get_top_100,
    get_total_users, get_total_groups, get_all_groups, get_all_users,
    add_permission, remove_permission, check_permission,
    ban_user, unban_user, is_banned, mute_user, unmute_user, is_muted,
    reset_group_scores, reset_all_scores, set_global_state, get_global_state,
    add_custom_question, del_custom_question, get_custom_questions,
    set_force_join, remove_force_join, get_force_join, get_group_stats,
    get_top_10_fastest, get_top_20_fastest, get_user_rank,
    set_custom_welcome, get_custom_welcome, remove_custom_welcome,
    get_group_settings, update_group_setting, delete_group_score_by_id,
    delete_user_score_by_rank, delete_fast_by_rank, get_user_by_username,
    set_welcome_gif, get_welcome_gif
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Two Owners Support
_owner_raw = os.environ.get("OWNER_IDS") or os.environ.get("OWNER_ID") or ""
OWNER_IDS = []
for _x in _owner_raw.split(","):
    _x = _x.strip()
    if _x.isdigit():
        OWNER_IDS.append(int(_x))
OWNER_ID = OWNER_IDS[0] if OWNER_IDS else 0
VIP_GROUP_ID = os.environ.get("VIP_GROUP_ID", "0")

# GIF Settings
WELCOME_GIF_DEFAULT = os.environ.get("WELCOME_GIF_URL", "https://raw.githubusercontent.com/playofdevil/QuizBot/main/welcome.gif")

def get_gif_url():
    custom = get_welcome_gif()
    if custom:
        return custom
    hourly = int(time.time() / 3600)
    return f"{WELCOME_GIF_DEFAULT}?v={hourly}"

# Dummy Web Server for Render
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, *args):
        pass

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# --- Helpers ---
def is_owner(user_id):
    return user_id in OWNER_IDS

def parse_command(text):
    if not text:
        return "", None
    if "@" in text:
        cmd, arg = text.split("@", 1)
        return cmd.lower().strip(), arg.strip()
    return text.lower().strip(), None

def normalize_answer(s):
    if not s:
        return ""
    return " ".join(s.lower().strip().split())

def get_questions(category):
    base_q = []
    if category == "easy":
        base_q = EASY_QUESTIONS.copy()
    elif category == "medium":
        base_q = MEDIUM_QUESTIONS.copy()
    elif category == "hard":
        base_q = HARD_QUESTIONS.copy()

    for c in get_custom_questions(category):
        base_q.append({"q": c['question'], "a": c['answer'], "c": "Custom"})

    seen = set()
    unique = []
    for q in base_q:
        key = normalize_answer(q['a'])
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    random.shuffle(unique)
    return unique

def get_target_user(update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    text = update.message.text or update.message.caption or ""
    if "@" in text:
        parts = text.split("@", 1)
        if len(parts) > 1:
            rest = parts[1].strip()
            if rest:
                return rest.split()[0]
    return None

def resolve_target(update):
    target = get_target_user(update)
    if not target:
        return None
    if isinstance(target, str):
        uname = target.lstrip("@").lower()
        doc = get_user_by_username(uname)
        if doc:
            return SimpleNamespace(id=doc['user_id'], first_name=doc.get('first_name', uname), username=doc.get('username', uname))
        return None
    return target

async def is_admin_or_permitted(update, context):
    if not update.effective_chat or update.effective_chat.type not in ['group', 'supergroup']:
        return False
    uid = update.effective_user.id
    cid = update.effective_chat.id
    if is_owner(uid):
        return True
    try:
        member = await update.effective_chat.get_member(uid)
        if member.status in ['administrator', 'creator']:
            return True
    except Exception:
        pass
    return check_permission(uid, cid)

async def check_force_join(update, context):
    channel = get_force_join()
    if not channel:
        return True
    try:
        member = await context.bot.get_chat_member(channel, update.effective_user.id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        await update.message.reply_text(f"⚠️ <b>You must join our channel to play!</b>\n\n👉 Join here: {channel}", parse_mode='HTML')
        return False
    except Exception:
        return True

active_quizzes = {}
_saved_groups = {}
CACHE_TTL = 3600

# --- Quiz Loop ---
async def quiz_loop(chat_id, context, data):
    try:
        await asyncio.sleep(1)
        settings = get_group_settings(chat_id)
        limit = settings.get('limit', 0)

        while data['active'] and data['q_index'] < len(data['questions']):
            if limit > 0 and data['q_index'] >= limit:
                await context.bot.send_message(chat_id, f"🎉 <b>Quiz Completed!</b> Limit of {limit} questions reached.", parse_mode='HTML')
                break

            question = data['questions'][data['q_index']]
            data['answered'] = False
            data['current_answer'] = normalize_answer(question['a'])
            data['start_time'] = time.time()
            subject = question.get('c', 'General')

            for attempt in range(3):
                if data['answered'] or not data['active']:
                    break

                points = 100 if attempt == 0 else 50 if attempt == 1 else 20

                msg_text = (
                    f"      ❖  <b>𝐐ᴜɪᴢ 𝐐ᴜᴇꜱᴛɪᴏɴ</b>  ❖\n\n"
                    f"── ⊱ ✦ 𝐃ᴇᴛᴀɪʟꜱ ✦ ⊰ ──\n\n"
                    f"📚  <b>𝐒ᴜʙᴊᴇᴄᴛ :</b> {subject}\n\n"
                    f"── ⊱ ✦ 𝐐ᴜᴇꜱᴛɪᴏɴ ✦ ⊰ ──\n\n"
                    f"➣  <b>{question['q']}</b>\n\n"
                    f"── ⊱ ✦ 𝐈ɴꜰᴏ ✦ ⊰ ──\n\n"
                    f"⏱  <b>𝐓ɪᴍᴇ :</b> {data['speed']}s\n"
                    f"💰  <b>𝐑ᴇᴡᴀʀᴅ :</b> {points} Pts\n"
                    f"🔄  <b>𝐑ᴏᴜɴᴅ :</b> {attempt+1}/3"
                )

                if settings.get('auto_delete') and context.chat_data.get('last_msg_id'):
                    try: await context.bot.delete_message(chat_id, context.chat_data['last_msg_id'])
                    except Exception: pass

                keyboard = [
                    [InlineKeyboardButton("⏭ Skip", callback_data="skip_quiz"), InlineKeyboardButton("💡 Hint", callback_data="hint_quiz"), InlineKeyboardButton("⏹ Stop", callback_data="stop_quiz")],
                    [InlineKeyboardButton("🏆 Score@all", callback_data="sc_all"), InlineKeyboardButton("⚡ Top@fast", callback_data="tf_all")]
                ]

                msg = await context.bot.send_message(chat_id, msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                context.chat_data['last_msg_id'] = msg.message_id

                for _ in range(data['speed']):
                    if data['answered'] or not data['active']: break
                    await asyncio.sleep(1)

            if not data['answered'] and data['active']:
                data['answered'] = True
                time_up_text = (
                    f"      ⏳  <b>𝐓ɪᴍᴇ'ꜱ 𝐔ᴘ!</b>\n\n"
                    f"── ⊱ ✦ 𝐑ᴇꜱᴜʟᴛ ✦ ⊰ ──\n\n"
                    f"❌  <b>𝐍ᴏʙᴏᴅʏ ɢᴏᴛ ɪᴛ ʀɪɢʜᴛ.</b>\n"
                    f"✅  <b>𝐀ɴꜱᴡᴇʀ ᴡᴀꜱ :</b> {question['a'].title()}\n\n"
                    f"➣  <b>𝐍ᴇxᴛ 𝐐ᴜᴇꜱᴛɪᴏɴ...</b>\n"
                    f"✦ ━━━━━━━━━━━━━ ✦"
                )
                if settings.get('auto_delete') and context.chat_data.get('last_msg_id'):
                    try: await context.bot.delete_message(chat_id, context.chat_data['last_msg_id'])
                    except Exception: pass

                msg = await context.bot.send_message(chat_id, time_up_text, parse_mode='HTML')
                context.chat_data['last_msg_id'] = msg.message_id
                await asyncio.sleep(2)

            data['q_index'] += 1
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"QUIZ LOOP CRASHED: {e}")
    finally:
        if chat_id in active_quizzes:
            del active_quizzes[chat_id]

# --- Welcome Menu Logic ---
async def send_welcome_menu(user_id, context, name="there"):
    text = (
        f"   ✦ ━━━━━━━━━━━━━ ✦\n"
        f"        <b>𝐖𝐄𝐋𝐂𝐎𝐌𝐄</b>\n"
        f"   ✦ ━━━━━━━━━━━━━ ✦\n\n"
        f"➣  <b>𝐇ᴇʏ, {name} 👋</b>\n"
        f"➣  <b>𝐑ᴇᴀᴅʏ 𝐓ᴏ 𝐓ᴇꜱᴛ 𝐘ᴏᴜʀ 𝐒ᴋɪʟʟꜱ?</b>\n\n"
        f"── ⊱ ✦ 𝐅ᴇᴀᴛᴜʀᴇꜱ ✦ ⊰ ──\n\n"
        f"◈  𝐋ɪᴠᴇ 𝐆ʀᴏᴜᴘ 𝐐ᴜɪᴢᴢᴇꜱ\n"
        f"◈  𝐆ʟᴏʙᴀʟ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅꜱ\n"
        f"◈  𝟏𝟎𝟎𝟎+ 𝐌ɪxᴇᴅ 𝐐ᴜᴇꜱᴛɪᴏɴꜱ\n"
        f"◈  𝐒𝐩ᴇᴇᴅ-𝐁ᴀ𝐬ᴇᴅ 𝐒ᴄᴏʀɪɴɢ\n\n"
        f"   ✦ ━━━━━━━━━━━━━ ✦\n"
        f"➣  <b>𝐂ʟɪᴄᴋ 𝐁ᴇʟᴏᴡ 𝐓ᴏ 𝐄xᴘʟᴏʀᴇ 👇</b>"
    )
    keyboard = [
        [InlineKeyboardButton("📖 Complete Guide", callback_data="wmenu_guide"), InlineKeyboardButton("🎯 How to Play", callback_data="wmenu_quiz")],
        [InlineKeyboardButton("🏆 Point System", callback_data="wmenu_points"), InlineKeyboardButton("❓ FAQ", callback_data="wmenu_faq")],
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]
    ]
    try:
        await context.bot.send_animation(chat_id=user_id, animation=get_gif_url(), caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        return True
    except Exception:
        try:
            await context.bot.send_message(chat_id=user_id, text=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
            return True
        except Exception:
            return False

async def start_cmd(update, context):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)
    await send_welcome_menu(user.id, context, name=user.first_name)

async def help_cmd(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        await send_welcome_menu(update.callback_query.from_user.id, context, name=update.callback_query.from_user.first_name)
        return

    if update.effective_chat.type in ['group', 'supergroup']:
        ok = await send_welcome_menu(update.effective_user.id, context, name=update.effective_user.first_name)
        if ok:
            try:
                await update.message.reply_text(f"📬 <b>Guide sent to your DM!</b>\n👉 Check @{context.bot.username}", parse_mode='HTML')
            except Exception: pass
        else:
            await update.message.reply_text(f"⚠️ <b>Please start me in DM first!</b>\n\n👉 Open @{context.bot.username} and press <b>Start</b>\nThen come back and type <code>help@</code>", parse_mode='HTML')
    else:
        await send_welcome_menu(update.effective_user.id, context, name=update.effective_user.first_name)

async def _edit_welcome(query, text, keyboard):
    try:
        if query.message.animation:
            await query.message.edit_caption(caption=text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        try: await query.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception: pass

async def wmenu_back(update, context):
    query = update.callback_query
    await query.answer()
    name = query.from_user.first_name
    text = (
        f"   ✦ ━━━━━━━━━━━━━ ✦\n"
        f"        <b>𝐖𝐄𝐋𝐂𝐎𝐌𝐄</b>\n"
        f"   ✦ ━━━━━━━━━━━━━ ✦\n\n"
        f"➣  <b>𝐇ᴇʏ, {name} 👋</b>\n"
        f"➣  <b>𝐑ᴇᴀᴅʏ 𝐓ᴏ 𝐓ᴇꜱᴛ 𝐘ᴏᴜʀ 𝐒ᴋɪʟʟꜱ?</b>\n\n"
        f"── ⊱ ✦ 𝐅ᴇᴀᴛᴜʀᴇꜱ ✦ ⊰ ──\n\n"
        f"◈  𝐋ɪᴠᴇ 𝐆ʀᴏᴜᴘ 𝐐ᴜɪᴢᴢᴇꜱ\n"
        f"◈  𝐆ʟᴏʙᴀʟ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅꜱ\n"
        f"◈  𝟏𝟎𝟎𝟎+ 𝐌ɪxᴇᴅ 𝐐ᴜᴇꜱᴛɪᴏɴꜱ\n"
        f"◈  𝐒𝐩ᴇᴇᴅ-𝐁ᴀ𝐬ᴇᴅ 𝐒ᴄᴏʀɪɴɢ\n\n"
        f"   ✦ ━━━━━━━━━━━━━ ✦\n"
        f"➣  <b>𝐂ʟɪᴄᴋ 𝐁ᴇʟᴏᴡ 𝐓ᴏ 𝐄xᴘʟᴏʀᴇ 👇</b>"
    )
    keyboard = [
        [InlineKeyboardButton("📖 Complete Guide", callback_data="wmenu_guide"), InlineKeyboardButton("🎯 How to Play", callback_data="wmenu_quiz")],
        [InlineKeyboardButton("🏆 Point System", callback_data="wmenu_points"), InlineKeyboardButton("❓ FAQ", callback_data="wmenu_faq")],
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]
    ]
    await _edit_welcome(query, text, keyboard)

async def wmenu_guide(update, context):
    query = update.callback_query
    await query.answer()
    text = "📖 <b>Complete Command Guide</b>\n\nChoose a category to see the commands:"
    keyboard = [
        [InlineKeyboardButton("👤 User Commands", callback_data="wmenu_user")],
        [InlineKeyboardButton("🛡️ Admin Commands", callback_data="wmenu_admin")],
        [InlineKeyboardButton("🔙 Back to Home", callback_data="wmenu_back")]
    ]
    await _edit_welcome(query, text, keyboard)

async def wmenu_user(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        "👤 <b>USER COMMANDS</b>\n"
        "────────────────\n\n"
        "<b>📊 Score@all</b> → Global Top 10\n"
        "<b>📊 Score@group</b> → Group Top 10\n"
        "<b>🏅 Rank@me</b> → Your global rank\n"
        "<b>⚡ top@fast</b> → Top 10 fastest users\n"
        "<b>📈 GroupStats@</b> → Group players & points\n"
        "<b>📖 help@</b> → Opens this guide\n"
        "<b>🚨 Report@</b> → Report a bad question\n\n"
        "💡 <b>Tip:</b> All commands use <code>@</code> symbol!"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="wmenu_guide")]]
    await _edit_welcome(query, text, keyboard)

async def wmenu_admin(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        "🛡️ <b>GROUP ADMIN COMMANDS</b>\n"
        "────────────────\n\n"
        "<b>🎮 Quiz@on / off</b> → Start/Stop\n"
        "<b>⏱️ Speed@10s</b> → Set timer\n"
        "<b>⏭️ Skip@ / 💡 Hint@</b> → Skip/Hint\n"
        "<b>✅ Yes@on / ❌ No@off</b> → Grant/Remove permission\n"
        "<b>👋 SetWelcome@</b> → Custom welcome\n"
        "<b>📅 ScheduleQuiz@18:00</b> → Auto-start\n"
        "<b>🔢 SetLimit@20</b> → Max questions\n"
        "<b>🔄 AutoDelete@on</b> → Delete old msgs\n\n"
        "💡 <i>Only Group Admins can use these!</i>"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="wmenu_guide")]]
    await _edit_welcome(query, text, keyboard)

async def wmenu_quiz(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        "🎯 <b>HOW TO PLAY</b>\n"
        "────────────────\n\n"
        "1️⃣ Admin types <code>Quiz@on</code>\n"
        "2️⃣ Choose category (Easy/Medium/Hard)\n"
        "3️⃣ Bot posts question with timer\n"
        "4️⃣ Type answer in group chat\n"
        "5️⃣ First correct answer wins!\n"
        "6️⃣ Next question comes after 1s\n\n"
        "<b>⚡ Tips:</b>\n"
        "• Faster = more points\n"
        "• Each question has 3 rounds\n"
        "• Admin can stop with <code>Quiz@off</code>"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back to Home", callback_data="wmenu_back")]]
    await _edit_welcome(query, text, keyboard)

async def wmenu_points(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        "🏆 <b>POINT SYSTEM</b>\n"
        "────────────────\n\n"
        "⏱️ <b>Within 10 sec</b> → 100 pts\n"
        "⏱️ <b>Within 20 sec</b> → 50 pts\n"
        "⏱️ <b>After 20 sec</b> → 20 pts\n\n"
        "────────────────\n"
        "💡 <b>Faster answers = more points!</b>"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back to Home", callback_data="wmenu_back")]]
    await _edit_welcome(query, text, keyboard)

async def wmenu_faq(update, context):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ <b>FREQUENTLY ASKED QUESTIONS</b>\n"
        "────────────────\n\n"
        "<b>Q: How to start a quiz?</b>\n"
        "A: Ask an admin to type <code>Quiz@on</code>\n\n"
        "<b>Q: Bot not responding?</b>\n"
        "A: Check the <code>@</code> symbol — <code>Quiz@on</code>, not <code>Quiz on</code>\n\n"
        "<b>Q: Score not adding?</b>\n"
        "A: Join the force-join channel if required\n\n"
        "<b>Q: How to report a question?</b>\n"
        "A: Type <code>Report@</code> during active quiz"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back to Home", callback_data="wmenu_back")]]
    await _edit_welcome(query, text, keyboard)

# --- Quiz Commands ---
async def quiz_cmd(update, context):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    if not await check_force_join(update, context): return
    if not get_global_state(): return await update.message.reply_text("⚠️ Bot is in maintenance.")
    if not await is_admin_or_permitted(update, context): return await update.message.reply_text("❌ Only Admins can start/stop the quiz.")

    cmd, arg = parse_command(update.message.text)
    chat_id = update.effective_chat.id
    arg_low = arg.lower() if arg else ""

    if arg_low == "on":
        if chat_id in active_quizzes: return await update.message.reply_text("⚠️ Quiz is already running!")
        keyboard = [[InlineKeyboardButton("🟢 Easy", callback_data="cat_easy")], [InlineKeyboardButton("🟡 Medium", callback_data="cat_medium")], [InlineKeyboardButton("🔴 Hard", callback_data="cat_hard")]]
        await update.message.reply_text("🎮 <b>Choose a Category to Start:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    elif arg_low == "off":
        if chat_id in active_quizzes:
            active_quizzes[chat_id]['active'] = False
            del active_quizzes[chat_id]
            await update.message.reply_text("⏸️ <b>Quiz Stopped!</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("⚠️ No quiz is running.")

async def category_callback(update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    if chat_id in active_quizzes: return await query.edit_message_text("⚠️ Quiz already running!")

    category = query.data.split("_", 1)[1]
    speed = get_group_settings(chat_id).get('speed', 15) or 15
    questions = get_questions(category)

    if not questions: return await query.edit_message_text("⚠️ No questions available in this category.")

    data = {'active': True, 'category': category, 'speed': speed, 'questions': questions, 'q_index': 0, 'answered': False, 'current_answer': None, 'start_time': 0}
    active_quizzes[chat_id] = data
    await query.edit_message_text(f"✅ Category <b>{category.title()}</b> selected!\n🚀 <b>Quiz Started!</b> Speed: {speed}s", parse_mode='HTML')
    asyncio.create_task(quiz_loop(chat_id, context, data))

async def button_handler(update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    action = query.data

    if action == "sc_all":
        top = get_top_10("global")
        if not top: return await context.bot.send_message(chat_id, "📊 No scores yet!")
        text = "🥇 <b>GLOBAL TOP 10</b> 🥇\n\n"
        for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b> — {u.get('score', 0)} pts\n"
        return await context.bot.send_message(chat_id, text, parse_mode='HTML')

    if action == "tf_all":
        top = get_top_10_fastest()
        if not top: return await context.bot.send_message(chat_id, "📊 No data yet!")
        text = "⚡ <b>FASTEST USERS</b> ⚡\n\n"
        for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b> — {u.get('fastest_time', 0)}s\n"
        return await context.bot.send_message(chat_id, text, parse_mode='HTML')

    if action in ["skip_quiz", "hint_quiz", "stop_quiz"]:
        if not await is_admin_or_permitted(update, context): return await query.answer("❌ Only Admins can use this.", show_alert=True)
        if chat_id not in active_quizzes: return await query.answer("⚠️ No quiz running.", show_alert=True)
        data = active_quizzes[chat_id]

        if action == "stop_quiz":
            data['active'] = False
            if chat_id in active_quizzes: del active_quizzes[chat_id]
            await context.bot.send_message(chat_id, "⏹️ <b>Quiz Stopped by Admin!</b>", parse_mode='HTML')
        elif action == "skip_quiz":
            if data['current_answer']: await context.bot.send_message(chat_id, f"⏭️ <b>Question Skipped!</b>\nCorrect answer: <b>{data['current_answer'].title()}</b>", parse_mode='HTML')
            data['answered'] = True
        elif action == "hint_quiz":
            if data['current_answer']:
                ans = data['current_answer']
                await context.bot.send_message(chat_id, f"💡 <b>Hint:</b> Starts with <code>{ans[0]}</code> · {len(ans)} letters.", parse_mode='HTML')

async def answer_handler(update, context):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    chat_id = update.effective_chat.id
    if chat_id not in active_quizzes: return

    data = active_quizzes[chat_id]
    user = update.effective_user

    if not data['active'] or data['answered'] or not data['current_answer']: return
    if is_banned(user.id) or is_muted(user.id, chat_id): return
    if not await check_force_join(update, context): return

    user_ans = normalize_answer(update.message.text)
    if user_ans == data['current_answer']:
        data['answered'] = True
        time_taken = round(time.time() - data['start_time'], 1)

        if str(chat_id) == VIP_GROUP_ID:
            if time_taken <= 5: points = 5240
            elif time_taken <= 10: points = 3430
            else: points = 1050
        else:
            if time_taken <= 10: points = 100
            elif time_taken <= 20: points = 50
            else: points = 20

        add_score(user.id, chat_id, user.first_name, points, time_taken)

        success_text = (
            f"      ✅  <b>𝐂ᴏʀʀᴇᴄᴛ!</b>\n\n"
            f"── ⊱ ✦ 𝐑ᴇꜱᴜʟᴛ ✦ ⊰ ──\n\n"
            f"👤  <b>𝐔ꜱᴇʀ :</b> {user.first_name}\n"
            f"🎯  <b>𝐀ɴꜱᴡᴇʀ :</b> {data['current_answer'].title()}\n"
            f"💰  <b>𝐏ᴏɪɴᴛꜱ :</b> +{points}\n"
            f"⏱  <b>𝐓ɪᴍᴇ :</b> {time_taken}s\n\n"
            f"✦ ━━━━━━━━━━━━━ ✦"
        )
        await update.message.reply_text(success_text, parse_mode='HTML')

# --- Admin Commands ---
async def schedule_quiz_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>ScheduleQuiz@18:00</code> or <code>ScheduleQuiz@off</code>", parse_mode='HTML')

    if arg.lower() == "off":
        for job in context.job_queue.get_jobs_by_name(f"schedule_{chat_id}"): job.schedule_removal()
        update_group_setting(chat_id, "schedule", None)
        return await update.message.reply_text("✅ Schedule disabled.", parse_mode='HTML')

    try:
        time_obj = datetime.datetime.strptime(arg, "%H:%M").time()
        update_group_setting(chat_id, "schedule", arg)
        for job in context.job_queue.get_jobs_by_name(f"schedule_{chat_id}"): job.schedule_removal()

        async def scheduled_start(ctx):
            chat = ctx.job.data['chat_id']
            speed = get_group_settings(chat).get('speed', 15) or 15
            questions = get_questions("easy")
            if not questions: return
            data = {'active': True, 'category': "easy", 'speed': speed, 'questions': questions, 'q_index': 0, 'answered': False, 'current_answer': None, 'start_time': 0}
            active_quizzes[chat] = data
            await ctx.bot.send_message(chat, "⏰ <b>Scheduled Quiz Started!</b>\nCategory: Easy", parse_mode='HTML')
            asyncio.create_task(quiz_loop(chat, ctx, data))

        context.job_queue.run_daily(scheduled_start, time=time_obj, chat_id=chat_id, name=f"schedule_{chat_id}", data={'chat_id': chat_id})
        await update.message.reply_text(f"✅ Daily quiz scheduled at <b>{arg}</b>.", parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Invalid time format. Use HH:MM (e.g., 18:00).", parse_mode='HTML')

async def set_limit_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>SetLimit@20</code> or <code>SetLimit@off</code>", parse_mode='HTML')

    if arg.lower() == "off":
        update_group_setting(chat_id, "limit", 0)
        return await update.message.reply_text("✅ Limit removed.", parse_mode='HTML')
    try:
        limit = int(arg)
        update_group_setting(chat_id, "limit", limit)
        await update.message.reply_text(f"✅ Limit set to <b>{limit}</b> questions.", parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Invalid number.", parse_mode='HTML')

async def auto_delete_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    cmd, arg = parse_command(update.message.text)
    if arg and arg.lower() == "on":
        update_group_setting(chat_id, "auto_delete", True)
        await update.message.reply_text("✅ Auto-delete enabled.", parse_mode='HTML')
    elif arg and arg.lower() == "off":
        update_group_setting(chat_id, "auto_delete", False)
        await update.message.reply_text("✅ Auto-delete disabled.", parse_mode='HTML')
    else:
        await update.message.reply_text("⚠️ Usage: <code>AutoDelete@on</code> or <code>AutoDelete@off</code>", parse_mode='HTML')

async def speed_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>Speed@10s</code> or <code>Speed@1m</code>", parse_mode='HTML')
    try:
        if arg.endswith('m'): speed = int(arg[:-1]) * 60
        elif arg.endswith('s'): speed = int(arg[:-1])
        else: speed = int(arg)
        if speed < 5 or speed > 120: raise ValueError
    except (ValueError, IndexError):
        return await update.message.reply_text("❌ Invalid. Use <code>Speed@10s</code> (5s-120s)", parse_mode='HTML')

    update_group_setting(update.effective_chat.id, "speed", speed)
    await update.message.reply_text(f"⚡ Speed set to <b>{speed} seconds</b>.", parse_mode='HTML')

async def skip_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes:
        data = active_quizzes[chat_id]
        if data['current_answer']: await update.message.reply_text(f"⏭️ <b>Question Skipped!</b>\nCorrect answer: <b>{data['current_answer'].title()}</b>", parse_mode='HTML')
        data['answered'] = True
    else: await update.message.reply_text("⚠️ No quiz running.")

async def hint_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes and active_quizzes[chat_id]['current_answer']:
        ans = active_quizzes[chat_id]['current_answer']
        await update.message.reply_text(f"💡 <b>Hint:</b> Starts with <code>{ans[0]}</code> · {len(ans)} letters.", parse_mode='HTML')
    else: await update.message.reply_text("⚠️ No active question.")

async def yes_perm(update, context):
    if not await is_admin_or_permitted(update, context): return
    target = resolve_target(update)
    if target:
        add_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"✅ Permission granted to {target.first_name}.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def no_perm(update, context):
    if not await is_admin_or_permitted(update, context): return
    target = resolve_target(update)
    if target:
        remove_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"❌ Permission removed for {target.first_name}.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def group_stats(update, context):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    stats = get_group_stats(update.effective_chat.id)
    await update.message.reply_text(f"📊 <b>GROUP STATS</b>\n\n👥 Total Players: {stats['players']}\n🏆 Total Points: {stats['points']}", parse_mode='HTML')

async def score_all(update, context):
    top = get_top_10("global")
    if not top: return await update.message.reply_text("📊 No scores yet!")
    text = "🥇 <b>GLOBAL TOP 10</b> 🥇\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b> — {u.get('score', 0)} pts\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def score_group(update, context):
    top = get_top_10(update.effective_chat.id)
    if not top: return await update.message.reply_text("📊 No scores in this group!")
    text = "🥇 <b>GROUP TOP 10</b> 🥇\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b> — {u.get('score', 0)} pts\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def top_fast(update, context):
    top = get_top_10_fastest()
    if not top: return await update.message.reply_text("📊 No data yet!")
    text = "⚡ <b>FASTEST USERS</b> ⚡\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b> — {u.get('fastest_time', 0)}s\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def rank_me(update, context):
    user = update.effective_user
    rank = get_user_rank(user.id)
    if rank == 0: return await update.message.reply_text("⚠️ You haven't played yet!")
    await update.message.reply_text(f"🏅 <b>{user.first_name}</b>, Global Rank: <b>#{rank}</b>", parse_mode='HTML')

async def report_cmd(update, context):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    chat_id = update.effective_chat.id
    user = update.effective_user

    if update.message.reply_to_message:
        replied = update.message.reply_to_message
        replied_text = replied.text or replied.caption or "[media]"
        report_text = f"🚨 <b>MESSAGE REPORT</b>\n\n📚 <b>Group:</b> {update.effective_chat.title}\n🆔 <b>Group ID:</b> <code>{chat_id}</code>\n👤 <b>By:</b> {user.first_name} (<code>{user.id}</code>)\n💬 <b>Message:</b>\n{replied_text}"
    elif chat_id in active_quizzes and active_quizzes[chat_id]['q_index'] < len(active_quizzes[chat_id]['questions']):
        data = active_quizzes[chat_id]
        question = data['questions'][data['q_index']]
        report_text = f"🚨 <b>QUESTION REPORT</b>\n\n📚 <b>Group:</b> {update.effective_chat.title}\n🆔 <b>Group ID:</b> <code>{chat_id}</code>\n👤 <b>By:</b> {user.first_name} (<code>{user.id}</code>)\n📂 <b>Category:</b> {data.get('category', 'N/A')}\n❓ <b>Q:</b> {question['q']}\n✅ <b>A:</b> {question['a']}\n🕐 {datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    else:
        return await update.message.reply_text("⚠️ No active question to report.\nReply to a message or start a quiz first.", parse_mode='HTML')

    sent = 0
    for oid in OWNER_IDS:
        try:
            await context.bot.send_message(oid, report_text, parse_mode='HTML')
            sent += 1
        except Exception as e:
            logger.warning(f"Report to owner {oid} failed: {e}")

    if sent > 0: await update.message.reply_text("✅ Report sent to owner(s)! Thank you.", parse_mode='HTML')
    else: await update.message.reply_text("❌ Failed to send report.", parse_mode='HTML')

async def set_welcome_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if update.message.reply_to_message:
        msg = update.message.reply_to_message
        text = msg.caption or msg.text or ""
        media_id, media_type = None, None
        if msg.photo: media_id, media_type = msg.photo[-1].file_id, "photo"
        elif msg.animation: media_id, media_type = msg.animation.file_id, "animation"
        elif msg.video: media_id, media_type = msg.video.file_id, "video"
        set_custom_welcome(chat_id, text, media_id, media_type)
        await update.message.reply_text("✅ Custom Welcome set (with media)!")
    else:
        raw = update.message.text or update.message.caption or ""
        cmd, arg = parse_command(raw)
        if not arg: return await update.message.reply_text("⚠️ Usage: <code>SetWelcome@Your text</code> or reply to media.", parse_mode='HTML')
        set_custom_welcome(chat_id, arg, None, None)
        await update.message.reply_text("✅ Custom Welcome text set!")

async def remove_welcome_cmd(update, context):
    if not await is_admin_or_permitted(update, context): return
    remove_custom_welcome(update.effective_chat.id)
    await update.message.reply_text("✅ Custom Welcome removed.")

async def welcome_new_member(update, context):
    for member in update.message.new_chat_members:
        if member.is_bot: continue
        chat_id = update.effective_chat.id
        custom = get_custom_welcome(chat_id)
        if custom:
            text = custom.get('text', '') or f"Welcome {member.first_name}!"
            now = datetime.datetime.now()
            text = text.replace("{name}", member.first_name)
            text = text.replace("{username}", f"@{member.username}" if member.username else member.first_name)
            text = text.replace("{group}", update.effective_chat.title or "")
            text = text.replace("{date}", now.strftime("%d %b %Y"))
            text = text.replace("{time}", now.strftime("%I:%M %p"))
            text = text.replace("{id}", str(member.id))
            media_id, media_type = custom.get('media_id'), custom.get('media_type')
            try:
                if media_type == "photo": await context.bot.send_photo(chat_id, media_id, caption=text, parse_mode='HTML')
                elif media_type == "animation": await context.bot.send_animation(chat_id, media_id, caption=text, parse_mode='HTML')
                elif media_type == "video": await context.bot.send_video(chat_id, media_id, caption=text, parse_mode='HTML')
                else: await context.bot.send_message(chat_id, text, parse_mode='HTML')
            except Exception:
                await context.bot.send_message(chat_id, text, parse_mode='HTML')

# --- Owner Commands ---
async def top10_details(update, context):
    if not is_owner(update.effective_user.id): return
    top = get_top_10("global")
    if not top: return await update.message.reply_text("📊 No users yet.")
    text = "👑 <b>TOP 10 USERS</b>\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u.get('username', 'Unknown')}</b>\n   🆔 <code>{u.get('user_id')}</code>\n   🏆 {u.get('score', 0)} pts\n\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def user_info(update, context):
    if not is_owner(update.effective_user.id): return
    target = resolve_target(update)
    if not target: return await update.message.reply_text("⚠️ Reply or tag a user.")
    prof = get_user_profile(target.id, "global") or {}
    await update.message.reply_text(f"👤 <b>User Info</b>\nName: {target.first_name}\nID: <code>{target.id}</code>\nScore: {prof.get('score', 0)}", parse_mode='HTML')

async def force_join_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>ForceJoin@channel_link</code>", parse_mode='HTML')
    set_force_join(arg)
    await update.message.reply_text("✅ Force join channel set!")

async def remove_force_join_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    remove_force_join()
    await update.message.reply_text("✅ Force join removed!")

async def ban_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    target = resolve_target(update)
    if target:
        ban_user(target.id)
        await update.message.reply_text(f"🚫 {target.first_name} banned.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def unban_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    target = resolve_target(update)
    if target:
        unban_user(target.id)
        await update.message.reply_text(f"✅ {target.first_name} unbanned.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def mute_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    target = resolve_target(update)
    if target:
        mute_user(target.id, update.effective_chat.id)
        await update.message.reply_text(f"🔇 {target.first_name} muted.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def unmute_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    target = resolve_target(update)
    if target:
        unmute_user(target.id, update.effective_chat.id)
        await update.message.reply_text(f"🔊 {target.first_name} unmuted.")
    else: await update.message.reply_text("⚠️ Reply or tag a valid user.")

async def reset_group(update, context):
    if not is_owner(update.effective_user.id): return
    reset_group_scores(update.effective_chat.id)
    await update.message.reply_text("🧹 Group leaderboard reset!")

async def reset_all(update, context):
    if not is_owner(update.effective_user.id): return
    reset_all_scores()
    await update.message.reply_text("🧹 Global leaderboard reset!")

async def add_q(update, context):
    if not is_owner(update.effective_user.id): return
    raw = update.message.text or update.message.caption or ""
    cmd, arg = parse_command(raw)
    if not arg: return await update.message.reply_text("❌ Format: <code>AddQ@easy@Question@Answer</code>", parse_mode='HTML')
    try:
        main_part, answer = arg.rsplit("@", 1)
        category, question = main_part.split("@", 1)
        add_custom_question(category.strip().lower(), question.strip(), answer.strip().lower())
        await update.message.reply_text(f"✅ Added to <b>{category.strip()}</b>!", parse_mode='HTML')
    except ValueError:
        await update.message.reply_text("❌ Format: <code>AddQ@easy@Question@Answer</code>", parse_mode='HTML')

async def del_q(update, context):
    if not is_owner(update.effective_user.id): return
    raw = update.message.text or update.message.caption or ""
    cmd, arg = parse_command(raw)
    if not arg: return await update.message.reply_text("❌ Format: <code>DelQ@easy@Question</code>", parse_mode='HTML')
    try:
        category, question = arg.split("@", 1)
        if del_custom_question(category.strip().lower(), question.strip()): await update.message.reply_text("✅ Deleted.")
        else: await update.message.reply_text("❌ Not found.")
    except ValueError:
        await update.message.reply_text("❌ Format: <code>DelQ@easy@Question</code>", parse_mode='HTML')

async def stats_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    cmd, arg = parse_command(update.message.text)
    arg_low = arg.lower() if arg else ""
    if arg_low == "users": await update.message.reply_text(f"👥 Users: {get_total_users()}")
    elif arg_low == "groups": await update.message.reply_text(f"👥 Groups: {get_total_groups()}")
    else: await update.message.reply_text("⚠️ Usage: <code>Stats@users</code> or <code>Stats@groups</code>", parse_mode='HTML')

async def broadcast_all(update, context):
    if not is_owner(update.effective_user.id): return
    raw = update.message.text or update.message.caption or ""
    cmd, arg = parse_command(raw)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>BroadcastAll@msg</code>", parse_mode='HTML')
    groups = get_all_groups()
    users = get_all_users()
    await update.message.reply_text(f"📢 Broadcasting to {len(groups)} groups and {len(users)} users...")
    ok = 0
    for g in groups:
        cid = g.get('chat_id')
        if not cid: continue
        try:
            await context.bot.send_message(cid, f"📢 {arg}")
            ok += 1
            await asyncio.sleep(0.5)
        except Exception: pass
    for u in users:
        uid = u.get('user_id')
        if not uid: continue
        try:
            await context.bot.send_message(uid, f"📢 {arg}")
            ok += 1
            await asyncio.sleep(0.5)
        except Exception: pass
    await update.message.reply_text(f"✅ Broadcast sent to {ok} chats.")

async def list_groups(update, context):
    if not is_owner(update.effective_user.id): return
    groups = get_all_groups()
    if not groups: return await update.message.reply_text("No groups found.")
    text = "📋 <b>GROUPS</b>\n\n"
    for g in groups: text += f"• {g.get('title', 'Unknown')} : <code>{g.get('chat_id', '?')}</code>\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def owner_on(update, context):
    if not is_owner(update.effective_user.id): return
    set_global_state(True)
    await update.message.reply_text("🌍 Global ON.")

async def owner_off(update, context):
    if not is_owner(update.effective_user.id): return
    set_global_state(False)
    await update.message.reply_text("🌍 Global OFF.")

async def setgif_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    raw = update.message.text or ""
    if not raw.lower().startswith('.setgif@'): return
    url = raw[len('.setgif@'):].strip()
    if not url: return await update.message.reply_text("⚠️ Usage: <code>.setgif@&lt;url&gt;</code>", parse_mode='HTML')
    if not (url.startswith('http://') or url.startswith('https://')): return await update.message.reply_text("❌ URL must start with <code>http://</code> or <code>https://</code>", parse_mode='HTML')
    set_welcome_gif(url)
    await update.message.reply_text("✅ <b>Welcome GIF updated!</b>\nTest with <code>/start</code>", parse_mode='HTML')

async def removegif_cmd(update, context):
    if not is_owner(update.effective_user.id): return
    set_welcome_gif(None)
    await update.message.reply_text("✅ Welcome GIF reset to default.")

async def del_group_score(update, context):
    if not is_owner(update.effective_user.id): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>DelGroupScore@Group_ID</code>", parse_mode='HTML')
    if delete_group_score_by_id(arg): await update.message.reply_text(f"✅ Score deleted for group <code>{arg}</code>.", parse_mode='HTML')
    else: await update.message.reply_text("❌ Group not found or already empty.")

async def del_user_score(update, context):
    if not is_owner(update.effective_user.id): return
    top_100 = get_top_100("global")
    if not top_100: return await update.message.reply_text("📊 No users to delete.")
    text = "📋 <b>Top 100 Users:</b>\n\n"
    for idx, u in enumerate(top_100, 1): text += f"{idx}. {u.get('username', 'Unknown')} - {u.get('score', 0)} pts\n"
    text += "\n👉 Type: <code>delscore@number</code>"
    context.user_data['delete_mode'] = 'user_score'
    await update.message.reply_text(text, parse_mode='HTML')

async def del_top_fast(update, context):
    if not is_owner(update.effective_user.id): return
    top_20 = get_top_20_fastest()
    if not top_20: return await update.message.reply_text("📊 No users found.")
    text = "⚡ <b>Top Fastest Users:</b>\n\n"
    for idx, u in enumerate(top_20, 1): text += f"{idx}. {u.get('username', 'Unknown')} - {u.get('fastest_time', 0)}s\n"
    text += "\n👉 Type: <code>deltop@number</code>"
    context.user_data['delete_mode'] = 'fastest'
    await update.message.reply_text(text, parse_mode='HTML')

async def handle_delete_index(update, context):
    if not is_owner(update.effective_user.id): return
    text = (update.message.text or "").lower()
    if 'delete_mode' not in context.user_data: return
    mode = context.user_data['delete_mode']

    try:
        if mode == 'user_score' and text.startswith('delscore@'):
            rank = int(text.split('@', 1)[1])
            username = delete_user_score_by_rank(rank)
            if username: await update.message.reply_text(f"✅ Deleted scores for <b>{username}</b> (Rank {rank}).", parse_mode='HTML')
            else: await update.message.reply_text("❌ Invalid rank.")
            del context.user_data['delete_mode']
        elif mode == 'fastest' and text.startswith('deltop@'):
            rank = int(text.split('@', 1)[1])
            username = delete_fast_by_rank(rank)
            if username: await update.message.reply_text(f"✅ Removed <b>{username}</b> from Fastest (Rank {rank}).", parse_mode='HTML')
            else: await update.message.reply_text("❌ Invalid rank.")
            del context.user_data['delete_mode']
        elif text.startswith('delscore@') or text.startswith('deltop@'):
            if mode == 'user_score': await update.message.reply_text("⚠️ Use <code>delscore@number</code>", parse_mode='HTML')
            else: await update.message.reply_text("⚠️ Use <code>deltop@number</code>", parse_mode='HTML')
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Please enter a valid number.")

# --- Main Application ---
def main():
    if not BOT_TOKEN: raise ValueError("❌ BOT_TOKEN environment variable is not set!")
    if not OWNER_IDS: raise ValueError("❌ OWNER_IDS (or OWNER_ID) environment variable is not set!")
    
    logger.info(f"👑 Owners loaded: {OWNER_IDS}")
    threading.Thread(target=run_dummy_server, daemon=True).start()

    application = (Application.builder().token(BOT_TOKEN).get_updates_read_timeout(60).get_updates_connect_timeout(15).read_timeout(30).connect_timeout(10).build())

    # Start / Help / Welcome Callbacks
    application.add_handler(CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^help@'), help_cmd))
    application.add_handler(CallbackQueryHandler(wmenu_guide, pattern=r'^wmenu_guide$'))
    application.add_handler(CallbackQueryHandler(wmenu_user, pattern=r'^wmenu_user$'))
    application.add_handler(CallbackQueryHandler(wmenu_admin, pattern=r'^wmenu_admin$'))
    application.add_handler(CallbackQueryHandler(wmenu_quiz, pattern=r'^wmenu_quiz$'))
    application.add_handler(CallbackQueryHandler(wmenu_points, pattern=r'^wmenu_points$'))
    application.add_handler(CallbackQueryHandler(wmenu_faq, pattern=r'^wmenu_faq$'))
    application.add_handler(CallbackQueryHandler(wmenu_back, pattern=r'^wmenu_back$'))
    
    # Reports
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Report@'), report_cmd))

    # Quiz Core
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Quiz@'), quiz_cmd))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r'^cat_'))
    application.add_handler(CallbackQueryHandler(button_handler, pattern=r'^(skip_quiz|hint_quiz|stop_quiz|sc_all|tf_all)$'))

    # Group Config
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Speed@'), speed_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^ScheduleQuiz@'), schedule_quiz_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^SetLimit@'), set_limit_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^AutoDelete@'), auto_delete_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^GroupStats@'), group_stats))

    # User Stats
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@all'), score_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@group'), score_group))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^top@fast'), top_fast))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Rank@me'), rank_me))

    # Welcome
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^SetWelcome@'), set_welcome_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^RemoveWelcome@'), remove_welcome_cmd))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    # Perms / Skip / Hint
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Skip@'), skip_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Hint@'), hint_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Yes@on'), yes_perm))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^No@off'), no_perm))

    # Owner Management
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Top10Details@'), top10_details))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^UserInfo@'), user_info))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^ForceJoin@'), force_join_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^RemoveForceJoin@'), remove_force_join_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Ban@'), ban_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Unban@'), unban_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Mute@'), mute_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Unmute@'), unmute_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Reset@group'), reset_group))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Reset@all'), reset_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^AddQ@'), add_q))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^DelQ@'), del_q))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Stats@'), stats_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^BroadcastAll@'), broadcast_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^ListGroups@'), list_groups))

    # Owner Delete / Global / GIF
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^DelGroupScore@'), del_group_score))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^DelUserScore@'), del_user_score))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^DelTopFast@'), del_top_fast))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^(delscore@|deltop@)'), handle_delete_index))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^\.on$'), owner_on))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^\.off$'), owner_off))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^\.setgif@'), setgif_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^\.removegif$'), removegif_cmd))

    # Answer Handler (Must be LAST among text handlers)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer_handler))

    # Group Saver (Runs in background)
    async def save_group_info(update, context):
        if update.effective_chat and update.effective_chat.type in ['group', 'supergroup']:
            cid = update.effective_chat.id
            now = time.time()
            if cid not in _saved_groups or (now - _saved_groups[cid]) > CACHE_TTL:
                try:
                    save_group(cid, update.effective_chat.title or "Unknown")
                    _saved_groups[cid] = now
                except Exception as e: logger.warning(f"save_group failed: {e}")

    application.add_handler(MessageHandler(filters.ALL, save_group_info), group=1)
    logger.info("🤖 Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
