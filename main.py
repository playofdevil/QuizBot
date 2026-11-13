import os
import random
import logging
import threading
import asyncio
import time
import datetime
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
    add_custom_question, del_custom_question, get_custom_questions,
    set_force_join, remove_force_join, get_force_join, get_user_top_group, get_group_stats,
    get_top_10_fastest, get_user_rank, set_custom_welcome, get_custom_welcome, remove_custom_welcome
)

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
VIP_GROUP_ID = os.environ.get("VIP_GROUP_ID", "0")

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

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
    
    custom = get_custom_questions(category)
    for c in custom:
        base_q.append({"q": c['question'], "a": c['answer']})
    base_q.reverse() 
    return base_q

def get_target_user(update: Update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    text = update.message.text
    if "@" in text:
        parts = text.split("@")
        if len(parts) > 1:
            return parts[1].split()[0]
    return None

async def is_admin_or_permitted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    if user_id == OWNER_ID: return True
    try:
        member = await update.effective_chat.get_member(user_id)
        if member.status in ['administrator', 'creator']: return True
    except Exception: pass
    return check_permission(user_id, chat_id)

async def owner_check(update: Update): 
    return update.effective_user.id == OWNER_ID

async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel = get_force_join()
    if not channel: return True
    try:
        member = await context.bot.get_chat_member(channel, update.effective_user.id)
        if member.status in ['member', 'administrator', 'creator']: return True
        else:
            await update.message.reply_text(f"⚠️ <b>You must join our channel to play!</b>\n\n👉 Join here: {channel}", parse_mode='HTML')
            return False
    except Exception:
        return True

active_quizzes = {}

async def quiz_loop(chat_id, context: ContextTypes.DEFAULT_TYPE, data):
    try:
        await asyncio.sleep(1)
        while data['active'] and data['q_index'] < len(data['questions']):
            if data.get('paused'):
                await asyncio.sleep(1)
                continue
                
            question = data['questions'][data['q_index']]
            data['answered'] = False
            data['current_answer'] = question['a'].lower().strip()
            data['start_time'] = time.time()
            
            for attempt in range(3):
                if data['answered'] or not data['active']: break
                if data.get('paused'): 
                    await asyncio.sleep(1)
                    continue
                
                points = 100 if attempt == 0 else 50 if attempt == 1 else 20
                
                # USING HTML PARSE MODE TO FIX UNDERSCORE ISSUE
                msg_text = (
                    f"▣ <b>𝐐ᴜɪᴢ 𝐐ᴜᴇꜱᴛɪᴏɴ</b> ▣\n\n"
                    f"📝 <b>{question['q']}</b>\n\n"
                    f"⏱️ <b>𝐓ɪᴍᴇ :</b> {data['speed']}s\n"
                    f"💰 <b>𝐑ᴇᴡᴀʀᴅ :</b> {points} 𝐏ᴛꜱ\n"
                    f"🔄 <b>𝐑ᴏᴜɴᴅ :</b> {attempt+1}/3\n\n"
                    f"👇 <b>𝐓ʏᴘᴇ 𝐘ᴏᴜʀ 𝐀ɴꜱᴡᴇʀ 𝐁ᴇʟᴏᴡ</b>"
                )
                
                keyboard = [[
                    InlineKeyboardButton("⏸️ Pause", callback_data="pause_quiz"),
                    InlineKeyboardButton("▶️ Resume", callback_data="resume_quiz")
                ],[
                    InlineKeyboardButton("⏭️ Skip", callback_data="skip_quiz"),
                    InlineKeyboardButton("💡 Hint", callback_data="hint_quiz")
                ]]
                
                try:
                    await context.bot.send_message(chat_id, msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
                except Exception:
                    pass
                
                wait_time = data['speed']
                for _ in range(wait_time):
                    if data['answered'] or not data['active']: break
                    if data.get('paused'): break
                    await asyncio.sleep(1)
                
            if not data['answered'] and data['active'] and not data.get('paused'):
                time_up_text = (
                    f"◈◈◑ ⏳ <b>𝐓ɪᴍᴇ'ꜱ 𝐔ᴘ</b> ◑◈◈\n\n"
                    f"❌ <b>𝐍ᴏʙᴏᴅʏ ɢᴏᴛ ɪᴛ ʀɪɢʜᴛ.</b>\n"
                    f"✅ <b>𝐓ʜᴇ 𝐀ɴꜱᴡᴇʀ 𝐖ᴀꜱ :</b> {question['a'].title()}\n\n"
                    f"🔄 <b>𝐍ᴇxᴛ 𝐐ᴜᴇꜱᴛɪᴏɴ...</b>"
                )
                await context.bot.send_message(chat_id, time_up_text, parse_mode='HTML')
                await asyncio.sleep(2)
            
            data['q_index'] += 1
            await asyncio.sleep(1)

        if data['active'] and not data.get('paused'):
            await context.bot.send_message(chat_id, "🎉 <b>𝐐ᴜɪᴢ 𝐂ᴏᴍᴘʟᴇᴛᴇᴅ!</b> 𝐀ʟʟ Qᴜᴇꜱᴛɪᴏɴꜱ ꜰɪɴɪꜱʜᴇᴅ.", parse_mode='HTML')
            
    except Exception as e:
        print(f"QUIZ LOOP CRASHED: {e}")
    finally:
        if chat_id in active_quizzes:
            del active_quizzes[chat_id]

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)
    
    welcome_text = (
        f"✧ <b>𝐄ɴɢʟɪꜱʜ 𝐐ᴜɪᴢ</b> ✧\n\n"
        f"👋 <b>𝐇ᴇʏ, {user.first_name}!</b>\n\n"
        f"➾ 𝐖ᴇʟᴄᴏᴍᴇ ᴛᴏ 𝐓ʜᴇ 𝐔ʟᴛɪᴍᴀᴛᴇ 𝐐ᴜɪᴢ 𝐄xᴘᴇʀɪᴇɴᴄᴇ.\n\n"
        f"▣ 𝐋ɪᴠᴇ 𝐆ʀᴏᴜᴘ 𝐐ᴜɪᴢᴢᴇꜱ\n"
        f"▣ 𝐆ʟᴏʙᴀʟ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅꜱ\n"
        f"▣ 𝟏𝟏𝟎𝟎+ 𝐌ɪxᴇᴅ 𝐐ᴜᴇꜱᴛɪᴏɴꜱ\n\n"
        f"👇 <b>𝐂ʟɪᴄᴋ 𝐁ᴇʟᴏᴡ 𝐓ᴏ 𝐒ᴛᴀʀᴛ</b>"
    )
    keyboard = [
        [InlineKeyboardButton("📖 View Commands (Help)", callback_data="show_help")],
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]
    ]
    
    photo_url = "https://raw.githubusercontent.com/playofdevil/QuizBot/main/welcome.gif"
    
    try:
        await update.message.reply_animation(
            animation=photo_url, 
            caption=welcome_text, 
            parse_mode='HTML', 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except:
        await update.message.reply_text(welcome_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        f"▣ <b>𝐁ᴏᴛ 𝐂ᴏᴍᴍᴀɴᴅꜱ 𝐆ᴜɪᴅᴇ</b> ▣\n\n"
        f"🛡️ <b>𝐆𝐑𝐎𝐔𝐏 𝐀𝐃𝐌𝐈𝐍 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
        f"• <code>Quiz@on</code> / <code>Quiz@off</code> — ꜱᴛᴀʀᴛ/ꜱᴛᴏᴘ Qᴜɪᴢ\n"
        f"• <code>Speed@10s</code> — ꜱᴇᴛ ᴛɪᴍᴇʀ\n"
        f"• <code>Skip@</code> / <code>Hint@</code> — ꜱᴋɪᴘ/ʜɪɴᴛ\n"
        f"• <code>Pause@q</code> / <code>Resume@q</code> — ᴘᴀᴜꜱᴇ/ʀᴇꜱᴜᴍᴇ\n"
        f"• <code>GroupStats@</code> — ɢʀᴏᴜᴘ ꜱᴛᴀᴛꜱ\n"
        f"• <code>Yes@on</code> / <code>No@off</code> — ɢʀᴀɴᴛ/ʀᴇᴍᴏᴠᴇ ᴘᴏᴡᴇʀ\n"
        f"• <code>SetWelcome@</code> / <code>RemoveWelcome@</code> — ꜱᴇᴛ ɢʀᴏᴜᴘ ᴡᴇʟᴄᴏᴍᴇ\n\n"
        f"👤 <b>𝐔𝐒𝐄𝐑 𝐂𝐎𝐌𝐌𝐀𝐍𝐃𝐒:</b>\n"
        f"• <code>Score@me</code> / <code>Score@group</code> / <code>Score@all</code> — ꜱᴄᴏʀᴇꜱ\n"
        f"• <code>Profile@username</code> — ᴠɪᴇᴡ ᴘʀᴏꜰɪʟᴇ\n"
        f"• <code>Rank@me</code> — ɢʟᴏʙᴀʟ ʀᴀɴᴋ\n"
        f"• <code>top@fast</code> — ꜰᴀꜱᴛᴇꜱᴛ ᴀɴꜱᴡᴇʀꜱ\n"
        f"• <code>Report@</code> — ʀᴇᴘᴏʀᴛ Qᴜᴇꜱᴛɪᴏɴ"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            if update.callback_query.message.animation or update.callback_query.message.photo:
                await update.callback_query.message.edit_caption(caption=help_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))
            else:
                await update.callback_query.message.edit_text(help_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))
        except Exception:
            await update.callback_query.message.reply_text(help_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]))
    else:
        await update.message.reply_text(help_text, parse_mode='HTML')

async def back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    
    welcome_text = (
        f"✧ <b>𝐄ɴɢʟɪꜱʜ 𝐐ᴜɪᴢ</b> ✧\n\n"
        f"👋 <b>𝐇ᴇʏ, {user.first_name}!</b>\n\n"
        f"➾ 𝐖ᴇʟᴄᴏᴍᴇ ᴛᴏ 𝐓ʜᴇ 𝐔ʟᴛɪᴍᴀᴛᴇ 𝐐ᴜɪᴢ 𝐄xᴘᴇʀɪᴇɴᴄᴇ.\n\n"
        f"▣ 𝐋ɪᴠᴇ 𝐆ʀᴏᴜᴘ 𝐐ᴜɪᴢᴢᴇꜱ\n"
        f"▣ 𝐆ʟᴏʙᴀʟ 𝐋ᴇᴀᴅᴇʀʙᴏᴀʀᴅꜱ\n"
        f"▣ 𝟏𝟏𝟎𝟎+ 𝐌ɪxᴇᴅ 𝐐ᴜᴇꜱᴛɪᴏɴꜱ\n\n"
        f"👇 <b>𝐂ʟɪᴄᴋ 𝐁ᴇʟᴏᴡ 𝐓ᴏ 𝐒ᴛᴀʀᴛ</b>"
    )
    keyboard = [
        [InlineKeyboardButton("📖 View Commands (Help)", callback_data="show_help")],
        [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]
    ]
    try:
        if query.message.animation or query.message.photo:
            await query.message.edit_caption(caption=welcome_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.edit_text(welcome_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        pass

async def quiz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    if not await check_force_join(update, context): return
    if not get_global_state(): return await update.message.reply_text("⚠️ Bot is in maintenance.")
    if not await is_admin_or_permitted(update, context): return await update.message.reply_text("❌ Only Admins can start/stop the quiz.")

    cmd, arg = parse_command(update.message.text)
    chat_id = update.effective_chat.id
    
    if arg and "on" in arg:
        if chat_id in active_quizzes: return await update.message.reply_text("⚠️ Quiz is already running!")
        keyboard = [
            [InlineKeyboardButton("🟢 Easy", callback_data="cat_easy")],
            [InlineKeyboardButton("🟡 Medium", callback_data="cat_medium")],
            [InlineKeyboardButton("🔴 Hard", callback_data="cat_hard")]
        ]
        await update.message.reply_text("🎮 <b>Choose a Category to Start:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    elif arg and "off" in arg:
        if chat_id in active_quizzes:
            active_quizzes[chat_id]['active'] = False
            del active_quizzes[chat_id]
            await update.message.reply_text("⏸️ <b>Quiz Stopped!</b>", parse_mode='HTML')
        else:
            await update.message.reply_text("⚠️ No quiz is running.")

async def pause_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes:
        active_quizzes[chat_id]['paused'] = True
        await update.message.reply_text("⏸️ <b>Quiz Paused!</b>", parse_mode='HTML')
    else: await update.message.reply_text("⚠️ No quiz running.")

async def resume_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes:
        active_quizzes[chat_id]['paused'] = False
        await update.message.reply_text("▶️ <b>Quiz Resumed!</b>", parse_mode='HTML')
    else: await update.message.reply_text("⚠️ No quiz running.")

async def group_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    stats = get_group_stats(update.effective_chat.id)
    await update.message.reply_text(f"📊 <b>GROUP STATS</b>\n\n👥 Total Players: {stats['players']}\n🏆 Total Points: {stats['points']}", parse_mode='HTML')

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    if chat_id in active_quizzes: return await query.edit_message_text("⚠️ Quiz already running!")

    category = query.data.split("_")[1]
    speed = context.chat_data.get('group_speed', 15)
    questions = get_questions(category)
    random.shuffle(questions)
    
    data = {'active': True, 'paused': False, 'category': category, 'speed': speed, 'questions': questions, 'q_index': 0, 'answered': False, 'current_answer': None, 'start_time': 0}
    active_quizzes[chat_id] = data
    await query.edit_message_text(f"✅ Category <b>{category.title()}</b> selected!\n🚀 <b>Quiz Started!</b> Speed: {speed}s", parse_mode='HTML')
    asyncio.create_task(quiz_loop(chat_id, context, data))

async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ['group', 'supergroup']: return
    chat_id = update.effective_chat.id
    if chat_id not in active_quizzes: return
    
    data = active_quizzes[chat_id]
    user = update.effective_user
    
    if not data['active'] or data['answered'] or not data['current_answer'] or data.get('paused'): return
    if is_banned(user.id) or is_muted(user.id, chat_id): return
    if not await check_force_join(update, context): return
    
    user_ans = update.message.text.lower().strip()
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
            f"⟡✦⟡ ✅ <b>𝐂ᴏʀʀᴇᴄᴛ!</b> ⟡✦⟡\n\n"
            f"👤 <b>𝐔ꜱᴇʀ:</b> {user.first_name}\n"
            f"🎯 <b>𝐀ɴꜱᴡᴇʀ:</b> {data['current_answer'].title()}\n"
            f"💰 <b>𝐏ᴏɪɴᴛꜱ:</b> +{points}\n"
            f"⏱️ <b>𝐓ɪᴍᴇ:</b> {time_taken}s"
        )
        await update.message.reply_text(success_text, parse_mode='HTML')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    action = query.data.split("_")[0]
    
    if action in ["pause", "resume", "skip", "hint"]:
        if not await is_admin_or_permitted(update, context):
            return await query.answer("❌ Only Admins can use this.", show_alert=True)
            
        if chat_id not in active_quizzes:
            return await query.answer("⚠️ No quiz running.", show_alert=True)
            
        data = active_quizzes[chat_id]
        
        if action == "pause":
            data['paused'] = True
            await query.answer("⏸️ Quiz Paused")
        elif action == "resume":
            data['paused'] = False
            await query.answer("▶️ Quiz Resumed")
        elif action == "skip":
            if data['current_answer']:
                await context.bot.send_message(chat_id, f"⏭️ <b>Question Skipped!</b> The correct answer was: <b>{data['current_answer'].title()}</b>", parse_mode='HTML')
            data['answered'] = True
        elif action == "hint":
            if data['current_answer']:
                ans = data['current_answer']
                await context.bot.send_message(chat_id, f"💡 <b>Hint:</b> Answer starts with <code>{ans[0]}</code> and has {len(ans)} letters.", parse_mode='HTML')

async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes:
        data = active_quizzes[chat_id]
        if data['current_answer']:
            await update.message.reply_text(f"⏭️ <b>Question Skipped!</b> The correct answer was: <b>{data['current_answer'].title()}</b>", parse_mode='HTML')
        data['answered'] = True
    else: await update.message.reply_text("⚠️ No quiz running.")

async def hint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    if chat_id in active_quizzes and active_quizzes[chat_id]['current_answer']:
        ans = active_quizzes[chat_id]['current_answer']
        await update.message.reply_text(f"💡 <b>Hint:</b> Answer starts with <code>{ans[0]}</code> and has {len(ans)} letters.", parse_mode='HTML')
    else: await update.message.reply_text("⚠️ No active question.")

async def yes_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        add_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"✅ Permission granted to {target.first_name}.")

async def no_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    target = get_target_user(update)
    if target and not isinstance(target, str):
        remove_permission(target.id, update.effective_chat.id)
        await update.message.reply_text(f"❌ Permission removed for {target.first_name}.")

async def speed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>Speed@10s</code>", parse_mode='HTML')
    try:
        speed = int(arg[:-1]) * (60 if arg.endswith('m') else 1)
        if speed < 5 or speed > 120: raise ValueError
    except: return await update.message.reply_text("❌ Invalid format.")
    context.chat_data['group_speed'] = speed
    await update.message.reply_text(f"⚡ Speed set to <b>{speed} seconds</b>.", parse_mode='HTML')

async def score_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    prof = get_user_profile(user.id, "global")
    await update.message.reply_text(f"🏆 <b>{user.first_name}</b>, your score is: <b>{prof['score']}</b> points!", parse_mode='HTML')

async def score_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_10("global")
    if not top: return await update.message.reply_text("📊 No scores yet!")
    text = "🥇 <b>GLOBAL TOP 10</b> 🥇\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u['username']}</b> — {u['score']} pts\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def score_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_10(update.effective_chat.id)
    if not top: return await update.message.reply_text("📊 No scores in this group!")
    text = f"🥇 <b>GROUP TOP 10</b> 🥇\n\n"
    for idx, u in enumerate(top, 1): text += f"{idx}. <b>{u['username']}</b> — {u['score']} pts\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def top_fast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_10_fastest()
    if not top: return await update.message.reply_text("📊 No data yet!")
    text = "⚡ <b>FASTEST USERS</b> ⚡\n\n"
    for idx, u in enumerate(top, 1):
        f_time = u.get('fastest_time', 0)
        text += f"{idx}. <b>{u['username']}</b> — {f_time}s\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def rank_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rank = get_user_rank(user.id)
    if rank == 0: return await update.message.reply_text("⚠️ You haven't played yet!")
    await update.message.reply_text(f"🏅 <b>{user.first_name}</b>, your Global Rank is: <b>#{rank}</b>", parse_mode='HTML')

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = get_target_user(update)
    if not target: return await update.message.reply_text("⚠️ Reply to a message or tag a username.")
    if isinstance(target, str): return await update.message.reply_text(f"Checking profile for @{target}...")
    prof = get_user_profile(target.id, "global")
    await update.message.reply_text(f"👤 <b>Profile: {target.first_name}</b>\n🏆 Score: {prof['score']}\n✅ Correct: {prof['correct']}", parse_mode='HTML')

async def set_welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    chat_id = update.effective_chat.id
    
    if update.message.reply_to_message:
        msg = update.message.reply_to_message
        text = msg.caption if msg.caption else ""
        media_id = None
        media_type = None
        
        if msg.photo: media_id = msg.photo[-1].file_id; media_type = "photo"
        elif msg.animation: media_id = msg.animation.file_id; media_type = "animation"
        elif msg.video: media_id = msg.video.file_id; media_type = "video"
        
        set_custom_welcome(chat_id, text, media_id, media_type)
        await update.message.reply_text("✅ Custom Welcome set (with media)!")
    else:
        cmd, arg = parse_command(update.message.text)
        if not arg: return await update.message.reply_text("⚠️ Usage: <code>SetWelcome@Your text</code> or reply to media.", parse_mode='HTML')
        set_custom_welcome(chat_id, arg, None, None)
        await update.message.reply_text("✅ Custom Welcome text set!")

async def remove_welcome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin_or_permitted(update, context): return
    remove_custom_welcome(update.effective_chat.id)
    await update.message.reply_text("✅ Custom Welcome removed.")

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot: continue
        chat_id = update.effective_chat.id
        custom = get_custom_welcome(chat_id)
        
        if custom:
            text = custom.get('text', '')
            if not text: text = f"Welcome {member.first_name}!"
            
            now = datetime.datetime.now()
            text = text.replace("{name}", member.first_name)
            text = text.replace("{username}", f"@{member.username}" if member.username else member.first_name)
            text = text.replace("{group}", update.effective_chat.title)
            text = text.replace("{date}", now.strftime("%d %b %Y"))
            text = text.replace("{time}", now.strftime("%I:%M %p"))
            text = text.replace("{id}", str(member.id))
            
            media_id = custom.get('media_id')
            media_type = custom.get('media_type')
            
            try:
                if media_type == "photo": await context.bot.send_photo(chat_id, media_id, caption=text, parse_mode='HTML')
                elif media_type == "animation": await context.bot.send_animation(chat_id, media_id, caption=text, parse_mode='HTML')
                elif media_type == "video": await context.bot.send_video(chat_id, media_id, caption=text, parse_mode='HTML')
                else: await context.bot.send_message(chat_id, text, parse_mode='HTML')
            except:
                await context.bot.send_message(chat_id, text, parse_mode='HTML')

async def top10_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    top = get_top_10("global")
    text = "👑 <b>TOP 10 USERS DETAILS</b>\n\n"
    for idx, u in enumerate(top, 1):
        grp = get_user_top_group(u['user_id'])
        text += f"{idx}. <b>{u['username']}</b> (ID: <code>{u['user_id']}</code>)\n🏆 {u['score']} pts | 📍 {grp}\n\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    target = get_target_user(update)
    if not target or isinstance(target, str): return await update.message.reply_text("⚠️ Reply to a user's message.")
    prof = get_user_profile(target.id, "global")
    await update.message.reply_text(f"👤 <b>User Info</b>\nName: {target.first_name}\nID: <code>{target.id}</code>\nScore: {prof['score']}", parse_mode='HTML')

async def force_join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    cmd, arg = parse_command(update.message.text)
    if not arg: return await update.message.reply_text("⚠️ Usage: <code>ForceJoin@channel_link</code>", parse_mode='HTML')
    set_force_join(arg)
    await update.message.reply_text("✅ Force join channel set!")

async def remove_force_join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    remove_force_join()
    await update.message.reply_text("✅ Force join channel removed!")

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
        await update.message.reply_text(f"🔇 {target.first_name} muted.")

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

async def add_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("AddQ@", "", 1)
    try:
        cat, q, a = text.split("@")
        add_custom_question(cat.lower(), q, a.lower())
        await update.message.reply_text(f"✅ Added to {cat}!")
    except: await update.message.reply_text("❌ Format: <code>AddQ@easy@Question@Answer</code>", parse_mode='HTML')

async def del_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("DelQ@", "", 1)
    try:
        cat, q = text.split("@")
        if del_custom_question(cat.lower(), q): await update.message.reply_text(f"✅ Deleted.")
        else: await update.message.reply_text("❌ Not found.")
    except: await update.message.reply_text("❌ Format: <code>DelQ@easy@Question</code>", parse_mode='HTML')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    cmd, arg = parse_command(update.message.text)
    if arg == "users": await update.message.reply_text(f"👥 Users: {get_total_users()}")
    if arg == "groups": await update.message.reply_text(f"👥 Groups: {get_total_groups()}")

async def broadcast_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("BroadcastAll@", "", 1)
    if not text: return await update.message.reply_text("⚠️ Usage: <code>BroadcastAll@msg</code>", parse_mode='HTML')
    groups = get_all_groups()
    users = get_all_users()
    await update.message.reply_text(f"📢 Broadcasting to {len(groups)} groups and {len(users)} users...")
    for g in groups:
        try: await context.bot.send_message(g['chat_id'], f"📢 {text}"); await asyncio.sleep(1)
        except: pass
    for u in users:
        try: await context.bot.send_message(u['user_id'], f"📢 {text}"); await asyncio.sleep(1)
        except: pass
    await update.message.reply_text("✅ Broadcast All Sent!")

async def broadcast_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("BroadcastGroups@", "", 1)
    if not text: return await update.message.reply_text("⚠️ Usage: <code>BroadcastGroups@msg</code>", parse_mode='HTML')
    groups = get_all_groups()
    for g in groups:
        try: await context.bot.send_message(g['chat_id'], f"📢 {text}"); await asyncio.sleep(1)
        except: pass
    await update.message.reply_text("✅ Sent to groups!")

async def broadcast_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    text = update.message.text.replace("BroadcastUsers@", "", 1)
    if not text: return await update.message.reply_text("⚠️ Usage: <code>BroadcastUsers@msg</code>", parse_mode='HTML')
    users = get_all_users()
    for u in users:
        try: await context.bot.send_message(u['user_id'], f"📢 {text}"); await asyncio.sleep(1)
        except Exception: pass
    await update.message.reply_text("✅ Sent to users!")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    groups = get_all_groups()
    if not groups: return await update.message.reply_text("No groups found.")
    text = "📋 <b>GROUPS LIST</b>\n\n"
    for g in groups: text += f"• {g.get('title', 'Unknown')} : <code>{g['chat_id']}</code>\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def owner_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    set_global_state(True)
    await update.message.reply_text("🌍 Global ON.")

async def owner_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await owner_check(update): return
    set_global_state(False)
    await update.message.reply_text("🌍 Global OFF.")

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^help@'), help_cmd))
    application.add_handler(CallbackQueryHandler(help_cmd, pattern=r'^show_help$'))
    application.add_handler(CallbackQueryHandler(back_to_start, pattern=r'^back_start$'))

    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Quiz@'), quiz_cmd))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r'^cat_'))
    application.add_handler(CallbackQueryHandler(button_handler, pattern=r'^(pause|resume|skip|hint)_quiz$'))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Speed@'), speed_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Pause@q'), pause_quiz))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Resume@q'), resume_quiz))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^GroupStats@'), group_stats))
    
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@me'), score_me))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@all'), score_all))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Score@group'), score_group))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^top@fast'), top_fast))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Rank@me'), rank_me))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Profile@'), profile_cmd))
    
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^SetWelcome@'), set_welcome_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^RemoveWelcome@'), remove_welcome_cmd))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))

    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Skip@'), skip_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Hint@'), hint_cmd))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^Yes@on'), yes_perm))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^No@off'), no_perm))
    
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
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^BroadcastGroups@'), broadcast_groups))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^BroadcastUsers@'), broadcast_users))
    application.add_handler(MessageHandler(filters.Regex(r'(?i)^ListGroups@'), list_groups))
    
    application.add_handler(MessageHandler(filters.Regex(r'^\.on$'), owner_on))
    application.add_handler(MessageHandler(filters.Regex(r'^\.off$'), owner_off))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer_handler))
    
    async def save_group_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type in ['group', 'supergroup']:
            save_group(update.effective_chat.id, update.effective_chat.title)
            
    application.add_handler(MessageHandler(filters.ALL, save_group_info))

    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
