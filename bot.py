import asyncio
import logging
import os
import re
from pathlib import Path
from logging.handlers import RotatingFileHandler

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from storage import Database
from antispam import AntiSpamMiddleware

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DATABASE_PATH", "data/bot.sqlite3")
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(), RotatingFileHandler("logs/bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
)
log = logging.getLogger("shmonza2bot")
dp = Dispatcher()
db = Database(DB_PATH)

def admin(uid): return uid in ADMIN_IDS

def menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔎 Поиск Wikipedia", callback_data="search")
    kb.button(text="📄 Sherlock-отчёты", callback_data="reports")
    kb.button(text="👤 Профиль", callback_data="profile")
    kb.button(text="ℹ️ Помощь", callback_data="help")
    kb.adjust(2, 2)
    return kb.as_markup()

def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статистика", callback_data="admin_stats")
    kb.button(text="📄 Мои отчёты", callback_data="reports")
    kb.adjust(2)
    return kb.as_markup()

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

@dp.message(CommandStart())
async def start(m: Message):
    u = m.from_user
    if not u: return
    await db.upsert_user(u.id, u.username, u.full_name)
    await m.answer(
        "Привет! Это обновлённая базовая версия @shmonza2bot.\n\n"
        "🔎 Поиск — по открытым статьям Wikipedia.\n"
        "📄 Sherlock-отчёты — хранение уже созданных текстовых отчётов Sherlock.\n\n"
        "Бот не запускает Sherlock сам по себе и не получает доступ к закрытым аккаунтам.",
        reply_markup=menu()
    )

@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if not m.from_user or not admin(m.from_user.id):
        await m.answer("⛔ Доступ только для администратора.")
        return
    await m.answer("⚙️ Админ-панель", reply_markup=admin_menu())

@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "/start — меню\n/search запрос — поиск по Wikipedia\n/reports — сохранённые Sherlock-отчёты\n"
        "/admin — панель администратора\n/cancel — отменить действие\n\n"
        "Чтобы сохранить отчёт: отправь боту текстовый файл .txt с подписью /sherlock имя_пользователя. "
        "Импорт доступен только администраторам."
    )

@dp.message(Command("cancel"))
async def cancel(m: Message):
    if m.from_user: await db.clear_state(m.from_user.id)
    await m.answer("Отменено.", reply_markup=menu())

@dp.message(Command("search"))
async def search_cmd(m: Message):
    q = (m.text or "").partition(" ")[2].strip()
    if not q:
        await m.answer("Напиши запрос: /search запрос")
        return
    await search_wikipedia(m, q)

async def search_wikipedia(m: Message, q: str):
    if not q or len(q) > 180:
        await m.answer("Запрос должен быть от 1 до 180 символов.")
        return
    results = []
    lang = "ru"
    for endpoint, code in [("https://ru.wikipedia.org/w/api.php", "ru"), ("https://en.wikipedia.org/w/api.php", "en")]:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                async with s.get(endpoint, params={"action":"query","list":"search","srsearch":q,"format":"json","utf8":1,"srlimit":5}) as r:
                    r.raise_for_status()
                    data = await r.json()
            results = data.get("query", {}).get("search", [])
            if results:
                lang = code
                break
        except Exception as e:
            log.warning("wikipedia_search_error type=%s", type(e).__name__)
    if not results:
        await m.answer("Ничего не найдено или сервис временно недоступен.")
        return
    lines = [f"🔎 <b>Результаты:</b> {esc(q)}\n"]
    for item in results:
        title = item.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
        link = f"https://{lang}.wikipedia.org/wiki/" + title.replace(" ", "_")
        lines.append(f'• <a href="{link}">{esc(title)}</a>\n{esc(snippet)[:220]}\n')
    await m.answer("\n".join(lines), disable_web_page_preview=True)

@dp.message(Command("sherlock"))
async def sherlock_usage(m: Message):
    await m.answer("Для импорта отчёта прикрепи .txt файл и укажи в подписи: /sherlock username. Только администратор может импортировать отчёты.")

@dp.message(F.document)
async def import_report(m: Message):
    if not m.from_user or not admin(m.from_user.id):
        await m.answer("⛔ Импорт отчётов доступен только администратору.")
        return
    doc = m.document
    caption = (m.caption or "").strip()
    match = re.match(r"^/sherlock(?:@\w+)?\s+([A-Za-z0-9_.-]{1,80})$", caption)
    if not match:
        await m.answer("Добавь к файлу подпись в формате: /sherlock username")
        return
    if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
        await m.answer("Поддерживаются только текстовые файлы .txt.")
        return
    if doc.file_size and doc.file_size > 1_000_000:
        await m.answer("Файл слишком большой. Максимум 1 МБ.")
        return
    try:
        tgfile = await m.bot.get_file(doc.file_id)
        from io import BytesIO
        buffer = BytesIO()
        await m.bot.download_file(tgfile.file_path, destination=buffer)
        raw = buffer.getvalue().decode("utf-8", errors="replace")
    except Exception as e:
        log.exception("report_download_failed")
        await m.answer("Не удалось прочитать файл. Попробуй сохранить его как UTF-8 .txt.")
        return
    report_id = await db.save_report(match.group(1), doc.file_name, raw, m.from_user.id)
    log.info("sherlock_report_imported report_id=%s by=%s", report_id, m.from_user.id)
    await m.answer(f"✅ Отчёт сохранён в базе.\nID отчёта: <code>{report_id}</code>\nUsername: <code>{esc(match.group(1))}</code>\nСтрок: {len(raw.splitlines())}")

@dp.message(Command("reports"))
async def reports(m: Message):
    if not m.from_user or not admin(m.from_user.id):
        await m.answer("⛔ Список отчётов доступен только администратору.")
        return
    rows = await db.latest_reports(10)
    if not rows:
        await m.answer("В базе пока нет Sherlock-отчётов.")
        return
    text = ["📄 <b>Последние отчёты</b>"]
    for rid, username, filename, created_at, line_count in rows:
        text.append(f"ID <code>{rid}</code> · <code>{esc(username)}</code> · {esc(filename)} · строк: {line_count}")
    text.append("\nЧтобы выгрузить отчёт: /report ID")
    await m.answer("\n".join(text))

@dp.message(Command("report"))
async def get_report(m: Message):
    if not m.from_user or not admin(m.from_user.id):
        await m.answer("⛔ Доступ только для администратора.")
        return
    arg = (m.text or "").partition(" ")[2].strip()
    if not arg.isdigit():
        await m.answer("Формат: /report ID")
        return
    row = await db.get_report(int(arg))
    if not row:
        await m.answer("Отчёт не найден.")
        return
    from aiogram.types import BufferedInputFile
    rid, username, filename, raw, created_at = row
    output = BufferedInputFile(raw.encode("utf-8"), filename=f"sherlock_{username}_{rid}.txt")
    await m.answer_document(output, caption=f"Отчёт #{rid} · {esc(username)}")

@dp.callback_query(F.data == "search")
async def cb_search(c):
    await c.message.answer("Введи запрос командой /search запрос")
    await c.answer()

@dp.callback_query(F.data == "reports")
async def cb_reports(c):
    if not admin(c.from_user.id):
        await c.answer("Нет доступа.", show_alert=True)
        return
    rows = await db.latest_reports(10)
    if not rows:
        await c.message.answer("В базе пока нет Sherlock-отчётов.")
    else:
        text = ["📄 <b>Последние отчёты</b>"]
        for rid, username, filename, created_at, line_count in rows:
            text.append(f"ID <code>{rid}</code> · <code>{esc(username)}</code> · {esc(filename)} · строк: {line_count}")
        text.append("\nЧтобы выгрузить отчёт: /report ID")
        await c.message.answer("\n".join(text))
    await c.answer()

@dp.callback_query(F.data == "profile")
async def cb_profile(c):
    row = await db.get_user(c.from_user.id)
    if row:
        await c.message.answer(f"👤 ID: <code>{c.from_user.id}</code>\nЗапусков: {row[5]}")
    else:
        await c.message.answer("Профиль не найден. Нажми /start.")
    await c.answer()

@dp.callback_query(F.data == "help")
async def cb_help(c):
    await help_cmd(c.message)
    await c.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_stats(c):
    if not admin(c.from_user.id):
        await c.answer("Нет доступа.", show_alert=True)
        return
    await c.message.answer(f"Пользователей: {await db.user_count()}\nОтчётов: {await db.report_count()}")
    await c.answer()

async def main():
    if not TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в переменных окружения Railway.")
    await db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp.message.middleware(AntiSpamMiddleware())
    me = await bot.get_me()
    log.info("starting username=%s id=%s", me.username, me.id)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await db.close()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
