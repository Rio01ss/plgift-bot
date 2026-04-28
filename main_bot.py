import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, BotCommand
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

# ========== 50 КОММЕНТАРИЕВ ==========
COMMENTS_50 = [
    "@PLGiftBot реально работает 🔥",
    "спасибо @PLGiftBot за подарок",
    "я не верил но @PLGiftBot работает",
    "Получил подарок от @PLGiftBot, рекомендую",
    "@PLGiftBot лучший бот для подарков",
    "Всем советую @PLGiftBot, проверил сам",
    "@PLGiftBot работает без обмана",
    "Круто @PLGiftBot, спасибо за подарок",
    "@PLGiftBot топ, получил за 5 минут",
    "Не скам @PLGiftBot реально выдает",
    "@PLGiftBot 🔥🔥🔥",
    "Благодарю @PLGiftBot",
    "@PLGiftBot работает 100%",
    "Проверено @PLGiftBot работает",
    "@PLGiftBot реально дает подарки",
    "Спасибо большое @PLGiftBot",
    "@PLGiftBot не обманул",
    "Работает @PLGiftBot рекомендую всем",
    "@PLGiftBot лучший 🔥",
    "Получил от @PLGiftBot, всем советую",
    "@PLGiftBot честный бот",
    "Класс @PLGiftBot работает",
    "@PLGiftBot спасибо за подарочек",
    "Реально работает @PLGiftBot",
    "@PLGiftBot топовый бот",
    "Все работает @PLGiftBot спасибо",
    "@PLGiftBot выдал подарок",
    "Спасибо @PLGiftBot за приз",
    "@PLGiftBot работает отлично",
    "Проверил @PLGiftBot все честно",
    "@PLGiftBot 🔥 работает",
    "Бот @PLGiftBot реально выдает",
    "@PLGiftBot спасибо, получил",
    "Работает без кидалова @PLGiftBot",
    "@PLGiftBot лучший подарочный бот",
    "Всем @PLGiftBot советую",
    "Получил приз @PLGiftBot",
    "@PLGiftBot честно работает",
    "Спасибо за подарок @PLGiftBot",
    "@PLGiftBot работает проверено лично",
    "Крутой бот @PLGiftBot",
    "@PLGiftBot реально дает",
    "Все честно @PLGiftBot",
    "@PLGiftBot получил подарок спасибо",
    "Работает как часы @PLGiftBot",
    "@PLGiftBot 🔥🔥🔥 топ",
    "Спасибо @PLGiftBot все получил",
    "@PLGiftBot не кидалово работает",
    "Лучший бот @PLGiftBot",
    "@PLGiftBot рекомендую друзьям",
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

# ========== ПРОВЕРКА ПОДПИСКИ ==========
ALLOWED_MEMBER_STATUSES = {"member", "administrator", "creator"}

async def check_channel_member(user_id: int) -> bool:
    """Возвращает True ТОЛЬКО если юзер реально состоит в канале.
    Любая ошибка (например, бот не админ канала) → False, и доступ закрыт.
    """
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        is_ok = member.status in ALLOWED_MEMBER_STATUSES
        logging.info(
            f"check_channel_member user={user_id} status={member.status} ok={is_ok}"
        )
        return is_ok
    except Exception as e:
        logging.error(
            f"Ошибка проверки подписки user={user_id}: {e}. "
            f"Убедись, что бот добавлен АДМИНОМ в канал {CHANNEL_ID}."
        )
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
            text += f"\n\nКомментарий: <b>«{comment}»</b>"
        await send(text, parse_mode=ParseMode.HTML)
    elif step == 2:
        text = "📩 <b>Жду скриншоты комментов из ленты TikTok (2-3 штуки).</b>"
        if comment:
            text += f"\n\nКомментарий: <b>«{comment}»</b>"
        await send(text, parse_mode=ParseMode.HTML)
    elif step == 3:
        await send(
            "📩 <b>Жду скриншот подписки на канал.</b>\n"
            f"👇 <code>{CHANNEL_LINK}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔐 Подписаться на канал", url=CHANNEL_LINK)]
            ]),
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
        "благодарность (например <i>«спасибо, сработало»</i> — точный текст "
        "пришлёт сам бот) и поставь лайк. Затем пришли боту скриншот.\n\n"
        "<b>Шаг 2️⃣ — Отметить бота под 3 видео 🎯</b>\n"
        "Найди в ленте 3 популярных видео и под каждым напиши комментарий "
        "<b>с отметкой нашего бота</b> (готовый текст пришлёт сам бот). "
        "Отправь скриншоты.\n\n"
        "<b>Шаг 3️⃣ — Подписка на канал 🔐</b>\n"
        "Подпишись на закрытый канал по присланной ссылке и нажми "
        "<b>«✅ Я подписался»</b> — бот сам проверит подписку через Telegram. "
        "Можешь также прислать скриншот подписки.\n\n"
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
        f"✅ <b>Успешно, вы успели!</b>\n"
        f"Вам выпал подарок\n\n"
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
        f"выпущен @{me.username}\n\n"
        f"👤 <b>Владелец:</b> Gift Relayer 🤖\n"
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
            [InlineKeyboardButton(text="OK", callback_data="step1_comment")]
        ])
    )

RANDOM_COMMENTS_50 = [
    "имба работает",
    "реально работает",
    "топ бот",
    "не скам, проверено",
    "получил подарок, спасибо",
    "работает без обмана",
    "лучший бот для подарков",
    "проверил сам, всё ок",
    "сработало с первого раза",
    "бот огонь",
    "честный бот",
    "выдаёт реально",
    "получил приз, рекомендую",
    "круто, всё пришло",
    "не верил, но работает",
    "спасибо за подарок",
    "топчик",
    "всё чётко",
    "забрал свой подарок",
    "респект создателю",
    "вау, реально работает",
    "проверено, не развод",
    "получилось с первого раза",
    "нереально крутой бот",
    "всё пришло мгновенно",
    "советую всем",
    "лучшее что видел",
    "выдал подарок, спасибо",
    "имба, работает",
    "ребята не обманывают",
    "пушка бот",
    "всё по-честному",
    "забрал свой приз",
    "класс, рекомендую",
    "получил, доволен",
    "проверил — рабочий",
    "топовая тема",
    "сработало, спасибо",
    "огонь, всё дошло",
    "подарок получен",
    "не лохотрон, проверено",
    "красавчики, всё выдали",
    "забираю свой подарок",
    "годнота",
    "бомба бот",
    "всё чётенько пришло",
    "получил, кайф",
    "работает 100%",
    "проверено лично",
    "забрал подарок, спс",
]

@dp.callback_query(F.data == "step1_comment")
async def step1_comment(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    comment = random.choice(RANDOM_COMMENTS_50)
    update_user(user_id, step=1, comment_text=comment)

    await callback.message.answer(
        f"📊 <b>Шаг 1 из 3</b>  ⬜⬜⬜\n\n"
        f"📥 <b>Для получения необходимо:</b>\n\n"
        f"1️⃣ Написать <b>«{comment}»</b> под комментарием с которого узнали о нас, и лайкуть его\n\n"
        f"📩 <b>Отправьте боту скриншот выполнения</b>",
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
    update_user(user_id, step=2, screenshots_count=1, comment_text=bot_comment)
    await state.set_state(Funnel.feed_comments)
    increment_stat("step1_done")

    add_notification(
        f"📝 Новый комментарий (шаг 1)!\n"
        f"ID: {user_id}\n"
        f"Юзер: @{message.from_user.username or 'нет'}\n"
        f"Текст: {step1_comment}"
    )

    await message.answer(
        f"📊 <b>Шаг 2 из 3</b>  ✅⬜⬜\n\n"
        f"✅ <b>Скриншот получен!</b>\n\n"
        f"2️⃣ Теперь зайди в <b>ленту TikTok (For You)</b>, найди <b>3 популярных видео</b> "
        f"(с большим количеством лайков/просмотров) и под каждым напиши комментарий "
        f"<b>с отметкой нашего бота</b>:\n\n"
        f"<b>«{bot_comment}»</b>\n\n"
        f"💡 <i>Совет: ищи видео на тему подарков, халявы, Telegram — там твоя аудитория</i>\n\n"
        f"📩 <b>Отправь скриншоты</b> (2-3 штуки достаточно)",
        parse_mode=ParseMode.HTML
    )

@dp.message(F.photo, Funnel.feed_comments)
async def screenshot_step2(message: types.Message, state: FSMContext):
    """Шаг 2 → Шаг 3: получили скрин из ленты, просим подписку."""
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        await message.answer("❌ Сначала нажми /start")
        return

    screenshots = (user[6] or 0) + 1
    update_user(user_id, step=3, screenshots_count=screenshots)
    await state.set_state(Funnel.subscribed)
    increment_stat("step2_done")

    add_notification(
        f"💬 Комментарии в ленте (шаг 2)!\n"
        f"ID: {user_id}\n"
        f"Юзер: @{message.from_user.username or 'нет'}\n"
        f"Скринов: {screenshots}"
    )

    await message.answer(
        f"📊 <b>Шаг 3 из 3</b>  ✅✅⬜\n\n"
        f"✅ <b>Отлично! Осталось последнее действие</b>\n\n"
        f"3️⃣ Подпишись на закрытый канал:\n"
        f"👇 <code>{CHANNEL_LINK}</code>\n\n"
        f"После подписки нажми <b>«✅ Я подписался»</b> — "
        f"я сам проверю и зачислю тебе награду.\n"
        f"Можно также прислать скриншот подписки.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Подписаться на канал", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
        ])
    )

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

    if SPONSOR_ENABLED:
        text += f"\n\n🎁 <b>Бонус от спонсора:</b>\nПолучи дополнительные призы!"
        keyboard.insert(0, [InlineKeyboardButton(text="🎁 Бонус от спонсора", url=SPONSOR_LINK)])

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
        "❌ <b>Я не вижу тебя в канале!</b>\n\n"
        f"Подпишись: <code>{CHANNEL_LINK}</code>\n"
        f"И снова нажми <b>«✅ Я подписался»</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Подписаться", url=CHANNEL_LINK)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")],
        ])
    )

@dp.callback_query(F.data == "check_sub", Funnel.subscribed)
async def check_sub_callback(callback: types.CallbackQuery, state: FSMContext):
    """Авто-проверка подписки на канал по нажатию кнопки."""
    user_id = callback.from_user.id
    is_member = await check_channel_member(user_id)

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

@dp.message(F.photo, Funnel.subscribed)
async def screenshot_step3(message: types.Message, state: FSMContext):
    """Шаг 3 → Финал: проверяем подписку и завершаем воронку (по скриншоту)."""
    user_id = message.from_user.id
    is_member = await check_channel_member(user_id)

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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
