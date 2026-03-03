import logging
import sqlite3
import random
import time
import asyncio
import os
import re
import json
import pickle
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ChatPermissions, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1001234567890"))
GAME_GROUP_URL = os.environ.get("GAME_GROUP_URL", "https://t.me/your_game_group")

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

# ==================== BACKUP FUNCTIONS ====================
def create_backup():
    """Create a complete backup of all database data"""
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    backup_data = {
        'users': [],
        'games': [],
        'bets': [],
        'welcome_settings': [],
        'timestamp': datetime.now().isoformat()
    }
    
    # Backup users
    c.execute("SELECT * FROM users")
    users = c.fetchall()
    for user in users:
        backup_data['users'].append({
            'user_id': user[0],
            'name': user[1],
            'mention': user[2],
            'today_deposit': user[3],
            'today_withdraw': user[4],
            'today_bet': user[5],
            'balance': user[6]
        })
    
    # Backup games
    c.execute("SELECT * FROM games")
    games = c.fetchall()
    for game in games:
        backup_data['games'].append({
            'id': game[0],
            'game_id': game[1],
            'status': game[2],
            'result': game[3],
            'created_at': game[4],
            'closed_at': game[5]
        })
    
    # Backup bets
    c.execute("SELECT * FROM bets")
    bets = c.fetchall()
    for bet in bets:
        backup_data['bets'].append({
            'id': bet[0],
            'game_id': bet[1],
            'user_id': bet[2],
            'bet_type': bet[3],
            'amount': bet[4],
            'status': bet[5],
            'timestamp': bet[6]
        })
    
    # Backup welcome settings
    c.execute("SELECT * FROM welcome_settings")
    settings = c.fetchall()
    for setting in settings:
        backup_data['welcome_settings'].append({
            'id': setting[0],
            'photo_id': setting[1],
            'caption': setting[2]
        })
    
    conn.close()
    
    # Save to file
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    
    return filename

def restore_backup(file_path):
    """Restore database from backup file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    # Clear existing data
    c.execute("DELETE FROM users")
    c.execute("DELETE FROM games")
    c.execute("DELETE FROM bets")
    c.execute("DELETE FROM welcome_settings")
    
    # Restore users
    for user in backup_data['users']:
        c.execute("""INSERT INTO users 
                    (user_id, name, mention, today_deposit, today_withdraw, today_bet, balance) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (user['user_id'], user['name'], user['mention'], 
                     user['today_deposit'], user['today_withdraw'], 
                     user['today_bet'], user['balance']))
    
    # Restore games
    for game in backup_data['games']:
        c.execute("""INSERT INTO games 
                    (id, game_id, status, result, created_at, closed_at) 
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (game['id'], game['game_id'], game['status'], 
                     game['result'], game['created_at'], game['closed_at']))
    
    # Restore bets
    for bet in backup_data['bets']:
        c.execute("""INSERT INTO bets 
                    (id, game_id, user_id, bet_type, amount, status, timestamp) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (bet['id'], bet['game_id'], bet['user_id'], 
                     bet['bet_type'], bet['amount'], bet['status'], bet['timestamp']))
    
    # Restore welcome settings
    for setting in backup_data['welcome_settings']:
        c.execute("""INSERT INTO welcome_settings 
                    (id, photo_id, caption) 
                    VALUES (?, ?, ?)""",
                    (setting['id'], setting['photo_id'], setting['caption']))
    
    conn.commit()
    conn.close()
    
    return len(backup_data['users'])

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

def get_user_bets(user_id, game_id=None):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    if game_id:
        c.execute("SELECT * FROM bets WHERE user_id = ? AND game_id = ?", (str(user_id), game_id))
    else:
        c.execute("SELECT * FROM bets WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (str(user_id),))
    bets = c.fetchall()
    conn.close()
    return bets

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

# ==================== BUTTONS ====================
def get_deposit_withdraw_buttons():
    keyboard = [
        [
            InlineKeyboardButton("💰 ငွေသွင်း", url="https://t.me/osamu1123"),
            InlineKeyboardButton("💸 ငွေထုတ်", url="https://t.me/osamu1123")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_warning_text():
    return "⚠️ **သတိပေးချက်** ⚠️\n\nငွေသွင်းငွေထုတ်ရန်အတွက် တရားဝင်အကောင့် @osamu1123 မှလွဲ၍ အခြားအကောင့်များသည် လူလိမ်များဖြစ်ကြပါသည်။\nUsername ကိုသေချာစစ်ဆေးပါ။"

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: {user.id} in {chat.id}")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    # GAME GROUP - Show game control buttons for owner, instructions for others
    if chat.id == GAME_GROUP_ID:
        if user.id == OWNER_ID:
            # Owner sees game control buttons
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text="👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
                     "ဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Normal users see instructions
            text = "🎲 **ကစားရန်**\n\n" \
                   "S100 (Small 100)\n" \
                   "B100 (Big 100)\n" \
                   "J100 (Japort 100)\n\n" \
                   "အနည်းဆုံး ၂၀၀ကျပ်\n" \
                   "အများဆုံး ၁၀၀၀ကျပ်"
            
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown'
            )
        return
    
    # PRIVATE CHAT - Owner DM with full controls
    if chat.type == 'private':
        if user.id == OWNER_ID:
            # Owner sees full control panel
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='owner_game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='owner_game_stop')],
                [InlineKeyboardButton("💰 ငွေသွင်း (Add MMK)", callback_data='add_money')],
                [InlineKeyboardButton("💸 ငွေထုတ် (Remove MMK)", callback_data='remove_money')],
                [InlineKeyboardButton("💾 Backup Data", callback_data='backup_data')],
                [InlineKeyboardButton("🔄 Restore Data", callback_data='restore_data')],
                [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
                [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
                "ဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။\n"
                "ငွေစာရင်းလုပ်ရန် ခလုတ်များသုံးပါ။\n"
                "Data Backup/Restore လုပ်ရန် ခလုတ်များသုံးပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Normal users see welcome message
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
    
    # ===== OWNER DM CALLBACKS =====
    if user.id == OWNER_ID:
        if data == 'owner_game_start':
            await query.answer()
            
            # Check if game already exists
            current_game = get_current_game()
            if current_game:
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ။ အရင်ပိတ်ပါ။")
                return
            
            game_id = create_game()
            
            text = f"**ပွဲစဉ်** - `{game_id}`\n" \
                   f"**စတင်လောင်းလို့ရပါပြီ**"
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=text,
                parse_mode='Markdown'
            )
            
            # Send warning as separate message
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            await query.message.reply_text("✅ ဂိမ်းစတင်ပြီးပါပြီ")
        
        elif data == 'owner_game_stop':
            await query.answer()
            
            game = get_current_game()
            if not game:
                await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
                return
            
            game_id = game['game_id']
            
            # Get bets
            bets = get_game_bets(game_id)
            
            # Send bet summary
            summary = f"✨ **ပွဲစဉ်** ➖ `{game_id}`\n"
            summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ** ➖\n\n"
            
            if bets:
                for bet in bets:
                    multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
                    bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
                    summary += f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})\n"
            else:
                summary += "❌ လောင်းကြေးမရှိပါ\n"
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID, 
                text=summary, 
                parse_mode='Markdown'
            )
            
            # Send warning as separate message
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Ask for dice
            await asyncio.sleep(1)
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
                parse_mode='Markdown'
            )
            
            # Store game ID for dice handling
            context.chat_data['awaiting_dice'] = game_id
            await query.message.reply_text("✅ ဂိမ်းပိတ်ပြီးပါပြီ။ အံစာတုံးစောင့်ဆိုင်းနေပါတယ်။")
        
        elif data == 'add_money':
            await query.answer()
            await query.edit_message_text(
                "💰 **ငွေသွင်းရန် User ID ကိုရိုက်ထည့်ပါ**\n\n"
                "ဥပမာ: `123456789`\n"
                "(သို့) User ရဲ့စာကို Reply လုပ်ပြီးလည်းရပါတယ်"
            )
            context.user_data['money_action'] = 'add'
            return
        
        elif data == 'remove_money':
            await query.answer()
            await query.edit_message_text(
                "💸 **ငွေထုတ်ရန် User ID ကိုရိုက်ထည့်ပါ**\n\n"
                "ဥပမာ: `123456789`\n"
                "(သို့) User ရဲ့စာကို Reply လုပ်ပြီးလည်းရပါတယ်"
            )
            context.user_data['money_action'] = 'remove'
            return
        
        elif data == 'backup_data':
            await query.answer()
            
            # Check if game is open
            current_game = get_current_game()
            if current_game:
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Backup လုပ်နိုင်ပါသည်။")
                return
            
            # Create backup
            filename = create_backup()
            
            # Send file
            with open(filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=f,
                    filename=filename,
                    caption=f"✅ **Backup အောင်မြင်ပါသည်**\n\nဖိုင်အမည်: {filename}\nရက်စွဲ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode='Markdown'
                )
            
            # Clean up file
            os.remove(filename)
            
            await query.message.reply_text("✅ Backup ပြီးပါပြီ။ ဖိုင်ကို လက်ခံရရှိပါမည်။")
        
        elif data == 'restore_data':
            await query.answer()
            
            # Check if game is open
            current_game = get_current_game()
            if current_game:
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Restore လုပ်နိုင်ပါသည်။")
                return
            
            await query.edit_message_text(
                "🔄 **Restore လုပ်ရန် Backup ဖိုင်ကို ပို့ပေးပါ**\n\n"
                "ဖိုင်သည် JSON format ဖြစ်ရပါမည်။"
            )
            context.user_data['awaiting'] = 'restore_file'
        
        elif data == 'welcome_setting':
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
        
        elif data == 'welcome_add_photo':
            await query.edit_message_text("📸 ပုံကို ပို့ပါ။")
            context.user_data['awaiting'] = 'welcome_photo'
        
        elif data == 'broadcast':
            await query.edit_message_text(
                "📢 **Broadcast ပို့ရန်**\n\n"
                "- ပုံ (သို့) စာသား ပို့ပါ"
            )
            context.user_data['awaiting'] = 'broadcast'
        
        elif data == 'back_to_owner':
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='owner_game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='owner_game_stop')],
                [InlineKeyboardButton("💰 ငွေသွင်း (Add MMK)", callback_data='add_money')],
                [InlineKeyboardButton("💸 ငွေထုတ် (Remove MMK)", callback_data='remove_money')],
                [InlineKeyboardButton("💾 Backup Data", callback_data='backup_data')],
                [InlineKeyboardButton("🔄 Restore Data", callback_data='restore_data')],
                [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
                [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
            ]
            await query.edit_message_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    
    # ===== GAME GROUP CALLBACKS =====
    elif data == 'game_start' and user.id == OWNER_ID:
        await query.answer()
        
        # Check if game already exists
        current_game = get_current_game()
        if current_game:
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ။ အရင်ပိတ်ပါ။")
            return
        
        game_id = create_game()
        
        text = f"**ပွဲစဉ်** - `{game_id}`\n" \
               f"**စတင်လောင်းလို့ရပါပြီ**"
        
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=text,
            parse_mode='Markdown'
        )
        
        # Send warning as separate message
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=get_warning_text(),
            reply_markup=get_deposit_withdraw_buttons(),
            parse_mode='Markdown'
        )
        
        await query.message.reply_text("✅ ဂိမ်းစတင်ပြီးပါပြီ")
    
    elif data == 'game_stop' and user.id == OWNER_ID:
        await query.answer()
        
        game = get_current_game()
        if not game:
            await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
            return
        
        game_id = game['game_id']
        
        # Get bets
        bets = get_game_bets(game_id)
        
        # Send bet summary
        summary = f"✨ **ပွဲစဉ်** ➖ `{game_id}`\n"
        summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ** ➖\n\n"
        
        if bets:
            for bet in bets:
                multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
                bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
                summary += f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})\n"
        else:
            summary += "❌ လောင်းကြေးမရှိပါ\n"
        
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID, 
            text=summary, 
            parse_mode='Markdown'
        )
        
        # Send warning as separate message
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=get_warning_text(),
            reply_markup=get_deposit_withdraw_buttons(),
            parse_mode='Markdown'
        )
        
        # Ask for dice
        await asyncio.sleep(1)
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
            parse_mode='Markdown'
        )
        
        # Store game ID for dice handling
        context.chat_data['awaiting_dice'] = game_id
        await query.message.reply_text("✅ ဂိမ်းပိတ်ပြီးပါပြီ။ အံစာတုံးစောင့်ဆိုင်းနေပါတယ်။")
    
    # ===== USER CALLBACKS =====
    elif data == 'account_info':
        user_data = get_user(user.id)
        if user_data:
            await query.edit_message_text(
                f"**အမည်** - {user_data['name']}\n"
                f"**ID** - `{user_data['user_id']}`\n"
                f"**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်",
                parse_mode='Markdown'
            )
    
    elif data == 'play_game':
        keyboard = [[InlineKeyboardButton("🎲 JOIN GROUP", url=GAME_GROUP_URL)]]
        await query.edit_message_text(
            "ကစားရန် Game Group ကိုသွားပါ။",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id}")
    
    # ===== OWNER DM - File upload for restore =====
    if chat.type == 'private' and user.id == OWNER_ID:
        if 'awaiting' in context.user_data:
            if context.user_data['awaiting'] == 'restore_file':
                if update.message.document:
                    file = await update.message.document.get_file()
                    file_path = f"restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    await file.download_to_drive(file_path)
                    
                    try:
                        user_count = restore_backup(file_path)
                        await update.message.reply_text(
                            f"✅ **Restore အောင်မြင်ပါသည်**\n\n"
                            f"အသုံးပြုသူ {user_count} ဦး ပြန်လည်ရောက်ရှိပါသည်။\n"
                            f"ဂိမ်းများနှင့် လောင်းကြေးများ ပြန်လည်ရောက်ရှိပါသည်။",
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        await update.message.reply_text(f"❌ Restore မအောင်မြင်ပါ။\nError: {e}")
                    
                    # Clean up
                    os.remove(file_path)
                    del context.user_data['awaiting']
                else:
                    await update.message.reply_text("❌ JSON ဖိုင်ကိုသာ ပို့ပါ။")
                return
            
            elif context.user_data['awaiting'] == 'welcome_photo':
                if update.message.photo:
                    photo_id = update.message.photo[-1].file_id
                    update_welcome_photo(photo_id)
                    await update.message.reply_text("✅ Welcome Photo ထည့်ပြီးပါပြီ")
                else:
                    update_welcome_caption(text)
                    await update.message.reply_text("✅ Welcome Message ပြင်ပြီးပါပြီ")
                del context.user_data['awaiting']
                return
            
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
                return
    
    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        game = get_current_game()
        
        # User info request with "3"
        if text == "3" and user.id != OWNER_ID:
            user_data = get_user(user.id)
            if user_data:
                # Get user's current bets if game is open
                bets_text = ""
                if game and game['status'] == 'open':
                    user_bets = get_user_bets(user.id, game['game_id'])
                    if user_bets:
                        bets_text = "\n\n**ယခုလောင်းထားသောငွေများ**\n"
                        for bet in user_bets:
                            bet_type = "S" if bet[3] == 'small' else "B" if bet[3] == 'big' else "J"
                            bets_text += f"{bet_type} {bet[4]:,} ကျပ်\n"
                
                msg = await update.message.reply_to_message.reply_text(
                    f"**အမည်** - {user_data['name']}\n"
                    f"**ID** - `{user_data['user_id']}`\n"
                    f"**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်"
                    f"{bets_text}",
                    parse_mode='Markdown'
                )
                # Auto delete after 5 seconds
                await asyncio.sleep(5)
                await msg.delete()
            return
        
        # Check if this is a deposit/withdraw command from owner
        if user.id == OWNER_ID and update.message.reply_to_message:
            replied = update.message.reply_to_message
            
            # Get the user who sent the original message
            target_user = replied.from_user
            target_user_id = target_user.id
            
            # If replying to bot's message, try to extract user ID from text
            if target_user.id == context.bot.id:
                match = re.search(r'ID[ -]+`?(\d+)`?', replied.text)
                if match:
                    target_user_id = int(match.group(1))
                else:
                    # Try to get from replied message sender
                    target_user_id = replied.from_user.id
            
            # Get user data
            user_data = get_user(target_user_id)
            if not user_data:
                await update.message.reply_text("❌ User ID မတွေ့ပါ။")
                return
            
            # Process deposit/withdraw
            if text.startswith('+'):
                try:
                    amount = int(text[1:])
                    
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'add')
                    update_today_stats(target_user_id, 'today_deposit', amount)
                    
                    # Send detailed info to the TARGET USER'S DM
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=f"✅ **ငွေသွင်းပြီးပါပြီ**\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"📢 {user_data['mention']}\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💰 ထည့်ငွေ: +{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    
                    # Send detailed info to OWNER'S DM
                    try:
                        await context.bot.send_message(
                            chat_id=OWNER_ID,
                            text=f"✅ **ငွေသွင်းပြီးပါပြီ**\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"📢 {user_data['mention']}\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💰 ထည့်ငွေ: +{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    
                    # Send confirmation to owner in group
                    await update.message.reply_text(f"✅ {user_data['name']} ထံသို့ {amount:,} ကျပ်ထည့်ပြီးပါပြီ")
                    
                    # Send public announcement to group
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"👤 {user_data['name']} အကောင့်ထဲသို့ {amount:,} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။\n🎲 ဂိမ်းစတင်ကစားနိုင်ပါပြီ။"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            
            elif text.startswith('-'):
                try:
                    amount = int(text[1:])
                    
                    if user_data['balance'] < amount:
                        await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                        return
                    
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'subtract')
                    update_today_stats(target_user_id, 'today_withdraw', amount)
                    
                    # Send detailed info to the TARGET USER'S DM
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=f"✅ **ငွေထုတ်ပြီးပါပြီ**\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"📢 {user_data['mention']}\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💸 ထုတ်ငွေ: -{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    
                    # Send detailed info to OWNER'S DM
                    try:
                        await context.bot.send_message(
                            chat_id=OWNER_ID,
                            text=f"✅ **ငွေထုတ်ပြီးပါပြီ**\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"📢 {user_data['mention']}\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💸 ထုတ်ငွေ: -{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    
                    # Send confirmation to owner in group
                    await update.message.reply_text(f"✅ {user_data['name']} ထံမှ {amount:,} ကျပ်ထုတ်ပြီးပါပြီ")
                    
                    # Send public announcement to group
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"🧊 {user_data['name']} သင်ထုတ်ယူငွေ {amount:,} ကျပ်ကို သင့် KPay/Wave အကောင့်ထဲသို့ လွဲပေးပြီးပါပြီ။ စစ်ဆေးပေးပါ။ 🧊"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            
            return
        
        # Regular betting - only if game is open
        if not game or game['status'] != 'open':
            return
        
        bet_type, amount = parse_bet(text)
        if bet_type:
            # Check if trying to bet both small and big
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                return
            
            # Check bet limits
            if amount < 200 or amount > 1000:
                await update.message.reply_text("❌ အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်သာလောင်းရမည်")
                return
            
            # Check if user already bet on small/big together
            if bet_type in ['small', 'big']:
                user_bets = get_user_bets(user.id, game['game_id'])
                for bet in user_bets:
                    if (bet_type == 'small' and bet[3] == 'big') or (bet_type == 'big' and bet[3] == 'small'):
                        await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                        return
            
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            save_bet(game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Small" if bet_type == 'small' else "Big" if bet_type == 'big' else "Japort"
            
            # Reply to user's bet message
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

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        if update.message.dice:
            dice_value = update.message.dice.value
            print(f"DICE: {dice_value}")
            
            if 'dice1' not in context.chat_data:
                context.chat_data['dice1'] = dice_value
                context.chat_data['dice1_msg_id'] = update.message.message_id
                dice_msg = await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text="🎲 **နောက်တစ်ခါထပ်ပို့ပါ**",
                    parse_mode='Markdown'
                )
                await asyncio.sleep(3)
                await dice_msg.delete()
            
            elif 'dice2' not in context.chat_data:
                context.chat_data['dice2'] = dice_value
                context.chat_data['dice2_msg_id'] = update.message.message_id
                
                # Both dice received, calculate result
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
                    
                    # Send warning as separate message with buttons
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=get_warning_text(),
                        reply_markup=get_deposit_withdraw_buttons(),
                        parse_mode='Markdown'
                    )
                    
                    # Close game
                    close_game(game_id)
                    
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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    print("=" * 60)
    print("🤖 BOT STARTED SUCCESSFULLY")
    print("=" * 60)
    print(f"👑 OWNER ID: {OWNER_ID}")
    print(f"🎮 GAME GROUP: {GAME_GROUP_ID}")
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
