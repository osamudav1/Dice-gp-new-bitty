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
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1002849045181"))
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
                  user_id TEXT,
                  bet_number INTEGER,
                  amount INTEGER,
                  status TEXT,
                  win_amount INTEGER DEFAULT 0,
                  timestamp TIMESTAMP)''')
    
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
            'result_number': game[3],
            'total_bet_amount': game[4],
            'total_win_amount': game[5],
            'owner_profit': game[6],
            'created_at': game[7],
            'closed_at': game[8]
        }
    return None

def create_game():
    game_id = get_next_game_id()
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO games (game_id, status, total_bet_amount, total_win_amount, owner_profit, created_at) VALUES (?, 'open', 0, 0, 0, ?)",
              (game_id, datetime.now()))
    conn.commit()
    conn.close()
    return game_id

def close_game(game_id, result_number, total_win_amount, owner_profit):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE games SET status = 'closed', result_number = ?, total_win_amount = ?, owner_profit = ?, closed_at = ? WHERE game_id = ?",
              (result_number, total_win_amount, owner_profit, datetime.now(), game_id))
    conn.commit()
    conn.close()

def save_bet(game_id, user_id, bet_number, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (game_id, user_id, bet_number, amount, status, timestamp) VALUES (?, ?, ?, ?, 'pending', ?)",
              (game_id, str(user_id), bet_number, amount, datetime.now()))
    
    c.execute("UPDATE games SET total_bet_amount = total_bet_amount + ? WHERE game_id = ?",
              (amount, game_id))
    
    conn.commit()
    conn.close()
    update_user_stats(user_id, amount, 0)

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
            'game_id': bet[1],
            'user_id': bet[2],
            'bet_number': bet[3],
            'amount': bet[4],
            'status': bet[5],
            'win_amount': bet[6],
            'user_name': user['name'] if user else 'Unknown'
        })
    return result

def update_bet_results(game_id, result_number):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE game_id = ?", (game_id,))
    bets = c.fetchall()
    
    winners = []
    total_win_amount = 0
    
    for bet in bets:
        if bet[3] == result_number:
            win_amount = bet[4] * result_number
            c.execute("UPDATE bets SET status = 'won', win_amount = ? WHERE id = ?", (win_amount, bet[0]))
            winners.append(bet)
            total_win_amount += win_amount
            
            user_id = bet[2]
            c.execute("UPDATE users SET total_win = total_win + ? WHERE user_id = ?", (win_amount, str(user_id)))
        else:
            c.execute("UPDATE bets SET status = 'lost', win_amount = 0 WHERE id = ?", (bet[0],))
    
    conn.commit()
    conn.close()
    return winners, total_win_amount

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

def get_user_bet_count_for_game(user_id, game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bets WHERE user_id = ? AND game_id = ?", (str(user_id), game_id))
    count = c.fetchone()[0]
    conn.close()
    return count

# ==================== BACKUP FUNCTIONS ====================
def create_backup():
    """Create backup of entire database"""
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    backup_data = {
        'users': [],
        'games': [],
        'bets': [],
        'timestamp': datetime.now().isoformat()
    }
    
    c.execute("SELECT * FROM users")
    users = c.fetchall()
    for user in users:
        backup_data['users'].append({
            'user_id': user[0],
            'name': user[1],
            'mention': user[2],
            'total_bet': user[3],
            'total_win': user[4],
            'balance': user[5]
        })
    
    c.execute("SELECT * FROM games")
    games = c.fetchall()
    for game in games:
        backup_data['games'].append({
            'id': game[0],
            'game_id': game[1],
            'status': game[2],
            'result_number': game[3],
            'total_bet_amount': game[4],
            'total_win_amount': game[5],
            'owner_profit': game[6],
            'created_at': str(game[7]),
            'closed_at': str(game[8]) if game[8] else None
        })
    
    c.execute("SELECT * FROM bets")
    bets = c.fetchall()
    for bet in bets:
        backup_data['bets'].append({
            'id': bet[0],
            'game_id': bet[1],
            'user_id': bet[2],
            'bet_number': bet[3],
            'amount': bet[4],
            'status': bet[5],
            'win_amount': bet[6],
            'timestamp': str(bet[7])
        })
    
    conn.close()
    
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    
    return filename

def restore_backup(file_path):
    """Restore database from backup"""
    with open(file_path, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    c.execute("DELETE FROM bets")
    c.execute("DELETE FROM games")
    c.execute("DELETE FROM users")
    
    for user in backup_data['users']:
        c.execute("INSERT INTO users (user_id, name, mention, total_bet, total_win, balance) VALUES (?, ?, ?, ?, ?, ?)",
                  (user['user_id'], user['name'], user['mention'], user['total_bet'], user['total_win'], user['balance']))
    
    for game in backup_data['games']:
        c.execute("INSERT INTO games (id, game_id, status, result_number, total_bet_amount, total_win_amount, owner_profit, created_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (game['id'], game['game_id'], game['status'], game['result_number'], game['total_bet_amount'], game['total_win_amount'], game['owner_profit'], game['created_at'], game['closed_at']))
    
    for bet in backup_data['bets']:
        c.execute("INSERT INTO bets (id, game_id, user_id, bet_number, amount, status, win_amount, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (bet['id'], bet['game_id'], bet['user_id'], bet['bet_number'], bet['amount'], bet['status'], bet['win_amount'], bet['timestamp']))
    
    conn.commit()
    conn.close()
    
    return True, f"Restored {len(backup_data['users'])} users, {len(backup_data['games'])} games, {len(backup_data['bets'])} bets"

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
            InlineKeyboardButton("🎨owner🎨", url=f"https://t.me/{owner_id}"),
            InlineKeyboardButton("💰 Giftway Channel 💰", url=f"https://t.me/addlist/Nl6SQeMQvIUxNGE9"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_warning_text():
    return "အောက်က Gift Way Channel\n\nလေးတွေကိုjoinပေးခဲ့ကြပါဦး။"

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: {user.id} in {chat.id}")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    # GAME GROUP
    if chat.id == GAME_GROUP_ID:
        if user.id == OWNER_ID:
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text="👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
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
    
    # PRIVATE CHAT - Owner only
    if chat.type == 'private' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🖼️ Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼️ Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼️ Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton("🗑️ ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data')],
            [InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\n"
            "အောက်ပါခလုတ်များကိုနှိပ်ပါ။",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    print(f"CALLBACK: {data} from {user.id}")
    
    if user.id != OWNER_ID:
        await query.answer("ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်", show_alert=True)
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
            [InlineKeyboardButton("🖼️ Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼️ Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼️ Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton("🗑️ ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data')],
            [InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # Backup
    elif data == 'backup_data':
        await query.answer()
        
        current_game = get_current_game()
        if current_game:
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Backup လုပ်ပါ။")
            return
        
        filename = create_backup()
        
        with open(filename, 'rb') as f:
            await context.bot.send_document(
                chat_id=user.id,
                document=f,
                filename=filename,
                caption=f"✅ **Backup အောင်မြင်ပါသည်**\n\nရက်စွဲ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
        
        os.remove(filename)
        await query.message.reply_text("✅ Backup ပြီးပါပြီ။ ဖိုင်ကို လက်ခံရရှိပါမည်။")
    
    # Restore
    elif data == 'restore_data':
        await query.answer()
        
        current_game = get_current_game()
        if current_game:
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှသာ Restore လုပ်ပါ။")
            return
        
        await query.message.reply_text(
            f"🔄 **Restore လုပ်ရန် Backup ဖိုင်ကို ပို့ပါ**\n\n"
            f"JSON ဖိုင်ကိုသာ ပို့ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_restore'] = True
    
    # Game control (in group)
    elif data in ['game_start', 'game_stop'] and update.callback_query.message.chat.id == GAME_GROUP_ID:
        if user.id != OWNER_ID:
            await query.answer("ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်", show_alert=True)
            return
        
        await query.answer()
        
        if data == 'game_start':
            current_game = get_current_game()
            if current_game:
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ")
                return
            
            game_id = create_game()
            
            custom_image = get_game_image('game_start')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=GAME_GROUP_ID,
                    photo=custom_image,
                    caption=f"🎲 ပွဲစဉ်အသစ် - {game_id}\n\n"
                            f"နံပါတ် ၁ မှ ၆ ထိရွေးချယ်လောင်းနိုင်ပါသည်။\n"
                            f"တစ်ယောက် တစ်ခါသာလောင်းနိုင်ပါသည်။\n"
                            f"အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်",
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=f"🎲 ပွဲစဉ်အသစ် - `{game_id}`\n\n"
                         f"နံပါတ် ၁ မှ ၆ ထိရွေးချယ်လောင်းနိုင်ပါသည်။\n"
                         f"တစ်ယောက် တစ်ခါသာလောင်းနိုင်ပါသည်။\n"
                         f"အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်",
                    parse_mode='Markdown'
                )
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
        
        elif data == 'game_stop':
            game = get_current_game()
            if not game:
                await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
                return
            
            game_id = game['game_id']
            bets = get_game_bets(game_id)
            
            # Create bet list text
            bet_text = f"🎲 ပွဲစဉ် ➖ `{game_id}`\n"
            bet_text += f"➖ လောင်းကြေးပိတ်ပါပြီ ➖\n\n"
            
            if bets:
                total_bet = 0
                for bet in bets:
                    bet_text += f"👤 {bet['user_name']} ➖ နံပါတ် {bet['bet_number']} - {bet['amount']:,} ကျပ်\n"
                    total_bet += bet['amount']
                bet_text += f"\nစုစုပေါင်းလောင်းငွေ: {total_bet:,} ကျပ်"
            else:
                bet_text += "😢 လောင်းကြေးမရှိပါ 😢\n"
            
            custom_image = get_game_image('game_stop')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=GAME_GROUP_ID,
                    photo=custom_image,
                    caption=bet_text,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=bet_text,
                    parse_mode='Markdown'
                )
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Ask owner to send dice
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text="🎲 pls Send Owner\n\n"
                     "ကျေးဇူးပြု၍ အံစာတုံး ၁ တုံး ပို့ပေးပါ။\n"
                     "wait ...",
                parse_mode='Markdown'
            )
            
            # Store game ID in bot_data
            context.bot_data['current_game_id'] = game_id
            context.bot_data['awaiting_dice'] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id} in {chat.id}")
    
    # ===== PRIVATE CHAT - Owner only =====
    if chat.type == 'private' and user.id == OWNER_ID:
        # Handle restore file upload
        if 'awaiting_restore' in context.user_data:
            if update.message.document:
                file = await update.message.document.get_file()
                file_path = f"restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                await file.download_to_drive(file_path)
                
                try:
                    success, message = restore_backup(file_path)
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
        
        # Handle image uploads
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
        
        return
    
    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        game = get_current_game()
        
        # Check if this is a deposit/withdraw command from owner
        if user.id == OWNER_ID and update.message.reply_to_message:
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
                        f"✅ {user_data['mention']} ထံသို့ {amount:,} ကျပ်ထည့်ပြီးပါပြီ"
                    )
                    
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"👤 {user_data['mention']} အကောင့်ထဲသို့\n {amount:,} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။\n🎲 ဂိမ်းစတင်ကစားနိုင်ပါပြီ။"
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
                        chat_id=GAME_GROUP_ID,
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
                    user_bets = get_user_bets(user.id, game['game_id'])
                    if user_bets:
                        bets_text = "\n\n**ယခုလောင်းထားသောငွေများ**\n"
                        for bet in user_bets:
                            bets_text += f"နံပါတ် {bet[3]} - {bet[4]:,} ကျပ်\n"
                
                msg = await update.message.reply_to_message.reply_text(
                    f"အမည် - {user_data['name']}\n"
                    f"ID - `{user_data['user_id']}`\n"
                    f"လက်ကျန်ငွေ - {user_data['balance']:,} ကျပ်"
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
            bet_count = get_user_bet_count_for_game(user.id, game['game_id'])
            if bet_count >= 1:
                await update.message.reply_text("❌ ဤပွဲစဉ်တွင် တစ်ခါထဲသာလောင်းလို့ရပါသည်။")
                return
            
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            save_bet(game['game_id'], user.id, bet_number, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            await update.message.reply_to_message.reply_text(
                f"🎲 ပွဲစဉ် 🎲 {game['game_id']}\n"
                f"➖➖➖➖➖➖\n"
                f" နံပါတ် {bet_number}** - {amount} ကျပ်\n"
                f"➖➖➖➖➖➖\n"
                f"✅ လောင်းကြေးတင်ပြီးပါပြီ\n"
                f"💰 လက်ကျန် {new_balance:,}Ks",
                parse_mode='Markdown'
            )

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if chat.id == GAME_GROUP_ID:
        # Only process dice sent by owner
        if user.id == OWNER_ID and update.message.dice:
            # Check if we're expecting dice
            if not context.bot_data.get('awaiting_dice'):
                await update.message.reply_text("❌ ယခုအချိန်တွင် အံစာတုံးမလိုအပ်ပါ။")
                return
            
            dice_value = update.message.dice.value
            print(f"🎲 OWNER DICE: {dice_value} in game group")
            
            # Get game_id from bot_data
            game_id = context.bot_data.get('current_game_id')
            
            if not game_id:
                print(f"❌ No current game found")
                await update.message.reply_text("❌ ဂိမ်းမရှိပါ။")
                return
            
            print(f"✅ Found game {game_id}")
            
            # Get game info
            game = get_current_game()
            if not game:
                print(f"❌ Game {game_id} not found in database")
                return
            
            print(f"📊 Processing game {game_id} with dice result: {dice_value}")
            
            # Update bet results
            winners, total_win_amount = update_bet_results(game_id, dice_value)
            
            total_bet_amount = game['total_bet_amount']
            owner_profit = total_bet_amount - total_win_amount
            
            # Close game with results
            close_game(game_id, dice_value, total_win_amount, owner_profit)
            print(f"✅ Game {game_id} closed")
            
            # Create result text for group
            result_text = f"🎉 ပွဲစဉ်ရလဒ် ➖ {game_id}\n\n"
            result_text += f"💥 Dice Bot 💥\n"
            result_text += f"အံစာတုံးရလဒ်: {dice_value}\n"
            result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
            
            if winners:
                for bet in winners:
                    win_amount = bet[4] * dice_value
                    user_info = get_user(bet[2])
                    
                    # Update winner's balance
                    new_balance = update_balance(bet[2], win_amount, 'add')
                    prev_balance = new_balance - win_amount
                    
                    result_text += f"👤 {user_info['name']} ➖ နံပါတ် {bet[3]} > {bet[4]:,} x {dice_value} = {win_amount:,} ကျပ်\n"
                    result_text += f"💰 လက်ကျန်: {prev_balance:,} + {win_amount:,} = {new_balance:,}Ks\n\n"
            else:
                result_text += "❌ အနိုင်ရသူမရှိပါ\n"
            
            # Send result to group
            custom_image = get_game_image('game_result')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=GAME_GROUP_ID,
                    photo=custom_image,
                    caption=result_text,
                    parse_mode='Markdown'
                )
            else:
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=result_text,
                    parse_mode='Markdown'
                )
            
            # Send warning with buttons
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Send owner report via DM
            try:
                owner_report = f"📊 ပွဲစဉ်အစီရင်ခံစာ**\n\n"
                owner_report += f"ပွဲစဉ်: `{game_id}`\n"
                owner_report += f"အံစာတုံးရလဒ်: {dice_value}\n"
                owner_report += f"စုစုပေါင်းလောင်းငွေ: {total_bet_amount:,} ကျပ်\n"
                owner_report += f"အနိုင်ငွေပေးချေခဲ့သည်: {total_win_amount:,} ကျပ်\n"
                owner_report += f"လက်ကျန်ငွေ (အမြတ်): {owner_profit:,} ကျပ်\n"
                
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=owner_report,
                    parse_mode='Markdown'
                )
                print(f"📨 Owner report sent")
            except Exception as e:
                print(f"❌ Failed to send owner report: {e}")
            
            # Clear current game
            context.bot_data['current_game_id'] = None
            context.bot_data['awaiting_dice'] = False
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
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    
    print("=" * 60)
    print("🎲 DICE GAME BOT STARTED")
    print("=" * 60)
    print(f"👑 OWNER: {OWNER_ID}")
    print(f"🎮 GAME GROUP: {GAME_GROUP_ID}")
    print("=" * 60)
    print("✅ GAME RULES:")
    print("   • Bet on numbers 1-6")
    print("   • Format: '1 1000', '2 500'")
    print("   • Min 200, Max 1000")
    print("   • One bet per person per game")
    print("   • Winnings: bet × result")
    print("=" * 60)
    print("🎲 DICE FLOW:")
    print("   1. Owner starts game")
    print("   2. Users bet")
    print("   3. Owner stops game")
    print("   4. Bot asks owner to send dice")
    print("   5. Owner sends dice")
    print("   6. Bot calculates results")
    print("   7. Shows winners and updates balances")
    print("   8. Sends owner report via DM")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
