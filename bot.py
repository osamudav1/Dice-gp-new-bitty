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
                 (game_id INTEGER PRIMARY KEY,
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
def get_user(user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (str(user_id),))
    user = c.fetchone()
    conn.close()
    
    if not user:
        return {
            'user_id': str(user_id),
            'name': 'Unknown',
            'mention': '',
            'today_deposit': 0,
            'today_withdraw': 0,
            'today_bet': 0,
            'balance': 0
        }
    
    return {
        'user_id': user[0],
        'name': user[1],
        'mention': user[2],
        'today_deposit': user[3],
        'today_withdraw': user[4],
        'today_bet': user[5],
        'balance': user[6]
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
            'game_id': game[0],
            'status': game[1],
            'result': game[2],
            'created_at': game[3],
            'closed_at': game[4]
        }
    return None

def create_game():
    game_id = random.randint(100000, 999999)
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
    
    # Update today's bet total
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

def cancel_user_bet(game_id, user_id):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    # Get bet amount
    c.execute("SELECT amount FROM bets WHERE game_id = ? AND user_id = ? AND status = 'pending'",
              (game_id, str(user_id)))
    bet = c.fetchone()
    
    if bet:
        # Delete bet
        c.execute("DELETE FROM bets WHERE game_id = ? AND user_id = ? AND status = 'pending'",
                  (game_id, str(user_id)))
        conn.commit()
        conn.close()
        return bet[0]
    
    conn.close()
    return 0

def update_bet_results(game_id, result_type):
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    
    # Get all bets for this game
    c.execute("SELECT * FROM bets WHERE game_id = ?", (game_id,))
    bets = c.fetchall()
    
    winners = []
    for bet in bets:
        if bet[3] == result_type:  # bet_type matches result
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

def reset_welcome():
    conn = sqlite3.connect('bot_database.db')
    c = conn.cursor()
    c.execute("UPDATE welcome_settings SET photo_id = NULL, caption = 'ကြိုဆိုပါတယ်' WHERE id = 1")
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
    
    # Patterns: s100, b200, j500, small100, big200, jp500, japort500
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

def create_result_image(dice1, dice2, total, result_type, winners):
    # Create a simple image with results
    img = Image.new('RGB', (800, 600), color=(30, 30, 30))
    d = ImageDraw.Draw(img)
    
    # Try to load font, use default if not available
    try:
        font = ImageFont.truetype("arial.ttf", 30)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Draw dice results
    d.text((50, 50), f"🎲 Dice 1: {dice1}", fill=(255, 255, 255), font=font)
    d.text((50, 100), f"🎲 Dice 2: {dice2}", fill=(255, 255, 255), font=font)
    d.text((50, 150), f"Total: {total}", fill=(255, 255, 0), font=font)
    d.text((50, 200), f"Result: {result_type.upper()}", fill=(0, 255, 0), font=font)
    
    # Draw winners
    d.text((50, 300), "🏆 Winners:", fill=(255, 215, 0), font=font)
    y = 350
    for winner in winners[:10]:  # Show max 10 winners
        d.text((50, y), f"{winner['user_name']}: {winner['amount']} -> {winner['winnings']}", 
               fill=(255, 255, 255), font=font_small)
        y += 30
    
    # Save to bytes
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"START: user={user.id}, chat={chat.id}")
    
    # Save user to database
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    
    # GAME GROUP - Owner menu buttons (Owner အတွက်သာ)
    if chat.id == GAME_GROUP_ID:
        if user.id == OWNER_ID:
            print("✅ Game Group Owner - showing 5 main menu buttons")
            
            # Main Menu Button ၅ ခု (Owner အတွက်သာ)
            keyboard = [
                [KeyboardButton("🎮 Game စတင်ရန်")],
                [KeyboardButton("⏹️ Game ပိတ်ရန်")],
                [KeyboardButton("🔴 Small"), KeyboardButton("🔵 Big"), KeyboardButton("🟣 Japort 7")]
            ]
            
            reply_markup = ReplyKeyboardMarkup(
                keyboard=keyboard,
                resize_keyboard=True,
                one_time_keyboard=False,
                input_field_placeholder="ခလုတ်တစ်ခုကိုနှိပ်ပါ..."
            )
            
            await update.message.reply_text(
                text="📌 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\nအောက်ပါခလုတ်များကိုနှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            print("✅ 5 main menu buttons sent to owner")
        else:
            # Normal user in game group - No buttons, just text
            await update.message.reply_text(
                "🎲 **ကစားရန်**\n\n"
                "S100 (Small 100)\n"
                "B100 (Big 100)\n"
                "J100 (Japort 100)\n\n"
                "အနည်းဆုံး လောင်းကြေး ၁၀၀ ကျပ်",
                parse_mode='Markdown'
            )
        return
    
    # DEPOSIT GROUP
    if chat.id == DEPOSIT_GROUP_ID:
        print("✅ Deposit Group - showing instructions")
        await update.message.reply_text(
            "💰 **ငွေသွင်း/ငွေထုတ် Group**\n\n"
            "သင်၏အချက်အလက်များကြည့်ရန် '1' ကိုနှိပ်ပါ။\n"
            "ငွေသွင်းရန် အကောင့်အချက်အလက်များကို Admin ထံမေးမြန်းပါ။\n\n"
            "**ငွေသွင်းရန်:** +ပမာဏ (ဥပမာ: +5000)\n"
            "**ငွေထုတ်ရန်:** -ပမာဏ (ဥပမာ: -2000)",
            parse_mode='Markdown'
        )
        return
    
    # PRIVATE CHAT
    if chat.type == 'private':
        print("✅ Private chat - showing appropriate menu")
        if user.id == OWNER_ID:
            # Owner DM menu
            keyboard = [
                [InlineKeyboardButton("Welcome Setting", callback_data='welcome_setting')],
                [InlineKeyboardButton("Broadcast", callback_data='broadcast')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "👑 **ပိုင်ရှင် ထိန်းချုပ်ခန်း**\n\nအောက်ပါရွေးချယ်စရာများကိုနှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # User DM menu
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
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    print(f"CALLBACK: {data} from user {user.id}")
    
    # ===== USER CALLBACKS =====
    if data == 'account_info':
        user_data = get_user(user.id)
        
        # Format today's stats
        today_deposit = user_data['today_deposit']
        today_withdraw = user_data['today_withdraw']
        today_bet = user_data['today_bet']
        balance = user_data['balance']
        
        await query.edit_message_text(
            f"**အမည်** - {user_data['name']}\n"
            f"**ID** - `{user_data['user_id']}`\n"
            f"**Mention** - {user_data['mention']}\n"
            f"**ယနေ့သွင်းငွေ** - {today_deposit} ကျပ်\n"
            f"**ယနေ့ထုတ်ငွေ** - {today_withdraw} ကျပ်\n"
            f"**ယနေ့လောင်းငွေ** - {today_bet} ကျပ်\n"
            f"**လက်ကျန်ငွေ** - {balance} ကျပ်",
            parse_mode='Markdown'
        )
    
    elif data == 'play_game':
        keyboard = [[InlineKeyboardButton("🎲 JOIN GROUP", url=GAME_GROUP_URL)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "ကစားရန် Game Group ကိုသွားပါ။",
            reply_markup=reply_markup
        )
    
    elif data == 'cancel_bet':
        game = get_current_game()
        if not game or game['status'] != 'open':
            await query.message.reply_text("❌ လက်ရှိ ဂိမ်းမရှိပါ။")
            return
        
        # Check if within 20 minutes of game start
        game_start = datetime.fromisoformat(game['created_at'])
        if datetime.now() - game_start > timedelta(minutes=20):
            await query.message.reply_text("⏰ လောင်းကြေး ပယ်ဖျက်ရန် အချိန်ကျော်လွန်သွားပါပြီ။")
            return
        
        amount = cancel_user_bet(game['game_id'], user.id)
        if amount > 0:
            new_balance = update_balance(user.id, amount, 'add')
            msg = await query.message.reply_text(
                f"✅ လောင်းကြေးပြန်ဖျက်ပြီးပါပြီ။\n"
                f"💰 **လက်ကျန်ငွေ** ➖ {new_balance}Ks",
                parse_mode='Markdown'
            )
            # Auto delete after 5 seconds
            await asyncio.sleep(5)
            await msg.delete()
        else:
            await query.message.reply_text("❌ သင်လောင်းထားတဲ့ငွေ မရှိပါ။")
    
    # ===== OWNER CALLBACKS =====
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
            "- Button ပါလိုချင်ရင် Button Name နဲ့ URL ကို ဒီပုံစံအတိုင်း ရိုက်ပါ:\n"
            "`ButtonName|https://example.com`\n\n"
            "ဥပမာ: `Channel|https://t.me/your_channel`",
            parse_mode='Markdown'
        )
        context.user_data['awaiting'] = 'broadcast'
    
    elif data == 'back_to_owner' and user.id == OWNER_ID:
        keyboard = [
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
    
    # ===== GAME GROUP - OWNER BUTTON HANDLER (Owner အတွက်သာ) =====
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        print(f"🔘 Owner button pressed: {text}")
        
        # Remove emoji for comparison if needed
        clean_text = text.replace("🎮 ", "").replace("⏹️ ", "").replace("🔴 ", "").replace("🔵 ", "").replace("🟣 ", "")
        
        if text == "🎮 Game စတင်ရန်" or clean_text == "Game စတင်ရန်":
            game_id = create_game()
            
            await context.bot.send_message(
                chat_id=GAME_GROUP_ID,
                text=f"🌟 **ပွဲစဉ်** ➖ `{game_id}`\n"
                     f"✅ **စတင်လောင်းလို့ရပါပြီ!**\n"
                     f"➖➖➖➖➖➖➖➖➖➖\n\n"
                     f"**ကစားနည်း:** S100, B100, J100 စသည်ဖြင့်ရိုက်ထည့်ပါ။",
                parse_mode='Markdown'
            )
            return
            
        elif text == "⏹️ Game ပိတ်ရန်" or clean_text == "Game ပိတ်ရန်":
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
            
            await asyncio.sleep(1)
            
            game = get_current_game()
            if game:
                bets = get_game_bets(game['game_id'])
                
                summary = f"✨ **ပွဲစဉ်** ➖ `{game['game_id']}`\n"
                summary += f"➖ **လောင်းကြေးပိတ်ပါပြီ!** ➖\n\n"
                
                for bet in bets:
                    multiplier = "5ဆ" if bet['bet_type'] == 'japort' else "2ဆ"
                    summary += f"👤 {bet['user_name']} ➖ {bet['bet_type']}({bet['bet_type'][0]}) {bet['amount']} ({multiplier})\n"
                
                await context.bot.send_message(chat_id=GAME_GROUP_ID, text=summary, parse_mode='Markdown')
                await context.bot.send_message(
                    chat_id=GAME_GROUP_ID,
                    text="🎲 **အံစာတုံး ၂ တုံး ပို့ဖို့ စောင့်နေပါသည်။**",
                    parse_mode='Markdown'
                )
                
                context.user_data['awaiting_dice'] = game['game_id']
            return
            
        elif text in ["🔴 Small", "🔵 Big", "🟣 Japort 7", "Small", "Big", "Japort 7"]:
            # Manual result entry (fallback)
            result_type = clean_text.lower()
            if result_type == "japort 7":
                result_type = "japort"
            
            game_id = context.user_data.get('awaiting_dice')
            if not game_id:
                await update.message.reply_text("❌ လက်ရှိ ဂိမ်းမရှိပါ။")
                return
            
            winners = update_bet_results(game_id, result_type)
            
            # Process winners
            winner_list = []
            for bet in winners:
                multiplier = 5 if result_type == 'japort' else 2
                winnings = bet[4] * multiplier
                new_balance = update_balance(bet[2], winnings, 'add')
                
                user_info = get_user(bet[2])
                winner_list.append({
                    'user_name': user_info['name'],
                    'amount': bet[4],
                    'winnings': winnings,
                    'new_balance': new_balance
                })
            
            # Send results
            result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
            result_text += f"💥 **Dice ပွဲစဉ်ရလဒ်** 💥\n"
            result_text += f"**Result:** {result_type.upper()} "
            
            if result_type == 'japort':
                result_text += "{5ဆ}\n"
            else:
                result_text += "{2ဆ}\n"
            
            result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
            
            for winner in winner_list:
                result_text += f"👤 {winner['user_name']} ➖ {result_type}({result_type[0]}) \n"
                result_text += f"💰 {winner['amount']}(လောင်း) + {winner['winnings'] - winner['amount']}(မြတ်) = {winner['winnings']}(စုစုပေါင်း)\n"
                result_text += f"💳 **လက်ကျန်** ➖ {winner['new_balance']}Ks\n\n"
            
            await context.bot.send_message(chat_id=GAME_GROUP_ID, text=result_text, parse_mode='Markdown')
            
            # Close game
            close_game(game_id)
            
            # Reset permissions
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
            
            del context.user_data['awaiting_dice']
            return
    
    # ===== DEPOSIT GROUP HANDLER =====
    if chat.id == DEPOSIT_GROUP_ID:
        # User sends "1" to get their info
        if text == "1":
            print(f"💰 User {user.id} sent 1 in deposit group")
            user_data = get_user(user.id)
            
            reply_msg = f"""**အမည်** - {user_data['name']}
**ID** - `{user_data['user_id']}`
**Mention** - {user_data['mention']}
**ယနေ့သွင်းငွေ** - {user_data['today_deposit']} ကျပ်
**ယနေ့လောင်းငွေ** - {user_data['today_bet']} ကျပ်
**ယနေ့ထုတ်ငွေ** - {user_data['today_withdraw']} ကျပ်
**လက်ကျန်ငွေ** - {user_data['balance']} ကျပ်"""
            
            # Reply to the user's "1" message
            await update.message.reply_to_message.reply_text(reply_msg, parse_mode='Markdown')
            print("✅ Response sent to user")
            return
        
        # Owner reply for deposit/withdraw - FIXED VERSION
        elif update.message.reply_to_message and user.id == OWNER_ID:
            replied = update.message.reply_to_message
            print(f"Reply detected: {replied.text}")
            print(f"Reply from bot: {replied.from_user.id == context.bot.id}")
            
            if replied.from_user.id == context.bot.id:
                # Extract user_id from replied message using multiple patterns
                target_user_id = None
                
                # Pattern 1: ID - `123456789`
                match = re.search(r'ID - `(\d+)`', replied.text)
                if match:
                    target_user_id = match.group(1)
                    print(f"Found ID with pattern 1: {target_user_id}")
                
                # Pattern 2: ID - 123456789
                if not target_user_id:
                    match = re.search(r'ID - (\d+)', replied.text)
                    if match:
                        target_user_id = match.group(1)
                        print(f"Found ID with pattern 2: {target_user_id}")
                
                # Pattern 3: Just numbers in the message
                if not target_user_id:
                    numbers = re.findall(r'\d+', replied.text)
                    if numbers:
                        # Take the longest number as ID
                        target_user_id = max(numbers, key=len)
                        print(f"Found ID with pattern 3: {target_user_id}")
                
                if target_user_id:
                    print(f"Target user ID: {target_user_id}")
                    
                    if text.startswith('+'):
                        try:
                            amount = int(text[1:])
                            print(f"Adding amount: {amount}")
                            
                            new_balance = update_balance(target_user_id, amount, 'add')
                            update_today_stats(target_user_id, 'today_deposit', amount)
                            
                            user_data = get_user(target_user_id)
                            
                            await update.message.reply_text(
                                f"✅ **ငွေသွင်းပြီးပါပြီ**\n\n"
                                f"**အမည်** - {user_data['name']}\n"
                                f"**ID** - `{target_user_id}`\n"
                                f"**ထည့်လိုက်တဲ့ငွေ** - {amount} ကျပ်\n"
                                f"**လက်ကျန်ငွေ** - {new_balance} ကျပ်",
                                parse_mode='Markdown'
                            )
                            
                            # Send to game group
                            await context.bot.send_message(
                                chat_id=GAME_GROUP_ID,
                                text=f"👤 {user_data['name']} လူကြီးမင်း၏ ဂိမ်းအကောင့်ထဲသို့ {amount} ကျပ် ထည့်သွင်းပေးလိုက်ပါပြီ။\n🎲 ဂိမ်းစတင်ကစားနိုင်ပါပြီ။"
                            )
                            print("Deposit completed successfully")
                        except Exception as e:
                            print(f"Error in deposit: {e}")
                            await update.message.reply_text(f"❌ ငွေသွင်းရာတွင် အဆင်မပြေပါ။ {e}")
                    
                    elif text.startswith('-'):
                        try:
                            amount = int(text[1:])
                            print(f"Withdrawing amount: {amount}")
                            
                            new_balance = update_balance(target_user_id, amount, 'subtract')
                            update_today_stats(target_user_id, 'today_withdraw', amount)
                            
                            user_data = get_user(target_user_id)
                            await update.message.reply_text(
                                f"✅ **ငွေထုတ်ပြီးပါပြီ**\n\n"
                                f"**အမည်** - {user_data['name']}\n"
                                f"**ID** - `{target_user_id}`\n"
                                f"**ထုတ်လိုက်တဲ့ငွေ** - {amount} ကျပ်\n"
                                f"**လက်ကျန်ငွေ** - {new_balance} ကျပ်",
                                parse_mode='Markdown'
                            )
                            print("Withdrawal completed successfully")
                        except Exception as e:
                            print(f"Error in withdrawal: {e}")
                            await update.message.reply_text(f"❌ ငွေထုတ်ရာတွင် အဆင်မပြေပါ။ {e}")
                else:
                    await update.message.reply_text("❌ User ID ကို ရှာမတွေ့ပါ။")
    
    # ===== GAME GROUP USER BET HANDLER =====
    elif chat.id == GAME_GROUP_ID:
        # Check if game is open
        game = get_current_game()
        if not game or game['status'] != 'open':
            return
        
        # Check if user is trying to bet
        bet_type, amount = parse_bet(text)
        if bet_type:
            # Check small/big together
            if has_both_small_big(text):
                await update.message.reply_text("❌ Small နဲ့ Big တစ်ပြိုင်နက် လောင်းလို့မရပါ။")
                return
            
            # Check minimum bet
            if amount < 100:
                await update.message.reply_text("❌ အနည်းဆုံး လောင်းကြေး ၁၀၀ ကျပ်ဖြစ်ပါတယ်။")
                return
            
            # Check balance
            user_data = get_user(user.id)
            if user_data['balance'] < amount:
                await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ။")
                return
            
            # Save bet
            save_bet(game['game_id'], user.id, bet_type, amount)
            new_balance = update_balance(user.id, amount, 'subtract')
            
            multiplier = "5ဆ" if bet_type == 'japort' else "2ဆ"
            bet_display = "Japort(Jp7)" if bet_type == 'japort' else f"{bet_type.capitalize()}({bet_type[0]})"
            
            await update.message.reply_to_message.reply_text(
                f"🎲 **ပွဲစဉ်** ➖ `{game['game_id']}`\n"
                f"➖➖➖➖➖\n"
                f"**{bet_display}** - {amount}Ks ({multiplier})\n"
                f"➖➖➖➖➖\n"
                f"✅ **အောင်မြင်စွာ လောင်းကြေးတင်ပြီးပါပြီ။**\n"
                f"💰 **လက်ကျန်ငွေ** ➖ {new_balance}Ks",
                parse_mode='Markdown'
            )
    
    # ===== OWNER DM HANDLERS =====
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
                # Parse broadcast message
                caption = update.message.caption or update.message.text
                photo_id = update.message.photo[-1].file_id if update.message.photo else None
                
                # Check for button
                reply_markup = None
                if caption and '|' in caption and 'http' in caption:
                    lines = caption.split('\n')
                    button_line = lines[-1]
                    if '|' in button_line:
                        button_name, button_url = button_line.split('|', 1)
                        caption = '\n'.join(lines[:-1])
                        keyboard = [[InlineKeyboardButton(button_name.strip(), url=button_url.strip())]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Get all users
                users = get_all_users()
                
                # Send in batches of 20 with 1 second delay
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
                        await asyncio.sleep(0.1)  # Small delay between messages
                    
                    await asyncio.sleep(1)  # 1 second delay between batches
                
                await update.message.reply_text(f"✅ Broadcast ပို့ပြီးပါပြီ။ လက်ခံသူ {sent_count} ယောက်")
                del context.user_data['awaiting']

async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    print(f"DICE: from user {user.id} in chat {chat.id}")
    
    # Only process dice in game group from owner
    if chat.id == GAME_GROUP_ID and user.id == OWNER_ID:
        if update.message.dice:
            dice_value = update.message.dice.value
            print(f"Dice value: {dice_value}")
            
            # Store dice values
            if 'dice1' not in context.user_data:
                context.user_data['dice1'] = dice_value
                context.user_data['dice1_msg_id'] = update.message.message_id
                print("First dice stored")
            elif 'dice2' not in context.user_data:
                context.user_data['dice2'] = dice_value
                context.user_data['dice2_msg_id'] = update.message.message_id
                print("Second dice stored")
                
                # Both dice received, calculate result
                dice1 = context.user_data['dice1']
                dice2 = context.user_data['dice2']
                total = dice1 + dice2
                
                print(f"Calculating result: {dice1}+{dice2}={total}")
                
                # Determine result: 2-6 = small, 7 = japort, 8-12 = big
                if 2 <= total <= 6:
                    result_type = 'small'
                elif total == 7:
                    result_type = 'japort'
                elif 8 <= total <= 12:
                    result_type = 'big'
                else:
                    result_type = 'unknown'
                
                print(f"Result type: {result_type}")
                
                # Get current game
                game_id = context.user_data.get('awaiting_dice')
                if game_id:
                    print(f"Processing game {game_id}")
                    
                    # Update bet results
                    winners = update_bet_results(game_id, result_type)
                    print(f"Winners: {len(winners)}")
                    
                    # Process winners and calculate winnings
                    winner_list = []
                    for bet in winners:
                        multiplier = 5 if result_type == 'japort' else 2
                        winnings = bet[4] * multiplier
                        new_balance = update_balance(bet[2], winnings, 'add')
                        
                        user_info = get_user(bet[2])
                        winner_list.append({
                            'user_name': user_info['name'],
                            'amount': bet[4],
                            'winnings': winnings,
                            'new_balance': new_balance
                        })
                    
                    # Create result image
                    try:
                        img_bytes = create_result_image(dice1, dice2, total, result_type, winner_list)
                        
                        # Send results with image
                        result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
                        result_text += f"💥 **Dice ပွဲစဉ်ရလဒ်** 💥\n"
                        result_text += f"{dice1}+{dice2} = {total}  **{result_type.upper()}** "
                        
                        if result_type == 'japort':
                            result_text += "{5ဆ}\n"
                        else:
                            result_text += "{2ဆ}\n"
                        
                        result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
                        
                        for winner in winner_list:
                            result_text += f"👤 {winner['user_name']} ➖ {result_type}({result_type[0]}) \n"
                            result_text += f"💰 {winner['amount']}(လောင်း) + {winner['winnings'] - winner['amount']}(မြတ်) = {winner['winnings']}(စုစုပေါင်း)\n"
                            result_text += f"💳 **လက်ကျန်** ➖ {winner['new_balance']}Ks\n\n"
                        
                        await context.bot.send_photo(
                            chat_id=GAME_GROUP_ID,
                            photo=img_bytes,
                            caption=result_text,
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        print(f"Error creating/sending image: {e}")
                        # Send without image
                        result_text = f"🎉 **ပွဲစဉ်** ➖ `{game_id}`\n"
                        result_text += f"💥 **Dice ပွဲစဉ်ရလဒ်** 💥\n"
                        result_text += f"{dice1}+{dice2} = {total}  **{result_type.upper()}** "
                        
                        if result_type == 'japort':
                            result_text += "{5ဆ}\n"
                        else:
                            result_text += "{2ဆ}\n"
                        
                        result_text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
                        
                        for winner in winner_list:
                            result_text += f"👤 {winner['user_name']} ➖ {result_type}({result_type[0]}) \n"
                            result_text += f"💰 {winner['amount']}(လောင်း) + {winner['winnings'] - winner['amount']}(မြတ်) = {winner['winnings']}(စုစုပေါင်း)\n"
                            result_text += f"💳 **လက်ကျန်** ➖ {winner['new_balance']}Ks\n\n"
                        
                        await context.bot.send_message(chat_id=GAME_GROUP_ID, text=result_text, parse_mode='Markdown')
                    
                    # Close game
                    close_game(game_id)
                    
                    # Reset permissions (allow users to chat again)
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
                    
                    # Clear user data
                    del context.user_data['dice1']
                    del context.user_data['dice2']
                    del context.user_data['dice1_msg_id']
                    del context.user_data['dice2_msg_id']
                    del context.user_data['awaiting_dice']
                    
                    print("✅ Game completed and cleaned up")

# ==================== MAIN ====================
def main():
    # Initialize database
    init_db()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Dice.ALL, handle_dice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_message))
    
    # Start bot
    print("=" * 60)
    print("🤖 BOT STARTED SUCCESSFULLY")
    print("=" * 60)
    print(f"👑 Owner ID: {OWNER_ID}")
    print(f"🎮 Game Group ID: {GAME_GROUP_ID}")
    print(f"💰 Deposit Group ID: {DEPOSIT_GROUP_ID}")
    print("=" * 60)
    print("✅ Features:")
    print("   - Game Group: /start shows 5 buttons (OWNER ONLY)")
    print("   - Deposit Group: '1' for user info, +amount/-amount for owner")
    print("   - Owner DM: Welcome Setting & Broadcast")
    print("=" * 60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
