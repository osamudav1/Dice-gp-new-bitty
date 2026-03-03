import logging
import sqlite3
import random
import time
import asyncio
import os
import re
import json
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_URL = "https://t.me/your_game_group"

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY,
                  name TEXT,
                  mention TEXT,
                  total_bet INTEGER DEFAULT 0,
                  total_win INTEGER DEFAULT 0,
                  balance INTEGER DEFAULT 0)''')
    
    # Games table
    c.execute('''CREATE TABLE IF NOT EXISTS games
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER UNIQUE,
                  group_id TEXT,
                  status TEXT,
                  result_number INTEGER,
                  total_bet_amount INTEGER DEFAULT 0,
                  total_win_amount INTEGER DEFAULT 0,
                  owner_profit INTEGER DEFAULT 0,
                  created_at TIMESTAMP,
                  closed_at TIMESTAMP)''')
    
    # Bets table
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER,
                  group_id TEXT,
                  user_id TEXT,
                  bet_number INTEGER,
                  amount INTEGER,
                  status TEXT,
                  win_amount INTEGER DEFAULT 0,
                  timestamp TIMESTAMP)''')
    
    # Admins table
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (group_id TEXT,
                  user_id TEXT,
                  added_by TEXT,
                  added_at TIMESTAMP,
                  PRIMARY KEY (group_id, user_id))''')
    
    # Groups table
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (group_id TEXT PRIMARY KEY,
                  group_name TEXT,
                  group_link TEXT,
                  added_by TEXT,
                  added_at TIMESTAMP)''')
    
    # Game images table
    c.execute('''CREATE TABLE IF NOT EXISTS game_images
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  image_type TEXT,
                  photo_id TEXT,
                  updated_by TEXT,
                  updated_at TIMESTAMP)''')
    
    conn.commit()
    conn.close()

# ==================== IMAGE FUNCTIONS ====================
def save_game_image(image_type, photo_id, updated_by):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO game_images (image_type, photo_id, updated_by, updated_at) VALUES (?, ?, ?, ?)",
              (image_type, photo_id, str(updated_by), datetime.now()))
    conn.commit()
    conn.close()

def get_game_image(image_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT photo_id FROM game_images WHERE image_type = ? ORDER BY updated_at DESC LIMIT 1", (image_type,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def delete_game_image(image_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("DELETE FROM game_images WHERE image_type = ?", (image_type,))
    conn.commit()
    conn.close()

# ==================== BACKUP FUNCTIONS ====================
def create_group_backup(group_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    backup_data = {
        'group_id': group_id,
        'users': [],
        'games': [],
        'bets': [],
        'admins': [],
        'timestamp': datetime.now().isoformat()
    }
    
    c.execute("SELECT group_name, group_link FROM groups WHERE group_id = ?", (str(group_id),))
    group_info = c.fetchone()
    if group_info:
        backup_data['group_name'] = group_info[0]
        backup_data['group_link'] = group_info[1]
    
    c.execute("SELECT DISTINCT user_id FROM bets WHERE group_id = ?", (str(group_id),))
    user_ids = c.fetchall()
    for (uid,) in user_ids:
        c.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
        user = c.fetchone()
        if user:
            backup_data['users'].append({
                'user_id': user[0],
                'name': user[1],
                'mention': user[2],
                'total_bet': user[3],
                'total_win': user[4],
                'balance': user[5]
            })
    
    c.execute("SELECT * FROM games WHERE group_id = ?", (str(group_id),))
    games = c.fetchall()
    for game in games:
        backup_data['games'].append({
            'id': game[0],
            'game_id': game[1],
            'group_id': game[2],
            'status': game[3],
            'result_number': game[4],
            'total_bet_amount': game[5],
            'total_win_amount': game[6],
            'owner_profit': game[7],
            'created_at': str(game[8]),
            'closed_at': str(game[9]) if game[9] else None
        })
    
    c.execute("SELECT * FROM bets WHERE group_id = ?", (str(group_id),))
    bets = c.fetchall()
    for bet in bets:
        backup_data['bets'].append({
            'id': bet[0],
            'game_id': bet[1],
            'group_id': bet[2],
            'user_id': bet[3],
            'bet_number': bet[4],
            'amount': bet[5],
            'status': bet[6],
            'win_amount': bet[7],
            'timestamp': str(bet[8])
        })
    
    c.execute("SELECT user_id FROM admins WHERE group_id = ?", (str(group_id),))
    admins = c.fetchall()
    for (admin_id,) in admins:
        backup_data['admins'].append(admin_id)
    
    conn.close()
    
    filename = f"backup_group_{group_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    
    return filename

def restore_group_backup(group_id, file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    if str(backup_data['group_id']) != str(group_id):
        conn.close()
        return False, "Backup file is for different group"
    
    c.execute("DELETE FROM bets WHERE group_id = ?", (str(group_id),))
    c.execute("DELETE FROM games WHERE group_id = ?", (str(group_id),))
    c.execute("DELETE FROM admins WHERE group_id = ?", (str(group_id),))
    
    for user in backup_data['users']:
        c.execute("INSERT OR REPLACE INTO users (user_id, name, mention, total_bet, total_win, balance) VALUES (?, ?, ?, ?, ?, ?)",
                  (user['user_id'], user['name'], user['mention'], user['total_bet'], user['total_win'], user['balance']))
    
    for game in backup_data['games']:
        c.execute("INSERT INTO games (id, game_id, group_id, status, result_number, total_bet_amount, total_win_amount, owner_profit, created_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (game['id'], game['game_id'], game['group_id'], game['status'], game['result_number'], game['total_bet_amount'], game['total_win_amount'], game['owner_profit'], game['created_at'], game['closed_at']))
    
    for bet in backup_data['bets']:
        c.execute("INSERT INTO bets (id, game_id, group_id, user_id, bet_number, amount, status, win_amount, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (bet['id'], bet['game_id'], bet['group_id'], bet['user_id'], bet['bet_number'], bet['amount'], bet['status'], bet['win_amount'], bet['timestamp']))
    
    for admin_id in backup_data['admins']:
        c.execute("INSERT INTO admins (group_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                  (str(group_id), admin_id, str(OWNER_ID), datetime.now()))
    
    conn.commit()
    conn.close()
    
    return True, f"Restored {len(backup_data['users'])} users, {len(backup_data['games'])} games, {len(backup_data['bets'])} bets"

# ==================== DATABASE FUNCTIONS ====================
def get_next_game_id(group_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT game_id FROM games WHERE group_id = ? ORDER BY game_id DESC LIMIT 1", (str(group_id),))
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
            'total_bet': user[3],
            'total_win': user[4],
            'balance': user[5]
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

def update_user_stats(user_id, bet_amount, win_amount=0):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE users SET total_bet = total_bet + ?, total_win = total_win + ? WHERE user_id = ?",
              (bet_amount, win_amount, str(user_id)))
    conn.commit()
    conn.close()

def get_current_game(group_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM games WHERE group_id = ? AND status = 'open' ORDER BY game_id DESC LIMIT 1", (str(group_id),))
    game = c.fetchone()
    conn.close()
    
    if game:
        return {
            'id': game[0],
            'game_id': game[1],
            'group_id': game[2],
            'status': game[3],
            'result_number': game[4],
            'total_bet_amount': game[5],
            'total_win_amount': game[6],
            'owner_profit': game[7],
            'created_at': game[8],
            'closed_at': game[9]
        }
    return None

def create_game(group_id):
    game_id = get_next_game_id(group_id)
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO games (game_id, group_id, status, total_bet_amount, total_win_amount, owner_profit, created_at) VALUES (?, ?, 'open', 0, 0, 0, ?)",
              (game_id, str(group_id), datetime.now()))
    conn.commit()
    conn.close()
    return game_id

def close_game(group_id, game_id, result_number, total_win_amount, owner_profit):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE games SET status = 'closed', result_number = ?, total_win_amount = ?, owner_profit = ?, closed_at = ? WHERE group_id = ? AND game_id = ?",
              (result_number, total_win_amount, owner_profit, datetime.now(), str(group_id), game_id))
    conn.commit()
    conn.close()

def save_bet(group_id, game_id, user_id, bet_number, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (game_id, group_id, user_id, bet_number, amount, status, timestamp) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
              (game_id, str(group_id), str(user_id), bet_number, amount, datetime.now()))
    
    c.execute("UPDATE games SET total_bet_amount = total_bet_amount + ? WHERE group_id = ? AND game_id = ?",
              (amount, str(group_id), game_id))
    
    conn.commit()
    conn.close()
    update_user_stats(user_id, amount, 0)

def get_game_bets(group_id, game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE group_id = ? AND game_id = ?", (str(group_id), game_id))
    bets = c.fetchall()
    conn.close()
    
    result = []
    for bet in bets:
        user = get_user(bet[3])
        result.append({
            'id': bet[0],
            'game_id': bet[1],
            'group_id': bet[2],
            'user_id': bet[3],
            'bet_number': bet[4],
            'amount': bet[5],
            'status': bet[6],
            'win_amount': bet[7],
            'user_name': user['name'] if user else 'Unknown'
        })
    return result

def update_bet_results(group_id, game_id, result_number):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE group_id = ? AND game_id = ?", (str(group_id), game_id))
    bets = c.fetchall()
    
    winners = []
    total_win_amount = 0
    
    for bet in bets:
        if bet[4] == result_number:
            win_amount = bet[5] * result_number
            c.execute("UPDATE bets SET status = 'won', win_amount = ? WHERE id = ?", (win_amount, bet[0]))
            winners.append(bet)
            total_win_amount += win_amount
            
            user_id = bet[3]
            c.execute("UPDATE users SET total_win = total_win + ? WHERE user_id = ?", (win_amount, str(user_id)))
        else:
            c.execute("UPDATE bets SET status = 'lost', win_amount = 0 WHERE id = ?", (bet[0],))
    
    conn.commit()
    conn.close()
    return winners, total_win_amount

def get_user_bets(user_id, group_id=None, game_id=None):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    if group_id and game_id:
        c.execute("SELECT * FROM bets WHERE user_id = ? AND group_id = ? AND game_id = ?", 
                  (str(user_id), str(group_id), game_id))
    elif group_id:
        c.execute("SELECT * FROM bets WHERE user_id = ? AND group_id = ? ORDER BY timestamp DESC LIMIT 10", 
                  (str(user_id), str(group_id)))
    else:
        c.execute("SELECT * FROM bets WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (str(user_id),))
    bets = c.fetchall()
    conn.close()
    return bets

def get_user_bet_count_for_game(user_id, group_id, game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id = ? AND group_id = ? AND game_id = ?",
              (str(user_id), str(group_id), game_id))
    count = c.fetchone()[0]
    conn.close()
    return count

# ==================== ADMIN FUNCTIONS ====================
def is_admin(group_id, user_id):
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM admins WHERE group_id = ? AND user_id = ?", (str(group_id), str(user_id)))
    result = c.fetchone()
    conn.close()
    return result is not None

def is_owner(user_id):
    return user_id == OWNER_ID

def add_admin(group_id, user_id, added_by):
    if added_by != OWNER_ID:
        return False, "Only main owner can add admins"
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO admins (group_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                  (str(group_id), str(user_id), str(added_by), datetime.now()))
        conn.commit()
        conn.close()
        return True, "Admin added successfully"
    except:
        conn.close()
        return False, "Failed to add admin"

def remove_admin(group_id, user_id, removed_by):
    if removed_by != OWNER_ID:
        return False, "Only main owner can remove admins"
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE group_id = ? AND user_id = ?", (str(group_id), str(user_id)))
    conn.commit()
    conn.close()
    return True, "Admin removed successfully"

def get_group_admins(group_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins WHERE group_id = ?", (str(group_id),))
    admins = c.fetchall()
    conn.close()
    return [admin[0] for admin in admins]

def get_all_groups():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT group_id, group_name, group_link, added_by FROM groups")
    groups = c.fetchall()
    conn.close()
    return groups

def add_group(group_id, group_name, group_link, added_by):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO groups (group_id, group_name, group_link, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
              (str(group_id), group_name, group_link, str(added_by), datetime.now()))
    conn.commit()
    conn.close()

# ==================== UTILITY FUNCTIONS ====================
def parse_bet(text):
    text = text.lower().strip()
    patterns = [
        (r'^1 (\d+)$', 1), (r'^2 (\d+)$', 2), (r'^3 (\d+)$', 3),
        (r'^4 (\d+)$', 4), (r'^5 (\d+)$', 5), (r'^6 (\d+)$', 6),
    ]
    for pattern, number in patterns:
        match = re.match(pattern, text)
        if match:
            return number, int(match.group(1))
    return None, None

# ==================== BUTTONS ====================
def get_deposit_withdraw_buttons(admin_username="osamu1123"):
    keyboard = [
        [
            InlineKeyboardButton("💰 ငွေသွင်း", url=f"https://t.me/{admin_username}"),
            InlineKeyboardButton("💸 ငွေထုတ်", url=f"https://t.me/{admin_username}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_warning_text():
    return "⚠️ **သတိပေးချက်** ⚠️\n\nငွေသွင်းငွေထုတ်ရန်အတွက် တရားဝင်အကောင့်မှလွဲ၍ အခြားအကောင့်များသည် လူလိမ်များဖြစ်ကြပါသည်။\nUsername ကိုသေချာစစ်ဆေးပါ။"

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: {user.id} in {chat.id} ({chat.type})")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    if chat.type in ['group', 'supergroup']:
        group_link = f"https://t.me/{chat.username}" if chat.username else None
        add_group(chat.id, chat.title, group_link, user.id)
        
        if is_admin(chat.id, user.id):
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text="👑 **Admin ထိန်းချုပ်ခန်း**\n\n"
                     "ဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။\n\n"
                     "**ငွေသွင်း/ထုတ်ရန်:** User စာကို Reply လုပ်ပြီး +5000 (သို့) -2000 ရိုက်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            text = "🎲 **ကစားနည်းအသစ်**\n\n" \
                   "နံပါတ်ရွေးပြီး လောင်းကြေးတင်ပါ။\n\n" \
                   "ဥပမာ:\n" \
                   "`1 1000` - နံပါတ် 1 ကို 1000 ကျပ်လောင်း\n" \
                   "`2 500` - နံပါတ် 2 ကို 500 ကျပ်လောင်း\n" \
                   "`3 200` - နံပါတ် 3 ကို 200 ကျပ်လောင်း\n\n" \
                   "အနည်းဆုံး ၂၀၀ကျပ်\n" \
                   "အများဆုံး ၁၀၀၀ကျပ်\n" \
                   "တစ်ယောက် တစ်ခါသာလောင်းရမည်\n\n" \
                   "**သတိပြုရန်:** Bot ရဲ့စာကို Reply လုပ်ပြီးမှသာ လောင်းကြေးတင်ရမည်။"
            
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown'
            )
        return
    
    if chat.type == 'private':
        if user.id == OWNER_ID:
            keyboard = [
                [InlineKeyboardButton("👥 Group များစာရင်း", callback_data='list_groups')],
                [InlineKeyboardButton("➕ Admin ထည့်ရန်", callback_data='add_admin')],
                [InlineKeyboardButton("➖ Admin ဖြုတ်ရန်", callback_data='remove_admin')],
                [InlineKeyboardButton("📋 Admin စာရင်း", callback_data='list_admins')],
                [InlineKeyboardButton("🖼️ Game Start ပုံထည့်", callback_data='set_start_image')],
                [InlineKeyboardButton("🖼️ Game Stop ပုံထည့်", callback_data='set_stop_image')],
                [InlineKeyboardButton("🖼️ Result ပုံထည့်", callback_data='set_result_image')],
                [InlineKeyboardButton("🗑️ ပုံဖျက်ရန်", callback_data='delete_images')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **Main Owner ထိန်းချုပ်ခန်း**\n\n"
                "အောက်ပါခလုတ်များကိုနှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🎲 ကစားရန်", url=GAME_GROUP_URL)]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "ကစားရန် Game Group ကိုသွားပါ။",
                reply_markup=reply_markup
            )

async def show_groups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = get_all_groups()
    
    if not groups:
        msg = "❌ Group မရှိပါသေးပါ။ Bot ကို Group ထဲဦးစွာထည့်ပါ။"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return
    
    for group_id, group_name, group_link, added_by in groups:
        admin_count = len(get_group_admins(group_id))
        added_user = get_user(added_by)
        added_mention = added_user['mention'] if added_user else "Unknown"
        
        text = f"**Group:** {group_name}\n"
        text += f"**ID:** `{group_id}`\n"
        if group_link:
            text += f"**Link:** [သွားရန်]({group_link})\n"
        text += f"**Admin အရေအတွက်:** {admin_count} ဦး\n"
        text += f"**ထည့်သွင်းသူ:** {added_mention}\n"
        
        keyboard = [
            [
                InlineKeyboardButton("💾 Backup", callback_data=f'backup_group_{group_id}'),
                InlineKeyboardButton("🔄 Restore", callback_data=f'restore_group_{group_id}')
            ],
            [InlineKeyboardButton("👥 Admin များ", callback_data=f'list_group_admins_{group_id}')],
            [
                InlineKeyboardButton("➕ Admin ထည့်", callback_data=f'add_group_admin_{group_id}'),
                InlineKeyboardButton("➖ Admin ဖြုတ်", callback_data=f'remove_group_admin_{group_id}')
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    print(f"CALLBACK: {data} from {user.id}")
    
    if user.id != OWNER_ID:
        await query.answer("Main Owner အတွက်သာဖြစ်ပါသည်", show_alert=True)
        return
    
    # Image settings
    if data == 'set_start_image':
        await query.answer()
        await query.edit_message_text(
            "🖼️ **Game Start အတွက်ပုံထည့်ရန်**\n\n"
            "ပုံကိုပို့ပါ။ ဤပုံသည် ဂိမ်းစတင်တိုင်းတွင်ပါမည်။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_start'
    
    elif data == 'set_stop_image':
        await query.answer()
        await query.edit_message_text(
            "🖼️ **Game Stop အတွက်ပုံထည့်ရန်**\n\n"
            "ပုံကိုပို့ပါ။ ဤပုံသည် ဂိမ်းပိတ်တိုင်းတွင်ပါမည်။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_stop'
    
    elif data == 'set_result_image':
        await query.answer()
        await query.edit_message_text(
            "🖼️ **Result အတွက်ပုံထည့်ရန်**\n\n"
            "ပုံကိုပို့ပါ။ ဤပုံသည် ရလဒ်ထုတ်တိုင်းတွင်ပါမည်။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_result'
    
    elif data == 'delete_images':
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🎮 Game Start ပုံဖျက်", callback_data='del_start')],
            [InlineKeyboardButton("⏹️ Game Stop ပုံဖျက်", callback_data='del_stop')],
            [InlineKeyboardButton("🎲 Result ပုံဖျက်", callback_data='del_result')],
            [InlineKeyboardButton("« နောက်သို့", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🗑️ **ဖျက်လိုသောပုံကိုရွေးပါ**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data == 'del_start':
        delete_game_image('game_start')
        await query.answer("✅ Game Start ပုံဖျက်ပြီးပါပြီ")
        await query.edit_message_text("✅ Game Start ပုံဖျက်ပြီးပါပြီ")
    
    elif data == 'del_stop':
        delete_game_image('game_stop')
        await query.answer("✅ Game Stop ပုံဖျက်ပြီးပါပြီ")
        await query.edit_message_text("✅ Game Stop ပုံဖျက်ပြီးပါပြီ")
    
    elif data == 'del_result':
        delete_game_image('game_result')
        await query.answer("✅ Result ပုံဖျက်ပြီးပါပြီ")
        await query.edit_message_text("✅ Result ပုံဖျက်ပြီးပါပြီ")
    
    elif data == 'back_to_main':
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("👥 Group များစာရင်း", callback_data='list_groups')],
            [InlineKeyboardButton("➕ Admin ထည့်ရန်", callback_data='add_admin')],
            [InlineKeyboardButton("➖ Admin ဖြုတ်ရန်", callback_data='remove_admin')],
            [InlineKeyboardButton("📋 Admin စာရင်း", callback_data='list_admins')],
            [InlineKeyboardButton("🖼️ Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼️ Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼️ Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton("🗑️ ပုံဖျက်ရန်", callback_data='delete_images')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👑 **Main Owner ထိန်းချုပ်ခန်း**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # Backup group
    elif data.startswith('backup_group_'):
        group_id = data.replace('backup_group_', '')
        await query.answer()
        
        current_game = get_current_game(group_id)
        if current_game:
            await query.message.reply_text("❌ ဤအုပ်စုတွင် ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Backup လုပ်ပါ။")
            return
        
        filename = create_group_backup(group_id)
        
        with open(filename, 'rb') as f:
            await context.bot.send_document(
                chat_id=user.id,
                document=f,
                filename=filename,
                caption=f"✅ **Backup အောင်မြင်ပါသည်**\n\nGroup: {group_id}\nရက်စွဲ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
        
        os.remove(filename)
        await query.message.reply_text("✅ Backup ပြီးပါပြီ။ ဖိုင်ကို လက်ခံရရှိပါမည်။")
    
    # Restore group
    elif data.startswith('restore_group_'):
        group_id = data.replace('restore_group_', '')
        await query.answer()
        
        current_game = get_current_game(group_id)
        if current_game:
            await query.message.reply_text("❌ ဤအုပ်စုတွင် ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Restore လုပ်ပါ။")
            return
        
        await query.message.reply_text(
            f"🔄 **Restore လုပ်ရန် Backup ဖိုင်ကို ပို့ပါ**\n\n"
            f"Group ID: `{group_id}`\n\n"
            f"ဤအုပ်စုအတွက် JSON ဖိုင်ကိုသာ ပို့ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_restore'] = group_id
    
    # List group admins
    elif data.startswith('list_group_admins_'):
        group_id = data.replace('list_group_admins_', '')
        await query.answer()
        
        admins = get_group_admins(group_id)
        group_info = get_all_groups()
        group_name = "Unknown"
        for gid, name, _, _ in group_info:
            if str(gid) == str(group_id):
                group_name = name
                break
        
        if admins:
            text = f"**{group_name}** အုပ်စုရှိ Admin များ\n\n"
            for admin_id in admins:
                user_data = get_user(admin_id)
                admin_name = user_data['name'] if user_data else "Unknown"
                admin_mention = user_data['mention'] if user_data else "Unknown"
                text += f"• {admin_name}\n  ID: `{admin_id}`\n  Mention: {admin_mention}\n\n"
        else:
            text = f"**{group_name}** အုပ်စုတွင် Admin မရှိပါ။"
        
        await query.message.reply_text(text, parse_mode='Markdown')
    
    # Add group admin
    elif data.startswith('add_group_admin_'):
        group_id = data.replace('add_group_admin_', '')
        await query.answer()
        
        await query.message.reply_text(
            f"➕ **Admin ထည့်ရန်**\n\n"
            f"Group ID: `{group_id}`\n\n"
            f"Admin လုပ်မည့် User ID ကိုရိုက်ထည့်ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['adding_admin'] = group_id
    
    # Remove group admin
    elif data.startswith('remove_group_admin_'):
        group_id = data.replace('remove_group_admin_', '')
        await query.answer()
        
        admins = get_group_admins(group_id)
        if not admins:
            await query.message.reply_text("❌ ဤအုပ်စုတွင် Admin မရှိပါ။")
            return
        
        text = f"➖ **Admin ဖြုတ်ရန်**\n\nGroup ID: `{group_id}`\n\nဖြုတ်မည့် Admin ID ကိုရိုက်ထည့်ပါ:\n"
        for admin_id in admins:
            user_data = get_user(admin_id)
            admin_name = user_data['name'] if user_data else "Unknown"
            text += f"• {admin_name} - `{admin_id}`\n"
        
        await query.message.reply_text(text, parse_mode='Markdown')
        context.user_data['removing_admin'] = group_id
    
    # Game control (for group admins)
    elif data in ['game_start', 'game_stop'] and update.callback_query.message.chat.type in ['group', 'supergroup']:
        group_id = str(update.callback_query.message.chat.id)
        
        if not is_admin(group_id, user.id):
            await query.answer("သင်သည် ဤအုပ်စုတွင် Admin မဟုတ်ပါ", show_alert=True)
            return
        
        await query.answer()
        
        if data == 'game_start':
            current_game = get_current_game(group_id)
            if current_game:
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ")
                return
            
            game_id = create_game(group_id)
            
            custom_image = get_game_image('game_start')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=group_id,
                    photo=custom_image,
                    caption=f"🎲 **ပွဲစဉ်အသစ်** - `{game_id}`\n\n"
                            f"နံပါတ် ၁ မှ ၆ ထိရွေးချယ်လောင်းနိုင်ပါသည်။\n"
                            f"တစ်ယောက် တစ်ခါသာလောင်းရမည်။\n"
                            f"အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်",
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"🎲 **ပွဲစဉ်အသစ်** - `{game_id}`\n\n"
                         f"နံပါတ် ၁ မှ ၆ ထိရွေးချယ်လောင်းနိုင်ပါသည်။\n"
                         f"တစ်ယောက် တစ်ခါသာလောင်းရမည်။\n"
                         f"အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်",
                    parse_mode='Markdown'
                )
            
            await context.bot.send_message(
                chat_id=group_id,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
        
        elif data == 'game_stop':
            game = get_current_game(group_id)
            if not game:
                await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
                return
            
            game_id = game['game_id']
            bets = get_game_bets(group_id, game_id)
            
            # Create bet list text
            bet_text = f"🎲 **ပွဲစဉ်** ➖ `{game_id}`\n"
            bet_text += f"➖ **လောင်းကြေးပိတ်ပါပြီ** ➖\n\n"
            
            if bets:
                total_bet = 0
                for bet in bets:
                    bet_text += f"👤 {bet['user_name']} ➖ နံပါတ် {bet['bet_number']} - {bet['amount']:,} ကျပ်\n"
                    total_bet += bet['amount']
                bet_text += f"\n**စုစုပေါင်းလောင်းငွေ:** {total_bet:,} ကျပ်"
            else:
                bet_text += "❌ လောင်းကြေးမရှိပါ\n"
            
            custom_image = get_game_image('game_stop')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=group_id,
                    photo=custom_image,
                    caption=bet_text,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=bet_text,
                    parse_mode='Markdown'
                )
            
            await context.bot.send_message(
                chat_id=group_id,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Prepare for dice
            await asyncio.sleep(1)
            dice_msg = await context.bot.send_message(
                chat_id=group_id,
                text="🎲 **အံစာတုံး စလှည့်ပါတော့မယ်...**\n\nခဏစောင့်ပါ။",
                parse_mode='Markdown'
            )
            await asyncio.sleep(2)
            await dice_msg.delete()
            
            # Store game ID
            if 'group_games' not in context.chat_data:
                context.chat_data['group_games'] = {}
            context.chat_data['group_games'][group_id] = game_id
            
            # Bot sends dice (one dice only)
            await context.bot.send_dice(chat_id=group_id, emoji='🎲')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id} in {chat.id}")
    
    # ===== PRIVATE CHAT - Owner only =====
    if chat.type == 'private' and user.id == OWNER_ID:
        if 'awaiting_image' in context.user_data:
            if update.message.photo:
                image_type = context.user_data['awaiting_image']
                photo_id = update.message.photo[-1].file_id
                save_game_image(image_type, photo_id, user.id)
                
                image_names = {
                    'game_start': 'Game Start',
                    'game_stop': 'Game Stop',
                    'game_result': 'Result'
                }
                
                await update.message.reply_text(f"✅ {image_names.get(image_type, '')} ပုံထည့်ပြီးပါပြီ")
                del context.user_data['awaiting_image']
            else:
                await update.message.reply_text("❌ ပုံကိုသာ ပို့ပါ။")
            return
        
        if 'adding_admin' in context.user_data:
            try:
                admin_id = int(text.strip())
                group_id = context.user_data['adding_admin']
                
                success, message = add_admin(group_id, admin_id, user.id)
                await update.message.reply_text(f"✅ {message}")
                
                if success:
                    try:
                        group_info = get_all_groups()
                        group_name = "Unknown"
                        for gid, name, _, _ in group_info:
                            if str(gid) == str(group_id):
                                group_name = name
                                break
                        
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=f"✅ သင့်အား **{group_name}** အုပ်စုတွင် Admin အဖြစ်ခန့်အပ်လိုက်ပါသည်။\n\nဂိမ်းစတင်/ပိတ်ခွင့်ရရှိပါမည်။",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                
            except ValueError:
                await update.message.reply_text("❌ User ID ဂဏန်းထည့်ပါ")
            
            del context.user_data['adding_admin']
            return
        
        if 'removing_admin' in context.user_data:
            try:
                admin_id = int(text.strip())
                group_id = context.user_data['removing_admin']
                
                success, message = remove_admin(group_id, admin_id, user.id)
                await update.message.reply_text(f"✅ {message}")
                
            except ValueError:
                await update.message.reply_text("❌ User ID ဂဏန်းထည့်ပါ")
            
            del context.user_data['removing_admin']
            return
        
        if 'awaiting_restore' in context.user_data:
            if update.message.document:
                file = await update.message.document.get_file()
                file_path = f"restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                await file.download_to_drive(file_path)
                
                group_id = context.user_data['awaiting_restore']
                
                try:
                    success, message = restore_group_backup(group_id, file_path)
                    if success:
                        await update.message.reply_text(f"✅ {message}")
                    else:
                        await update.message.reply_text(f"❌ {message}")
                except Exception as e:
                    await update.message.reply_text(f"❌ Restore failed: {str(e)}")
                
                os.remove(file_path)
                del context.user_data['awaiting_restore']
            else:
                await update.message.reply_text("❌ JSON ဖိုင်ကိုသာ ပို့ပါ။")
            return
        
        return
    
    # ===== GROUP CHAT =====
    if chat.type in ['group', 'supergroup']:
        group_id = str(chat.id)
        game = get_current_game(group_id)
        
        # Check if this is a deposit/withdraw command from admin
        if is_admin(group_id, user.id) and update.message.reply_to_message:
            replied = update.message.reply_to_message
            
            target_user = replied.from_user
            target_user_id = target_user.id
            
            if target_user.id == context.bot.id:
                match = re.search(r'ID[ -]+`?(\d+)`?', replied.text)
                if match:
                    target_user_id = int(match.group(1))
            
            user_data = get_user(target_user_id)
            if not user_data:
                await update.message.reply_text("❌ User ID မတွေ့ပါ။ User က bot ကို /start လုပ်ထားဖို့လိုပါတယ်။")
                return
            
            if text.startswith('+'):
                try:
                    amount = int(text[1:])
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'add')
                    
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
                    
                    await update.message.reply_text(
                        f"✅ {user_data['name']} ထံသို့ {amount:,} ကျပ်ထည့်ပြီးပါပြီ"
                    )
                    
                    await context.bot.send_message(
                        chat_id=group_id,
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
                    
                    await update.message.reply_text(
                        f"✅ {user_data['name']} ထံမှ {amount:,} ကျပ်ထုတ်ပြီးပါပြီ"
                    )
                    
                    await context.bot.send_message(
                        chat_id=group_id,
                        text=f"🧊 {user_data['name']} ထုတ်ယူငွေ {amount:,} ကျပ်ကို လွဲပေးပြီးပါပြီ။"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            
            return
        
        # User info request
        if text == "3":
            user_data = get_user(user.id)
            if user_data:
                bets_text = ""
                if game and game['status'] == 'open':
                    user_bets = get_user_bets(user.id, group_id, game['game_id'])
                    if user_bets:
                        bets_text = "\n\n**ယခုလောင်းထားသောငွေများ**\n"
                        for bet in user_bets:
                            bets_text += f"နံပါတ် {bet[4]} - {bet[5]:,} ကျပ်\n"
                
                msg = await update.message.reply_to_message.reply_text(
                    f"**အမည်** - {user_data['name']}\n"
                    f"**ID** - `{user_data['user_id']}`\n"
                    f"**လက်ကျန်ငွေ** - {user_data['balance']:,} ကျပ်"
                    f"{bets_text}",
                    parse_mode='Markdown'
                )
                await asyncio.sleep(5)
                await msg.delete()
            return
        
        # Betting - ONLY if replying to bot's message
        if not update.message.reply_to_message or update.message.reply_to_message.from_user.id != context.bot.id:
            return
        
        if not game or game['status'] != 'open':
            return
        
        bet_number, amount = parse_bet(text)
        if bet_number and amount:
            if amount < 200 or amount > 1000:
                await update.message.reply_text("❌ အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်")
                return
            
            # Check if user already bet in this game
            bet_count = get_user_bet_count_for_game(user.id, group_id, game['game_id'])
            if bet_count >= 1:
                await update.message.reply_text("❌ ဤပွဲစဉ်တွင် တစ်ခါထဲသာလောင်းလို့ရပါသည်။")
                return
            
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            save_bet(group_id, game['game_id'], user.id, bet_number, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            await update.message.reply_to_message.reply_text(
                f"🎲 **ပွဲစဉ်** `{game['game_id']}`\n"
                f"➖➖➖➖➖\n"
                f"**နံပါတ် {bet_number}** - {amount} ကျပ်\n"
                f"➖➖➖➖➖\n"
                f"✅ လောင်းကြေးတင်ပြီးပါပြီ\n"
                f"💰 လက်ကျန် {new_balance:,}Ks",
                parse_mode='Markdown'
            )

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.type in ['group', 'supergroup']:
        group_id = str(chat.id)
        
        # Only process dice sent by bot
        if user.id == context.bot.id:
            dice_value = update.message.dice.value
            print(f"🎲 BOT DICE: {dice_value} in group {group_id}")
            
            # Get game_id from context
            if 'group_games' not in context.chat_data:
                print("❌ group_games not found in context")
                return
            
            group_games = context.chat_data['group_games']
            game_id = group_games.get(group_id)
            
            if not game_id:
                print(f"❌ No game found for group {group_id}")
                return
            
            print(f"✅ Found game {game_id} for group {group_id}")
            
            # Get game info
            game = get_current_game(group_id)
            if not game:
                print(f"❌ Game {game_id} not found in database")
                return
            
            print(f"📊 Processing game {game_id} with dice result: {dice_value}")
            
            # Update bet results
            winners, total_win_amount = update_bet_results(group_id, game_id, dice_value)
            
            total_bet_amount = game['total_bet_amount']
            owner_profit = total_bet_amount - total_win_amount
            
            # Close game with results
            close_game(group_id, game_id, dice_value, total_win_amount, owner_profit)
            print(f"✅ Game {game_id} closed")
            
            # Create result text for group
            result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
            result_text += f"💥 **Dice Bot** 💥\n"
            result_text += f"**အံစာတုံးရလဒ်:** {dice_value}\n"
            result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
            
            if winners:
                for bet in winners:
                    win_amount = bet[5] * dice_value
                    user_info = get_user(bet[3])
                    
                    # Update winner's balance
                    new_balance = update_balance(bet[3], win_amount, 'add')
                    prev_balance = new_balance - win_amount
                    
                    result_text += f"👤 {user_info['name']} ➖ နံပါတ် {bet[4]} > {bet[5]:,} x {dice_value} = {win_amount:,} ကျပ်\n"
                    result_text += f"💰 လက်ကျန်: {prev_balance:,} + {win_amount:,} = {new_balance:,}Ks\n\n"
            else:
                result_text += "❌ အနိုင်ရသူမရှိပါ\n"
            
            # Send result to group
            custom_image = get_game_image('game_result')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=chat.id,
                    photo=custom_image,
                    caption=result_text,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=result_text,
                    parse_mode='Markdown'
                )
            
            # Send warning with buttons
            await context.bot.send_message(
                chat_id=chat.id,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Send owner report via DM
            try:
                owner_report = f"📊 **ပွဲစဉ်အစီရင်ခံစာ**\n\n"
                owner_report += f"**ပွဲစဉ်:** `{game_id}`\n"
                owner_report += f"**အုပ်စု:** {chat.title}\n"
                owner_report += f"**အံစာတုံးရလဒ်:** {dice_value}\n"
                owner_report += f"**စုစုပေါင်းလောင်းငွေ:** {total_bet_amount:,} ကျပ်\n"
                owner_report += f"**အနိုင်ငွေပေးချေခဲ့သည်:** {total_win_amount:,} ကျပ်\n"
                owner_report += f"**လက်ကျန်ငွေ (အမြတ်):** {owner_profit:,} ကျပ်\n"
                
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=owner_report,
                    parse_mode='Markdown'
                )
                print(f"📨 Owner report sent")
            except Exception as e:
                print(f"❌ Failed to send owner report: {e}")
            
            # Clean up
            del group_games[group_id]
            print(f"✅ Game {game_id} completed in group {group_id}")

# ==================== MAIN ====================
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Dice.ALL, handle_dice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    
    print("=" * 60)
    print("🤖 NEW DICE GAME BOT STARTED")
    print("=" * 60)
    print(f"👑 MAIN OWNER: {OWNER_ID}")
    print("=" * 60)
    print("✅ NEW GAME RULES:")
    print("   • Bet on numbers 1-6 only")
    print("   • Format: '1 1000', '2 500', etc.")
    print("   • Min 200, Max 1000 per bet")
    print("   • One bet per person per game")
    print("   • Winnings: bet_amount × result_number")
    print("   • Bot sends one dice only")
    print("   • Owner gets profit report in DM")
    print("=" * 60)
    print("🎲 DICE FLOW:")
    print("   1. Admin stops game")
    print("   2. Bot sends dice")
    print("   3. Waits for dice to stop rolling")
    print("   4. Calculates results immediately")
    print("   5. Shows winners and updates balances")
    print("   6. Sends owner report via DM")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
