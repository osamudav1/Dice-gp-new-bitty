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
                  group_id TEXT,
                  status TEXT,
                  result TEXT,
                  created_at TIMESTAMP,
                  closed_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  game_id INTEGER,
                  group_id TEXT,
                  user_id TEXT,
                  bet_type TEXT,
                  amount INTEGER,
                  status TEXT,
                  timestamp TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (group_id TEXT,
                  user_id TEXT,
                  added_by TEXT,
                  added_at TIMESTAMP,
                  PRIMARY KEY (group_id, user_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS groups
                 (group_id TEXT PRIMARY KEY,
                  group_name TEXT,
                  added_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS welcome_settings
                 (id INTEGER PRIMARY KEY CHECK (id=1),
                  photo_id TEXT,
                  caption TEXT)''')
    
    c.execute("INSERT OR IGNORE INTO welcome_settings (id, caption) VALUES (1, 'ကြိုဆိုပါတယ်')")
    
    conn.commit()
    conn.close()

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
            'result': game[4],
            'created_at': game[5],
            'closed_at': game[6]
        }
    return None

def create_game(group_id):
    game_id = get_next_game_id(group_id)
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO games (game_id, group_id, status, created_at) VALUES (?, ?, 'open', ?)",
              (game_id, str(group_id), datetime.now()))
    c.execute("INSERT OR IGNORE INTO groups (group_id, added_at) VALUES (?, ?)",
              (str(group_id), datetime.now()))
    conn.commit()
    conn.close()
    return game_id

def close_game(group_id, game_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE games SET status = 'closed', closed_at = ? WHERE group_id = ? AND game_id = ?",
              (datetime.now(), str(group_id), game_id))
    conn.commit()
    conn.close()

def save_bet(group_id, game_id, user_id, bet_type, amount):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("INSERT INTO bets (game_id, group_id, user_id, bet_type, amount, status, timestamp) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
              (game_id, str(group_id), str(user_id), bet_type, amount, datetime.now()))
    conn.commit()
    conn.close()
    update_today_stats(user_id, 'today_bet', amount)

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
            'bet_type': bet[4],
            'amount': bet[5],
            'status': bet[6],
            'user_name': user['name'] if user else 'Unknown'
        })
    return result

def update_bet_results(group_id, game_id, result_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM bets WHERE group_id = ? AND game_id = ?", (str(group_id), game_id))
    bets = c.fetchall()
    
    winners = []
    for bet in bets:
        if bet[4] == result_type:
            c.execute("UPDATE bets SET status = 'won' WHERE id = ?", (bet[0],))
            winners.append(bet)
        else:
            c.execute("UPDATE bets SET status = 'lost' WHERE id = ?", (bet[0],))
    
    conn.commit()
    conn.close()
    return winners

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
    c.execute("SELECT group_id, group_name FROM groups")
    groups = c.fetchall()
    conn.close()
    return groups

def update_group_name(group_id, group_name):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE groups SET group_name = ? WHERE group_id = ?", (group_name, str(group_id)))
    conn.commit()
    conn.close()

# ==================== WELCOME SETTINGS ====================
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

# ==================== IMAGE GENERATION ====================
async def create_start_image(game_id):
    """Create image for game start"""
    img = Image.new('RGB', (600, 200), color=(30, 30, 30))
    d = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("arial.ttf", 40)
        font_small = ImageFont.truetype("arial.ttf", 25)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Draw title
    d.text((50, 50), f"🎮 ဂိမ်းစတင်ပါပြီ", fill=(255, 215, 0), font=font)
    d.text((50, 110), f"ပွဲစဉ်: {game_id}", fill=(255, 255, 255), font=font_small)
    
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

async def create_stop_image(game_id, bets):
    """Create image for game stop with bet list"""
    img = Image.new('RGB', (700, 400), color=(30, 30, 30))
    d = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Draw title
    d.text((50, 30), f"✨ ပွဲစဉ် ➖ {game_id}", fill=(255, 215, 0), font=font)
    d.text((50, 80), f"➖ လောင်းကြေးပိတ်ပါပြီ ➖", fill=(255, 255, 255), font=font_small)
    
    # Draw bets
    y = 140
    if bets:
        for bet in bets:
            multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
            bet_type_display = "S" if bet['bet_type'] == 'small' else "B" if bet['bet_type'] == 'big' else "J"
            text = f"👤 {bet['user_name']} ➖ {bet_type_display} {bet['amount']:,} ({multiplier})"
            d.text((50, y), text, fill=(200, 200, 200), font=font_small)
            y += 30
    else:
        d.text((50, y), "❌ လောင်းကြေးမရှိပါ", fill=(200, 200, 200), font=font_small)
    
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

async def create_result_image(game_id, dice1, dice2, total, display, multiplier, winners):
    """Create image with game results"""
    img = Image.new('RGB', (800, 600), color=(30, 30, 30))
    d = ImageDraw.Draw(img)
    
    try:
        font = ImageFont.truetype("arial.ttf", 30)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Draw title
    d.text((50, 30), f"🎉 ပွဲစဉ် ➖ {game_id}", fill=(255, 215, 0), font=font)
    d.text((50, 80), f"💥 Dice Bot 💥", fill=(255, 255, 255), font=font)
    d.text((50, 130), f"{dice1}+{dice2} = {total} {display} ({multiplier}ဆ)", fill=(0, 255, 0), font=font_small)
    
    # Draw winners
    y = 190
    if winners:
        for bet in winners:
            winnings = bet[5] * multiplier
            user_info = get_user(bet[3])
            text = f"👤 {user_info['name']} ➖ {display} > {bet[5]:,} + {winnings - bet[5]:,} = {winnings:,}"
            d.text((50, y), text, fill=(200, 200, 200), font=font_small)
            y += 30
            
            # Show new balance
            new_balance = user_info['balance'] if user_info else 0
            prev_balance = new_balance - winnings
            balance_text = f"   💰 လက်ကျန်: {prev_balance:,} + {winnings:,} = {new_balance:,}Ks"
            d.text((70, y), balance_text, fill=(255, 255, 0), font=font_small)
            y += 40
    else:
        d.text((50, y), "❌ အနိုင်ရသူမရှိပါ", fill=(200, 200, 200), font=font_small)
    
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: {user.id} in {chat.id} ({chat.type})")
    
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    if chat.type in ['group', 'supergroup']:
        update_group_name(chat.id, chat.title or "Unknown")
        
        if is_admin(chat.id, user.id):
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text="👑 **Admin ထိန်းချုပ်ခန်း**\n\n"
                     "ဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            text = "🎲 **ကစားရန်**\n\n" \
                   "S200 (Small 200)\n" \
                   "B500 (Big 500)\n" \
                   "J1000 (Japort 1000)\n\n" \
                   "အနည်းဆုံး ၂၀၀ကျပ်\n" \
                   "အများဆုံး ၁၀၀၀ကျပ်"
            
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown'
            )
        return
    
    if chat.type == 'private':
        if user.id == OWNER_ID:
            welcome = get_welcome_settings()
            keyboard = [
                [InlineKeyboardButton("👥 Group များစာရင်း", callback_data='list_groups')],
                [InlineKeyboardButton("➕ Admin ထည့်ရန်", callback_data='add_admin')],
                [InlineKeyboardButton("➖ Admin ဖြုတ်ရန်", callback_data='remove_admin')],
                [InlineKeyboardButton("📋 Admin စာရင်း", callback_data='list_admins')],
                [InlineKeyboardButton("🖼️ Welcome ပုံထည့်ရန်", callback_data='welcome_add_photo')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if welcome['photo_id']:
                await update.message.reply_photo(
                    photo=welcome['photo_id'],
                    caption="👑 **Main Owner ထိန်းချုပ်ခန်း**",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    "👑 **Main Owner ထိန်းချုပ်ခန်း**",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        else:
            welcome = get_welcome_settings()
            keyboard = [
                [InlineKeyboardButton("🎲 ကစားရန်", url=GAME_GROUP_URL)]
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
    
    print(f"CALLBACK: {data} from {user.id}")
    
    # Handle group selection
    if data.startswith('select_group_'):
        group_id = data.replace('select_group_', '')
        context.user_data['selected_group'] = group_id
        
        if 'add_admin' in context.user_data:
            await query.edit_message_text(
                f"Group ID: `{group_id}`\n\n"
                "Admin ထည့်ရန် User ID ကိုရိုက်ထည့်ပါ။",
                parse_mode='Markdown'
            )
            context.user_data['awaiting'] = 'admin_user_id'
        elif 'remove_admin' in context.user_data:
            admins = get_group_admins(group_id)
            if admins:
                text = f"Group ID: `{group_id}`\n\nဖြုတ်ရန် Admin ID ကိုရိုက်ထည့်ပါ:\n"
                for admin_id in admins:
                    text += f"• `{admin_id}`\n"
            else:
                text = f"Group ID: `{group_id}`\n\nဤအုပ်စုတွင် Admin မရှိပါ။"
            
            await query.edit_message_text(text, parse_mode='Markdown')
            context.user_data['awaiting'] = 'remove_admin_id'
        return
    
    # Game control
    if data in ['game_start', 'game_stop']:
        group_id = str(query.message.chat.id)
        
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
            
            # Send start image
            img_bytes = await create_start_image(game_id)
            await context.bot.send_photo(
                chat_id=group_id,
                photo=img_bytes,
                caption=f"**စတင်လောင်းလို့ရပါပြီ**",
                parse_mode='Markdown'
            )
            
            # Send warning with buttons
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
            
            # Send stop image with bet list
            img_bytes = await create_stop_image(game_id, bets)
            await context.bot.send_photo(
                chat_id=group_id,
                photo=img_bytes
            )
            
            # Send warning with buttons
            await context.bot.send_message(
                chat_id=group_id,
                text=get_warning_text(),
                reply_markup=get_deposit_withdraw_buttons(),
                parse_mode='Markdown'
            )
            
            # Bot will auto-send dice
            await asyncio.sleep(1)
            
            # Store game ID
            if 'group_games' not in context.chat_data:
                context.chat_data['group_games'] = {}
            context.chat_data['group_games'][group_id] = game_id
            
            # Bot sends first dice
            await context.bot.send_dice(chat_id=group_id, emoji='🎲')
    
    # Owner commands
    elif user.id == OWNER_ID:
        if data == 'list_groups':
            groups = get_all_groups()
            if groups:
                text = "**Bot သုံးနေသော Group များ**\n\n"
                for group_id, group_name in groups:
                    name = group_name or "Unknown"
                    admins = get_group_admins(group_id)
                    admin_count = len(admins)
                    text += f"• {name}\n  ID: `{group_id}`\n  Admin: {admin_count} ဦး\n"
            else:
                text = "Bot ကို မည်သည့် Group မှ မသုံးရသေးပါ။"
            
            await query.edit_message_text(text, parse_mode='Markdown')
        
        elif data == 'add_admin':
            groups = get_all_groups()
            if not groups:
                await query.edit_message_text("❌ Group မရှိပါ။ ဦးစွာ Bot ကို Group ထဲထည့်ပါ။")
                return
            
            keyboard = []
            for group_id, group_name in groups:
                name = group_name or "Unknown"
                keyboard.append([InlineKeyboardButton(f"{name}", callback_data=f'select_group_{group_id}')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "Admin ထည့်ရန် Group ကိုရွေးပါ။",
                reply_markup=reply_markup
            )
            context.user_data['add_admin'] = True
        
        elif data == 'remove_admin':
            groups = get_all_groups()
            if not groups:
                await query.edit_message_text("❌ Group မရှိပါ။")
                return
            
            keyboard = []
            for group_id, group_name in groups:
                name = group_name or "Unknown"
                keyboard.append([InlineKeyboardButton(f"{name}", callback_data=f'select_group_{group_id}')])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "Admin ဖြုတ်ရန် Group ကိုရွေးပါ။",
                reply_markup=reply_markup
            )
            context.user_data['remove_admin'] = True
        
        elif data == 'list_admins':
            groups = get_all_groups()
            if not groups:
                await query.edit_message_text("❌ Group မရှိပါ။")
                return
            
            text = "**Group အလိုက် Admin များ**\n\n"
            for group_id, group_name in groups:
                name = group_name or "Unknown"
                admins = get_group_admins(group_id)
                text += f"**{name}** (ID: `{group_id}`)\n"
                if admins:
                    for admin_id in admins:
                        user_data = get_user(admin_id)
                        admin_name = user_data['name'] if user_data else "Unknown"
                        text += f"  • {admin_name} (`{admin_id}`)\n"
                else:
                    text += "  • Admin မရှိ\n"
                text += "\n"
            
            await query.edit_message_text(text, parse_mode='Markdown')
        
        elif data == 'welcome_add_photo':
            await query.edit_message_text("📸 Welcome အတွက် ပုံကို ပို့ပါ။")
            context.user_data['awaiting'] = 'welcome_photo'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id} in {chat.id}")
    
    # Private chat handling
    if chat.type == 'private':
        if user.id == OWNER_ID and 'awaiting' in context.user_data:
            if context.user_data['awaiting'] == 'admin_user_id':
                try:
                    admin_id = int(text.strip())
                    group_id = context.user_data.get('selected_group')
                    
                    if group_id:
                        success, message = add_admin(group_id, admin_id, user.id)
                        await update.message.reply_text(f"✅ {message}")
                    else:
                        await update.message.reply_text("❌ Group ရွေးထားခြင်းမရှိပါ")
                    
                except ValueError:
                    await update.message.reply_text("❌ User ID ဂဏန်းထည့်ပါ")
                
                context.user_data.clear()
                return
            
            elif context.user_data['awaiting'] == 'remove_admin_id':
                try:
                    admin_id = int(text.strip())
                    group_id = context.user_data.get('selected_group')
                    
                    if group_id:
                        success, message = remove_admin(group_id, admin_id, user.id)
                        await update.message.reply_text(f"✅ {message}")
                    else:
                        await update.message.reply_text("❌ Group ရွေးထားခြင်းမရှိပါ")
                    
                except ValueError:
                    await update.message.reply_text("❌ User ID ဂဏန်းထည့်ပါ")
                
                context.user_data.clear()
                return
            
            elif context.user_data['awaiting'] == 'welcome_photo':
                if update.message.photo:
                    photo_id = update.message.photo[-1].file_id
                    update_welcome_photo(photo_id)
                    await update.message.reply_text("✅ Welcome Photo ထည့်ပြီးပါပြီ")
                else:
                    update_welcome_caption(text)
                    await update.message.reply_text("✅ Welcome Message ပြင်ပြီးပါပြီ")
                context.user_data.clear()
                return
        
        return
    
    # Group chat handling
    if chat.type in ['group', 'supergroup']:
        group_id = str(chat.id)
        game = get_current_game(group_id)
        
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
                            bet_type = "S" if bet[4] == 'small' else "B" if bet[4] == 'big' else "J"
                            bets_text += f"{bet_type} {bet[5]:,} ကျပ်\n"
                
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
        
        # Betting
        if not game or game['status'] != 'open':
            return
        
        bet_type, amount = parse_bet(text)
        if bet_type:
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                return
            
            if amount < 200 or amount > 1000:
                await update.message.reply_text("❌ အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်")
                return
            
            if bet_type in ['small', 'big']:
                user_bets = get_user_bets(user.id, group_id, game['game_id'])
                for bet in user_bets:
                    if (bet_type == 'small' and bet[4] == 'big') or (bet_type == 'big' and bet[4] == 'small'):
                        await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                        return
            
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            save_bet(group_id, game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Small" if bet_type == 'small' else "Big" if bet_type == 'big' else "Japort"
            
            await update.message.reply_to_message.reply_text(
                f"**ပွဲစဉ်** `{game['game_id']}`\n"
                f"➖➖➖➖➖\n"
                f"**{bet_display}** - {amount} ({multiplier})\n"
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
        
        # Only process dice sent by bot (auto dice)
        if user.id == context.bot.id:
            dice_value = update.message.dice.value
            print(f"BOT DICE: {dice_value} in group {group_id}")
            
            if 'group_dice' not in context.chat_data:
                context.chat_data['group_dice'] = {}
            
            if 'group_games' not in context.chat_data:
                context.chat_data['group_games'] = {}
            
            group_dice = context.chat_data['group_dice']
            game_id = group_games.get(group_id)
            
            if not game_id:
                return
            
            # First dice
            if group_id not in group_dice:
                group_dice[group_id] = {'dice1': dice_value}
                print(f"First dice for group {group_id}: {dice_value}")
                
                # Wait for dice to finish rolling (Telegram auto handles this)
                # Send second dice after short delay
                await asyncio.sleep(2)
                await context.bot.send_dice(chat_id=chat.id, emoji='🎲')
            
            # Second dice
            elif 'dice2' not in group_dice[group_id]:
                group_dice[group_id]['dice2'] = dice_value
                
                dice1 = group_dice[group_id]['dice1']
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
                
                print(f"Game {game_id} result: {result}")
                
                # Update results
                winners = update_bet_results(group_id, game_id, result)
                
                # Create and send result image
                img_bytes = await create_result_image(game_id, dice1, dice2, total, display, multiplier, winners)
                await context.bot.send_photo(
                    chat_id=chat.id,
                    photo=img_bytes
                )
                
                # Send warning with buttons
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=get_warning_text(),
                    reply_markup=get_deposit_withdraw_buttons(),
                    parse_mode='Markdown'
                )
                
                # Close game
                close_game(group_id, game_id)
                
                # Clean up
                del group_dice[group_id]
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
    
    print("=" * 60)
    print("🤖 MULTI-GROUP BOT STARTED")
    print("=" * 60)
    print(f"👑 MAIN OWNER: {OWNER_ID}")
    print("=" * 60)
    print("✅ FEATURES:")
    print("   • Image for game start")
    print("   • Image for game stop with bet list")
    print("   • Image for results with winners")
    print("   • Bot auto-sends 2 dice")
    print("   • Waits for dice to stop before calculating")
    print("   • Multiple groups support")
    print("   • Group-wise admins")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
