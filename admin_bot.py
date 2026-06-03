import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, BotCommandScopeChat
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import ADMIN_BOT_TOKEN, OWNER_ID, ADMIN_CHECK_INTERVAL

logging.basicConfig(level=logging.INFO)

bot = Bot(token=ADMIN_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== FSM ==========
class Broadcast(StatesGroup):
    waiting_message = State()
    confirm = State()

class AddSponsor(StatesGroup):
    waiting_label = State()
    waiting_url = State()

# ========== БД ==========
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

def init_sponsors_table():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS sponsors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL,
        url TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

def get_sponsors() -> list[tuple]:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT id, label, url FROM sponsors ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return rows

def add_sponsor(label: str, url: str) -> int:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO sponsors (label, url, created_at) VALUES (?,?,?)",
              (label, url, datetime.now().isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()
    return sid

def delete_sponsor(sid: int) -> bool:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM sponsors WHERE id=?", (sid,))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def get_all_user_ids(only_completed=False) -> list[int]:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    if only_completed:
        c.execute("SELECT user_id FROM users WHERE step >= 4")
    else:
        c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

# ========== СТАРТ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Нет доступа")
        return
    await state.clear()
    await message.answer(
        "🤖 <b>Админ-панель PLGiftBot</b>\n\n"
        "<b>📊 Статистика</b>\n"
        "/stats — статистика за день\n"
        "/live — активность по часам\n"
        "/notify — последние уведомления\n"
        "/users — список пользователей\n"
        "/clear — очистить уведомления\n\n"
        "<b>🎁 Подарки</b>\n"
        "/gifts — список текущих подарков\n"
        "/setgift 1-6 Slug Number — заменить подарок\n"
        "/setgiftname 1-6 Имя — изменить имя\n"
        "/resetgifts — вернуть дефолтные подарки\n\n"
        "<b>📢 Рассылка</b>\n"
        "/broadcast — разослать пост всем пользователям\n"
        "/broadcast_done — разослать только завершившим воронку\n\n"
        "<b>🤝 Спонсоры</b>\n"
        "/sponsors — список спонсоров\n"
        "/addsponsor — добавить спонсора\n"
        "/delsponsor ID — удалить спонсора",
        parse_mode=ParseMode.HTML
    )

# ========== СТАТИСТИКА ==========
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    stats, total, completed, step1, step2, step3 = get_stats()
    conv = round((stats[5] / max(stats[1], 1)) * 100, 1)
    await message.answer(
        f"📊 <b>Статистика за сегодня ({stats[0]})</b>\n\n"
        f"👤 Новые {stats[1]}\n"
        f"📝 На комменте шаг 1 {step1}\n"
        f"💬 На комментах в ленте шаг 2 {step2}\n"
        f"🔐 На подписке шаг 3 {step3}\n"
        f"✅ Завершили {stats[5]}\n\n"
        f"📈 Всего пользователей {total}\n"
        f"🏆 Всего завершили {completed}\n\n"
        f"💰 Конверсия сегодня {conv}%",
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
    text = "🔥 <b>Регистрации по часам сегодня</b>\n\n"
    for h in hourly:
        text += f"{h[0]}:00 — {h[1]} чел\n"
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
    text = "👥 <b>Последние 20 пользователей</b>\n\n"
    for u in users:
        status = {0: "🆕", 1: "📝", 2: "💬", 3: "🔐", 4: "✅"}.get(u[3], "❓")
        name = u[2] or "-"
        text += f"{status} <a href='tg://user?id={u[0]}'>{name}</a> | @{u[1] or '-'} | {u[4][:10]}\n"
    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ========== ПОДАРКИ ==========
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
        await message.answer("Подарков нет в БД Запусти главного бота — он засеет дефолты")
        return
    text = "🎁 <b>Текущие подарки</b>\n\n"
    for slot, name, slug, number, emoji in rows:
        link = f"https://t.me/nft/{slug}-{number}"
        text += f"<b>{slot}.</b> {emoji or ''} {name}\n   <code>{slug}-{number}</code>\n   {link}\n\n"
    text += "Менять через /setgift 1-6 Slug Number"
    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("setgift"))
async def cmd_setgift(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Использование /setgift 1-6 Slug Number\nПример /setgift 6 SnoopCigar 12345")
        return
    try:
        slot = int(parts[1])
        slug = parts[2].strip()
        number = int(parts[3].strip())
    except ValueError:
        await message.answer("❌ Slot и Number должны быть числами")
        return
    if not (1 <= slot <= 6):
        await message.answer("❌ Slot должен быть от 1 до 6")
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("UPDATE gifts SET slug=?, number=? WHERE slot=?", (slug, number, slot))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    if not changed:
        await message.answer(f"❌ Подарок в slot {slot} не найден")
        return
    link = f"https://t.me/nft/{slug}-{number}"
    await message.answer(
        f"✅ Подарок #{slot} обновлён\n<code>{slug}-{number}</code>\n{link}",
        parse_mode=ParseMode.HTML, disable_web_page_preview=False
    )

@dp.message(Command("setgiftname"))
async def cmd_setgiftname(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование /setgiftname 1-6 Имя")
        return
    try:
        slot = int(parts[1])
    except ValueError:
        await message.answer("❌ Slot должен быть числом 1-6")
        return
    if not (1 <= slot <= 6):
        await message.answer("❌ Slot должен быть от 1 до 6")
        return
    name = parts[2].strip()
    if not name:
        await message.answer("❌ Имя не может быть пустым")
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
        await message.answer(f"❌ Подарок в slot {slot} не найден")

@dp.message(Command("resetgifts"))
async def cmd_resetgifts(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM gifts")
    conn.commit()
    conn.close()
    try:
        from main_bot import DEFAULT_GIFTS, init_gifts
        init_gifts(DEFAULT_GIFTS)
        await message.answer("✅ Подарки сброшены к дефолтам Открой /gifts")
    except Exception as e:
        await message.answer(f"⚠️ Таблица очищена но засеять не удалось {e}\nПерезапусти главного бота")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM admin_notifications")
    conn.commit()
    conn.close()
    await message.answer("🗑 Уведомления очищены")

# ========== СПОНСОРЫ ==========
@dp.message(Command("sponsors"))
async def cmd_sponsors(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    rows = get_sponsors()
    if not rows:
        await message.answer(
            "📋 Спонсоров пока нет\n\nДобавить через /addsponsor",
            parse_mode=ParseMode.HTML
        )
        return
    text = "📋 <b>Текущие спонсоры</b>\n\n"
    for sid, label, url in rows:
        text += f"<b>#{sid}</b> {label}\n{url}\n\n"
    text += "Удалить через /delsponsor ID\nДобавить через /addsponsor"
    await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("addsponsor"))
async def cmd_addsponsor(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.set_state(AddSponsor.waiting_label)
    await message.answer(
        "➕ <b>Добавление спонсора</b>\n\n"
        "Шаг 1 из 2 — Напиши название кнопки\n"
        "Например: 🎁 Бонус от партнёра\n\n"
        "Для отмены /cancel",
        parse_mode=ParseMode.HTML
    )

@dp.message(AddSponsor.waiting_label)
async def sponsor_get_label(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("❌ Отменено")
        return
    await state.update_data(label=message.text.strip())
    await state.set_state(AddSponsor.waiting_url)
    await message.answer(
        "Шаг 2 из 2 — Отправь ссылку\n"
        "Например: https://t.me/channel или https://t.me/bot\n\n"
        "Для отмены /cancel"
    )

@dp.message(AddSponsor.waiting_url)
async def sponsor_get_url(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("❌ Отменено")
        return
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("❌ Ссылка должна начинаться с http или https Попробуй снова")
        return
    data = await state.get_data()
    label = data.get("label", "Спонсор")
    sid = add_sponsor(label, url)
    await state.clear()
    await message.answer(
        f"✅ <b>Спонсор #{sid} добавлен</b>\n\n"
        f"Кнопка: {label}\n"
        f"Ссылка: {url}\n\n"
        f"Теперь появится в финальном экране бота",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("delsponsor"))
async def cmd_delsponsor(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        rows = get_sponsors()
        if not rows:
            await message.answer("Спонсоров нет")
            return
        text = "Используй /delsponsor ID\n\nТекущие спонсоры\n\n"
        for sid, label, url in rows:
            text += f"#{sid} {label}\n"
        await message.answer(text)
        return
    try:
        sid = int(parts[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return
    if delete_sponsor(sid):
        await message.answer(f"✅ Спонсор #{sid} удалён")
    else:
        await message.answer(f"❌ Спонсор #{sid} не найден")

# ========== РАССЫЛКА ==========
# Храним пост для рассылки в памяти между шагами FSM
_broadcast_message: dict[int, types.Message] = {}
_broadcast_only_done: dict[int, bool] = {}

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    _broadcast_only_done[OWNER_ID] = False
    await state.set_state(Broadcast.waiting_message)
    await message.answer(
        "📢 <b>Рассылка всем пользователям</b>\n\n"
        "Отправь сообщение которое хочешь разослать\n"
        "Поддерживаются текст фото видео и текст с кнопками\n\n"
        "Для отмены напиши /cancel",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("broadcast_done"))
async def cmd_broadcast_done(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    _broadcast_only_done[OWNER_ID] = True
    await state.set_state(Broadcast.waiting_message)
    await message.answer(
        "📢 <b>Рассылка завершившим воронку</b>\n\n"
        "Отправь сообщение которое хочешь разослать\n"
        "Поддерживаются текст фото видео и текст с кнопками\n\n"
        "Для отмены напиши /cancel",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("cancel"), Broadcast.waiting_message)
@dp.message(Command("cancel"), Broadcast.confirm)
async def cmd_cancel(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    await state.clear()
    _broadcast_message.pop(OWNER_ID, None)
    await message.answer("❌ Рассылка отменена")

@dp.message(Broadcast.waiting_message)
async def broadcast_receive_message(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    _broadcast_message[OWNER_ID] = message
    only_done = _broadcast_only_done.get(OWNER_ID, False)

    user_ids = get_all_user_ids(only_completed=only_done)
    target_label = "завершившим воронку" if only_done else "всем пользователям"

    await state.set_state(Broadcast.confirm)
    await message.answer(
        f"👀 <b>Предпросмотр поста выше</b>\n\n"
        f"Получателей <b>{len(user_ids)}</b> ({target_label})\n\n"
        f"Подтвердить рассылку?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разослать", callback_data="broadcast_confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast_cancel"),
            ]
        ])
    )

@dp.callback_query(F.data == "broadcast_cancel", Broadcast.confirm)
async def broadcast_cancel_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    _broadcast_message.pop(OWNER_ID, None)
    await callback.message.edit_text("❌ Рассылка отменена")

@dp.callback_query(F.data == "broadcast_confirm", Broadcast.confirm)
async def broadcast_confirm_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    orig = _broadcast_message.pop(OWNER_ID, None)
    if not orig:
        await callback.message.edit_text("❌ Сообщение не найдено начни заново")
        return

    only_done = _broadcast_only_done.get(OWNER_ID, False)
    user_ids = get_all_user_ids(only_completed=only_done)

    await callback.message.edit_text(
        f"🚀 Запускаю рассылку на <b>{len(user_ids)}</b> пользователей...",
        parse_mode=ParseMode.HTML
    )

    sent = 0
    failed = 0

    for uid in user_ids:
        try:
            # Копируем оригинальное сообщение — сохраняет форматирование фото видео кнопки
            await orig.copy_to(uid)
            sent += 1
        except Exception:
            failed += 1
        # Антифлуд — Telegram разрешает ~30 сообщений/сек
        await asyncio.sleep(0.05)

    await callback.message.answer(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"Отправлено {sent}\n"
        f"Не доставлено {failed}",
        parse_mode=ParseMode.HTML
    )

# ========== АВТ0-УВЕДОМЛЕНИЯ ==========
async def notification_worker():
    await asyncio.sleep(10)
    while True:
        try:
            notifs = get_notifications(5)
            if notifs:
                for n in notifs:
                    await bot.send_message(
                        OWNER_ID,
                        f"🔔 <b>Новое уведомление</b>\n"
                        f"🕐 {n[2][:16]}\n\n{n[1]}",
                        parse_mode=ParseMode.HTML
                    )
                mark_notifications_sent([n[0] for n in notifs])
        except Exception as e:
            logging.error(f"Ошибка авто-уведомлений {e}")
        await asyncio.sleep(ADMIN_CHECK_INTERVAL)

# ========== МЕНЮ ==========
ADMIN_COMMANDS = [
    BotCommand(command="start", description="🤖 Меню админ-панели"),
    BotCommand(command="stats", description="📊 Статистика за день"),
    BotCommand(command="live", description="🔥 Активность по часам"),
    BotCommand(command="users", description="👥 Последние пользователи"),
    BotCommand(command="notify", description="🔔 Последние уведомления"),
    BotCommand(command="clear", description="🗑 Очистить уведомления"),
    BotCommand(command="gifts", description="🎁 Список подарков"),
    BotCommand(command="setgift", description="✏️ Заменить подарок slot slug number"),
    BotCommand(command="setgiftname", description="🏷 Изменить имя подарка"),
    BotCommand(command="resetgifts", description="♻️ Сбросить подарки к дефолтам"),
    BotCommand(command="broadcast", description="📢 Рассылка всем пользователям"),
    BotCommand(command="broadcast_done", description="📢 Рассылка завершившим воронку"),
    BotCommand(command="sponsors", description="🤝 Список спонсоров"),
    BotCommand(command="addsponsor", description="➕ Добавить спонсора"),
    BotCommand(command="delsponsor", description="🗑 Удалить спонсора по ID"),
]

async def setup_admin_menu():
    try:
        await bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=OWNER_ID),
        )
    except Exception as e:
        logging.error(f"Не удалось установить меню команд {e}")

async def main():
    init_sponsors_table()
    asyncio.create_task(notification_worker())
    await setup_admin_menu()
    me = await bot.get_me()
    print(f"✅ Админ-бот запущен @{me.username}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
