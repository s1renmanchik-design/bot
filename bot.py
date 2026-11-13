import asyncio
import datetime
import html
import logging
import os
import random
import re
import sys

from aiohttp import web
import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

try:
  from aiogram.client.default import DefaultBotProperties
except ImportError:  # старые версии aiogram 3.x
  DefaultBotProperties = None

try:
  from zoneinfo import ZoneInfo

  LOCAL_TZ = ZoneInfo("Europe/Kyiv")
except Exception:  # нет базы часовых поясов -> фиксированный UTC+2
  LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=2))

logging.basicConfig(level=logging.INFO)

_bg_tasks = set()

# ВАЖНО: токен только из переменной окружения TOKEN (на хостинге).
TOKEN = os.getenv("TOKEN")
if not TOKEN:
  sys.exit("Не задана переменная окружения TOKEN")

PORT = int(os.getenv("PORT", 10000))
# Путь к базе: на хостинге укажи путь на постоянном диске, например /data/bot.db
DB_NAME = os.getenv("DB_PATH", "bot.db")

ADMIN_ID = 5681420389

# Канал для обязательной подписки (ID = -100 + число).
CHANNEL_ID = -1002925107145
CHANNEL_URL = "https://t.me/iiris_cnn_bot"

DT_FORMAT = "%Y-%m-%d %H:%M:%S"

# Модерация
MAX_WARNS = 3
PERM_BAN = "2999-12-31 00:00:00"

# Рефералы
REF_BONUS = 7  # токенов пригласившему за подписавшегося реферала

# Топы
TOP_RESET_DAYS = 14
TOP_REWARD_PREMIUM_DAYS = 7
TOP_REWARD_YTOKENS = 2

# Лотерея
LOTTERY_HOURS = 3
LOTTERY_NORMAL_CHANCE = 0.80  # обычный приз 4-10 токенов
LOTTERY_BIG_LOW_CHANCE = 0.15  # крупный приз 11-14 токенов
# остаток (5%) — самый крупный приз 15-20 токенов
# Уведомление отправляется когда СУММАРНЫЙ приз (базовый + взносы) >= этого значения
LOTTERY_NOTIFY_PRIZE = 15
LOTTERY_MIN_PLAYERS = 2
LOTTERY_BIG_PRIZE = 15
LOTTERY_BIG_MIN_PLAYERS = 4


# --- ОБЩИЕ ХЕЛПЕРЫ ---


def connect():
  """Подключение к БД с увеличенным ожиданием (против 'database is locked')."""
  return aiosqlite.connect(DB_NAME, timeout=30)


def esc(value) -> str:
  """Экранирует текст для HTML-разметки Telegram."""
  return html.escape(str(value), quote=False)


def hidden_link(link) -> str:
  """Ссылка под спойлером: скрыта, пока человек не нажмёт на неё."""
  return f"<tg-spoiler>{esc(link)}</tg-spoiler>"


def ru_plural(n: int, one: str, few: str, many: str) -> str:
  """'1 токен', '2 токена', '5 токенов'."""
  n_abs = abs(n)
  if n_abs % 10 == 1 and n_abs % 100 != 11:
    word = one
  elif 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
    word = few
  else:
    word = many
  return f"{n} {word}"


def now_local() -> datetime.datetime:
  """Текущее время по Киеву (naive) - нужно только для ночного режима."""
  return datetime.datetime.now(LOCAL_TZ).replace(tzinfo=None)


def parse_dt(value):
  try:
    return datetime.datetime.strptime(value, DT_FORMAT) if value else None
  except Exception:
    return None


def txt(message: Message) -> str:
  return (message.text or "").strip()


async def safe_send(bot: Bot, uid: int, text: str, **kwargs) -> bool:
  try:
    await bot.send_message(uid, text, **kwargs)
    return True
  except Exception:
    return False


async def deny(message: Message) -> bool:
  """True, если пользователь не админ (и ему уже ответили)."""
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return True
  return False


def parse_args(command: CommandObject, count: int):
  """Ровно count целых чисел или None."""
  parts = (command.args or "").split()
  if len(parts) != count or not all(p.isdigit() for p in parts):
    return None
  return [int(p) for p in parts]


def lottery_cost_for_total(total: int) -> int:
  """Каждые 5 токенов приза = +1 токен к цене участия (минимум 1)."""
  return max(1, total // 5)


def fee_to_prize(paid: int) -> int:
  """В приз уходит 50% потраченных на участие токенов (вверх)."""
  return (paid + 1) // 2


def cost_text(cost: int) -> str:
  return "бесплатно" if cost == 0 else f"{cost} токен(ов)"


def lottery_min_players(prize: int) -> int:
  return (
      LOTTERY_BIG_MIN_PLAYERS if prize >= LOTTERY_BIG_PRIZE else LOTTERY_MIN_PLAYERS
  )


# --- БАЗА ДАННЫХ ---


async def init_db():
  async with connect() as db:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tokens INTEGER DEFAULT 0,
            y_tokens INTEGER DEFAULT 0,
            full_tokens INTEGER DEFAULT 0,
            is_premium INTEGER DEFAULT 0,
            bought_folders INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT 0,
            ref_activated INTEGER DEFAULT 0,
            ref_income INTEGER DEFAULT 0,
            ref_extra INTEGER DEFAULT 0,
            premium_expires TEXT DEFAULT NULL,
            warns INTEGER DEFAULT 0,
            banned_until TEXT DEFAULT NULL,
            ban_reason TEXT DEFAULT NULL,
            ref_joined_at TEXT DEFAULT NULL,
            notify_lottery INTEGER DEFAULT 1,
            aura_type TEXT DEFAULT NULL,
            aura_name TEXT DEFAULT NULL,
            aura_percent INTEGER DEFAULT 0,
            aura_expires TEXT DEFAULT NULL,
            lottery_wins INTEGER DEFAULT 0,
            exchanges INTEGER DEFAULT 0,
            restricted_until TEXT DEFAULT NULL
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price TEXT,
            link TEXT,
            buyers_count INTEGER DEFAULT 0,
            description TEXT DEFAULT NULL,
            photo_id TEXT DEFAULT NULL,
            token_buyers INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS unique_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            token_price INTEGER DEFAULT 0,
            amount INTEGER,
            command TEXT,
            description TEXT DEFAULT NULL,
            photo_id TEXT DEFAULT NULL,
            token_buyers INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            reward_type TEXT,
            reward_value TEXT,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_activations (
            user_id INTEGER,
            code TEXT,
            PRIMARY KEY (user_id, code)
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_cost INTEGER,
            prize INTEGER,
            ends_at TEXT,
            finished INTEGER DEFAULT 0,
            cost_fixed INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_participants (
            round_id INTEGER,
            user_id INTEGER,
            paid INTEGER DEFAULT NULL,
            PRIMARY KEY (round_id, user_id)
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS roulette_state (
            user_id INTEGER PRIMARY KEY,
            games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ex_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER, give TEXT, want TEXT, owner_link TEXT,
            search TEXT, status TEXT DEFAULT 'open',
            taker_id INTEGER, taker_link TEXT, created INTEGER, taken INTEGER
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS ex_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            offer_id INTEGER, complainer_id INTEGER, accused_id INTEGER,
            status TEXT DEFAULT 'new', created INTEGER,
            UNIQUE (offer_id, complainer_id)
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS top_reset (
            id INTEGER PRIMARY KEY,
            next_reset TEXT
        );
    """
    )

    # Миграция: добавляем недостающие колонки в таблицы старой базы
    migrations = {
        "users": {
            "tokens": "INTEGER DEFAULT 0",
            "y_tokens": "INTEGER DEFAULT 0",
            "full_tokens": "INTEGER DEFAULT 0",
            "is_premium": "INTEGER DEFAULT 0",
            "bought_folders": "INTEGER DEFAULT 0",
            "referrer_id": "INTEGER DEFAULT 0",
            "ref_activated": "INTEGER DEFAULT 0",
            "ref_income": "INTEGER DEFAULT 0",
            "ref_extra": "INTEGER DEFAULT 0",
            "premium_expires": "TEXT DEFAULT NULL",
            "warns": "INTEGER DEFAULT 0",
            "banned_until": "TEXT DEFAULT NULL",
            "ban_reason": "TEXT DEFAULT NULL",
            "exchanges": "INTEGER DEFAULT 0",
            "restricted_until": "TEXT DEFAULT NULL",
            "ref_joined_at": "TEXT DEFAULT NULL",
            "notify_lottery": "INTEGER DEFAULT 1",
            "aura_type": "TEXT DEFAULT NULL",
            "aura_name": "TEXT DEFAULT NULL",
            "aura_percent": "INTEGER DEFAULT 0",
            "aura_expires": "TEXT DEFAULT NULL",
            "lottery_wins": "INTEGER DEFAULT 0",
        },
        "products": {
            "buyers_count": "INTEGER DEFAULT 0",
            "description": "TEXT DEFAULT NULL",
            "photo_id": "TEXT DEFAULT NULL",
            "token_buyers": "INTEGER DEFAULT 0",
        },
        "unique_products": {
            "token_price": "INTEGER DEFAULT 0",
            "description": "TEXT DEFAULT NULL",
            "photo_id": "TEXT DEFAULT NULL",
            "token_buyers": "INTEGER DEFAULT 0",
        },
        "lottery_participants": {"paid": "INTEGER DEFAULT NULL"},
        "lottery_rounds": {"cost_fixed": "INTEGER DEFAULT 0"},
        "promocodes": {
            "max_uses": "INTEGER DEFAULT 0",
            "used_count": "INTEGER DEFAULT 0",
        },
        "roulette_state": {
            "games": "INTEGER DEFAULT 0",
            "wins": "INTEGER DEFAULT 0",
        },
    }
    for table, columns in migrations.items():
      async with db.execute(f"PRAGMA table_info({table})") as cur:
        existing = {row[1] for row in await cur.fetchall()}
      for col, ddl in columns.items():
        if col not in existing:
          await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
          logging.info("Миграция: добавлена колонка %s.%s", table, col)

    await db.commit()


# --- СОСТОЯНИЯ (FSM) ---


class AddProduct(StatesGroup):
  title = State()
  price = State()
  link = State()
  description = State()
  photo = State()


class AddUniqueProduct(StatesGroup):
  title = State()
  price = State()
  token_price = State()
  amount = State()
  command = State()
  description = State()
  photo = State()


class EditProduct(StatesGroup):
  new_value = State()
  photo = State()


class EditUniqueProduct(StatesGroup):
  new_value = State()
  photo = State()


class BroadcastState(StatesGroup):
  message = State()


class PromocodeState(StatesGroup):
  code = State()


class CreatePromoState(StatesGroup):
  code = State()
  type = State()
  value = State()
  uses = State()


class GivePremiumState(StatesGroup):
  user_id = State()
  days = State()


class GiveState(StatesGroup):
  user_id = State()
  amount = State()


class NewOfferState(StatesGroup):
  give = State()
  want = State()
  link = State()


class ExSearchState(StatesGroup):
  query = State()


class ExTakeState(StatesGroup):
  link = State()


class ExAdminMsgState(StatesGroup):
  text = State()


class RouletteState(StatesGroup):
  bet = State()


router = Router()


# --- АУРА, БАНЫ, ПРЕДУПРЕЖДЕНИЯ ---


async def get_user_aura(user_id: int):
  async with connect() as db:
    async with db.execute(
        "SELECT aura_type, aura_name, aura_percent, aura_expires FROM users"
        " WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0] or not row[3]:
      return None
    exp = parse_dt(row[3])
    if exp is None:
      return None
    if exp <= datetime.datetime.now():
      await db.execute(
          "UPDATE users SET aura_type = NULL, aura_name = NULL,"
          " aura_percent = 0, aura_expires = NULL WHERE user_id = ?",
          (user_id,),
      )
      await db.commit()
      return None
    return {
        "type": row[0],
        "name": row[1],
        "percent": row[2] or 0,
        "expires": exp,
    }


async def get_active_ban_info(user_id: int):
  async with connect() as db:
    async with db.execute(
        "SELECT banned_until, ban_reason FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0]:
      return None, None
    until = parse_dt(row[0])
    if until is None:
      return None, None
    if until <= datetime.datetime.now():
      await db.execute(
          "UPDATE users SET banned_until = NULL, ban_reason = NULL WHERE"
          " user_id = ?",
          (user_id,),
      )
      await db.commit()
      return None, None
    return until, row[1]


APPEAL_TEXT = (
    "Если вы ограничены от бота или заблокированы, напишите @ucu52, если вы"
    " получили наказание незаслуженно."
)


def ban_text(until: datetime.datetime, reason: str = None) -> str:
  if until.year >= 2900:
    base = "🚫 Ваш профиль заблокирован навсегда."
  else:
    base = f"🚫 Ваш профиль заблокирован до {until.strftime('%d.%m.%Y %H:%M')}."
  reason_str = f"\n📝 Причина: <b>{esc(reason)}</b>" if reason else ""
  return f"{base}{reason_str}\n\n{APPEAL_TEXT}"


async def ensure_user(db, user_id: int) -> None:
  await db.execute(
      "INSERT OR IGNORE INTO users (user_id, tokens, y_tokens, full_tokens,"
      " is_premium, bought_folders, referrer_id, ref_activated)"
      " VALUES (?, 0, 0, 0, 0, 0, 0, 0)",
      (user_id,),
  )


async def ban_user(target_id: int, days: int, reason: str = None) -> str:
  """Банит на days дней (0 = навсегда). Возвращает текст срока."""
  if days > 0:
    until = datetime.datetime.now() + datetime.timedelta(days=days)
    until_str = until.strftime(DT_FORMAT)
    text = f"до {until.strftime('%d.%m.%Y %H:%M')}"
  else:
    until_str = PERM_BAN
    text = "навсегда"
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET banned_until = ?, ban_reason = ? WHERE user_id = ?",
        (until_str, reason, target_id),
    )
    await db.commit()
  return text


async def unban_user(target_id: int) -> None:
  async with connect() as db:
    await db.execute(
        "UPDATE users SET banned_until = NULL, ban_reason = NULL WHERE"
        " user_id = ?",
        (target_id,),
    )
    await db.commit()


async def add_warn(target_id: int, reason: str = None):
  """Добавляет предупреждение. Возвращает (кол-во варнов, забанен ли)."""
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET warns = COALESCE(warns, 0) + 1 WHERE user_id = ?",
        (target_id,),
    )
    async with db.execute(
        "SELECT warns FROM users WHERE user_id = ?", (target_id,)
    ) as cur:
      row = await cur.fetchone()
    warns = row[0] if row and row[0] is not None else 0
    banned = False
    if warns >= MAX_WARNS:
      ban_msg = f"Превышен лимит предупреждений ({MAX_WARNS})" + (
          f": {reason}" if reason else ""
      )
      await db.execute(
          "UPDATE users SET banned_until = ?, ban_reason = ?, is_premium = 0,"
          " premium_expires = NULL, tokens = 0, y_tokens = 0, full_tokens = 0"
          " WHERE user_id = ?",
          (PERM_BAN, ban_msg, target_id),
      )
      banned = True
    await db.commit()
  return warns, banned


async def remove_warn(target_id: int) -> int:
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET warns = MAX(0, COALESCE(warns, 0) - 1)"
        " WHERE user_id = ?",
        (target_id,),
    )
    async with db.execute(
        "SELECT warns FROM users WHERE user_id = ?", (target_id,)
    ) as cur:
      row = await cur.fetchone()
    await db.commit()
  return row[0] if row and row[0] is not None else 0


async def send_warn_notice(bot: Bot, target_id: int, warns: int, banned: bool):
  if banned:
    text = (
        f"🚫 Вы получили {warns}/{MAX_WARNS} предупреждений. Ваш профиль"
        " заблокирован навсегда, премиум-подписка снята, токены обнулены."
    )
  else:
    text = (
        f"⚠️ Вам выдано предупреждение ({warns}/{MAX_WARNS}). При"
        f" {MAX_WARNS} предупреждениях профиль будет заблокирован,"
        " премиум снят, токены обнулены."
    )
  await safe_send(bot, target_id, text)


async def remove_premium(target_id: int) -> None:
  async with connect() as db:
    await db.execute(
        "UPDATE users SET is_premium = 0, premium_expires = NULL"
        " WHERE user_id = ?",
        (target_id,),
    )
    await db.commit()


async def remove_tokens(target_id: int, amount: int) -> int:
  """Забирает токены (не уходит ниже 0). Возвращает новый баланс."""
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET tokens = MAX(0, COALESCE(tokens, 0) - ?)"
        " WHERE user_id = ?",
        (amount, target_id),
    )
    async with db.execute(
        "SELECT tokens FROM users WHERE user_id = ?", (target_id,)
    ) as cur:
      row = await cur.fetchone()
    await db.commit()
  return row[0] if row and row[0] is not None else 0


# --- ОБЯЗАТЕЛЬНАЯ ПОДПИСКА И РЕФЕРАЛЫ ---


async def is_subscribed(bot: Bot, user_id: int) -> bool:
  try:
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
  except Exception:
    # Бот не админ в канале или неверный CHANNEL_ID: не блокируем всех,
    # но пишем ошибку в лог.
    logging.exception("Не удалось проверить подписку")
    return True
  if member.status in (
      ChatMemberStatus.MEMBER,
      ChatMemberStatus.ADMINISTRATOR,
      ChatMemberStatus.CREATOR,
  ):
    return True
  if member.status == ChatMemberStatus.RESTRICTED:
    return bool(getattr(member, "is_member", False))
  return False


async def activate_referral(bot: Bot, user_id: int) -> None:
  """Бонус пригласившему: один раз и только после подписки."""
  async with connect() as db:
    cur = await db.execute(
        "UPDATE users SET ref_activated = 1, ref_income = ? WHERE user_id = ?"
        " AND ref_activated = 0 AND referrer_id != 0",
        (REF_BONUS, user_id),
    )
    if cur.rowcount == 0:
      return
    async with db.execute(
        "SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)
    ) as c:
      row = await c.fetchone()
    if not row:
      return
    referrer_id = row[0]
    await ensure_user(db, referrer_id)
    await db.execute(
        "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
        (REF_BONUS, referrer_id),
    )
    await db.commit()
  await safe_send(
      bot,
      referrer_id,
      "🎉 По вашей рефке зарегистрировался новый пользователь!\n"
      f"❤️ Вам начислено {REF_BONUS} токенов!",
  )


def sub_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
          [
              InlineKeyboardButton(
                  text="✅ Я подписался", callback_data="check_sub"
              )
          ],
      ]
  )


SUB_TEXT = (
    "🔒 Чтобы пользоваться ботом, подпишись на наш канал и нажми «Я"
    " подписался»."
)


class SubscribeMiddleware(BaseMiddleware):

  async def __call__(self, handler, event, data):
    user = data.get("event_from_user")
    bot: Bot = data["bot"]

    if user is None or user.id == ADMIN_ID:
      return await handler(event, data)

    # Забаненные пользователи не могут пользоваться ботом
    until, reason = await get_active_ban_info(user.id)
    if until is not None:
      msg_text = ban_text(until, reason)
      if isinstance(event, CallbackQuery):
        if event.message:
          await event.message.answer(msg_text)
        await event.answer()
      else:
        await event.answer(msg_text)
      return None

    # /start пропускаем, чтобы реферальная ссылка успела сработать
    if isinstance(event, Message) and (event.text or "").startswith("/start"):
      return await handler(event, data)

    # кнопку проверки подписки пропускаем
    if isinstance(event, CallbackQuery) and event.data == "check_sub":
      return await handler(event, data)

    if await is_subscribed(bot, user.id):
      await activate_referral(bot, user.id)
      return await handler(event, data)

    if isinstance(event, CallbackQuery):
      if event.message:
        await event.message.answer(SUB_TEXT, reply_markup=sub_keyboard())
      await event.answer()
    else:
      await event.answer(SUB_TEXT, reply_markup=sub_keyboard())
    return None


router.message.outer_middleware(SubscribeMiddleware())
router.callback_query.outer_middleware(SubscribeMiddleware())


def get_main_keyboard(user_id):
  kb = [
      [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="🛒 Магазин")],
      [KeyboardButton(text="💎 Уникальный магазин")],
      [
          KeyboardButton(text="👥 Реферальная система"),
          KeyboardButton(text="🏆 Топ игроков"),
      ],
      [
          KeyboardButton(text="🪙 Как получить токены?"),
          KeyboardButton(text="👑 Премиум-подписка"),
      ],
      [KeyboardButton(text="🔄 Обмены"), KeyboardButton(text="🎰 Лотерея")],
      [KeyboardButton(text="🎲 Рулетка")],
  ]
  if user_id == ADMIN_ID:
    kb.append([KeyboardButton(text="⚙️ Админ-панель")])
  return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# --- ВЫДАЧА ТОКЕНОВ, ПРЕМИУМА, ПРОМОКОДЫ ---


async def _add_balance(target_id: int, column: str, amount: int) -> None:
  if column not in ("tokens", "y_tokens", "full_tokens"):
    raise ValueError("bad column")
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        f"UPDATE users SET {column} = COALESCE({column}, 0) + ? WHERE"
        " user_id = ?",
        (amount, target_id),
    )
    await db.commit()


async def grant_tokens(target_id: int, amount: int) -> None:
  await _add_balance(target_id, "tokens", amount)


async def grant_y_tokens(target_id: int, amount: int) -> None:
  await _add_balance(target_id, "y_tokens", amount)


async def grant_full_tokens(target_id: int, amount: int) -> None:
  await _add_balance(target_id, "full_tokens", amount)


def premium_active(is_prem, expires_str) -> bool:
  if is_prem != 1:
    return False
  exp = parse_dt(expires_str)
  return exp is None or exp > datetime.datetime.now()


async def apply_premium(db, user_id: int, days: int) -> datetime.datetime:
  """Выдаёт/продлевает премиум в уже открытом соединении (без commit)."""
  await ensure_user(db, user_id)
  async with db.execute(
      "SELECT is_premium, premium_expires FROM users WHERE user_id = ?",
      (user_id,),
  ) as cur:
    row = await cur.fetchone()
  now = datetime.datetime.now()
  base = now
  if row and row[0] == 1:
    cur_exp = parse_dt(row[1])
    if cur_exp and cur_exp > now:
      base = cur_exp
  new_expire = base + datetime.timedelta(days=days)
  await db.execute(
      "UPDATE users SET is_premium = 1, premium_expires = ? WHERE user_id = ?",
      (new_expire.strftime(DT_FORMAT), user_id),
  )
  return new_expire


async def grant_premium(target_id: int, days: int) -> datetime.datetime:
  async with connect() as db:
    new_expire = await apply_premium(db, target_id, days)
    await db.commit()
  return new_expire


async def apply_promocode(user_id: int, code_text: str) -> str:
  code_text = (code_text or "").strip()
  if not code_text:
    return "❌ Промокод не указан."
  try:
    async with connect() as db:
      async with db.execute(
          "SELECT code, reward_type, reward_value, max_uses, used_count"
          " FROM promocodes WHERE LOWER(code) = LOWER(?)",
          (code_text,),
      ) as cur:
        promo = await cur.fetchone()
      if not promo:
        return "❌ Неверный или несуществующий промокод."

      real_code, reward_type, reward_value, _max_uses, _used = promo
      try:
        val = int(reward_value)
      except (TypeError, ValueError):
        return "❌ У промокода неверно задана награда."
      if reward_type not in ("tokens", "premium"):
        return "❌ Неизвестный тип награды у промокода."

      await ensure_user(db, user_id)
      # Активация: PRIMARY KEY не даст активировать дважды
      try:
        await db.execute(
            "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
            (user_id, real_code),
        )
      except aiosqlite.IntegrityError:
        return "❌ Вы уже активировали этот промокод!"

      # Лимит активаций проверяем атомарно
      cur = await db.execute(
          "UPDATE promocodes SET used_count = COALESCE(used_count, 0) + 1"
          " WHERE code = ? AND (COALESCE(max_uses, 0) = 0 OR"
          " COALESCE(used_count, 0) < max_uses)",
          (real_code,),
      )
      if cur.rowcount == 0:
        await db.rollback()
        return "❌ У этого промокода закончились активации."

      if reward_type == "tokens":
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE"
            " user_id = ?",
            (val, user_id),
        )
        reply = f"✅ Промокод активирован!\n🎁 Вам зачислено: <b>{val} токенов</b>"
      else:
        new_expire = await apply_premium(db, user_id, val)
        reply = (
            f"✅ Промокод активирован!\n👑 Премиум на <b>{val} дней</b> "
            f"(до {new_expire.strftime('%d.%m.%Y')})!"
        )
      await db.commit()
    return reply
  except Exception as e:
    logging.exception("Ошибка промокода")
    return f"❌ Ошибка обработки: {esc(e)}"


# --- СТАРТ, ПРОМОКОД, ОТМЕНА ---


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
  await state.clear()
  user_id = message.from_user.id

  args = (message.text or "").split()
  referrer_id = None
  if len(args) > 1 and args[1].isdigit():
    potential_ref = int(args[1])
    if potential_ref != user_id:
      referrer_id = potential_ref

  now_str = datetime.datetime.now().strftime(DT_FORMAT)
  async with connect() as db:
    async with db.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
      existing_user = await cursor.fetchone()

    if existing_user is None:
      # Реферером может быть только тот, кто уже есть в базе
      if referrer_id:
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (referrer_id,)
        ) as cursor:
          if await cursor.fetchone() is None:
            referrer_id = None
      await db.execute(
          "INSERT INTO users (user_id, tokens, y_tokens, full_tokens,"
          " is_premium, bought_folders, referrer_id, ref_activated,"
          " ref_joined_at) VALUES (?, 0, 0, 0, 0, 0, ?, 0, ?)",
          (user_id, referrer_id or 0, now_str),
      )
      await db.commit()

  if referrer_id and await is_subscribed(bot, user_id):
    await activate_referral(bot, user_id)

  await message.answer(
      "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре"
      " ниже:",
      reply_markup=get_main_keyboard(user_id),
  )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
  await state.clear()
  await message.answer(
      "Отменено.", reply_markup=get_main_keyboard(message.from_user.id)
  )


@router.message(Command("promocode"))
async def cmd_promocode(
    message: Message, command: CommandObject, state: FSMContext
):
  await state.clear()
  user_id = message.from_user.id
  code_text = (command.args or "").strip()
  if code_text:
    reply = await apply_promocode(user_id, code_text)
    await message.answer(reply, reply_markup=get_main_keyboard(user_id))
    return
  await message.answer("🎟 Введите промокод:")
  await state.set_state(PromocodeState.code)


@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, bot: Bot):
  if await is_subscribed(bot, callback.from_user.id):
    await activate_referral(bot, callback.from_user.id)
    try:
      await callback.message.delete()
    except Exception:
      pass
    await callback.message.answer(
        "✅ Спасибо за подписку! Выбирай раздел в меню.",
        reply_markup=get_main_keyboard(callback.from_user.id),
    )
    await callback.answer()
  else:
    await callback.answer("❌ Ты ещё не подписан на канал!", show_alert=True)


# --- АДМИН-КОМАНДЫ: ВЫДАЧА, БАНЫ, ПРЕДУПРЕЖДЕНИЯ ---


@router.message(Command("premium", "p"))
async def cmd_premium(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  args = [a for a in (command.args or "").split() if a.lower() != "add"]
  if len(args) != 2 or not all(a.isdigit() for a in args) or int(args[0]) <= 0:
    await message.answer(
        "ℹ️ Использование: <code>/p [дни] [ID]</code>\n"
        "Пример: <code>/p 30 5681420389</code>"
    )
    return
  days, target_id = int(args[0]), int(args[1])
  try:
    new_expire = await grant_premium(target_id, days)
  except Exception as e:
    logging.exception("Ошибка выдачи премиума командой")
    await message.answer(f"❌ Ошибка выдачи премиума: {esc(e)}")
    return
  formatted = new_expire.strftime("%d.%m.%Y")
  await message.answer(
      f"✅ <b>Премиум выдан!</b>\n🆔 <code>{target_id}</code>\n📅 {days} дн."
      f" (до {formatted})"
  )
  await safe_send(
      bot,
      target_id,
      f"🎉 <b>Вам активирована Премиум-подписка на {days} дней!</b>\n📅 До"
      f" <b>{formatted}</b>.\n\nТеперь любые фулы в магазине бесплатны!",
  )


@router.message(Command("token", "tokens", "t"))
async def cmd_token(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  args = [a for a in (command.args or "").split() if a.lower() != "add"]
  if len(args) != 2 or not all(a.isdigit() for a in args) or int(args[0]) <= 0:
    await message.answer(
        "ℹ️ Использование: <code>/t [сколько] [ID]</code>\n"
        "Пример: <code>/t 50 5681420389</code>"
    )
    return
  amount, target_id = int(args[0]), int(args[1])
  try:
    await grant_tokens(target_id, amount)
  except Exception as e:
    logging.exception("Ошибка выдачи токенов командой")
    await message.answer(f"❌ Ошибка выдачи токенов: {esc(e)}")
    return
  await message.answer(
      f"✅ Выдано <b>+{amount}</b> токенов пользователю"
      f" <code>{target_id}</code>."
  )
  await safe_send(
      bot,
      target_id,
      f"🎁 <b>Администратор начислил вам {amount} токенов!</b>",
  )


async def _special_token_cmd(
    message: Message, command: CommandObject, bot: Bot, column: str, label: str
):
  if await deny(message):
    return
  name = "ytoken" if column == "y_tokens" else "ftoken"
  parts = (command.args or "").split()
  if (
      len(parts) != 3
      or parts[0].lower() not in ("add", "del")
      or not parts[1].isdigit()
      or not parts[2].isdigit()
  ):
    await message.answer(
        f"ℹ️ Использование:\n<code>/{name} add [ID] [количество]</code>\n"
        f"<code>/{name} del [ID] [количество]</code>"
    )
    return
  action, target_id, amount = parts[0].lower(), int(parts[1]), int(parts[2])
  async with connect() as db:
    await ensure_user(db, target_id)
    if action == "add":
      await db.execute(
          f"UPDATE users SET {column} = COALESCE({column}, 0) + ? WHERE"
          " user_id = ?",
          (amount, target_id),
      )
    else:
      await db.execute(
          f"UPDATE users SET {column} = MAX(0, COALESCE({column}, 0) - ?)"
          " WHERE user_id = ?",
          (amount, target_id),
      )
    await db.commit()
  if action == "add":
    await message.answer(
        f"✅ <code>{target_id}</code>: начислено <b>+{amount}</b> {label}."
    )
    await safe_send(
        bot,
        target_id,
        f"🎁 Администратор начислил вам <b>+{amount}</b> {label}!",
    )
  else:
    await message.answer(
        f"✅ У <code>{target_id}</code> списано <b>{amount}</b> {label}."
    )


@router.message(Command("ytoken"))
async def cmd_ytoken(message: Message, command: CommandObject, bot: Bot):
  await _special_token_cmd(message, command, bot, "y_tokens", "уникальных токенов")


@router.message(Command("ftoken"))
async def cmd_ftoken(message: Message, command: CommandObject, bot: Bot):
  await _special_token_cmd(message, command, bot, "full_tokens", "фулловых токенов")


@router.message(Command("untoken"))
async def cmd_untoken(message: Message, command: CommandObject):
  if await deny(message):
    return
  args = parse_args(command, 2)
  if not args or args[1] <= 0:
    await message.answer(
        "ℹ️ Использование:\n<code>/untoken [ID] [число токенов]</code>"
    )
    return
  target_id, amount = args
  new_balance = await remove_tokens(target_id, amount)
  await message.answer(
      f"✅ У <code>{target_id}</code> забрано до <b>{amount}</b> токенов."
      f" Баланс: <b>{new_balance}</b>."
  )


@router.message(Command("unpremium"))
async def cmd_unpremium(message: Message, command: CommandObject):
  if await deny(message):
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer("ℹ️ Использование:\n<code>/unpremium [ID]</code>")
    return
  await remove_premium(args[0])
  await message.answer(f"✅ Премиум у <code>{args[0]}</code> снят.")


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  parts = (command.args or "").split()
  if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/ban [ID] [дни] [причина]</code>\n"
        "Пример: <code>/ban 5681420389 7 спам</code>\n"
        "<i>(0 дней = бан навсегда)</i>"
    )
    return
  target_id, days = int(parts[0]), int(parts[1])
  reason = " ".join(parts[2:]) or None
  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя забанить админа.")
    return
  term = await ban_user(target_id, days, reason)
  await message.answer(
      f"🚫 Пользователь <code>{target_id}</code> заблокирован {term}."
  )
  await safe_send(
      bot,
      target_id,
      f"🚫 Ваш профиль заблокирован {term}."
      + (f"\n📝 Причина: <b>{esc(reason)}</b>" if reason else "")
      + f"\n\n{APPEAL_TEXT}",
  )


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer("ℹ️ Использование:\n<code>/unban [ID]</code>")
    return
  await unban_user(args[0])
  await message.answer(f"✅ Пользователь <code>{args[0]}</code> разблокирован.")
  await safe_send(bot, args[0], "✅ Ваш профиль разблокирован.")


@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  parts = (command.args or "").split()
  if not parts or not parts[0].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/warn [ID] [причина]</code>"
    )
    return
  target_id = int(parts[0])
  reason = " ".join(parts[1:]) or None
  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя выдать предупреждение админу.")
    return
  warns, banned = await add_warn(target_id, reason)
  if banned:
    await message.answer(
        f"⚠️ <code>{target_id}</code> получил {warns}/{MAX_WARNS}"
        " предупреждений и заблокирован навсегда (премиум снят, токены"
        " обнулены)."
    )
  else:
    await message.answer(
        f"⚠️ <code>{target_id}</code>: выдано предупреждение"
        f" ({warns}/{MAX_WARNS})."
    )
  await send_warn_notice(bot, target_id, warns, banned)


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer("ℹ️ Использование:\n<code>/unwarn [ID]</code>")
    return
  warns = await remove_warn(args[0])
  await message.answer(
      f"✅ У <code>{args[0]}</code> снято предупреждение (осталось"
      f" {warns}/{MAX_WARNS})."
  )
  await safe_send(
      bot,
      args[0],
      f"✅ С вас снято предупреждение (осталось {warns}/{MAX_WARNS}).",
  )


@router.message(Command("aura"))
async def cmd_aura(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  parts = (command.args or "").split()

  if len(parts) == 2 and parts[0].lower() == "del" and parts[1].isdigit():
    target_id = int(parts[1])
    async with connect() as db:
      await db.execute(
          "UPDATE users SET aura_type = NULL, aura_name = NULL,"
          " aura_percent = 0, aura_expires = NULL WHERE user_id = ?",
          (target_id,),
      )
      await db.commit()
    await message.answer(
        f"✅ У пользователя <code>{target_id}</code> аннулирована аура."
    )
    return

  # /aura add [ID] [тип] [%] [срок] [Название]
  # процент может быть отрицательным: -50
  # срок: число дней (30) или число часов с суффиксом h (12h)
  if len(parts) >= 6 and parts[0].lower() == "add" and parts[1].isdigit():
    target_id = int(parts[1])
    aura_type = parts[2].lower()
    if aura_type not in ("lottery", "roulette", "shop"):
      await message.answer(
          "❌ Неверный тип ауры! Доступно: <code>lottery</code>,"
          " <code>roulette</code>, <code>shop</code>"
      )
      return

    # процент: допускаем -число и +число
    raw_pct = parts[3].lstrip("+")
    try:
      percent = int(raw_pct)
    except ValueError:
      await message.answer("❌ Процент должен быть числом (можно отрицательным).")
      return

    # срок: «12h» — часы, «30» — дни
    raw_dur = parts[4]
    if raw_dur.lower().endswith("h"):
      num = raw_dur[:-1]
      if not num.isdigit() or int(num) <= 0:
        await message.answer("❌ Укажите срок: число дней (30) или часов (12h).")
        return
      delta = datetime.timedelta(hours=int(num))
      dur_text = f"{int(num)} ч."
    else:
      if not raw_dur.isdigit() or int(raw_dur) <= 0:
        await message.answer("❌ Укажите срок: число дней (30) или часов (12h).")
        return
      delta = datetime.timedelta(days=int(raw_dur))
      dur_text = f"{int(raw_dur)} дн."

    aura_name = " ".join(parts[5:])
    if not aura_name:
      await message.answer("❌ Укажите название ауры.")
      return

    expires_at = datetime.datetime.now() + delta
    async with connect() as db:
      await ensure_user(db, target_id)
      await db.execute(
          "UPDATE users SET aura_type = ?, aura_name = ?, aura_percent = ?,"
          " aura_expires = ? WHERE user_id = ?",
          (aura_type, aura_name, percent, expires_at.strftime(DT_FORMAT), target_id),
      )
      await db.commit()

    sign = "+" if percent >= 0 else ""
    await message.answer(
        "✨ <b>Аура выдана!</b>\n\n"
        f"🆔 <code>{target_id}</code>\n"
        f"🏷 Название: <b>{esc(aura_name)}</b>\n"
        f"⚙️ Тип: <code>{aura_type}</code>\n"
        f"📊 Эффект: <b>{sign}{percent}%</b>\n"
        f"⏱ Срок: <b>{dur_text}</b> (до {expires_at.strftime('%d.%m.%Y %H:%M')})"
    )
    await safe_send(
        bot,
        target_id,
        f"✨ Вам выдана аура <b>{esc(aura_name)}</b>"
        f" ({sign}{percent}%) на {dur_text}!",
    )
    return

  await message.answer(
      "ℹ️ <b>Управление аурами:</b>\n\n"
      "Выдать:\n"
      "<code>/aura add [ID] [lottery/roulette/shop] [%] [срок] [Название]</code>\n"
      "Срок: число дней (<code>30</code>) или часов (<code>12h</code>)\n"
      "Процент может быть отрицательным: <code>-50</code>\n\n"
      "Примеры:\n"
      "<code>/aura add 123 shop -50 30 Скидочник</code> — скидка 50% на 30 дней\n"
      "<code>/aura add 123 roulette -100 12h Невезение</code> — все лузы на 12 ч\n"
      "<code>/aura add 123 lottery 20 7 Удачник</code> — +20% шанс на 7 дней\n\n"
      "Удалить:\n<code>/aura del [ID]</code>"
  )


@router.message(Command("all"))
async def cmd_all(message: Message, command: CommandObject, bot: Bot):
  if await deny(message):
    return
  parts = (command.args or "").split()
  if (
      len(parts) != 2
      or parts[0].lower() not in ("token", "tokens")
      or not parts[1].isdigit()
      or int(parts[1]) <= 0
  ):
    await message.answer(
        "ℹ️ Использование:\n<code>/all token [сколько]</code>\n"
        "Пример: <code>/all token 5</code> — выдать всем по 5 токенов."
    )
    return
  amount = int(parts[1])
  now_str = datetime.datetime.now().strftime(DT_FORMAT)
  async with connect() as db:
    cur = await db.execute(
        "UPDATE users SET tokens = COALESCE(tokens, 0) + ?"
        " WHERE banned_until IS NULL OR banned_until <= ?",
        (amount, now_str),
    )
    await db.commit()
    count = cur.rowcount
    async with db.execute(
        "SELECT user_id FROM users WHERE banned_until IS NULL"
        " OR banned_until <= ?",
        (now_str,),
    ) as cur2:
      user_ids = [row[0] for row in await cur2.fetchall()]

  await message.answer(
      f"✅ Выдано по <b>{amount}</b> токенов: <b>{count}</b> чел."
      " Рассылаю уведомления…"
  )

  async def _notify_all():
    for uid in user_ids:
      await safe_send(
          bot, uid, f"🎁 Администратор выдал всем по <b>{amount}</b> токенов!"
      )
      await asyncio.sleep(0.05)

  task = asyncio.create_task(_notify_all())
  _bg_tasks.add(task)
  task.add_done_callback(_bg_tasks.discard)


# --- РЕФЕРАЛЫ: /uref и /check ---

REF_TOP_SQL = """
    SELECT user_id, is_premium, ref_count FROM (
        SELECT u.user_id, u.is_premium,
               (SELECT COUNT(*) FROM users r
                 WHERE r.referrer_id = u.user_id AND r.ref_activated = 1)
               + COALESCE(u.ref_extra, 0) AS ref_count
        FROM users u
    )
    WHERE ref_count > 0
    ORDER BY ref_count DESC
    LIMIT ?
"""


async def ref_summary(db, uid: int):
  """(подписавшиеся, не подписавшиеся, добавлено админом, общий доход)."""
  async with db.execute(
      "SELECT COALESCE(SUM(ref_activated = 1), 0),"
      " COALESCE(SUM(ref_activated = 0), 0), COALESCE(SUM(ref_income), 0)"
      " FROM users WHERE referrer_id = ?",
      (uid,),
  ) as c:
    row = await c.fetchone()
  active, pending, income = row[0], row[1], row[2]
  async with db.execute(
      "SELECT COALESCE(ref_extra, 0) FROM users WHERE user_id = ?", (uid,)
  ) as c:
    row = await c.fetchone()
  extra = row[0] if row else 0
  return active, pending, extra, income + extra * REF_BONUS


async def uref_add(target_id: int, count: int) -> None:
  async with connect() as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET ref_extra = COALESCE(ref_extra, 0) + ?,"
        " tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
        (count, count * REF_BONUS, target_id),
    )
    await db.commit()


async def uref_remove(target_id: int, count):
  """Убирает count рефералов (None = всех) и забирает их доход.
  Возвращает (убрано, забрано токенов, баланс)."""
  removed, taken = 0, 0
  async with connect() as db:
    await ensure_user(db, target_id)
    async with db.execute(
        "SELECT COALESCE(ref_extra, 0) FROM users WHERE user_id = ?",
        (target_id,),
    ) as c:
      row = await c.fetchone()
    extra = row[0] if row else 0

    # сначала снимаем рефералов, добавленных админом
    n_extra = extra if count is None else min(count, extra)
    if n_extra:
      await db.execute(
          "UPDATE users SET ref_extra = ref_extra - ? WHERE user_id = ?",
          (n_extra, target_id),
      )
      removed += n_extra
      taken += n_extra * REF_BONUS

    # потом настоящих
    left = None if count is None else count - n_extra
    if left is None or left > 0:
      where = "referrer_id = ?" + (
          "" if left is None else " AND ref_activated = 1"
      )
      sql = (
          "SELECT user_id, ref_activated, COALESCE(ref_income, 0) FROM users"
          f" WHERE {where} ORDER BY user_id DESC"
      )
      args = [target_id]
      if left is not None:
        sql += " LIMIT ?"
        args.append(left)
      async with db.execute(sql, args) as c:
        rows = await c.fetchall()
      for rid, activated, income in rows:
        if activated:
          removed += 1
          taken += income if income > 0 else REF_BONUS
        await db.execute(
            "UPDATE users SET referrer_id = 0, ref_income = 0 WHERE"
            " user_id = ?",
            (rid,),
        )

    if taken:
      await db.execute(
          "UPDATE users SET tokens = MAX(0, COALESCE(tokens, 0) - ?)"
          " WHERE user_id = ?",
          (taken, target_id),
      )
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (target_id,)
    ) as c:
      balance = (await c.fetchone())[0]
    await db.commit()
  return removed, taken, balance


@router.message(Command("uref"))
async def cmd_uref(message: Message, command: CommandObject):
  if await deny(message):
    return
  usage = (
      "ℹ️ Рефералы:\n"
      "<code>/uref [ID]</code> — список рефералов и доход\n"
      f"<code>/uref add [ID] [число]</code> — добавить (+{REF_BONUS} токенов"
      " за каждого)\n"
      "<code>/uref del [ID] [число]</code> — убрать N рефералов и забрать"
      " их доход\n"
      "<code>/uref del [ID] all</code> — убрать всех"
  )
  parts = (command.args or "").split()

  if len(parts) == 1 and parts[0].isdigit():
    target_id = int(parts[0])
    async with connect() as db:
      active, pending, extra, income = await ref_summary(db, target_id)
      async with db.execute(
          "SELECT user_id, ref_activated, COALESCE(ref_income, 0),"
          " ref_joined_at FROM users WHERE referrer_id = ? ORDER BY"
          " ref_activated DESC, user_id DESC LIMIT 20",
          (target_id,),
      ) as c:
        rows = await c.fetchall()
    lines = []
    for rid, act, inc, joined_at in rows:
      time_str = f" [{joined_at}]" if joined_at else ""
      status = "✅" if act else "⏳ не подписан"
      lines.append(f"• <code>{rid}</code> — {status}{time_str}, принёс {inc}")
    await message.answer(
        f"👥 <b>Рефералы ID</b> <code>{target_id}</code>\n"
        f"✅ Засчитано: <b>{active + extra}</b> (из них админом: {extra})\n"
        f"⏳ Не подписались: <b>{pending}</b>\n"
        f"💰 Доход: <b>{income}</b> токенов\n\n" + ("\n".join(lines) or "— нет —")
    )
    return

  if (
      len(parts) == 3
      and parts[0].lower() in ("add", "del")
      and parts[1].isdigit()
  ):
    action, target_id, raw = parts[0].lower(), int(parts[1]), parts[2].lower()
    if action == "add":
      if not raw.isdigit() or int(raw) <= 0:
        await message.answer(usage)
        return
      count = int(raw)
      await uref_add(target_id, count)
      await message.answer(
          f"✅ ID <code>{target_id}</code>: добавлено рефералов:"
          f" <b>{count}</b>, начислено <b>{count * REF_BONUS}</b> токенов."
      )
      return
    if raw == "all":
      count = None
    elif raw.isdigit() and int(raw) > 0:
      count = int(raw)
    else:
      await message.answer(usage)
      return
    removed, taken, balance = await uref_remove(target_id, count)
    await message.answer(
        f"✅ ID <code>{target_id}</code>: убрано рефералов:"
        f" <b>{removed}</b>, списано дохода: <b>{taken}</b> токенов.\n"
        f"Баланс сейчас: <b>{balance}</b>."
    )
    return

  await message.answer(usage)


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject):
  if await deny(message):
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer("ℹ️ Использование:\n<code>/check [ID]</code>")
    return
  target_id = args[0]
  now = datetime.datetime.now()

  async with connect() as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT * FROM users WHERE user_id = ?", (target_id,)
    ) as c:
      u = await c.fetchone()
    if not u:
      await message.answer("❌ Пользователь не найден в базе.")
      return
    active, pending, extra, income = await ref_summary(db, target_id)
    async with db.execute(
        "SELECT COUNT(*), COALESCE(SUM(COALESCE(paid, 0)), 0)"
        " FROM lottery_participants WHERE user_id = ?",
        (target_id,),
    ) as c:
      lot = await c.fetchone()
    async with db.execute(
        "SELECT COALESCE(games, 0), COALESCE(wins, 0) FROM roulette_state"
        " WHERE user_id = ?",
        (target_id,),
    ) as c:
      rl = await c.fetchone()
    async with db.execute(
        "SELECT COUNT(*) FROM ex_offers WHERE owner_id = ? AND status = 'open'",
        (target_id,),
    ) as c:
      open_offers = (await c.fetchone())[0]
  lot_n, lot_paid = lot[0], lot[1]
  rl_games, rl_wins = (rl[0], rl[1]) if rl else (0, 0)

  prem_exp = parse_dt(u["premium_expires"])
  if premium_active(u["is_premium"], u["premium_expires"]):
    prem = (
        f"✅ до {prem_exp.strftime('%d.%m.%Y %H:%M')}" if prem_exp else "✅ активна"
    )
  else:
    prem = "❌ нет"

  aura = await get_user_aura(target_id)
  aura_info = (
      f"✨ {esc(aura['name'])} ({aura['type']}, {aura['percent']}%) до"
      f" {aura['expires'].strftime('%d.%m.%Y %H:%M')}"
      if aura
      else "❌ нет"
  )

  def until_text(value):
    dt = parse_dt(value)
    if not dt or dt <= now:
      return "нет"
    if dt.year >= 2900:
      return "🚫 навсегда"
    return f"🚫 до {dt.strftime('%d.%m.%Y %H:%M')}"

  if u["referrer_id"]:
    invited = f"<code>{u['referrer_id']}</code> " + (
        "(подписался ✅)" if u["ref_activated"] else "(ещё не подписался ⏳)"
    )
  else:
    invited = "никто / сброшено"

  await message.answer(
      f"🔎 <b>Аккаунт</b> <code>{target_id}</code>\n\n"
      f"🪙 Токены: <b>{u['tokens'] or 0}</b>\n"
      f"💎 Уникальные токены: <b>{u['y_tokens'] or 0}</b>\n"
      f"📦 Фулловые токены: <b>{u['full_tokens'] or 0}</b>\n"
      f"👑 Премиум: {prem}\n"
      f"✨ Аура: {aura_info}\n"
      f"📦 Куплено фулов: {u['bought_folders'] or 0}\n"
      f"⚠️ Предупреждения: {u['warns'] or 0}/{MAX_WARNS}\n"
      f"🚫 Бан: {until_text(u['banned_until'])}\n"
      f"🔒 Ограничение обменов: {until_text(u['restricted_until'])}\n\n"
      "👥 <b>Рефералы</b>\n"
      f"• Засчитано: <b>{active + extra}</b> (добавлено админом: {extra})\n"
      f"• Не подписались: {pending}\n"
      f"• Доход с рефералов: <b>{income}</b> токенов\n"
      f"• Пригласил его: {invited}\n\n"
      "🔄 <b>Обмены</b>\n"
      f"• Проведено: <b>{u['exchanges'] or 0}</b>\n"
      f"• Открытых сейчас: {open_offers}\n\n"
      "🎰 <b>Лотерея</b>\n"
      f"• Участий: <b>{lot_n}</b>, заплачено всего: {lot_paid} токенов\n"
      f"• Побед за период: {u['lottery_wins'] or 0}\n\n"
      "🎲 <b>Рулетка</b>\n"
      f"• Игр: <b>{rl_games}</b> (побед: {rl_wins}, поражений:"
      f" {rl_games - rl_wins})"
  )


@router.message(F.document, F.caption.startswith("/restore"), F.from_user.id == ADMIN_ID)
async def cmd_restore(message: Message, bot: Bot):
  """Админ шлёт файл bot.db с подписью /restore, и база заменяется."""
  tmp = DB_NAME + ".restore"
  try:
    await bot.download(message.document, destination=tmp)
    with open(tmp, "rb") as f:
      is_sqlite = f.read(16).startswith(b"SQLite format 3")
    if not is_sqlite:
      os.remove(tmp)
      await message.answer("❌ Это не файл базы SQLite.")
      return
    os.replace(tmp, DB_NAME)
    await init_db()  # докатываем недостающие колонки
  except Exception as e:
    logging.exception("Ошибка восстановления БД")
    await message.answer(f"❌ Не удалось восстановить: {esc(e)}")
    return
  await message.answer("✅ База восстановлена из файла.")


# --- ТОПЫ ---


def tops_menu_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="👥 Топ по рефералам", callback_data="top_refs")],
          [InlineKeyboardButton(text="🎰 Топ по лотерее", callback_data="top_lottery")],
          [InlineKeyboardButton(text="🎲 Топ по рулетке", callback_data="top_roulette")],
      ]
  )


async def get_next_reset(db) -> datetime.datetime:
  async with db.execute("SELECT next_reset FROM top_reset WHERE id = 1") as cur:
    row = await cur.fetchone()
  nxt = parse_dt(row[0]) if row and row[0] else None
  if nxt is None:
    nxt = datetime.datetime.now() + datetime.timedelta(days=TOP_RESET_DAYS)
    await db.execute(
        "INSERT OR REPLACE INTO top_reset (id, next_reset) VALUES (1, ?)",
        (nxt.strftime(DT_FORMAT),),
    )
    await db.commit()
  return nxt


@router.callback_query(F.data.startswith("top_reward_"))
async def process_top_reward(callback: CallbackQuery):
  parts = callback.data.split("_")
  try:
    winner_id = int(parts[2])
    reward_type = parts[3]
  except (IndexError, ValueError):
    await callback.answer()
    return
  if callback.from_user.id != winner_id:
    await callback.answer("❌ Это меню не для вас!", show_alert=True)
    return

  # Сначала убираем кнопки: повторное нажатие уже не сработает
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    await callback.answer("Награда уже получена.", show_alert=True)
    return

  if reward_type == "prem":
    await grant_premium(winner_id, TOP_REWARD_PREMIUM_DAYS)
    text = (
        f"✅ Вы получили <b>Премиум-подписку на {TOP_REWARD_PREMIUM_DAYS}"
        " дней</b>!"
    )
  else:
    await grant_y_tokens(winner_id, TOP_REWARD_YTOKENS)
    text = f"✅ Вы получили <b>{TOP_REWARD_YTOKENS} уникальных токена</b>!"
  try:
    await callback.message.edit_text(text, reply_markup=None)
  except Exception:
    pass
  await callback.answer()


@router.callback_query(F.data.in_({"top_refs", "top_lottery", "top_roulette"}))
async def show_specific_top(callback: CallbackQuery):
  top_type = callback.data.split("_")[1]
  async with connect() as db:
    next_reset = await get_next_reset(db)

    if top_type == "refs":
      async with db.execute(REF_TOP_SQL, (10,)) as cursor:
        rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по рефералам</b>"
      unit = "реф."
    elif top_type == "lottery":
      async with db.execute(
          "SELECT user_id, is_premium, COALESCE(lottery_wins, 0) AS lw FROM"
          " users WHERE lw > 0 ORDER BY lw DESC LIMIT 10"
      ) as cursor:
        rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по победам в лотерее</b>"
      unit = "побед"
    else:
      async with db.execute(
          "SELECT u.user_id, u.is_premium, COALESCE(r.wins, 0) AS w FROM users"
          " u JOIN roulette_state r ON u.user_id = r.user_id WHERE w > 0"
          " ORDER BY w DESC LIMIT 10"
      ) as cursor:
        rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по победам в рулетке</b>"
      unit = "побед"

  items = [
      f"{i}. Пользователь <code>{r[0]}</code>{' 👑' if r[1] == 1 else ''} —"
      f" <b>{r[2]}</b> {unit}"
      for i, r in enumerate(rows, 1)
  ]

  delta = next_reset - datetime.datetime.now()
  if delta.total_seconds() > 0:
    timer_text = f"через {delta.days} дн. {delta.seconds // 3600} ч."
  else:
    timer_text = "скоро сброс..."

  footer = (
      f"⏳ <b>Сброс топа:</b> {timer_text}\n"
      "🎁 <b>Награда 1 места (на выбор):</b> Премиум на"
      f" {TOP_REWARD_PREMIUM_DAYS} дней <u>ИЛИ</u> {TOP_REWARD_YTOKENS}"
      " уникальных токена!"
  )
  text = title + "\n\n" + ("\n".join(items) if items else "Пока пусто.") + f"\n\n{footer}"
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="⬅️ Назад к выбору топов", callback_data="back_to_tops"
              )
          ]
      ]
  )
  try:
    await callback.message.edit_text(text, reply_markup=kb)
  except Exception:
    pass
  await callback.answer()


@router.callback_query(F.data == "back_to_tops")
async def back_to_tops(callback: CallbackQuery):
  try:
    await callback.message.edit_text(
        "🏆 <b>Выберите категорию топа:</b>", reply_markup=tops_menu_keyboard()
    )
  except Exception:
    pass
  await callback.answer()


# --- КАК ПОЛУЧИТЬ ТОКЕНЫ ---

HOWTO_TEXTS = {
    "howto_tokens": (
        "🪙 <b>Как получить обычные токены?</b>\n\n"
        "1. Возьмите фото: https://t.me/JmcDeveloper/11\n\n"
        "2. Оставляйте его в комментариях под постами/ответами другим людям"
        " <u>(под тему сестра и брата)</u>.\n\n"
        "3. Отправляйте доказательства (скриншоты) в директ канала:"
        " https://t.me/JmcDeveloper?direct\n\n"
        "💰 <b>1 отправка фото в комментариях = 1 токен!</b>\n\n"
        "📖 Детально расписал тут: https://t.me/JmcDeveloper/18\n"
        "<i>(не выдам токены если не посмотрите 👆)</i>"
    ),
    "howto_ytokens": (
        "💎 <b>Как получить уникальные токены?</b>\n\n"
        "Их можно только купить: <b>15 звёзд = 1 уникальный токен.</b>\n\n"
        "<b>Как купить?</b> Кинь любой подарок стоимостью 15 звёзд на аккаунт"
        " @ucu52.\n\n"
        "<b>Преимущества:</b>\n"
        "• Там выходят фулы на 1 день позже чем в стандартный магазин\n"
        "• Уникальные товары: аура, фулловые токены и другое"
    ),
    "howto_ftokens": (
        "📦 <b>Как получить фулловые токены?</b>\n\n"
        "Их можно купить в <b>💎 Уникальном магазине</b> за уникальные токены"
        " (или получить от администратора).\n\n"
        "<b>Преимущество:</b>\n"
        "Позволяет забрать абсолютно любой фулл в обычном магазине <b>бесплатно</b>"
        " — просто используй фулловый токен при покупке!"
    ),
}


@router.callback_query(F.data.in_({"howto_tokens", "howto_ytokens", "howto_ftokens"}))
async def howto_cb(callback: CallbackQuery):
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="⬅️ Назад", callback_data="howto_back")]
      ]
  )
  await callback.message.edit_text(
      HOWTO_TEXTS[callback.data],
      reply_markup=kb,
      disable_web_page_preview=True,
  )
  await callback.answer()


@router.callback_query(F.data == "howto_back")
async def howto_back(callback: CallbackQuery):
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🪙 Обычные токены", callback_data="howto_tokens")],
          [InlineKeyboardButton(text="💎 Уникальные токены", callback_data="howto_ytokens")],
          [InlineKeyboardButton(text="📦 Фулловые токены", callback_data="howto_ftokens")],
      ]
  )
  await callback.message.edit_text(
      "🪙 <b>Как получить токены?</b>\nВыбери тип токена:", reply_markup=kb
  )
  await callback.answer()


# --- ЛОТЕРЕЯ ---


def roll_lottery_params():
  """(приз, цена_фиксирована). Ночью 1:00-8:00 (Киев): приз 4, участие 1."""
  hour = now_local().hour
  if 1 <= hour < 8:
    return 4, 1
  r = random.random()
  if r < LOTTERY_NORMAL_CHANCE:
    return random.randint(4, 10), 0
  if r < LOTTERY_NORMAL_CHANCE + LOTTERY_BIG_LOW_CHANCE:
    return random.randint(11, 14), 0
  return random.randint(15, 20), 0


async def notify_big_lottery(bot: Bot, cost: int, prize: int) -> None:
  text = (
      "🔥 <b>Началась крупная лотерея!</b>\n\n"
      f"🏆 Приз: <b>от {prize} токенов</b> (+ взносы участников)\n"
      f"🎟 Участие: <b>{cost_text(cost)}</b>\n"
      f"👥 Минимум участников: <b>{lottery_min_players(prize)}</b>\n"
      f"⏱ Длится {LOTTERY_HOURS} ч."
  )
  async with connect() as db:
    async with db.execute(
        "SELECT user_id, banned_until, COALESCE(notify_lottery, 1) FROM users"
    ) as cur:
      rows = await cur.fetchall()
  now = datetime.datetime.now()
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🎰 Участвовать", callback_data="lottery_open")],
          [
              InlineKeyboardButton(
                  text="🔕 Отключить уведомления",
                  callback_data="lottery_toggle_notify",
              )
          ],
      ]
  )
  for uid, banned_until, notify in rows:
    if notify == 0:
      continue
    until = parse_dt(banned_until)
    if until and until > now:
      continue
    await safe_send(bot, uid, text, reply_markup=kb)
    await asyncio.sleep(0.05)


async def create_lottery_round(bot: Bot = None) -> None:
  prize, fixed = roll_lottery_params()
  cost = 1 if fixed else lottery_cost_for_total(prize)
  ends_at = datetime.datetime.now() + datetime.timedelta(hours=LOTTERY_HOURS)
  async with connect() as db:
    await db.execute(
        "INSERT INTO lottery_rounds (entry_cost, prize, ends_at, finished,"
        " cost_fixed) VALUES (?, ?, ?, 0, ?)",
        (cost, prize, ends_at.strftime(DT_FORMAT), fixed),
    )
    await db.commit()
  # Уведомляем по СУММАРНОМУ призу — проверяем после каждого нового участника тоже
  if bot is not None and prize >= LOTTERY_NOTIFY_PRIZE:
    task = asyncio.create_task(notify_big_lottery(bot, cost, prize))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def get_current_round():
  """(id, entry_cost, prize, ends_at, cost_fixed) или None."""
  async with connect() as db:
    async with db.execute(
        "SELECT id, entry_cost, prize, ends_at, COALESCE(cost_fixed, 0)"
        " FROM lottery_rounds WHERE finished = 0 ORDER BY id DESC LIMIT 1"
    ) as cur:
      return await cur.fetchone()


async def lottery_stats(db, rnd) -> dict:
  round_id, entry_cost, prize, _, fixed = rnd
  async with db.execute(
      "SELECT COUNT(*), COALESCE(SUM((COALESCE(paid, ?) + 1) / 2), 0)"
      " FROM lottery_participants WHERE round_id = ?",
      (entry_cost, round_id),
  ) as cur:
    players, pool = await cur.fetchone()
  total = prize + pool
  cost = entry_cost if fixed else lottery_cost_for_total(total)
  return {"players": players, "pool": pool, "total": total, "cost": cost}


def lottery_again_keyboard(user_notify: int = 1) -> InlineKeyboardMarkup:
  toggle_text = (
      "🔕 Отключить уведомления"
      if user_notify == 1
      else "🔔 Включить уведомления"
  )
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🎰 Попытать удачу ещё раз", callback_data="lottery_open"
              )
          ],
          [
              InlineKeyboardButton(
                  text=toggle_text, callback_data="lottery_toggle_notify"
              )
          ],
      ]
  )


async def build_lottery_view(user_id: int):
  """Возвращает (текст, клавиатура) для экрана лотереи."""
  rnd = await get_current_round()
  if not rnd:
    return "⏳ Новый розыгрыш скоро начнётся. Загляни чуть позже!", None

  round_id, _, prize, ends_at_str, fixed = rnd
  ends_at = parse_dt(ends_at_str) or datetime.datetime.now()
  left_min = max(
      0, int((ends_at - datetime.datetime.now()).total_seconds() // 60)
  )
  hours, minutes = divmod(left_min, 60)

  async with connect() as db:
    st = await lottery_stats(db, rnd)
    async with db.execute(
        "SELECT 1 FROM lottery_participants WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ) as cur:
      joined = await cur.fetchone() is not None
    async with db.execute(
        "SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      urow = await cur.fetchone()
      user_notify = urow[0] if urow else 1
  cost = st["cost"]

  text = (
      "🎰 <b>Лотерея</b>\n\n"
      f"🏆 Приз: <b>{st['total']} токенов</b>"
      f" ({prize} + {st['pool']} от взносов)\n"
      f"🎟 Участие: <b>{cost_text(cost)}</b>"
      + ("" if fixed else " (растёт вместе с призом)")
      + "\n"
      f"👥 Участников: <b>{st['players']}</b> (минимум для розыгрыша:"
      f" {lottery_min_players(prize)})\n"
      f"⏱ До конца розыгрыша: <b>{hours} ч {minutes} мин</b>\n\n"
      "Победитель выбирается случайно среди всех участников. "
      + (
          "Половина токенов за участие добавляется к призу."
          if cost
          else "Участие бесплатное."
      )
  )

  toggle_text = (
      "🔕 Отключить уведомления"
      if user_notify == 1
      else "🔔 Включить уведомления"
  )
  toggle_row = [
      InlineKeyboardButton(text=toggle_text, callback_data="lottery_toggle_notify")
  ]

  if joined:
    return (
        text + "\n\n✅ Ты уже участвуешь в этом розыгрыше!",
        InlineKeyboardMarkup(inline_keyboard=[toggle_row]),
    )

  join_row = [
      InlineKeyboardButton(
          text=(
              "🎟 Участвовать (бесплатно)"
              if cost == 0
              else f"🎟 Участвовать ({cost} токен.)"
          ),
          callback_data=f"lottery_join_{round_id}_{cost}",
      )
  ]
  return text, InlineKeyboardMarkup(inline_keyboard=[join_row, toggle_row])


async def finish_lottery_round(bot: Bot, round_id: int, prize: int) -> None:
  async with connect() as db:
    # Сначала помечаем раунд завершённым, чтобы не выдать приз дважды
    await db.execute(
        "UPDATE lottery_rounds SET finished = 1 WHERE id = ?", (round_id,)
    )
    await db.commit()
    async with db.execute(
        "SELECT entry_cost FROM lottery_rounds WHERE id = ?", (round_id,)
    ) as cur:
      row = await cur.fetchone()
    cost = row[0] if row and row[0] else 0

    async with db.execute(
        "SELECT user_id, paid FROM lottery_participants WHERE round_id = ?",
        (round_id,),
    ) as cur:
      part_rows = await cur.fetchall()
    players = [r[0] for r in part_rows]
    paid_map = {r[0]: (r[1] if r[1] is not None else cost) for r in part_rows}

    notify_map = {}
    for uid in players:
      async with db.execute(
          "SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?",
          (uid,),
      ) as c:
        urow = await c.fetchone()
      notify_map[uid] = urow[0] if urow else 1

    min_players = lottery_min_players(prize)
    if players and len(players) < min_players:
      # Мало участников: приз не разыгрывается, токены за участие возвращаем
      for uid in players:
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
            (paid_map[uid], uid),
        )
      await db.commit()
      for uid in players:
        await safe_send(
            bot,
            uid,
            "😔 В розыгрыше не набралось нужное число участников"
            f" ({len(players)} из {min_players}). Приз не разыгран."
            + (
                f" Тебе вернули <b>{paid_map[uid]} токен(ов)</b>."
                if paid_map[uid]
                else ""
            )
            + "\n\nХотите ещё попытать удачу?",
            reply_markup=lottery_again_keyboard(notify_map[uid]),
        )
        await asyncio.sleep(0.05)
      return

  # Половина каждого взноса участника добавляется к призу
  prize = prize + sum(fee_to_prize(v) for v in paid_map.values())

  # Шансы: аура lottery увеличивает вес участника
  weights = []
  for uid in players:
    weight = 1.0
    aura = await get_user_aura(uid)
    if aura and aura["type"] == "lottery":
      weight += aura["percent"] / 100.0
    weights.append(weight)

  winner = random.choices(players, weights=weights, k=1)[0] if players else None
  if winner is not None:
    async with connect() as db:
      await db.execute(
          "UPDATE users SET tokens = COALESCE(tokens, 0) + ?,"
          " lottery_wins = COALESCE(lottery_wins, 0) + 1 WHERE user_id = ?",
          (prize, winner),
      )
      await db.commit()

  for uid in players:
    if uid == winner:
      text = (
          "🎉 <b>Ты выиграл в лотерее!</b>\n🏆 Тебе начислено:"
          f" <b>{prize} токенов</b>.\n\nХотите ещё попытать удачу?"
      )
    else:
      text = (
          "😔 В этот раз удача была не на твоей стороне. Победитель уже"
          " получил приз.\n\nХотите ещё попытать удачу?"
      )
    await safe_send(
        bot, uid, text, reply_markup=lottery_again_keyboard(notify_map[uid])
    )
    await asyncio.sleep(0.05)


async def lottery_loop(bot: Bot):
  while True:
    try:
      rnd = await get_current_round()
      if rnd is None:
        await create_lottery_round(bot)
        rnd = await get_current_round()

      round_id, _cost, prize, ends_at_str, _fixed = rnd
      ends_at = parse_dt(ends_at_str) or datetime.datetime.now()
      delay = (ends_at - datetime.datetime.now()).total_seconds()
      if delay > 0:
        # спим короткими кусками: админ может изменить время тиража
        await asyncio.sleep(min(delay, 15))
        continue
      await finish_lottery_round(bot, round_id, prize)
    except Exception:
      logging.exception("Ошибка в цикле лотереи")
      await asyncio.sleep(30)


@router.message(Command("lottery"))
async def cmd_lottery(message: Message, command: CommandObject):
  if await deny(message):
    return

  usage = (
      "ℹ️ Управление текущей лотереей:\n"
      "<code>/lottery prize [токены]</code> — главный приз (можно 0)\n"
      "<code>/lottery cost [токены]</code> — цена участия, фиксированная"
      " (можно 0)\n"
      "<code>/lottery cost auto</code> — вернуть автоцену\n"
      "<code>/lottery time [часы]</code> — сколько часов до конца, считая с"
      " сейчас (можно <code>30m</code> — минуты)\n"
      "<code>/lottery</code> — показать текущие настройки\n\n"
      "<i>Участники не сбрасываются. Изменения действуют на текущий тираж.</i>"
  )

  rnd = await get_current_round()
  if not rnd:
    await message.answer("⏳ Сейчас нет активного тиража. Подожди секунду.")
    return
  round_id = rnd[0]

  parts = (command.args or "").split()
  if parts:
    if len(parts) != 2:
      await message.answer(usage)
      return
    field, raw = parts[0].lower(), parts[1].lower()
    async with connect() as db:
      if field == "prize":
        if not raw.isdigit():
          await message.answer(usage)
          return
        await db.execute(
            "UPDATE lottery_rounds SET prize = ? WHERE id = ? AND finished = 0",
            (int(raw), round_id),
        )
      elif field == "cost":
        if raw == "auto":
          await db.execute(
              "UPDATE lottery_rounds SET cost_fixed = 0 WHERE id = ? AND"
              " finished = 0",
              (round_id,),
          )
        elif raw.isdigit():
          await db.execute(
              "UPDATE lottery_rounds SET entry_cost = ?, cost_fixed = 1"
              " WHERE id = ? AND finished = 0",
              (int(raw), round_id),
          )
        else:
          await message.answer(usage)
          return
      elif field == "time":
        unit_minutes = raw.endswith("m")
        num = raw[:-1] if (unit_minutes or raw.endswith("h")) else raw
        if not num.isdigit() or int(num) <= 0:
          await message.answer(usage)
          return
        minutes = int(num) if unit_minutes else int(num) * 60
        minutes = min(minutes, 24 * 60 * 14)
        new_end = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
        await db.execute(
            "UPDATE lottery_rounds SET ends_at = ? WHERE id = ? AND finished = 0",
            (new_end.strftime(DT_FORMAT), round_id),
        )
      else:
        await message.answer(usage)
        return
      await db.commit()
    rnd = await get_current_round()
    if not rnd:
      await message.answer("⏳ Тираж как раз закончился.")
      return

  round_id, _, prize, ends_at_str, fixed = rnd
  ends_at = parse_dt(ends_at_str) or datetime.datetime.now()
  left_min = max(
      0, int((ends_at - datetime.datetime.now()).total_seconds() // 60)
  )
  async with connect() as db:
    st = await lottery_stats(db, rnd)
  await message.answer(
      ("✅ Готово.\n\n" if parts else "")
      + "🎰 <b>Текущая лотерея</b>\n"
      f"🏆 Главный приз: <b>{prize}</b> токенов (+ {st['pool']} от взносов ="
      f" {st['total']})\n"
      f"🎟 Участие: <b>{cost_text(st['cost'])}</b>"
      f" ({'фиксированная' if fixed else 'авто'})\n"
      f"👥 Участников: <b>{st['players']}</b> (минимум:"
      f" {lottery_min_players(prize)})\n"
      f"⏱ До конца: <b>{left_min // 60} ч {left_min % 60} мин</b>"
      + ("" if parts else "\n\n" + usage)
  )


@router.callback_query(F.data == "lottery_open")
async def lottery_open(callback: CallbackQuery):
  text, keyboard = await build_lottery_view(callback.from_user.id)
  await callback.message.answer(text, reply_markup=keyboard)
  await callback.answer()


@router.callback_query(F.data == "lottery_toggle_notify")
async def lottery_toggle_notify(callback: CallbackQuery):
  user_id = callback.from_user.id
  async with connect() as db:
    await ensure_user(db, user_id)
    async with db.execute(
        "SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    new_val = 0 if (row[0] if row else 1) == 1 else 1
    await db.execute(
        "UPDATE users SET notify_lottery = ? WHERE user_id = ?",
        (new_val, user_id),
    )
    await db.commit()

  status_str = "отключены 🔕" if new_val == 0 else "включены 🔔"
  await callback.answer(
      f"Уведомления о крупных лотереях {status_str}!", show_alert=True
  )
  try:
    text, kb = await build_lottery_view(user_id)
    await callback.message.edit_text(text, reply_markup=kb)
  except Exception:
    pass


@router.callback_query(F.data.startswith("lottery_join_"))
async def lottery_join(callback: CallbackQuery):
  user_id = callback.from_user.id
  parts = callback.data.split("_")
  round_id = int(parts[2])
  shown_cost = int(parts[3]) if len(parts) > 3 else None
  price_changed = False
  cost = 0

  async with connect() as db:
    async with db.execute(
        "SELECT id, entry_cost, prize, ends_at, COALESCE(cost_fixed, 0),"
        " finished FROM lottery_rounds WHERE id = ?",
        (round_id,),
    ) as cur:
      row = await cur.fetchone()

    if not row or row[5] == 1:
      await callback.answer("⏳ Этот розыгрыш уже завершён.", show_alert=True)
      return
    rnd = row[:5]
    ends_at = parse_dt(rnd[3]) or datetime.datetime.now()
    if ends_at <= datetime.datetime.now():
      await callback.answer("⏳ Розыгрыш уже заканчивается.", show_alert=True)
      return

    async with db.execute(
        "SELECT 1 FROM lottery_participants WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ) as cur:
      if await cur.fetchone():
        await callback.answer("✅ Ты уже участвуешь!", show_alert=True)
        return

    st = await lottery_stats(db, rnd)
    cost = st["cost"]
    if shown_cost is not None and shown_cost != cost:
      price_changed = True
    else:
      # Списание токенов одним запросом: сработает, только если хватает токенов
      if cost > 0:
        cur = await db.execute(
            "UPDATE users SET tokens = tokens - ? WHERE user_id = ? AND"
            " COALESCE(tokens, 0) >= ?",
            (cost, user_id, cost),
        )
        charged = cur.rowcount
      else:
        await ensure_user(db, user_id)
        charged = 1
      if charged == 0:
        await db.rollback()
        await callback.answer(
            f"❌ Недостаточно токенов! Участие стоит {cost}.", show_alert=True
        )
        return
      await db.execute(
          "INSERT INTO lottery_participants (round_id, user_id, paid)"
          " VALUES (?, ?, ?)",
          (round_id, user_id, cost),
      )
      await db.commit()

  if price_changed:
    await callback.answer(
        f"💱 Цена участия изменилась: теперь {cost_text(cost)}. Проверь и"
        " нажми «Участвовать» ещё раз.",
        show_alert=True,
    )
    try:
      text, kb = await build_lottery_view(user_id)
      await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
      pass
    return

  await callback.answer("🎟 Ты в игре! Удачи!", show_alert=True)
  await callback.message.answer(
      "🎟 Ты участвуешь в лотерее!"
      + (f" Списано: <b>{cost} токен(ов)</b>." if cost else " Участие бесплатное.")
      + "\nРезультат придёт сюда, когда закончится розыгрыш."
  )
  # Проверяем: не пересёк ли суммарный приз порог уведомления ПОСЛЕ этого взноса
  rnd_now = await get_current_round()
  if rnd_now and rnd_now[0] == round_id:
    async with connect() as db:
      st_now = await lottery_stats(db, rnd_now)
    if st_now["total"] >= LOTTERY_NOTIFY_PRIZE and st_now["players"] >= 2:
      task = asyncio.create_task(
          notify_big_lottery(callback.bot, st_now["cost"], st_now["total"])
      )
      _bg_tasks.add(task)
      task.add_done_callback(_bg_tasks.discard)


# --- МАГАЗИН ---


async def shop_price(user_id: int, base_price: int, buyers_count: int) -> int:
  price = max(1, int(base_price * 0.2)) if buyers_count == 0 else base_price
  aura = await get_user_aura(user_id)
  if aura and aura["type"] == "shop":
    price = max(1, int(round(price * (1 - aura["percent"] / 100.0))))
  return price


def parse_price(price_raw) -> int:
  try:
    return int("".join(filter(str.isdigit, str(price_raw))))
  except ValueError:
    return 0


async def pay_with_tokens(callback: CallbackQuery, bot: Bot, p_id: int) -> bool:
  """Покупка за обычные токены. True, если покупка прошла."""
  user_id = callback.from_user.id

  async with connect() as db:
    async with db.execute(
        "SELECT title, price, link, buyers_count FROM products WHERE id = ?",
        (p_id,),
    ) as cursor:
      product = await cursor.fetchone()
    if not product:
      await callback.answer("❌ Этот товар был удален.", show_alert=True)
      return False
    title, price_raw, link, buyers_count = product
    final_price = await shop_price(user_id, parse_price(price_raw), buyers_count)

    async with db.execute(
        "SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
      urow = await cursor.fetchone()
    referrer_id = urow[0] if urow else 0

    cur = await db.execute(
        "UPDATE users SET tokens = tokens - ?, bought_folders ="
        " COALESCE(bought_folders, 0) + 1 WHERE user_id = ? AND"
        " COALESCE(tokens, 0) >= ?",
        (final_price, user_id, final_price),
    )
    if cur.rowcount == 0:
      await db.rollback()
      async with db.execute(
          "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
      ) as c:
        brow = await c.fetchone()
      await callback.answer(
          f"❌ Недостаточно токенов!\nЦена: {final_price} токенов, а у вас:"
          f" {brow[0] if brow else 0}.",
          show_alert=True,
      )
      return False

    await db.execute(
        "UPDATE products SET buyers_count = buyers_count + 1,"
        " token_buyers = COALESCE(token_buyers, 0) + 1 WHERE id = ?",
        (p_id,),
    )

    ref_bonus = 0
    if referrer_id and referrer_id != user_id:
      ref_bonus = round(final_price * 0.15)
      if ref_bonus > 0:
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE"
            " user_id = ?",
            (ref_bonus, referrer_id),
        )
        await db.execute(
            "UPDATE users SET ref_income = COALESCE(ref_income, 0) + ? WHERE"
            " user_id = ?",
            (ref_bonus, user_id),
        )
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as c:
      new_tokens = (await c.fetchone())[0]
    await db.commit()

  if ref_bonus > 0:
    await safe_send(
        bot,
        referrer_id,
        "💰 Реферальный бонус!\nВаш реферал совершил покупку.\n🎁 Вам"
        f" начислено {ref_bonus} токенов (15% от покупки).",
    )

  if buyers_count == 0:
    text = (
        f"🎉 Поздравляем! Ты первый покупатель фулла {esc(title)}!\n🔥"
        f" Применилась скидка 80%!\nСписано: {final_price} токенов.\n💲 Ваш"
        f" остаток: {new_tokens} токенов.\n\n🔗 Твоя ссылка на скачивание"
        f" (нажми, чтобы показать):\n{hidden_link(link)}"
    )
  else:
    text = (
        f"✅ Спасибо за покупку фула {esc(title)}!\n💰 Списано:"
        f" {final_price} токенов. Остаток: {new_tokens} токенов.\n\n🔗 Твоя"
        f" ссылка на скачивание (нажми, чтобы показать):\n{hidden_link(link)}"
    )
  await callback.message.answer(text, disable_web_page_preview=True)
  await callback.answer()
  return True


@router.callback_query(F.data.startswith("desc_"))
async def show_product_desc(callback: CallbackQuery):
  """Показывает описание товара (фото + текст) и кнопку «Купить»."""
  p_id = int(callback.data.split("_")[1])
  async with connect() as db:
    async with db.execute(
        "SELECT title, price, buyers_count, description, photo_id, token_buyers"
        " FROM products WHERE id = ?",
        (p_id,),
    ) as cur:
      row = await cur.fetchone()
  if not row:
    await callback.answer("❌ Товар не найден.", show_alert=True)
    return
  title, price, buyers_count, description, photo_id, token_buyers = row
  final_price = max(1, int(parse_price(price) * 0.2)) if buyers_count == 0 else parse_price(price)
  first_buy = buyers_count == 0

  cap = f"📁 <b>{esc(title)}</b>\n"
  if first_buy:
    cap += f"💰 Цена: <s>{price}</s> → <b>{final_price} токенов</b> (скидка 80%!)\n"
  else:
    cap += f"💰 Цена: <b>{price} токенов</b>\n"
  cap += f"👥 Купили за токены: <b>{token_buyers}</b>\n"
  if description:
    cap += f"\n{description}"

  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy_{p_id}")],
          [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_shop")],
      ]
  )
  if photo_id:
    await callback.message.answer_photo(photo_id, caption=cap, reply_markup=kb)
  else:
    await callback.message.answer(cap, reply_markup=kb, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "back_to_shop")
async def back_to_shop(callback: CallbackQuery):
  async with connect() as db:
    async with db.execute(
        "SELECT id, title, price, buyers_count, token_buyers FROM products"
    ) as cursor:
      products = await cursor.fetchall()
  if not products:
    await callback.answer("Магазин пуст.", show_alert=True)
    return
  keyboard_buttons = []
  for p_id, title, price, buyers_count, token_buyers in products:
    if buyers_count == 0:
      btn_text = f"🔥 {title} — {price} токенов (ПЕРВАЯ ПОКУПКА -80%!)"
    else:
      btn_text = f"📁 {title} — {price} токенов | 👥 {token_buyers} купили"
    keyboard_buttons.append(
        [InlineKeyboardButton(text=btn_text, callback_data=f"desc_{p_id}")]
    )
  try:
    await callback.message.edit_text(
        "🛍 <b>Доступные фулы:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )
  except Exception:
    await callback.message.answer(
        "🛍 <b>Доступные фулы:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )
  await callback.answer()


@router.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  if await get_restriction(user_id):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  p_id = int(callback.data.split("_")[1])

  async with connect() as db:
    async with db.execute(
        "SELECT title, price, link, buyers_count FROM products WHERE id = ?",
        (p_id,),
    ) as cursor:
      product = await cursor.fetchone()
    if not product:
      await callback.answer("❌ Этот товар был удален.", show_alert=True)
      return
    title, price_raw, link, buyers_count = product

    async with db.execute(
        "SELECT is_premium, premium_expires, COALESCE(full_tokens, 0)"
        " FROM users WHERE user_id = ?",
        (user_id,),
    ) as cursor:
      u_data = await cursor.fetchone()
    is_prem = u_data[0] if u_data else 0
    prem_expires = u_data[1] if u_data else None
    full_tokens = u_data[2] if u_data else 0

    if is_prem == 1 and not premium_active(is_prem, prem_expires):
      await db.execute(
          "UPDATE users SET is_premium = 0, premium_expires = NULL WHERE"
          " user_id = ?",
          (user_id,),
      )
      await db.commit()
      is_prem = 0

    if is_prem == 1:
      await db.execute(
          "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
          (p_id,),
      )
      await db.execute(
          "UPDATE users SET bought_folders = COALESCE(bought_folders, 0) + 1"
          " WHERE user_id = ?",
          (user_id,),
      )
      await db.commit()
      await callback.message.answer(
          f"👑 Премиум-доступ применен!\n\n✅ Вы получили фулл"
          f" {esc(title)} бесплатно по подписке!\n\n🔗 Ссылка (нажми, чтобы"
          f" показать):\n{hidden_link(link)}",
          disable_web_page_preview=True,
      )
      await callback.answer()
      return

  if full_tokens > 0:
    final_price = await shop_price(user_id, parse_price(price_raw), buyers_count)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Использовать Фулловый токен",
                    callback_data=f"usefull_{p_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🪙 Обычными ({final_price} токенов)",
                    callback_data=f"usenorm_{p_id}",
                )
            ],
        ]
    )
    await callback.message.answer(
        "❓ У вас есть фулловый токен. Использовать его или заплатить обычными"
        f" токенами ({final_price} шт.)?",
        reply_markup=kb,
    )
    await callback.answer()
    return

  await pay_with_tokens(callback, bot, p_id)


@router.callback_query(F.data.startswith("usenorm_"))
async def process_buy_norm(callback: CallbackQuery, bot: Bot):
  if await get_restriction(callback.from_user.id):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  p_id = int(callback.data.split("_")[1])
  if await pay_with_tokens(callback, bot, p_id):
    try:
      await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
      pass


@router.callback_query(F.data.startswith("usefull_"))
async def process_buy_full(callback: CallbackQuery):
  user_id = callback.from_user.id
  if await get_restriction(user_id):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  p_id = int(callback.data.split("_")[1])

  async with connect() as db:
    async with db.execute(
        "SELECT title, link FROM products WHERE id = ?", (p_id,)
    ) as cur:
      prod = await cur.fetchone()
    if not prod:
      await callback.answer("❌ Товар не найден.", show_alert=True)
      return
    title, link = prod

    cur = await db.execute(
        "UPDATE users SET full_tokens = full_tokens - 1, bought_folders ="
        " COALESCE(bought_folders, 0) + 1 WHERE user_id = ? AND"
        " COALESCE(full_tokens, 0) > 0",
        (user_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(
          "❌ У вас больше нет фулловых токенов.", show_alert=True
      )
      return
    await db.execute(
        "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
        (p_id,),
    )
    await db.commit()

  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass
  await callback.message.answer(
      f"✅ Успешная покупка фулла {esc(title)} за фулловый токен!\n\n🔗 Ссылка"
      f" (нажми, чтобы показать):\n{hidden_link(link)}",
      disable_web_page_preview=True,
  )
  await callback.answer()


# --- УНИКАЛЬНЫЙ МАГАЗИН ---


def parse_unique_command(cmd: str):
  """Разбирает команду товара. Возвращает (action, args) или None.

  Форматы: 'ftoken 1', 'ytoken 5', 'token 10', 'p 30',
  'aura shop 15 30 Название'. Старый формат с 'add {user_id}' тоже работает.
  """
  parts = [
      p
      for p in (cmd or "").replace("{user_id}", " ").split()
      if p.lower() != "add"
  ]
  if not parts:
    return None
  action = parts[0].lstrip("/").lower()
  args = parts[1:]
  if action in ("token", "tokens", "t"):
    action = "token"
  elif action in ("p", "premium"):
    action = "premium"

  if action in ("ftoken", "ytoken", "token"):
    if not args:
      return action, [1]
    if len(args) == 1 and args[0].isdigit() and int(args[0]) > 0:
      return action, [int(args[0])]
    return None
  if action == "premium":
    if len(args) == 1 and args[0].isdigit() and int(args[0]) > 0:
      return action, [int(args[0])]
    return None
  if action == "aura":
    if len(args) >= 3 and args[0].lower() in ("lottery", "roulette", "shop"):
      raw_pct = args[1].lstrip("+")
      try:
        percent = int(raw_pct)
      except ValueError:
        return None
      raw_dur = args[2]
      if raw_dur.lower().endswith("h"):
        num = raw_dur[:-1]
        if not num.isdigit() or int(num) <= 0:
          return None
        delta = datetime.timedelta(hours=int(num))
      else:
        if not raw_dur.isdigit() or int(raw_dur) <= 0:
          return None
        delta = datetime.timedelta(days=int(raw_dur))
      name = " ".join(args[3:]) or None
      return action, [args[0].lower(), percent, delta, name]
    return None
  return None


async def apply_unique_command(user_id: int, title: str, parsed) -> None:
  action, args = parsed
  if action == "ftoken":
    await grant_full_tokens(user_id, args[0])
  elif action == "ytoken":
    await grant_y_tokens(user_id, args[0])
  elif action == "token":
    await grant_tokens(user_id, args[0])
  elif action == "premium":
    await grant_premium(user_id, args[0])
  elif action == "aura":
    aura_type, percent, delta, name = args
    expires = datetime.datetime.now() + delta
    async with connect() as db:
      await ensure_user(db, user_id)
      await db.execute(
          "UPDATE users SET aura_type = ?, aura_name = ?, aura_percent = ?,"
          " aura_expires = ? WHERE user_id = ?",
          (
              aura_type,
              name or title,
              percent,
              expires.strftime(DT_FORMAT),
              user_id,
          ),
      )
      await db.commit()


@router.callback_query(F.data.startswith("udesc_"))
async def show_unique_product_desc(callback: CallbackQuery):
  """Показывает описание уникального товара и кнопки покупки."""
  up_id = int(callback.data.split("_")[1])
  async with connect() as db:
    async with db.execute(
        "SELECT title, price, COALESCE(token_price,0), amount, description,"
        " photo_id, COALESCE(token_buyers,0) FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      row = await cur.fetchone()
  if not row or row[3] <= 0:
    await callback.answer("❌ Товар закончился или удален.", show_alert=True)
    return
  title, y_price, t_price, amount, description, photo_id, token_buyers = row

  cap = f"💎 <b>{esc(title)}</b>\n"
  if y_price > 0:
    cap += f"💎 Цена: <b>{y_price} уникальных токенов</b>\n"
  if t_price > 0:
    cap += f"🪙 Или: <b>{t_price} обычных токенов</b>\n"
  cap += f"📦 Осталось: <b>{amount}</b>\n"
  cap += f"👥 Купили за токены: <b>{token_buyers}</b>\n"
  if description:
    cap += f"\n{description}"

  buttons = []
  if y_price > 0:
    buttons.append([InlineKeyboardButton(
        text=f"💎 Купить за {y_price} уник. токенов",
        callback_data=f"ubuy_{up_id}",
    )])
  if t_price > 0:
    buttons.append([InlineKeyboardButton(
        text=f"🪙 Купить за {t_price} обычных токенов",
        callback_data=f"utbuy_{up_id}",
    )])
  buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_ushop")])
  kb = InlineKeyboardMarkup(inline_keyboard=buttons)

  if photo_id:
    await callback.message.answer_photo(photo_id, caption=cap, reply_markup=kb)
  else:
    await callback.message.answer(cap, reply_markup=kb, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "back_to_ushop")
async def back_to_ushop(callback: CallbackQuery):
  async with connect() as db:
    async with db.execute(
        "SELECT id, title, price, COALESCE(token_price,0), amount,"
        " COALESCE(token_buyers,0) FROM unique_products WHERE amount > 0"
    ) as cursor:
      u_products = await cursor.fetchall()
  if not u_products:
    await callback.answer("Уникальный магазин пуст.", show_alert=True)
    return
  kb_buttons = []
  for up_id, title, price, token_price, amount, token_buyers in u_products:
    prices = []
    if price > 0:
      prices.append(f"{price} 💎")
    if token_price > 0:
      prices.append(f"{token_price} 🪙")
    price_str = " / ".join(prices) if prices else "бесплатно"
    btn_text = (f"{title} — {price_str} (ост.: {amount}) | 👥 {token_buyers}")[:60]
    kb_buttons.append(
        [InlineKeyboardButton(text=btn_text, callback_data=f"udesc_{up_id}")]
    )
  try:
    await callback.message.edit_text(
        "💎 <b>Уникальный магазин</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )
  except Exception:
    await callback.message.answer(
        "💎 <b>Уникальный магазин</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )
  await callback.answer()


@router.callback_query(F.data.startswith("utbuy_"))
async def process_utbuy(callback: CallbackQuery):
  """Покупка уникального товара за обычные токены."""
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])

  async with connect() as db:
    async with db.execute(
        "SELECT title, COALESCE(token_price,0), amount, command"
        " FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[2] <= 0:
      await callback.answer("❌ Товар закончился или удален.", show_alert=True)
      return
    title, t_price, _amount, command_str = prod
    if t_price <= 0:
      await callback.answer("❌ Этот товар нельзя купить за обычные токены.", show_alert=True)
      return

    parsed = parse_unique_command(command_str)
    if parsed is None:
      await callback.answer("❌ Команда товара настроена неверно. Напиши админу.", show_alert=True)
      return

    cur = await db.execute(
        "UPDATE unique_products SET amount = amount - 1 WHERE id = ? AND amount > 0",
        (up_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer("❌ Товар закончился.", show_alert=True)
      return

    await ensure_user(db, user_id)
    cur = await db.execute(
        "UPDATE users SET tokens = tokens - ? WHERE user_id = ?"
        " AND COALESCE(tokens, 0) >= ?",
        (t_price, user_id, t_price),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(
          f"❌ Недостаточно токенов! Нужно: {t_price}.", show_alert=True
      )
      return

    await db.execute(
        "UPDATE unique_products SET token_buyers = COALESCE(token_buyers,0) + 1"
        " WHERE id = ?",
        (up_id,),
    )
    await db.commit()

  try:
    await apply_unique_command(user_id, title, parsed)
  except Exception:
    logging.exception("Ошибка выдачи уник. товара (за токены)")
    await callback.message.answer("⚠️ Ошибка при выдаче товара. Напиши админу.")
    await callback.answer()
    return

  await callback.message.answer(
      f"🪙 <b>Успешная покупка!</b>\nВы приобрели: <b>{esc(title)}</b> за"
      f" {t_price} обычных токенов.\nВсё уже начислено в профиль."
  )
  await callback.answer()


@router.callback_query(F.data.startswith("ubuy_"))
async def process_ubuy(callback: CallbackQuery):
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])

  async with connect() as db:
    async with db.execute(
        "SELECT title, price, amount, command FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[2] <= 0:
      await callback.answer("❌ Товар закончился или удален.", show_alert=True)
      return
    title, price, _amount, command_str = prod

    parsed = parse_unique_command(command_str)
    if parsed is None:
      await callback.answer(
          "❌ У товара неверно настроена команда. Напиши админу.",
          show_alert=True,
      )
      return

    # Товар списываем первым и только если он ещё есть
    cur = await db.execute(
        "UPDATE unique_products SET amount = amount - 1"
        " WHERE id = ? AND amount > 0",
        (up_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer("❌ Товар закончился.", show_alert=True)
      return

    await ensure_user(db, user_id)
    cur = await db.execute(
        "UPDATE users SET y_tokens = y_tokens - ? WHERE user_id = ?"
        " AND COALESCE(y_tokens, 0) >= ?",
        (price, user_id, price),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(
          f"❌ Недостаточно уникальных токенов! Нужно: {price}.",
          show_alert=True,
      )
      return
    await db.execute(
        "UPDATE unique_products SET token_buyers = COALESCE(token_buyers,0) + 1"
        " WHERE id = ?",
        (up_id,),
    )
    await db.commit()

  try:
    await apply_unique_command(user_id, title, parsed)
  except Exception:
    logging.exception("Ошибка выдачи товара уник. магазина")
    await callback.message.answer(
        "⚠️ Произошла ошибка при выдаче товара. Напиши админу."
    )
    await callback.answer()
    return

  await callback.message.answer(
      f"💎 <b>Успешная покупка!</b>\nВы приобрели: <b>{esc(title)}</b> за"
      f" {price} уникальных токенов.\nВсё уже начислено в профиль."
  )
  await callback.answer()


# --- ГЛАВНОЕ МЕНЮ ---


@router.message(
    F.text.in_(
        {
            "👤 Мой Профиль",
            "🛒 Магазин",
            "🛍 Магазин",
            "💎 Уникальный магазин",
            "👥 Реферальная система",
            "🏆 Топ игроков",
            "🪙 Как получить токены?",
            "👑 Премиум-подписка",
            "🎰 Лотерея",
            "🔄 Обмены",
            "🎲 Рулетка",
            "⚙️ Админ-панель",
        }
    )
)
async def menu_router(message: Message, bot: Bot, state: FSMContext):
  await state.clear()
  user_id = message.from_user.id
  text = message.text

  if text == "👤 Мой Профиль":
    async with connect() as db:
      await ensure_user(db, user_id)
      await db.commit()
      async with db.execute(
          "SELECT COALESCE(tokens, 0), COALESCE(y_tokens, 0),"
          " COALESCE(full_tokens, 0), is_premium, COALESCE(bought_folders, 0),"
          " premium_expires, COALESCE(warns, 0), COALESCE(exchanges, 0)"
          " FROM users WHERE user_id = ?",
          (user_id,),
      ) as cursor:
        (tokens, y_tokens, full_tokens, is_prem, bought, prem_expires, warns,
         exchanges) = await cursor.fetchone()

    prem_status = "❌ Не активна"
    if is_prem == 1:
      if premium_active(is_prem, prem_expires):
        exp_date = parse_dt(prem_expires)
        if exp_date:
          days_left = (exp_date - datetime.datetime.now()).days
          prem_status = f"✅ До {exp_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
        else:
          prem_status = "✅ Активна"
      else:
        await remove_premium(user_id)
        prem_status = "❌ Не активна (истекла)"

    aura = await get_user_aura(user_id)
    if aura:
      aura_days = (aura["expires"] - datetime.datetime.now()).days
      aura_status = (
          f"✨ <b>{esc(aura['name'])}</b> ({aura['percent']}% бонусов, ещё"
          f" {aura_days} дн.)"
      )
    else:
      aura_status = "❌ Нет"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎟 Ввести промокод", callback_data="enter_promocode"
                )
            ]
        ]
    )
    await message.answer(
        f"👤 <b>Ваш Профиль</b>\n\n🆔 Ваш ID: <code>{user_id}</code>\n"
        f"💲 Баланс токенов: {tokens}\n"
        f"💎 Уникальные токены: {y_tokens}\n"
        f"📦 Фулловые токены: {full_tokens}\n"
        f"👑 Премиум-подписка: {prem_status}\n"
        f"✨ Активная аура: {aura_status}\n"
        f"📦 Собрано фулов: {bought} шт.\n"
        f"⚠️ Предупреждения: {warns}/{MAX_WARNS}\n"
        f"🔄 Обменов проведено: {exchanges}",
        reply_markup=keyboard,
    )

  elif text in ("🛒 Магазин", "🛍 Магазин"):
    async with connect() as db:
      async with db.execute(
          "SELECT id, title, price, buyers_count, token_buyers FROM products"
      ) as cursor:
        products = await cursor.fetchall()

    if not products:
      await message.answer("📭 В магазине пока пусто. Либо же вы все раскупили!")
      return

    keyboard_buttons = []
    for p_id, title, price, buyers_count, token_buyers in products:
      if buyers_count == 0:
        btn_text = f"🔥 {title} — {price} токенов (ПЕРВАЯ ПОКУПКА -80%!)"
      else:
        btn_text = f"📁 {title} — {price} токенов | 👥 {token_buyers} купили"
      keyboard_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"desc_{p_id}")]
      )
    await message.answer(
        "🛍 <b>Доступные фулы:</b>\n<i>(Нажми на нужный, чтобы посмотреть"
        " описание)</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )

  elif text == "💎 Уникальный магазин":
    async with connect() as db:
      async with db.execute(
          "SELECT id, title, price, COALESCE(token_price,0), amount,"
          " COALESCE(token_buyers,0) FROM unique_products WHERE amount > 0"
      ) as cursor:
        u_products = await cursor.fetchall()

    if not u_products:
      await message.answer("📭 Уникальный магазин пуст.")
      return

    kb_buttons = []
    for up_id, title, price, token_price, amount, token_buyers in u_products:
      prices = []
      if price > 0:
        prices.append(f"{price} 💎")
      if token_price > 0:
        prices.append(f"{token_price} 🪙")
      price_str = " / ".join(prices) if prices else "бесплатно"
      btn_text = (
          f"{title} — {price_str} (ост.: {amount}) | 👥 {token_buyers}"
      )[:60]
      kb_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"udesc_{up_id}")]
      )
    await message.answer(
        "💎 <b>Уникальный магазин</b>\nНажми на товар для описания и покупки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    await message.answer(
        "👥 <b>Реферальная система</b>\n\nПриглашай друзей по своей ссылке:\n"
        f"<code>{ref_link}</code>\n\n"
        f"🎁 За каждого приглашенного (после подписки на канал) —"
        f" <b>{REF_BONUS} токенов</b>!\n"
        "💰 А также вы получаете <b>15% дохода</b> от трат ваших рефералов"
        " токенами в обычном магазине!"
    )

  elif text == "🏆 Топ игроков":
    await message.answer(
        "🏆 <b>Выберите категорию топа:</b>", reply_markup=tops_menu_keyboard()
    )

  elif text == "🪙 Как получить токены?":
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🪙 Обычные токены", callback_data="howto_tokens")],
            [InlineKeyboardButton(text="💎 Уникальные токены", callback_data="howto_ytokens")],
            [InlineKeyboardButton(text="📦 Фулловые токены", callback_data="howto_ftokens")],
        ]
    )
    await message.answer(
        "🪙 <b>Как получить токены?</b>\nВыбери тип токена:", reply_markup=kb
    )

  elif text == "👑 Премиум-подписка":
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Отправить Подарок 50 Stars",
                    url="https://t.me/ucu52",
                )
            ]
        ]
    )
    prem_text = (
        "👑 <b>Премиум-подписка на 1 МЕСЯЦ (30 дней)</b>\n"
        "<b>Преимущества:</b>\n"
        "Дает право покупать АБСОЛЮТНО ЛЮБЫЕ фулы бесплатно c обычного"
        " магазина!\n\n"
        "💳 <b>Способ 1:</b> Купить | Заплатить 50 Звезд\n"
        "(любым подарком на профиль @ucu52)\n\n"
        "📸 <b>Способ 2 (за 100 фото):</b> Кинуть 100 Фото в коментарии в тт\n"
        "подробнее про фото: https://t.me/JmcDeveloper/18\n\n"
        "Вы можете получить премиум бесплатно за 100 фото в коменты!\n"
        "<i>Если была хотя бы одна фото дублирована или уже принята как Токен"
        " — подписка не выдается.</i>\n"
        "Если вы сделали все хорошо, отправляйте сюда:"
        " https://t.me/JmcDeveloper?direct\n"
        "<b>(И СРАЗУ ГОВОРИТЕ ЧТО ДЛЯ ПРЕМИУМ ПОДПИСКИ)</b>"
    )
    await message.answer(
        prem_text, reply_markup=keyboard, disable_web_page_preview=True
    )

  elif text == "🎰 Лотерея":
    lottery_text, lottery_kb = await build_lottery_view(user_id)
    await message.answer(lottery_text, reply_markup=lottery_kb)

  elif text == "🔄 Обмены":
    await message.answer(await ex_menu_text(), reply_markup=exchange_menu_kb())

  elif text == "🎲 Рулетка":
    await state.set_state(RouletteState.bet)
    await message.answer(await roulette_intro_text(user_id))

  elif text == "⚙️ Админ-панель":
    if user_id != ADMIN_ID:
      await message.answer("⛔️ У тебя нет доступа!")
      return

    def btn(label, data):
      return [InlineKeyboardButton(text=label, callback_data=data)]

    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            btn("➕ Добавить фулл", "add_product"),
            btn("💎 Добавить в уник. магазин", "add_unique_product"),
            btn("✏️ Редактировать товар", "ed_list"),
            btn("🗑 Удалить товар", "del_list"),
            btn("✏️ Ред. уник. товар", "ued_list"),
            btn("🗑 Уд. уник. товар", "udel_list"),
            btn("🎟 Создать промокод", "admin_create_promo"),
            btn("🪙 Выдать токены", "admin_give_tokens"),
            btn("💎 Выдать уник. токены", "admin_give_ytokens"),
            btn("📦 Выдать фулл. токены", "admin_give_ftokens"),
            btn("👑 Выдать Премиум", "admin_give_premium"),
            btn("📢 Сделать рассылку", "start_broadcast"),
            btn("💾 Скачать бэкап БД", "admin_backup_db"),
        ]
    )
    await message.answer("⚙️ Панель администратора:", reply_markup=admin_keyboard)


# --- ПРОМОКОД: ВВОД ПОЛЬЗОВАТЕЛЕМ ---


@router.message(StateFilter(PromocodeState.code))
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = txt(message)
  await state.clear()
  reply = await apply_promocode(user_id, code_text)
  await message.answer(reply, reply_markup=get_main_keyboard(user_id))


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await state.set_state(PromocodeState.code)
  await callback.message.answer("🎟 Введите промокод:")
  await callback.answer()


# --- АДМИНКА: ВЫДАЧА ТОКЕНОВ И ПРЕМИУМА ---

GIVE_KINDS = {
    "tokens": ("🪙", "токенов", grant_tokens),
    "y": ("💎", "уникальных токенов", grant_y_tokens),
    "full": ("📦", "фулловых токенов", grant_full_tokens),
}


async def _start_give(callback: CallbackQuery, state: FSMContext, kind: str):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(GiveState.user_id)
  await state.update_data(kind=kind)
  icon, label, _ = GIVE_KINDS[kind]
  await callback.message.answer(
      f"{icon} Введи <b>Telegram ID</b> пользователя для начисления {label}:"
  )
  await callback.answer()


@router.callback_query(F.data == "admin_give_tokens")
async def start_give_tokens(callback: CallbackQuery, state: FSMContext):
  await _start_give(callback, state, "tokens")


@router.callback_query(F.data == "admin_give_ytokens")
async def start_give_ytokens(callback: CallbackQuery, state: FSMContext):
  await _start_give(callback, state, "y")


@router.callback_query(F.data == "admin_give_ftokens")
async def start_give_ftokens(callback: CallbackQuery, state: FSMContext):
  await _start_give(callback, state, "full")


@router.message(StateFilter(GiveState.user_id))
async def process_give_id(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  await state.update_data(target_id=int(txt(message)))
  await state.set_state(GiveState.amount)
  data = await state.get_data()
  icon, label, _ = GIVE_KINDS[data["kind"]]
  await message.answer(f"{icon} Сколько {label} начислить? (целое число)")


@router.message(StateFilter(GiveState.amount))
async def process_give_amount(message: Message, state: FSMContext, bot: Bot):
  if not txt(message).isdigit() or int(txt(message)) <= 0:
    await message.answer("❌ Введите число больше нуля.")
    return
  amount = int(txt(message))
  data = await state.get_data()
  target_id, kind = data["target_id"], data["kind"]
  await state.clear()
  icon, label, grant = GIVE_KINDS[kind]
  try:
    await grant(target_id, amount)
  except Exception as e:
    logging.exception("Ошибка выдачи через админ-панель")
    await message.answer(f"❌ Ошибка выдачи: {esc(e)}")
    return
  await message.answer(
      f"✅ <code>{target_id}</code>: начислено <b>+{amount}</b> {label}.",
      reply_markup=get_main_keyboard(message.from_user.id),
  )
  await safe_send(
      bot,
      target_id,
      f"🎁 <b>Администратор начислил вам {amount} {label}!</b> Проверьте"
      " профиль.",
  )


@router.callback_query(F.data == "admin_give_premium")
async def start_give_premium(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(GivePremiumState.user_id)
  await callback.message.answer(
      "👑 Введи <b>Telegram ID</b> пользователя для выдачи Премиума:"
  )
  await callback.answer()


@router.message(StateFilter(GivePremiumState.user_id))
async def process_give_premium_id(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Только цифры.")
    return
  await state.update_data(target_id=int(txt(message)))
  await state.set_state(GivePremiumState.days)
  await message.answer("⏳ На сколько <b>дней</b> выдать Премиум?")


@router.message(StateFilter(GivePremiumState.days))
async def process_give_premium_days(
    message: Message, state: FSMContext, bot: Bot
):
  if not txt(message).isdigit() or int(txt(message)) <= 0:
    await message.answer("❌ Введите число дней цифрами.")
    return
  days = int(txt(message))
  target_id = (await state.get_data())["target_id"]
  await state.clear()
  try:
    new_expire = await grant_premium(target_id, days)
  except Exception as e:
    logging.exception("Ошибка выдачи премиума")
    await message.answer(f"❌ Ошибка выдачи премиума: {esc(e)}")
    return
  formatted = new_expire.strftime("%d.%m.%Y")
  await message.answer(
      f"✅ Премиум выдан <code>{target_id}</code> на {days} дн. (до"
      f" {formatted}).",
      reply_markup=get_main_keyboard(message.from_user.id),
  )
  await safe_send(
      bot,
      target_id,
      f"🎉 <b>Вам активирована Премиум-подписка на {days} дней!</b>\n📅 До"
      f" <b>{formatted}</b>.",
  )


# --- АДМИНКА: ПРОМОКОДЫ ---


@router.callback_query(F.data == "admin_create_promo")
async def start_create_promo(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(CreatePromoState.code)
  await callback.message.answer(
      "🎟 Введите текст промокода (например: <code>START100</code>):"
  )
  await callback.answer()


@router.message(StateFilter(CreatePromoState.code))
async def process_promo_code(message: Message, state: FSMContext):
  code = txt(message)
  if not code or " " in code:
    await message.answer("❌ Промокод — одно слово без пробелов.")
    return
  await state.update_data(code=code)
  await state.set_state(CreatePromoState.type)
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(text="🪙 Токены", callback_data="promo_type_tokens"),
              InlineKeyboardButton(text="👑 Премиум", callback_data="promo_type_premium"),
          ]
      ]
  )
  await message.answer("🎁 Выберите, что будет давать промокод:", reply_markup=kb)


@router.callback_query(F.data.startswith("promo_type_"))
async def process_promo_type(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_type = callback.data.split("_")[2]
  if p_type not in ("tokens", "premium"):
    await callback.answer()
    return
  await state.update_data(type=p_type)
  await state.set_state(CreatePromoState.value)
  if p_type == "tokens":
    await callback.message.answer("💰 Введите количество токенов (число):")
  else:
    await callback.message.answer("⏳ Введите количество дней Премиума (число):")
  await callback.answer()


@router.message(StateFilter(CreatePromoState.value))
async def process_promo_value(message: Message, state: FSMContext):
  if not txt(message).isdigit() or int(txt(message)) <= 0:
    await message.answer("❌ Ошибка! Введите целое число больше нуля.")
    return
  await state.update_data(value=txt(message))
  await state.set_state(CreatePromoState.uses)
  await message.answer(
      "🔢 Введите <b>количество использований</b> (например: 10 или"
      " 100):\n<i>(Введите 0 для безлимита)</i>"
  )


@router.message(StateFilter(CreatePromoState.uses))
async def process_promo_uses(message: Message, state: FSMContext, bot: Bot):
  if not txt(message).isdigit():
    await message.answer("❌ Введите число использований цифрами.")
    return
  max_uses = int(txt(message))
  data = await state.get_data()
  code, reward_type, reward_value = (
      str(data["code"]),
      str(data["type"]),
      str(data["value"]),
  )
  await state.clear()

  async with connect() as db:
    await db.execute(
        """
        INSERT INTO promocodes (code, reward_type, reward_value, max_uses, used_count)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(code) DO UPDATE SET reward_type = ?, reward_value = ?, max_uses = ?
        """,
        (code, reward_type, reward_value, max_uses,
         reward_type, reward_value, max_uses),
    )
    await db.commit()

  uses_text = f"{max_uses} раз" if max_uses > 0 else "Безлимитно"
  reward_desc = (
      f"{reward_value} токенов"
      if reward_type == "tokens"
      else f"Премиум на {reward_value} дней"
  )
  await message.answer(
      f"✅ <b>Промокод создан!</b>\n\n🎟 Промокод: <code>{esc(code)}</code>\n"
      f"🎁 Награда: {reward_desc}\n👥 Использований: {uses_text}",
      reply_markup=get_main_keyboard(message.from_user.id),
  )

  # Готовый пост для канала
  try:
    username = (await bot.get_me()).username
  except Exception:
    username = "ygolokbomja_bot"
  if reward_type == "tokens":
    reward_line = ru_plural(int(reward_value), "токен", "токена", "токенов")
  else:
    reward_line = "Премиум на " + ru_plural(
        int(reward_value), "день", "дня", "дней"
    )
  if max_uses > 0:
    uses_line = ru_plural(
        max_uses, "Использование", "Использования", "Использований"
    )
  else:
    uses_line = "Безлимит использований"
  await message.answer(
      "📋 <b>Готовый пост для канала:</b>\n\n"
      f"<code>/promocode {esc(code)}</code>\n"
      f"{reward_line}\n{uses_line}\n"
      f"<i>Вписывать промокод в бота @{esc(username)}</i>"
  )


# --- АДМИНКА: ТОВАРЫ ---


@router.callback_query(F.data == "add_product")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer("📝 Введи название фула (например: Казань, Пантера):")
  await state.set_state(AddProduct.title)
  await callback.answer()


@router.message(StateFilter(AddProduct.title))
async def process_product_title(message: Message, state: FSMContext):
  if not txt(message):
    await message.answer("❌ Введи название текстом.")
    return
  await state.update_data(title=txt(message))
  await message.answer("💰 Введи цену в токенах (число, например: 15):")
  await state.set_state(AddProduct.price)


@router.message(StateFilter(AddProduct.price))
async def process_product_price(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Цена — целое число.")
    return
  await state.update_data(price=txt(message))
  await message.answer(
      "🔗 Отправь ссылку на скачивание (Mega / облако):\n\n<i>(⚠️"
      " Напоминание: у первого покупателя этого товара будет скидка"
      " 80%)</i>"
  )
  await state.set_state(AddProduct.link)


@router.message(StateFilter(AddProduct.link))
async def process_product_link(message: Message, state: FSMContext):
  link = txt(message)
  if not link:
    await message.answer("❌ Отправь ссылку текстом.")
    return
  await state.update_data(link=link)
  await message.answer(
      "📝 Введи описание товара (текст, ссылки — всё что угодно).\n"
      "Или напиши <code>-</code> чтобы пропустить."
  )
  await state.set_state(AddProduct.description)


@router.message(StateFilter(AddProduct.description))
async def process_product_description(message: Message, state: FSMContext):
  desc = txt(message)
  await state.update_data(description=None if desc == "-" else desc)
  await message.answer(
      "🖼 Отправь фото для товара (как изображение, не файл).\n"
      "Или напиши <code>-</code> чтобы пропустить."
  )
  await state.set_state(AddProduct.photo)


@router.message(StateFilter(AddProduct.photo))
async def process_product_photo(message: Message, state: FSMContext):
  photo_id = None
  if message.photo:
    photo_id = message.photo[-1].file_id
  elif txt(message) != "-":
    await message.answer("❌ Отправь фото как изображение или напиши <code>-</code>.")
    return
  data = await state.get_data()
  await state.clear()
  async with connect() as db:
    await db.execute(
        "INSERT INTO products (title, price, link, buyers_count, description,"
        " photo_id) VALUES (?, ?, ?, 0, ?, ?)",
        (data["title"], data["price"], data["link"],
         data.get("description"), photo_id),
    )
    await db.commit()
  await message.answer(
      f"✅ Фулл добавлен в магазин!\n\n📌 Название: {esc(data['title'])}\n"
      f"💰 Цена: {esc(data['price'])} токенов\n🔗 Ссылка: {esc(data['link'])}\n"
      "🎁 Активирована скидка 80% для первого покупателя!"
  )


@router.callback_query(F.data == "ed_list")
async def show_edit_products_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with connect() as db:
    async with db.execute("SELECT id, title FROM products") as cur:
      products = await cur.fetchall()
  if not products:
    await callback.message.answer("📭 В магазине нет товаров для редактирования.")
    await callback.answer()
    return
  kb = [
      [InlineKeyboardButton(text=f"✏️ {p[1]}", callback_data=f"ep_{p[0]}")]
      for p in products
  ]
  await callback.message.answer(
      "✏️ Выберите товар для редактирования:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("ep_"))
async def process_edit_product_select(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[1])
  await state.update_data(product_id=p_id)
  async with connect() as db:
    async with db.execute(
        "SELECT title, price, link FROM products WHERE id = ?", (p_id,)
    ) as cur:
      product = await cur.fetchone()
  if not product:
    await callback.answer("❌ Товар не найден.", show_alert=True)
    return
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="📝 Название", callback_data="ef_title")],
          [InlineKeyboardButton(text="💰 Цена", callback_data="ef_price")],
          [InlineKeyboardButton(text="🔗 Ссылка", callback_data="ef_link")],
          [InlineKeyboardButton(text="📄 Описание", callback_data="ef_description")],
          [InlineKeyboardButton(text="🖼 Фото", callback_data="ef_photo")],
      ]
  )
  await callback.message.answer(
      f"✏️ <b>Редактирование:</b> {esc(product[0])}\n💰 Цена:"
      f" {esc(product[1])} токенов\n🔗 Ссылка: {esc(product[2])}\n\nЧто"
      " изменить?",
      reply_markup=kb,
  )
  await callback.answer()


@router.callback_query(
    F.data.in_({"ef_title", "ef_price", "ef_link", "ef_description", "ef_photo"})
)
async def process_edit_product_field(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  field = callback.data.split("_")[1]
  await state.update_data(field=field)
  if field == "photo":
    await state.set_state(EditProduct.photo)
    await callback.message.answer(
        "🖼 Отправь новое фото как изображение, или напиши <code>-</code>"
        " чтобы удалить фото."
    )
  else:
    await state.set_state(EditProduct.new_value)
    hints = {
        "title": "новое название",
        "price": "новую цену (число)",
        "link": "новую ссылку",
        "description": "новое описание (или <code>-</code> чтобы удалить)",
    }
    await callback.message.answer(
        f"✍️ Введи {hints.get(field, 'новое значение')}:"
    )
  await callback.answer()


@router.message(StateFilter(EditProduct.new_value))
async def process_edit_product_save(message: Message, state: FSMContext):
  if message.from_user.id != ADMIN_ID:
    return
  data = await state.get_data()
  p_id, field = data["product_id"], data["field"]
  new_val = txt(message)
  if field not in ("title", "price", "link", "description") or not new_val:
    await message.answer("❌ Неверное значение.")
    return
  if field == "price" and not new_val.isdigit():
    await message.answer("❌ Цена — целое число.")
    return
  db_val = None if (field == "description" and new_val == "-") else new_val
  await state.clear()
  async with connect() as db:
    await db.execute(
        f"UPDATE products SET {field} = ? WHERE id = ?", (db_val, p_id)
    )
    await db.commit()
  await message.answer("✅ Товар успешно обновлен!")


@router.message(StateFilter(EditProduct.photo))
async def process_edit_product_photo(message: Message, state: FSMContext):
  if message.from_user.id != ADMIN_ID:
    return
  data = await state.get_data()
  p_id = data["product_id"]
  if message.photo:
    photo_id = message.photo[-1].file_id
  elif txt(message) == "-":
    photo_id = None
  else:
    await message.answer("❌ Отправь фото как изображение или напиши <code>-</code>.")
    return
  await state.clear()
  async with connect() as db:
    await db.execute(
        "UPDATE products SET photo_id = ? WHERE id = ?", (photo_id, p_id)
    )
    await db.commit()
  await message.answer("✅ Фото товара обновлено!")


@router.callback_query(F.data == "del_list")
async def show_delete_products_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with connect() as db:
    async with db.execute("SELECT id, title FROM products") as cur:
      products = await cur.fetchall()
  if not products:
    await callback.message.answer("📭 Нет товаров для удаления.")
    await callback.answer()
    return
  kb = [
      [InlineKeyboardButton(text=f"🗑 {p[1]}", callback_data=f"dp_{p[0]}")]
      for p in products
  ]
  await callback.message.answer(
      "🗑 Выберите товар для удаления:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("dp_"))
async def process_delete_product(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[1])
  async with connect() as db:
    await db.execute("DELETE FROM products WHERE id = ?", (p_id,))
    await db.commit()
  await callback.message.answer("✅ Товар удален из магазина.")
  await callback.answer()


# --- АДМИНКА: УНИКАЛЬНЫЕ ТОВАРЫ ---

UNIQUE_COMMAND_HELP = (
    "⚙️ Что выдавать при покупке? Примеры:\n"
    "<code>ftoken 1</code> — фулловый токен\n"
    "<code>ytoken 5</code> — уникальные токены\n"
    "<code>token 10</code> — обычные токены\n"
    "<code>p 30</code> — премиум на 30 дней\n"
    "<code>aura shop 15 30 Оптовик</code> — аура (тип, %, дни, название)\n\n"
    "ID вводить не нужно, получит тот, кто купил."
)


@router.callback_query(F.data == "add_unique_product")
async def start_add_unique_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer(
      "💎 Введите название товара (например: Фулловый токен х1):"
  )
  await state.set_state(AddUniqueProduct.title)
  await callback.answer()


@router.message(StateFilter(AddUniqueProduct.title))
async def process_u_title(message: Message, state: FSMContext):
  if not txt(message):
    await message.answer("❌ Введи название текстом.")
    return
  await state.update_data(title=txt(message))
  await message.answer("💰 Введите цену в <b>уникальных токенах</b> (например: 10):")
  await state.set_state(AddUniqueProduct.price)


@router.message(StateFilter(AddUniqueProduct.price))
async def process_u_price(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Введите число!")
    return
  await state.update_data(price=int(txt(message)))
  await message.answer(
      "🪙 Введите цену в <b>обычных токенах</b> (или <code>0</code> если"
      " нельзя купить за обычные токены):"
  )
  await state.set_state(AddUniqueProduct.token_price)


@router.message(StateFilter(AddUniqueProduct.token_price))
async def process_u_token_price(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Введите число (0 если не продаётся за обычные токены).")
    return
  await state.update_data(token_price=int(txt(message)))
  await message.answer("📦 Введите количество (штук в наличии, например: 5):")
  await state.set_state(AddUniqueProduct.amount)


@router.message(StateFilter(AddUniqueProduct.amount))
async def process_u_amount(message: Message, state: FSMContext):
  if not txt(message).isdigit():
    await message.answer("❌ Введите число!")
    return
  await state.update_data(amount=int(txt(message)))
  await message.answer(UNIQUE_COMMAND_HELP)
  await state.set_state(AddUniqueProduct.command)


@router.message(StateFilter(AddUniqueProduct.command))
async def process_u_command(message: Message, state: FSMContext):
  cmd = txt(message)
  if parse_unique_command(cmd) is None:
    await message.answer("❌ Не понял команду.\n\n" + UNIQUE_COMMAND_HELP)
    return
  await state.update_data(command=cmd)
  await message.answer(
      "📝 Введи описание товара (текст, ссылки — всё что угодно).\n"
      "Или напиши <code>-</code> чтобы пропустить."
  )
  await state.set_state(AddUniqueProduct.description)


@router.message(StateFilter(AddUniqueProduct.description))
async def process_u_description(message: Message, state: FSMContext):
  desc = txt(message)
  await state.update_data(description=None if desc == "-" else desc)
  await message.answer(
      "🖼 Отправь фото для товара (как изображение, не файл).\n"
      "Или напиши <code>-</code> чтобы пропустить."
  )
  await state.set_state(AddUniqueProduct.photo)


@router.message(StateFilter(AddUniqueProduct.photo))
async def process_u_photo(message: Message, state: FSMContext):
  photo_id = None
  if message.photo:
    photo_id = message.photo[-1].file_id
  elif txt(message) != "-":
    await message.answer("❌ Отправь фото как изображение или напиши <code>-</code>.")
    return
  data = await state.get_data()
  await state.clear()
  async with connect() as db:
    await db.execute(
        "INSERT INTO unique_products (title, price, token_price, amount,"
        " command, description, photo_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (data["title"], data["price"], data.get("token_price", 0),
         data["amount"], data["command"], data.get("description"), photo_id),
    )
    await db.commit()
  await message.answer("✅ Товар добавлен в уникальный магазин!")


@router.callback_query(F.data == "ued_list")
async def show_edit_unique_products_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with connect() as db:
    async with db.execute("SELECT id, title FROM unique_products") as cur:
      products = await cur.fetchall()
  if not products:
    await callback.message.answer("📭 Уникальный магазин пуст.")
    await callback.answer()
    return
  kb = [
      [InlineKeyboardButton(text=f"✏️ {p[1]}", callback_data=f"uep_{p[0]}")]
      for p in products
  ]
  await callback.message.answer(
      "✏️ Выберите уникальный товар для редактирования:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("uep_"))
async def process_edit_unique_select(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[1])
  await state.update_data(product_id=p_id)
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="Название", callback_data="uef_title")],
          [InlineKeyboardButton(text="Цена 💎", callback_data="uef_price")],
          [InlineKeyboardButton(text="Цена 🪙 токены", callback_data="uef_token_price")],
          [InlineKeyboardButton(text="Количество", callback_data="uef_amount")],
          [InlineKeyboardButton(text="Команда", callback_data="uef_command")],
          [InlineKeyboardButton(text="📄 Описание", callback_data="uef_description")],
          [InlineKeyboardButton(text="🖼 Фото", callback_data="uef_photo")],
      ]
  )
  await callback.message.answer("Что изменить в уникальном товаре?", reply_markup=kb)
  await callback.answer()


@router.callback_query(
    F.data.in_({
        "uef_title", "uef_price", "uef_token_price", "uef_amount",
        "uef_command", "uef_description", "uef_photo"
    })
)
async def process_edit_unique_field(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  # callback.data = "uef_token_price" → field = "token_price"
  field = callback.data[4:]  # strip "uef_"
  await state.update_data(field=field)
  if field == "photo":
    await state.set_state(EditUniqueProduct.photo)
    await callback.message.answer(
        "🖼 Отправь новое фото как изображение, или напиши <code>-</code>"
        " чтобы удалить фото."
    )
  else:
    await state.set_state(EditUniqueProduct.new_value)
    hints = {
        "title": "новое название",
        "price": "новую цену в уникальных токенах (число)",
        "token_price": "новую цену в обычных токенах (0 = нельзя купить за токены)",
        "amount": "новое количество (число)",
        "command": "новую команду",
        "description": "новое описание (или <code>-</code> чтобы удалить)",
    }
    hint = ("\n\n" + UNIQUE_COMMAND_HELP) if field == "command" else ""
    await callback.message.answer(
        f"✍️ Введите {hints.get(field, 'новое значение')}:{hint}"
    )
  await callback.answer()


@router.message(StateFilter(EditUniqueProduct.new_value))
async def process_edit_unique_save(message: Message, state: FSMContext):
  if message.from_user.id != ADMIN_ID:
    return
  data = await state.get_data()
  p_id, field = data["product_id"], data["field"]
  new_val = txt(message)
  valid_fields = ("title", "price", "token_price", "amount", "command", "description")
  if field not in valid_fields or not new_val:
    await message.answer("❌ Неверное значение.")
    return
  if field in ("price", "token_price", "amount"):
    if not new_val.isdigit():
      await message.answer("❌ Это поле должно быть числом.")
      return
    value = int(new_val)
  elif field == "description":
    value = None if new_val == "-" else new_val
  else:
    value = new_val
  if field == "command" and parse_unique_command(new_val) is None:
    await message.answer("❌ Не понял команду.\n\n" + UNIQUE_COMMAND_HELP)
    return
  await state.clear()
  async with connect() as db:
    await db.execute(
        f"UPDATE unique_products SET {field} = ? WHERE id = ?", (value, p_id)
    )
    await db.commit()
  await message.answer("✅ Уникальный товар обновлен!")


@router.message(StateFilter(EditUniqueProduct.photo))
async def process_edit_unique_photo(message: Message, state: FSMContext):
  if message.from_user.id != ADMIN_ID:
    return
  data = await state.get_data()
  p_id = data["product_id"]
  if message.photo:
    photo_id = message.photo[-1].file_id
  elif txt(message) == "-":
    photo_id = None
  else:
    await message.answer("❌ Отправь фото как изображение или напиши <code>-</code>.")
    return
  await state.clear()
  async with connect() as db:
    await db.execute(
        "UPDATE unique_products SET photo_id = ? WHERE id = ?", (photo_id, p_id)
    )
    await db.commit()
  await message.answer("✅ Фото уникального товара обновлено!")


@router.callback_query(F.data == "udel_list")
async def show_delete_unique_products_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with connect() as db:
    async with db.execute("SELECT id, title FROM unique_products") as cur:
      products = await cur.fetchall()
  if not products:
    await callback.message.answer("📭 Уникальный магазин пуст.")
    await callback.answer()
    return
  kb = [
      [InlineKeyboardButton(text=f"🗑 {p[1]}", callback_data=f"udp_{p[0]}")]
      for p in products
  ]
  await callback.message.answer(
      "🗑 Выберите уникальный товар для удаления:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("udp_"))
async def process_delete_unique(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[1])
  async with connect() as db:
    await db.execute("DELETE FROM unique_products WHERE id = ?", (p_id,))
    await db.commit()
  await callback.message.answer("✅ Уникальный товар удален.")
  await callback.answer()


# --- АДМИНКА: РАССЫЛКА И БЭКАП ---


@router.callback_query(F.data == "start_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer(
      "📢 Отправь текст, картинку или видео для рассылки всем пользователям"
      " бота (/cancel — отмена):"
  )
  await state.set_state(BroadcastState.message)
  await callback.answer()


@router.message(StateFilter(BroadcastState.message))
async def send_broadcast(message: Message, state: FSMContext, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    return
  await state.clear()
  async with connect() as db:
    async with db.execute("SELECT user_id FROM users") as cursor:
      users = await cursor.fetchall()

  success, fail = 0, 0
  status_msg = await message.answer("⏳ Рассылка началась...")
  for (uid,) in users:
    try:
      await bot.copy_message(
          chat_id=uid,
          from_chat_id=message.chat.id,
          message_id=message.message_id,
      )
      success += 1
    except Exception:
      fail += 1
    await asyncio.sleep(0.05)

  await status_msg.edit_text(
      f"✅ Рассылка завершена!\n\n📤 Доставлено: {success}\n❌ Не доставлено"
      f" (заблокировали бота): {fail}"
  )


@router.callback_query(F.data == "admin_backup_db")
async def backup_database_manual(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer("⛔️ У тебя нет доступа!", show_alert=True)
    return
  if os.path.exists(DB_NAME):
    await callback.message.answer_document(
        FSInputFile(DB_NAME),
        caption=(
            "💾 Ручной бэкап БД:"
            f" {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        ),
    )
    await callback.answer()
  else:
    await callback.answer("❌ База данных не найдена!", show_alert=True)


# --- ОБМЕНЫ ССЫЛКАМИ ---

EXCHANGE_COST = 1  # сколько токенов платит тот, кто берёт обмен
RESTRICT_HOURS = 72  # на сколько часов ограничивается обвиняемый
MAX_OPEN_OFFERS = 5  # сколько открытых обменов может держать один человек
EX_PAGE_SIZE = 5
EX_SEARCH = {}  # user_id -> строка поиска
LINK_RE = re.compile(
    r"^(https://mega\.nz/(file|folder)/\S+|https://cloud\.mail\.ru/\S+)$", re.I
)
LINK_RULE = "⚠️ Ссылка должна быть с mega.nz или cloud.mail.ru"
LINK_ERROR = "❌ Неверная ссылка. " + LINK_RULE


async def ex_exec(sql, args=()):
  async with connect() as db:
    cur = await db.execute(sql, args)
    await db.commit()
    return cur.rowcount, cur.lastrowid


async def ex_one(sql, args=()):
  async with connect() as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(sql, args) as cur:
      return await cur.fetchone()


async def ex_all(sql, args=()):
  async with connect() as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(sql, args) as cur:
      return await cur.fetchall()


async def get_restriction(user_id: int):
  """datetime окончания ограничения обменов/покупок или None."""
  row = await ex_one(
      "SELECT restricted_until FROM users WHERE user_id = ?", (user_id,)
  )
  if not row or not row["restricted_until"]:
    return None
  until = parse_dt(row["restricted_until"])
  if until is None:
    return None
  if until <= datetime.datetime.now():
    await ex_exec(
        "UPDATE users SET restricted_until = NULL WHERE user_id = ?",
        (user_id,),
    )
    return None
  return until


RESTRICTED_TEXT = (
    "🔒 На вас подана жалоба. Обмены и покупки в магазине временно"
    " ограничены до решения администратора.\n\n" + APPEAL_TEXT
)
RESTRICTED_ALERT = (
    "🔒 Вы ограничены от обменов и покупок. Если это незаслуженно — напишите"
    " @ucu52"
)


def ex_kb(rows) -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
          for row in rows
      ]
  )


async def ex_show(event, text, kb=None):
  """Callback -> редактирует сообщение, Message -> отправляет новое."""
  if isinstance(event, CallbackQuery):
    try:
      await event.message.edit_text(
          text, reply_markup=kb, disable_web_page_preview=True
      )
      return
    except TelegramBadRequest as e:
      if "not modified" in str(e).lower():
        return
    await event.message.answer(
        text, reply_markup=kb, disable_web_page_preview=True
    )
  else:
    await event.answer(text, reply_markup=kb, disable_web_page_preview=True)


def exchange_menu_kb() -> InlineKeyboardMarkup:
  return ex_kb([
      [("➕ Выставить обмен", "ex:new"), ("📋 Все обмены", "ex:all")],
      [("🔍 Поиск", "ex:search"), ("📦 Мои обмены", "ex:my")],
  ])


async def ex_menu_text() -> str:
  return (
      "🔄 <b>Обмены ссылками</b>\nВыставляй свой обмен или бери чужой."
      f" Чтобы провести обмен, нужно заплатить {EXCHANGE_COST} токен."
  )


@router.callback_query(F.data == "ex:menu")
async def ex_menu_cb(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await ex_show(callback, await ex_menu_text(), exchange_menu_kb())
  await callback.answer()


@router.callback_query(F.data == "ex:cancel")
async def ex_cancel(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await ex_show(callback, "Отменено.", ex_kb([[("⬅️ В обмены", "ex:menu")]]))
  await callback.answer()


@router.callback_query(F.data == "ex:noop")
async def ex_noop(callback: CallbackQuery):
  await callback.answer()


# --- создание обмена ---


@router.callback_query(F.data == "ex:new")
async def ex_new(callback: CallbackQuery, state: FSMContext):
  uid = callback.from_user.id
  if await get_restriction(uid):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  cnt = await ex_one(
      "SELECT COUNT(*) n FROM ex_offers WHERE owner_id = ? AND status = 'open'",
      (uid,),
  )
  if cnt["n"] >= MAX_OPEN_OFFERS:
    await callback.answer(
        f"У тебя уже {MAX_OPEN_OFFERS} открытых обменов. Удали лишний.",
        show_alert=True,
    )
    return
  await state.set_state(NewOfferState.give)
  await ex_show(
      callback,
      "Что ты <b>отдаёшь</b>? (например: Пантера)",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(NewOfferState.give))
async def ex_new_give(message: Message, state: FSMContext):
  text = txt(message)[:60]
  if not text:
    await message.answer("Напиши текстом, что ты отдаёшь.")
    return
  await state.update_data(give=text)
  await state.set_state(NewOfferState.want)
  await message.answer(
      "Что хочешь <b>получить</b>? (например: Сердцеед)",
      reply_markup=ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.want))
async def ex_new_want(message: Message, state: FSMContext):
  text = txt(message)[:60]
  if not text:
    await message.answer("Напиши текстом, что хочешь получить.")
    return
  await state.update_data(want=text)
  await state.set_state(NewOfferState.link)
  await message.answer(
      f"Отправь <b>свою ссылку</b> для обмена.\n{LINK_RULE}",
      reply_markup=ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.link))
async def ex_new_link(message: Message, state: FSMContext):
  link = txt(message)
  if not LINK_RE.match(link):
    await message.answer(LINK_ERROR)
    return
  data = await state.get_data()
  await state.clear()
  _, offer_id = await ex_exec(
      "INSERT INTO ex_offers (owner_id, give, want, owner_link, search,"
      " created) VALUES (?, ?, ?, ?, ?, ?)",
      (
          message.from_user.id,
          data["give"],
          data["want"],
          link,
          (data["give"] + " " + data["want"]).lower(),
          int(datetime.datetime.now().timestamp()),
      ),
  )
  await message.answer(
      f"✅ Обмен #{offer_id} выставлен: <b>{esc(data['give'])}</b> →"
      f" <b>{esc(data['want'])}</b>.\nТебе придёт уведомление, когда кто-то"
      " возьмётся за него.",
      reply_markup=ex_kb([[("🔄 Обмены", "ex:menu")]]),
  )


# --- список и поиск ---


async def ex_render_list(event, user_id: int, page: int):
  q = EX_SEARCH.get(user_id)
  admin = user_id == ADMIN_ID
  where = "status IN ('open', 'taken')" if admin else "status = 'open'"
  args = []
  if q:
    if q.isdigit():
      where += " AND (owner_id = ? OR id = ?)"
      args += [int(q), int(q)]
    else:
      for w in q.lower().split()[:3]:
        where += " AND search LIKE ?"
        args.append(f"%{w}%")
  total = (
      await ex_one(f"SELECT COUNT(*) n FROM ex_offers WHERE {where}", args)
  )["n"]
  pages = max(1, -(-total // EX_PAGE_SIZE))
  page = max(0, min(page, pages - 1))
  rows = await ex_all(
      f"SELECT * FROM ex_offers WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
      args + [EX_PAGE_SIZE, page * EX_PAGE_SIZE],
  )
  head = "📋 <b>Открытые обмены</b>" if not admin else "📋 <b>Обмены (админ)</b>"
  if q:
    head += f"\n🔍 Поиск: <i>{esc(q)}</i>"
  head += f"\nНайдено: {total} · стр. {page + 1}/{pages}"
  if not rows:
    head += "\n\nПока пусто."
  buttons = [
      [
          (
              f"{'✅ ' if r['status'] == 'taken' else ''}#{r['id']}"
              f" {r['give']} → {r['want']}"
          )[:60],
          f"ex:view:{r['id']}",
      ]
      for r in rows
  ]
  buttons = [[(t, d)] for t, d in buttons]
  nav = []
  if page > 0:
    nav.append(("⬅️", f"ex:list:{page - 1}"))
  nav.append((f"{page + 1}/{pages}", "ex:noop"))
  if page < pages - 1:
    nav.append(("➡️", f"ex:list:{page + 1}"))
  buttons.append(nav)
  buttons.append([("🔍 Поиск", "ex:search"), ("🧹 Сбросить", "ex:all")])
  buttons.append([("⬅️ В обмены", "ex:menu")])
  await ex_show(event, head, ex_kb(buttons))


@router.callback_query(F.data == "ex:all")
async def ex_all_cb(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  EX_SEARCH.pop(callback.from_user.id, None)
  await ex_render_list(callback, callback.from_user.id, 0)
  await callback.answer()


@router.callback_query(F.data.startswith("ex:list:"))
async def ex_list_cb(callback: CallbackQuery):
  await ex_render_list(
      callback, callback.from_user.id, int(callback.data.split(":")[2])
  )
  await callback.answer()


@router.callback_query(F.data == "ex:search")
async def ex_search(callback: CallbackQuery, state: FSMContext):
  await state.set_state(ExSearchState.query)
  await ex_show(
      callback,
      "Напиши слово (или два): <b>Пантера</b>, <b>Сердцеед</b>\n"
      "Или ID человека / номер обмена — только цифры.",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExSearchState.query))
async def ex_search_do(message: Message, state: FSMContext):
  await state.clear()
  EX_SEARCH[message.from_user.id] = txt(message)[:60]
  await ex_render_list(message, message.from_user.id, 0)


# --- просмотр, удаление, мои обмены ---


@router.callback_query(F.data.startswith("ex:view:"))
async def ex_view(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  admin = uid == ADMIN_ID
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] not in ("open", "taken"):
    await callback.answer("Этот обмен уже недоступен.", show_alert=True)
    return

  report_kb = ex_kb([
      [("🚨 Пожаловаться", f"ex:confirm_rep:{offer_id}")],
      [("⬅️ Назад", "ex:menu")],
  ])

  if o["status"] == "open":
    text = (
        f"🔄 <b>Обмен #{o['id']}</b>\nОтдаёт: <b>{esc(o['give'])}</b>\nХочет:"
        f" <b>{esc(o['want'])}</b>\nСтоимость обмена: {EXCHANGE_COST} токен"
    )
    if admin:
      text += (
          "\n\n🛠 <b>Админ:</b>\n"
          f"🆔 Обменщик: <code>{o['owner_id']}</code>\n"
          f"🔗 Ссылка:\n{esc(o['owner_link'])}"
      )
    if o["owner_id"] == uid or admin:
      kb = ex_kb(
          [[("🗑 Удалить", f"ex:del:{offer_id}")], [("⬅️ Назад", "ex:list:0")]]
      )
    else:
      kb = ex_kb([
          [("🤝 Провести обмен", f"ex:take:{offer_id}")],
          [("⬅️ Назад", "ex:list:0")],
      ])
  elif uid == o["owner_id"]:
    text = (
        f"✅ <b>Обмен #{o['id']} завершен!</b>\n"
        f"🆔 ID обменщика: <code>{o['taker_id']}</code>\n"
        f"🔗 Ссылка взявшегося:\n{esc(o['taker_link'])}"
    )
    kb = report_kb
  elif uid == o["taker_id"]:
    text = (
        f"✅ <b>Обмен #{o['id']} завершен!</b>\n"
        f"🆔 ID владельца: <code>{o['owner_id']}</code>\n"
        f"🔗 Ссылка владельца:\n{esc(o['owner_link'])}"
    )
    kb = report_kb
  elif admin:
    text = (
        f"✅ <b>Обмен #{o['id']} (завершён)</b>\n"
        f"{esc(o['give'])} → {esc(o['want'])}\n\n"
        f"👤 Владелец: <code>{o['owner_id']}</code>\n{esc(o['owner_link'])}\n\n"
        f"🤝 Взял: <code>{o['taker_id']}</code>\n{esc(o['taker_link'])}"
    )
    kb = ex_kb([[("⬅️ Назад", "ex:list:0")]])
  else:
    await callback.answer("Этот обмен уже недоступен.", show_alert=True)
    return

  await ex_show(callback, text, kb)
  await callback.answer()


@router.callback_query(F.data == "ex:my")
async def ex_my_cb(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  uid = callback.from_user.id
  rows = await ex_all(
      "SELECT * FROM ex_offers WHERE (owner_id = ? OR taker_id = ?) AND status"
      " IN ('open', 'taken') ORDER BY id DESC LIMIT 10",
      (uid, uid),
  )
  if not rows:
    await ex_show(
        callback,
        "📦 У вас нет активных обменов.",
        ex_kb([[("⬅️ В обмены", "ex:menu")]]),
    )
    await callback.answer()
    return
  icon = {"open": "🟢", "taken": "✅"}
  buttons = [
      [(f"{icon[r['status']]} #{r['id']} {r['give']} → {r['want']}"[:60],
        f"ex:view:{r['id']}")]
      for r in rows
  ]
  buttons.append([("⬅️ В обмены", "ex:menu")])
  await ex_show(callback, "📦 <b>Ваши обмены:</b>", ex_kb(buttons))
  await callback.answer()


@router.callback_query(F.data.startswith("ex:del:"))
async def ex_delete_offer(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  rc, _ = await ex_exec(
      "UPDATE ex_offers SET status = 'deleted' WHERE id = ? AND status ="
      " 'open' AND (owner_id = ? OR ? = ?)",
      (offer_id, uid, uid, ADMIN_ID),
  )
  if not rc:
    await callback.answer("Нельзя удалить этот обмен.", show_alert=True)
    return
  await ex_show(callback, "🗑 Обмен удален.", ex_kb([[("🔄 Обмены", "ex:menu")]]))
  await callback.answer()


# --- взять обмен ---


@router.callback_query(F.data.startswith("ex:take:"))
async def ex_take(callback: CallbackQuery, state: FSMContext):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "open":
    await callback.answer("Этот обмен уже забрали или удалили.", show_alert=True)
    return
  if o["owner_id"] == uid:
    await callback.answer("Это твой обмен.", show_alert=True)
    return
  if await get_restriction(uid):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  bal = await ex_one(
      "SELECT COALESCE(tokens, 0) t FROM users WHERE user_id = ?", (uid,)
  )
  if not bal or bal["t"] < EXCHANGE_COST:
    await callback.answer(
        f"Нужно {EXCHANGE_COST} токен для обмена.", show_alert=True
    )
    return
  await state.set_state(ExTakeState.link)
  await state.update_data(offer_id=offer_id)
  await ex_show(
      callback,
      f"Обмен #{offer_id}: <b>{esc(o['give'])}</b> → <b>{esc(o['want'])}</b>\n\n"
      "Отправь <b>свою ссылку</b>. После этого спишется"
      f" {EXCHANGE_COST} токен и ты получишь ссылку обменщика.\n{LINK_RULE}",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExTakeState.link))
async def ex_take_link(message: Message, state: FSMContext, bot: Bot):
  link = txt(message)
  if not LINK_RE.match(link):
    await message.answer(LINK_ERROR)
    return
  uid = message.from_user.id
  offer_id = (await state.get_data())["offer_id"]
  await state.clear()
  if await get_restriction(uid):
    await message.answer(RESTRICTED_TEXT)
    return

  now_ts = int(datetime.datetime.now().timestamp())
  rc, _ = await ex_exec(
      "UPDATE ex_offers SET status = 'taken', taker_id = ?, taker_link = ?,"
      " taken = ? WHERE id = ? AND status = 'open' AND owner_id != ?",
      (uid, link, now_ts, offer_id, uid),
  )
  if not rc:
    await message.answer("Не успел — этот обмен уже забрали.")
    return
  # Списание токена: только если хватает
  rc, _ = await ex_exec(
      "UPDATE users SET tokens = tokens - ? WHERE user_id = ? AND"
      " COALESCE(tokens, 0) >= ?",
      (EXCHANGE_COST, uid, EXCHANGE_COST),
  )
  if not rc:
    await ex_exec(
        "UPDATE ex_offers SET status = 'open', taker_id = NULL,"
        " taker_link = NULL, taken = NULL WHERE id = ?",
        (offer_id,),
    )
    await message.answer(f"Не хватает токенов (нужно {EXCHANGE_COST}).")
    return

  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  await ex_exec(
      "UPDATE users SET exchanges = COALESCE(exchanges, 0) + 1"
      " WHERE user_id IN (?, ?)",
      (o["owner_id"], uid),
  )
  report_kb = ex_kb([[("🚨 Пожаловаться", f"ex:confirm_rep:{offer_id}")]])
  await message.answer(
      f"🤝 <b>Обмен #{offer_id}</b>: {esc(o['give'])} → {esc(o['want'])}\n"
      f"Ссылка обменщика (ID <code>{o['owner_id']}</code>):\n"
      f"{esc(o['owner_link'])}\n\nСписано {EXCHANGE_COST} токен.",
      reply_markup=report_kb,
      disable_web_page_preview=True,
  )
  await safe_send(
      bot,
      o["owner_id"],
      f"🔔 ID <code>{uid}</code> взялся за твой обмен #{offer_id}:"
      f" {esc(o['give'])} → {esc(o['want'])}\nЕго ссылка:\n{esc(link)}",
      reply_markup=report_kb,
      disable_web_page_preview=True,
  )


# --- жалобы ---


@router.callback_query(F.data.startswith("ex:confirm_rep:"))
async def ex_confirm_report(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  text = (
      "⚠️ <b>Вы уверены, что хотите подать жалобу на этого пользователя?</b>"
      "\n\n• При ложной жалобе вы получите <b>1 предупреждение</b>.\n• Если"
      " вас реально обманули (ссылка неверная или закрыта ключом) — смело"
      " отправляйте жалобу."
  )
  kb = ex_kb([
      [("✅ Да, отправить жалобу", f"ex:rep:{offer_id}")],
      [("❌ Отмена", f"ex:view:{offer_id}")],
  ])
  await ex_show(callback, text, kb)
  await callback.answer()


@router.callback_query(F.data.startswith("ex:rep:"))
async def ex_report(callback: CallbackQuery, bot: Bot):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "taken":
    await callback.answer("Жалоба недоступна.", show_alert=True)
    return
  if uid == o["owner_id"]:
    accused = o["taker_id"]
  elif uid == o["taker_id"]:
    accused = o["owner_id"]
  else:
    await callback.answer("Ошибка доступа.", show_alert=True)
    return

  try:
    _, report_id = await ex_exec(
        "INSERT INTO ex_reports (offer_id, complainer_id, accused_id, created)"
        " VALUES (?, ?, ?, ?)",
        (offer_id, uid, accused, int(datetime.datetime.now().timestamp())),
    )
  except aiosqlite.IntegrityError:
    await callback.answer(
        "Ты уже отправил жалобу по этому обмену.", show_alert=True
    )
    return

  until = datetime.datetime.now() + datetime.timedelta(hours=RESTRICT_HOURS)
  await ex_exec(
      "UPDATE users SET restricted_until = ? WHERE user_id = ?",
      (until.strftime(DT_FORMAT), accused),
  )
  await callback.answer("🚨 Жалоба отправлена админу.", show_alert=True)
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass

  await safe_send(
      bot,
      accused,
      f"🚨 На вас подана жалоба по обмену #{offer_id}. Обмены и покупки в"
      " магазине временно ограничены до решения администратора.",
  )
  sent = await safe_send(
      bot,
      ADMIN_ID,
      f"🚨 <b>Жалоба #{report_id}</b>\nОт ID <code>{uid}</code> на ID"
      f" <code>{accused}</code>\n"
      f"Обмен #{offer_id}: {esc(o['give'])} → {esc(o['want'])}\n"
      f"Владелец: <code>{o['owner_id']}</code>\n{esc(o['owner_link'])}\n"
      f"Взявший: <code>{o['taker_id']}</code>\n{esc(o['taker_link'])}\n\n"
      f"Ограничения на ID {accused}: {RESTRICT_HOURS} ч или до твоего"
      " решения.",
      reply_markup=ex_kb([
          [("✅ Ложная жалоба: снять ограничения", f"ex:ok:{report_id}")],
          [
              ("✉️ Жалобщику", f"ex:msg:{report_id}:c"),
              ("✉️ Обвиняемому", f"ex:msg:{report_id}:a"),
          ],
      ]),
      disable_web_page_preview=True,
  )
  if not sent:
    logging.error("Не удалось отправить жалобу админу")


@router.callback_query(F.data.startswith("ex:ok:"))
async def ex_admin_ok(callback: CallbackQuery, bot: Bot):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  report_id = int(callback.data.split(":")[2])
  r = await ex_one("SELECT * FROM ex_reports WHERE id = ?", (report_id,))
  if not r or r["status"] != "new":
    await callback.answer("Уже обработано.", show_alert=True)
    return
  await ex_exec(
      "UPDATE ex_reports SET status = 'released' WHERE id = ?", (report_id,)
  )
  await ex_exec(
      "UPDATE users SET restricted_until = NULL WHERE user_id = ?",
      (r["accused_id"],),
  )
  await safe_send(bot, r["accused_id"], "✅ Ограничения сняты администратором.")

  warns, banned = await add_warn(r["complainer_id"], "Ложная жалоба на обмен")
  await send_warn_notice(bot, r["complainer_id"], warns, banned)
  await callback.answer("Готово.")
  try:
    await callback.message.edit_reply_markup(
        reply_markup=ex_kb([
            [
                ("✉️ Жалобщику", f"ex:msg:{report_id}:c"),
                ("✉️ Обвиняемому", f"ex:msg:{report_id}:a"),
            ]
        ])
    )
  except Exception:
    pass
  await callback.message.answer(
      f"✅ Жалоба #{report_id}: ограничения с ID <code>{r['accused_id']}</code>"
      f" сняты, жалобщику ID <code>{r['complainer_id']}</code> выдано"
      f" предупреждение ({warns}/{MAX_WARNS})."
  )


@router.callback_query(F.data.startswith("ex:msg:"))
async def ex_admin_msg(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  parts = callback.data.split(":")
  report_id = int(parts[2])
  who = parts[3] if len(parts) > 3 else "c"
  r = await ex_one("SELECT * FROM ex_reports WHERE id = ?", (report_id,))
  if not r:
    await callback.answer("Жалоба не найдена.", show_alert=True)
    return
  target = r["complainer_id"] if who == "c" else r["accused_id"]
  await state.set_state(ExAdminMsgState.text)
  await state.update_data(target_id=target, report_id=report_id)
  await callback.message.answer(
      f"Напиши сообщение для ID <code>{target}</code> по жалобе"
      f" #{report_id} (/cancel — отмена):"
  )
  await callback.answer()


@router.message(StateFilter(ExAdminMsgState.text))
async def ex_admin_msg_send(message: Message, state: FSMContext, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    return
  if not txt(message):
    await message.answer("Напиши текстом.")
    return
  data = await state.get_data()
  await state.clear()
  ok = await safe_send(
      bot,
      data["target_id"],
      f"✉️ <b>Сообщение от администрации:</b>\n{esc(txt(message))}",
  )
  await message.answer("Отправлено." if ok else "Не доставлено (бот заблокирован).")


# --- РУЛЕТКА 50/50 ---

ROULETTE_MIN = 2
ROULETTE_MAX = 10
_roulette_locks = {}


def roulette_decide(current_tokens: int, roll_win: bool, aura_percent: int) -> bool:
  # Базовый шанс победы 55%; аура добавляет или отнимает процент
  # -100% → 55-100 = -45 → clamp до 0 (всегда луз)
  # +100% → 55+100 = 155 → clamp до 100 (всегда win)
  base_chance = max(0.0, min(1.0, 0.55 + aura_percent / 100.0))
  roll_win = random.random() < base_chance
  # Скрытый штраф: если накоплено >= 50 токенов — шанс победы падает до 30%
  # (не применяется если аура уже опустила шанс ниже 30%)
  if current_tokens >= 50 and base_chance > 0.30:
    return random.random() < 0.30
  return roll_win


async def roulette_play(user_id: int, bet: int) -> dict:
  """Один раунд. {'error': 'balance', 'balance': N} или {'win': bool, 'balance': N}."""
  # Аура читается ДО открытия транзакции (get_user_aura может писать в БД)
  aura = await get_user_aura(user_id)
  aura_percent = aura["percent"] if (aura and aura["type"] == "roulette") else 0

  lock = _roulette_locks.setdefault(user_id, asyncio.Lock())
  async with lock:
    async with connect() as db:
      cur = await db.execute(
          "UPDATE users SET tokens = tokens - ? WHERE user_id = ? AND"
          " COALESCE(tokens, 0) >= ?",
          (bet, user_id, bet),
      )
      if cur.rowcount == 0:
        async with db.execute(
            "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?",
            (user_id,),
        ) as c:
          row = await c.fetchone()
        return {"error": "balance", "balance": row[0] if row else 0}

      async with db.execute(
          "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
      ) as c:
        current_bal = (await c.fetchone())[0]

      win = roulette_decide(current_bal, random.random() < 0.5, aura_percent)

      await db.execute(
          "INSERT OR IGNORE INTO roulette_state (user_id) VALUES (?)", (user_id,)
      )
      await db.execute(
          "UPDATE roulette_state SET games = COALESCE(games, 0) + 1, wins ="
          " COALESCE(wins, 0) + ? WHERE user_id = ?",
          (1 if win else 0, user_id),
      )
      if win:
        await db.execute(
            "UPDATE users SET tokens = tokens + ? WHERE user_id = ?",
            (bet * 2, user_id),
        )
      async with db.execute(
          "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
      ) as c:
        balance = (await c.fetchone())[0]
      await db.commit()
  return {"win": win, "balance": balance}


def roulette_result_kb(bet: int) -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"🔁 Сыграть снова ({bet} токен.)",
                  callback_data=f"rl_again_{bet}",
              )
          ],
          [InlineKeyboardButton(text="💰 Другая ставка", callback_data="rl_new")],
      ]
  )


async def roulette_intro_text(user_id: int) -> str:
  async with connect() as db:
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
  balance = row[0] if row else 0
  return (
      "🎲 <b>Рулетка 50/50</b>\n\n"
      "Ставишь токены — и либо удваиваешь их, либо теряешь.\n"
      "Выиграл: получаешь ставку ×2 (ставка 4 → на баланс вернётся 8).\n"
      "Проиграл: ставка сгорает.\n\n"
      f"💰 Ставка: от <b>{ROULETTE_MIN}</b> до <b>{ROULETTE_MAX}</b> токенов.\n"
      f"Твой баланс: <b>{balance}</b> токенов.\n\n"
      "✍️ Напиши в чат, сколько токенов ставишь:"
  )


def roulette_result_text(bet: int, res: dict) -> str:
  if res["win"]:
    return (
        f"🎉 <b>Победа!</b> Ставка {bet} → <b>+{bet}</b> токенов.\n"
        f"Баланс: <b>{res['balance']}</b>"
    )
  return (
      f"💥 <b>Проигрыш.</b> Ставка {bet} токенов сгорела.\n"
      f"Баланс: <b>{res['balance']}</b>"
  )


@router.message(StateFilter(RouletteState.bet))
async def roulette_bet(message: Message, state: FSMContext):
  raw = txt(message)
  if not raw.isdigit() or not ROULETTE_MIN <= int(raw) <= ROULETTE_MAX:
    await message.answer(
        f"Напиши число от {ROULETTE_MIN} до {ROULETTE_MAX} — сколько токенов"
        " ставишь."
    )
    return
  bet = int(raw)
  res = await roulette_play(message.from_user.id, bet)
  if "error" in res:
    await message.answer(
        f"❌ Не хватает токенов. Баланс: <b>{res['balance']}</b>. Напиши"
        " ставку поменьше или нажми другую кнопку меню."
    )
    return
  await state.clear()
  await message.answer(
      roulette_result_text(bet, res), reply_markup=roulette_result_kb(bet)
  )


@router.callback_query(F.data.startswith("rl_again_"))
async def roulette_again(callback: CallbackQuery):
  bet = int(callback.data.split("_")[2])
  if not ROULETTE_MIN <= bet <= ROULETTE_MAX:
    await callback.answer()
    return
  res = await roulette_play(callback.from_user.id, bet)
  if "error" in res:
    await callback.answer(
        f"❌ Не хватает токенов. Баланс: {res['balance']}", show_alert=True
    )
    return
  await callback.answer()
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass
  await callback.message.answer(
      roulette_result_text(bet, res), reply_markup=roulette_result_kb(bet)
  )


@router.callback_query(F.data == "rl_new")
async def roulette_new(callback: CallbackQuery, state: FSMContext):
  await state.set_state(RouletteState.bet)
  await callback.answer()
  await callback.message.answer(await roulette_intro_text(callback.from_user.id))


# --- ГЛОБАЛЬНЫЙ ЛОВЕЦ ОШИБОК ---


@router.errors()
async def global_error_handler(event: ErrorEvent):
  logging.exception("Необработанная ошибка: %s", event.exception)


# --- ФОНОВЫЕ ЗАДАЧИ ---


async def send_top_reward(bot: Bot, w_id: int, top_name: str) -> None:
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"👑 Премиум на {TOP_REWARD_PREMIUM_DAYS} дней",
                  callback_data=f"top_reward_{w_id}_prem",
              )
          ],
          [
              InlineKeyboardButton(
                  text=f"💎 {TOP_REWARD_YTOKENS} уникальных токена",
                  callback_data=f"top_reward_{w_id}_ytoken",
              )
          ],
      ]
  )
  await safe_send(
      bot,
      w_id,
      f"🏆 <b>Поздравляем! Вы заняли 1 место в {top_name}!</b>\n\n"
      "Выберите вашу награду:",
      reply_markup=kb,
  )


async def run_top_reset(bot: Bot) -> None:
  async with connect() as db:
    async with db.execute(REF_TOP_SQL, (1,)) as cursor:
      ref_winner = await cursor.fetchone()
    async with db.execute(
        "SELECT user_id FROM users WHERE COALESCE(lottery_wins, 0) > 0"
        " ORDER BY lottery_wins DESC LIMIT 1"
    ) as cursor:
      lot_winner = await cursor.fetchone()
    async with db.execute(
        "SELECT user_id FROM roulette_state WHERE COALESCE(wins, 0) > 0"
        " ORDER BY wins DESC LIMIT 1"
    ) as cursor:
      rl_winner = await cursor.fetchone()

    # Сброс: неподписавшиеся рефералы сохраняются (ref_activated = 0)
    await db.execute(
        "UPDATE users SET referrer_id = 0, ref_income = 0 WHERE"
        " ref_activated = 1"
    )
    await db.execute("UPDATE users SET ref_extra = 0, lottery_wins = 0")
    await db.execute("UPDATE roulette_state SET games = 0, wins = 0")
    new_next = datetime.datetime.now() + datetime.timedelta(days=TOP_RESET_DAYS)
    await db.execute(
        "INSERT OR REPLACE INTO top_reset (id, next_reset) VALUES (1, ?)",
        (new_next.strftime(DT_FORMAT),),
    )
    await db.commit()

  if ref_winner:
    await send_top_reward(bot, ref_winner[0], "топе по рефералам")
  if lot_winner:
    await send_top_reward(bot, lot_winner[0], "топе по лотерее")
  if rl_winner:
    await send_top_reward(bot, rl_winner[0], "топе по рулетке")
  await safe_send(
      bot,
      ADMIN_ID,
      f"🔄 Автосброс всех топов (каждые {TOP_RESET_DAYS} дней) выполнен,"
      " победителям отправлен выбор награды!",
  )


async def top_reset_loop(bot: Bot):
  """Проверяет срок сброса из БД: переживает перезапуски хостинга."""
  while True:
    try:
      async with connect() as db:
        next_reset = await get_next_reset(db)
      if datetime.datetime.now() >= next_reset:
        await run_top_reset(bot)
    except Exception:
      logging.exception("Ошибка при автосбросе топов")
    await asyncio.sleep(300)


async def auto_backup_loop(bot: Bot):
  backup_message_ids = []
  while True:
    await asyncio.sleep(300)
    try:
      if os.path.exists(DB_NAME):
        sent_msg = await bot.send_document(
            ADMIN_ID,
            FSInputFile(DB_NAME),
            caption=(
                "⏱ Авто-бэкап БД:\n"
                f"📅 {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            ),
        )
        backup_message_ids.append(sent_msg.message_id)
        # Храним только 5 последних бэкапов в чате
        if len(backup_message_ids) > 5:
          old_msg_id = backup_message_ids.pop(0)
          try:
            await bot.delete_message(chat_id=ADMIN_ID, message_id=old_msg_id)
          except Exception:
            pass
    except Exception:
      logging.exception("Ошибка при отправке авто-бэкапа")


async def handle_ping(request):
  return web.Response(text="Bot is running!")


async def start_web_server():
  app = web.Application()
  app.router.add_get("/", handle_ping)
  runner = web.AppRunner(app)
  await runner.setup()
  site = web.TCPSite(runner, "0.0.0.0", PORT)
  await site.start()


async def main():
  await init_db()
  await start_web_server()

  if DefaultBotProperties is not None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
  else:
    bot = Bot(token=TOKEN, parse_mode="HTML")

  dp = Dispatcher(storage=MemoryStorage())
  dp.include_router(router)

  tasks = [
      asyncio.create_task(auto_backup_loop(bot)),
      asyncio.create_task(top_reset_loop(bot)),
      asyncio.create_task(lottery_loop(bot)),
  ]

  await bot.delete_webhook(drop_pending_updates=True)
  print(f"Бот успешно запущен, веб-сервер слушает порт {PORT}!")
  try:
    await dp.start_polling(bot)
  finally:
    for task in tasks:
            task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
