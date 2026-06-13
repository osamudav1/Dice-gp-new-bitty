# Dice-gp-new-bitty Deployment Guide

ဤ Project သည် Telegram Dice Game Bot ဖြစ်ပြီး Render Platform တွင် အလွယ်တကူတင်နိုင်ရန် ပြင်ဆင်ပေးထားပါသည်။

## 1. Project အလုပ်လုပ်ပုံ (Bot Logic)

Bot ၏ အဓိက လုပ်ဆောင်ချက်များကို အောက်ပါအတိုင်း ခွဲခြားနိုင်ပါသည်။

| လုပ်ဆောင်ချက် | အသေးစိတ် |
| :--- | :--- |
| **ဂိမ်းစတင်ခြင်း** | Admin သို့မဟုတ် Owner မှ `/start` နှိပ်ပြီး "🟢 ဂိမ်းစတင်ရန်" ကိုနှိပ်ပါက ပွဲစဉ်အသစ် စတင်ပါသည်။ |
| **လောင်းကြေးတင်ခြင်း** | User များသည် Group ထဲတွင် `နံပါတ် ပမာဏ` (ဥပမာ: `1 500`) ဟု ရိုက်ပို့၍ လောင်းနိုင်ပါသည်။ |
| **ဂိမ်းပိတ်ခြင်း** | Admin မှ "🔴 ဂိမ်းပိတ်ရန်" ကိုနှိပ်ပါက လောင်းကြေးများကို စုစည်းပြသပြီး အံစာတုံး စောင့်ဆိုင်းပါသည်။ |
| **ရလဒ်ထွက်ခြင်း** | Owner မှ အံစာတုံး (Dice) ပို့လိုက်ပါက ရလဒ်ကို တွက်ချက်ပြီး အနိုင်ရသူများကို ငွေအလိုအလျောက် ပေါင်းထည့်ပေးပါသည်။ |
| **ငွေသွင်း/ထုတ်** | Owner သည် User ၏ Message ကို Reply လုပ်ပြီး `+1000` သို့မဟုတ် `-500` ဟု ရိုက်၍ လက်ကျန်ငွေ ထိန်းချုပ်နိုင်ပါသည်။ |

## 2. Render Deployment Setup

Render တွင် Blueprint ကို အသုံးပြု၍ တစ်ခါတည်း တင်နိုင်ပါသည်။

1. **GitHub Repository** ကို Render နှင့် ချိတ်ဆက်ပါ။
2. **render.yaml** ဖိုင်ပါရှိပြီးသားဖြစ်သောကြောင့် Render မှ အလိုအလျောက် Environment များကို တောင်းပါလိမ့်မည်။
3. အောက်ပါ **Environment Variables** များကို ဖြည့်သွင်းပေးရန် လိုအပ်ပါသည် -

| Variable | Description |
| :--- | :--- |
| `BOT_TOKEN` | Telegram BotFather ထံမှ ရရှိသော Token |
| `OWNER_ID` | Bot ပိုင်ရှင်၏ Telegram User ID |
| `GAME_GROUP_ID` | ဂိမ်းကစားမည့် Group ၏ ID (ဥပမာ: `-100...`) |
| `DATABASE_URL` | Render မှ ပေးသော PostgreSQL Connection String (အလိုအလျောက် ချိတ်ဆက်ပါမည်) |
| `PORT` | `8080` (Health Check အတွက် အသုံးပြုပါသည်) |

## 3. UptimeRobot Monitoring (Keep-Alive)

Render Free Plan သည် အသုံးပြုသူမရှိပါက အိပ်ပျော် (Sleep) သွားတတ်သောကြောင့် Bot အမြဲတမ်း နိုးကြားနေစေရန် UptimeRobot တွင် အောက်ပါအတိုင်း ထည့်သွင်းပေးရပါမည်။

1. [UptimeRobot](https://uptimerobot.com/) သို့သွားပြီး Login ဝင်ပါ။
2. **Add New Monitor** ကိုနှိပ်ပါ။
3. **Monitor Type:** `HTTP(s)` ကိုရွေးပါ။
4. **Friendly Name:** `Dice Bot Health` (နှစ်သက်ရာပေးနိုင်သည်)
5. **URL (or IP):** Render မှ ပေးထားသော App URL ကို ထည့်ပါ (ဥပမာ: `https://dice-game-bot.onrender.com/`)
6. **Monitoring Interval:** `5 minutes` ဟု ထားပေးပါ။
7. **Create Monitor** ကိုနှိပ်ပါ။

ဤသို့ပြုလုပ်ခြင်းဖြင့် UptimeRobot မှ ၅ မိနစ်တစ်ခါ Bot ဆီသို့ Signal ပို့နေမည်ဖြစ်သောကြောင့် Bot သည် အမြဲတမ်း အလုပ်လုပ်နေမည် ဖြစ်ပါသည်။

---
**Manus AI** မှ စနစ်တကျ ပြင်ဆင်ပေးထားပါသည်။
