import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat
from aiogram.filters import Command
from aiogram.enums import ParseMode

from config import ADMIN_BOT_TOKEN, OWNER_ID, ADMIN_CHECK_INTERVAL

logging.basicConfig(level=logging.INFO)

bot = Bot(token=ADMIN_BOT_TOKEN)
dp = Dispatcher()

def get_stats():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT * FROM stats WHERE date=?", (today,))
    stats = c.fetchone()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE step >= 4")
    completed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE step = 1")
    step1 = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE step = 2")
    step2 = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE step = 3")
    step3 = c.fetchone()[0]
    conn.close()
    if not stats:
        stats = (today, 0, 0, 0, 0, 0)
    return stats, total, completed, step1, step2, step3

def get_notifications(limit=10):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT id, text, created_at FROM admin_notifications WHERE is_sent=0 ORDER BY id DESC LIMIT ?", (limit,))
    notifs = c.fetchall()
    conn.close()
    return notifs

def mark_notifications_sent(ids):
    if not ids:
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    placeholders = ','.join('?' * len(ids))
    c.execute(f"UPDATE admin_notifications SET is_sent=1 WHERE id IN ({placeholders})", ids)
    conn.commit()
    conn.close()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Нет доступа")
        return

    await message.answer(
        "🤖 <b>Админ-панель PLGiftBot</b>\n\n"
        "<b>📊 Статистика:</b>\n"
        "/stats — статистика за день\n"
        "/live — активность по часам\n"
        "/notify — последние уведомления\n"
        "/users — список пользователей\n"
        "/clear — очистить уведомления\n\n"
        "<b>🎁 Подарки:</b>\n"
        "/gifts — список текущих подарков\n"
        "/setgift &lt;1-6&gt; &lt;Slug&gt; &lt;Number&gt; — заменить подарок\n"
        "  пример: <code>/setgift 6 SnoopCigar 12345</code>\n"
        "/setgiftname &lt;1-6&gt; &lt;Имя&gt; — изменить отображаемое имя\n"
        "  пример: <code>/setgiftname 6 Snoop Cigar</code>\n"
        "/resetgifts — вернуть дефолтные подарки",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    stats, total, completed, step1, step2, step3 = get_stats()

    conv = round((stats[5]/max(stats[1],1))*100, 1)

    await message.answer(
        f"📊 <b>Статистика за сегодня ({stats[0]}):</b>\n\n"
        f"👤 <b>Новые:</b> {stats[1]}\n"
        f"📝 <b>На комменте (шаг 1):</b> {step1}\n"
        f"💬 <b>На комментах в ленте (шаг 2):</b> {step2}\n"
        f"🔐 <b>На подписке (шаг 3):</b> {step3}\n"
        f"✅ <b>Завершили:</b> {stats[5]}\n\n"
        f"📈 <b>Всего пользователей:</b> {total}\n"
        f"🏆 <b>Всего завершили:</b> {completed}\n\n"
        f"💰 <b>Конверсия сегодня:</b> {conv}%",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("live"))
async def cmd_live(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT strftime('%H', created_at) as hour, COUNT(*) FROM users WHERE date(created_at)=date('now') GROUP BY hour")
    hourly = c.fetchall()
    conn.close()

    text = f"🔥 <b>Регистрации по часам сегодня:</b>\n\n"
    for h in hourly:
        text += f"{h[0]}:00 — {h[1]} чел.\n"

    if not hourly:
        text += "Пока нет данных\n"

    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("notify"))
async def cmd_notify(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    notifs = get_notifications(10)
    if not notifs:
        await message.answer("📭 Нет новых уведомлений")
        return

    for n in notifs:
        await message.answer(
            f"🔔 <b>Уведомление #{n[0]}</b>\n"
            f"🕐 {n[2][:16]}\n\n{n[1]}",
            parse_mode=ParseMode.HTML
        )

    mark_notifications_sent([n[0] for n in notifs])

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, step, created_at FROM users ORDER BY created_at DESC LIMIT 20")
    users = c.fetchall()
    conn.close()

    text = "👥 <b>Последние 20 пользователей:</b>\n\n"
    for u in users:
        status = {0: "🆕", 1: "📝", 2: "💬", 3: "🔐", 4: "✅"}.get(u[3], "❓")
        name = u[2] or "-"
        user_link = f"tg://user?id={u[0]}"
        text += f"{status} <a href='{user_link}'>{name}</a> | @{u[1] or '-'} | {u[4][:10]}\n"

    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ========== УПРАВЛЕНИЕ ПОДАРКАМИ ==========
def _fetch_all_gifts():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT slot, name, slug, number, emoji FROM gifts ORDER BY slot")
    rows = c.fetchall()
    conn.close()
    return rows

@dp.message(Command("gifts"))
async def cmd_gifts(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    rows = _fetch_all_gifts()
    if not rows:
        await message.answer("Подарков ещё нет в БД. Запусти главного бота — он засеет дефолты.")
        return
    text = "🎁 <b>Текущие подарки:</b>\n\n"
    for slot, name, slug, number, emoji in rows:
        link = f"https://t.me/nft/{slug}-{number}"
        text += f"<b>{slot}.</b> {emoji or ''} {name}\n   <code>{slug}-{number}</code>\n   {link}\n\n"
    text += "Менять: <code>/setgift &lt;1-6&gt; &lt;Slug&gt; &lt;Number&gt;</code>"
    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("setgift"))
async def cmd_setgift(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 4:
        await message.answer(
            "Использование:\n<code>/setgift &lt;1-6&gt; &lt;Slug&gt; &lt;Number&gt;</code>\n"
            "Пример: <code>/setgift 6 SnoopCigar 12345</code>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        slot = int(parts[1])
        slug = parts[2].strip()
        number = int(parts[3].strip())
    except ValueError:
        await message.answer("❌ Slot и Number должны быть числами.")
        return
    if not (1 <= slot <= 6):
        await message.answer("❌ Slot должен быть от 1 до 6.")
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("UPDATE gifts SET slug=?, number=? WHERE slot=?", (slug, number, slot))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()

    if not changed:
        await message.answer(f"❌ Подарок в slot={slot} не найден.")
        return

    link = f"https://t.me/nft/{slug}-{number}"
    await message.answer(
        f"✅ Подарок #{slot} обновлён:\n<code>{slug}-{number}</code>\n{link}\n\n"
        f"Проверь, открывается ли ссылка карточкой подарка. Если нет — slug или номер не существуют на Telegram.",
        parse_mode=ParseMode.HTML, disable_web_page_preview=False
    )

@dp.message(Command("setgiftname"))
async def cmd_setgiftname(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/setgiftname &lt;1-6&gt; &lt;Имя&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return
    try:
        slot = int(parts[1])
    except ValueError:
        await message.answer("❌ Slot должен быть числом 1-6.")
        return
    if not (1 <= slot <= 6):
        await message.answer("❌ Slot должен быть от 1 до 6.")
        return
    name = parts[2].strip()
    if not name:
        await message.answer("❌ Имя не может быть пустым.")
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("UPDATE gifts SET name=? WHERE slot=?", (name, slot))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    if changed:
        await message.answer(f"✅ Имя подарка #{slot} → <b>{name}</b>", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"❌ Подарок в slot={slot} не найден.")

@dp.message(Command("resetgifts"))
async def cmd_resetgifts(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    # Удаляем все, главный бот при следующем старте засеет дефолты.
    # Чтобы сразу — импортируем дефолты и засеем здесь же.
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM gifts")
    conn.commit()
    conn.close()
    try:
        from main_bot import DEFAULT_GIFTS, init_gifts
        init_gifts(DEFAULT_GIFTS)
        await message.answer("✅ Подарки сброшены к дефолтам. Открой /gifts.")
    except Exception as e:
        await message.answer(
            f"⚠️ Таблица очищена, но засеять дефолты сразу не удалось: {e}\n"
            "Перезапусти главного бота — он заполнит её сам."
        )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return

    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM admin_notifications")
    conn.commit()
    conn.close()

    await message.answer("🗑 <b>Уведомления очищены</b>")

# Авто-уведомления
async def notification_worker():
    await asyncio.sleep(10)
    while True:
        try:
            notifs = get_notifications(5)
            if notifs:
                for n in notifs:
                    await bot.send_message(
                        OWNER_ID,
                        f"🔔 <b>Новое уведомление!</b>\n"
                        f"🕐 {n[2][:16]}\n\n{n[1]}",
                        parse_mode=ParseMode.HTML
                    )
                mark_notifications_sent([n[0] for n in notifs])
        except Exception as e:
            logging.error(f"Ошибка авто-уведомлений: {e}")

        await asyncio.sleep(ADMIN_CHECK_INTERVAL)

ADMIN_COMMANDS = [
    BotCommand(command="start", description="🤖 Меню админ-панели"),
    BotCommand(command="stats", description="📊 Статистика за день"),
    BotCommand(command="live", description="🔥 Активность по часам"),
    BotCommand(command="users", description="👥 Последние пользователи"),
    BotCommand(command="notify", description="🔔 Последние уведомления"),
    BotCommand(command="clear", description="🗑 Очистить уведомления"),
    BotCommand(command="gifts", description="🎁 Список подарков"),
    BotCommand(command="setgift", description="✏️ Заменить подарок (slot slug number)"),
    BotCommand(command="setgiftname", description="🏷 Изменить имя подарка"),
    BotCommand(command="resetgifts", description="♻️ Сбросить подарки к дефолтам"),
]

async def setup_admin_menu():
    """Показывает список команд только владельцу при вводе '/' в чате с ботом."""
    try:
        await bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=OWNER_ID),
        )
    except Exception as e:
        logging.error(f"Не удалось установить меню команд: {e}")

async def main():
    asyncio.create_task(notification_worker())
    await setup_admin_menu()
    me = await bot.get_me()
    print(f"✅ Админ-бот запущен: @{me.username}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
