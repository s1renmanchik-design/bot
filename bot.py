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
REF_BONUS = 7  # токенов пригласившему за подписавшегося реферала

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
  return LOTTERY_BIG_MIN_PLAYERS if prize >= LOTTERY_BIG_PRIZE else LOTTERY_MIN_PLAYERS


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
            aura_expires TEXT DEFAULT NULL
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
            finished INTEGER DEFAULT 0
        );
    """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS lottery_participants (
            round_id INTEGER,
            user_id INTEGER,
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
          logging.info("Миграция: добавлена колонка %s.%s", table, col)

    await db.execute(
        "UPDATE users SET ref_income = ? WHERE referrer_id != 0"
        " AND ref_activated = 1 AND COALESCE(ref_income, 0) = 0",
        (REF_BONUS,),
    )
    await db.commit()


class AddProduct(StatesGroup):
  title = State()
  price = State()
  link = State()


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


router = Router()


# --- ПРОВЕРКА И СБРОС ПРОСРОЧЕННЫХ АУР ---

async def get_user_aura(user_id: int):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT aura_type, aura_name, aura_percent, aura_expires FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0] or not row[3]:
      return None
    try:
      exp = datetime.datetime.strptime(row[3], DT_FORMAT)
      if exp <= datetime.datetime.now():
        # Аура истекла, стираем
        await db.execute(
            "UPDATE users SET aura_type = NULL, aura_name = NULL, aura_percent = 0, aura_expires = NULL WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return None
      return {"type": row[0], "name": row[1], "percent": row[2], "expires": exp}
    except Exception:
      return None


# --- БАНЫ, ПРЕДУПРЕЖДЕНИЯ И ПРИЧИНЫ ---


async def get_active_ban_info(user_id: int):
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT banned_until, ban_reason FROM users WHERE user_id = ?", (user_id,)
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
          "UPDATE users SET banned_until = NULL, ban_reason = NULL WHERE user_id = ?",
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
      "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
      " referrer_id, ref_activated) VALUES (?, 0, 0, 0, 0, 0)"
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
        "UPDATE users SET banned_until = NULL, ban_reason = NULL WHERE user_id = ?",
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
      ban_msg = f"Превышен лимит предупреждений ({MAX_WARNS})" + (f": {reason}" if reason else "")
      await db.execute(
          "UPDATE users SET banned_until = ?, ban_reason = ?, is_premium = 0,"
          " premium_expires = NULL, tokens = 0 WHERE user_id = ?",
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


# --- РЕФЕРАЛЬНАЯ СИСТЕМА И ПОДПИСКА ---


async def is_subscribed(bot: Bot, user_id: int) -> bool:
  try:
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
  except Exception:
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
        "🎉 По вашей рефке зарегистрировался новый фембойчик!\n"
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
        INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
        VALUES (?, ?, 0, 0, 0, 0)
        ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
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
        INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
        VALUES (?, 0, 1, 0, 0, 0, ?)
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
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
            VALUES (?, ?, 0, 0, 0, 0)
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
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
            VALUES (?, 0, 1, 0, 0, 0, ?)
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


# --- ЛОТЕРЕЯ (с учетом ауры) ---


def roll_lottery_params():
  hour = now_local().hour
  if 1 <= hour < 8:
    return 4, 1
  r = random.random()
  if r < LOTTERY_NORMAL_CHANCE:
    return random.randint(4, 10), 0
  if r < LOTTERY_NORMAL_CHANCE + LOTTERY_BIG_LOW_CHANCE:
    return random.randint(11, 14), 0
  return random.randint(15, 20), 0


_bg_tasks = set()


async def notify_big_lottery(bot: Bot, cost: int, prize: int) -> None:
  text = (
      "🔥 <b>Началась крупная лотерея!</b>\n\n"
      f"🏆 Приз: <b>от {prize} токенов</b> (+ взносы участников)\n"
      f"🎟 Участие: <b>{cost_text(cost)}</b>\n"
      f"👥 Минимум участников: <b>{lottery_min_players(prize)}</b>\n"
      f"⏱ Длится {LOTTERY_HOURS} ч."
  )
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT user_id, banned_until, COALESCE(notify_lottery, 1) FROM users") as cur:
      rows = await cur.fetchall()
  now = datetime.datetime.now()
  for uid, banned_until, notify in rows:
    if notify == 0:
      continue
    if banned_until:
      try:
        if datetime.datetime.strptime(banned_until, DT_FORMAT) > now:
          continue
      except Exception:
        pass
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Участвовать", callback_data="lottery_open")],
            [InlineKeyboardButton(text="🔕 Отключить уведомления", callback_data="lottery_toggle_notify")]
        ]
    )
    try:
      await bot.send_message(uid, text, reply_markup=kb)
    except Exception:
      pass
    await asyncio.sleep(0.05)


def lottery_join_keyboard(user_notify: int = 1) -> InlineKeyboardMarkup:
  toggle_text = "🔕 Отключить уведомления" if user_notify == 1 else "🔔 Включить уведомления"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🎰 Участвовать", callback_data="lottery_open")],
          [InlineKeyboardButton(text=toggle_text, callback_data="lottery_toggle_notify")]
      ]
  )


async def create_lottery_round(bot: Bot = None) -> None:
  prize, fixed = roll_lottery_params()
  cost = 1 if fixed else lottery_cost_for_total(prize)
  ends_at = datetime.datetime.now() + datetime.timedelta(hours=LOTTERY_HOURS)
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "INSERT INTO lottery_rounds (entry_cost, prize, ends_at, finished,"
        " cost_fixed) VALUES (?, ?, ?, 0, ?)",
        (cost, prize, ends_at.strftime(DT_FORMAT), fixed),
    )
    await db.commit()
  if bot is not None and prize >= LOTTERY_NOTIFY_PRIZE:
    task = asyncio.create_task(notify_big_lottery(bot, cost, prize))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def get_current_round():
  async with aiosqlite.connect(DB_NAME) as db:
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
  toggle_text = "🔕 Отключить уведомления" if user_notify == 1 else "🔔 Включить уведомления"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🎰 Попытать удачу ещё раз", callback_data="lottery_open")],
          [InlineKeyboardButton(text=toggle_text, callback_data="lottery_toggle_notify")]
      ]
  )


async def build_lottery_view(user_id: int):
  rnd = await get_current_round()
  if not rnd:
    return "⏳ Новый розыгрыш скоро начнётся. Загляни чуть позже!", None

  round_id, _, prize, ends_at_str, fixed = rnd
  ends_at = datetime.datetime.strptime(ends_at_str, DT_FORMAT)
  left_min = max(0, int((ends_at - datetime.datetime.now()).total_seconds() // 60))
  hours, minutes = divmod(left_min, 60)

  async with aiosqlite.connect(DB_NAME) as db:
    st = await lottery_stats(db, rnd)
    async with db.execute(
        "SELECT 1 FROM lottery_participants WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ) as cur:
      joined = await cur.fetchone() is not None
    async with db.execute(
        "SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?", (user_id,)
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
  
  toggle_text = "🔕 Отключить уведомления" if user_notify == 1 else "🔔 Включить уведомления"
  
  if joined:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="lottery_toggle_notify")]
        ]
    )
    return text + "\n\n✅ Ты уже участвуешь в этом розыгрыше!", keyboard

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=(
                      "🎟 Участвовать (бесплатно)"
                      if cost == 0
                      else f"🎟 Участвовать ({cost} токен.)"
                  ),
                  callback_data=f"lottery_join_{round_id}_{cost}",
              )
          ],
          [InlineKeyboardButton(text=toggle_text, callback_data="lottery_toggle_notify")]
      ]
  )
  return text, keyboard


@router.callback_query(F.data == "lottery_toggle_notify")
async def lottery_toggle_notify(callback: CallbackQuery):
  user_id = callback.from_user.id
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
    current = row[0] if row else 1
    new_val = 0 if current == 1 else 1
    await db.execute(
        "UPDATE users SET notify_lottery = ? WHERE user_id = ?", (new_val, user_id)
    )
    await db.commit()

  status_str = "отключены 🔕" if new_val == 0 else "включены 🔔"
  await callback.answer(f"Уведомления о крупных лотереях {status_str}!", show_alert=True)
  
  try:
    text, kb = await build_lottery_view(user_id)
    await callback.message.edit_text(text, reply_markup=kb)
  except Exception:
    pass


async def finish_lottery_round(bot: Bot, round_id: int, prize: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
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

    min_players = lottery_min_players(prize)
    if players and len(players) < min_players:
      for uid in players:
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
            (paid_map[uid], uid),
        )
      await db.commit()
      for uid in players:
        async with aiosqlite.connect(DB_NAME) as udb:
          async with udb.execute("SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?", (uid,)) as c:
            urow = await c.fetchone()
            unotify = urow[0] if urow else 1
        try:
          await bot.send_message(
              uid,
              "😔 В розыгрыше не набралось нужное число участников"
              f" ({len(players)} из {min_players}). Приз не разыгран."
              + (
                  f" Тебе вернули <b>{paid_map[uid]} токен(ов)</b>."
                  if paid_map[uid]
                  else ""
              )
              + "\n\nХотите ещё попытать удачу?",
              reply_markup=lottery_again_keyboard(unotify),
          )
        except Exception:
          pass
        await asyncio.sleep(0.05)
      return

    prize = prize + sum(fee_to_prize(v) for v in paid_map.values())
    
    # Расчет победителя с учетом ауры типа "lottery"
    weights = []
    for uid in players:
      weight = 1.0
      aura = await get_user_aura(uid)
      if aura and aura["type"] == "lottery":
        weight += (aura["percent"] / 100.0)
      weights.append(weight)
    
    winner = random.choices(players, weights=weights, k=1)[0] if players else None
    
    if winner is not None:
      await db.execute(
          """
          INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
          VALUES (?, ?, 0, 0, 0, 1)
          ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
          """,
          (winner, prize, prize),
      )
      await db.commit()

  for uid in players:
    async with aiosqlite.connect(DB_NAME) as udb:
      async with udb.execute("SELECT COALESCE(notify_lottery, 1) FROM users WHERE user_id = ?", (uid,)) as c:
        urow = await c.fetchone()
        unotify = urow[0] if urow else 1
    try:
      if uid == winner:
        await bot.send_message(
            uid,
            f"🎉 <b>Ты выиграл в лотерее!</b>\n🏆 Тебе начислено:"
            f" <b>{prize} токенов</b>.\n\nХотите ещё попытать удачу?",
            reply_markup=lottery_again_keyboard(unotify),
        )
      else:
        await bot.send_message(
            uid,
            "😔 В этот раз удача была не на твоей стороне. Победитель уже"
            " получил приз.\n\nХотите ещё попытать удачу?",
            reply_markup=lottery_again_keyboard(unotify),
        )
    except Exception:
      pass
    await asyncio.sleep(0.05)


async def lottery_loop(bot: Bot):
  while True:
    try:
      rnd = await get_current_round()
      if rnd is None:
        await create_lottery_round(bot)
        rnd = await get_current_round()

      round_id, cost, prize, ends_at_str, _fixed = rnd
      ends_at = datetime.datetime.strptime(ends_at_str, DT_FORMAT)
      delay = (ends_at - datetime.datetime.now()).total_seconds()
      if delay > 0:
        await asyncio.sleep(min(delay, 15))
        continue
      await finish_lottery_round(bot, round_id, prize)
    except Exception:
      logging.exception("Ошибка в цикле лотереи")
      await asyncio.sleep(30)


@router.callback_query(F.data == "lottery_open")
async def lottery_open(callback: CallbackQuery):
  text, keyboard = await build_lottery_view(callback.from_user.id)
  await callback.message.answer(text, reply_markup=keyboard)
  await callback.answer()


@router.callback_query(F.data.startswith("lottery_join_"))
async def lottery_join(callback: CallbackQuery):
  user_id = callback.from_user.id
  parts = callback.data.split("_")
  round_id = int(parts[2])
  shown_cost = int(parts[3]) if len(parts) > 3 else None
  price_changed = False

  async with aiosqlite.connect(DB_NAME) as db:
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
    try:
      ends_at = datetime.datetime.strptime(rnd[3], DT_FORMAT)
    except Exception:
      ends_at = datetime.datetime.now()
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
      + "\n"
      "Результат придёт сюда, когда закончится розыгрыш."
  )


# --- СТАРТ И ПРОМОКОД ---


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
          "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
          " referrer_id, ref_activated, ref_joined_at) VALUES (?, 0, 0, 0, ?, 0, ?)",
          (user_id, ref_to_save, now_str),
      )
      await db.commit()

  if referrer_id and await is_subscribed(bot, user_id):
    await activate_referral(bot, user_id)

  await message.answer(
      "👋 Привет! Добро пожаловать в бота (Уголок бомжа). Выбирай нужный раздел на клавиатуре"
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


# --- АДМИН-КОМАНДЫ (включая /aura, /uref и /check) ---


@router.message(Command("aura"))
async def cmd_aura(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  
  parts = (command.args or "").split()
  if len(parts) >= 1 and parts[0].lower() == "del" and len(parts) == 2 and parts[1].isdigit():
    target_id = int(parts[1])
    async with aiosqlite.connect(DB_NAME) as db:
      await db.execute(
          "UPDATE users SET aura_type = NULL, aura_name = NULL, aura_percent = 0, aura_expires = NULL WHERE user_id = ?",
          (target_id,),
      )
      await db.commit()
    await message.answer(f"✅ У пользователя <code>{target_id}</code> успешно аннулирована аура.")
    return

  # Использование: /aura add [ID] [lottery/roulette/shop] [процент] [дни] [Название]
  if len(parts) >= 5 and parts[0].lower() == "add" and parts[1].isdigit() and parts[3].isdigit() and parts[4].isdigit():
    target_id = int(parts[1])
    aura_type = parts[2].lower()
    if aura_type not in ("lottery", "roulette", "shop"):
      await message.answer("❌ Неверный тип ауры! Доступно: <code>lottery</code>, <code>roulette</code>, <code>shop</code>")
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
          "UPDATE users SET aura_type = ?, aura_name = ?, aura_percent = ?, aura_expires = ? WHERE user_id = ?",
          (aura_type, aura_name, percent, expires_str, target_id),
      )
      await db.commit()

    await message.answer(
        f"✨ <b>Аура успешно выдана!</b>\n\n"
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
      "<code>/aura add [ID] [lottery/roulette/shop] [процент] [дни] [Название]</code>\n"
      "<i>Пример: <code>/aura add 5681420389 lottery 15 7 Удача Бога</code></i>\n\n"
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
      where = "referrer_id = ?" + ("" if left is None else " AND ref_activated = 1")
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
      "<code>/uref [ID]</code> — список рефералов, время входа и доход\n"
      "<code>/uref add [ID] [число]</code> — добавить рефералов"
      f" (+{REF_BONUS} токенов за каждого)\n"
      "<code>/uref del [ID] [число]</code> — убрать N рефералов и забрать"
      " их доход\n"
      "<code>/uref del [ID] all</code> — убрать всех"
  )
  parts = (command.args or "").split()
  if len(parts) == 1 and parts[0].isdigit():
    target_id = int(parts[0])
    async with aiosqlite.connect(DB_NAME) as db:
      active, pending, extra, income = await ref_summary(db, target_id)
      async with db.execute(
          "SELECT user_id, ref_activated, COALESCE(ref_income, 0), ref_joined_at FROM users"
          " WHERE referrer_id = ? ORDER BY ref_activated DESC, user_id DESC"
          " LIMIT 20",
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
        f"✅ Засчитано: <b>{active + extra}</b>"
        f" (из них добавлено админом: {extra})\n"
        f"⏳ Не подписались: <b>{pending}</b>\n"
        f"💰 Доход с рефералов: <b>{income}</b> токенов\n\n{lines_str}"
    )
    return

  if len(parts) == 3 and parts[0].lower() in ("add", "del") and parts[1].isdigit():
    action, target_id, raw = parts[0].lower(), int(parts[1]), parts[2].lower()
    if action == "add":
      if not raw.isdigit() or int(raw) <= 0:
        await message.answer(usage)
        return
      count = int(raw)
      await uref_add(target_id, count)
      await message.answer(
          f"✅ ID <code>{target_id}</code>: добавлено рефералов: <b>{count}</b>,"
          f" начислено <b>{count * REF_BONUS}</b> токенов."
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
        f"✅ ID <code>{target_id}</code>: убрано рефералов: <b>{removed}</b>,"
        f" списано дохода: <b>{taken}</b> токенов.\n"
        f"Баланс сейчас: <b>{balance}</b>."
    )
    return
  await message.answer(usage)


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
    async with db.execute(
        "SELECT COUNT(*) FROM ex_offers WHERE owner_id = ? AND status = 'open'",
        (target_id,),
    ) as c:
      open_offers = (await c.fetchone())[0]

  rl_games, rl_wins = rl if rl else (0, 0)
  prem_exp = parse_dt(u["premium_expires"])
  if u["is_premium"] == 1 and (prem_exp is None or prem_exp > now):
    prem = f"✅ до {prem_exp.strftime('%d.%m.%Y %H:%M')}" if prem_exp else "✅ активна"
  else:
    prem = "❌ нет"

  aura = await get_user_aura(target_id)
  aura_info = f"✨ {aura['name']} ({aura['type']}, {aura['percent']}%) до {aura['expires'].strftime('%d.%m.%Y %H:%M')}" if aura else "❌ нет"

  def until_text(value):
    dt = parse_dt(value)
    if not dt or dt <= now:
      return "нет"
    if dt.year >= 2900:
      return "🚫 навсегда"
    return f"🚫 до {dt.strftime('%d.%m.%Y %H:%M')}"

  invited = (
      f"<code>{u['referrer_id']}</code> "
      + ("(подписался ✅)" if u["ref_activated"] else "(ещё не подписался ⏳)")
      if u["referrer_id"]
      else "никто / сброшено"
  )
  ban_info = until_text(u["banned_until"])
  if u["banned_until"] and parse_dt(u["banned_until"]) and parse_dt(u["banned_until"]) > now:
    if u["ban_reason"]:
      ban_info += f" (Причина: {esc(u['ban_reason'])})"

  await message.answer(
      f"🔎 <b>Аккаунт</b> <code>{target_id}</code>\n\n"
      f"🪙 Токены: <b>{u['tokens'] or 0}</b>\n"
      f"👑 Премиум: {prem}\n"
      f"✨ Аура: {aura_info}\n"
      f"📦 Куплено фулов: {u['bought_folders'] or 0}\n"
      f"⚠️ Предупреждения: {u['warns'] or 0}/{MAX_WARNS}\n"
      f"🚫 Бан: {ban_info}\n"
      f"🔒 Ограничение обменов: {until_text(u['restricted_until'])}\n\n"
      f"👥 <b>Рефералы</b>\n"
      f"• Засчитано: <b>{active + extra}</b> (добавлено админом: {extra})\n"
      f"• Не подписались: {pending}\n"
      f"• Доход с рефералов: <b>{income}</b> токенов\n"
      f"• Пригласил его: {invited}\n\n"
      f"🔄 <b>Обмены</b>\n"
      f"• Проведено: <b>{u['exchanges'] or 0}</b>\n"
      f"• Открытых сейчас: {open_offers}\n\n"
      f"🎰 <b>Лотерея</b>\n"
      f"• Участий: <b>{lot_n}</b>, заплачено всего: {lot_paid} токенов\n\n"
      f"🎲 <b>Рулетка</b>\n"
      f"• Игр: <b>{rl_games}</b> (побед: {rl_wins}, поражений: {rl_games - rl_wins})"
  )


@router.message(Command("premium", "p"))
async def cmd_premium(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  args = (command.args or "").split()
  if (
      len(args) < 2
      or not args[0].isdigit()
      or not args[1].isdigit()
      or int(args[0]) <= 0
  ):
    await message.answer(
        "ℹ️ Использование:\n<code>/p [дни] [ID]</code> или <code>/premium add [дни] [ID]</code>\n"
        "Пример: <code>/p 30 5681420389</code>"
    )
    return

  days = int(args[0])
  target_id = int(args[1])

  try:
    new_expire = await grant_premium(target_id, days)
  except Exception as e:
    logging.exception("Ошибка выдачи премиума командой")
    await message.answer(f"❌ Ошибка выдачи премиума: {esc(e)}")
    return

  formatted_date = new_expire.strftime("%d.%m.%Y")
  await message.answer(
      f"✅ <b>Премиум успешно выдан!</b>\n\n🆔 Пользователь:"
      f" <code>{target_id}</code>\n📅 Срок: <b>{days} дней</b> (до"
      f" {formatted_date})"
  )
  try:
    await bot.send_message(
        target_id,
        f"🎉 <b>Вам активирована Премиум-подписка на {days} дней!</b>\n📅"
        f" Срок действия: до <b>{formatted_date}</b>.\n\nТеперь любые фулы в"
        " магазине доступны бесплатно!",
    )
  except Exception:
    pass


@router.message(Command("token", "tokens", "t"))
async def cmd_token(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  args = (command.args or "").split()
  if (
      len(args) < 2
      or not args[0].isdigit()
      or not args[1].isdigit()
      or int(args[0]) <= 0
  ):
    await message.answer(
        "ℹ️ Использование:\n<code>/t [сколько] [ID]</code> или <code>/token add [сколько] [ID]</code>\n"
        "Пример: <code>/t 50 5681420389</code>"
    )
    return

  amount = int(args[0])
  target_id = int(args[1])

  try:
    await grant_tokens(target_id, amount)
  except Exception as e:
    logging.exception("Ошибка выдачи токенов командой")
    await message.answer(f"❌ Ошибка выдачи токенов: {esc(e)}")
    return

  await message.answer(
      f"✅ <b>Токены успешно выданы!</b>\n\n🆔 Пользователь:"
      f" <code>{target_id}</code>\n🪙 Начислено: <b>+{amount} токенов</b>"
  )
  try:
    await bot.send_message(
        target_id,
        f"🎁 <b>Администратор начислил вам {amount} токенов!</b> Проверьте"
        " баланс в профиле.",
    )
  except Exception:
    pass


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/ban [ID] [дни] [причина]</code>\n"
        "Пример: <code>/ban 5681420389 7 Спам в боте</code>\n"
        "<i>(0 дней = бан навсегда)</i>"
    )
    return
  target_id = int(parts[0])
  days = int(parts[1])
  reason = " ".join(parts[2:]) if len(parts) > 2 else None

  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя забанить админа.")
    return
  term = await ban_user(target_id, days, reason)
  reason_admin = f"\n📝 Причина: {esc(reason)}" if reason else ""
  await message.answer(
      f"🚫 Пользователь <code>{target_id}</code> заблокирован {term}.{reason_admin}"
  )
  try:
    user_msg = f"🚫 Ваш профиль заблокирован {term}."
    if reason:
      user_msg += f"\n📝 Причина: <b>{esc(reason)}</b>"
    user_msg += f"\n\n{APPEAL_TEXT}"
    await bot.send_message(target_id, user_msg)
  except Exception:
    pass


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/unban [ID]</code>\n"
        "Пример: <code>/unban 5681420389</code>"
    )
    return
  target_id = int(parts[0])
  await unban_user(target_id)
  await message.answer(
      f"✅ Пользователь <code>{target_id}</code> разблокирован."
  )
  try:
    await bot.send_message(target_id, "✅ Ваш профиль разблокирован.")
  except Exception:
    pass


@router.message(Command("warn"))
async def cmd_warn(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) < 1 or not parts[0].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/warn [ID] [причина]</code>\n"
        "Пример: <code>/warn 5681420389 Неадекватное поведение</code>"
    )
    return
  target_id = int(parts[0])
  reason = " ".join(parts[1:]) if len(parts) > 1 else None

  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя выдать предупреждение админу.")
    return
  warns, banned = await add_warn(target_id, reason)
  reason_text = f"\n📝 Причина: {esc(reason)}" if reason else ""
  
  if banned:
    await message.answer(
        f"⚠️ Пользователь <code>{target_id}</code> получил"
        f" {warns}/{MAX_WARNS} предупреждений и заблокирован навсегда"
        f" (премиум снят, токены обнулены).{reason_text}"
    )
    try:
      u_msg = f"🚫 Вы получили {warns}/{MAX_WARNS} предупреждений. Ваш профиль заблокирован навсегда, премиум-подписка снята, токены обнулены."
      if reason:
        u_msg += f"\n📝 Причина последней выдачи: <b>{esc(reason)}</b>"
      u_msg += f"\n\n{APPEAL_TEXT}"
      await bot.send_message(target_id, u_msg)
    except Exception:
      pass
  else:
    await message.answer(
        f"⚠️ Пользователю <code>{target_id}</code> выдано предупреждение"
        f" ({warns}/{MAX_WARNS}).{reason_text}"
    )
    try:
      u_msg = f"⚠️ Вам выдано предупреждение ({warns}/{MAX_WARNS}). При {MAX_WARNS} предупреждениях профиль будет заблокирован, премиум снят, токены обнулены."
      if reason:
        u_msg += f"\n📝 Причина: <b>{esc(reason)}</b>"
      await bot.send_message(target_id, u_msg)
    except Exception:
      pass


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/unwarn [ID]</code>\n"
        "Пример: <code>/unwarn 5681420389</code>"
    )
    return
  target_id = int(parts[0])
  warns = await remove_warn(target_id)
  await message.answer(
      f"✅ У пользователя <code>{target_id}</code> снято предупреждение"
      f" (осталось {warns}/{MAX_WARNS})."
  )
  try:
    await bot.send_message(
        target_id,
        f"✅ С вас снято предупреждение (осталось {warns}/{MAX_WARNS}).",
    )
  except Exception:
    pass


@router.message(Command("unpremium"))
async def cmd_unpremium(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 1 or not parts[0].isdigit():
    await message.answer(
        "ℹ️ Использование:\n<code>/unpremium [ID]</code>\n"
        "Пример: <code>/unpremium 5681420389</code>"
    )
    return
  target_id = int(parts[0])
  await remove_premium(target_id)
  await message.answer(
      f"✅ Премиум у пользователя <code>{target_id}</code> снят."
  )


@router.message(Command("untoken"))
async def cmd_untoken(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  parts = (command.args or "").split()
  if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[1]) <= 0:
    await message.answer(
        "ℹ️ Использование:\n<code>/untoken [ID] [число токенов]</code>\n"
        "Пример: <code>/untoken 5681420389 20</code>"
    )
    return
  target_id = int(parts[0])
  amount = int(parts[1])
  new_balance = await remove_tokens(target_id, amount)
  await message.answer(
      f"✅ У пользователя <code>{target_id}</code> забрано до"
      f" <b>{amount}</b> токенов. Баланс сейчас: <b>{new_balance}</b>."
  )


@router.message(Command("all"))
async def cmd_all(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
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
  async with aiosqlite.connect(DB_NAME) as db:
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
      f"✅ Выдано по <b>{amount}</b> токенов всем пользователям:"
      f" <b>{count}</b> чел. Рассылаю уведомления…"
  )

  async def _notify_all():
    for uid in user_ids:
      try:
        await bot.send_message(
            uid, f"🎁 Администратор выдал всем по <b>{amount}</b> токенов!"
        )
      except Exception:
        pass
      await asyncio.sleep(0.05)

  task = asyncio.create_task(_notify_all())
  _bg_tasks.add(task)
  task.add_done_callback(_bg_tasks.discard)


@router.message(Command("lottery"))
async def cmd_lottery(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  usage = (
      "ℹ️ Управление текущей лотереей:\n"
      "<code>/lottery prize [токены]</code> — главный приз (можно 0)\n"
      "<code>/lottery cost [токены]</code> — цена участия, фиксированная"
      " (можно 0 — бесплатно)\n"
      "<code>/lottery cost auto</code> — вернуть автоцену (растёт вместе с"
      " призом)\n"
      "<code>/lottery time [часы]</code> — сколько часов до конца, считая с"
      " сейчас (можно <code>30m</code> — минуты)\n"
      "<code>/lottery</code> — показать текущие настройки\n\n"
      "<i>Участники не сбрасываются: все, кто уже вошёл, остаются в"
      " розыгрыше. Изменения действуют на текущий тираж, следующие выпадают по"
      " обычным правилам.</i>"
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
    async with aiosqlite.connect(DB_NAME) as db:
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
              "UPDATE lottery_rounds SET cost_fixed = 0 WHERE id = ?"
              " AND finished = 0",
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

  round_id, _, prize, ends_at_str, fixed = rnd
  ends_at = datetime.datetime.strptime(ends_at_str, DT_FORMAT)
  left_min = max(0, int((ends_at - datetime.datetime.now()).total_seconds() // 60))
  async with aiosqlite.connect(DB_NAME) as db:
    st = await lottery_stats(db, rnd)
  await message.answer(
      ("✅ Готово.\n\n" if parts else "")
      + "🎰 <b>Текущая лотерея</b>\n"
      f"🏆 Главный приз: <b>{prize}</b> токенов (+ {st['pool']} от взносов ="
      f" {st['total']})\n"
      f"🎟 Участие: <b>{cost_text(st['cost'])}</b>"
      f" ({'фиксированная' if fixed else 'авто'})\n"
      f"👥 Участников: <b>{st['players']}</b> (минимум: {lottery_min_players(prize)})\n"
      f"⏱ До конца: <b>{left_min // 60} ч {left_min % 60} мин</b>"
      + ("" if parts else "\n\n" + usage)
  )


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


@router.message(
    F.text.in_(
        {
            "👤 Мой Профиль",
            "🛒 Магазин",
            "🛍 Магазин",
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
          "SELECT tokens, is_premium, bought_folders, premium_expires, warns,"
          " COALESCE(exchanges, 0) FROM users WHERE user_id = ?",
          (user_id,),
      ) as cursor:
        user_data = await cursor.fetchone()

      if not user_data:
        await db.execute(
            "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
            " referrer_id, ref_activated) VALUES (?, 0, 0, 0, 0, 0)",
            (user_id,),
        )
        await db.commit()
        tokens, is_prem, bought, prem_expires, warns, exchanges = 0, 0, 0, None, 0, 0
      else:
        tokens, is_prem, bought, prem_expires, warns, exchanges = user_data

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

    # Проверка ауры
    aura = await get_user_aura(user_id)
    if aura:
      aura_days = (aura["expires"] - datetime.datetime.now()).days
      aura_status = f"✨ <b>{esc(aura['name'])}</b> ({aura['percent']}% бонусов, ещё {aura_days} дн.)"
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
        f"💲 Баланс: {tokens if tokens is not None else 0} токенов\n"
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
      await message.answer(
          "📭 В магазине пока пусто. Либо же вы все раскупили!"
      )
      return

    keyboard_buttons = []
    for prod in products:
      p_id, title, price, buyers_count = prod
      if buyers_count == 0:
        btn_text = f"🔥 {title} — {price} токенов (ПЕРВАЯ ПОКУПКА -80%!)"
      else:
        btn_text = f"📁 {title} — {price} токенов"

      keyboard_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"buy_{p_id}")]
      )

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(
        "🛍 <b>Доступные фулы:</b>\n<i>(Нажми на нужную, чтобы забрать"
        " ссылку)</i>",
        reply_markup=keyboard,
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_text = (
        "👥 <b>Реферальная система</b>\n\nПриглашай друзей по своей"
        f" персональной ссылке:\n<code>{ref_link}</code>\n\n🎁 Бонусы:\n• За"
        f" каждого приглашенного друга (после подписки на канал) вы получаете {REF_BONUS} токенов!\n• Получайте 15%"
        " от суммы всех покупок токенами ваших рефералов!"
    )
    await message.answer(ref_text)

  elif text == "🏆 Топ игроков":
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT user_id, is_premium, premium_expires FROM users WHERE"
          " is_premium = 1"
      ) as cursor:
        prem_users = await cursor.fetchall()
      for p_user in prem_users:
        if p_user[2]:
          try:
            exp_dt = datetime.datetime.strptime(p_user[2], DT_FORMAT)
            if datetime.datetime.now() >= exp_dt:
              await db.execute(
                  "UPDATE users SET is_premium = 0, premium_expires = NULL"
                  " WHERE user_id = ?",
                  (p_user[0],),
              )
              await db.commit()
          except Exception:
            pass

      async with db.execute(REF_TOP_SQL, (10,)) as cursor:
        top_list = await cursor.fetchall()

    footer = (
        "<i>(каждую неделю топ сбрасывается, а игроку, занявшему 🥇 1 место,"
        " выдается премиум-подписка на 14 дней)</i>"
    )

    if not top_list:
      await message.answer(
          "🏆 В топе пока никого нет.\nПриглашайте друзей по реферальной"
          f" ссылке, чтобы занять первое место!\n\n{footer}"
      )
      return

    text_top = "🏆 <b>Топ-10 крутых по рефералам:</b>\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, row in enumerate(top_list, start=1):
      uid, is_prem, count = row
      prefix = medals[i - 1] if i <= 3 else f"{i}."
      crown = " 👑" if is_prem == 1 else ""
      text_top += (
          f"{prefix} Пользователь <code>{uid}</code>{crown} —"
          f" <b>{count}</b> реф.\n"
      )

    text_top += f"\n{footer}"
    await message.answer(text_top)

  elif text == "🪙 Как получить токены?":
    tokens_text = (
        "Как получить токены?\n\n"
        "1. Возьмите фото. https://t.me/JmcDeveloper/11\n"
        "2. Оставляйте в комментариях под постами/ответами другим людям."
        " <u>(под тему сестра и брата)</u>\n"
        "3. Отправляйте доказательства (скриншоты) в директ канала."
        " https://t.me/JmcDeveloper?direct\n\n"
        "ГЛАВНОЕ: тема видосов должна касаться по типу сестры и брата и"
        " тд.\n\n"
        "💰 1 отправка фото в комментариях = 1 токен!\n\n\n\n"
        "📖 ДЕТАЛЬНО РАСПИСАЛ ТУТ: https://t.me/JmcDeveloper/18\n"
        "📖 ДЕТАЛЬНО РАСПИСАЛ ТУТ: https://t.me/JmcDeveloper/18\n"
        "📖 ДЕТАЛЬНО РАСПИСАЛ ТУТ: https://t.me/JmcDeveloper/18"
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
        "👑 Премиум-подписка на 1 МЕСЯЦ (30 дней)\n\nДает право покупать"
        " АБСОЛЮТНО ЛЮБЫЕ фулы бесплатно (даже с 0 токенов на балансе)!\n\n💳"
        " Стоимость:\n• 50 Звезд (любым подарком в Telegram за 50 звезд на"
        " профиль @ucu52)."
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
                    text="🪙 Выдать токены пользователю",
                    callback_data="admin_give_tokens",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Выдать Премиум пользователю",
                    callback_data="admin_give_premium",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Сделать рассылку",
                    callback_data="start_broadcast",
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
      "🪙 Введи <b>Telegram ID</b> пользователя, которому нужно начислить"
      " токены:"
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
      "👑 Введи <b>Telegram ID</b> пользователя, которому нужно выдать Премиум:"
  )
  await callback.answer()


@router.message(StateFilter(GiveTokensState.user_id))
async def process_give_tokens_id(message: Message, state: FSMContext):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  await state.update_data(target_id=int(text))
  await state.set_state(GiveTokensState.amount)
  await message.answer(
      f"🪙 Сколько <b>токенов</b> начислить пользователю <code>{text}</code>?\n"
      "<i>(Введи целое число, например: 50)</i>"
  )


@router.message(StateFilter(GivePremiumState.user_id))
async def process_give_premium_id(message: Message, state: FSMContext):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  await state.update_data(target_id=int(text))
  await state.set_state(GivePremiumState.days)
  await message.answer(
      f"⏳ На сколько <b>дней</b> выдать Премиум пользователю"
      f" <code>{text}</code>?\n<i>(Введи число, например: 30)</i>"
  )


@router.message(StateFilter(GivePremiumState.days))
async def process_give_premium_days(
    message: Message, state: FSMContext, bot: Bot
):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! Введите число дней цифрами.")
    return
  days = int(text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()

  try:
    new_expire = await grant_premium(target_id, days)
    formatted_date = new_expire.strftime("%d.%m.%Y")

    await message.answer(
        f"✅ <b>Премиум успешно выдан!</b>\n\n🆔 Пользователь:"
        f" <code>{target_id}</code>\n📅 Срок: <b>{days} дней</b> (до"
        f" {formatted_date})",
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    try:
      await bot.send_message(
          target_id,
          f"🎉 <b>Вам активирована Премиум-подписка на {days} дней!</b>\n📅"
          f" Срок действия: до <b>{formatted_date}</b>.\n\nТеперь любые фулы в"
          " магазине доступны бесплатно!",
      )
    except Exception:
      pass
  except Exception as e:
    logging.exception("Ошибка выдачи премиума")
    await message.answer(f"❌ Ошибка выдачи премиума: {esc(e)}")


@router.message(StateFilter(GiveTokensState.amount))
async def process_give_tokens_amount(
    message: Message, state: FSMContext, bot: Bot
):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! Введите количество токенов цифрами.")
    return
  amount = int(text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()

  try:
    await grant_tokens(target_id, amount)

    await message.answer(
        f"✅ <b>Токены успешно выданы!</b>\n\n🆔 Пользователь:"
        f" <code>{target_id}</code>\n🪙 Начислено: <b>+{amount} токенов</b>",
        reply_markup=get_main_keyboard(message.from_user.id),
    )
    try:
      await bot.send_message(
          target_id,
          f"🎁 <b>Администратор начислил вам {amount} токенов!</b> Проверьте"
          " баланс в профиле.",
      )
    except Exception:
      pass
  except Exception as e:
    logging.exception("Ошибка выдачи токенов")
    await message.answer(f"❌ Ошибка выдачи токенов: {esc(e)}")


@router.message(StateFilter(CreatePromoState.code))
async def process_promo_code(message: Message, state: FSMContext):
  await state.update_data(code=(message.text or "").strip())
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🪙 Токены", callback_data="promo_type_tokens"
              ),
              InlineKeyboardButton(
                  text="👑 Премиум", callback_data="promo_type_premium"
              ),
          ]
      ]
  )
  await message.answer(
      "🎁 Выберите, что будет давать этот промокод:", reply_markup=keyboard
  )


@router.message(StateFilter(CreatePromoState.value))
async def process_promo_value(message: Message, state: FSMContext):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! Введите целое число (цифрами).")
    return
  await state.update_data(value=text)
  await state.set_state(CreatePromoState.uses)
  await message.answer(
      "🔢 Введите <b>количество использований</b> (например: 10 или"
      " 100):\n<i>(Введите 0 для безлимита)</i>"
  )


@router.message(StateFilter(CreatePromoState.uses))
async def process_promo_uses(message: Message, state: FSMContext, bot: Bot):
  text = (message.text or "").strip()
  if not text.isdigit():
    await message.answer("❌ Ошибка! Введите число использований цифрами.")
    return
  max_uses = int(text)
  data = await state.get_data()
  code, reward_type, reward_value = (
      str(data["code"]),
      str(data["type"]),
      str(data["value"]),
  )
  await state.clear()

  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
        INSERT INTO promocodes (code, reward_type, reward_value, max_uses, used_count)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(code) DO UPDATE SET reward_type = ?, reward_value = ?, max_uses = ?
        """,
        (
            code,
            reward_type,
            reward_value,
            max_uses,
            reward_type,
            reward_value,
            max_uses,
        ),
    )
    await db.commit()

  uses_text = f"{max_uses} раз" if max_uses > 0 else "Безлимитно"
  reward_desc = (
      f"{reward_value} токенов"
      if reward_type == "tokens"
      else f"Премиум на {reward_value} дней"
  )
  await message.answer(
      f"✅ <b>Промокод успешно создан!</b>\n\n🎟 Промокод: <code>{esc(code)}</code>\n"
      f"🎁 Награда: {reward_desc}\n👥 Использований: {uses_text}",
      reply_markup=get_main_keyboard(message.from_user.id),
  )

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


@router.message(StateFilter(EditProduct.new_value))
async def process_new_value(message: Message, state: FSMContext):
  data = await state.get_data()
  p_id = data["product_id"]
  field = data["field"]
  new_val = (message.text or "").strip()
  await state.clear()

  async with aiosqlite.connect(DB_NAME) as db:
    if field == "title":
      await db.execute(
          "UPDATE products SET title = ? WHERE id = ?", (new_val, p_id)
      )
    elif field == "price":
      await db.execute(
          "UPDATE products SET price = ? WHERE id = ?", (new_val, p_id)
      )
    elif field == "link":
      await db.execute(
          "UPDATE products SET link = ? WHERE id = ?", (new_val, p_id)
      )
    await db.commit()

  await message.answer("✅ Товар успешно обновлен!")


@router.message(StateFilter(AddProduct.title))
async def process_title(message: Message, state: FSMContext):
  await state.update_data(title=message.text)
  await message.answer("💰 Введи цену в токенах (например: 15 или 50):")
  await state.set_state(AddProduct.price)


@router.message(StateFilter(AddProduct.price))
async def process_price(message: Message, state: FSMContext):
  await state.update_data(price=message.text)
  await message.answer(
      "🔗 Отправь ссылку на скачивание (Mega / облако):\n\n<i>(⚠️"
      " Напоминание: у первого покупателя этого товара будет скидка"
      " 80%)</i>"
  )
  await state.set_state(AddProduct.link)


@router.message(StateFilter(AddProduct.link))
async def process_link(message: Message, state: FSMContext):
  data = await state.get_data()
  title, price, link = data["title"], data["price"], message.text
  await state.clear()

  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "INSERT INTO products (title, price, link, buyers_count) VALUES (?, ?,"
        " ?, 0)",
        (title, price, link),
    )
    await db.commit()

  await message.answer(
      f"✅ Фулл успешно добавлен в магазин!\n\n📌 Название: {esc(title)}\n"
      f"💰 Цена: {esc(price)} токенов\n🔗 Ссылка: {esc(link)}\n🎁"
      " Активирована скидка 80% для первого покупателя!"
  )


@router.message(StateFilter(BroadcastState.message))
async def send_broadcast(message: Message, state: FSMContext, bot: Bot):
  await state.clear()
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT user_id FROM users") as cursor:
      users = await cursor.fetchall()

  success, fail = 0, 0
  status_msg = await message.answer("⏳ Рассылка началась...")

  for user in users:
    try:
      await bot.copy_message(
          chat_id=user[0],
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


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  await state.set_state(PromocodeState.code)
  await callback.message.answer("🎟 Введите промокод:")
  await callback.answer()


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


@router.callback_query(F.data.startswith("promo_type_"))
async def process_promo_type(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_type = callback.data.split("_")[2]
  await state.update_data(type=p_type)
  if p_type == "tokens":
    await callback.message.answer(
        "💰 Введите количество токенов (число, например: 50):"
    )
  else:
    await callback.message.answer(
        "⏳ Введите количество дней Премиума (число, например: 30):"
    )
  await state.set_state(CreatePromoState.value)
  await callback.answer()


@router.callback_query(F.data == "admin_backup_db")
async def backup_database_manual(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer("⛔️ У тебя нет доступа!", show_alert=True)
    return
  if os.path.exists(DB_NAME):
    document = FSInputFile(DB_NAME)
    await callback.message.answer_document(
        document, caption="💾 Ручной бэкап базы данных"
    )
    await callback.answer()
  else:
    await callback.answer("❌ База данных не найдена!", show_alert=True)


@router.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  if await get_restriction(user_id):
    await callback.answer(RESTRICTED_ALERT, show_alert=True)
    return
  p_id = int(callback.data.split("_")[1])

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT title, price, link, buyers_count FROM products WHERE id = ?",
        (p_id,),
    ) as cursor:
      product = await cursor.fetchone()

    if not product:
      await callback.answer("❌ Этот товар был удален.", show_alert=True)
      return

    title, price_raw, link, buyers_count = product

    try:
      base_price = int("".join(filter(str.isdigit, str(price_raw))))
    except ValueError:
      base_price = 0

    async with db.execute(
        "SELECT tokens, is_premium, bought_folders, referrer_id,"
        " premium_expires FROM users WHERE user_id = ?",
        (user_id,),
    ) as cursor:
      u_data = await cursor.fetchone()

    tokens = u_data[0] if (u_data and u_data[0] is not None) else 0
    is_prem = u_data[1] if u_data else 0
    referrer_id = u_data[3] if u_data else 0
    prem_expires = u_data[4] if u_data else None

    if is_prem == 1 and prem_expires:
      try:
        exp_date = datetime.datetime.strptime(prem_expires, DT_FORMAT)
        if datetime.datetime.now() >= exp_date:
          is_prem = 0
          await db.execute(
              "UPDATE users SET is_premium = 0, premium_expires = NULL WHERE"
              " user_id = ?",
              (user_id,),
          )
          await db.commit()
      except Exception:
        pass

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
      text = (
          f"👑 Премиум-доступ применен!\n\n✅ Вы успешно получили фулл"
          f" {esc(title)} бесплатно по подписке!\n\n🔗 Ссылка"
          f" (нажми, чтобы показать):\n{hidden_link(link)}"
      )
      await callback.message.answer(text, disable_web_page_preview=True)
      await callback.answer()
      return

    # Стандартная или первая цена со скидкой 80%
    if buyers_count == 0:
      final_price = max(1, int(base_price * 0.2))
    else:
      final_price = base_price

    # Проверка ауры магазина ("shop")
    aura = await get_user_aura(user_id)
    aura_discount_text = ""
    if aura and aura["type"] == "shop":
      discount_percent = aura["percent"]
      discounted = int(round(final_price * (1 - discount_percent / 100.0)))
      final_price = max(1, discounted)
      aura_discount_text = f"\n✨ Применена аура «{esc(aura['name'])}» (-{discount_percent}%)!"

    if tokens < final_price:
      await callback.answer(
          f"❌ Недостаточно токенов!\nЦена: {final_price} токенов, а у вас:"
          f" {tokens}.",
          show_alert=True,
      )
      return

    new_tokens = tokens - final_price
    await db.execute(
        "UPDATE users SET tokens = ?, bought_folders ="
        " COALESCE(bought_folders, 0) + 1 WHERE user_id = ?",
        (new_tokens, user_id),
    )
    await db.execute(
        "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
        (p_id,),
    )
    await db.commit()

    if referrer_id and referrer_id != 0:
      ref_bonus = round(final_price * 0.15)
      if ref_bonus > 0:
        await db.execute(
            "UPDATE users SET ref_income = COALESCE(ref_income, 0) + ? WHERE user_id = ?",
            (ref_bonus, referrer_id),
        )
        await db.execute(
            """
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
            VALUES (?, ?, 0, 0, 0, 0)
            ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
            """,
            (referrer_id, ref_bonus, ref_bonus),
        )
        await db.commit()
        try:
          await bot.send_message(
              referrer_id,
              f"💰 Реферальный бонус!\nВаш реферал совершил покупку.\n🎁 Вам"
              f" начислено {ref_bonus} токенов (15% от покупки).",
          )
        except Exception:
          pass

  if buyers_count == 0:
    text = (
        f"🎉 Поздравляем! Ты первый покупатель фулла {esc(title)}!\n🔥"
        f" Применилась скидка 80%!"
        f"{aura_discount_text}\n"
        f"Списано: {final_price} токенов.\n💲 Ваш"
        f" остаток: {new_tokens} токенов.\n\n🔗 Твоя ссылка на скачивание"
        f" (нажми, чтобы показать):\n{hidden_link(link)}"
    )
  else:
    text = (
        f"✅ Спасибо за покупку фула {esc(title)}!"
        f"{aura_discount_text}\n"
        f"💰 Списано: {final_price} токенов. Остаток: {new_tokens} токенов.\n\n🔗 Твоя"
        f" ссылка на скачивание (нажми, чтобы показать):\n{hidden_link(link)}"
    )

  await callback.message.answer(text, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "edit_product_list")
async def edit_product_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT id, title FROM products") as cursor:
      products = await cursor.fetchall()

  if not products:
    await callback.message.answer("📭 Нет товаров для редактирования.")
    await callback.answer()
    return

  keyboard_buttons = [
      [InlineKeyboardButton(text=f"✏️ {p[1]}", callback_data=f"edit_prod_{p[0]}")]
      for p in products
  ]
  await callback.message.answer(
      "Выбери товар для редактирования:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("edit_prod_"))
async def select_edit_field(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[2])
  await state.update_data(product_id=p_id)

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT title, price, link FROM products WHERE id = ?", (p_id,)
    ) as cursor:
      product = await cursor.fetchone()

  if not product:
    await callback.answer("❌ Товар не найден.", show_alert=True)
    return

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📝 Название", callback_data="field_title"
              ),
              InlineKeyboardButton(
                  text="💰 Цену", callback_data="field_price"
              ),
          ],
          [InlineKeyboardButton(text="🔗 Ссылку", callback_data="field_link")],
      ]
  )
  await callback.message.answer(
      f"✏️ <b>Редактирование товара:</b> {esc(product[0])}\n💰 Текущая цена:"
      f" {esc(product[1])} токенов\n🔗 Ссылка: {esc(product[2])}\n\nЧто"
      " именно хочешь изменить?",
      reply_markup=keyboard,
  )
  await callback.answer()


@router.callback_query(F.data.startswith("field_"))
async def prompt_new_value(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  field = callback.data.split("_")[1]
  await state.update_data(field=field)
  field_names = {
      "title": "новое название",
      "price": "новую цену в токенах",
      "link": "новую ссылку",
  }
  await callback.message.answer(f"Введи {field_names[field]} для этого товара:")
  await state.set_state(EditProduct.new_value)
  await callback.answer()


@router.callback_query(F.data == "add_product")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer(
      "📝 Введи название фула (например: Казань, Пантера):"
  )
  await state.set_state(AddProduct.title)
  await callback.answer()


@router.callback_query(F.data == "delete_product_list")
async def delete_product_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT id, title FROM products") as cursor:
      products = await cursor.fetchall()

  if not products:
    await callback.message.answer("📭 Нет товаров для удаления.")
    await callback.answer()
    return

  keyboard_buttons = [
      [
          InlineKeyboardButton(
              text=f"❌ Удалить: {p[1]}", callback_data=f"del_prod_{p[0]}"
          )
      ]
      for p in products
  ]
  await callback.message.answer(
      "Выбери товар для удаления:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
  )
  await callback.answer()


@router.callback_query(F.data.startswith("del_prod_"))
async def delete_product_execute(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[2])
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute("DELETE FROM products WHERE id = ?", (p_id,))
    await db.commit()

  await callback.answer("Товар удален!", show_alert=True)


@router.callback_query(F.data == "start_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await state.clear()
  await callback.message.answer(
      "📢 Отправь текст, картинку или видео для рассылки всем пользователям бота:"
  )
  await state.set_state(BroadcastState.message)
  await callback.answer()


# --- ОБМЕНЫ ССЫЛКАМИ ---

EXCHANGE_COST = 1
RESTRICT_HOURS = 72
MAX_OPEN_OFFERS = 5
EX_PAGE_SIZE = 5
EX_SEARCH = {}
LINK_RE = re.compile(r"^(https://mega\.nz/(file|folder)/\S+|https://cloud\.mail\.ru/\S+)$", re.I)
LINK_RULE = (
    "⚠️ <b>Ссылка обязана быть рабочей!</b> Поддерживаются:\n"
    "• <code>https://mega.nz/file/...</code>\n"
    "• <code>https://mega.nz/folder/...</code>\n"
    "• <code>https://cloud.mail.ru/...</code>"
)
LINK_ERROR = (
    "❌ Ссылка не подходит. Она должна быть с <b>mega.nz</b> (файл/папка) "
    "или <b>cloud.mail.ru</b>. Отправь ещё раз."
)


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


async def send_warn_notice(bot: Bot, target_id: int, warns: int, banned: bool, reason: str = None):
  reason_str = f"\n📝 Причина: <b>{esc(reason)}</b>" if reason else ""
  if banned:
    text = (
        f"🚫 Вы получили {warns}/{MAX_WARNS} предупреждений.{reason_str} Ваш профиль"
        " заблокирован навсегда, премиум-подписка снята, токены обнулены."
    )
  else:
    text = (
        f"⚠️ Вам выдано предупреждение ({warns}/{MAX_WARNS}).{reason_str} При"
        f" {MAX_WARNS} предупреждениях профиль будет заблокирован,"
        " премиум снят, токены обнулены."
    )
  try:
    await bot.send_message(target_id, text)
  except Exception:
    pass


async def ex_menu_text() -> str:
  return (
      "🔄 <b>Обмены фуллами</b>\nВыставляй свой обмен или бери чужой."
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
  await ex_show(callback, "ГАЛЯ ОТМЕНАА.", ex_kb([[("⬅️ В обмены", "ex:menu")]]))
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
  text = (message.text or "").strip()[:60]
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
  text = (message.text or "").strip()[:60]
  if not text:
    await message.answer("Напиши текстом, что хочешь получить.")
    return
  await state.update_data(want=text)
  await state.set_state(NewOfferState.link)
  await message.answer(
      f"Отправь <b>свою ссылку на фулл</b> для обмена.\n{LINK_RULE}",
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
      f"✅ Обмен #{offer_id} выставлен: <b>{esc(data['give'])}</b> →"
      f" <b>{esc(data['want'])}</b>.\nТебе придёт уведомление, когда кто-то"
      " возьмётся за него.",
      reply_markup=ex_kb([[("🔄 Обмены", "ex:menu")]]),
  )


async def ex_render_list(event, user_id: int, page: int):
  q = EX_SEARCH.get(user_id)
  where, args = "status = 'open'", []
  if q:
    if q.isdigit():
      where += " AND (owner_id = ? OR id = ?)"
      args += [int(q), int(q)]
    else:
      for w in q.lower().split()[:3]:
        where += " AND search LIKE ?"
        args.append(f"%{w}%")
  total = (await ex_one(f"SELECT COUNT(*) n FROM ex_offers WHERE {where}", args))["n"]
  pages = max(1, -(-total // EX_PAGE_SIZE))
  page = max(0, min(page, pages - 1))
  rows = await ex_all(
      f"SELECT * FROM ex_offers WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
      args + [EX_PAGE_SIZE, page * EX_PAGE_SIZE],
  )
  head = "📋 <b>Открытые обмены</b>"
  if q:
    head += f"\n🔍 Поиск: <i>{esc(q)}</i>"
  head += f"\nНайдено: {total} · стр. {page + 1}/{pages}"
  if not rows:
    head += "\n\nПока пусто."
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
      "Напиши слово (или два): <b>Пантера</b>, <b>Сердцеед</b>\n"
      "Или ID человека / названия обмена — только цифры.",
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
  if not o or o["status"] != "open":
    await callback.answer("Этот обмен уже недоступен.", show_alert=True)
    return
  
  text = (
      f"🔄 <b>Обмен #{o['id']}</b>\nОтдаёт: <b>{esc(o['give'])}</b>\nХочет:"
      f" <b>{esc(o['want'])}</b>\nОбменщик ID: <code>{o['owner_id']}</code>"
  )
  
  if uid == ADMIN_ID:
    text += f"\n🔗 Ссылка владельца (админ-доступ): {esc(o['owner_link'])}"

  text += f"\nСтоимость обмена: {EXCHANGE_COST} токен"

  if o["owner_id"] == uid:
    kb = ex_kb([[("🗑 Удалить", f"ex:del:{offer_id}")], [("⬅️ Назад", "ex:list:0")]])
  else:
    kb = ex_kb([
        [("🤝 Провести обмен", f"ex:take:{offer_id}")],
        [("⬅️ Назад", "ex:list:0")],
    ])
  await ex_show(callback, text, kb)
  await callback.answer()


async def ex_render_my(callback: CallbackQuery):
  rows = await ex_all(
      "SELECT * FROM ex_offers WHERE owner_id = ? AND status IN"
      " ('open', 'taken') ORDER BY id DESC LIMIT 15",
      (callback.from_user.id,),
  )
  icon = {"open": "🟢", "taken": "✅"}
  buttons = [
      [(f"🟢 #{r['id']} {r['give']} → {r['want']}"[:60], f"ex:view:{r['id']}")]
      for r in rows
      if r["status"] == "open"
  ]
  body = "\n".join(
      f"{icon[r['status']]} #{r['id']} {esc(r['give'])} → {esc(r['want'])}"
      for r in rows
  )
  buttons.append([("⬅️ В обмены", "ex:menu")])
  await ex_show(
      callback, "📦 <b>Мои обмены</b>\n" + (body or "Пока пусто."), ex_kb(buttons)
  )


@router.callback_query(F.data == "ex:my")
async def ex_my(callback: CallbackQuery):
  await ex_render_my(callback)
  await callback.answer()


@router.callback_query(F.data.startswith("ex:del:"))
async def ex_del(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  rc, _ = await ex_exec(
      "UPDATE ex_offers SET status = 'deleted' WHERE id = ? AND owner_id = ?"
      " AND status = 'open'",
      (offer_id, callback.from_user.id),
  )
  await callback.answer("Удалено." if rc else "Не получилось.", show_alert=not rc)
  await ex_render_my(callback)


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
  bal = await ex_one("SELECT COALESCE(tokens, 0) t FROM users WHERE user_id = ?", (uid,))
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
      "Отправь <b>свою ссылку на фулл</b>. После этого спишется"
      f" {EXCHANGE_COST} токен и ты получишь ссылку обменщика.\n{LINK_RULE}",
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
  await message.answer(
      f"🤝 <b>Обмен #{offer_id}</b>: {esc(o['give'])} → {esc(o['want'])}\n"
      f"Ссылка обменщика (ID <code>{o['owner_id']}</code>):\n"
      f"{esc(o['owner_link'])}\n\nСписано {EXCHANGE_COST} токен.",
      disable_web_page_preview=True,
  )
  try:
    await bot.send_message(
        o["owner_id"],
        f"🔔 ID <code>{uid}</code> взялся за твой обмен #{offer_id}:"
        f" {esc(o['give'])} → {esc(o['want'])}\nЕго ссылка:\n{esc(link)}",
        reply_markup=ex_kb([[("🚨 Пожаловаться", f"ex:rep:{offer_id}")]]),
        disable_web_page_preview=True,
    )
  except Exception:
    pass


@router.callback_query(F.data.startswith("ex:rep:"))
async def ex_report(callback: CallbackQuery, bot: Bot):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "taken" or o["owner_id"] != uid:
    await callback.answer("Жалоба недоступна.", show_alert=True)
    return
  accused = o["taker_id"]
  try:
    _, report_id = await ex_exec(
        "INSERT INTO ex_reports (offer_id, complainer_id, accused_id, created)"
        " VALUES (?, ?, ?, ?)",
        (offer_id, uid, accused, int(datetime.datetime.now().timestamp())),
    )
  except aiosqlite.IntegrityError:
    await callback.answer("Ты уже отправил жалобу по этому обмену.", show_alert=True)
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
  try:
    await bot.send_message(
        accused,
        f"🚨 На вас подана жалоба по обмену #{offer_id}. Обмены и покупки в"
        " магазине временно ограничены до решения администратора.",
    )
  except Exception:
    pass
  try:
    await bot.send_message(
        ADMIN_ID,
        f"🚨 <b>Жалоба #{report_id}</b>\nID <code>{uid}</code> (обменщик)"
        f" пожаловался на ID <code>{accused}</code> (взялся за обмен)\n"
        f"Обмен #{offer_id}: {esc(o['give'])} → {esc(o['want'])}\n"
        f"Ссылка обменщика: {esc(o['owner_link'])}\n"
        f"Ссылка взявшегося: {esc(o['taker_link'])}\n"
        f"Ограничения на ID {accused}: {RESTRICT_HOURS} ч или до твоего"
        " решения.",
        reply_markup=ex_kb([
            [("✅ Убрать ограничения", f"ex:ok:{report_id}")],
            [("✉️ Написать обменщику", f"ex:msg:{report_id}")],
        ]),
        disable_web_page_preview=True,
    )
  except Exception:
    logging.exception("Не удалось отправить жалобу админу")


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
  await ex_exec("UPDATE ex_reports SET status = 'released' WHERE id = ?", (report_id,))
  await ex_exec(
      "UPDATE users SET restricted_until = NULL WHERE user_id = ?",
      (r["accused_id"],),
  )
  try:
    await bot.send_message(
        r["accused_id"], "✅ Ограничения сняты администратором."
    )
  except Exception:
    pass
  warns, banned = await add_warn(r["complainer_id"], "Ложная жалоба на обмен")
  await send_warn_notice(bot, r["complainer_id"], warns, banned, "Ложная жалоба на обмен")
  await callback.answer("Готово.")
  try:
    await callback.message.edit_reply_markup(
        reply_markup=ex_kb([[("✉️ Написать обменщику", f"ex:msg:{report_id}")]])
    )
  except Exception:
    pass
  await callback.message.answer(
      f"✅ Жалоба #{report_id}: ограничения с ID <code>{r['accused_id']}</code>"
      f" сняты, обменщику ID <code>{r['complainer_id']}</code> выдано"
      f" предупреждение ({warns}/{MAX_WARNS})."
  )


@router.callback_query(F.data.startswith("ex:msg:"))
async def ex_admin_msg(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  report_id = int(callback.data.split(":")[2])
  await state.set_state(ExAdminMsgState.text)
  await state.update_data(report_id=report_id)
  await callback.message.answer(
      f"Напиши сообщение для обменщика по жалобе #{report_id}"
      " (/cancel — отмена):"
  )
  await callback.answer()


@router.message(StateFilter(ExAdminMsgState.text), Command("cancel"))
async def ex_admin_msg_cancel(message: Message, state: FSMContext):
  await state.clear()
  await message.answer("Отменено.")


@router.message(StateFilter(ExAdminMsgState.text))
async def ex_admin_msg_send(message: Message, state: FSMContext, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    return
  report_id = (await state.get_data())["report_id"]
  await state.clear()
  r = await ex_one("SELECT * FROM ex_reports WHERE id = ?", (report_id,))
  if not r:
    await message.answer("Жалоба не найдена.")
    return
  try:
    await bot.send_message(
        r["complainer_id"],
        f"✉️ <b>Сообщение от администрации:</b>\n{esc(message.text or '')}",
    )
    await message.answer("Отправлено.")
  except Exception:
    await message.answer("Не доставлено (человек заблокировал бота).")


# --- РУЛЕТКА 50/50 (с балансиром и аурой) ---

ROULETTE_MIN = 2
ROULETTE_MAX = 10
_roulette_locks = {}


class RouletteState(StatesGroup):
  bet = State()


def roulette_decide(current_tokens: int, roll_win: bool, aura_percent: int) -> bool:
  # Если у чела аура рулетки, повышаем шанс победы на aura_percent
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

      # Проверка ауры рулетки
      aura = await get_user_aura(user_id)
      aura_percent = aura["percent"] if (aura and aura["type"] == "roulette") else 0

      win = roulette_decide(current_bal, random.random() < 0.5, aura_percent)

      await db.execute(
          "INSERT OR IGNORE INTO roulette_state (user_id) VALUES (?)",
          (user_id,),
      )
      await db.execute(
          "UPDATE roulette_state SET games = COALESCE(games, 0) + 1,"
          " wins = COALESCE(wins, 0) + ? WHERE user_id = ?",
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
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
  balance = row[0] if row else 0
  return (
      "🎲 <b>Рулетка 50/50</b>\n\n"
      "Ставишь токены — и либо удваиваешь их, либо теряешь. Шанс выиграть"
      " 50/50!\n"
      f"Выиграл: получаешь ставку ×2 (ставка 4 → на баланс вернётся 8).\n"
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
  raw = (message.text or "").strip()
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


@router.errors()
async def global_error_handler(event: ErrorEvent):
  logging.exception("Необработанная ошибка: %s", event.exception)


async def weekly_reset_loop(bot: Bot):
  while True:
    await asyncio.sleep(604800)
    try:
      async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(REF_TOP_SQL, (1,)) as cursor:
          winner = await cursor.fetchone()

        if winner:
          winner_id = winner[0]
          new_exp = datetime.datetime.now() + datetime.timedelta(days=14)
          expire_date = new_exp.strftime(DT_FORMAT)
          formatted_date = new_exp.strftime("%d.%m.%Y")

          await db.execute(
              "UPDATE users SET is_premium = 1, premium_expires = ? WHERE"
              " user_id = ?",
              (expire_date, winner_id),
          )
          await db.commit()

          try:
            await bot.send_message(
                winner_id,
                "🏆 <b>Поздравляем! Вы заняли 1 место в еженедельном топе"
                " рефералов!</b>\n👑 Вам автоматически начислена"
                f" Премиум-подписка на <b>14 дней</b> (до {formatted_date})!",
            )
          except Exception:
            pass

        await db.execute(
            "UPDATE users SET referrer_id = 0, ref_income = 0"
            " WHERE ref_activated = 1"
        )
        await db.execute("UPDATE users SET ref_extra = 0")
        await db.commit()

      try:
        await bot.send_message(
            ADMIN_ID,
            "🔄 Еженедельный топ рефералов успешно сброшен, победителю выдан"
            " Премиум на 14 дней!",
        )
      except Exception:
        pass

    except Exception:
      logging.exception("Ошибка при еженедельном сбросе топа")


async def auto_backup_loop(bot: Bot):
  backup_message_ids = []
  while True:
    await asyncio.sleep(300)
    try:
      if os.path.exists(DB_NAME):
        document = FSInputFile(DB_NAME)
        sent_msg = await bot.send_document(
            ADMIN_ID,
            document,
            caption=(
                "⏱ база данных обновилась:\n"
                f"📅 {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            ),
        )
        backup_message_ids.append(sent_msg.message_id)

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

  background_tasks = [
      asyncio.create_task(auto_backup_loop(bot)),
      asyncio.create_task(weekly_reset_loop(bot)),
      asyncio.create_task(lottery_loop(bot)),
  ]

  await bot.delete_webhook(drop_pending_updates=True)
  print(
      "Бот успешно запущен, авто-бэкап (5 минут), еженедельный сброс и"
      f" лотерея активны, веб-сервер слушает порт {PORT}!"
  )
  try:
    await dp.start_polling(bot)
  finally:
    for task in background_tasks:
      task.cancel()


if __name__ == "__main__":
  asyncio.run(main())
