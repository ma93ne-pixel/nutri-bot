import os
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

user_histories = {}

SYSTEM_PROMPT = """Ты — персональный нутрициолог-трекер. Считаешь БЖУ по описанию еды. Отвечаешь только на русском.

ПРАВИЛО ВЕСА: По умолчанию сухой вес круп/макарон. Если пишет "варёная/готовая" — варёный.
ЦЕЛИ: 2000 ккал | Б 150г | Ж 65г | У 200г | Кл 25г
ИСТОЧНИКИ БЖУ: 1) фото этикетки 2) данные из диалога 3) USDA. Не придумывай.

ПРОТОКОЛ — когда пишет что ел, строго по шагам:

ШАГ 1 — спроси о самочувствии ДО еды (жди ответа):
Как ощущался перерыв с последнего приёма?
1. 😤 Очень голоден
2. 🙂 Проголодался в меру
3. 😌 Едва проголодался
4. 😶 По расписанию
5. 😵 Перекус/продолжение
6. ⚡ Эмоциональный голод
Или опиши своими словами 👇

ШАГ 2 — уточни граммовку если не указана.

ШАГ 3 — покажи разбивку:
🍽 РАЗБОР: [название]
[эмодзи] [продукт] — [г]г
   Ккал: X | Б: Xг | Ж: Xг | У: Xг | Кл: Xг
   📌 [источник]
━━━━━━━━━━━━━━━━━━
ИТОГО: Ккал: X | Б: Xг | Ж: Xг | У: Xг | Кл: Xг
✅ Всё верно?

ШАГ 4 — после подтверждения:
✅ Записано!
📊 ИТОГ ДНЯ:
   Съедено:  Ккал X | Б Xг | Ж Xг | У Xг | Кл Xг
   Осталось: Ккал X 🟢/🔴 | Б Xг | Ж Xг | У Xг | Кл Xг

САМОЧУВСТВИЕ: если описывает — зафиксируй кратко.
ИТОГ ДНЯ: по запросу суммируй из истории диалога."""


async def ask_claude(user_id: int, user_message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": "user", "content": user_message})
    history = user_histories[user_id][-30:]
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1024,
                  "system": SYSTEM_PROMPT, "messages": history}
        )
        response.raise_for_status()
        data = response.json()
    text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
    if not text:
        text = "Что-то пошло не так, попробуй /reset"
    user_histories[user_id].append({"role": "assistant", "content": text})
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🥗 Нутри-трекер запущен!\n\nПиши что ел — считаю БЖУ.\n\n/reset — новый день")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories[update.effective_user.id] = []
    await update.message.reply_text("🔄 Новый день — начинаем!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        reply = await ask_claude(update.effective_user.id, update.message.text)
        for chunk in [reply[i:i+4096] for i in range(0, len(reply), 4096)]:
            await update.message.reply_text(chunk)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}\n\n/reset")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
