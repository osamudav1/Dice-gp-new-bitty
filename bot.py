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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1001234567890"))
GAME_GROUP_URL = os.environ.get("GAME_GROUP_URL", "https://t.me/your_game_group")

# States for conversation
(ASK_USER_ID, ASK_AMOUNT) = range(2)

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY,
                  name TEXT,
                  mention TEXT,
                  today_deposit INTEGER DEFAULT 0,
                  today_withdraw INTEGER DEFAULT 0,
                  today_bet INTEGER DEFAULT 0,
                  balance INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS games
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER UNIQUE,
                  status TEXT,
                  result TEXT,
                  created_at TIMESTAMP,
                  closed_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER,
                  user_id TEXT,
                  bet_type TEXT,
                  amount INTEGER,
                  status TEXT,
                  timestamp TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS welcome_settings
                 (id INTEGER PRIMARY KEY CHECK (id=1),
                  photo_id TEXT,
                  caption TEXT)''')
    
    c.execute("INSERT OR IGNORE INTO welcome_settings (id, caption) VALUES (1, 'ကြိုဆိုပါတယ်')")
    
    conn.commit()
    conn.close()

# ==================== DATABASE FUNCTIONS ====================
def get_next_game_id():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT game_id FROM games ORDER BY game_id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result[0] + 1 if result else 100000

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
    return None

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
            'user_name': user['name'] if user else 'Unknown'
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
        (r'^s(\d+)$', 'small'), (r'^b(\d+)$', 'big'), (r'^j(\d+)$', 'japort'),
        (r'^jp(\d+)$', 'japort'), (r'^small(\d+)$', 'small'), (r'^big(\d+)$', 'big'),
        (r'^japort(\d+)$', 'japort'), (r'^small (\d+)$', 'small'),
        (r'^big (\d+)$', 'big'), (r'^japort (\d+)$', 'japort'),
    ]
    for pattern, bet_type in patterns:
        match = re.match(pattern, text)
        if match:
            return bet_type, int(match.group(1))
    return None, None

def has_both_small_big(text):
    text = text.lower()
    return ('s' in text or 'small' in text) and ('b' in text or 'big' in text)

# ==================== AUTO CLOSE GAME ====================
async def auto_close_game(context: ContextTypes.DEFAULT_TYPE):
    """Auto close game after 1 minute"""
    job = context.job
    game_id = job.data
    
    print(f"🔴 AUTO CLOSE: Game {game_id}")
    
    # Check if game still exists and is open
    game = get_current_game()
    if not game or game['game_id'] != game_id or game['status'] != 'open':
        print(f"Game {game_id} already closed")
        return
    
    # 1. CLOSE CHAT PERMISSIONS FIRST
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
    print("✅ Chat closed")
    
    # 2. GET BETS AND SHOW LIST
    bets = get_game_bets(game_id)
    
    summary = f"✨ **ပွဲစဉ်** ➖ `{game_id}`\n"
    summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ** ➖\n\n"
    
    if bets:
        for bet in bets:
            multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
            bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
            summary += f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})\n"
    else:
        summary += "❌ လောင်းကြေးမရှိပါ\n"
    
    await context.bot.send_message(chat_id=GAME_GROUP_ID, text=summary, parse_mode='Markdown')
    
    # 3. ASK FOR DICE
    await asyncio.sleep(1)
    await context.bot.send_message(
        chat_id=GAME_GROUP_ID,
        text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
        parse_mode='Markdown'
    )
    
    # 4. STORE GAME ID
    context.chat_data['awaiting_dice'] = game_id
    print(f"✅ Auto close done")

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: {user.id} in {chat.id}")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    # GAME GROUP - Everyone sees instructions
    if chat.id == GAME_GROUP_ID:
        await update.message.reply_text(
            text="🎲 **ကစားရန်**\n\n"
                 "S100 (Small 100)\n"
                 "B100 (Big 100)\n"
                 "J100 (Japort 100)\n\n"
                 "အနည်းဆုံး ၁၀၀ကျပ်",
            parse_mode='Markdown'
        )
        return
    
    # PRIVATE CHAT
    if chat.type == 'private':
        if user.id == OWNER_ID:
            # Owner sees game start button and money management
            keyboard = [
                [InlineKeyboardButton("🎮 Game စတင်ရန်", callback_data='owner_game_start')],
                [InlineKeyboardButton("💰 ငွေသွင်း (Add MMK)", callback_data='add_money')],
                [InlineKeyboardButton("💸 ငွေထုတ် (Remove MMK)", callback_data='remove_money')],
                [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
                [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
                "Game စတင်ရန် ခလုတ်နှိပ်ပါ။\n"
                "ငွေစာရင်းလုပ်ရန် ခလုတ်များသုံးပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Normal user sees welcome message
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
    
    print(f"CALLBACK: {data}")
    
    # OWNER CALLBACKS
    if data.startswith('owner_') and user.id == OWNER_ID:
        if data == 'owner_game_start':
            await query.answer()
            game_id = create_game()
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=f"**ပွဲစဉ်** - `{game_id}`\n"
                     f"**စတင်လောင်းလို့ရပါပြီ**\n\n"
                     f"⏰ **1 မိနစ်အကြာတွင် အလိုအလျောက်ပိတ်ပါမည်။**",
                parse_mode='Markdown'
            )
            
            # Schedule auto close after 60 seconds
            context.job_queue.run_once(
                auto_close_game, 
                60,
                data=game_id,
                name=f"close_game_{game_id}"
            )
            print(f"Scheduled auto close for {game_id}")
            await query.message.reply_text("✅ Game စတင်ပြီးပါပြီ")
    
    # MONEY MANAGEMENT
    elif data == 'add_money' and user.id == OWNER_ID:
        await query.answer()
        await query.edit_message_text(
            "💰 **ငွေသွင်းရန် User ID ကိုရိုက်ထည့်ပါ**\n\n"
            "ဥပမာ: `123456789`\n"
            "(သို့) User ရဲ့စာကို Reply လုပ်ပြီးလည်းရပါတယ်"
        )
        context.user_data['money_action'] = 'add'
        return
    
    elif data == 'remove_money' and user.id == OWNER_ID:
        await query.answer()
        await query.edit_message_text(
            "💸 **ငွေထုတ်ရန် User ID ကိုရိုက်ထည့်ပါ**\n\n"
            "ဥပမာ: `123456789`\n"
            "(သို့) User ရဲ့စာကို Reply လုပ်ပြီးလည်းရပါတယ်"
        )
        context.user_data['money_action'] = 'remove'
        return
    
    # USER CALLBACKS
    elif data == 'account_info':
        user_data = get_user(user.id)
        if user_data:
            await query.edit_message_text(
                f"**အမည်** - {user_data['name']}\n"
                f"**ID** - `{user_data['user_id']}`\n"
                f"**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်\n"
                f"**ယနေ့သွင်း** - {user_data['today_deposit']:,}\n"
                f"**ယနေ့ထုတ်** - {user_data['today_withdraw']:,}\n"
                f"**ယနေ့လောင်း** - {user_data['today_bet']:,}",
                parse_mode='Markdown'
            )
    
    elif data == 'play_game':
        keyboard = [[InlineKeyboardButton("🎲 JOIN GROUP", url=GAME_GROUP_URL)]]
        await query.edit_message_text(
            "ကစားရန် Game Group ကိုသွားပါ။",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == 'welcome_setting' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🖼️ ပုံထည့်ရန်", callback_data='welcome_add_photo')],
            [InlineKeyboardButton("« နောက်သို့", callback_data='back_to_owner')]
        ]
        await query.edit_message_text(
            "**Welcome Message Settings**\n\n"
            "ပုံထည့်ရန် နှိပ်ပြီး ပုံပို့ပါ။\n"
            "စာသားပြင်လိုရင် စာသားတိုက်ရိုက်ပို့ပါ။",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    elif data == 'welcome_add_photo' and user.id == OWNER_ID:
        await query.edit_message_text("📸 ပုံကို ပို့ပါ။")
        context.user_data['awaiting'] = 'welcome_photo'
    
    elif data == 'broadcast' and user.id == OWNER_ID:
        await query.edit_message_text(
            "📢 **Broadcast ပို့ရန်**\n\n"
            "- ပုံ (သို့) စာသား ပို့ပါ\n"
            "- Button ပါလိုချင်ရင်: `ButtonName|https://example.com`"
        )
        context.user_data['awaiting'] = 'broadcast'
    
    elif data == 'back_to_owner' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🎮 Game စတင်ရန်", callback_data='owner_game_start')],
            [InlineKeyboardButton("💰 ငွေသွင်း (Add MMK)", callback_data='add_money')],
            [InlineKeyboardButton("💸 ငွေထုတ် (Remove MMK)", callback_data='remove_money')],
            [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
            [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
        ]
        await query.edit_message_text(
            "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id}")
    
    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        game = get_current_game()
        if not game or game['status'] != 'open':
            return
        
        bet_type, amount = parse_bet(text)
        if bet_type:
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                return
            
            if amount < 100:
                await update.message.reply_text("❌ အနည်းဆုံး ၁၀၀ကျပ်")
                return
            
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            save_bet(game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Small" if bet_type == 'small' else "Big" if bet_type == 'big' else "Japort"
            
            await update.message.reply_to_message.reply_text(
                f"**ပွဲစဉ်** `{game['game_id']}`\n"
                f"➖➖➖➖➖\n"
                f"**{bet_display}** - {amount} ({multiplier})\n"
                f"➖➖➖➖➖\n"
                f"✅ အောင်မြင်စွာ လောင်းကြေးတင်ပြီးပါပြီ။\n"
                f"💰 လက်ကျန်ငွေ {new_balance:,}Ks",
                parse_mode='Markdown'
            )
        return
    
    # ===== OWNER DM - Money Management =====
    if chat.type == 'private' and user.id == OWNER_ID:
        # Check if we're in money management mode
        if 'money_action' in context.user_data:
            action = context.user_data['money_action']
            target_user_id = None
            
            # Check if this is a reply to a message
            if update.message.reply_to_message:
                replied = update.message.reply_to_message.text
                # Extract ID from replied message
                match = re.search(r'ID[ -]+`?(\d+)`?', replied)
                if match:
                    target_user_id = match.group(1)
            
            # If not reply, treat text as user ID
            if not target_user_id and text.strip().isdigit():
                target_user_id = text.strip()
            
            if target_user_id:
                user_data = get_user(target_user_id)
                if user_data:
                    context.user_data['target_user_id'] = target_user_id
                    await update.message.reply_text(
                        f"👤 **အသုံးပြုသူ အချက်အလက်**\n"
                        f"အမည်: {user_data['name']}\n"
                        f"ID: `{target_user_id}`\n"
                        f"လက်ကျန်ငွေ: {user_data['balance']:,} ကျပ်\n\n"
                        f"💰 **ငွေပမာဏ ရိုက်ထည့်ပါ**",
                        parse_mode='Markdown'
                    )
                    context.user_data['awaiting_amount'] = True
                else:
                    await update.message.reply_text("❌ ဤ User ID ကို စနစ်တွင် ရှာမတွေ့ပါ။\nUser က bot ကို /start လုပ်ထားဖို့လိုပါတယ်။")
                    context.user_data.clear()
            else:
                await update.message.reply_text("❌ User ID မှားနေပါတယ်။ ဂဏန်းသက်သက်ထည့်ပါ။")
            return
        
        # Check if we're awaiting amount
        if 'awaiting_amount' in context.user_data:
            try:
                amount = int(text.strip())
                if amount <= 0:
                    await update.message.reply_text("❌ ငွေပမာဏ 0 ထက်ကြီးရပါမယ်")
                    return
                
                action = context.user_data['money_action']
                target_id = context.user_data['target_user_id']
                user_data = get_user(target_id)
                
                if action == 'add':
                    new_balance = update_balance(target_id, amount, 'add')
                    update_today_stats(target_id, 'today_deposit', amount)
                    action_text = "ငွေသွင်း"
                    emoji = "💰"
                    gp_message = f"👤 {user_data['name']} အကောင့်ထဲသို့ {amount:,} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။\n🎲 ဂိမ်းစတင်ကစားနိုင်ပါပြီ။"
                else:
                    new_balance = update_balance(target_id, amount, 'subtract')
                    update_today_stats(target_id, 'today_withdraw', amount)
                    action_text = "ငွေထုတ်"
                    emoji = "💸"
                    gp_message = f"🧊 {user_data['name']} သင်ထုတ်ယူငွေ {amount:,} ကျပ်ကို သင့် KPay/Wave အကောင့်ထဲသို့ လွဲပေးပြီးပါပြီ။ စစ်ဆေးပေးပါ။ 🧊"
                
                # Send to owner
                await update.message.reply_text(
                    f"✅ **{action_text}ပြီးပါပြီ**\n\n"
                    f"👤 **အမည်:** {user_data['name']}\n"
                    f"🆔 **ID:** `{target_id}`\n"
                    f"💵 **{action_text}ငွေ:** {amount:,} ကျပ်\n"
                    f"💳 **လက်ကျန်အသစ်:** {new_balance:,} ကျပ်",
                    parse_mode='Markdown'
                )
                
                # Send to game group
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=gp_message
                )
                
                context.user_data.clear()
                
            except ValueError:
                await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            return
        
        # Handle other owner commands
        if 'awaiting' in context.user_data:
            if context.user_data['awaiting'] == 'welcome_photo':
                if update.message.photo:
                    photo_id = update.message.photo[-1].file_id
                    update_welcome_photo(photo_id)
                    await update.message.reply_text("✅ Welcome Photo ထည့်ပြီးပါပြီ")
                else:
                    update_welcome_caption(text)
                    await update.message.reply_text("✅ Welcome Message ပြင်ပြီးပါပြီ")
                del context.user_data['awaiting']
            
            elif context.user_data['awaiting'] == 'broadcast':
                photo_id = update.message.photo[-1].file_id if update.message.photo else None
                caption = update.message.caption or text
                
                users = get_all_users()
                await update.message.reply_text(f"📢 Broadcast စတင်နေပါပြီ... လက်ခံသူ {len(users)} ယောက်")
                
                sent = 0
                for uid in users:
                    try:
                        if photo_id:
                            await context.bot.send_photo(chat_id=int(uid), photo=photo_id, caption=caption)
                        else:
                            await context.bot.send_message(chat_id=int(uid), text=caption)
                        sent += 1
                    except:
                        pass
                    await asyncio.sleep(0.1)
                
                await update.message.reply_text(f"✅ Broadcast ပို့ပြီးပါပြီ။ လက်ခံသူ {sent} ယောက်")
                del context.user_data['awaiting']

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        if update.message.dice:
            dice_value = update.message.dice.value
            print(f"DICE: {dice_value}")
            
            if 'dice1' not in context.chat_data:
                context.chat_data['dice1'] = dice_value
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text="🎲 **နောက်တစ်ခါထပ်ပို့ပါ**",
                    parse_mode='Markdown'
                )
            
            elif 'dice2' not in context.chat_data:
                context.chat_data['dice2'] = dice_value
                dice1 = context.chat_data['dice1']
                dice2 = dice_value
                total = dice1 + dice2
                
                # Determine result
                if 2 <= total <= 6:
                    result = 'small'
                    display = "Small(S)"
                    multiplier = 2
                elif total == 7:
                    result = 'japort'
                    display = "Japort(J)"
                    multiplier = 5
                else:
                    result = 'big'
                    display = "Big(B)"
                    multiplier = 2
                
                game_id = context.chat_data.get('awaiting_dice')
                if game_id:
                    print(f"Processing game {game_id} result: {result}")
                    
                    # Remove scheduled job
                    jobs = context.job_queue.get_jobs_by_name(f"close_game_{game_id}")
                    for job in jobs:
                        job.schedule_removal()
                    
                    # Update bet results
                    winners = update_bet_results(game_id, result)
                    
                    # Build result message
                    msg = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
                    msg += f"💥 **Dice Bot** 💥\n"
                    msg += f"  {dice1}+{dice2} = {total} {display} ({multiplier}ဆ)\n"
                    msg += f"➖➖➖➖➖➖➖➖➖➖\n\n"
                    
                    if winners:
                        for bet in winners:
                            winnings = bet[4] * multiplier
                            new_balance = update_balance(bet[2], winnings, 'add')
                            user_info = get_user(bet[2])
                            prev_balance = new_balance - winnings
                            
                            msg += f"👤 {user_info['name']} ➖ {display} > {bet[4]:,}(လောင်း) + {winnings - bet[4]:,}(ဒိုင်လျော်) = {winnings:,}(နိုင်)\n"
                            msg += f"💰 **လက်ကျန်ငွေ** ➖ {prev_balance:,} + {winnings:,} = {new_balance:,}Ks\n\n"
                    else:
                        msg += "❌ အနိုင်ရသူမရှိပါ\n"
                    
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=msg,
                        parse_mode='Markdown'
                    )
                    
                    # Close game
                    close_game(game_id)
                    
                    # Reopen permissions
                    await context.bot.set_chat_permissions(
                        chat_id=GAME_GROUP_ID,
                        permissions=ChatPermissions(can_send_messages=True)
                    )
                    
                    # Clear all data
                    context.chat_data.clear()
                    print(f"✅ Game {game_id} completed")

# ==================== MAIN ====================
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Dice.ALL, handle_dice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("=" * 60)
    print("🤖 BOT STARTED SUCCESSFULLY")
    print("=" * 60)
    print(f"👑 OWNER ID: {OWNER_ID}")
    print(f"🎮 GAME GROUP: {GAME_GROUP_ID}")
    print("=" * 60)
    print("✅ GAME GROUP FEATURES:")
    print("   - Game start → 60 sec timer")
    print("   - Timer up → Chat CLOSED first")
    print("   → Bet list displayed")
    print("   → 2 dice requested")
    print("   → Owner rolls dice")
    print("   → Auto calculate winners")
    print("   → Update balances")
    print("   → Chat REOPENED")
    print("=" * 60)
    print("✅ OWNER DM FEATURES:")
    print("   - Game start button")
    print("   - Add MMK: Click → Enter User ID → Enter Amount")
    print("   - Remove MMK: Click → Enter User ID → Enter Amount")
    print("   - Auto sends to Game Group")
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
