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
except ImportError:
  DefaultBotProperties = None

try:
  from zoneinfo import ZoneInfo

  LOCAL_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
  LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=2))

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
  sys.exit("Не задана переменная окружения TOKEN")

PORT = int(os.getenv("PORT", 10000))
DB_NAME = "bot.db"

ADMIN_ID = 5681420389

CHANNEL_ID = -1002925107145
CHANNEL_URL = "https://t.me/iiris_cnn_bot"

DT_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_WARNS = 3
PERM_BAN = "2999-12-31 00:00:00"
REF_BONUS = 7

LOTTERY_HOURS = 3
LOTTERY_NORMAL_CHANCE = 0.80
LOTTERY_BIG_LOW_CHANCE = 0.15
LOTTERY_NOTIFY_PRIZE = 11
LOTTERY_MIN_PLAYERS = 2
LOTTERY_BIG_PRIZE = 15
LOTTERY_BIG_MIN_PLAYERS = 4


def lottery_cost_for_total(total: int) -> int:
  return max(1, total // 5)


def fee_to_prize(paid: int) -> int:
  return (paid + 1) // 2


def cost_text(cost: int) -> str:
  return "бесплатно" if cost == 0 else f"{cost} токен(ов)"


def lottery_min_players(prize: int) -> int:
  return (
      LOTTERY_BIG_MIN_PLAYERS if prize >= LOTTERY_BIG_PRIZE else LOTTERY_MIN_PLAYERS
  )


def hidden_link(link) -> str:
  return f"<tg-spoiler>{esc(link)}</tg-spoiler>"


def esc(value) -> str:
  return html.escape(str(value), quote=False)


def ru_plural(n: int, one: str, few: str, many: str) -> str:
  n_abs = abs(n)
  if n_abs % 10 == 1 and n_abs % 100 != 11:
    word = one
  elif 2 <= n_abs % 10 <= 4 and not 12 <= n_abs % 100 <= 14:
    word = few
  else:
    word = many
  return f"{n} {word}"


def now_local() -> datetime.datetime:
  return datetime.datetime.now(LOCAL_TZ).replace(tzinfo=None)


async def init_db():
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            tokens INTEGER DEFAULT 0,
            y_tokens INTEGER DEFAULT 0,
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
            buyers_count INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS unique_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            amount INTEGER,
            command TEXT
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

    migrations = {
        "users": {
            "tokens": "INTEGER DEFAULT 0",
            "y_tokens": "INTEGER DEFAULT 0",
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
        },
        "lottery_participants": {
            "paid": "INTEGER DEFAULT NULL",
        },
        "lottery_rounds": {
            "cost_fixed": "INTEGER DEFAULT 0",
        },
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

    await db.commit()


class AddProduct(StatesGroup):
  title = State()
  price = State()
  link = State()


class AddUniqueProduct(StatesGroup):
  title = State()
  price = State()
  amount = State()
  command = State()


class EditProduct(StatesGroup):
  product_id = State()
  field = State()
  new_value = State()


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


class GiveTokensState(StatesGroup):
  user_id = State()
  amount = State()


class GiveYTokensState(StatesGroup):
  user_id = State()
  amount = State()


router = Router()


async def get_user_aura(user_id: int):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT aura_type, aura_name, aura_percent, aura_expires FROM users"
        " WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0] or not row[3]:
      return None
    try:
      exp = datetime.datetime.strptime(row[3], DT_FORMAT)
      if exp <= datetime.datetime.now():
        await db.execute(
            "UPDATE users SET aura_type = NULL, aura_name = NULL, aura_percent"
            " = 0, aura_expires = NULL WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return None
      return {"type": row[0], "name": row[1], "percent": row[2], "expires": exp}
    except Exception:
      return None


async def get_active_ban_info(user_id: int):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT banned_until, ban_reason FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0]:
      return None, None
    try:
      until = datetime.datetime.strptime(row[0], DT_FORMAT)
    except Exception:
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
      "INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders,"
      " referrer_id, ref_activated) VALUES (?, 0, 0, 0, 0, 0, 0)"
      " ON CONFLICT(user_id) DO NOTHING",
      (user_id,),
  )


async def ban_user(target_id: int, days: int, reason: str = None) -> str:
  if days > 0:
    until = datetime.datetime.now() + datetime.timedelta(days=days)
    until_str = until.strftime(DT_FORMAT)
    text = f"до {until.strftime('%d.%m.%Y %H:%M')}"
  else:
    until_str = PERM_BAN
    text = "навсегда"
  async with aiosqlite.connect(DB_NAME) as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET banned_until = ?, ban_reason = ? WHERE user_id = ?",
        (until_str, reason, target_id),
    )
    await db.commit()
  return text


async def unban_user(target_id: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE users SET banned_until = NULL, ban_reason = NULL WHERE user_id ="
        " ?",
        (target_id,),
    )
    await db.commit()


async def add_warn(target_id: int, reason: str = None):
  async with aiosqlite.connect(DB_NAME) as db:
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
          " premium_expires = NULL, tokens = 0, y_tokens = 0 WHERE user_id = ?",
          (PERM_BAN, ban_msg, target_id),
      )
      banned = True
    await db.commit()
  return warns, banned


async def remove_warn(target_id: int) -> int:
  async with aiosqlite.connect(DB_NAME) as db:
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


async def remove_premium(target_id: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE users SET is_premium = 0, premium_expires = NULL"
        " WHERE user_id = ?",
        (target_id,),
    )
    await db.commit()


async def remove_tokens(target_id: int, amount: int) -> int:
  async with aiosqlite.connect(DB_NAME) as db:
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


async def is_subscribed(bot: Bot, user_id: int) -> bool:
  try:
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
  except Exception:
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
  async with aiosqlite.connect(DB_NAME) as db:
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
  try:
    await bot.send_message(
        referrer_id,
        "🎉 По вашей рефке зарегистрировался новый пользователь!\n"
        f"❤️ Вам начислено {REF_BONUS} токенов!",
    )
  except Exception:
    pass


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

    until, reason = await get_active_ban_info(user.id)
    if until is not None:
      msg_text = ban_text(until, reason)
      if isinstance(event, CallbackQuery):
        await event.message.answer(msg_text)
        await event.answer()
      else:
        await event.answer(msg_text)
      return None

    if isinstance(event, Message) and (event.text or "").startswith("/start"):
      return await handler(event, data)

    if isinstance(event, CallbackQuery) and event.data == "check_sub":
      return await handler(event, data)

    if await is_subscribed(bot, user.id):
      await activate_referral(bot, user.id)
      return await handler(event, data)

    if isinstance(event, CallbackQuery):
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


async def grant_tokens(target_id: int, amount: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
        INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders, referrer_id, ref_activated)
        VALUES (?, ?, 0, 0, 0, 0, 0)
        ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
        """,
        (target_id, amount, amount),
    )
    await db.commit()


async def grant_y_tokens(target_id: int, amount: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
        INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders, referrer_id, ref_activated)
        VALUES (?, 0, ?, 0, 0, 0, 0)
        ON CONFLICT(user_id) DO UPDATE SET y_tokens = COALESCE(y_tokens, 0) + ?
        """,
        (target_id, amount, amount),
    )
    await db.commit()


async def grant_premium(target_id: int, days: int) -> datetime.datetime:
  now = datetime.datetime.now()
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT is_premium, premium_expires FROM users WHERE user_id = ?",
        (target_id,),
    ) as cursor:
      u_info = await cursor.fetchone()

    base_date = now
    if u_info and u_info[0] == 1 and u_info[1]:
      try:
        curr_exp = datetime.datetime.strptime(u_info[1], DT_FORMAT)
        if curr_exp > now:
          base_date = curr_exp
      except Exception:
        pass

    new_expire = base_date + datetime.timedelta(days=days)
    expire_str = new_expire.strftime(DT_FORMAT)
    await db.execute(
        """
        INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
        VALUES (?, 0, 0, 1, 0, 0, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
        """,
        (target_id, expire_str, expire_str),
    )
    await db.commit()
  return new_expire


async def apply_promocode(user_id: int, code_text: str) -> str:
  code_text = (code_text or "").strip()
  if not code_text:
    return "❌ Промокод не указан."

  try:
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT code, reward_type, reward_value, max_uses, used_count "
          "FROM promocodes WHERE LOWER(code) = LOWER(?)",
          (code_text,),
      ) as cur:
        promo = await cur.fetchone()

      if not promo:
        return "❌ Неверный или несуществующий промокод."

      real_code, reward_type, reward_value, max_uses, used_count = promo

      async with db.execute(
          "SELECT 1 FROM promo_activations WHERE user_id = ? AND"
          " LOWER(code) = LOWER(?)",
          (user_id, real_code),
      ) as cur:
        if await cur.fetchone():
          return "❌ Вы уже активировали этот промокод!"

      if max_uses > 0 and used_count >= max_uses:
        return "❌ У этого промокода закончились активации."

      val = int(reward_value)

      if reward_type == "tokens":
        await db.execute(
            """
            INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders, referrer_id, ref_activated)
            VALUES (?, ?, 0, 0, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
            """,
            (user_id, val, val),
        )
        reply = f"✅ Промокод активирован!\n🎁 Вам зачислено: <b>{val} токенов</b>"

      elif reward_type == "premium":
        async with db.execute(
            "SELECT is_premium, premium_expires FROM users WHERE user_id = ?",
            (user_id,),
        ) as cur:
          u_info = await cur.fetchone()

        now = datetime.datetime.now()
        base_date = now
        if u_info and u_info[0] == 1 and u_info[1]:
          try:
            curr_exp = datetime.datetime.strptime(u_info[1], DT_FORMAT)
            if curr_exp > now:
              base_date = curr_exp
          except Exception:
            pass

        new_expire = base_date + datetime.timedelta(days=val)
        expire_str = new_expire.strftime(DT_FORMAT)
        await db.execute(
            """
            INSERT INTO users (user_id, tokens, y_tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
            VALUES (?, 0, 0, 1, 0, 0, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
            """,
            (user_id, expire_str, expire_str),
        )
        reply = (
            f"✅ Промокод активирован!\n👑 Премиум на <b>{val} дней</b> "
            f"(до {new_expire.strftime('%d.%m.%Y')})!"
        )
      else:
        return "❌ Неизвестный тип награды у промокода."

      await db.execute(
          "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
          (user_id, real_code),
      )
      await db.execute(
          "UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?",
          (real_code,),
      )
      await db.commit()

    return reply

  except Exception as e:
    logging.exception("Ошибка промокода")
    return f"❌ Ошибка обработки: {esc(e)}"


# --- АДМИНСКИЕ КОМАНДЫ (ПОСТАВЛЕНЫ ДО МЕНЮ!) ---


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
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
      existing_user = await cursor.fetchone()

    if existing_user is None:
      ref_to_save = referrer_id if referrer_id else 0
      await db.execute(
          "INSERT INTO users (user_id, tokens, y_tokens, is_premium,"
          " bought_folders, referrer_id, ref_activated, ref_joined_at) VALUES"
          " (?, 0, 0, 0, 0, ?, 0, ?)",
          (user_id, ref_to_save, now_str),
      )
      await db.commit()

  if referrer_id and await is_subscribed(bot, user_id):
    await activate_referral(bot, user_id)

  await message.answer(
      "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре"
      " ниже:",
      reply_markup=get_main_keyboard(user_id),
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


@router.message(Command("aura"))
async def cmd_aura(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  parts = (command.args or "").split()
  if (
      len(parts) >= 1
      and parts[0].lower() == "del"
      and len(parts) == 2
      and parts[1].isdigit()
  ):
    target_id = int(parts[1])
    async with aiosqlite.connect(DB_NAME) as db:
      await db.execute(
          "UPDATE users SET aura_type = NULL, aura_name = NULL, aura_percent ="
          " 0, aura_expires = NULL WHERE user_id = ?",
          (target_id,),
      )
      await db.commit()
    await message.answer(
        f"✅ У пользователя <code>{target_id}</code> успешно аннулирована аура."
    )
    return

  if (
      len(parts) >= 5
      and parts[0].lower() == "add"
      and parts[1].isdigit()
      and parts[3].isdigit()
      and parts[4].isdigit()
  ):
    target_id = int(parts[1])
    aura_type = parts[2].lower()
    if aura_type not in ("lottery", "roulette", "shop"):
      await message.answer(
          "❌ Неверный тип ауры! Доступно: <code>lottery</code>,"
          " <code>roulette</code>, <code>shop</code>"
      )
      return
    percent = int(parts[3])
    days = int(parts[4])
    aura_name = " ".join(parts[5:])
    if not aura_name:
      await message.answer("❌ Укажите название ауры!")
      return

    expires_at = datetime.datetime.now() + datetime.timedelta(days=days)
    expires_str = expires_at.strftime(DT_FORMAT)

    async with aiosqlite.connect(DB_NAME) as db:
      await ensure_user(db, target_id)
      await db.execute(
          "UPDATE users SET aura_type = ?, aura_name = ?, aura_percent = ?,"
          " aura_expires = ? WHERE user_id = ?",
          (aura_type, aura_name, percent, expires_str, target_id),
      )
      await db.commit()

    await message.answer(
        "✨ <b>Аура успешно выдана!</b>\n\n"
        f"🆔 Пользователь: <code>{target_id}</code>\n"
        f"🏷 Название: <b>{esc(aura_name)}</b>\n"
        f"⚙️ Тип: <code>{aura_type}</code>\n"
        f"📊 Бонус: <b>{percent}%</b>\n"
        f"⏱ Срок: <b>{days} дн.</b> (до {expires_at.strftime('%d.%m.%Y %H:%M')})"
    )
    return

  await message.answer(
      "ℹ️ <b>Управление аурами:</b>\n\n"
      "1️⃣ Выдать ауру:\n"
      "<code>/aura add [ID] [lottery/roulette/shop] [процент] [дни] [Название]</code>"
      " \n\n"
      "2️⃣ Удалить ауру:\n"
      "<code>/aura del [ID]</code>"
  )


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


def parse_dt(value):
  try:
    return datetime.datetime.strptime(value, DT_FORMAT) if value else None
  except Exception:
    return None


async def ref_summary(db, uid: int):
  async with db.execute(
      "SELECT COALESCE(SUM(ref_activated = 1), 0),"
      " COALESCE(SUM(ref_activated = 0), 0), COALESCE(SUM(ref_income), 0)"
      " FROM users WHERE referrer_id = ?",
      (uid,),
  ) as c:
    active, pending, income = await c.fetchone()
  async with db.execute(
      "SELECT COALESCE(ref_extra, 0) FROM users WHERE user_id = ?", (uid,)
  ) as c:
    row = await c.fetchone()
  extra = row[0] if row else 0
  return active, pending, extra, income + extra * REF_BONUS


async def uref_add(target_id: int, count: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await ensure_user(db, target_id)
    await db.execute(
        "UPDATE users SET ref_extra = COALESCE(ref_extra, 0) + ?,"
        " tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
        (count, count * REF_BONUS, target_id),
    )
    await db.commit()


async def uref_remove(target_id: int, count):
  removed, taken = 0, 0
  async with aiosqlite.connect(DB_NAME) as db:
    await ensure_user(db, target_id)
    async with db.execute(
        "SELECT COALESCE(ref_extra, 0) FROM users WHERE user_id = ?",
        (target_id,),
    ) as c:
      row = await c.fetchone()
      extra = row[0] if row else 0

    n_extra = extra if count is None else min(count, extra)
    if n_extra:
      await db.execute(
          "UPDATE users SET ref_extra = ref_extra - ? WHERE user_id = ?",
          (n_extra, target_id),
      )
      removed += n_extra
      taken += n_extra * REF_BONUS

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
            "UPDATE users SET referrer_id = 0, ref_income = 0 WHERE user_id = ?",
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
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  usage = (
      "ℹ️ Рефералы:\n"
      "<code>/uref [ID]</code> — список рефералов и доход\n"
      "<code>/uref add [ID] [число]</code> — добавить рефералов\n"
      "<code>/uref del [ID] [число]</code> — убрать N рефералов\n"
      "<code>/uref del [ID] all</code> — убрать всех"
  )
  parts = (command.args or "").split()
  if len(parts) == 1 and parts[0].isdigit():
    target_id = int(parts[0])
    async with aiosqlite.connect(DB_NAME) as db:
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

    lines_str = "\n".join(lines) or "— нет —"
    await message.answer(
        f"👥 <b>Рефералы ID</b> <code>{target_id}</code>\n"
        f"✅ Засчитано: <b>{active + extra}</b> (из них админом: {extra})\n"
        f"⏳ Не подписались: <b>{pending}</b>\n"
        f"💰 Доход: <b>{income}</b> токенов\n\n{lines_str}"
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
      await message.answer(f"✅ Добавлено рефералов: {count}")
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
        f"✅ Убрано рефералов: {removed}, списано дохода: {taken}"
    )
    return
  await message.answer(usage)


@router.message(Command("ytoken"))
async def cmd_ytoken(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if (
      len(parts) == 3
      and parts[0].lower() in ("add", "del")
      and parts[1].isdigit()
      and parts[2].isdigit()
  ):
    action, target_id, amount = parts[0].lower(), int(parts[1]), int(parts[2])
    async with aiosqlite.connect(DB_NAME) as db:
      await ensure_user(db, target_id)
      if action == "add":
        await db.execute(
            "UPDATE users SET y_tokens = COALESCE(y_tokens, 0) + ? WHERE"
            " user_id = ?",
            (amount, target_id),
        )
        await db.commit()
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> начислено"
            f" <b>+{amount}</b> уникальных токенов."
        )
        try:
          await message.bot.send_message(
              target_id,
              f"🎁 Администратор начислил вам <b>+{amount}</b> уникальных"
              " токенов!",
          )
        except Exception:
          pass
      else:
        await db.execute(
            "UPDATE users SET y_tokens = MAX(0, COALESCE(y_tokens, 0) - ?) WHERE"
            " user_id = ?",
            (amount, target_id),
        )
        await db.commit()
        await message.answer(
            f"✅ У пользователя <code>{target_id}</code> списано"
            f" <b>{amount}</b> уникальных токенов."
        )
    return
  await message.answer(
      "ℹ️ Использование:\n<code>/ytoken add [ID] [количество]</code>\n<code>/ytoken"
      " del [ID] [количество]</code>"
  )


@router.message(Command("check"))
async def cmd_check(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer("ℹ️ Использование:\n<code>/check [ID]</code>")
    return
  target_id = int(parts[0])
  now = datetime.datetime.now()
  async with aiosqlite.connect(DB_NAME) as db:
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
      lot_n, lot_paid = await c.fetchone()
    async with db.execute(
        "SELECT COALESCE(games, 0), COALESCE(wins, 0) FROM roulette_state"
        " WHERE user_id = ?",
        (target_id,),
    ) as c:
      rl = await c.fetchone()

  rl_games, rl_wins = rl if rl else (0, 0)
  prem_exp = parse_dt(u["premium_expires"])
  if u["is_premium"] == 1 and (prem_exp is None or prem_exp > now):
    prem = (
        f"✅ до {prem_exp.strftime('%d.%m.%Y %H:%M')}"
        if prem_exp
        else "✅ активна"
    )
  else:
    prem = "❌ нет"

  aura = await get_user_aura(target_id)
  aura_info = (
      f"✨ {aura['name']} ({aura['type']}, {aura['percent']}%) до"
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

  await message.answer(
      f"🔎 <b>Аккаунт</b> <code>{target_id}</code>\n\n"
      f"🪙 Токены: <b>{u['tokens'] or 0}</b>\n"
      f"💎 Уникальные токены: <b>{u['y_tokens'] or 0}</b>\n"
      f"👑 Премиум: {prem}\n"
      f"✨ Аура: {aura_info}\n"
      f"⚠️ Предупреждения: {u['warns'] or 0}/{MAX_WARNS}\n"
      f"🚫 Бан: {until_text(u['banned_until'])}\n\n"
      f"👥 Рефералы: {active + extra} (доход: {income})\n"
      f"🎰 Лотерея побед: {u.get('lottery_wins', 0)}\n"
      f"🎲 Рулетка побед: {rl_wins} из {rl_games}"
  )


@router.message(Command("premium", "p"))
async def cmd_premium(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  args = (command.args or "").split()
  if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
    await message.answer("ℹ️ Использование: <code>/p [дни] [ID]</code>")
    return
  days, target_id = int(args[0]), int(args[1])
  new_expire = await grant_premium(target_id, days)
  formatted_date = new_expire.strftime("%d.%m.%Y")
  await message.answer(
      f"✅ Премиум выдан пользователю <code>{target_id}</code> на {days} дн."
  )
  try:
    await bot.send_message(
        target_id, f"🎉 Вам активирована Премиум-подписка на {days} дней!"
    )
  except Exception:
    pass


@router.message(Command("token", "tokens", "t"))
async def cmd_token(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  args = (command.args or "").split()
  if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
    await message.answer("ℹ️ Использование: <code>/t [сколько] [ID]</code>")
    return
  amount, target_id = int(args[0]), int(args[1])
  await grant_tokens(target_id, amount)
  await message.answer(
      f"✅ Выдано +{amount} токенов пользователю <code>{target_id}</code>."
  )


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
    await message.answer(
        "ℹ️ Использование: <code>/ban [ID] [дни] [причина]</code>"
    )
    return
  target_id, days = int(parts[0]), int(parts[1])
  reason = " ".join(parts[2:]) if len(parts) > 2 else None
  term = await ban_user(target_id, days, reason)
  await message.answer(
      f"🚫 Пользователь <code>{target_id}</code> заблокирован {term}."
  )


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer("ℹ️ Использование: <code>/unban [ID]</code>")
    return
  target_id = int(parts[0])
  await unban_user(target_id)
  await message.answer(f"✅ Пользователь <code>{target_id}</code> разблокирован.")


@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) < 1 or not parts[0].isdigit():
    await message.answer("ℹ️ Использование: <code>/warn [ID] [причина]</code>")
    return
  target_id = int(parts[0])
  reason = " ".join(parts[1:]) if len(parts) > 1 else None
  warns, banned = await add_warn(target_id, reason)
  await message.answer(
      f"⚠️ Предупреждение выдано пользователю <code>{target_id}</code>"
      f" ({warns}/{MAX_WARNS})."
  )


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer("ℹ️ Использование: <code>/unwarn [ID]</code>")
    return
  warns = await remove_warn(int(parts[0]))
  await message.answer(
      f"✅ Предупреждение снято (осталось {warns}/{MAX_WARNS})."
  )


def tops_menu_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="👥 Топ по рефералам", callback_data="top_refs"
              )
          ],
          [
              InlineKeyboardButton(
                  text="🎰 Топ по лотерее", callback_data="top_lottery"
              )
          ],
          [
              InlineKeyboardButton(
                  text="🎲 Топ по рулетке", callback_data="top_roulette"
              )
          ],
      ]
  )


@router.callback_query(F.data.startswith("top_"))
async def show_specific_top(callback: CallbackQuery):
  top_type = callback.data.split("_")[1]
  async with aiosqlite.connect(DB_NAME) as db:
    if top_type == "refs":
      async with db.execute(REF_TOP_SQL, (10,)) as cursor:
        rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по рефералам</b>"
      footer = (
          "<i>(каждые 14 дней топ сбрасывается, 1 место получает Премиум на 14"
          " дней)</i>"
      )
      items = [
          f"{i}. Пользователь <code>{r[0]}</code>{' 👑' if r[1] == 1 else ''} —"
          f" <b>{r[2]}</b> реф."
          for i, r in enumerate(rows, 1)
      ]

    elif top_type == "lottery":
      async with db.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
      if "lottery_wins" not in cols:
        rows = []
      else:
        async with db.execute(
            "SELECT user_id, is_premium, COALESCE(lottery_wins, 0) as lw FROM"
            " users WHERE lw > 0 ORDER BY lw DESC LIMIT 10"
        ) as cursor:
          rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по победам в лотерее</b>"
      footer = (
          "<i>(каждые 14 дней топ сбрасывается, 1 место получает Премиум на 14"
          " дней)</i>"
      )
      items = [
          f"{i}. Пользователь <code>{r[0]}</code>{' 👑' if r[1] == 1 else ''} —"
          f" <b>{r[2]}</b> побед"
          for i, r in enumerate(rows, 1)
      ]

    else:  # roulette
      async with db.execute(
          "SELECT u.user_id, u.is_premium, COALESCE(r.wins, 0) as w FROM users"
          " u LEFT JOIN roulette_state r ON u.user_id = r.user_id WHERE w > 0"
          " ORDER BY w DESC LIMIT 10"
      ) as cursor:
        rows = await cursor.fetchall()
      title = "🏆 <b>Топ-10 по победам в рулетке</b>"
      footer = (
          "<i>(каждые 14 дней топ сбрасывается, 1 место получает Премиум на 14"
          " дней)</i>"
      )
      items = [
          f"{i}. Пользователь <code>{r[0]}</code>{' 👑' if r[1] == 1 else ''} —"
          f" <b>{r[2]}</b> побед"
          for i, r in enumerate(rows, 1)
      ]

  text = (
      title
      + "\n\n"
      + ("\n".join(items) if items else "Пока пусто.")
      + f"\n\n{footer}"
  )
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
  kb = tops_menu_keyboard()
  try:
    await callback.message.edit_text(
        "🏆 <b>Выберите категорию топа:</b>", reply_markup=kb
    )
  except Exception:
    pass
  await callback.answer()


# --- ОБРАБОТЧИК КНОПОК ГЛАВНОГО МЕНЮ (СПУЩЕН НИЖЕ КОМАНД!) ---


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
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT tokens, y_tokens, is_premium, bought_folders, premium_expires,"
          " warns, COALESCE(exchanges, 0) FROM users WHERE user_id = ?",
          (user_id,),
      ) as cursor:
        user_data = await cursor.fetchone()

      if not user_data:
        await ensure_user(db, user_id)
        tokens, y_tokens, is_prem, bought, prem_expires, warns, exchanges = (
            0,
            0,
            0,
            0,
            None,
            0,
            0,
        )
      else:
        tokens, y_tokens, is_prem, bought, prem_expires, warns, exchanges = (
            user_data
        )

    prem_status = "❌ Не активна"
    if is_prem == 1 and prem_expires:
      try:
        exp_date = datetime.datetime.strptime(prem_expires, DT_FORMAT)
        if datetime.datetime.now() < exp_date:
          days_left = (exp_date - datetime.datetime.now()).days
          formatted_date = exp_date.strftime("%d.%m.%Y")
          prem_status = f"✅ До {formatted_date} ({days_left} дн.)"
        else:
          async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE users SET is_premium = 0, premium_expires = NULL WHERE"
                " user_id = ?",
                (user_id,),
            )
            await db.commit()
          prem_status = "❌ Не активна (истекла)"
      except Exception:
        prem_status = "✅ Активна"

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
    profile_text = (
        f"👤 <b>Ваш Профиль</b>\n\n🆔 Ваш ID: <code>{user_id}</code>\n"
        f"💲 Баланс токенов: {tokens if tokens is not None else 0}\n"
        f"💎 Уникальные токены: {y_tokens if y_tokens is not None else 0}\n"
        f"👑 Премиум-подписка: {prem_status}\n"
        f"✨ Активная аура: {aura_status}\n"
        f"📦 Собрано фулов: {bought} шт.\n"
        f"⚠️ Предупреждения: {warns or 0}/{MAX_WARNS}\n"
        f"🔄 Обменов проведено: {exchanges}"
    )
    await message.answer(profile_text, reply_markup=keyboard)

  elif text in ("🛒 Магазин", "🛍 Магазин"):
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT id, title, price, buyers_count FROM products"
      ) as cursor:
        products = await cursor.fetchall()

    if not products:
      await message.answer("📭 В магазине пока пусто.")
      return

    keyboard_buttons = []
    for prod in products:
      p_id, title, price, buyers_count = prod
      btn_text = (
          f"🔥 {title} — {price} токенов (ПЕРВАЯ ПОКУПКА -80%!)"
          if buyers_count == 0
          else f"📁 {title} — {price} токенов"
      )
      keyboard_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"buy_{p_id}")]
      )

    await message.answer(
        "🛍 <b>Доступные фулы:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )

  elif text == "💎 Уникальный магазин":
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT id, title, price, amount FROM unique_products WHERE amount > 0"
      ) as cursor:
        u_products = await cursor.fetchall()

    if not u_products:
      await message.answer("📭 Уникальный магазин пуст.")
      return

    kb_buttons = []
    for up in u_products:
      up_id, title, price, amount = up
      kb_buttons.append([
          InlineKeyboardButton(
              text=f"💎 {title} — {price} у.т. (осталось: {amount})",
              callback_data=f"ubuy_{up_id}",
          )
      ])

    await message.answer(
        "💎 <b>Уникальный магазин</b>\nПокупайте уникальные предметы за"
        " уникальные токены:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_text = (
        "👥 <b>Реферальная система</b>\n\nПриглашай друзей по своей ссылке:\n"
        f"<code>{ref_link}</code>\n\n🎁 За каждого приглашенного — {REF_BONUS}"
        " токенов!"
    )
    await message.answer(ref_text)

  elif text == "🏆 Топ игроков":
    await message.answer(
        "🏆 <b>Выберите категорию топа:</b>", reply_markup=tops_menu_keyboard()
    )

  elif text == "🪙 Как получить токены?":
    tokens_text = (
        "Как получить токены?\n\n"
        "1. Возьмите фото. https://t.me/JmcDeveloper/11\n"
        "2. Оставляйте в комментариях под постами.\n"
        "3. Отправляйте скриншоты в директ канала.\n\n"
        "💰 1 отправка фото = 1 токен!"
    )
    await message.answer(tokens_text, disable_web_page_preview=True)

  elif text == "👑 Премиум-подписка":
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Отправить Подарок 50 Stars (@ucu52)",
                    url="https://t.me/ucu52",
                )
            ]
        ]
    )
    prem_text = (
        "👑 <b>Премиум-подписка на 1 МЕСЯЦ (30 дней)</b>\n\n"
        "Дает право покупать АБСОЛЮТНО ЛЮБЫЕ фулы бесплатно!\n\n"
        "💳 <b>Способ 1:</b> 50 Звезд (любым подарком на профиль @ucu52).\n\n"
        "📸 <b>Способ 2 (за 100 фото):</b>\n"
        "Вы можете получить премиум бесплатно за 100 уникальных фоток! "
        "<i>Если была хотя бы одна фото дублирована или уже принята как Токен —"
        " подписка не выдается.</i>\n"
        "Если вы сделали все хорошо, отправляйте сюда:"
        " https://t.me/JmcDeveloper?direct <b>(И СРАЗУ ГОВОРИТЕ ЧТО ДЛЯ ПРЕМИУМ"
        " ПОДПИСКИ)</b>"
    )
    await message.answer(prem_text, reply_markup=keyboard)

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
    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить фулл", callback_data="add_product"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Добавить в уник. магазин",
                    callback_data="add_unique_product",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать товар",
                    callback_data="edit_product_list",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить товар", callback_data="delete_product_list"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Создать промокод", callback_data="admin_create_promo"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪙 Выдать токены", callback_data="admin_give_tokens"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Выдать уник. токены",
                    callback_data="admin_give_ytokens",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Выдать Премиум",
                    callback_data="admin_give_premium",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Сделать рассылку", callback_data="start_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💾 Скачать бэкап БД", callback_data="admin_backup_db"
                )
            ],
        ]
    )
    await message.answer("⚙️ Панель администратора:", reply_markup=admin_keyboard)


@router.callback_query(F.data.startswith("ubuy_"))
async def process_ubuy(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT title, price, amount, command FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[2] <= 0:
      await callback.answer("❌ Товар закончился или удален.", show_alert=True)
      return
    title, price, amount, command_str = prod

    async with db.execute(
        "SELECT COALESCE(y_tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
    my_ytokens = row[0] if row else 0

    if my_ytokens < price:
      await callback.answer(
          f"❌ Недостаточно уникальных токенов! Нужно: {price}, у вас:"
          f" {my_ytokens}.",
          show_alert=True,
      )
      return

    await db.execute(
        "UPDATE users SET y_tokens = y_tokens - ? WHERE user_id = ?",
        (price, user_id),
    )
    await db.execute(
        "UPDATE unique_products SET amount = amount - 1 WHERE id = ?", (up_id,)
    )
    await db.commit()

  try:
    cmd_parts = command_str.strip().split()
    if cmd_parts and cmd_parts[0].lower() == "aura":
      if len(cmd_parts) >= 4:
        aura_type = cmd_parts[1].lower()
        percent = int(cmd_parts[2])
        days = int(cmd_parts[3])
        aura_name = " ".join(cmd_parts[4:]) if len(cmd_parts) > 4 else title

        expires_at = datetime.datetime.now() + datetime.timedelta(days=days)
        expires_str = expires_at.strftime(DT_FORMAT)
        async with aiosqlite.connect(DB_NAME) as db:
          await db.execute(
              "UPDATE users SET aura_type = ?, aura_name = ?, aura_percent = ?,"
              " aura_expires = ? WHERE user_id = ?",
              (aura_type, aura_name, percent, expires_str, user_id),
          )
          await db.commit()
  except Exception:
    logging.exception("Ошибка выполнения команды товара уник. магазина")

  await callback.message.answer(
      f"💎 <b>Успешная покупка!</b>\nВы приобрели: <b>{esc(title)}</b> за {price}"
      " уникальных токенов."
  )
  await callback.answer()


@router.callback_query(F.data == "add_unique_product")
async def start_add_unique_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer(
      "💎 Введите название товара (например: Аура Оптовик):"
  )
  await state.set_state(AddUniqueProduct.title)
  await callback.answer()


@router.message(StateFilter(AddUniqueProduct.title))
async def process_u_title(message: Message, state: FSMContext):
  await state.update_data(title=message.text)
  await message.answer(
      "💰 Введите цену в <b>уникальных токенах</b> (например: 10):"
  )
  await state.set_state(AddUniqueProduct.price)


@router.message(StateFilter(AddUniqueProduct.price))
async def process_u_price(message: Message, state: FSMContext):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Введите число!")
    return
  await state.update_data(price=int(text))
  await message.answer("📦 Введите количество (штук в наличии, например: 5):")
  await state.set_state(AddUniqueProduct.amount)


@router.message(StateFilter(AddUniqueProduct.amount))
async def process_u_amount(message: Message, state: FSMContext):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Введите число!")
    return
  await state.update_data(amount=int(text))
  await message.answer(
      "⚙️ Введите команду/действие при покупке (например: <code>aura shop 15 30"
      " Оптовик</code>):"
  )
  await state.set_state(AddUniqueProduct.command)


@router.message(StateFilter(AddUniqueProduct.command))
async def process_u_command(message: Message, state: FSMContext):
  cmd = (message.text or "").strip()
  data = await state.get_data()
  await state.clear()

  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "INSERT INTO unique_products (title, price, amount, command) VALUES (?,"
        " ?, ?, ?)",
        (data["title"], data["price"], data["amount"], cmd),
    )
    await db.commit()

  await message.answer("✅ Товар успешно добавлен в уникальный магазин!")


@router.message(StateFilter(PromocodeState.code))
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = (message.text or "").strip()
  await state.clear()
  reply = await apply_promocode(user_id, code_text)
  await message.answer(reply, reply_markup=get_main_keyboard(user_id))


@router.callback_query(F.data == "admin_give_tokens")
async def start_give_tokens(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(GiveTokensState.user_id)
  await callback.message.answer(
      "🪙 Введи Telegram ID пользователя для начисления токенов:"
  )
  await callback.answer()


@router.callback_query(F.data == "admin_give_ytokens")
async def start_give_ytokens(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(GiveYTokensState.user_id)
  await callback.message.answer(
      "💎 Введи Telegram ID пользователя для начисления <b>уникальных"
      " токенов</b>:"
  )
  await callback.answer()


@router.callback_query(F.data == "admin_give_premium")
async def start_give_premium(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await state.set_state(GivePremiumState.user_id)
  await callback.message.answer(
      "👑 Введи Telegram ID пользователя для выдачи Премиума:"
  )
  await callback.answer()


@router.message(StateFilter(GiveTokensState.user_id))
async def process_give_tokens_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Только цифры.")
    return
  await state.update_data(target_id=int(message.text))
  await state.set_state(GiveTokensState.amount)
  await message.answer("🪙 Сколько токенов начислить?")


@router.message(StateFilter(GiveYTokensState.user_id))
async def process_give_ytokens_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Только цифры.")
    return
  await state.update_data(target_id=int(message.text))
  await state.set_state(GiveYTokensState.amount)
  await message.answer("💎 Сколько уникальных токенов начислить?")


@router.message(StateFilter(GiveYTokensState.amount))
async def process_give_ytokens_amount(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Введите число.")
    return
  amount = int(message.text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()
  await grant_y_tokens(target_id, amount)
  await message.answer(
      f"✅ Пользователю <code>{target_id}</code> начислено +{amount} уник."
      " токенов."
  )


@router.message(StateFilter(GiveTokensState.amount))
async def process_give_tokens_amount(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Введите число.")
    return
  amount = int(message.text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()
  await grant_tokens(target_id, amount)
  await message.answer(
      f"✅ Начислено +{amount} токенов пользователю <code>{target_id}</code>."
  )


@router.message(StateFilter(GivePremiumState.user_id))
async def process_give_premium_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Только цифры.")
    return
  await state.update_data(target_id=int(message.text))
  await state.set_state(GivePremiumState.days)
  await message.answer("⏳ На сколько дней выдать Премиум?")


@router.message(StateFilter(GivePremiumState.days))
async def process_give_premium_days(
    message: Message, state: FSMContext, bot: Bot
):
  if not message.text.isdigit():
    await message.answer("❌ Введите число дней.")
    return
  days = int(message.text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()
  new_expire = await grant_premium(target_id, days)
  await message.answer(f"✅ Премиум выдан на {days} дней.")
  try:
    await bot.send_message(
        target_id, f"🎉 Вам активирована Премиум-подписка на {days} дней!"
    )
  except Exception:
    pass


@router.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  p_id = int(callback.data.split("_")[1])

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT title, price, link, buyers_count FROM products WHERE id = ?",
        (p_id,),
    ) as cursor:
      product = await cursor.fetchone()

    if not product:
      await callback.answer("❌ Товар удален.", show_alert=True)
      return

    title, price_raw, link, buyers_count = product
    try:
      base_price = int("".join(filter(str.isdigit, str(price_raw))))
    except ValueError:
      base_price = 0

    async with db.execute(
        "SELECT tokens, is_premium, referrer_id, premium_expires FROM users"
        " WHERE user_id = ?",
        (user_id,),
    ) as cursor:
      u_data = await cursor.fetchone()

    tokens = u_data[0] if (u_data and u_data[0] is not None) else 0
    is_prem = u_data[1] if u_data else 0
    referrer_id = u_data[2] if u_data else 0
    prem_expires = u_data[3] if u_data else None

    if is_prem == 1:
      await db.execute(
          "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
          (p_id,),
      )
      await db.commit()
      text = (
          f"👑 Премиум-доступ!\n\n✅ Фулл {esc(title)}"
          f" бесплатно!\n\n🔗 Ссылка:\n{hidden_link(link)}"
      )
      await callback.message.answer(text, disable_web_page_preview=True)
      await callback.answer()
      return

    final_price = (
        max(1, int(base_price * 0.2)) if buyers_count == 0 else base_price
    )

    aura = await get_user_aura(user_id)
    if aura and aura["type"] == "shop":
      discount_percent = aura["percent"]
      final_price = max(
          1, int(round(final_price * (1 - discount_percent / 100.0)))
      )

    if tokens < final_price:
      await callback.answer(
          f"❌ Недостаточно токенов! Нужно: {final_price}.", show_alert=True
      )
      return

    await db.execute(
        "UPDATE users SET tokens = tokens - ?, bought_folders ="
        " COALESCE(bought_folders, 0) + 1 WHERE user_id = ?",
        (final_price, user_id),
    )
    await db.execute(
        "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
        (p_id,),
    )
    await db.commit()

  await callback.message.answer(
      f"✅ Спасибо за покупку!\n\n🔗 Ссылка:\n{hidden_link(link)}",
      disable_web_page_preview=True,
  )
  await callback.answer()


EXCHANGE_COST = 1
RESTRICT_HOURS = 72
MAX_OPEN_OFFERS = 5
EX_PAGE_SIZE = 5
EX_SEARCH = {}
LINK_RE = re.compile(
    r"^(https://mega\.nz/(file|folder)/\S+|https://cloud\.mail\.ru/\S+)$", re.I
)
LINK_RULE = "⚠️ Ссылка должна быть с mega.nz или cloud.mail.ru"
LINK_ERROR = "❌ Неверная ссылка."


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


async def ex_exec(sql, args=()):
  async with aiosqlite.connect(DB_NAME) as db:
    cur = await db.execute(sql, args)
    await db.commit()
    return cur.rowcount, cur.lastrowid


async def ex_one(sql, args=()):
  async with aiosqlite.connect(DB_NAME) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(sql, args) as cur:
      return await cur.fetchone()


async def ex_all(sql, args=()):
  async with aiosqlite.connect(DB_NAME) as db:
    db.row_factory = aiosqlite.Row
    async with db.execute(sql, args) as cur:
      return await cur.fetchall()


async def get_restriction(user_id: int):
  row = await ex_one(
      "SELECT restricted_until FROM users WHERE user_id = ?", (user_id,)
  )
  if not row or not row["restricted_until"]:
    return None
  try:
    until = datetime.datetime.strptime(row["restricted_until"], DT_FORMAT)
  except Exception:
    return None
  if until <= datetime.datetime.now():
    await ex_exec(
        "UPDATE users SET restricted_until = NULL WHERE user_id = ?",
        (user_id,),
    )
    return None
  return until


RESTRICTED_TEXT = "🔒 На вас подана жалоба. Обмены и магазин ограничены."
RESTRICTED_ALERT = "🔒 Вы ограничены от обменов."


def ex_kb(rows) -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
          for row in rows
      ]
  )


async def ex_show(event, text, kb=None):
  if isinstance(event, CallbackQuery):
    try:
      await event.message.edit_text(
          text, reply_markup=kb, disable_web_page_preview=True
      )
      return
    except TelegramBadRequest:
      pass
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
      "🔄 <b>Обмены фуллами</b>\nСтоимость проведения обмена:"
      f" {EXCHANGE_COST} токен."
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
        f"У вас уже {MAX_OPEN_OFFERS} открытых обменов.", show_alert=True
    )
    return
  await state.set_state(NewOfferState.give)
  await ex_show(
      callback, "Что вы <b>отдаете</b>?", ex_kb([[("❌ Отмена", "ex:cancel")]])
  )
  await callback.answer()


@router.message(StateFilter(NewOfferState.give))
async def ex_new_give(message: Message, state: FSMContext):
  text = (message.text or "").strip()[:60]
  if not text:
    return
  await state.update_data(give=text)
  await state.set_state(NewOfferState.want)
  await message.answer(
      "Что хотите <b>получить</b>?",
      reply_markup=ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.want))
async def ex_new_want(message: Message, state: FSMContext):
  text = (message.text or "").strip()[:60]
  if not text:
    return
  await state.update_data(want=text)
  await state.set_state(NewOfferState.link)
  await message.answer(
      f"Отправьте <b>ссылку на свой фулл</b>:\n{LINK_RULE}",
      reply_markup=ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.link))
async def ex_new_link(message: Message, state: FSMContext):
  link = (message.text or "").strip()
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
      f"✅ Обмен #{offer_id} выставлен!",
      reply_markup=ex_kb([[("🔄 Обмены", "ex:menu")]]),
  )


async def ex_render_list(event, user_id: int, page: int):
  q = EX_SEARCH.get(user_id)
  where, args = "status = 'open'", []
  if q:
    for w in q.lower().split()[:3]:
      where += " AND search LIKE ?"
      args.append(f"%{w}%")
  total = (
      await ex_one(f"SELECT COUNT(*) n FROM ex_offers WHERE {where}", args)
  )["n"]
  pages = max(1, -(-total // EX_PAGE_SIZE))
  page = max(0, min(page, pages - 1))
  rows = await ex_all(
      f"SELECT * FROM ex_offers WHERE {where} ORDER BY id DESC LIMIT ? OFFSET"
      " ?",
      args + [EX_PAGE_SIZE, page * EX_PAGE_SIZE],
  )
  head = f"📋 <b>Открытые обмены</b> (стр. {page + 1}/{pages})"
  buttons = [
      [(f"#{r['id']} {r['give']} → {r['want']}"[:60], f"ex:view:{r['id']}")]
      for r in rows
  ]
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
      "Введите название для поиска:",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExSearchState.query))
async def ex_search_do(message: Message, state: FSMContext):
  await state.clear()
  EX_SEARCH[message.from_user.id] = (message.text or "").strip()[:60]
  await ex_render_list(message, message.from_user.id, 0)


@router.callback_query(F.data.startswith("ex:view:"))
async def ex_view(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] not in ("open", "taken"):
    await callback.answer("Обмен недоступен.", show_alert=True)
    return

  if o["status"] == "open":
    text = (
        f"🔄 <b>Обмен #{o['id']}</b>\nОтдает: <b>{esc(o['give'])}</b>\nХочет:"
        f" <b>{esc(o['want'])}</b>"
    )
    if o["owner_id"] == uid:
      kb = ex_kb(
          [[("🗑 Удалить", f"ex:del:{offer_id}")], [("⬅️ Назад", "ex:list:0")]]
      )
    else:
      kb = ex_kb([
          [("🤝 Провести обмен", f"ex:take:{offer_id}")],
          [("⬅️ Назад", "ex:list:0")],
      ])
  else:
    if uid == o["owner_id"]:
      text = (
          f"✅ <b>Обмен #{o['id']} завершен!</b>\nСсылка"
          f" взявшегося:\n{esc(o['taker_link'])}"
      )
      kb = ex_kb([
          [("🚨 Пожаловаться", f"ex:confirm_rep:{offer_id}")],
          [("⬅️ Назад", "ex:menu")],
      ])
    elif uid == o["taker_id"]:
      text = (
          f"✅ <b>Обмен #{o['id']} завершен!</b>\nСсылка"
          f" владельца:\n{esc(o['owner_link'])}"
      )
      kb = ex_kb([
          [("🚨 Пожаловаться", f"ex:confirm_rep:{offer_id}")],
          [("⬅️ Назад", "ex:menu")],
      ])
    else:
      return

  await ex_show(callback, text, kb)
  await callback.answer()


@router.callback_query(F.data.startswith("ex:confirm_rep:"))
async def ex_confirm_report(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  text = (
      "⚠️ <b>Вы уверены, что хотите кинуть жалобу на данного"
      " пользователя?</b>\n\n• При ложном репорте вы получите <b>1"
      " Предупреждение</b>.\n• Если вас реально обманули и ссылка оказалась"
      " неверной или есть ключ, то отправляйте смело жалобу и получите токены"
      " за моральный ущерб."
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
        "Вы уже отправляли жалобу по этому обмену.", show_alert=True
    )
    return

  until = datetime.datetime.now() + datetime.timedelta(hours=RESTRICT_HOURS)
  await ex_exec(
      "UPDATE users SET restricted_until = ? WHERE user_id = ?",
      (until.strftime(DT_FORMAT), accused),
  )
  await callback.answer(
      "🚨 Жалоба успешно отправлена администратору.", show_alert=True
  )

  try:
    await bot.send_message(
        accused,
        "🚨 На вас подана жалоба по обмену. Обмены временно ограничены.",
    )
  except Exception:
    pass

  try:
    await bot.send_message(
        ADMIN_ID,
        f"🚨 <b>Жалоба #{report_id}</b>\nОт ID <code>{uid}</code> на ID"
        f" <code>{accused}</code>\nОбмен #{offer_id}: {esc(o['give'])} →"
        f" {esc(o['want'])}",
        reply_markup=ex_kb([
            [
                (
                    "✅ Снять ограничения (ложная жалоба)",
                    f"ex:ok:{report_id}",
                )
            ],
            [("✉️ Написать пользователю", f"ex:msg:{report_id}")],
        ]),
        disable_web_page_preview=True,
    )
  except Exception:
    pass


@router.callback_query(F.data.startswith("ex:ok:"))
async def ex_admin_ok(callback: CallbackQuery, bot: Bot):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  report_id = int(callback.data.split(":")[2])
  r = await ex_one("SELECT * FROM ex_reports WHERE id = ?", (report_id,))
  if not r:
    await callback.answer("Не найдено.", show_alert=True)
    return
  await ex_exec(
      "UPDATE ex_reports SET status = 'released' WHERE id = ?", (report_id,)
  )
  await ex_exec(
      "UPDATE users SET restricted_until = NULL WHERE user_id = ?",
      (r["accused_id"],),
  )

  warns, banned = await add_warn(r["complainer_id"], "Ложная жалоба на обмен")
  try:
    await bot.send_message(
        r["complainer_id"],
        f"⚠️ Ваша жалоба признана ложной. Вы получили предупреждение"
        f" ({warns}/{MAX_WARNS}).",
    )
  except Exception:
    pass

  await callback.answer("Готово.")
  await callback.message.answer(
      f"✅ Жалоба #{report_id} закрыта. Жаловшемуся выдано предупреждение."
  )


@router.callback_query(F.data.startswith("ex:msg:"))
async def ex_admin_msg(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  report_id = int(callback.data.split(":")[2])
  await state.set_state(ExAdminMsgState.text)
  await state.update_data(report_id=report_id)
  await callback.message.answer("Напишите текст сообщения:")
  await callback.answer()


@router.callback_query(F.data.startswith("ex:take:"))
async def ex_take(callback: CallbackQuery, state: FSMContext):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "open":
    await callback.answer("Этот обмен уже забрали.", show_alert=True)
    return
  if o["owner_id"] == uid:
    await callback.answer("Это ваш обмен.", show_alert=True)
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
      f"Отправьте <b>ссылку на свой фулл</b>:\n{LINK_RULE}",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExTakeState.link))
async def ex_take_link(message: Message, state: FSMContext, bot: Bot):
  link = (message.text or "").strip()
  if not LINK_RE.match(link):
    await message.answer(LINK_ERROR)
    return
  uid = message.from_user.id
  offer_id = (await state.get_data())["offer_id"]
  await state.clear()

  rc, _ = await ex_exec(
      "UPDATE ex_offers SET status = 'taken', taker_id = ?, taker_link = ?,"
      " taken = ? WHERE id = ? AND status = 'open' AND owner_id != ?",
      (uid, link, int(datetime.datetime.now().timestamp()), offer_id, uid),
  )
  if not rc:
    await message.answer("Не успели — этот обмен уже забрали.")
    return
  await ex_exec(
      "UPDATE users SET tokens = tokens - ? WHERE user_id = ?",
      (EXCHANGE_COST, uid),
  )
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))

  await message.answer(
      f"🤝 <b>Обмен #{offer_id} успешно проведен!</b>\nСсылка"
      f" обменщика:\n{esc(o['owner_link'])}\n\nСписан {EXCHANGE_COST} токен.",
      disable_web_page_preview=True,
  )
  try:
    await bot.send_message(
        o["owner_id"],
        f"🔔 Пользователь <code>{uid}</code> взял ваш обмен #{offer_id}!\nЕго"
        f" ссылка:\n{esc(link)}",
        disable_web_page_preview=True,
    )
  except Exception:
    pass


ROULETTE_MIN = 2
ROULETTE_MAX = 10
_roulette_locks = {}


class RouletteState(StatesGroup):
  bet = State()


def roulette_decide(
    current_tokens: int, roll_win: bool, aura_percent: int
) -> bool:
  if aura_percent > 0:
    roll_win = random.random() < (0.50 + (aura_percent / 100.0))
  if current_tokens >= 30:
    return random.random() < 0.30
  return roll_win


async def roulette_play(user_id: int, bet: int) -> dict:
  lock = _roulette_locks.setdefault(user_id, asyncio.Lock())
  async with lock:
    async with aiosqlite.connect(DB_NAME) as db:
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
          "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?",
          (user_id,),
      ) as c:
        row_bal = await c.fetchone()
      current_bal = row_bal[0] if row_bal else 0

      aura = await get_user_aura(user_id)
      aura_percent = (
          aura["percent"] if (aura and aura["type"] == "roulette") else 0
      )

      win = roulette_decide(current_bal, random.random() < 0.5, aura_percent)

      await db.execute(
          "INSERT OR IGNORE INTO roulette_state (user_id) VALUES (?)",
          (user_id,),
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
                  text=f"🔁 Сыграть снова ({bet} ток.)",
                  callback_data=f"rl_again_{bet}",
              )
          ],
          [InlineKeyboardButton(text="💰 Другая ставка", callback_data="rl_new")],
      ]
  )


async def roulette_intro_text(user_id: int) -> str:
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
  balance = row[0] if row else 0
  return (
      "🎲 <b>Рулетка 50/50</b>\nСтавка: от"
      f" {ROULETTE_MIN} до {ROULETTE_MAX} токенов.\nВаш баланс:"
      f" <b>{balance}</b>.\n\nНапишите сумму ставки в чат:"
  )


@router.message(StateFilter(RouletteState.bet))
async def roulette_bet(message: Message, state: FSMContext):
  raw = (message.text or "").strip()
  if not raw.isdigit() or not ROULETTE_MIN <= int(raw) <= ROULETTE_MAX:
    await message.answer(f"Напишите число от {ROULETTE_MIN} до {ROULETTE_MAX}.")
    return
  bet = int(raw)
  res = await roulette_play(message.from_user.id, bet)
  if "error" in res:
    await message.answer(f"❌ Недостаточно токенов. Баланс: {res['balance']}.")
    return
  await state.clear()
  text = (
      f"🎉 <b>Победа!</b> +{bet} токенов."
      if res["win"]
      else "💥 <b>Проигрыш.</b> Ставка сгорела."
  )
  text += f"\nБаланс: <b>{res['balance']}</b>"
  await message.answer(text, reply_markup=roulette_result_kb(bet))


@router.callback_query(F.data.startswith("rl_again_"))
async def roulette_again(callback: CallbackQuery):
  bet = int(callback.data.split("_")[2])
  res = await roulette_play(callback.from_user.id, bet)
  if "error" in res:
    await callback.answer("❌ Недостаточно токенов.", show_alert=True)
    return
  await callback.answer()
  text = (
      f"🎉 <b>Победа!</b> +{bet} токенов."
      if res["win"]
      else "💥 <b>Проигрыш.</b>"
  )
  text += f"\nБаланс: <b>{res['balance']}</b>"
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass
  await callback.message.answer(text, reply_markup=roulette_result_kb(bet))


@router.callback_query(F.data == "rl_new")
async def roulette_new(callback: CallbackQuery, state: FSMContext):
  await state.set_state(RouletteState.bet)
  await callback.answer()
  await callback.message.answer(
      await roulette_intro_text(callback.from_user.id)
  )


async def weekly_reset_loop(bot: Bot):
  while True:
    await asyncio.sleep(1209600)
    try:
      expire_date = (
          datetime.datetime.now() + datetime.timedelta(days=14)
      ).strftime(DT_FORMAT)

      async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(REF_TOP_SQL, (1,)) as cursor:
          ref_winner = await cursor.fetchone()

        if ref_winner:
          w_id = ref_winner[0]
          await db.execute(
              "UPDATE users SET is_premium = 1, premium_expires = ? WHERE"
              " user_id = ?",
              (expire_date, w_id),
          )
          await db.commit()
          try:
            await bot.send_message(
                w_id,
                "🏆 <b>Поздравляем! Вы заняли 1 место в топе по рефералям и"
                " получили Премиум на 14 дней!</b>",
            )
          except Exception:
            pass

        async with db.execute(
            "SELECT user_id FROM users WHERE COALESCE(lottery_wins, 0) > 0"
            " ORDER BY lottery_wins DESC LIMIT 1"
        ) as cursor:
          lot_winner = await cursor.fetchone()

        if lot_winner:
          w_id = lot_winner[0]
          await db.execute(
              "UPDATE users SET is_premium = 1, premium_expires = ? WHERE"
              " user_id = ?",
              (expire_date, w_id),
          )
          await db.commit()
          try:
            await bot.send_message(
                w_id,
                "🏆 <b>Поздравляем! Вы заняли 1 место в топе по лотерее и"
                " получили Премиум на 14 дней!</b>",
            )
          except Exception:
            pass

        async with db.execute(
            "SELECT user_id FROM roulette_state WHERE COALESCE(wins, 0) > 0"
            " ORDER BY wins DESC LIMIT 1"
        ) as cursor:
          rl_winner = await cursor.fetchone()

        if rl_winner:
          w_id = rl_winner[0]
          await db.execute(
              "UPDATE users SET is_premium = 1, premium_expires = ? WHERE"
              " user_id = ?",
              (expire_date, w_id),
          )
          await db.commit()
          try:
            await bot.send_message(
                w_id,
                "🏆 <b>Поздравляем! Вы заняли 1 место в топе по рулетке и"
                " получили Премиум на 14 дней!</b>",
            )
          except Exception:
            pass

        await db.execute(
            "UPDATE users SET referrer_id = 0, ref_income = 0 WHERE"
            " ref_activated = 1"
        )
        await db.execute("UPDATE users SET ref_extra = 0")
        await db.execute("UPDATE users SET lottery_wins = 0")
        await db.execute("DELETE FROM roulette_state")
        await db.commit()

      try:
        await bot.send_message(
            ADMIN_ID,
            "🔄 Автоматический сброс всех трех топов (каждые 14 дней) выполнен,"
            " победителям выданы Премиумы!",
        )
      except Exception:
        pass

    except Exception:
      logging.exception("Ошибка при автосбросе топов")


async def auto_backup_loop(bot: Bot):
  while True:
    await asyncio.sleep(300)
    try:
      if os.path.exists(DB_NAME):
        document = FSInputFile(DB_NAME)
        await bot.send_document(
            ADMIN_ID,
            document,
            caption=(
                "⏱ Авто-бэкап БД:"
                f" {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            ),
        )
    except Exception:
      pass


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

  _bg_tasks.add(asyncio.create_task(auto_backup_loop(bot)))
  _bg_tasks.add(asyncio.create_task(weekly_reset_loop(bot)))
  _bg_tasks.add(asyncio.create_task(lottery_loop(bot)))

  await bot.delete_webhook(drop_pending_updates=True)
  print(f"Бот успешно запущен на порту {PORT}!")
  try:
    await dp.start_polling(bot)
  finally:
    for task in _bg_tasks:
      task.cancel()


if __name__ == "__main__":
  asyncio.run(main())
