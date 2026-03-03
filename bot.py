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
            await update.message.reply_text(
                text="🎲 **ကစားရန်**\n\n"
                     "S100 (Small 100)\n"
                     "B100 (Big 100)\n"
                     "J100 (Japort 100)\n\n"
                     "အနည်းဆုံး ၁၀၀ကျပ်",
                parse_mode='Markdown'
            )
        return
    
    # PRIVATE CHAT - Welcome message for users
    if chat.type == 'private':
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
    
    # GAME GROUP CALLBACKS
    if data == 'game_start' and user.id == OWNER_ID:
        await query.answer()
        
        # Check if game already exists
        current_game = get_current_game()
        if current_game:
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ။ အရင်ပိတ်ပါ။")
            return
        
        game_id = create_game()
        
        await context.bot.send_message(
            chat_id=GAME_GROUP_ID,
            text=f"**ပွဲစဉ်** - `{game_id}`\n"
                 f"**စတင်လောင်းလို့ရပါပြီ**",
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
        
        await context.bot.send_message(chat_id=GAME_GROUP_ID, text=summary, parse_mode='Markdown')
        
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""
    
    print(f"MESSAGE: {text[:30]} from {user.id}")
    
    # ===== GAME GROUP =====
    if chat.id == GAME_GROUP_ID:
        # Check if this is a deposit/withdraw command (owner only)
        if user.id == OWNER_ID and update.message.reply_to_message:
            replied = update.message.reply_to_message
            if replied.from_user.id == context.bot.id:
                # Extract user ID from replied message
                match = re.search(r'ID[ -]+`?(\d+)`?', replied.text)
                if match:
                    target_id = match.group(1)
                    
                    if text.startswith('+'):
                        try:
                            amount = int(text[1:])
                            user_data = get_user(target_id)
                            if not user_data:
                                await update.message.reply_text("❌ User ID မတွေ့ပါ")
                                return
                            
                            prev_balance = user_data['balance']
                            new_balance = update_balance(target_id, amount, 'add')
                            update_today_stats(target_id, 'today_deposit', amount)
                            
                            # Send detailed info to owner (private reply)
                            await update.message.reply_text(
                                f"✅ **ငွေသွင်းပြီးပါပြီ**\n\n"
                                f"👤 {user_data['name']}\n"
                                f"🆔 `{target_id}`\n"
                                f"📢 {user_data['mention']}\n"
                                f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                f"💰 ထည့်ငွေ: +{amount:,} ကျပ်\n"
                                f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                                parse_mode='Markdown'
                            )
                            
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
                            user_data = get_user(target_id)
                            if not user_data:
                                await update.message.reply_text("❌ User ID မတွေ့ပါ")
                                return
                            
                            if user_data['balance'] < amount:
                                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                                return
                            
                            prev_balance = user_data['balance']
                            new_balance = update_balance(target_id, amount, 'subtract')
                            update_today_stats(target_id, 'today_withdraw', amount)
                            
                            # Send detailed info to owner (private reply)
                            await update.message.reply_text(
                                f"✅ **ငွေထုတ်ပြီးပါပြီ**\n\n"
                                f"👤 {user_data['name']}\n"
                                f"🆔 `{target_id}`\n"
                                f"📢 {user_data['mention']}\n"
                                f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                f"💸 ထုတ်ငွေ: -{amount:,} ကျပ်\n"
                                f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                                parse_mode='Markdown'
                            )
                            
                            # Send public announcement to group
                            await context.bot.send_message(
                                chat_id=GAME_GROUP_ID,
                                text=f"🧊 {user_data['name']} သင်ထုတ်ယူငွေ {amount:,} ကျပ်ကို သင့် KPay/Wave အကောင့်ထဲသို့ လွဲပေးပြီးပါပြီ။ စစ်ဆေးပေးပါ။ 🧊"
                            )
                            
                        except ValueError:
                            await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းထည့်ပါ")
                    
                    return
        
        # Regular betting
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
            
            # Send bet confirmation
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
    
    print("=" * 60)
    print("🤖 BOT STARTED SUCCESSFULLY")
    print("=" * 60)
    print(f"👑 OWNER ID: {OWNER_ID}")
    print(f"🎮 GAME GROUP: {GAME_GROUP_ID}")
    print("=" * 60)
    print("✅ GAME GROUP FEATURES:")
    print("   - Owner sees: 🎮 ဂိမ်းစတင်ရန် / ⏹️ ဂိမ်းပိတ်ရန် buttons")
    print("   - Game start → Betting open")
    print("   - Game stop → Show bet list → Ask for 2 dice")
    print("   - Owner rolls dice → Auto calculate winners")
    print("   - Update balances")
    print("   - NO AUTO DELETE - all messages remain")
    print("=" * 60)
    print("✅ DEPOSIT/WITHDRAW IN GAME GROUP:")
    print("   - Reply to user's bet message with:")
    print("   - +amount (ငွေသွင်း)")
    print("   - -amount (ငွေထုတ်) - checks balance first")
    print("   - Shows owner: ID / Mention / Previous balance / Amount / New balance")
    print("   - Sends public announcement to group")
    print("=" * 60)
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
