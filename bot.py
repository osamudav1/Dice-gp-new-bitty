import logging
import sqlite3
import random
import time
import asyncio
import os
import re
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
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

def get_all_users():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT user_id, name, mention, balance FROM users")
    users = c.fetchall()
    conn.close()
    return users

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
    
    # PRIVATE CHAT
    if chat.type == 'private':
        if user.id == OWNER_ID:
            keyboard = [
                [InlineKeyboardButton("🎮 ဂိမ်းစတင်ရန်", callback_data='owner_game_start')],
                [InlineKeyboardButton("⏹️ ဂိမ်းပိတ်ရန်", callback_data='owner_game_stop')],
                [InlineKeyboardButton("💰 တစ်ဦးချင်းငွေသွင်း", callback_data='add_money')],
                [InlineKeyboardButton("💸 တစ်ဦးချင်းငွေထုတ်", callback_data='remove_money')],
                [InlineKeyboardButton("👥 အဖွဲ့လိုက်ငွေသွင်း", callback_data='all_add_money')],
                [InlineKeyboardButton("👥 အဖွဲ့လိုက်ငွေထုတ်", callback_data='all_remove_money')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    
    print(f"CALLBACK: {data}")
    
    if user.id != OWNER_ID:
        await query.answer("ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်", show_alert=True)
        return
    
    # GAME CONTROL
    if data == 'game_start' or data == 'owner_game_start':
        await query.answer()
        
        current_game = get_current_game()
        if current_game:
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ")
            return
        
        game_id = create_game()
        
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=f"**ပွဲစဉ်** - `{game_id}`\n"
                 f"**စတင်လောင်းလို့ရပါပြီ**",
            parse_mode='Markdown'
        )
        
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=get_warning_text(),
            reply_markup=get_deposit_withdraw_buttons(),
            parse_mode='Markdown'
        )
        
        await query.message.reply_text("✅ ဂိမ်းစတင်ပြီးပါပြီ")
    
    elif data == 'game_stop' or data == 'owner_game_stop':
        await query.answer()
        
        game = get_current_game()
        if not game:
            await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
            return
        
        game_id = game['game_id']
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
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=get_warning_text(),
            reply_markup=get_deposit_withdraw_buttons(),
            parse_mode='Markdown'
        )
        
        await asyncio.sleep(1)
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text="🎲 **အံစာတုံး ၂ တုံး ပို့ပေးပါ။**",
            parse_mode='Markdown'
        )
        
        context.chat_data['awaiting_dice'] = game_id
        await query.message.reply_text("✅ ဂိမ်းပိတ်ပြီးပါပြီ")
    
    # INDIVIDUAL MONEY MANAGEMENT
    elif data == 'add_money':
        await query.answer()
        await query.edit_message_text(
            "💰 **တစ်ဦးချင်းငွေသွင်းရန်**\n\n"
            "User ရဲ့စာကို Reply လုပ်ပြီး +ပမာဏ ရိုက်ထည့်ပါ။\n"
            "ဥပမာ: +5000"
        )
    
    elif data == 'remove_money':
        await query.answer()
        await query.edit_message_text(
            "💸 **တစ်ဦးချင်းငွေထုတ်ရန်**\n\n"
            "User ရဲ့စာကို Reply လုပ်ပြီး -ပမာဏ ရိုက်ထည့်ပါ။\n"
            "ဥပမာ: -2000"
        )
    
    # ALL USERS MONEY MANAGEMENT
    elif data == 'all_add_money':
        await query.answer()
        await query.edit_message_text(
            "👥 **အဖွဲ့လိုက်ငွေသွင်းရန်**\n\n"
            "/all +ပမာဏ ရိုက်ထည့်ပါ။\n"
            "ဥပမာ: /all +1000\n\n"
            "ဒီ command ကို Group ထဲမှာရိုက်ပါ။"
        )
    
    elif data == 'all_remove_money':
        await query.answer()
        await query.edit_message_text(
            "👥 **အဖွဲ့လိုက်ငွေထုတ်ရန်**\n\n"
            "/all -ပမာဏ ရိုက်ထည့်ပါ။\n"
            "ဥပမာ: /all -500\n\n"
            "ဒီ command ကို Group ထဲမှာရိုက်ပါ။"
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id}")
    
    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        game = get_current_game()
        
        # CHECK FOR /all COMMAND (OWNER ONLY)
        if user.id == OWNER_ID and text.startswith('/all'):
            parts = text.split()
            if len(parts) == 2 and (parts[1].startswith('+') or parts[1].startswith('-')):
                try:
                    amount = int(parts[1])
                    operation = 'add' if parts[1].startswith('+') else 'subtract'
                    op_text = "ထည့်ပေး" if operation == 'add' else "ထုတ်ယူ"
                    
                    # Get all users
                    all_users = get_all_users()
                    success_count = 0
                    
                    # Send processing message
                    processing_msg = await update.message.reply_text(f"⏳ လုပ်ဆောင်နေပါသည်... အသုံးပြုသူ {len(all_users)} ယောက်")
                    
                    for uid in all_users:
                        try:
                            user_data = get_user(uid)
                            if user_data:
                                prev_balance = user_data['balance']
                                
                                if operation == 'add':
                                    new_balance = update_balance(uid, abs(amount), 'add')
                                    update_today_stats(uid, 'today_deposit', abs(amount))
                                else:
                                    if user_data['balance'] >= abs(amount):
                                        new_balance = update_balance(uid, abs(amount), 'subtract')
                                        update_today_stats(uid, 'today_withdraw', abs(amount))
                                    else:
                                        continue
                                
                                # Send DM to each user
                                try:
                                    await context.bot.send_message(
                                        chat_id=int(uid),
                                        text=f"✅ **အဖွဲ့လိုက်ငွေ{op_text}ခြင်း**\n\n"
                                             f"👤 {user_data['name']}\n"
                                             f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                             f"💰 {op_text}ငွေ: {amount:+,} ကျပ်\n"
                                             f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                                        parse_mode='Markdown'
                                    )
                                except:
                                    pass
                                success_count += 1
                            await asyncio.sleep(0.1)
                        except:
                            continue
                    
                    await processing_msg.delete()
                    
                    await update.message.reply_text(
                        f"✅ **အဖွဲ့လိုက်ငွေ{op_text}ခြင်း ပြီးဆုံးပါပြီ**\n\n"
                        f"စုစုပေါင်း: {len(all_users)} ယောက်\n"
                        f"အောင်မြင်သည်: {success_count} ယောက်\n"
                        f"ငွေပမာဏ: {amount:+,} ကျပ်",
                        parse_mode='Markdown'
                    )
                    
                    # Group announcement
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"👥 အသုံးပြုသူအားလုံးအတွက် ငွေ {amount:+,} ကျပ် {op_text}ခဲ့သည်။"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ မှန်ကန်စွာရိုက်ထည့်ပါ")
            return
        
        # USER INFO REQUEST
        if text == "3" and user.id != OWNER_ID:
            user_data = get_user(user.id)
            if user_data:
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
                await asyncio.sleep(5)
                await msg.delete()
            return
        
        # INDIVIDUAL DEPOSIT/WITHDRAW (OWNER REPLY)
        if user.id == OWNER_ID and update.message.reply_to_message:
            replied = update.message.reply_to_message
            target_user = replied.from_user
            target_user_id = target_user.id
            
            # If replying to bot message, extract ID
            if target_user.id == context.bot.id:
                match = re.search(r'ID[ -]+`?(\d+)`?', replied.text)
                if match:
                    target_user_id = int(match.group(1))
            
            user_data = get_user(target_user_id)
            if not user_data:
                await update.message.reply_text("❌ User ID မတွေ့ပါ")
                return
            
            # Process deposit/withdraw
            if text.startswith('+'):
                try:
                    amount = int(text[1:])
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'add')
                    update_today_stats(target_user_id, 'today_deposit', amount)
                    
                    # Send DM to user
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
                    
                    # Send DM to owner
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
                    
                    # Group announcement
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"👤 {user_data['name']} အကောင့်ထဲသို့ {amount:,} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            
            elif text.startswith('-'):
                try:
                    amount = int(text[1:])
                    
                    if user_data['balance'] < amount:
                        await update.message.reply_text("❌ လက်ကျန်မလုံလောက်")
                        return
                    
                    prev_balance = user_data['balance']
                    new_balance = update_balance(target_user_id, amount, 'subtract')
                    update_today_stats(target_user_id, 'today_withdraw', amount)
                    
                    # Send DM to user
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
                    
                    # Send DM to owner
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
                    
                    # Group announcement
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=f"🧊 {user_data['name']} ထုတ်ယူငွေ {amount:,} ကျပ်ကို လွဲပေးပြီးပါပြီ။"
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
            return
        
        # BETTING
        if not game or game['status'] != 'open':
            return
        
        bet_type, amount = parse_bet(text)
        if bet_type:
            # Check both small/big
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                return
            
            # Check limits
            if amount < 200 or amount > 1000:
                await update.message.reply_text("❌ အနည်းဆုံး ၂၀၀ကျပ်၊ အများဆုံး ၁၀၀၀ကျပ်")
                return
            
            # Check if already bet on opposite
            if bet_type in ['small', 'big']:
                user_bets = get_user_bets(user.id, game['game_id'])
                for bet in user_bets:
                    if (bet_type == 'small' and bet[3] == 'big') or (bet_type == 'big' and bet[3] == 'small'):
                        await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက်မရပါ")
                        return
            
            # Check balance
            user_data = get_user(user.id)
            if not user_data or user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                return
            
            # Save bet
            save_bet(game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Small" if bet_type == 'small' else "Big" if bet_type == 'big' else "Japort"
            
            # Confirm bet by replying
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
    
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        if update.message.dice:
            dice_value = update.message.dice.value
            print(f"DICE: {dice_value}")
            
            # First dice
            if 'dice1' not in context.chat_data:
                context.chat_data['dice1'] = dice_value
                print(f"First dice: {dice_value}")
                
                msg = await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text="🎲 **နောက်တစ်ခါထပ်ပို့ပါ**",
                    parse_mode='Markdown'
                )
                await asyncio.sleep(2)
                await msg.delete()
            
            # Second dice
            elif 'dice2' not in context.chat_data:
                context.chat_data['dice2'] = dice_value
                print(f"Second dice: {dice_value}")
                
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
                    print(f"Game {game_id} result: {result}")
                    
                    # Update results
                    winners = update_bet_results(game_id, result)
                    
                    # Build message
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
                            
                            msg += f"👤 {user_info['name']} ➖ {display} > {bet[4]:,} + {winnings - bet[4]:,} = {winnings:,}\n"
                            msg += f"💰 လက်ကျန် {prev_balance:,} + {winnings:,} = {new_balance:,}Ks\n\n"
                    else:
                        msg += "❌ အနိုင်ရသူမရှိပါ\n"
                    
                    # Send result
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=msg,
                        parse_mode='Markdown'
                    )
                    
                    # Send warning with buttons
                    await context.bot.send_message(
                        chat_id=GAME_GROUP_ID,
                        text=get_warning_text(),
                        reply_markup=get_deposit_withdraw_buttons(),
                        parse_mode='Markdown'
                    )
                    
                    # Close game
                    close_game(game_id)
                    
                    # Clear data
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
    
    print("=" * 60)
    print("🤖 BOT STARTED")
    print("=" * 60)
    print(f"OWNER: {OWNER_ID}")
    print(f"GAME GROUP: {GAME_GROUP_ID}")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
