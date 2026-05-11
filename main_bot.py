import asyncio
import html
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    BotCommand,
    ChatJoinRequest,
)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from config import *

logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        step INTEGER DEFAULT 0,
        dice_result INTEGER,
        comment_text TEXT,
        screenshots_count INTEGER DEFAULT 0,
        joined_channel INTEGER DEFAULT 0,
        created_at TEXT,
        last_active TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS stats (
        date TEXT PRIMARY KEY,
        new_users INTEGER DEFAULT 0,
        step1_done INTEGER DEFAULT 0,
        step2_done INTEGER DEFAULT 0,
        step3_done INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS admin_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT,
        is_sent INTEGER DEFAULT 0,
        created_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS gifts (
        slot INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT NOT NULL,
        number INTEGER NOT NULL,
        model TEXT,
        pattern TEXT,
        background TEXT,
        rarity_model TEXT,
        rarity_pattern TEXT,
        rarity_bg TEXT,
        total INTEGER,
        issued INTEGER,
        value TEXT,
        emoji TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS channel_join_requests (
        user_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, channel_id)
    )''')

    conn.commit()
    conn.close()

def init_gifts(defaults: dict):
    """Засевает таблицу gifts из defaults, только если она пустая."""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM gifts")
    if c.fetchone()[0] == 0:
        for slot, g in defaults.items():
            c.execute(
                "INSERT INTO gifts (slot, name, slug, number, model, pattern, background, "
                "rarity_model, rarity_pattern, rarity_bg, total, issued, value, emoji) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    slot, g["name"], g["slug"], g["number"],
                    g["model"], g["pattern"], g["background"],
                    g["rarity_model"], g["rarity_pattern"], g["rarity_bg"],
                    g["total"], g["issued"], g["value"], g["emoji"],
                )
            )
        conn.commit()
    conn.close()

def _row_to_gift(row) -> dict:
    g = {
        "slot": row[0], "name": row[1], "slug": row[2], "number": row[3],
        "model": row[4], "pattern": row[5], "background": row[6],
        "rarity_model": row[7], "rarity_pattern": row[8], "rarity_bg": row[9],
        "total": row[10], "issued": row[11], "value": row[12], "emoji": row[13],
    }
    g["gift_link"] = f"https://t.me/nft/{g['slug']}-{g['number']}"
    return g

def get_gift(slot: int) -> dict:
    """Возвращает подарок из БД для slot (1-6). Если нет — slot 1 как fallback."""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT * FROM gifts WHERE slot=?", (slot,))
    row = c.fetchone()
    if row is None:
        c.execute("SELECT * FROM gifts WHERE slot=1")
        row = c.fetchone()
    conn.close()
    return _row_to_gift(row) if row else None

def get_all_gifts() -> list[dict]:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT * FROM gifts ORDER BY slot")
    rows = c.fetchall()
    conn.close()
    return [_row_to_gift(r) for r in rows]

def update_gift_slug_number(slot: int, slug: str, number: int) -> bool:
    """Обновляет slug + номер подарка в slot. Возвращает True если что-то обновилось."""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("UPDATE gifts SET slug=?, number=? WHERE slot=?", (slug, number, slot))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def update_gift_name(slot: int, name: str) -> bool:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("UPDATE gifts SET name=? WHERE slot=?", (name, slot))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def reset_gifts_to_defaults(defaults: dict):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM gifts")
    conn.commit()
    conn.close()
    init_gifts(defaults)

def get_user(user_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def add_user(user_id, username, first_name):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, created_at, last_active) VALUES (?,?,?,?,?)",
              (user_id, username, first_name, now, now))
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("INSERT INTO stats (date, new_users) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET new_users=new_users+1",
              (today,))
    conn.commit()
    conn.close()

def update_user(user_id, **kwargs):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    for key, value in kwargs.items():
        c.execute(f"UPDATE users SET {key}=?, last_active=? WHERE user_id=?", 
                  (value, datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def increment_feed_screenshot_count_by(user_id: int, delta: int) -> int:
    """Атомарно +delta к счётчику скринов шага 2 (альбомом приходит несколько фото — одним пакетом)."""
    if delta <= 0:
        conn = sqlite3.connect('bot.db')
        c = conn.cursor()
        c.execute("SELECT screenshots_count FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()
        return int(row[0]) if row and row[0] is not None else 0
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        "UPDATE users SET screenshots_count = COALESCE(screenshots_count, 0) + ?, last_active = ? "
        "WHERE user_id = ?",
        (delta, now, user_id),
    )
    c.execute("SELECT screenshots_count FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.commit()
    conn.close()
    return int(row[0]) if row and row[0] is not None else 0

def record_channel_join_request(user_id: int, channel_id: int) -> None:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO channel_join_requests (user_id, channel_id, created_at) VALUES (?,?,?)",
        (user_id, channel_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

def has_channel_join_request(user_id: int, channel_id: int) -> bool:
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM channel_join_requests WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id),
    )
    ok = c.fetchone() is not None
    conn.close()
    return ok

def increment_stat(field):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute(f"INSERT INTO stats (date, {field}) VALUES (?, 1) ON CONFLICT(date) DO UPDATE SET {field}={field}+1",
              (today,))
    conn.commit()
    conn.close()

def add_notification(text):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO admin_notifications (text, created_at) VALUES (?, ?)",
              (text, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ========== 50 КОММЕНТАРИЕВ (лента TikTok, задание 2 — с отметкой бота) ==========
COMMENTS_50 = [
    "Ребят, не спите — @PLGiftBot реально отдаёт подарки, гоните проверять 👇",
    "Кто ещё сомневается — откройте @PLGiftBot и сами всё увидите, я уже в деле",
    "Лайкните, если тоже любите халяву: я через @PLGiftBot забрал приз, попробуйте и вы",
    "Забирай подарок, пока не поздно — @PLGiftBot работает без заморочек 🔥",
    "Пишу под популярным роликом: @PLGiftBot выручает, кому актуально — вперёд по ссылке в профиле бота",
    "Скиньте друзьям: @PLGiftBot, проверено лично, не развод",
    "Если лень искать — начните с @PLGiftBot, дальше всё интуитивно",
    "Кидаю всем в комменты: @PLGiftBot — топ по подаркам в Telegram",
    "Хочешь так же? Тогда не тяни — @PLGiftBot ждёт тебя первым шагом",
    "Мне пришло, значит и вам придёт — @PLGiftBot, не благодарите 😄",
    "Ставь ❤️ и бегом в @PLGiftBot — там вся механика за пару минут",
    "Без воды: @PLGiftBot сработал с первого захода, кому надо — забирайте",
    "Кто в теме подарков в TG — загляните в @PLGiftBot, окупится с лихвой",
    "Мой честный отзыв: @PLGiftBot выдал то, что обещал — дублируйте у себя",
    "Не верьте слухам — проверьте @PLGiftBot сами, дальше решайте",
    "Под этим видео оставлю метку @PLGiftBot — пусть люди не боятся пробовать",
    "Коротко: @PLGiftBot = рабочая схема, осталось только сделать пару шагов",
    "Кто пролистал до сюда — поздравляю, вы нашли @PLGiftBot, дальше проще",
    "Забирай инструкцию в @PLGiftBot и повторяй за мной — у меня вышло",
    "Для тех, кто «всё видел»: @PLGiftBot всё равно удивит, зайдите свежим взглядом",
    "Тегаю @PLGiftBot, чтобы вы не потеряли — там реальные призы, не картинки",
    "Листай ленту дальше, но сначала загляни в @PLGiftBot — потом скажешь спасибо",
    "Подарок уже у меня в профиле — спасибо @PLGiftBot, дублируйте сценарий",
    "Если вы тут за халявой — ваш маршрут: @PLGiftBot и пару минут внимания",
    "Не скриньте только меня — сами пройдите путь в @PLGiftBot, так честнее",
    "Кидаю якорь @PLGiftBot — кто хочет такой же результат, действуйте",
    "Работает как заявлено: @PLGiftBot, без скрытых платежей и странных ссылок",
    "Кто боится кидалова — смотрите на меня и на @PLGiftBot, всё прозрачно",
    "Поставь лайк автору и загляни в @PLGiftBot — там вторая часть истории",
    "Мне хватило одного вечера с @PLGiftBot — попробуйте выстроить свой темп",
    "Делюсь находкой: @PLGiftBot, чтобы вы не гуглили часами",
    "Если лень читать простыни — @PLGiftBot ведёт за руку, я прошёл",
    "Короткий путь к призу — через @PLGiftBot, остальное приложится",
    "Не откладывай на потом — @PLGiftBot сейчас в тренде, потом будет очередь",
    "Отмечаю @PLGiftBot здесь, чтобы алгоритм показал тем, кому это зайдёт",
    "Сделай скрин и отправь друзьям с упоминанием @PLGiftBot — пусть тоже заберут",
    "Я не рекламщик, я пользователь: @PLGiftBot реально выручает",
    "Кто ищет честный сервис подарков — ваш ориентир @PLGiftBot",
    "Пишу вслух: @PLGiftBot, забирайте пока работает стабильно",
    "Лента полна обещаний, а @PLGiftBot — про конкретику и шаги",
    "Не верьте мне на слово — зайдите в @PLGiftBot и вернитесь с отзывом",
    "Мой совет дня: начни с @PLGiftBot, потом расскажешь, как прошло",
    "Кто любит быстрые победы — @PLGiftBot как раз про это",
    "Оставляю метку @PLGiftBot под роликом, который залетит — пусть люди видят",
    "Хочешь так же круто — повтори маршрут через @PLGiftBot",
    "Без лишних слов: @PLGiftBot сработал, дальше за вами",
    "Кому нужен живой кейс — я прошёл через @PLGiftBot, дублируйте",
    "Тег @PLGiftBot для тех, кто всё ещё ищет «нормальный» бот подарков",
    "Забирай сценарий: лайк автору + шаги в @PLGiftBot = результат",
    "Пусть комменты не зря: @PLGiftBot — проверенная точка входа",
]

# ========== РЕАЛЬНЫЕ ПОДАРКИ TELEGRAM ==========
# ВАЖНО: чтобы Telegram нарисовал большую карточку подарка из ссылки t.me/nft/...,
# КОМБИНАЦИЯ <Name>-<Number> ДОЛЖНА реально существовать на Telegram.
# Иначе превью будет пустым/обычным. Snoop Dogg #412744 — подтверждённый рабочий пример.
DEFAULT_GIFTS = {
    1: {
        "name": "Jelly Bunny",
        "number": 94870,
        "model": "Ballistics Gel",
        "pattern": "Mint Green",
        "background": "Scissors",
        "rarity_model": "0.4%",
        "rarity_pattern": "0.4%",
        "rarity_bg": "1.2%",
        "total": 581059,
        "issued": 593781,
        "value": "~850 UAH",
        "emoji": "🐰",
        "slug": "JellyBunny",
    },
    2: {
        "name": "Diamond Key",
        "number": 2142,
        "model": "Play Button",
        "pattern": "Pacific Cyan",
        "background": "Flower Sun",
        "rarity_model": "0.1%",
        "rarity_pattern": "0.8%",
        "rarity_bg": "2%",
        "total": 150000,
        "issued": 152000,
        "value": "~2 500 UAH",
        "emoji": "🔑",
        "slug": "DiamondKey",
    },
    3: {
        "name": "Lol Pop",
        "number": 65421,
        "model": "Strawberry",
        "pattern": "Sweet Swirl",
        "background": "Sky Blue",
        "rarity_model": "0.8%",
        "rarity_pattern": "1.5%",
        "rarity_bg": "0.2%",
        "total": 320000,
        "issued": 325000,
        "value": "~1 200 UAH",
        "emoji": "🍭",
        "slug": "LolPop",
    },
    4: {
        "name": "Snoop Dogg",
        "number": 412744,
        "model": "Fish Hat",
        "pattern": "Bubble Tea",
        "background": "Mystic Pearl",
        "rarity_model": "3%",
        "rarity_pattern": "0.4%",
        "rarity_bg": "1.2%",
        "total": 581059,
        "issued": 593781,
        "value": "~1 033,00 UAH",
        "emoji": "🐕",
        "slug": "SnoopDogg",
    },
    5: {
        "name": "Toy Bear",
        "number": 89134,
        "model": "Pink Plush",
        "pattern": "Hearts",
        "background": "Lavender",
        "rarity_model": "5%",
        "rarity_pattern": "2%",
        "rarity_bg": "4%",
        "total": 890000,
        "issued": 902000,
        "value": "~300 UAH",
        "emoji": "🧸",
        "slug": "ToyBear",
    },
    6: {
        "name": "Snoop Cigar",
        "number": 156803,
        "model": "Premium Blend",
        "pattern": "Dark Lilac",
        "background": "Misfit",
        "rarity_model": "1.2%",
        "rarity_pattern": "0.6%",
        "rarity_bg": "3%",
        "total": 420000,
        "issued": 435000,
        "value": "~600 UAH",
        "emoji": "🚬",
        "slug": "SnoopCigar",
    },
}

for _g in DEFAULT_GIFTS.values():
    _g["gift_link"] = f"https://t.me/nft/{_g['slug']}-{_g['number']}"

# Создаём таблицы и засеваем подарки на module load,
# чтобы admin_bot мог читать их сразу при импорте.
init_db()
init_gifts(DEFAULT_GIFTS)

# ========== БОТ ==========
bot = Bot(token=MAIN_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== FSM СОСТОЯНИЯ ==========
class Funnel(StatesGroup):
    start = State()          # step 0 — ещё не бросал кубик
    gift_shown = State()     # step 1 — нужно прислать скрин первого комментария
    feed_comments = State()  # step 2 — нужно прислать скрины комментов в ленте
    subscribed = State()     # step 3 — нужно прислать скрин подписки на канал
    completed = State()      # step 4 — всё пройдено

STEP_TO_STATE = {
    0: Funnel.start,
    1: Funnel.gift_shown,
    2: Funnel.feed_comments,
    3: Funnel.subscribed,
    4: Funnel.completed,
}

class StateSyncMiddleware(BaseMiddleware):
    """Синхронизирует FSM-состояние пользователя с шагом из БД на каждом апдейте.
    Это нужно, чтобы перезапуск бота (MemoryStorage сбрасывается) не ломал воронку.
    """
    async def __call__(self, handler, event, data):
        state: FSMContext = data.get("state")
        user_obj = None
        if isinstance(event, types.Message):
            user_obj = event.from_user
        elif isinstance(event, types.CallbackQuery):
            user_obj = event.from_user
        if user_obj is not None and state is not None:
            db_user = get_user(user_obj.id)
            if db_user is not None:
                target = STEP_TO_STATE.get(db_user[3], Funnel.start)
                current = await state.get_state()
                if current != target.state:
                    await state.set_state(target)
        return await handler(event, data)

dp.message.middleware(StateSyncMiddleware())
dp.callback_query.middleware(StateSyncMiddleware())

# ========== Шаг 2 воронки (скрины из ленты) ==========
REQUIRED_FEED_SCREENSHOTS = 5


def ru_n_screenshots(n: int) -> str:
    """Число + слово «скриншот» с правильным склонением (2 скриншота, 5 скриншотов)."""
    n_abs = abs(n) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 19:
        word = "скриншотов"
    elif n1 == 1:
        word = "скриншот"
    elif 2 <= n1 <= 4:
        word = "скриншота"
    else:
        word = "скриншотов"
    return f"{n} {word}"


# Альбом (media_group): ждём короткую паузу, считаем все фото пакетом — одно сообщение от бота
STEP2_MEDIA_GROUP_FLUSH_SEC = 0.85
_step2_mg_lock = asyncio.Lock()
_step2_mg_counts: dict[tuple[int, int], int] = {}
_step2_mg_last_message: dict[tuple[int, int], types.Message] = {}
_step2_mg_tasks: dict[tuple[int, int], asyncio.Task] = {}


async def _run_step2_after_photos(
    answer_message: types.Message, state: FSMContext, user_id: int, delta: int
) -> None:
    """Учёт delta скриншотов шага 2 и одно ответное сообщение (для альбома delta > 1)."""
    user = get_user(user_id)
    if not user:
        await answer_message.answer("❌ Сначала нажми /start")
        return

    new_count = increment_feed_screenshot_count_by(user_id, delta)

    if new_count < REQUIRED_FEED_SCREENSHOTS:
        remaining = REQUIRED_FEED_SCREENSHOTS - new_count
        await answer_message.answer(
            f"Получено {new_count}/{REQUIRED_FEED_SCREENSHOTS}. "
            f"Отправь ещё {ru_n_screenshots(remaining)}.",
            parse_mode=ParseMode.HTML,
        )
        return

    update_user(user_id, step=3)
    await state.set_state(Funnel.subscribed)
    increment_stat("step2_done")

    add_notification(
        f"💬 Комментарии в ленте (шаг 2)!\n"
        f"ID: {user_id}\n"
        f"Юзер: @{answer_message.from_user.username or 'нет'}\n"
        f"Скринов: {new_count}"
    )

    await answer_message.answer(
        f"📊 <b>Шаг 3 из 3</b>  ✅✅⬜\n\n"
        f"✅ <b>Отлично! Осталось последнее действие</b>\n\n"
        f"3️⃣ Подпишись на закрытый канал (или подай заявку на вступление — бот это засчитает):\n"
        f"👇 <code>{html.escape(CHANNEL_LINK)}</code>\n\n"
        f"После подписки или подачи заявки нажми <b>«✅ Я подписался»</b> — "
        f"я сам проверю и зачислю тебе награду.\n"
        f"Можно также прислать скриншот подписки.",
        parse_mode=ParseMode.HTML,
        reply_markup=subscription_reply_markup(),
    )


async def _flush_step2_media_group(key: tuple[int, int], state: FSMContext) -> None:
    try:
        await asyncio.sleep(STEP2_MEDIA_GROUP_FLUSH_SEC)
    except asyncio.CancelledError:
        return
    async with _step2_mg_lock:
        n = _step2_mg_counts.pop(key, 0)
        msg = _step2_mg_last_message.pop(key, None)
        _step2_mg_tasks.pop(key, None)
    if n <= 0 or msg is None:
        return
    await _run_step2_after_photos(msg, state, key[0], n)


async def schedule_step2_media_group(message: types.Message, state: FSMContext) -> None:
    """Накапливает фото одного альбома; после паузы без новых частей — один ответ."""
    assert message.media_group_id is not None
    key = (message.from_user.id, message.media_group_id)
    async with _step2_mg_lock:
        _step2_mg_counts[key] = _step2_mg_counts.get(key, 0) + 1
        _step2_mg_last_message[key] = message
        old = _step2_mg_tasks.get(key)
        if old is not None and not old.done():
            old.cancel()
        _step2_mg_tasks[key] = asyncio.create_task(_flush_step2_media_group(key, state))


def subscription_reply_markup() -> InlineKeyboardMarkup:
    """Кнопки: основной канал, резервные по ссылкам из конфига, проверка подписки."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔐 Подписаться на канал", url=CHANNEL_LINK)],
    ]
    for idx, link in enumerate(CHANNEL_BACKUP_LINKS, start=1):
        label = (
            f"🔐 Резервный канал {idx}"
            if len(CHANNEL_BACKUP_LINKS) > 1
            else "🔐 Резервный канал"
        )
        rows.append([InlineKeyboardButton(text=label, url=link)])
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ========== ПРОВЕРКА ПОДПИСКИ ==========
ALLOWED_MEMBER_STATUSES = {"member", "administrator", "creator"}

async def check_force_sub_satisfied(user_id: int) -> bool:
    """True, если пользователь member/admin/creator в одном из каналов Force Sub
    ИЛИ зафиксирована заявка на вступление (chat_join_request) в этот канал.
    """
    for cid in FORCE_SUB_CHANNEL_IDS:
        try:
            member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
            if member.status in ALLOWED_MEMBER_STATUSES:
                logging.info(
                    f"check_force_sub user={user_id} channel={cid} status={member.status} ok=True"
                )
                return True
        except Exception as e:
            logging.error(
                f"Ошибка get_chat_member user={user_id} channel={cid}: {e}. "
                f"Убедись, что бот — админ канала."
            )
        if has_channel_join_request(user_id, cid):
            logging.info(
                f"check_force_sub user={user_id} channel={cid} ok=True (join_request pending)"
            )
            return True

    logging.info(f"check_force_sub user={user_id} ok=False channels={FORCE_SUB_CHANNEL_IDS}")
    return False

# ========== ХЕЛПЕР: ПОДСКАЗКА ПО ТЕКУЩЕМУ ШАГУ ==========
async def send_step_hint(message_or_callback, db_user):
    """Отправляет подсказку, что пользователь должен сделать сейчас."""
    if isinstance(message_or_callback, types.CallbackQuery):
        send = message_or_callback.message.answer
    else:
        send = message_or_callback.answer

    step = db_user[3] if db_user else 0
    comment = db_user[5] if db_user else None

    if step == 0:
        await send(
            "🎁 <b>Начни с броска кубика — нажми /start</b>",
            parse_mode=ParseMode.HTML,
        )
    elif step == 1:
        text = "📩 <b>Жду скриншот первого комментария.</b>"
        if comment:
            text += f"\n\nТекст для комментария (скопируй одним нажатием):\n<code>{html.escape(comment)}</code>"
        await send(text, parse_mode=ParseMode.HTML)
    elif step == 2:
        text = (
            f"📩 <b>Жду скриншоты комментов из ленты TikTok "
            f"(нужно {REQUIRED_FEED_SCREENSHOTS} скриншотов, можно альбомом).</b>"
        )
        if comment:
            text += f"\n\nТекст с отметкой бота (скопируй одним нажатием):\n<code>{html.escape(comment)}</code>"
        await send(text, parse_mode=ParseMode.HTML)
    elif step == 3:
        await send(
            "📩 <b>Жду подтверждения: подписка или заявка в один из наших каналов.</b>\n"
            f"👇 <code>{html.escape(CHANNEL_LINK)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=subscription_reply_markup(),
        )
    elif step >= 4:
        await send(
            "🎉 <b>Ты уже всё прошёл!</b> Жди розыгрыша.",
            parse_mode=ParseMode.HTML,
        )

# ========== ОБРАБОТЧИКИ ==========

@dp.message(Command("timer"))
async def cmd_timer(message: types.Message):
    """Показать таймер до следующего розыгрыша."""
    await message.answer(draw_countdown_text(), parse_mode=ParseMode.HTML)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Подробная инструкция по получению подарка."""
    text = (
        "ℹ️ <b>Как получить подарок</b>\n\n"
        "Сначала жми /start и нажми кнопку «🎲 Бросить кубик» — бот покажет, "
        "какой подарок тебе выпал. После этого пройди 3 шага воронки:\n\n"
        "<b>Шаг 1️⃣ — Благодарность под исходным комментарием 📝</b>\n"
        "Под комментарием, с которого ты узнал о нас, напиши короткую "
        "благодарность (точный текст пришлю в моноширинном блоке — "
        "его можно скопировать одним нажатием) и поставь лайк. "
        "Затем пришли боту скриншот.\n\n"
        "<b>Шаг 2️⃣ — Отметить бота под видео в ленте 🎯</b>\n"
        "Найди в ленте <b>несколько популярных видео</b> и под каждым напиши комментарий "
        "<b>с отметкой нашего бота</b> (готовый текст пришлю отдельным моноширинным блоком — "
        "в Telegram его можно скопировать одним нажатием). "
        f"Пришли боту <b>{REQUIRED_FEED_SCREENSHOTS} скриншотов</b> (можно одним альбомом; "
        "на весь альбом бот ответит один раз).\n\n"
        "<b>Шаг 3️⃣ — Подписка на канал 🔐</b>\n"
        "Подпишись на закрытый канал по ссылке (или подай заявку на вступление — "
        "бот это тоже засчитает) и нажми <b>«✅ Я подписался»</b>. "
        "Можно также прислать скриншот подписки.\n\n"
        "🎉 <b>Готово!</b> Ты в очереди на розыгрыш.\n\n"
        "<b>Полезные команды:</b>\n"
        "/start — начать или начать заново\n"
        "/timer — когда следующий розыгрыш\n"
        "/help — эта инструкция"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name

    add_user(user_id, username, first_name)

    # Сбрасываем прогресс воронки — даём пользователю начать с чистого листа
    update_user(
        user_id,
        step=0,
        dice_result=None,
        screenshots_count=0,
        joined_channel=0,
        comment_text=None,
    )
    await state.set_state(Funnel.start)

    welcome_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить кубик", callback_data="roll_dice")],
        [InlineKeyboardButton(text="🔍 Посмотреть отзывы", url="https://t.me/PLGiftOTZ")]
    ])
    welcome_text = (
        "🎁 <b>Брось кубик и получи подарок</b> 👇🎁\n\n"
        "Жми кнопку ниже!"
    )
    try:
        await message.answer_photo(
            photo=FSInputFile("PLGIFT.png"),
            caption=welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_kb,
        )
    except Exception as e:
        logging.error(f"Не удалось отправить PLGIFT.png: {e}")
        await message.answer(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_kb,
        )

@dp.callback_query(F.data == "roll_dice", Funnel.start)
async def roll_dice(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    dice_msg = await callback.message.answer_dice(emoji="🎲")
    await asyncio.sleep(2.5)

    result = dice_msg.dice.value
    gift = get_gift(result)

    update_user(user_id, step=1, dice_result=result)
    await state.set_state(Funnel.gift_shown)

    await callback.message.answer(
        f"✅ <b>Успешно, ты успел!</b>\n"
        f"Тебе выпал подарок\n\n"
        f"{gift['gift_link']}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Забрать подарок 🎁", callback_data="show_gift")]
        ])
    )

@dp.callback_query(F.data == "show_gift")
async def show_gift(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = get_user(user_id)

    if not user:
        return

    result = user[4] or 1
    gift = get_gift(result)
    me = await bot.get_me()

    text = (
        f"<b>{gift['name']} #{gift['number']}</b>\n"
        f"выпущен ботом @{me.username}\n\n"
        f"👤 <b>Владелец:</b> Подарочный ретранслятор 🤖\n"
        f"🎩 <b>Модель:</b> {gift['model']} <code>{gift['rarity_model']}</code>\n"
        f"🎨 <b>Узор:</b> {gift['pattern']} <code>{gift['rarity_pattern']}</code>\n"
        f"🌈 <b>Фон:</b> {gift['background']} <code>{gift['rarity_bg']}</code>\n"
        f"📦 <b>Количество:</b> {gift['total']:,}, выпущено {gift['issued']:,}\n"
        f"💰 <b>Ценность:</b> <code>{gift['value']}</code> "
        f"<a href='{gift['gift_link']}'>подробнее</a>"
    )

    await callback.message.answer(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Понятно", callback_data="step1_comment")]
        ])
    )

# ========== 50 фраз для благодарности под исходным комментарием (задание 1) ==========
RANDOM_COMMENTS_50 = [
    "Спасибо за наводку — полез сюда по твоему комменту, уже забрал свой подарок 🔥",
    "Лайк поставил и пошёл по ссылке: всё сработало, как ты и писал — респект автору коммента",
    "Не зря пролистал до тебя: твой отзыв оказался правдой, делюсь тем же с другими",
    "Забираю подарок и возвращаюсь с благодарностью — кто сомневается, пусть повторит мой путь",
    "Твой коммент спас меня от сомнений: прошёл по инструкции и не пожалел, кидаю плюс в ленту",
    "Оставляю тут короткое «спасибо» — без твоего сообщения я бы прошёл мимо",
    "Работает один в один, как ты описал: лайк тебе и удачи в ленте",
    "Скинул друзьям твой тред — пусть тоже попробуют, у меня всё вышло с первого раза",
    "Честно, думал развод — проверил лично, всё ок, спасибо что подсветил тему",
    "Забираю приз и оставляю след в комментах: пусть алгоритм поднимет полезный отзыв выше",
    "Ты молодец, что не жадничаешь инфой — я повторил шаги и получил результат",
    "Поставил сердечко и написал, как просили: дальше только вперёд за подарками",
    "Коротко и по делу — твой коммент сработал как стартовый сигнал, полет нормальный",
    "Если кто читает цепочку — не ленитесь, повторите сценарий, у меня сработало",
    "Лайк автору и плюс в карму: на твоём пути реально можно выиграть время",
    "Вернулся с подтверждением — не фейк, не картинка из интернета, всё по-взрослому",
    "Пусть этот коммент поднимут: тут правда, а не очередная сказка про «лёгкие деньги»",
    "Забираю бонус и оставляю благодарность — пусть люди видят живой кейс",
    "Ты подсветил рабочую схему — я прошёл её и советую не тянуть с регистрацией",
    "Не верил до последнего — теперь сам пишу под роликом, чтобы другие не боялись",
    "Сделал как в инструкции под твоим постом: всё чисто, без скрытых условий",
    "Кидаю «спасибо» вслух — без твоего сообщения я бы так и остался в сомнениях",
    "Лента полна воды, а тут конкретика — повторил и получил то, что обещали",
    "Поставил лайк и забрал подарок — возвращаюсь с отчётом, как просили ветку",
    "Твой совет окупился за пару минут — делюсь им дальше по комментариям",
    "Оставляю след под видео: пусть кто сомневается, увидит ещё один живой отзыв",
    "Работает стабильно — лайк тебе и удачи, кто дочитал, тот уже в теме",
    "Забираю приз и не жадничаю словами — правда рабочая, проверено сегодня",
    "Ты сэкономил мне часы поисков — отблагодарил лайком и короткой фразой здесь",
    "Пусть модерация не удалит: тут реальный опыт, а не рекламная простыня",
    "Сделал по твоему маршруту — всё легло в голову, спасибо за честный сигнал",
    "Не скринь только меня — сами пройдите шаги, у меня без сюрпризов",
    "Лайк и короткое «реально работает» — больше добавить нечего, всё по факту",
    "Твой коммент стоил того, чтобы остановить скролл — результат уже у меня",
    "Возвращаюсь с благодарностью: кто ищет нормальный способ, тот найдёт по твоим словам",
    "Поставил плюс автору и пошёл дальше по цепочке — цель выполнена, подарок у меня",
    "Коротко: сработало, как ты написал — пусть коммент не утонет, это полезно",
    "Забираю награду и оставляю добрый след — пусть лента покажет это тем, кто в поиске",
    "Ты не зря старался в комменте — я проверил и подтверждаю каждое слово",
    "Лайк тебе и удачи в рекомендациях — пусть больше людей увидят рабочую подсказку",
    "Сделал всё по инструкции из твоего сообщения — возвращаюсь с «спасибо» и плюсом",
    "Не развод, проверено лично — оставляю это здесь, чтобы цепочка была длиннее",
    "Твой отзыв сработал как трамплин — я уже с призом, остальным тоже советую",
    "Поставил лайк и написал коротко: правда, работает, иду дальше по воронке",
    "Забираю подарок и фиксирую благодарность — пусть автор видит, что не зря старался",
    "Кто читает ветку до конца — не бойтесь, повторите действия, у меня без подвоха",
    "Ты подсветил нормальный вход в тему — я прошёл и возвращаюсь с подтверждением",
    "Лайк и уважение: без твоего коммента я бы так и гонял сомнения в голове",
    "Короткий отчёт: всё честно, шаги простые — спасибо, что не жадничаешь инфой",
    "Оставляю метку под роликом — пусть алгоритм покажет тем, кто тоже ищет халяву",
]

@dp.callback_query(F.data == "step1_comment")
async def step1_comment(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    comment = random.choice(RANDOM_COMMENTS_50)
    update_user(user_id, step=1, comment_text=comment)

    c_html = html.escape(comment)
    await callback.message.answer(
        f"📊 <b>Шаг 1 из 3</b>  ⬜⬜⬜\n\n"
        f"📥 <b>Для получения необходимо:</b>\n\n"
        f"1️⃣ Написать под комментарием, с которого узнал о нас, такой текст "
        f"(скопируй одним нажатием) и поставь лайк:\n<code>{c_html}</code>\n\n"
        f"📩 <b>Отправь боту скриншот выполнения</b>",
        parse_mode=ParseMode.HTML
    )

@dp.message(F.photo, Funnel.gift_shown)
async def screenshot_step1(message: types.Message, state: FSMContext):
    """Шаг 1 → Шаг 2: получили скрин первого комментария."""
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала нажми /start")
        return

    step1_comment = user[5]
    bot_comment = random.choice(COMMENTS_50)
    update_user(user_id, step=2, screenshots_count=0, comment_text=bot_comment)
    await state.set_state(Funnel.feed_comments)
    increment_stat("step1_done")

    add_notification(
        f"📝 Новый комментарий (шаг 1)!\n"
        f"ID: {user_id}\n"
        f"Юзер: @{message.from_user.username or 'нет'}\n"
        f"Текст: {step1_comment}"
    )

    bc_html = html.escape(bot_comment)
    await message.answer(
        f"📊 <b>Шаг 2 из 3</b>  ✅⬜⬜\n\n"
        f"✅ <b>Скриншот получен!</b>\n\n"
        f"2️⃣ Теперь зайди в <b>ленту TikTok (For You)</b>, найди <b>популярные видео</b> "
        f"(с большим количеством лайков/просмотров) и под каждым напиши комментарий "
        f"<b>с отметкой нашего бота</b>. Скопируй текст одним нажатием:\n\n"
        f"<code>{bc_html}</code>\n\n"
        f"💡 <i>Совет: ищи видео на тему подарков, халявы, Telegram — там твоя аудитория</i>\n\n"
        f"📩 <b>Отправь {REQUIRED_FEED_SCREENSHOTS} скриншотов</b> "
        f"(можно одним альбомом — каждый кадр засчитается; бот ответит один раз на весь альбом)",
        parse_mode=ParseMode.HTML
    )

@dp.message(F.photo, Funnel.feed_comments)
async def screenshot_step2(message: types.Message, state: FSMContext):
    """Шаг 2: счётчик скринов в БД; альбом — несколько фото, одно ответное сообщение."""
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала нажми /start")
        return
    if message.media_group_id is not None:
        await schedule_step2_media_group(message, state)
        return
    await _run_step2_after_photos(message, state, user_id, 1)

# ========== ТАЙМЕР ДО СЛЕДУЮЩЕГО РОЗЫГРЫША ==========
MSK_TZ = timezone(timedelta(hours=3))
DRAW_HOUR_MSK = 21  # ежедневный розыгрыш в 21:00 МСК

def next_draw_dt() -> datetime:
    """Возвращает datetime следующего розыгрыша в МСК."""
    now = datetime.now(MSK_TZ)
    today_draw = now.replace(hour=DRAW_HOUR_MSK, minute=0, second=0, microsecond=0)
    if now >= today_draw:
        return today_draw + timedelta(days=1)
    return today_draw

def format_time_left(td: timedelta) -> str:
    """'5ч 23м' или '47м 12с' — компактный человекочитаемый формат."""
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}ч {minutes:02d}м"
    if minutes > 0:
        return f"{minutes}м {seconds:02d}с"
    return f"{seconds}с"

def draw_countdown_text() -> str:
    """Готовая строка с временем следующего розыгрыша."""
    nxt = next_draw_dt()
    left = nxt - datetime.now(MSK_TZ)
    return (
        f"⏰ <b>Следующий розыгрыш:</b> "
        f"{nxt.strftime('%d.%m в %H:%M')} МСК\n"
        f"⏳ Осталось: <b>{format_time_left(left)}</b>"
    )

# ========== ХЕЛПЕР: ЗАВЕРШЕНИЕ ВОРОНКИ ==========
async def finalize_funnel(user_id: int, username: str | None, state: FSMContext, send):
    """Помечает пользователя как завершившего воронку и шлёт финальное сообщение."""
    update_user(user_id, step=4, joined_channel=1)
    await state.set_state(Funnel.completed)
    increment_stat("step3_done")
    increment_stat("completed")

    me = await bot.get_me()
    share_text = "Получи реальный подарок из Telegram! 🎁"
    share_url = f"https://t.me/{me.username}"

    text = "🎉 <b>Все задания выполнены!</b>\n\n<b>Выполнено ✅</b>\n\n"
    text += "⏳ <b>Ты в очереди на получение подарка!</b>\n"
    text += draw_countdown_text() + "\n\n"
    text += "👥 <b>Пригласи друга — получи дополнительный шанс!</b>"

    keyboard = [
        [InlineKeyboardButton(
            text="📤 Пригласить друга",
            url=f"https://t.me/share/url?url={share_url}&text={share_text}"
        )],
        [InlineKeyboardButton(text="🎲 Бросить ещё раз", callback_data="play_again")],
    ]

    if SPONSOR_LINKS:
        text += f"\n\n🎁 <b>Бонус от спонсора:</b>\nПолучи дополнительные призы!"
        sponsor_block: list[list[InlineKeyboardButton]] = []
        for i, link in enumerate(SPONSOR_LINKS):
            label = "🎁 Бонус от спонсора" if len(SPONSOR_LINKS) == 1 else f"🎁 Спонсор {i + 1}"
            sponsor_block.append([InlineKeyboardButton(text=label, url=link)])
        keyboard = sponsor_block + keyboard

    await send(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

    add_notification(
        f"✅ Пользователь завершил все задания!\n"
        f"ID: {user_id}\n"
        f"Юзер: @{username or 'нет'}\n"
        f"В канале: ✅"
    )

async def _send_not_subscribed(send):
    await send(
        "❌ <b>Пока не вижу подписки или заявки в канал.</b>\n\n"
        "Вступи в один из наших каналов по ссылке или подай заявку на вступление — "
        "бот зафиксирует заявку и пропустит дальше.\n"
        "Затем снова нажми <b>«✅ Я подписался»</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=subscription_reply_markup(),
    )

@dp.callback_query(F.data == "check_sub", Funnel.subscribed)
async def check_sub_callback(callback: types.CallbackQuery, state: FSMContext):
    """Авто-проверка подписки на канал по нажатию кнопки."""
    user_id = callback.from_user.id
    is_member = await check_force_sub_satisfied(user_id)

    if not is_member:
        await callback.answer("Подписка не найдена 🙁", show_alert=True)
        await _send_not_subscribed(callback.message.answer)
        return

    await callback.answer("Подписка подтверждена ✅", show_alert=False)
    await finalize_funnel(user_id, callback.from_user.username, state, callback.message.answer)

@dp.callback_query(F.data == "check_sub")
async def check_sub_wrong_state(callback: types.CallbackQuery):
    """Кнопка нажата не на шаге 3 — даём подсказку по текущему шагу."""
    user = get_user(callback.from_user.id)
    await callback.answer()
    if user:
        await send_step_hint(callback, user)


@dp.chat_join_request()
async def on_chat_join_request(join: ChatJoinRequest):
    """Каналы с заявками: фиксируем заявку — check_force_sub_satisfied засчитает как успех."""
    cid = join.chat.id
    if cid not in FORCE_SUB_CHANNEL_IDS:
        return
    uid = join.from_user.id
    try:
        record_channel_join_request(uid, cid)
        logging.info(f"chat_join_request: сохранена заявка user={uid} channel={cid}")
    except Exception:
        logging.exception(f"chat_join_request: ошибка записи в БД user={uid} channel={cid}")

@dp.message(F.photo, Funnel.subscribed)
async def screenshot_step3(message: types.Message, state: FSMContext):
    """Шаг 3 → Финал: проверяем подписку и завершаем воронку (по скриншоту)."""
    user_id = message.from_user.id
    is_member = await check_force_sub_satisfied(user_id)

    if not is_member:
        await _send_not_subscribed(message.answer)
        return

    await finalize_funnel(user_id, message.from_user.username, state, message.answer)

# ========== БРОСИТЬ ЕЩЁ РАЗ ==========
@dp.callback_query(F.data == "play_again")
async def play_again(callback: types.CallbackQuery, state: FSMContext):
    """Сбрасывает прогресс и предлагает бросить кубик снова."""
    user_id = callback.from_user.id
    update_user(
        user_id,
        step=0,
        dice_result=None,
        screenshots_count=0,
        joined_channel=0,
        comment_text=None,
    )
    await state.set_state(Funnel.start)
    await callback.answer("Прогресс сброшен 🔄")

    welcome_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Бросить кубик", callback_data="roll_dice")],
        [InlineKeyboardButton(text="🔍 Посмотреть отзывы", url="https://t.me/PLGiftOTZ")],
    ])
    try:
        await callback.message.answer_photo(
            photo=FSInputFile("PLGIFT.png"),
            caption=(
                "🎁 <b>Брось кубик и получи подарок</b> 👇🎁\n\n"
                "Жми кнопку ниже!"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_kb,
        )
    except Exception as e:
        logging.error(f"play_again: не удалось отправить PLGIFT.png: {e}")
        await callback.message.answer(
            "🎁 <b>Брось кубик и получи подарок</b> 👇🎁\n\nЖми кнопку ниже!",
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_kb,
        )

# ========== FALLBACK: ловим всё, что не подошло под состояние ==========
@dp.callback_query(F.data == "roll_dice")
async def roll_dice_already(callback: types.CallbackQuery):
    """Кубик уже бросал — повторно не даём."""
    await callback.answer(
        "Ты уже бросал кубик 🎲\nЕсли хочешь начать сначала — отправь /start",
        show_alert=True,
    )
    user = get_user(callback.from_user.id)
    await send_step_hint(callback, user)

@dp.message(F.photo)
async def photo_unexpected(message: types.Message):
    """Скрин получен, но шаг не тот — подсказываем, что вообще ждём."""
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("❌ Сначала нажми /start")
        return
    await send_step_hint(message, user)

@dp.message()
async def message_fallback(message: types.Message):
    """Любой другой текст/стикер/что угодно — мягкая подсказка."""
    user = get_user(message.from_user.id)
    if not user:
        await message.answer(
            "👋 Привет! Чтобы участвовать — нажми /start",
            parse_mode=ParseMode.HTML,
        )
        return
    await send_step_hint(message, user)

USER_COMMANDS = [
    BotCommand(command="start", description="🎁 Начать (или начать заново)"),
    BotCommand(command="timer", description="⏰ Когда следующий розыгрыш"),
    BotCommand(command="help", description="ℹ️ Как получить подарок"),
]

async def setup_user_menu():
    """Меню команд при вводе '/' — для всех пользователей бота."""
    try:
        await bot.set_my_commands(USER_COMMANDS)
    except Exception as e:
        logging.error(f"Не удалось установить меню команд: {e}")

# ========== ЗАПУСК ==========
async def main():
    init_db()
    await setup_user_menu()
    me = await bot.get_me()
    print(f"✅ Основной бот запущен: @{me.username}")
    allowed = list(dp.resolve_used_update_types())
    if "chat_join_request" not in allowed:
        allowed.append("chat_join_request")
    await dp.start_polling(bot, allowed_updates=allowed)

if __name__ == "__main__":
    asyncio.run(main())
