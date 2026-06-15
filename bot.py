import logging
import random
import time
import asyncio
import os
import re
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import io
from pymongo import MongoClient

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))
GAME_GROUP_ID = int(os.environ.get("GAME_GROUP_ID", "-1002849045181"))

# ==================== MONGODB (Waifu Bot) ====================
MONGODB_URL = os.environ.get("MONGODB_URL")
EXCHANGE_RATE = 4350  # 1$ = 4350 MMK

def get_waifu_coins(user_id: int):
    """Get user's $ balance from waifu_bot MongoDB."""
    if not MONGODB_URL:
        return None
    try:
        client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        db = client["waifu_bot"]
        user = db["users"].find_one({"id": user_id})
        client.close()
        if user is not None:
            raw = float(user.get("coins", 0))
            return round(raw / 100, 4)
        return None
    except Exception as e:
        print(f"MongoDB error: {e}")
        return None

def subtract_waifu_coins(user_id: int, amount_dollars: float) -> bool:
    """Subtract dollars from waifu_bot."""
    if not MONGODB_URL:
        return False
    try:
        coins_to_deduct = round(amount_dollars * 100, 4)
        client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        db = client["waifu_bot"]
        result = db["users"].update_one(
            {"id": user_id, "coins": {"$gte": coins_to_deduct}},
            {"$inc": {"coins": -coins_to_deduct}}
        )
        client.close()
        return result.modified_count > 0
    except Exception as e:
        print(f"MongoDB error: {e}")
        return False

def add_waifu_coins(user_id: int, amount_dollars: float) -> bool:
    """Add dollars to waifu_bot."""
    if not MONGODB_URL:
        return False
    try:
        coins_to_add = round(amount_dollars * 100, 4)
        client = MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
        db = client["waifu_bot"]
        result = db["users"].update_one(
            {"id": user_id},
            {"$inc": {"coins": coins_to_add}}
        )
        client.close()
        return result.modified_count > 0
    except Exception as e:
        print(f"MongoDB error: {e}")
        return False

MIN_BET = 500
MAX_BET = 1000000

# ==================== JSON DATABASE (SQLite REPLACEMENT) ====================
DB_FILE = "database.json"

def _load_db():
    """Load database from JSON file"""
    if not os.path.exists(DB_FILE):
        return {
            "users": {},
            "games": [],
            "bets": [],
            "admins": [],
            "game_images": {},
            "settings": {},
            "game_id_counter": 100000
        }
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_db(data):
    """Save database to JSON file"""
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)

# ==================== USERS ====================
def get_user(user_id):
    db = _load_db()
    user = db["users"].get(str(user_id))
    if user:
        user["user_id"] = user_id
    return user

def get_user_by_username(username):
    if not username.startswith('@'):
        username = '@' + username
    db = _load_db()
    for uid, user in db["users"].items():
        if user.get("mention") == username:
            user["user_id"] = int(uid)
            return user
    return None

def create_or_update_user(user_id, name, mention):
    db = _load_db()
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": name,
            "mention": mention,
            "total_bet": 0,
            "total_win": 0,
            "balance": 0
        }
    else:
        db["users"][uid]["name"] = name
        db["users"][uid]["mention"] = mention
    _save_db(db)

def update_balance(user_id, amount, operation='add'):
    db = _load_db()
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": "Unknown",
            "mention": "",
            "total_bet": 0,
            "total_win": 0,
            "balance": 0
        }

    if operation == 'add':
        db["users"][uid]["balance"] += amount
    else:
        db["users"][uid]["balance"] -= amount

    new_balance = db["users"][uid]["balance"]
    _save_db(db)
    return new_balance

def update_user_stats(user_id, bet_amount, win_amount=0):
    db = _load_db()
    uid = str(user_id)
    if uid in db["users"]:
        db["users"][uid]["total_bet"] += bet_amount
        db["users"][uid]["total_win"] += win_amount
        _save_db(db)

# ==================== GAMES ====================
def get_next_game_id():
    db = _load_db()
    db["game_id_counter"] = db.get("game_id_counter", 100000) + 1
    _save_db(db)
    return db["game_id_counter"]

def get_current_game():
    db = _load_db()
    for game in reversed(db["games"]):
        if game.get("status") == "open":
            return game
    return None

def create_game(chat_id):
    game_id = get_next_game_id()
    db = _load_db()
    game = {
        "id": len(db["games"]) + 1,
        "game_id": game_id,
        "chat_id": chat_id,
        "status": "open",
        "result_number": None,
        "total_bet_amount": 0,
        "total_win_amount": 0,
        "owner_profit": 0,
        "created_at": datetime.now().isoformat(),
        "closed_at": None
    }
    db["games"].append(game)
    _save_db(db)
    return game_id

def close_game(game_id, result_number, total_win_amount, owner_profit):
    db = _load_db()
    for game in db["games"]:
        if game["game_id"] == game_id:
            game["status"] = "closed"
            game["result_number"] = result_number
            game["total_win_amount"] = total_win_amount
            game["owner_profit"] = owner_profit
            game["closed_at"] = datetime.now().isoformat()
            break
    _save_db(db)

def get_game_bets(game_id):
    db = _load_db()
    bets = []
    for bet in db["bets"]:
        if bet["game_id"] == game_id:
            user = get_user(bet["user_id"])
            bet_copy = bet.copy()
            bet_copy["user_name"] = user["name"] if user else "Unknown"
            bets.append(bet_copy)
    return bets

# ==================== BETS ====================
def save_bet(game_id, user_id, bet_number, amount):
    db = _load_db()
    bet = {
        "id": len(db["bets"]) + 1,
        "game_id": game_id,
        "user_id": user_id,
        "bet_number": bet_number,
        "amount": amount,
        "status": "pending",
        "win_amount": 0,
        "timestamp": datetime.now().isoformat()
    }
    db["bets"].append(bet)

    for game in db["games"]:
        if game["game_id"] == game_id:
            game["total_bet_amount"] += amount
            break

    _save_db(db)
    update_user_stats(user_id, amount, 0)

def cancel_bet_db(game_id, user_id):
    db = _load_db()
    total_refund = 0
    remaining_bets = []

    for bet in db["bets"]:
        if bet["game_id"] == game_id and bet["user_id"] == user_id and bet["status"] == "pending":
            total_refund += bet["amount"]
        else:
            remaining_bets.append(bet)

    if total_refund > 0:
        db["bets"] = remaining_bets
        for game in db["games"]:
            if game["game_id"] == game_id:
                game["total_bet_amount"] -= total_refund
                break
        _save_db(db)

    return total_refund

def update_bet_results(game_id, result_number):
    db = _load_db()
    winners = []
    total_win_amount = 0

    for bet in db["bets"]:
        if bet["game_id"] == game_id:
            if bet["bet_number"] == result_number:
                win_amount = bet["amount"] * result_number
                bet["status"] = "won"
                bet["win_amount"] = win_amount
                winners.append(bet)
                total_win_amount += win_amount
                uid = str(bet["user_id"])
                if uid in db["users"]:
                    db["users"][uid]["total_win"] += win_amount
            else:
                bet["status"] = "lost"

    _save_db(db)
    return winners, total_win_amount

def get_user_bets(user_id, game_id=None):
    db = _load_db()
    result = []
    for bet in db["bets"]:
        if bet["user_id"] == user_id:
            if game_id is None or bet["game_id"] == game_id:
                result.append((
                    bet["id"], bet["game_id"], bet["user_id"],
                    bet["bet_number"], bet["amount"], bet["status"],
                    bet["win_amount"], bet["timestamp"]
                ))
    return result

def get_user_bet_count_for_game(user_id, game_id):
    db = _load_db()
    count = 0
    for bet in db["bets"]:
        if bet["user_id"] == user_id and bet["game_id"] == game_id:
            count += 1
    return count

# ==================== ADMINS ====================
def add_admin(user_id, name):
    db = _load_db()
    db["admins"] = [a for a in db["admins"] if a["user_id"] != user_id]
    db["admins"].append({
        "user_id": user_id,
        "name": name,
        "added_at": datetime.now().isoformat()
    })
    _save_db(db)

def remove_admin(user_id):
    db = _load_db()
    original_count = len(db["admins"])
    db["admins"] = [a for a in db["admins"] if a["user_id"] != user_id]
    if len(db["admins"]) < original_count:
        _save_db(db)
        return True
    return False

def is_admin(user_id):
    db = _load_db()
    return any(a["user_id"] == user_id for a in db["admins"])

def get_admins():
    db = _load_db()
    return [(a["user_id"], a["name"], a["added_at"]) for a in db["admins"]]

def is_staff(user_id):
    return user_id == OWNER_ID or is_admin(user_id)

# ==================== GAME IMAGES ====================
def save_game_image(image_type, photo_id, updated_by):
    db = _load_db()
    db["game_images"][image_type] = {
        "photo_id": photo_id,
        "updated_by": updated_by,
        "updated_at": datetime.now().isoformat()
    }
    _save_db(db)

def get_game_image(image_type):
    db = _load_db()
    img = db["game_images"].get(image_type)
    return img["photo_id"] if img else None

def delete_game_image(image_type):
    db = _load_db()
    if image_type in db["game_images"]:
        del db["game_images"][image_type]
        _save_db(db)

# ==================== SETTINGS ====================
def set_setting(key, value):
    db = _load_db()
    db["settings"][key] = value
    _save_db(db)

def get_setting(key, default=None):
    db = _load_db()
    return db["settings"].get(key, default)

# ==================== BACKUP & RESTORE ====================
def create_backup():
    db = _load_db()
    filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, default=str, ensure_ascii=False)
    return filename

def restore_backup(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        required_keys = ["users", "games", "bets", "admins", "game_images", "settings", "game_id_counter"]
        for key in required_keys:
            if key not in data:
                data[key] = {} if key not in ["games", "bets", "admins"] else []
        _save_db(data)
        return True, "Restore Successful"
    except Exception as e:
        return False, str(e)

def cleanup_stuck_games():
    """Close any open games from previous session."""
    db = _load_db()
    changed = False
    for game in db["games"]:
        if game.get("status") == "open":
            game["status"] = "closed"
            game["closed_at"] = datetime.now().isoformat()
            changed = True
    if changed:
        _save_db(db)
        print("⚠️ Closed stuck open games from previous session.")

# ==================== CHAT PERMISSION FUNCTIONS ====================
async def lock_chat(bot, chat_id):
    try:
        permissions = ChatPermissions(can_send_messages=False)
        await bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
    except Exception as e:
        print(f"Error locking chat {chat_id}: {e}")

async def unlock_chat(bot, chat_id):
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=False,
            can_invite_users=True,
            can_pin_messages=False
        )
        await bot.set_chat_permissions(chat_id=chat_id, permissions=permissions)
    except Exception as e:
        print(f"Error unlocking chat {chat_id}: {e}")

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

def get_user_game_keyboard():
    keyboard = [
        [
            KeyboardButton("👤 Profile"),
            KeyboardButton("❌ လောင်းကြေးပယ်ဖျက်"),
            KeyboardButton("❓ Help"),
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

# ==================== AUTO MODE LOGIC ====================
async def auto_game_loop(context: ContextTypes.DEFAULT_TYPE):
    while True:
        try:
            auto_dice = get_setting('auto_dice', 'off')
            if auto_dice != 'on':
                await asyncio.sleep(5)
                continue

            game = get_current_game()
            if not game:
                await asyncio.sleep(3)
                if get_setting('auto_dice', 'off') != 'on': continue

                await unlock_chat(context.bot, GAME_GROUP_ID)
                game_id = create_game(GAME_GROUP_ID)
                caption = (
                    f"🎲 *ပွဲစဉ်အသစ်* — `{game_id}`\n\n"
                    f"နံပါတ် ၁ မှ ၆ ထိ လောင်းနိုင်ပါသည်\n"
                    f"တစ်ယောက် နှစ်ကြိမ်အထိ လောင်းနိုင်သည် (မတူသောနံပါတ်)\n"
                    f"Min {MIN_BET:,}ကျပ် │ Max {MAX_BET:,}ကျပ်"
                )
                custom_image = get_game_image('game_start')
                try:
                    if custom_image:
                        await context.bot.send_photo(chat_id=GAME_GROUP_ID, photo=custom_image, caption=caption, parse_mode='Markdown', reply_markup=get_user_game_keyboard())
                    else:
                        await context.bot.send_message(chat_id=GAME_GROUP_ID, text=caption, parse_mode='Markdown', reply_markup=get_user_game_keyboard())
                except Exception as e:
                    print(f"Error starting game in {GAME_GROUP_ID}: {e}")
                continue

            bets = get_game_bets(game['game_id'])
            if bets:
                try:
                    await context.bot.send_message(chat_id=GAME_GROUP_ID, text="⏳ လောင်းကြေးများ ရောက်ရှိလာပါပြီ။ နောက် ၃၀ စက္ကန့်အတွင်း ပွဲပိတ်ပါမည်။")
                except: pass
                await asyncio.sleep(30)

                game = get_current_game()
                if not game: continue

                await lock_chat(context.bot, GAME_GROUP_ID)
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
                try:
                    if custom_image:
                        await context.bot.send_photo(chat_id=GAME_GROUP_ID, photo=custom_image, caption=bet_text, parse_mode='Markdown', reply_markup=get_owner_button())
                    else:
                        await context.bot.send_message(chat_id=GAME_GROUP_ID, text=bet_text, parse_mode='Markdown', reply_markup=get_owner_button())

                    await context.bot.send_message(chat_id=GAME_GROUP_ID, text="🤖 *Auto Dice Mode: ON*\nBot မှ အလိုအလျောက် အံစာတုံးလှည့်ပေးနေပါသည်...", parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
                    await asyncio.sleep(2)

                    dice_msg = await context.bot.send_dice(chat_id=GAME_GROUP_ID)
                    dice_value = dice_msg.dice.value

                    await asyncio.sleep(4)
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
                        await context.bot.send_photo(chat_id=GAME_GROUP_ID, photo=custom_image, caption=result_text, parse_mode='Markdown', reply_markup=get_owner_button())
                    else:
                        await context.bot.send_message(chat_id=GAME_GROUP_ID, text=result_text, parse_mode='Markdown', reply_markup=get_owner_button())

                    await context.bot.send_message(chat_id=GAME_GROUP_ID, text="🔚 ပွဲစဉ်ပြီးပါပြီ")
                    await unlock_chat(context.bot, GAME_GROUP_ID)

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
                    except: pass
                except Exception as e:
                    print(f"Error processing game result: {e}")

                await asyncio.sleep(3)
            else:
                await asyncio.sleep(5)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"ERROR in auto_game_loop: {e}")
            await asyncio.sleep(10)

# ==================== COMMAND HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    print(f"START: {user.id} in {chat.id}")

    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)

    if chat.type not in ['private', 'group', 'supergroup']:
        return

    if chat.type in ['group', 'supergroup']:
        if is_staff(user.id):
            label = "👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*" if user.id == OWNER_ID else "🛡 *Admin ထိန်းချုပ်ခန်း*"
            keyboard = [
                [InlineKeyboardButton("🟢 ဂိမ်းစတင်ရန်", callback_data='game_start')],
                [InlineKeyboardButton("🔴 ဂိမ်းပိတ်ရန်", callback_data='game_stop')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                text=f"{label}\n\nဂိမ်းစတင်ရန် သို့ ဂိမ်းပိတ်ရန် ခလုတ်နှိပ်ပါ။",
                reply_markup=reply_markup,
                parse_mode='Markdown',
                do_quote=True
            )
        else:
            text = (
                "🎲 *ကစားနည်း*\n\n"
                "နံပါတ် ရွေးပြီး လောင်းကြေးတင်ပါ\n"
                "`1 500` ` 2 200` ` 3 50`\n\n"
                f"💰 အနည်းဆုံး {MIN_BET:,}ကျပ်  အများဆုံး {MAX_BET:,}ကျပ်\n"
                "📌 တစ်ယောက် တစ်ခါသာ လောင်းနိုင်သည်\n"
                "💬 Group တွင် တိုက်ရိုက်ရိုက်ပို့နိုင်သည်"
            )
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=get_user_game_keyboard(),
                do_quote=True
            )
        return

    if chat.type == 'private' and user.id == OWNER_ID:
        auto_dice = get_setting('auto_dice', 'off')
        auto_dice_label = "🎲 Auto Dice: ON" if auto_dice == 'on' else "🎲 Auto Dice: OFF"
        keyboard = [
            [InlineKeyboardButton("🖼 Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼 Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼 Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton(auto_dice_label, callback_data='toggle_auto_dice')],
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
        return

    if chat.type == 'private' and is_admin(user.id):
        await update.message.reply_text(
            "🛡 *Admin ထိန်းချုပ်ခန်း*\n\nGroup ထဲတွင် /start နှိပ်ပြီး ဂိမ်းစ/ပိတ်နိုင်သည်။",
            parse_mode='Markdown'
        )

# ==================== CALLBACK HANDLER ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    chat_id = query.message.chat.id

    print(f"CALLBACK: {data} from {user.id}")

    try:
        await _handle_callback_inner(update, context, query, user, data, chat_id)
    except Exception as e:
        print(f"❌ CALLBACK ERROR [{data}]: {e}")
        import traceback; traceback.print_exc()
        try:
            await query.answer("❌ Error ဖြစ်ပါသည်", show_alert=True)
        except Exception:
            pass

async def _handle_callback_inner(update, context, query, user, data, chat_id):
    if data in ['game_start', 'game_stop']:
        if not is_staff(user.id):
            await query.answer("Staff သာ အသုံးပြုနိုင်သည်", show_alert=True)
            return

        if data == 'game_start':
            try:
                if get_current_game():
                    await query.answer("❌ ဂိမ်းအဖွင့်ရှိပြီးသားပါ။ /resetgame သုံးပါ", show_alert=True)
                    return

                await query.answer("ဂိမ်းစတင်နေပါပြီ...")

                global GAME_GROUP_ID
                if chat_id != OWNER_ID:
                    GAME_GROUP_ID = chat_id

                await unlock_chat(context.bot, chat_id)
                game_id = create_game(chat_id)

                caption = (
                    f"🎲 *ပွဲစဉ်အသစ်* — `{game_id}`\n\n"
                    f"နံပါတ် ၁ မှ ၆ ထိ လောင်းနိုင်ပါသည်\n"
                    f"တစ်ယောက် နှစ်ကြိမ်အထိ လောင်းနိုင်သည် (မတူသောနံပါတ်)\n"
                    f"Min {MIN_BET:,}ကျပ် │ Max {MAX_BET:,}ကျပ်"
                )

                custom_image = get_game_image('game_start')
                reply_markup = get_user_game_keyboard()

                try:
                    if custom_image:
                        await context.bot.send_photo(chat_id=chat_id, photo=custom_image, caption=caption, parse_mode='Markdown', reply_markup=reply_markup)
                    else:
                        await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode='Markdown', reply_markup=reply_markup)
                except Exception as e:
                    print(f"Error sending game start message: {e}")
                    await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode='Markdown', reply_markup=reply_markup)
            except Exception as e:
                print(f"CRITICAL ERROR in game_start: {e}")
                import traceback; traceback.print_exc()
                await query.message.reply_text(f"❌ ဂိမ်းစတင်ရန် အမှားအယွင်းရှိနေပါသည်: {str(e)}")
        elif data == 'game_stop':
            await query.answer()
            game = get_current_game()
            if not game:
                await query.message.reply_text("❌ ဂိမ်းမရှိပါ")
                return
            await lock_chat(context.bot, chat_id)
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
            try:
                if custom_image:
                    await context.bot.send_photo(chat_id=chat_id, photo=custom_image, caption=bet_text, parse_mode='Markdown', reply_markup=get_owner_button())
                else:
                    await context.bot.send_message(chat_id=chat_id, text=bet_text, parse_mode='Markdown', reply_markup=get_owner_button())
            except Exception as e:
                print(f"Error sending game stop: {e}")
                await context.bot.send_message(chat_id=chat_id, text=bet_text, parse_mode='Markdown', reply_markup=get_owner_button())
            await context.bot.send_message(chat_id=chat_id, text="🎲 Owner — ကျေးဇူးပြု၍ အံစာတုံး ၁ တုံး ပို့ပေးပါ ⏳", parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
            context.bot_data[f'current_game_id_{chat_id}'] = game_id
            context.bot_data[f'awaiting_dice_{chat_id}'] = True
        return

    elif data in ['set_start_image', 'set_stop_image', 'set_result_image', 'delete_images', 'del_start', 'del_stop', 'del_result', 'back_to_main', 'toggle_auto_dice', 'backup_data', 'restore_data']:
        if user.id != OWNER_ID:
            await query.answer("ပိုင်ရှင်အတွက်သာဖြစ်ပါသည်", show_alert=True)
            return

    if data == 'set_start_image':
        await query.answer()
        await query.edit_message_text("🖼 *Game Start ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။", parse_mode='Markdown')
        context.user_data['awaiting_image'] = 'game_start'

    elif data == 'set_stop_image':
        await query.answer()
        await query.edit_message_text("🖼 *Game Stop ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။", parse_mode='Markdown')
        context.user_data['awaiting_image'] = 'game_stop'

    elif data == 'set_result_image':
        await query.answer()
        await query.edit_message_text("🖼 *Result ပုံထည့်ရန်*\n\nပုံကိုပို့ပါ။", parse_mode='Markdown')
        context.user_data['awaiting_image'] = 'game_result'

    elif data == 'delete_images':
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🟢 Game Start ပုံဖျက်", callback_data='del_start')],
            [InlineKeyboardButton("🔴 Game Stop ပုံဖျက်", callback_data='del_stop')],
            [InlineKeyboardButton("🟡 Result ပုံဖျက်", callback_data='del_result')],
            [InlineKeyboardButton("◀️ နောက်သို့", callback_data='back_to_main')]
        ]
        await query.edit_message_text("🗑 *ဖျက်လိုသောပုံကိုရွေးပါ*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

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
        auto_dice = get_setting('auto_dice', 'off')
        auto_dice_label = "🎲 Auto Dice: ON" if auto_dice == 'on' else "🎲 Auto Dice: OFF"
        keyboard = [
            [InlineKeyboardButton("🖼 Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼 Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼 Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton(auto_dice_label, callback_data='toggle_auto_dice')],
            [InlineKeyboardButton("🗑 ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data'),
             InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        await query.edit_message_text("👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == 'toggle_auto_dice':
        current = get_setting('auto_dice', 'off')
        new_val = 'on' if current == 'off' else 'off'
        set_setting('auto_dice', new_val)
        await query.answer(f"🎲 Auto Dice: {new_val.upper()}")

        auto_dice_label = "🎲 Auto Dice: ON" if new_val == 'on' else "🎲 Auto Dice: OFF"
        keyboard = [
            [InlineKeyboardButton("🖼 Game Start ပုံထည့်", callback_data='set_start_image')],
            [InlineKeyboardButton("🖼 Game Stop ပုံထည့်", callback_data='set_stop_image')],
            [InlineKeyboardButton("🖼 Result ပုံထည့်", callback_data='set_result_image')],
            [InlineKeyboardButton(auto_dice_label, callback_data='toggle_auto_dice')],
            [InlineKeyboardButton("🗑 ပုံဖျက်ရန်", callback_data='delete_images')],
            [InlineKeyboardButton("💾 Backup", callback_data='backup_data'),
             InlineKeyboardButton("🔄 Restore", callback_data='restore_data')]
        ]
        await query.edit_message_text("👑 *ပိုင်ရှင် ထိန်းချုပ်ခန်း*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif data == 'backup_data':
        await query.answer()
        if get_current_game():
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှ Backup လုပ်ပါ။")
            return
        filename = create_backup()
        with open(filename, 'rb') as f:
            await context.bot.send_document(chat_id=user.id, document=f, filename=filename, caption=f"✅ *Backup အောင်မြင်ပါသည်*\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", parse_mode='Markdown')
        os.remove(filename)
        await query.message.reply_text("✅ Backup ပြီးပါပြီ")

    elif data == 'restore_data':
        await query.answer()
        if get_current_game():
            await query.message.reply_text("❌ ဂိမ်းအဖွင့်ရှိနေပါသည်။ ဂိမ်းပြီးမှ Restore လုပ်ပါ။")
            return
        await query.message.reply_text("🔄 *Restore လုပ်ရန် Backup ဖိုင်ကို ပို့ပါ*\n\nJSON ဖိုင်သာ လက်ခံမည်။", parse_mode='Markdown')
        context.user_data['awaiting_restore'] = True

# ==================== MESSAGE HANDLER ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text if update.message.text else ""

    print(f"MESSAGE: {text[:30]} from {user.id} in {chat.id}")

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

    if chat.type in ['group', 'supergroup']:
        if is_staff(user.id) and update.message.reply_to_message:
            reply_text = text.strip()
            if reply_text.startswith('+') or reply_text.startswith('-'):
                target_user = update.message.reply_to_message.from_user
                if not target_user.is_bot:
                    target_mention = f"@{target_user.username}" if target_user.username else target_user.full_name
                    create_or_update_user(target_user.id, target_user.full_name, target_mention)

                    try:
                        operation = 'add' if reply_text.startswith('+') else 'subtract'
                        amount = int(reply_text[1:])
                        user_data = get_user(target_user.id)

                        if operation == 'subtract' and user_data['balance'] < amount:
                            await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ")
                            return

                        prev_balance = user_data['balance']
                        new_balance = update_balance(target_user.id, amount, operation)

                        op_sign = "+" if operation == 'add' else "-"
                        op_name = "သွင်း" if operation == 'add' else "ထုတ်"

                        try:
                            await context.bot.send_message(
                                chat_id=target_user.id,
                                text=f"✅ *ငွေ{op_name}ပြီးပါပြီ*\n\n"
                                     f"👤 {user_data['name']}\n"
                                     f"🆔 `{target_user.id}`\n"
                                     f"💵 အရင်လက်ကျန်: {prev_balance:,} ကျပ်\n"
                                     f"💰 {op_name}ငွေ: {op_sign}{amount:,} ကျပ်\n"
                                     f"💳 လက်ကျန်အသစ်: {new_balance:,} ကျပ်",
                                parse_mode='Markdown'
                            )
                        except: pass

                        await update.message.reply_text(f"✅ {user_data['name']} ထံ {op_sign}{amount:,} ကျပ် {op_name}ပြီးပါပြီ")
                        return
                    except ValueError:
                        pass

        game = get_current_game()

        if text.lower().strip() in ['.bal', '/bal']:
            mention = f"@{user.username}" if user.username else user.full_name
            create_or_update_user(user.id, user.full_name, mention)
            user_data = get_user(user.id)
            if not user_data:
                return
            bet_text = ""
            if game:
                user_bets = get_user_bets(user.id, game['game_id'])
                if user_bets:
                    bet_text = "\n\n🎯 *ယခုလောင်းထားသောငွေ:*\n"
                    for b in user_bets:
                        bet_text += f"  နံပါတ် {b[3]} — {b[4]:,} ကျပ်\n"
            msg = await update.message.reply_text(
                f"💰 *{user_data['name']} ရဲ့ Balance*\n\n"
                f"💳 လက်ကျန်: `{user_data['balance']:,} ကျပ်`\n"
                f"📊 စုစုပေါင်းလောင်း: {user_data['total_bet']:,} ကျပ်\n"
                f"🏆 စုစုပေါင်းနိုင်: {user_data['total_win']:,} ကျပ်"
                f"{bet_text}",
                parse_mode='Markdown',
                do_quote=True
            )
            await asyncio.sleep(15)
            try: await msg.delete()
            except: pass
            return

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
                do_quote=True
            )
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass
            return

        if text == "❌ လောင်းကြေးပယ်ဖျက်":
            if not game:
                msg = await update.message.reply_text("❌ ယခုဂိမ်းမရှိပါ", do_quote=True)
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return
            refund = cancel_bet_db(game['game_id'], user.id)
            if refund == 0:
                msg = await update.message.reply_text("❌ လောင်းကြေးမရှိပါ", do_quote=True)
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
                do_quote=True
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
                f"💰 Min: {MIN_BET:,} ကျပ် │ Max: {MAX_BET:,} ကျပ်\n"
                "📌 တစ်ပွဲ နှစ်ကြိမ်အထိ (မတူသောနံပါတ်) လောင်းနိုင်သည်\n\n"
                "⏱ ဤစာ 10 စက္ကန့်အတွင်း ပျောက်သွားမည်",
                parse_mode='Markdown',
                do_quote=True
            )
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass
            return

        if is_staff(user.id) and update.message.reply_to_message:
            if text.startswith('+') or text.startswith('-'):
                replied = update.message.reply_to_message
                target_user = replied.from_user
                target_user_id = target_user.id

                if target_user.id == context.bot.id:
                    match = re.search(r'🆔\s*`?(\d+)`?', replied.text or "")
                    if match:
                        target_user_id = int(match.group(1))

                user_data = get_user(target_user_id)
                if not user_data:
                    await update.message.reply_text("❌ User ID မတွေ့ပါ။ User က /start လုပ်ထားဖို့လိုပါသည်။")
                    return

                try:
                    amount_str = text[1:].strip()
                    if not amount_str.isdigit():
                        return

                    amount = int(amount_str)
                    if text.startswith('+'):
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
                        await update.message.reply_text(f"✅ {user_data['mention']} ထံ {amount:,} ကျပ် ထည့်ပြီးပါပြီ")
                    else:
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
                        await update.message.reply_text(f"✅ {user_data['name']} ထံမှ {amount:,} ကျပ် ထုတ်ပြီးပါပြီ")
                    return
                except ValueError:
                    pass

        if not game or game['status'] != 'open':
            return

        bet_number, amount = parse_bet(text)
        if not bet_number or not amount:
            return

        mention = f"@{user.username}" if user.username else user.full_name
        create_or_update_user(user.id, user.full_name, mention)

        if amount < MIN_BET or amount > MAX_BET:
            msg = await update.message.reply_text(f"❌ Min {MIN_BET:,}ကျပ် — Max {MAX_BET:,}ကျပ်", do_quote=True)
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        bet_count = get_user_bet_count_for_game(user.id, game['game_id'])
        if bet_count >= 2:
            msg = await update.message.reply_text("❌ ဤပွဲစဉ်တွင် နှစ်ကြိမ်သာ လောင်းနိုင်သည်", do_quote=True)
            await asyncio.sleep(5)
            try: await msg.delete()
            except: pass
            return

        existing_bets = get_user_bets(user.id, game['game_id'])
        for eb in existing_bets:
            if eb[3] == bet_number:
                msg = await update.message.reply_text(f"❌ နံပါတ် {bet_number} ကို လောင်းပြီးပါပြီ — မတူသောနံပါတ်ကိုသာ ရွေးပါ", do_quote=True)
                await asyncio.sleep(5)
                try: await msg.delete()
                except: pass
                return

        user_data = get_user(user.id)
        if not user_data or user_data['balance'] < amount:
            msg = await update.message.reply_text("❌ လက်ကျန်ငွေ မလုံလောက်ပါ", do_quote=True)
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
            do_quote=True
        )

# ==================== DICE HANDLER ====================
async def handle_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    chat_id = chat.id

    if chat.type not in ['group', 'supergroup']:
        return
    if not is_staff(user.id) or not update.message.dice:
        return
    if not context.bot_data.get(f'awaiting_dice_{chat_id}'):
        await update.message.reply_text("❌ ယခုအချိန်တွင် အံစာတုံးမလိုအပ်ပါ။")
        return

    dice_value = update.message.dice.value
    game_id = context.bot_data.get(f'current_game_id_{chat_id}')
    print(f"🎲 DICE: {dice_value} for {game_id}")

    await asyncio.sleep(4)

    winners, total_win_amount = update_bet_results(game_id, dice_value)
    game = get_current_game()
    if not game:
        game = {'total_bet_amount': 0}

    total_bet_amount = game.get('total_bet_amount', 0)
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
        await context.bot.send_photo(chat_id=chat_id, photo=custom_image, caption=result_text, parse_mode='Markdown', reply_markup=get_owner_button())
    else:
        await context.bot.send_message(chat_id=chat_id, text=result_text, parse_mode='Markdown', reply_markup=get_owner_button())

    await context.bot.send_message(chat_id=chat_id, text="🔚 ပွဲစဉ်ပြီးပါပြီ")
    await unlock_chat(context.bot, chat_id)
    context.bot_data[f'awaiting_dice_{chat_id}'] = False

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
    except: pass

# ==================== ADMIN COMMANDS ====================
async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("❌ User ID ထည့်ပါ။\nသုံးနည်း: `/addadmin 123456789 Name`", parse_mode='Markdown')
        return
    try:
        admin_id = int(context.args[0])
        name = " ".join(context.args[1:]) if len(context.args) > 1 else f"Admin {admin_id}"
        add_admin(admin_id, name)
        await update.message.reply_text(f"✅ Admin Added: `{name}` ({admin_id})", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ User ID သည် ဂဏန်းဖြစ်ရပါမည်။")

async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("❌ User ID ထည့်ပါ။\nသုံးနည်း: `/removeadmin 123456789`", parse_mode='Markdown')
        return
    try:
        admin_id = int(context.args[0])
        if remove_admin(admin_id):
            await update.message.reply_text(f"✅ Admin Removed: `{admin_id}`", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ Admin `{admin_id}` ကို စာရင်းထဲတွင် မတွေ့ပါ။", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ User ID သည် ဂဏန်းဖြစ်ရပါမည်။")

async def listadmins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        return
    admins = get_admins()
    if not admins:
        await update.message.reply_text("📋 Admin မရှိသေးပါ။")
        return
    text = "📋 *Admins စာရင်း*\n\n"
    for i, row in enumerate(admins, 1):
        text += f"{i}. {row[1]} — `{row[0]}`\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def mmk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_staff(user.id):
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ သုံးနည်း: `/mmk [user id/username] [+-amount]`\nဥပမာ: `/mmk 123456789 +5000` သို့မဟုတ် `/mmk @username -2000`", parse_mode='Markdown')
        return

    target_input = context.args[0]
    amount_input = context.args[1]

    user_data = None
    if target_input.isdigit():
        user_data = get_user(target_input)
    else:
        user_data = get_user_by_username(target_input)

    if not user_data:
        await update.message.reply_text(f"❌ User `{target_input}` ကို မတွေ့ပါ။ User က /start လုပ်ထားဖို့လိုပါသည်။", parse_mode='Markdown')
        return

    target_user_id = user_data['user_id']

    try:
        if amount_input.startswith('+'):
            amount = int(amount_input[1:])
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
            except: pass
            await update.message.reply_text(f"✅ {user_data['mention']} ထံ {amount:,} ကျပ် ထည့်ပြီးပါပြီ")
        elif amount_input.startswith('-'):
            amount = int(amount_input[1:])
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
            except: pass
            await update.message.reply_text(f"✅ {user_data['name']} ထံမှ {amount:,} ကျပ် ထုတ်ပြီးပါပြီ")
        else:
            await update.message.reply_text("❌ ငွေပမာဏ ရှေ့တွင် + သို့မဟုတ် - ထည့်ပါ။\nဥပမာ: `+5000` သို့မဟုတ် `-2000`", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("❌ ငွေပမာဏ ဂဏန်းဖြစ်ရပါမည်။")

async def bal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bal — မိမိ dice balance စစ်ကြည့်ရန်"""
    user = update.effective_user
    chat = update.effective_chat
    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    user_data = get_user(user.id)
    if not user_data:
        await update.message.reply_text("❌ /start လုပ်ပြီးမှ စစ်ပါ။")
        return
    game = get_current_game()
    bet_text = ""
    if game:
        user_bets = get_user_bets(user.id, game['game_id'])
        if user_bets:
            bet_text = "\n\n🎯 *ယခုလောင်းထားသောငွေ:*\n"
            for b in user_bets:
                bet_text += f"  နံပါတ် {b[3]} — {b[4]:,} ကျပ်\n"
    msg = await update.message.reply_text(
        f"💰 *{user_data['name']} ရဲ့ Balance*\n\n"
        f"💳 လက်ကျန်: `{user_data['balance']:,} ကျပ်`\n"
        f"📊 စုစုပေါင်းလောင်း: {user_data['total_bet']:,} ကျပ်\n"
        f"🏆 စုစုပေါင်းနိုင်: {user_data['total_win']:,} ကျပ်"
        f"{bet_text}",
        parse_mode='Markdown',
        do_quote=True
    )
    if chat.type in ['group', 'supergroup']:
        await asyncio.sleep(15)
        try: await msg.delete()
        except: pass

async def resetgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != OWNER_ID:
        return

    db = _load_db()
    changed = False
    for game in db["games"]:
        if game.get("status") == "open":
            game["status"] = "closed"
            game["closed_at"] = datetime.now().isoformat()
            changed = True
    if changed:
        _save_db(db)

    for key in list(context.bot_data.keys()):
        if key.startswith('awaiting_dice_') or key.startswith('current_game_id_'):
            context.bot_data[key] = False

    if changed:
        await update.message.reply_text("✅ Stuck game ပွဲများ ပိတ်ပြီးပါပြီ။ ယခု ဂိမ်းအသစ် စနိုင်ပါပြီ။")
    else:
        await update.message.reply_text("ℹ️ Stuck game မရှိပါ။")

# ==================== EXCHANGE COMMANDS ====================
async def exchanged_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "💱 *သုံးနည်း:* `/exchangeD 4350`\n"
            f"_(MMK ပမာဏထည့်ပါ, Rate: 1$ = {EXCHANGE_RATE:,} MMK)_",
            parse_mode='Markdown'
        )
        return

    try:
        mmk_amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ဂဏန်းသာ ထည့်ပါ။ ဥပမာ: `/exchangeD 4350`", parse_mode='Markdown')
        return

    if mmk_amount < EXCHANGE_RATE:
        await update.message.reply_text(f"❌ အနည်းဆုံး {EXCHANGE_RATE:,} MMK (1$) ထည့်ပါ။")
        return

    dollars_needed = round(mmk_amount / EXCHANGE_RATE, 4)

    coins = get_waifu_coins(user.id)
    if coins is None:
        await update.message.reply_text("❌ Waifu Bot တွင် သင့် account မတွေ့ပါ။\nWaifu Bot ကို အရင်သုံးဖူးရပါမည်။")
        return

    if coins < dollars_needed:
        await update.message.reply_text(
            f"❌ Waifu Bot $ လက်ကျန် မလုံလောက်ပါ\n\n"
            f"💰 သင့်လက်ကျန်: `{coins:.4f}$`\n"
            f"💸 လိုအပ်သည်: `{dollars_needed:.4f}$`",
            parse_mode='Markdown'
        )
        return

    success = subtract_waifu_coins(user.id, dollars_needed)
    if not success:
        await update.message.reply_text("❌ Waifu Bot $ နှုတ်ယူ မအောင်မြင်ပါ။ နောက်မှ ထပ်စမ်းပါ။")
        return

    mention = f"@{user.username}" if user.username else user.full_name
    create_or_update_user(user.id, user.full_name, mention)
    new_dice_balance = update_balance(user.id, mmk_amount, 'add')

    await update.message.reply_text(
        f"✅ *Exchange အောင်မြင်ပါသည်*\n\n"
        f"👤 {user.full_name}\n"
        f"━━━━━━━━━━━━\n"
        f"💸 Waifu Bot: `-{dollars_needed:.4f}$`\n"
        f"💵 Dice Bot: `+{mmk_amount:,} MMK`\n"
        f"━━━━━━━━━━━━\n"
        f"💰 Dice Balance: `{new_dice_balance:,} MMK`\n"
        f"📊 Rate: 1$ = {EXCHANGE_RATE:,} MMK",
        parse_mode='Markdown'
    )

async def exchangew_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "💱 *သုံးနည်း:* `/exchangeW 4350`\n"
            f"_(MMK ပမာဏထည့်ပါ, Rate: {EXCHANGE_RATE:,} MMK = 1$)_",
            parse_mode='Markdown'
        )
        return

    try:
        mmk_amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ဂဏန်းသာ ထည့်ပါ။ ဥပမာ: `/exchangeW 4350`", parse_mode='Markdown')
        return

    if mmk_amount < EXCHANGE_RATE:
        await update.message.reply_text(f"❌ အနည်းဆုံး {EXCHANGE_RATE:,} MMK (1$) ထည့်ပါ။")
        return

    user_data = get_user(user.id)
    if not user_data or user_data['balance'] < mmk_amount:
        current_bal = user_data['balance'] if user_data else 0
        await update.message.reply_text(
            f"❌ Dice Bot လက်ကျန် မလုံလောက်ပါ\n\n"
            f"💰 သင့်လက်ကျန်: `{current_bal:,} MMK`\n"
            f"💸 လိုအပ်သည်: `{mmk_amount:,} MMK`",
            parse_mode='Markdown'
        )
        return

    dollars_earned = round(mmk_amount / EXCHANGE_RATE, 4)

    coins = get_waifu_coins(user.id)
    if coins is None:
        await update.message.reply_text("❌ Waifu Bot တွင် သင့် account မတွေ့ပါ။\nWaifu Bot ကို အရင်သုံးဖူးရပါမည်။")
        return

    new_dice_balance = update_balance(user.id, mmk_amount, 'subtract')

    success = add_waifu_coins(user.id, dollars_earned)
    if not success:
        update_balance(user.id, mmk_amount, 'add')
        await update.message.reply_text("❌ Waifu Bot $ ထည့် မအောင်မြင်ပါ။ Balance ပြန်ထည့်ပြီးပါပြီ။")
        return

    await update.message.reply_text(
        f"✅ *Exchange အောင်မြင်ပါသည်*\n\n"
        f"👤 {user.full_name}\n"
        f"━━━━━━━━━━━━\n"
        f"💵 Dice Bot: `-{mmk_amount:,} MMK`\n"
        f"💸 Waifu Bot: `+{dollars_earned:.4f}$`\n"
        f"━━━━━━━━━━━━\n"
        f"💰 Dice Balance: `{new_dice_balance:,} MMK`\n"
        f"📊 Rate: {EXCHANGE_RATE:,} MMK = 1$",
        parse_mode='Markdown'
    )

# ==================== HEALTH CHECK SERVER ====================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"🌐 Health check server running on port {port}")
    server.serve_forever()

# ==================== MAIN ====================
async def post_init(application: Application):
    cleanup_stuck_games()
    asyncio.create_task(auto_game_loop(application))

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("addadmin", addadmin_command))
    app.add_handler(CommandHandler("removeadmin", removeadmin_command))
    app.add_handler(CommandHandler("listadmins", listadmins_command))
    app.add_handler(CommandHandler("mmk", mmk_command))
    app.add_handler(CommandHandler("bal", bal_command))
    app.add_handler(CommandHandler("resetgame", resetgame_command))
    app.add_handler(CommandHandler("exchangeD", exchanged_command))
    app.add_handler(CommandHandler("exchangeW", exchangew_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Dice.ALL, handle_dice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_message))

    async def error_handler(update, context):
        import traceback
        err = context.error
        tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
        print(f"❌ ERROR:\n{tb}")
        from telegram.error import Conflict, TimedOut, NetworkError
        if isinstance(err, Conflict):
            print("⚠️ CONFLICT: Bot is already running elsewhere! Stop the other instance.")
        elif isinstance(err, (TimedOut, NetworkError)):
            print("⚠️ Network issue — will retry automatically.")

    app.add_error_handler(error_handler)

    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    print("=" * 50)
    print("🎲 DICE GAME BOT STARTED (JSON Database)")
    print(f"👑 OWNER: {OWNER_ID}")
    print(f"🎮 GROUP: {GAME_GROUP_ID}")
    print("=" * 50)

    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
