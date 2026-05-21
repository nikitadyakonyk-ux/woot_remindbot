import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TZ = ZoneInfo("Europe/Kyiv")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url="sqlite:///reminders.db")},
    timezone=TZ,
)

UNIT_SECONDS = {
    "с": 1, "сек": 1, "секунд": 1, "секунди": 1,
    "хв": 60, "х": 60, "хвилин": 60, "хвилини": 60, "м": 60,
    "г": 3600, "год": 3600, "годин": 3600, "години": 3600, "ч": 3600,
    "д": 86400, "дн": 86400, "день": 86400, "дні": 86400, "днів": 86400,
}
TIME_PATTERN = re.compile(
    r"(\d+)\s*(секунд|секунди|сек|хвилин|хвилини|годин|години|днів|дні|день|год|хв|сек|дн|с|х|м|г|ч|д)\b",
    re.IGNORECASE,
)
CLOCK_PATTERN = re.compile(r"\bо\s+(\d{1,2})[:\.](\d{2})\b", re.IGNORECASE)
WEEKDAYS_UA = {
    "понеділка": "mon", "понеділок": "mon",
    "вівторка": "tue", "вівторок": "tue",
    "середи": "wed", "середа": "wed",
    "четверга": "thu", "четвер": "thu",
    "п'ятниці": "fri", "п'ятницю": "fri", "п'ятниця": "fri",
    "суботи": "sat", "суботу": "sat", "субота": "sat",
    "неділі": "sun", "неділю": "sun", "неділя": "sun",
}
WEEKDAYS_UA_DISPLAY = {
    "mon": "понеділок", "tue": "вівторок", "wed": "середу",
    "thu": "четвер", "fri": "п'ятницю", "sat": "суботу", "sun": "неділю",
}
WEEKLY_PATTERN = re.compile(
    r"\bкожного\s+(понеділка|вівторка|середи|четверга|п['\u2019]ятниці|суботи|неділі)\b",
    re.IGNORECASE,
)
DAILY_PATTERN = re.compile(r"\bщодня\b", re.IGNORECASE)
MENTION_PATTERN = re.compile(r"@[\w\d_]+")


def extract_mentions(text):
    return MENTION_PATTERN.findall(text)


def clean_text(text, *patterns_to_remove):
    result = text
    for p in patterns_to_remove:
        result = re.sub(p, "", result, flags=re.IGNORECASE)
    result = re.sub(r"\b(нагадай|нагадати|через|за|до|і|та|потім|сьогодні|о|зранку|ввечері|вранці)\b", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+", " ", result).strip(" ,.-")
    return result or "нагадування"


def build_label(text, *patterns_to_remove):
    mentions = extract_mentions(text)
    label = clean_text(text, *patterns_to_remove)
    if mentions:
        mentions_str = " ".join(mentions)
        label = re.sub(r"@[\w\d_]+", "", label).strip(" ,.-") or "нагадування"
        return f"{mentions_str} {label}"
    return label


def parse_daily(text):
    clock_match = CLOCK_PATTERN.search(text)
    if not clock_match:
        return None, None, None
    hour = int(clock_match.group(1))
    minute = int(clock_match.group(2))
    label = build_label(text, DAILY_PATTERN, CLOCK_PATTERN, TIME_PATTERN)
    return hour, minute, label


def parse_weekly(text):
    week_match = WEEKLY_PATTERN.search(text)
    clock_match = CLOCK_PATTERN.search(text)
    if not week_match or not clock_match:
        return None, None, None, None
    day_ua = week_match.group(1).lower().replace("\u2019", "'")
    day_cron = WEEKDAYS_UA.get(day_ua)
    hour = int(clock_match.group(1))
    minute = int(clock_match.group(2))
    label = build_label(text, WEEKLY_PATTERN, CLOCK_PATTERN, TIME_PATTERN)
    return day_cron, hour, minute, label


def parse_clock(text):
    clock_match = CLOCK_PATTERN.search(text)
    if not clock_match:
        return None, None
    hour = int(clock_match.group(1))
    minute = int(clock_match.group(2))
    now = datetime.now(TZ)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    label = build_label(text, CLOCK_PATTERN, TIME_PATTERN)
    return run_at, label


def parse_reminders(text):
    matches = list(TIME_PATTERN.finditer(text))
    if not matches:
        return [], text.strip()
    base = int(matches[0].group(1)) * UNIT_SECONDS[matches[0].group(2).lower()]
    deltas = [timedelta(seconds=base)]
    for m in matches[1:]:
        s = int(m.group(1)) * UNIT_SECONDS[m.group(2).lower()]
        tail = text[m.end():m.end()+15].lower()
        if "до" in tail:
            off = base - s
            if off > 0:
                deltas.append(timedelta(seconds=off))
        else:
            deltas.append(timedelta(seconds=s))
    label = build_label(text, *[re.escape(m.group(0)) for m in matches])
    return sorted(deltas), label


async def send_reminder(chat_id, text):
    await bot.send_message(chat_id, f"🔔 {text}")


@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "Хелоууу! в цей бот пиши що і коли тобі нагадати треба і коли\n\n"
        "Приклади:\n"
        "• Опублікувати Карінусіку сторіс сьогодні о 20:00\n"
        "• Кожного четверга о 11:00 здати контент план по Карінусіку\n"
        "• зателефонувати мамі о 18:30\n"
        "• яйця через 7 хв\n"
        "• щодня о 10:00 @karinusik опублікувати сторіс\n\n"
        "/list — мої нагадування\n"
        "/clear — видалити всі"
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
        recurring = "🔁 " if j.trigger.__class__.__name__ == "CronTrigger" else ""
        lines.append(f"• {recurring}{when} — {j.kwargs['text']}")
    await msg.answer("Найближчі:\n" + "\n".join(lines))


@dp.message(Command("clear"))
async def cmd_clear(msg: types.Message):
    n = 0
    for j in scheduler.get_jobs():
        if j.kwargs.get("chat_id") == msg.chat.id:
            j.remove()
            n += 1
    await msg.answer(f"Видалено: {n}")


@dp.message()
async def handle_message(msg: types.Message):
    if not msg.text:
        return
    text = msg.text

    if DAILY_PATTERN.search(text):
        hour, minute, label = parse_daily(text)
        if hour is not None:
            scheduler.add_job(
                send_reminder, "cron",
                hour=hour, minute=minute,
                kwargs={"chat_id": msg.chat.id, "text": label},
            )
            await msg.answer(
                f"🔁 Щодня нагадуватиму: {label}\n"
                f"🕐 Кожного дня о {hour:02d}:{minute:02d}"
            )
            return
        await msg.answer("Вкажи час: щодня о 10:00 текст")
        return

    if WEEKLY_PATTERN.search(text):
        day_cron, hour, minute, label = parse_weekly(text)
        if day_cron:
            scheduler.add_job(
                send_reminder, "cron",
                day_of_week=day_cron, hour=hour, minute=minute,
                kwargs={"chat_id": msg.chat.id, "text": label},
            )
            day_display = WEEKDAYS_UA_DISPLAY.get(day_cron, day_cron)
            await msg.answer(
                f"🔁 Щотижня нагадуватиму: {label}\n"
                f"📅 Кожного {day_display} о {hour:02d}:{minute:02d}"
            )
            return

    if CLOCK_PATTERN.search(text) and not TIME_PATTERN.search(text):
        run_at, label = parse_clock(text)
        if run_at:
            scheduler.add_job(
                send_reminder, "date", run_date=run_at,
                kwargs={"chat_id": msg.chat.id, "text": label},
            )
            await msg.answer(
                f"✅ Нагадаю: {label}\n"
                f"🕐 {run_at.strftime('%d.%m о %H:%M')}"
            )
            return

    deltas, label = parse_reminders(text)
    if not deltas:
        await msg.answer(
            "Не зрозумів коли нагадати 🤔\n\n"
            "Спробуй:\n"
            "• щось через 30хв\n"
            "• щось о 18:30\n"
            "• кожного четверга о 11:00 щось"
        )
        return
    now = datetime.now(TZ)
    times = []
    for d in deltas:
        run_at = now + d
        scheduler.add_job(
            send_reminder, "date", run_date=run_at,
            kwargs={"chat_id": msg.chat.id, "text": label},
        )
        times.append(run_at.strftime("%H:%M"))
    await msg.answer(f"✅ Нагадаю: {label}\n🕐 {', '.join(times)}")


async def main():
    scheduler.start()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
