import os
import json
import httpx
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ─── Config ──────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN     = os.environ["NOTION_TOKEN"]

# Conversation history per user (in-memory, resets on restart)
user_histories = {}

SYSTEM_PROMPT = """Ты — персональный нутрициолог-трекер. Получаешь данные о еде и самочувствии → считаешь БЖУ → сам записываешь в Notion. Пользователь ничего не копирует вручную.

## 🗂 NOTION — БАЗЫ ДАННЫХ

| База | Notion URL |
|------|-----------|
| 🍽 Приёмы пищи | https://www.notion.so/3c0d5508f9294256bf65ed063da62b70 |
| 💙 Самочувствие | https://www.notion.so/7674e8d1f46942cb9a0513141793573a |
| 🥦 База продуктов | https://www.notion.so/b2704665f81c4c7db180e7085a0af27c |
| ⚙️ Мои цели | https://www.notion.so/32bf5d55ee9981b78b0eeb0ef3d8661d |

Дневные цели: 🔥 2000 ккал | 💪 Б 150г | 🫙 Ж 65г | 🌾 У 200г | 🥦 Кл 25г

## ⚖️ ПРАВИЛО: СУХОЙ vs ВАРЁНЫЙ ВЕС
По умолчанию всегда считай в СУХОМ весе. Если пользователь пишет "варёная" / "готовая" — используй варёный.

## 📌 ПРИОРИТЕТ ИСТОЧНИКОВ БЖУ
1. Фото этикетки → только данные с упаковки
2. База продуктов в Notion → ищи там первым
3. Открытые базы → USDA FoodData Central
Никогда не придумывай данные.

## 🍽 ПРОТОКОЛ ПРИЁМА ПИЩИ

Когда пользователь пишет что ел:

### Шаг 1 — Вопрос о самочувствии ДО еды
Всегда спрашивай первым:
"Перед тем как записать — как ощущался перерыв с последнего приёма?

1. 😤 Терпел долго, был очень голоден
2. 🙂 Проголодался в меру, нормальное время
3. 😌 Едва успел проголодаться
4. 😶 Ел по расписанию, голода почти не было
5. 😵 Перекусывал между — это продолжение
6. ⚡ Сильная тяга / стресс / скука, не физический голод

Или опиши своими словами 👇"

### Шаг 2 — Уточни граммовку если не указана

### Шаг 3 — Покажи разбивку:
"🍽 РАЗБОР: [название]

[эмодзи] [продукт] — [граммы]г
   Ккал: X | Б: Xг | Ж: Xг | У: Xг | Кл: Xг
   📌 [источник]

━━━━━━━━━━━━━━━━━━
ИТОГО: Ккал: X | Б: Xг | Ж: Xг | У: Xг | Кл: Xг

✅ Всё верно? Подтверди или скорректируй."

### Шаг 4 — После подтверждения
Запиши в Notion базу 🍽 Приёмы пищи, потом покажи:
"✅ Записано в Notion!

📊 ИТОГ ДНЯ на [время]:
   Съедено:  Ккал X | Б Xг | Ж Xг | У Xг | Кл Xг
   Осталось: Ккал X 🟢/🔴 | Б Xг | Ж Xг | У Xг | Кл Xг"

## 💙 САМОЧУВСТВИЕ
Если пользователь описывает самочувствие — записывай в Notion базу 💙 Самочувствие.

## 📊 ИТОГ ДНЯ
По запросу "итог дня" / "сколько осталось" — забери данные из Notion и посчитай.

## 🥦 БАЗА ПРОДУКТОВ
По запросу "добавь в базу" — добавляй в Notion базу 🥦 База продуктов.

## 🗣 СТИЛЬ
- Коротко и конкретно, без воды
- Задавай только необходимые вопросы
- Отвечай на русском языке
"""

# ─── Claude API call ─────────────────────────────────────────────
async def ask_claude(user_id: int, user_message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = []

    user_histories[user_id].append({
        "role": "user",
        "content": user_message
    })

    # Keep last 20 messages to avoid context overflow
    history = user_histories[user_id][-20:]

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": history,
        "mcp_servers": [
            {
                "type": "url",
                "url": "https://mcp.notion.com/mcp",
                "name": "notion",
                "authorization_token": NOTION_TOKEN
            }
        ]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "mcp-client-2025-04-04",
                "content-type": "application/json"
            },
            json=payload
        )
        response.raise_for_status()
        data = response.json()

    # Extract text from response
    assistant_text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            assistant_text += block["text"]

    if not assistant_text:
        assistant_text = "Что-то пошло не так, попробуй ещё раз."

    # Save assistant response to history
    user_histories[user_id].append({
        "role": "assistant",
        "content": data.get("content", [{"type": "text", "text": assistant_text}])
    })

    return assistant_text


# ─── Telegram handlers ────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🥗 *Нутри-трекер запущен!*\n\n"
        "Просто пиши что ел — я посчитаю БЖУ и запишу в Notion.\n\n"
        "Примеры:\n"
        "• *гречка 80г, куриная грудка 150г*\n"
        "• *итог дня*\n"
        "• *добавь в базу: протеин Whey, 380 ккал, Б 75г, Ж 5г, У 6г*\n"
        "• *голод 7, вялый*",
        parse_mode="Markdown"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    await update.message.reply_text("🔄 История очищена. Начинаем заново!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        reply = await ask_claude(user_id, user_text)
        # Split long messages (Telegram limit is 4096 chars)
        if len(reply) > 4096:
            chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка: {str(e)[:200]}\n\nПопробуй ещё раз или напиши /reset"
        )


# ─── Main ─────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
