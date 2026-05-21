"""
Простий Telegram-бот для нагадувань.
Юзер пише: "Міт з Артуром нагадай через 45х і за 1хв"
Бот ставить два нагадування і шле повідомлення коли настане час.

Запуск:
1. pip install aiogram apscheduler dateparser
2. export BOT_TOKEN="твій_токен_від_@BotFather"
3. python bot.py
"""

import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TZ = ZoneInfo("Europe/Kyiv")  # зміни на свій таймзон якщо треба
DB_PATH = "reminders.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# APScheduler з SQLite — нагадування переживуть рестарт
scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH}")},
    timezone=TZ,
)


# ──────────────────────── Парсер часу ────────────────────────

# Підтримує формати:
#   "через 45х", "через 45хв", "через 45 хвилин"
#   "через 2г", "через 2 години"
#   "через 30с", "через 30 секунд"
#   "за 1хв до", "за 10хв" (інтерпретується як "через 10хв"
#                            якщо немає базової події в майбутньому)
#
# Логіка для повідомлень типу "нагадай через 45х і за 1хв до":
#   - перше число → нагадування через X від зараз
#   - "за Y до" → нагадування за Y до МОМЕНТУ першого нагадування

UNIT_SECONDS = {
    "с": 1, "сек": 1, "секунд": 1, "секунди": 1,
    "хв": 60, "х": 60, "хвилин": 60, "хвилини": 60, "м": 60,
    "г": 3600, "год": 3600, "годин": 3600, "години": 3600, "ч": 3600,
    "д": 86400, "дн": 86400, "день": 86400, "дні": 86400, "днів": 86400,
}

# Регулярка ловить: число + опціональний пробіл + одиниця
TIME_PATTERN = re.compile(
    r"(\d+)\s*(секунд|секунди|сек|хвилин|хвилини|годин|години|днів|дні|день|год|хв|сек|дн|с|х|м|г|ч|д)\b",
    re.IGNORECASE,
)


def parse_reminders(text: str) -> tuple[list[timedelta], str]:
    """
    Повертає (список_відступів_від_зараз, текст_нагадування).

    "Міт з Артуром нагадай через 45х і за 1хв до"
      → ([45min, 44min], "Міт з Артуром")

    "купити хліб через 30хв"
      → ([30min], "купити хліб")
    """
    matches = list(TIME_PATTERN.finditer(text))
    if not matches:
        return [], text.strip()

    # Перший таймстемп — основа ("через X")
    base_seconds = int(matches[0].group(1)) * UNIT_SECONDS[matches[0].group(2).lower()]
    deltas = [timedelta(seconds=base_seconds)]

    # Решта — дивимось контекст: "за Y до" означає (base - Y)
    for m in matches[1:]:
        seconds = int(m.group(1)) * UNIT_SECONDS[m.group(2).lower()]
        # шукаємо "до" в межах 15 символів після числа
        tail = text[m.end():m.end() + 15].lower()
        if "до" in tail:
            offset = base_seconds - seconds
            if offset > 0:
                deltas.append(timedelta(seconds=offset))
        else:
            deltas.append(timedelta(seconds=seconds))

    # Витягуємо чистий текст нагадування — все крім часових фраз і службових слів
    clean = text
    for m in matches:
        # розширюємо діапазон щоб захопити "через", "за", "і", "до"
        clean = clean.replace(m.group(0), "")
    clean = re.sub(r"\b(нагадай|нагадати|через|за|до|і|та|потім)\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean).strip(" ,.-")

    return sorted(deltas), clean or "нагадування"


# ──────────────────────── Хендлери ────────────────────────


async def send_reminder(chat_id: int, text: str):
    await bot.send_message(chat_id, f"🔔 {text}")


@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "Привіт! Пиши що тобі нагадати і коли.\n\n"
        "Приклади:\n"
        "• <code>купити хліб через 30хв</code>\n"
        "• <code>Міт з Артуром нагадай через 45х і за 1хв до</code>\n"
        "• <code>яйця через 7хв</code>\n\n"
        "Команди: /list — мої нагадування, /clear — видалити всі",
        parse_mode="HTML",
    )


@dp.message(Command("list"))
async def cmd_list(msg: types.Message):
    jobs = [j for j in scheduler.get_jobs() if j.kwargs.get("chat_id") == msg.chat.id]
    if not jobs:
        await msg.answer("Нагадувань немає 🌱")
        return
    lines = []
    for j in sorted(jobs, key=lambda x: x.next_run_time):
        when = j.next_run_time.astimezone(TZ).strftime("%d.%m %H:%M")
        lines.append(f"• {when} — {j.kwargs['text']}")
    await msg.answer("Найближчі нагадування:\n" + "\n".join(lines))


@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    count = 0
    for j in scheduler.get_jobs():
        if j.kwargs.get("chat_id") == msg.chat.id:
            j.remove()
            count += 1
    await msg.answer(f"Видалено: {count}")


@dp.message()
async def handle_message(msg: types.Message):
    if not msg.text:
        return

    deltas, text = parse_reminders(msg.text)

    if not deltas:
        await msg.answer(
            "Не зрозумів коли нагадати 🤔\n"
            "Спробуй: <code>щось через 30хв</code> або <code>через 2г</code>",
            parse_mode="HTML",
        )
        return

    now = datetime.now(TZ)
    scheduled = []
    for delta in deltas:
        run_at = now + delta
        scheduler.add_job(
            send_reminder,
            "date",
            run_date=run_at,
            kwargs={"chat_id": msg.chat.id, "text": text},
        )
        scheduled.append(run_at)

    # Підтвердження юзеру
    times_str = ", ".join(t.strftime("%H:%M") for t in scheduled)
    await msg.answer(f"✅ Нагадаю: <b>{text}</b>\n🕐 {times_str}", parse_mode="HTML")


# ──────────────────────── Запуск ────────────────────────


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Встанови змінну середовища BOT_TOKEN")
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
