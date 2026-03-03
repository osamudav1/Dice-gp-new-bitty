import logging
import sqlite3
import random
import time
import asyncio
import os
import re
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# ==================== CONFIGURATION ====================
# Use environment variables for security
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1001234567890"))
DEPOSIT_GROUP_ID = int(os.environ.get("DEPOSIT_GROUP_ID", "-1001234567891"))
GAME_GROUP_URL = os.environ.get("GAME_GROUP_URL", "https://t.me/your_game_group")
DEPOSIT_URL = os.environ.get("DEPOSIT_URL", "https://t.me/your_deposit_group")
WITHDRAW_URL = os.environ.get("WITHDRAW_URL", "https://t.me/your_withdraw_group")

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY,
                  name TEXT,
                  mention TEXT,
                  today_deposit INTEGER DEFAULT 0,
                  today_withdraw INTEGER DEFAULT 0,
                  today_bet INTEGER DEFAULT 0,
                  balance INTEGER DEFAULT 0)''')
    
    # Games table
    c.execute('''CREATE TABLE IF NOT EXISTS games
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER UNIQUE,
                  status TEXT,
                  result TEXT,
                  created_at TIMESTAMP,
                  closed_at TIMESTAMP)''')
    
    # Bets table
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER,
                  user_id TEXT,
                  bet_type TEXT,
                  amount INTEGER,
                  status TEXT,
                  timestamp TIMESTAMP)''')
    
    # Welcome settings table
    c.execute('''CREATE TABLE IF NOT EXISTS welcome_settings
                 (id INTEGER PRIMARY KEY CHECK (id=1),
                  photo_id TEXT,
                  caption TEXT)''')
    
    # Insert default welcome if not exists
    c.execute("INSERT OR IGNORE INTO welcome_settings (id, caption) VALUES (1, 'ကြိုဆိုပါတယ်')")
    
    conn.commit()
    conn.close()

# ==================== DATABASE FUNCTIONS ====================
def get_next_game_id():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT game_id FROM games ORDER BY game_id DESC LIMIT 1")
    result = c.fetchone()
    
    if result:
        next_id = result[0] + 1
    else:
        next_id = 100000
    
    conn.close()
    return next_id

def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),))
    user = c.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'name': user[1],
            'mention': user[2],
            'today_deposit': user[3],
            'today_withdraw': user[4],
            'today_bet': user[5],
            'balance': user[6]
        }
    else:
        return {
            'user_id': str(user_id),
            'name': 'Unknown',
            'mention': '',
            'today_deposit': 0,
            'today_withdraw': 0,
            'today_bet': 0,
            'balance': 0
        }

def create_or_update_user(user_id, name, mention):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, name, mention, balance) VALUES (?, ?, ?, COALESCE((SELECT balance FROM users WHERE user_id = ?), 0))",
              (str(user_id), name, mention, str(user_id)))
    conn.commit()
    conn.close()

def update_balance(user_id, amount, operation='add'):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    if operation == 'add':
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, str(user_id)))
    else:
        c.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, str(user_id)))
    
    conn.commit()
    
    c.execute("SELECT balance FROM users WHERE user_id = ?", (str(user_id),))
    new_balance = c.fetchone()[0]
    conn.close()
    return new_balance

def update_today_stats(user_id, field, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute(f"UPDATE users SET {field} = {field} + ? WHERE user_id = ?", (amount, str(user_id)))
    conn.commit()
    conn.close()

def get_current_game():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE status = 'open' ORDER BY game_id DESC LIMIT 1")
    game = c.fetchone()
    conn.close()
    
    if game:
        return {
            'id': game[0],
            'game_id': game[1],
            'status': game[2],
            'result': game[3],
            'created_at': game[4],
            'closed_at': game[5]
        }
    return None

def create_game():
    game_id = get_next_game_id()
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO games (game_id, status, created_at) VALUES (?, 'open', ?)",
              (game_id, datetime.now()))
    conn.commit()
    conn.close()
    return game_id

def close_game(game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE games SET status = 'closed', closed_at = ? WHERE game_id = ?",
              (datetime.now(), game_id))
    conn.commit()
    conn.close()

def save_bet(game_id, user_id, bet_type, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (game_id, user_id, bet_type, amount, status, timestamp) VALUES (?, ?, ?, ?, 'pending', ?)",
              (game_id, str(user_id), bet_type, amount, datetime.now()))
    conn.commit()
    conn.close()
    update_today_stats(user_id, 'today_bet', amount)

def get_game_bets(game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE game_id = ?", (game_id,))
    bets = c.fetchall()
    conn.close()
    
    result = []
    for bet in bets:
        user = get_user(bet[2])
        result.append({
            'id': bet[0],
            'user_id': bet[2],
            'bet_type': bet[3],
            'amount': bet[4],
            'status': bet[5],
            'user_name': user['name']
        })
    return result

def update_bet_results(game_id, result_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    c.execute("SELECT * FROM bets WHERE game_id = ?", (game_id,))
    bets = c.fetchall()
    
    winners = []
    for bet in bets:
        if bet[3] == result_type:
            c.execute("UPDATE bets SET status = 'won' WHERE id = ?", (bet[0],))
            winners.append(bet)
        else:
            c.execute("UPDATE bets SET status = 'lost' WHERE id = ?", (bet[0],))
    
    conn.commit()
    conn.close()
    return winners

def get_welcome_settings():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT photo_id, caption FROM welcome_settings WHERE id = 1")
    result = c.fetchone()
    conn.close()
    return {'photo_id': result[0], 'caption': result[1]}

def update_welcome_photo(photo_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE welcome_settings SET photo_id = ? WHERE id = 1", (photo_id,))
    conn.commit()
    conn.close()

def update_welcome_caption(caption):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE welcome_settings SET caption = ? WHERE id = 1", (caption,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    return [user[0] for user in users]

# ==================== UTILITY FUNCTIONS ====================
def parse_bet(text):
    text = text.lower().strip()
    
    patterns = [
        (r'^s(\d+)$', 'small'),
        (r'^b(\d+)$', 'big'),
        (r'^j(\d+)$', 'japort'),
        (r'^jp(\d+)$', 'japort'),
        (r'^small(\d+)$', 'small'),
        (r'^big(\d+)$', 'big'),
        (r'^japort(\d+)$', 'japort'),
        (r'^small (\d+)$', 'small'),
        (r'^big (\d+)$', 'big'),
        (r'^japort (\d+)$', 'japort'),
    ]
    
    for pattern, bet_type in patterns:
        match = re.match(pattern, text)
        if match:
            return bet_type, int(match.group(1))
    
    return None, None

def has_both_small_big(text):
    text = text.lower()
    has_small = 's' in text or 'small' in text
    has_big = 'b' in text or 'big' in text
    return has_small and has_big

# ==================== AUTO CLOSE GAME FUNCTIONS ====================
async def countdown_10sec(context: ContextTypes.DEFAULT_TYPE):
    """Send countdown message 10 seconds before closing"""
    job = context.job
    game_id = job.data
    
    print(f"⏰ 10 second countdown for game {game_id}")
    
    game = get_current_game()
    if not game or game['game_id'] != game_id or game['status'] != 'open':
        print("Game already closed or not found")
        return
    
    await context.bot.send_message(
        chat_id=GAME_GROUP_ID,
        text="⚠️ **ပွဲပိတ်ရန် ၁၀ စက္ကန့်သာလိုတော့သည်** ⚠️",
        parse_mode='Markdown'
    )

async def auto_close_game(context: ContextTypes.DEFAULT_TYPE):
    """Auto close game after 1 minute and process results"""
    job = context.job
    game_id = job.data
    
    print(f"⏰ AUTO CLOSE TRIGGERED for game {game_id}")
    
    # Check if game is still open
    game = get_current_game()
    if not game or game['game_id'] != game_id or game['status'] != 'open':
        print("Game already closed or not found - stopping auto close")
        return
    
    # 1. ချက်ချင်း chat ပိတ်
    await context.bot.set_chat_permissions(
        chat_id=GAME_GROUP_ID,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False
        )
    )
    print("✅ Chat permissions closed")
    
    # 2. လောင်းထားတဲ့အချက်အလက်တွေယူ
    bets = get_game_bets(game_id)
    print(f"Found {len(bets)} bets")
    
    # 3. Summary ပို့
    summary = f"✨ **ပွဲစဉ်** ➖ `{game_id}`\n"
    summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ!** ➖\n\n"
    
    if bets:
        for bet in bets:
            multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
            bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
            summary += f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})\n"
    else:
        summary += "❌ လောင်းကြေးမရှိပါ\n"
    
    await context.bot.send_message(chat_id=GAME_GROUP_ID, text=summary, parse_mode='Markdown')
    
    # 4. ၁ စက္ကန့်စောင့်
    await asyncio.sleep(1)
    
    # 5. အံစာတုံးတောင်း
    await context.bot.send_message(
        chat_id=GAME_GROUP_ID,
        text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
        parse_mode='Markdown'
    )
    
    # 6. Game ID ကို chat_data မှာသိမ်း
    context.chat_data['awaiting_dice'] = game_id
    print(f"✅ Auto close completed for game {game_id}, awaiting dice")

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: user={user.id}, chat={chat.id}")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    if chat.id == GAME_GROUP_ID:
        await update.message.reply_text(
            text="🎲 **ကစားရန်**\n\n"
                 "S100 (Small 100)\n"
                 "B100 (Big 100)\n"
                 "J100 (Japort 100)\n\n"
                 "အနည်းဆုံး လောင်းကြေး ၁၀၀ ကျပ်",
            parse_mode='Markdown'
        )
        return
    
    if chat.id == DEPOSIT_GROUP_ID:
        await update.message.reply_text(
            "💰 **ငွေသွင်း/ငွေထုတ် Group**\n\n"
            "သင်၏အချက်အလက်များကြည့်ရန် '1' ကိုနှိပ်ပါ။\n"
            "**ငွေသွင်းရန်:** +ပမာဏ (ဥပမာ: +5000)\n"
            "**ငွေထုတ်ရန်:** -ပမာဏ (ဥပမာ: -2000)",
            parse_mode='Markdown'
        )
        return
    
    if chat.type == 'private':
        if user.id == OWNER_ID:
            keyboard = [
                [InlineKeyboardButton("🎮 Game စတင်ရန်", callback_data='owner_game_start')],
                [InlineKeyboardButton("⏹️ Game ပိတ်ရန်", callback_data='owner_game_stop')],
                [InlineKeyboardButton("🔴 Small", callback_data='owner_small'),
                 InlineKeyboardButton("🔵 Big", callback_data='owner_big'),
                 InlineKeyboardButton("🟣 Japort 7", callback_data='owner_japort')],
                [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
                [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
                "Game Group ကို အောက်ပါခလုတ်များဖြင့် ထိန်းချုပ်နိုင်ပါသည်။\n\n"
                "**သတိပြုရန်:** Game စတင်ပါက 1 မိနစ်အကြာတွင် အလိုအလျောက်ပိတ်ပါမည်။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            welcome = get_welcome_settings()
            keyboard = [
                [
                    InlineKeyboardButton("📊 အကောင့်အချက်အလက်", callback_data='account_info'),
                    InlineKeyboardButton("🎲 ကစားရန်", callback_data='play_game')
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if welcome['photo_id']:
                await update.message.reply_photo(
                    photo=welcome['photo_id'],
                    caption=welcome['caption'],
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(welcome['caption'], reply_markup=reply_markup)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    print(f"CALLBACK: {data} from user {user.id}")
    
    if data.startswith('owner_'):
        if user.id == OWNER_ID:
            if data == 'owner_game_start':
                await query.answer("✅ Game စတင်ရန်")
                
                game_id = create_game()
                print(f"Game created with ID: {game_id}")
                
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=f"**ပွဲစဉ်** - `{game_id}`\n"
                         f"**စတင်လောင်းလို့ရပါပြီ!**\n\n"
                         f"**ကစားနည်း:** S100, B100, J100 စသည်ဖြင့်ရိုက်ထည့်ပါ။\n\n"
                         f"⏰ **1 မိနစ်အကြာတွင် အလိုအလျောက်ပိတ်ပါမည်။**",
                    parse_mode='Markdown'
                )
                
                # Schedule 10 second countdown (after 50 seconds)
                context.job_queue.run_once(
                    countdown_10sec, 
                    50,
                    data=game_id,
                    name=f"countdown_{game_id}"
                )
                print(f"Scheduled countdown for game {game_id} in 50 seconds")
                
                # Schedule auto close after 1 minute (60 seconds)
                context.job_queue.run_once(
                    auto_close_game, 
                    60,
                    data=game_id,
                    name=f"close_game_{game_id}"
                )
                print(f"Scheduled auto close for game {game_id} in 60 seconds")
                
            elif data == 'owner_game_stop':
                await query.answer("✅ Game ပိတ်ရန်")
                
                game = get_current_game()
                if game:
                    game_id = game['game_id']
                    print(f"Manually stopping game {game_id}")
                    
                    # Remove any scheduled jobs
                    for job_name in [f"countdown_{game_id}", f"close_game_{game_id}"]:
                        jobs = context.job_queue.get_jobs_by_name(job_name)
                        for job in jobs:
                            job.schedule_removal()
                            print(f"Removed {job_name}")
                    
                    # Close chat permissions
                    await context.bot.set_chat_permissions(
                        chat_id=GAME_GROUP_ID,
                        permissions=ChatPermissions(
                            can_send_messages=False,
                            can_send_media_messages=False,
                            can_send_polls=False,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False
                        )
                    )
                    print("✅ Chat permissions closed")
                    
                    bets = get_game_bets(game_id)
                    
                    summary = f"✨ **ပွဲစဉ်** ➖ `{game_id}`\n"
                    summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ!** ➖\n\n"
                    
                    if bets:
                        for bet in bets:
                            multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
                            bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
                            summary += f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})\n"
                    else:
                        summary += "❌ လောင်းကြေးမရှိပါ\n"
                    
                    await context.bot.send_message(chat_id=GAME_GROUP_ID, text=summary, parse_mode='Markdown')
                    
                    await asyncio.sleep(1)
                    
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
                        parse_mode='Markdown'
                    )
                    
                    context.chat_data['awaiting_dice'] = game_id
                    print(f"Game {game_id} manually closed, awaiting dice")
                else:
                    await query.message.reply_text("❌ လက်ရှိ ဂိမ်းမရှိပါ။")
            
            elif data in ['owner_small', 'owner_big', 'owner_japort']:
                result_type = data.replace('owner_', '')
                await query.answer(f"✅ {result_type.capitalize()} အနိုင်ကြေညာရန်")
                
                game_id = context.chat_data.get('awaiting_dice')
                if not game_id:
                    await query.message.reply_text("❌ လက်ရှိ ဂိမ်းမရှိပါ။")
                    return
                
                print(f"Manual result: {result_type} for game {game_id}")
                
                # Remove any scheduled jobs
                for job_name in [f"countdown_{game_id}", f"close_game_{game_id}"]:
                    jobs = context.job_queue.get_jobs_by_name(job_name)
                    for job in jobs:
                        job.schedule_removal()
                
                # Update bet results
                winners = update_bet_results(game_id, result_type)
                
                result_display = "Small(S)" if result_type == 'small' else "Big(B)" if result_type == 'big' else "Japort(J)"
                multiplier_display = "5ဆ" if result_type == 'japort' else "2ဆ"
                
                result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
                result_text += f"💥 **Dice Bot** 💥\n"
                result_text += f"  **Manual Result:** {result_display} {multiplier_display}\n"
                result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
                
                if winners:
                    for bet in winners:
                        multiplier = 5 if result_type == 'japort' else 2
                        winnings = bet[4] * multiplier
                        new_balance = update_balance(bet[2], winnings, 'add')
                        user_info = get_user(bet[2])
                        prev_balance = new_balance - winnings
                        
                        result_text += f"👤 {user_info['name']} ➖ {result_display} > {bet[4]:,}(လောင်းကြေး) + {winnings - bet[4]:,}(ဒိုင်လျော်ကြေး) = {winnings:,}(နိုင်ကြေး)\n"
                        result_text += f"💰 **လက်ကျန်ငွေ** ➖ {prev_balance:,} + {winnings:,} = {new_balance:,}Ks\n\n"
                else:
                    result_text += "❌ အနိုင်ရသူမရှိပါ\n"
                
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=result_text,
                    parse_mode='Markdown'
                )
                
                close_game(game_id)
                
                await context.bot.set_chat_permissions(
                    chat_id=GAME_GROUP_ID,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                )
                
                # Clear chat data
                for key in ['awaiting_dice', 'dice1', 'dice2', 'dice1_msg_id', 'dice2_msg_id']:
                    if key in context.chat_data:
                        del context.chat_data[key]
                
                print(f"Game {game_id} completed manually")
        else:
            await query.answer("❌ ဤခလုတ်သည် ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်။", show_alert=True)
        return
    
    if data == 'account_info':
        user_data = get_user(user.id)
        await query.edit_message_text(
            f"**အမည်** - {user_data['name']}\n"
            f"**ID** - `{user_data['user_id']}`\n"
            f"**Mention** - {user_data['mention']}\n"
            f"**ယနေ့သွင်းငွေ** - {user_data['today_deposit']:,} ကျပ်\n"
            f"**ယနေ့ထုတ်ငွေ** - {user_data['today_withdraw']:,} ကျပ်\n"
            f"**ယနေ့လောင်းငွေ** - {user_data['today_bet']:,} ကျပ်\n"
            f"**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်",
            parse_mode='Markdown'
        )
    
    elif data == 'play_game':
        keyboard = [[InlineKeyboardButton("🎲 JOIN GROUP", url=GAME_GROUP_URL)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "ကစားရန် Game Group ကိုသွားပါ။",
            reply_markup=reply_markup
        )
    
    elif data == 'welcome_setting' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🖼️ ပုံထည့်ရန်", callback_data='welcome_add_photo')],
            [InlineKeyboardButton("🗑️ ဖျက်ရန်", callback_data='welcome_reset')],
            [InlineKeyboardButton("« နောက်သို့", callback_data='back_to_owner')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "**Welcome Message Settings**\n\n"
            "ပုံထည့်ရန် နှိပ်ပြီး ပုံပို့ပါ။\n"
            "စာသားပြင်လိုရင် စာသားတိုက်ရိုက်ပို့ပါ။",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        context.user_data['setting'] = 'welcome'
    
    elif data == 'welcome_add_photo' and user.id == OWNER_ID:
        await query.edit_message_text("📸 ပုံကို ပို့ပါ။")
        context.user_data['awaiting'] = 'welcome_photo'
    
    elif data == 'welcome_reset' and user.id == OWNER_ID:
        reset_welcome()
        await query.edit_message_text("✅ Welcome Message ကို Default ပြန်ထားပြီးပါပြီ။")
    
    elif data == 'broadcast' and user.id == OWNER_ID:
        await query.edit_message_text(
            "📢 **Broadcast ပို့ရန်**\n\n"
            "- ပုံ (သို့) စာသား ပို့ပါ\n"
            "- Button ပါလိုချင်ရင်: `ButtonName|https://example.com`",
            parse_mode='Markdown'
        )
        context.user_data['awaiting'] = 'broadcast'
    
    elif data == 'back_to_owner' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🎮 Game စတင်ရန်", callback_data='owner_game_start')],
            [InlineKeyboardButton("⏹️ Game ပိတ်ရန်", callback_data='owner_game_stop')],
            [InlineKeyboardButton("🔴 Small", callback_data='owner_small'),
             InlineKeyboardButton("🔵 Big", callback_data='owner_big'),
             InlineKeyboardButton("🟣 Japort 7", callback_data='owner_japort')],
            [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
            [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text
    
    print(f"MESSAGE: '{text}' from user {user.id} in chat {chat.id}")
    
    # Deposit Group Handler
    if chat.id == DEPOSIT_GROUP_ID:
        if text == "1":
            print(f"💰 User {user.id} sent 1 in deposit group")
            user_data = get_user(user.id)
            
            reply_msg = f"""**အမည်** - {user_data['name']}
**ID** - `{user_data['user_id']}`
**Mention** - {user_data['mention']}
**ယနေ့သွင်းငွေ** - {user_data['today_deposit']:,} ကျပ်
**ယနေ့လောင်းငွေ** - {user_data['today_bet']:,} ကျပ်
**ယနေ့ထုတ်ငွေ** - {user_data['today_withdraw']:,} ကျပ်
**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်"""
            
            await update.message.reply_to_message.reply_text(reply_msg, parse_mode='Markdown')
            return
        
        elif update.message.reply_to_message and user.id == OWNER_ID:
            replied = update.message.reply_to_message
            if replied.from_user.id == context.bot.id:
                # Extract user_id
                target_user_id = None
                match = re.search(r'ID - `(\d+)`', replied.text)
                if match:
                    target_user_id = match.group(1)
                
                if target_user_id:
                    if text.startswith('+'):
                        try:
                            amount = int(text[1:])
                            new_balance = update_balance(target_user_id, amount, 'add')
                            update_today_stats(target_user_id, 'today_deposit', amount)
                            user_data = get_user(target_user_id)
                            
                            await update.message.reply_text(
                                f"✅ **ငွေသွင်းပြီးပါပြီ**\n\n"
                                f"**အမည်** - {user_data['name']}\n"
                                f"**ID** - `{target_user_id}`\n"
                                f"**ထည့်လိုက်တဲ့ငွေ** - {amount:,} ကျပ်\n"
                                f"**လက်ကျန်ငွေ** - {new_balance:,} ကျပ်",
                                parse_mode='Markdown'
                            )
                            
                            await context.bot.send_message(
                                chat_id=GAME_GROUP_ID,
                                text=f"👤 {user_data['name']} လူကြီးမင်း၏ ဂိမ်းအကောင့်ထဲသို့ {amount:,} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။\n🎲 ဂိမ်းစတင်ကစားနိုင်ပါပြီ။"
                            )
                        except Exception as e:
                            await update.message.reply_text(f"❌ ငွေသွင်းရာတွင် အဆင်မပြေပါ။ {e}")
                    
                    elif text.startswith('-'):
                        try:
                            amount = int(text[1:])
                            new_balance = update_balance(target_user_id, amount, 'subtract')
                            update_today_stats(target_user_id, 'today_withdraw', amount)
                            user_data = get_user(target_user_id)
                            
                            withdraw_message = f"🧊 {user_data['name']} သင်ထုတ်ယူငွေ {amount:,} ကျပ်ကို သင့် KPay/Wave အကောင့်ထဲသို့ လွဲပေးပြီးပါပြီ။ စစ်ဆေးပေးပါ။ 🧊"
                            
                            await update.message.reply_text(
                                f"✅ **ငွေထုတ်ပြီးပါပြီ**\n\n"
                                f"**အမည်** - {user_data['name']}\n"
                                f"**ID** - `{target_user_id}`\n"
                                f"**ထုတ်လိုက်တဲ့ငွေ** - {amount:,} ကျပ်\n"
                                f"**လက်ကျန်ငွေ** - {new_balance:,} ကျပ်",
                                parse_mode='Markdown'
                            )
                            
                            await context.bot.send_message(
                                chat_id=GAME_GROUP_ID,
                                text=withdraw_message,
                                parse_mode='Markdown'
                            )
                        except Exception as e:
                            await update.message.reply_text(f"❌ ငွေထုတ်ရာတွင် အဆင်မပြေပါ။ {e}")
                else:
                    await update.message.reply_text("❌ User ID ကို ရှာမတွေ့ပါ။")
    
    # Game Group User Bet Handler
    elif chat.id == GAME_GROUP_ID:
        game = get_current_game()
        if not game or game['status'] != 'open':
            return
        
        bet_type, amount = parse_bet(text)
        if bet_type:
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက် လောင်းလို့မရပါ။")
                return
            
            if amount < 100:
                await update.message.reply_text("❌ အနည်းဆုံး လောင်းကြေး ၁၀၀ ကျပ်ဖြစ်ပါတယ်။")
                return
            
            user_data = get_user(user.id)
            if user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ။")
                return
            
            save_bet(game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Small(s)" if bet_type == 'small' else "Big(b)" if bet_type == 'big' else "Japort(j)"
            
            await update.message.reply_to_message.reply_text(
                f"**ပွဲစဉ်** ➖ `{game['game_id']}`\n"
                f"➖➖➖➖➖\n"
                f"**{bet_display}** - {amount} ([{multiplier}]ဆ)\n"
                f"➖➖➖➖➖\n"
                f"✅ **အောင်မြင်စွာ လောင်းကြေးတင်ပြီးပါပြီ။**\n"
                f"💰 **လက်ကျန်ငွေ** ➖ {new_balance:,}Ks",
                parse_mode='Markdown'
            )
    
    # Owner DM Handlers
    elif chat.type == 'private' and user.id == OWNER_ID:
        if 'awaiting' in context.user_data:
            if context.user_data['awaiting'] == 'welcome_photo':
                if update.message.photo:
                    photo_id = update.message.photo[-1].file_id
                    update_welcome_photo(photo_id)
                    await update.message.reply_text("✅ Welcome Photo ထည့်ပြီးပါပြီ။")
                else:
                    update_welcome_caption(update.message.text)
                    await update.message.reply_text("✅ Welcome Message ပြင်ပြီးပါပြီ။")
                
                del context.user_data['awaiting']
            
            elif context.user_data['awaiting'] == 'broadcast':
                caption = update.message.caption or update.message.text
                photo_id = update.message.photo[-1].file_id if update.message.photo else None
                
                reply_markup = None
                if caption and '|' in caption and 'http' in caption:
                    lines = caption.split('\n')
                    button_line = lines[-1]
                    if '|' in button_line:
                        button_name, button_url = button_line.split('|', 1)
                        caption = '\n'.join(lines[:-1])
                        keyboard = [[InlineKeyboardButton(button_name.strip(), url=button_url.strip())]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                
                users = get_all_users()
                await update.message.reply_text(f"📢 Broadcast စတင်နေပါပြီ... လက်ခံသူ {len(users)} ယောက်")
                
                sent_count = 0
                for i in range(0, len(users), 20):
                    batch = users[i:i+20]
                    for user_id in batch:
                        try:
                            if photo_id:
                                await context.bot.send_photo(
                                    chat_id=int(user_id),
                                    photo=photo_id,
                                    caption=caption,
                                    reply_markup=reply_markup
                                )
                            else:
                                await context.bot.send_message(
                                    chat_id=int(user_id),
                                    text=caption,
                                    reply_markup=reply_markup
                                )
                            sent_count += 1
                        except Exception as e:
                            print(f"Failed to send to {user_id}: {e}")
                        await asyncio.sleep(0.1)
                    
                    await asyncio.sleep(1)
                
                await update.message.reply_text(f"✅ Broadcast ပို့ပြီးပါပြီ။ လက်ခံသူ {sent_count} ယောက်")
                del context.user_data['awaiting']

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"DICE: from user {user.id} in chat {chat.id}")
    
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        if update.message.dice:
            dice_value = update.message.dice.value
            print(f"Dice value: {dice_value}")
            
            if 'dice1' not in context.chat_data:
                context.chat_data['dice1'] = dice_value
                context.chat_data['dice1_msg_id'] = update.message.message_id
                print("First dice stored")
                
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text="🎲 **နောက်တစ်ခါထပ်ပို့ပါ။**",
                    parse_mode='Markdown'
                )
                
            elif 'dice2' not in context.chat_data:
                context.chat_data['dice2'] = dice_value
                context.chat_data['dice2_msg_id'] = update.message.message_id
                print("Second dice stored")
                
                dice1 = context.chat_data['dice1']
                dice2 = context.chat_data['dice2']
                total = dice1 + dice2
                
                print(f"Calculating result: {dice1}+{dice2}={total}")
                
                if 2 <= total <= 6:
                    result_type = 'small'
                    result_display = "Small(S)"
                elif total == 7:
                    result_type = 'japort'
                    result_display = "Japort(J)"
                elif 8 <= total <= 12:
                    result_type = 'big'
                    result_display = "Big(B)"
                else:
                    result_type = 'unknown'
                    result_display = "Unknown"
                
                multiplier_display = "5ဆ" if result_type == 'japort' else "2ဆ"
                
                game_id = context.chat_data.get('awaiting_dice')
                if game_id:
                    print(f"Processing game {game_id}")
                    
                    # Remove any scheduled jobs
                    for job_name in [f"countdown_{game_id}", f"close_game_{game_id}"]:
                        jobs = context.job_queue.get_jobs_by_name(job_name)
                        for job in jobs:
                            job.schedule_removal()
                    
                    winners = update_bet_results(game_id, result_type)
                    print(f"Winners: {len(winners)}")
                    
                    result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
                    result_text += f"💥 **Dice Bot** 💥\n"
                    result_text += f"  {dice1}+{dice2} = {total} {result_display} {multiplier_display}\n"
                    result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
                    
                    if winners:
                        for bet in winners:
                            multiplier = 5 if result_type == 'japort' else 2
                            winnings = bet[4] * multiplier
                            new_balance = update_balance(bet[2], winnings, 'add')
                            user_info = get_user(bet[2])
                            prev_balance = new_balance - winnings
                            
                            result_text += f"👤 {user_info['name']} ➖ {result_display} > {bet[4]:,}(လောင်းကြေး) + {winnings - bet[4]:,}(ဒိုင်လျော်ကြေး) = {winnings:,}(နိုင်ကြေး)\n"
                            result_text += f"💰 **လက်ကျန်ငွေ** ➖ {prev_balance:,} + {winnings:,} = {new_balance:,}Ks\n\n"
                    else:
                        result_text += "❌ အနိုင်ရသူမရှိပါ\n"
                    
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=result_text,
                        parse_mode='Markdown'
                    )
                    
                    close_game(game_id)
                    
                    await context.bot.set_chat_permissions(
                        chat_id=GAME_GROUP_ID,
                        permissions=ChatPermissions(
                            can_send_messages=True,
                            can_send_media_messages=True,
                            can_send_polls=True,
                            can_send_other_messages=True,
                            can_add_web_page_previews=True
                        )
                    )
                    
                    # Clear chat data
                    for key in ['dice1', 'dice2', 'dice1_msg_id', 'dice2_msg_id', 'awaiting_dice']:
                        if key in context.chat_data:
                            del context.chat_data[key]
                    
                    print(f"✅ Game {game_id} completed successfully")
                else:
                    print("No awaiting dice game found")
                    # Clear dice data
                    del context.chat_data['dice1']
                    del context.chat_data['dice2']

# ==================== MAIN ====================
def main():
    init_db()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Dice.ALL, handle_dice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("=" * 60)
    print("🤖 BOT STARTED SUCCESSFULLY")
    print("=" * 60)
    print(f"👑 Owner ID: {OWNER_ID}")
    print(f"🎮 Game Group ID: {GAME_GROUP_ID}")
    print(f"💰 Deposit Group ID: {DEPOSIT_GROUP_ID}")
    print("=" * 60)
    print("✅ AUTO CLOSE WORKING:")
    print("   - Game start → 50 sec → countdown message")
    print("   - Game start → 60 sec → AUTO CLOSE")
    print("   - Auto close → permissions closed")
    print("   - Auto close → bets summary")
    print("   - Auto close → dice request")
    print("   - Owner dice → result calculation")
    print("   - Owner dice → winners announcement")
    print("   - Owner dice → permissions reopen")
    print("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
