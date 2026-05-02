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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1002849045181"))

MIN_BET = 50
MAX_BET = 1000

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY,
                  name TEXT,
                  mention TEXT,
                  total_bet INTEGER DEFAULT 0,
                  total_win INTEGER DEFAULT 0,
                  balance INTEGER DEFAULT 0)''')
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
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER,
                  user_id TEXT,
                  bet_number INTEGER,
                  amount INTEGER,
                  status TEXT,
                  win_amount INTEGER DEFAULT 0,
                  timestamp TIMESTAMP)''')
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

def cancel_bet_db(game_id, user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM bets WHERE game_id = ? AND user_id = ? AND status = 'pending'",
              (game_id, str(user_id)))
    row = c.fetchone()
    total = row[0] if row[0] else 0
    if total == 0:
        conn.close()
        return 0
    c.execute("DELETE FROM bets WHERE game_id = ? AND user_id = ? AND status = 'pending'",
              (game_id, str(user_id)))
    c.execute("UPDATE games SET total_bet_amount = total_bet_amount - ? WHERE game_id = ?",
              (total, game_id))
    conn.commit()
    conn.close()
    return total

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
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    backup_data = {
        'users': [],
        'games': [],
        'bets': [],
        'timestamp': datetime.now().isoformat()
    }
    c.execute("SELECT * FROM users")
    for user in c.fetchall():
        backup_data['users'].append({
            'user_id': user[0], 'name': user[1], 'mention': user[2],
            'total_bet': user[3], 'total_win': user[4], 'balance': user[5]
        })
    c.execute("SELECT * FROM games")
    for game in c.fetchall():
        backup_data['games'].append({
            'id': game[0], 'game_id': game[1], 'status': game[2],
            'result_number': game[3], 'total_bet_amount': game[4],
            'total_win_amount': game[5], 'owner_profit': game[6],
            'created_at': str(game[7]),
            'closed_at': str(game[8]) if game[8] else None
        })
    c.execute("SELECT * FROM bets")
    for bet in c.fetchall():
        backup_data['bets'].append({
            'id': bet[0], 'game_id': bet[1], 'user_id': bet[2],
            'bet_number': bet[3], 'amount': bet[4], 'status': bet[5],
            'win_amount': bet[6], 'timestamp': str(bet[7])
        })
    conn.close()
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(backup_data, f, ensure_ascii=False, indent=2)
    return filename

def restore_backup(file_path):
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

# ==================== UTILITY ====================
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
def get_owner_button():
    keyboard = [
        [InlineKeyboardButton("👑 Owner", url=f"tg://user?id={OWNER_ID}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_warning_text():
    return "အောက်က Gift Way Channel\nလေးတွေကိုjoinပေးခဲ့ကြပါဦး။"

def get_user_game_keyboard():
    keyboard = [
        [
            KeyboardButton("👤 Profile"),
            KeyboardButton("❌ လောင်းကြေးပယ်ဖျက်"),
            KeyboardButton("❓ Help"),
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

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
                [InlineKeyboardButton("🟢 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("🔴 ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text="👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*\n\nဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown',
                quote=True
            )
        else:
            text = (
                "🎲 *ကစားနည်း*\n\n"
                "နံပါတ် ရွေးပြီး လောင်းကြေးတင်ပါ\n"
                "`1 500` ` 2 200` ` 3 50`\n\n"
                f"〰️ အနည်းဆုံး {MIN_BET:,}ကျပ်  အများဆုံး {MAX_BET:,}ကျပ်\n"
                "〰️ တစ်ယောက် တစ်ခါသာ လောင်းနိုင်သည်\n"
                "〰️ Group တွင် တိုက်ရိုက်ရိုက်ပို့နိုင်သည်"
            )
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=get_user_game_keyboard(),
                quote=True
            )
        return

    # PRIVATE CHAT - Owner only
    if chat.type == 'private' and user.id == OWNER_ID:
        keyboard = [
            [InlineKeyboardButton("🖼 Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼 Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼 Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton("🗑 ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data'),
             InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*\n\nအောက်ပါခလုတ်များကိုနှိပ်ပါ။",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ==================== CALLBACK HANDLER ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    print(f"CALLBACK: {data} from {user.id}")

    # ===== OWNER-ONLY CALLBACKS =====
    if user.id != OWNER_ID:
        await query.answer("ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်", show_alert=True)
        return

    # Image settings
    if data == 'set_start_image':
        await query.answer()
        await query.edit_message_text(
            "🖼 *Game Start ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_start'

    elif data == 'set_stop_image':
        await query.answer()
        await query.edit_message_text(
            "🖼 *Game Stop ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_stop'

    elif data == 'set_result_image':
        await query.answer()
        await query.edit_message_text(
            "🖼 *Result ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_image'] = 'game_result'

    elif data == 'delete_images':
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🟢 Game Start ပုံဖျက်", callback_data='del_start')],
            [InlineKeyboardButton("🔴 Game Stop ပုံဖျက်", callback_data='del_stop')],
            [InlineKeyboardButton("🟡 Result ပုံဖျက်", callback_data='del_result')],
            [InlineKeyboardButton("◀️ နောက်သို့", callback_data='back_to_main')]
        ]
        await query.edit_message_text(
            "🗑 *ဖျက်လိုသောပုံကိုရွေးပါ*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    elif data == 'del_start':
        delete_game_image('game_start')
        await query.answer("✅ ဖျက်ပြီး")
        await query.edit_message_text("✅ Game Start ပုံဖျက်ပြီးပါပြီ")

    elif data == 'del_stop':
        delete_game_image('game_stop')
        await query.answer("✅ ဖျက်ပြီး")
        await query.edit_message_text("✅ Game Stop ပုံဖျက်ပြီးပါပြီ")

    elif data == 'del_result':
        delete_game_image('game_result')
        await query.answer("✅ ဖျက်ပြီး")
        await query.edit_message_text("✅ Result ပုံဖျက်ပြီးပါပြီ")

    elif data == 'back_to_main':
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🖼 Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼 Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼 Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton("🗑 ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data'),
             InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        await query.edit_message_text(
            "👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    elif data == 'backup_data':
        await query.answer()
        if get_current_game():
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှ Backup လုပ်ပါ။")
            return
        filename = create_backup()
        with open(filename, 'rb') as f:
            await context.bot.send_document(
                chat_id=user.id,
                document=f,
                filename=filename,
                caption=f"✅ *Backup အောင်မြင်ပါသည်*\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode='Markdown'
            )
        os.remove(filename)
        await query.message.reply_text("✅ Backup ပြီးပါပြီ")

    elif data == 'restore_data':
        await query.answer()
        if get_current_game():
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှ Restore လုပ်ပါ။")
            return
        await query.message.reply_text(
            "🔄 *Restore လုပ်ရန် Backup ဖိုင်ကို ပို့ပါ*\n\nJSON ဖိုင်သာ လက်ခံမည်။",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_restore'] = True

    # Game control (in group)
    elif data in ['game_start', 'game_stop'] and query.message.chat.id == GAME_GROUP_ID:
        await query.answer()

        if data == 'game_start':
            if get_current_game():
                await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ")
                return

            game_id = create_game()

            caption = (
                f"🎲 *ပွဲစဉ်အသစ်* — `{game_id}`\n\n"
                f"နံပါတ် ၁ မှ ၆ ထိ လောင်းနိုင်ပါသည်\n"
                f"တစ်ယောက် နှစ်ကြိမ်အထိ လောင်းနိုင်သည် (မတူသောနံပါတ်)\n"
                f"Min {MIN_BET:,}ကျပ် │ Max {MAX_BET:,}ကျပ်"
            )

            custom_image = get_game_image('game_start')
            if custom_image:
                await context.bot.send_photo(
                    chat_id=GAME_GROUP_ID,
                    photo=custom_image,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=get_user_game_keyboard()
                )
            else:
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text=caption,
                    parse_mode='Markdown',
                    reply_markup=get_user_game_keyboard()
                )

            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=get_warning_text(),
                reply_markup=get_owner_button()
            )

        elif data == 'game_stop':
            game = get_current_game()
            if not game:
                await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
                return

            game_id = game['game_id']
            bets = get_game_bets(game_id)

            bet_text = f"🎲 *ပွဲစဉ်* — `{game_id}`\n➖ လောင်းကြေးပိတ်ပြီ ➖\n\n"
            if bets:
                total_bet = 0
                for bet in bets:
                    bet_text += f"👤 {bet['user_name']} — နံပါတ် {bet['bet_number']} — {bet['amount']:,} ကျပ်\n"
                    total_bet += bet['amount']
                bet_text += f"\n💵 စုစုပေါင်း: {total_bet:,} ကျပ်"
            else:
                bet_text += "😢 လောင်းကြေးမရှိပါ"

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
                reply_markup=get_owner_button()
            )

            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text="🎲 Owner — ကျေးဇူးပြု၍ အံစာတုံး ၁ တုံး ပို့ပေးပါ ⏳",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardRemove()
            )

            context.bot_data['current_game_id'] = game_id
            context.bot_data['awaiting_dice'] = True

# ==================== MESSAGE HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""

    print(f"MESSAGE: {text[:30]} from {user.id} in {chat.id}")

    # ===== PRIVATE CHAT - Owner only =====
    if chat.type == 'private' and user.id == OWNER_ID:
        if 'awaiting_restore' in context.user_data:
            if update.message.document:
                file = await update.message.document.get_file()
                file_path = f"restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                await file.download_to_drive(file_path)
                try:
                    success, message = restore_backup(file_path)
                    await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
                except Exception as e:
                    await update.message.reply_text(f"❌ Restore failed: {str(e)}")
                os.remove(file_path)
                del context.user_data['awaiting_restore']
            else:
                await update.message.reply_text("❌ JSON ဖိုင်ကိုသာ ပို့ပါ။")
            return

        if 'awaiting_image' in context.user_data:
            if update.message.photo:
                image_type = context.user_data['awaiting_image']
                photo_id = update.message.photo[-1].file_id
                save_game_image(image_type, photo_id, user.id)
                names = {'game_start': 'Game Start', 'game_stop': 'Game Stop', 'game_result': 'Result'}
                await update.message.reply_text(f"✅ {names.get(image_type, '')} ပုံထည့်ပြီးပါပြီ")
                del context.user_data['awaiting_image']
            else:
                await update.message.reply_text("❌ ပုံကိုသာ ပို့ပါ။")
            return
        return

    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        game = get_current_game()

        # ===== KEYBOARD BUTTON ACTIONS =====
        if text == "👤 Profile":
            mention = f"@{user.username}" if user.username else user.full_name
            create_or_update_user(user.id, user.full_name, mention)
            user_data = get_user(user.id)
            if not user_data:
                return
            bet_text = ""
            if game:
                user_bets = get_user_bets(user.id, game['game_id'])
                if user_bets:
                    bet_text = "\n\n🎯 ယခုလောင်းထားသောငွေ:\n"
                    for b in user_bets:
                        bet_text += f"  နံပါတ် {b[3]} — {b[4]:,} ကျပ်\n"
            msg = await update.message.reply_text(
                f"👤 *{user_data['name']}*\n"
                f"🆔 `{user_data['user_id']}`\n"
                f"💰 လက်ကျန်: {user_data['balance']:,} ကျပ်\n"
                f"📊 စုစုပေါင်းလောင်း: {user_data['total_bet']:,} ကျပ်\n"
                f"🏆 စုစုပေါင်းနိုင်: {user_data['total_win']:,} ကျပ်"
                f"{bet_text}",
                parse_mode='Markdown',
                quote=True
            )
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass
            return

        if text == "❌ လောင်းကြေးပယ်ဖျက်":
            if not game:
                msg = await update.message.reply_text("❌ ယခုဂိမ်းမရှိပါ", quote=True)
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return
            refund = cancel_bet_db(game['game_id'], user.id)
            if refund == 0:
                msg = await update.message.reply_text("❌ လောင်းကြေးမရှိပါ", quote=True)
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return
            new_balance = update_balance(user.id, refund, 'add')
            msg = await update.message.reply_text(
                f"✅ လောင်းကြေးအားလုံး ပယ်ဖျက်ပြီး\n"
                f"💵 ပြန်ရငွေ: {refund:,} ကျပ်\n"
                f"💰 လက်ကျန်: {new_balance:,} ကျပ်",
                parse_mode='Markdown',
                quote=True
            )
            await asyncio.sleep(8)
            try: await msg.delete()
            except: pass
            return

        if text == "❓ Help":
            msg = await update.message.reply_text(
                "📖 *ကစားနည်း*\n\n"
                "Group ထဲတွင် တိုက်ရိုက်ရိုက်ပို့ပါ\n"
                "`နံပါတ် ငွေပမာဏ`\n\n"
                "ဥပမာ:\n"
                "`1 500` — နံပါတ် 1 ကို 500 ကျပ်\n"
                "`3 200` — နံပါတ် 3 ကို 200 ကျပ်\n"
                "`6 50`  — နံပါတ် 6 ကို 50 ကျပ်\n\n"
                f"〰️ Min: {MIN_BET:,} ကျပ် │ Max: {MAX_BET:,} ကျပ်\n"
                "〰️ တစ်ပွဲ နှစ်ကြိမ်အထိ (မတူသောနံပါတ်) လောင်းနိုင်သည်\n\n"
                "⏱ ဤစာ 10 စက္ကန့်အတွင်း ပျောက်သွားမည်",
                parse_mode='Markdown',
                quote=True
            )
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass
            return

        # Owner deposit/withdraw (must reply to a message)
        if user.id == OWNER_ID and update.message.reply_to_message:
            replied = update.message.reply_to_message
            target_user = replied.from_user
            target_user_id = target_user.id

            if target_user.id == context.bot.id:
                match = re.search(r'ID.*?`?(\d+)`?', replied.text or "")
                if match:
                    target_user_id = int(match.group(1))

            user_data = get_user(target_user_id)
            if not user_data:
                await update.message.reply_text("❌ User ID မတွေ့ပါ။ User က /start လုပ်ထားဖို့လိုပါသည်။")
                return

            if text.startswith('+'):
                try:
                    amount = int(text[1:])
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'add')
                    try:
                        await context.bot.send_message(
                            chat_id=target_user_id,
                            text=f"✅ *ငွေသွင်းပြီးပါပြီ*\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💰 ထည့်ငွေ: +{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    await update.message.reply_text(
                        f"✅ {user_data['mention']} ထံ {amount:,} ကျပ် ထည့်ပြီးပါပြီ"
                    )
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"👤 {user_data['mention']} အကောင့်ထဲသို့\n{amount:,} ကျပ် ထည့်သွင်းပေးပြီ 🎲"
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
                            text=f"✅ *ငွေထုတ်ပြီးပါပြီ*\n\n"
                                 f"👤 {user_data['name']}\n"
                                 f"🆔 `{target_user_id}`\n"
                                 f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                 f"💸 ထုတ်ငွေ: -{amount:,} ကျပ်\n"
                                 f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                            parse_mode='Markdown'
                        )
                    except:
                        pass
                    await update.message.reply_text(
                        f"✅ {user_data['name']} ထံမှ {amount:,} ကျပ် ထုတ်ပြီးပါပြီ"
                    )
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"🧊 {user_data['name']} ထုတ်ငွေ {amount:,} ကျပ် လွဲပေးပြီးပါပြီ"
                    )
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            return

        # ===== BETTING — no need to reply to bot, any message in group =====
        if not game or game['status'] != 'open':
            return

        bet_number, amount = parse_bet(text)
        if not bet_number or not amount:
            return

        mention = f"@{user.username}" if user.username else user.full_name
        create_or_update_user(user.id, user.full_name, mention)

        if amount < MIN_BET or amount > MAX_BET:
            msg = await update.message.reply_text(
                f"❌ Min {MIN_BET:,}ကျပ် — Max {MAX_BET:,}ကျပ်",
                quote=True
            )
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        bet_count = get_user_bet_count_for_game(user.id, game['game_id'])
        if bet_count >= 2:
            msg = await update.message.reply_text(
                "❌ ဤပွဲစဉ်တွင် နှစ်ကြိမ်သာ လောင်းနိုင်သည်",
                quote=True
            )
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        # Check if user already bet on same number
        existing_bets = get_user_bets(user.id, game['game_id'])
        for eb in existing_bets:
            if eb[3] == bet_number:
                msg = await update.message.reply_text(
                    f"❌ နံပါတ် {bet_number} ကို လောင်းပြီးပါပြီ — မတူသောနံပါတ်ကိုသာ ရွေးပါ",
                    quote=True
                )
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return

        user_data = get_user(user.id)
        if not user_data or user_data['balance'] < amount:
            msg = await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ", quote=True)
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        save_bet(game['game_id'], user.id, bet_number, amount)
        new_balance = update_balance(user.id, amount, 'subtract')

        await update.message.reply_text(
            f"🎲 *ပွဲစဉ်* `{game['game_id']}`\n"
            f"━━━━━━━━━━━━\n"
            f"👤 {user.full_name}\n"
            f"🎯 နံပါတ် *{bet_number}* — {amount:,} ကျပ်\n"
            f"━━━━━━━━━━━━\n"
            f"✅ လောင်းကြေးတင်ပြီးပါပြီ\n"
            f"💰 လက်ကျန်: {new_balance:,} ကျပ်",
            parse_mode='Markdown',
            quote=True
        )

# ==================== DICE HANDLER ====================
async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if chat.id != GAME_GROUP_ID:
        return
    if user.id != OWNER_ID or not update.message.dice:
        return
    if not context.bot_data.get('awaiting_dice'):
        await update.message.reply_text("❌ ယခုအချိန်တွင် အံစာတုံးမလိုအပ်ပါ။")
        return

    dice_value = update.message.dice.value
    game_id = context.bot_data.get('current_game_id')
    print(f"🎲 DICE: {dice_value} | Game: {game_id}")

    if not game_id:
        await update.message.reply_text("❌ ဂိမ်းမရှိပါ။")
        return

    game = get_current_game()
    if not game:
        return

    winners, total_win_amount = update_bet_results(game_id, dice_value)
    total_bet_amount = game['total_bet_amount']
    owner_profit = total_bet_amount - total_win_amount
    close_game(game_id, dice_value, total_win_amount, owner_profit)

    result_text = (
        f"🎉 *ပွဲစဉ်ရလဒ်* — `{game_id}`\n"
        f"━━━━━━━━━━━━━\n"
        f"🎲 အံစာတုံး: *{dice_value}*\n"
        f"━━━━━━━━━━━━━\n\n"
    )

    if winners:
        for bet in winners:
            win_amount = bet[4] * dice_value
            user_info = get_user(bet[2])
            new_balance = update_balance(bet[2], win_amount, 'add')
            prev_balance = new_balance - win_amount
            result_text += (
                f"🏆 {user_info['name']}\n"
                f"   နံပါတ် {bet[3]} — {bet[4]:,} × {dice_value} = {win_amount:,} ကျပ်\n"
                f"   💰 {prev_balance:,} + {win_amount:,} = {new_balance:,} ကျပ်\n\n"
            )
    else:
        result_text += "❌ အနိုင်ရသူမရှိပါ\n"

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

    await context.bot.send_message(
        chat_id=GAME_GROUP_ID,
        text=get_warning_text(),
        reply_markup=get_owner_button()
    )

    # Remove the keyboard now that the game is over
    await context.bot.send_message(
        chat_id=GAME_GROUP_ID,
        text="🔚 ပွဲစဉ်ပြီးပါပြီ",
        reply_markup=ReplyKeyboardRemove()
    )

    try:
        owner_report = (
            f"📊 *ပွဲစဉ်အစီရင်ခံစာ*\n\n"
            f"ပွဲစဉ်: `{game_id}`\n"
            f"အံစာတုံး: {dice_value}\n"
            f"စုစုပေါင်းလောင်းငွေ: {total_bet_amount:,} ကျပ်\n"
            f"အနိုင်ငွေပေးချေ: {total_win_amount:,} ကျပ်\n"
            f"အမြတ်: {owner_profit:,} ကျပ်"
        )
        await context.bot.send_message(chat_id=OWNER_ID, text=owner_report, parse_mode='Markdown')
    except Exception as e:
        print(f"❌ Owner report failed: {e}")

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

    print("=" * 50)
    print("🎲 DICE GAME BOT STARTED")
    print(f"👑 OWNER: {OWNER_ID}")
    print(f"🎮 GROUP: {GAME_GROUP_ID}")
    print(f"💰 BET: {MIN_BET} - {MAX_BET}")
    print("=" * 50)

    app.run_polling()

if __name__ == '__main__':
    main()
