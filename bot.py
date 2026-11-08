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

# ВАЖНО: токен только из переменной окружения TOKEN (на хостинге).
TOKEN = os.getenv("TOKEN")
if not TOKEN:
  sys.exit("Не задана переменная окружения TOKEN")

PORT = int(os.getenv("PORT", 10000))
DB_NAME = "bot.db"

ADMIN_ID = 5681420389

# Канал для обязательной подписки.
# Для каналов Telegram ID = -100 + число (2925107145 -> -1002925107145).
CHANNEL_ID = -1002925107145
CHANNEL_URL = "https://t.me/iiris_cnn_bot"

DT_FORMAT = "%Y-%m-%d %H:%M:%S"

# Модерация
MAX_WARNS = 3
PERM_BAN = "2999-12-31 00:00:00"

# Лотерея
LOTTERY_HOURS = 3
LOTTERY_RARE_CHANCE = 0.10  # шанс редкого крупного приза (9-20 токенов)
LOTTERY_NOTIFY_PRIZE = 14  # от этого приза и выше всем приходит уведомление о старте
LOTTERY_MIN_PLAYERS = 2  # минимум участников, чтобы разыграть приз
LOTTERY_BIG_PRIZE = 15  # приз от этого значения и выше...
LOTTERY_BIG_MIN_PLAYERS = 4  # ...требует вот столько участников


def lottery_min_players(prize: int) -> int:
  return LOTTERY_BIG_MIN_PLAYERS if prize >= LOTTERY_BIG_PRIZE else LOTTERY_MIN_PLAYERS


def esc(value) -> str:
  """Экранирует текст для HTML-разметки Telegram."""
  return html.escape(str(value), quote=False)


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
            ref_activated INTEGER DEFAULT 1,
            premium_expires TEXT DEFAULT NULL,
            warns INTEGER DEFAULT 0,
            banned_until TEXT DEFAULT NULL
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

    # Миграция: добавляем недостающие колонки в таблицы старой базы
    migrations = {
        "users": {
            "tokens": "INTEGER DEFAULT 0",
            "is_premium": "INTEGER DEFAULT 0",
            "bought_folders": "INTEGER DEFAULT 0",
            "referrer_id": "INTEGER DEFAULT 0",
            "ref_activated": "INTEGER DEFAULT 1",
            "premium_expires": "TEXT DEFAULT NULL",
            "warns": "INTEGER DEFAULT 0",
            "banned_until": "TEXT DEFAULT NULL",
            "exchanges": "INTEGER DEFAULT 0",
            "restricted_until": "TEXT DEFAULT NULL",
        },
        "products": {
            "buyers_count": "INTEGER DEFAULT 0",
        },
        "promocodes": {
            "max_uses": "INTEGER DEFAULT 0",
            "used_count": "INTEGER DEFAULT 0",
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


# --- БАНЫ И ПРЕДУПРЕЖДЕНИЯ ---


async def get_active_ban(user_id: int):
  """Возвращает datetime окончания бана или None, если бана нет."""
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT banned_until FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
    if not row or not row[0]:
      return None
    try:
      until = datetime.datetime.strptime(row[0], DT_FORMAT)
    except Exception:
      return None
    if until <= datetime.datetime.now():
      await db.execute(
          "UPDATE users SET banned_until = NULL WHERE user_id = ?", (user_id,)
      )
      await db.commit()
      return None
    return until


def ban_text(until: datetime.datetime) -> str:
  if until.year >= 2900:
    return "🚫 Ваш профиль заблокирован навсегда."
  return f"🚫 Ваш профиль заблокирован до {until.strftime('%d.%m.%Y %H:%M')}."


async def ensure_user(db, user_id: int) -> None:
  await db.execute(
      "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
      " referrer_id, ref_activated) VALUES (?, 0, 0, 0, 0, 1)"
      " ON CONFLICT(user_id) DO NOTHING",
      (user_id,),
  )


async def ban_user(target_id: int, days: int) -> str:
  """Банит на days дней (0 = навсегда). Возвращает текст срока."""
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
        "UPDATE users SET banned_until = ? WHERE user_id = ?",
        (until_str, target_id),
    )
    await db.commit()
  return text


async def unban_user(target_id: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "UPDATE users SET banned_until = NULL WHERE user_id = ?", (target_id,)
    )
    await db.commit()


async def add_warn(target_id: int):
  """Добавляет предупреждение. Возвращает (кол-во варнов, забанен ли)."""
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
      await db.execute(
          "UPDATE users SET banned_until = ?, is_premium = 0,"
          " premium_expires = NULL, tokens = 0 WHERE user_id = ?",
          (PERM_BAN, target_id),
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
  """Забирает токены (не уходит ниже 0). Возвращает новый баланс."""
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


# --- ОБЯЗАТЕЛЬНАЯ ПОДПИСКА НА КАНАЛ ---


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
    until = await get_active_ban(user.id)
    if until is not None:
      if isinstance(event, CallbackQuery):
        await event.message.answer(ban_text(until))
        await event.answer()
      else:
        await event.answer(ban_text(until))
      return None

    # /start пропускаем, чтобы реферальная ссылка успела сработать
    if isinstance(event, Message) and (event.text or "").startswith("/start"):
      return await handler(event, data)

    # кнопку проверки подписки пропускаем
    if isinstance(event, CallbackQuery) and event.data == "check_sub":
      return await handler(event, data)

    if await is_subscribed(bot, user.id):
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
  ]
  if user_id == ADMIN_ID:
    kb.append([KeyboardButton(text="⚙️ Админ-панель")])
  return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# --- ОБЩИЕ ФУНКЦИИ: ВЫДАЧА ТОКЕНОВ, ПРЕМИУМА, АКТИВАЦИЯ ПРОМОКОДА ---


async def grant_tokens(target_id: int, amount: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
        INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
        VALUES (?, ?, 0, 0, 0, 1)
        ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
        """,
        (target_id, amount, amount),
    )
    await db.commit()


async def grant_premium(target_id: int, days: int) -> datetime.datetime:
  """Выдает премиум на days дней (продлевает, если уже активен)."""
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
        VALUES (?, 0, 1, 0, 0, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
        """,
        (target_id, expire_str, expire_str),
    )
    await db.commit()
  return new_expire


async def apply_promocode(user_id: int, code_text: str) -> str:
  """Активирует промокод и возвращает текст ответа пользователю."""
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
            VALUES (?, ?, 0, 0, 0, 1)
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
            VALUES (?, 0, 1, 0, 0, 1, ?)
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

      # Сначала запись в БД, потом ответ пользователю
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


# --- ЛОТЕРЕЯ ---


def roll_lottery_params():
  """Возвращает (цена участия, приз). Ночью 1:00-8:00 (Киев): 1 -> 4."""
  hour = now_local().hour
  if 1 <= hour < 8:
    return 1, 4
  # Обычно приз 5-8 токенов, а крупный 9-20 выпадает редко
  if random.random() < LOTTERY_RARE_CHANCE:
    prize = random.randint(9, 20)
  else:
    prize = random.randint(5, 8)
  # чем больше приз, тем дороже участие: 5 -> 1 токен ... 20 -> 4 токена
  cost = 1 + (prize - 5) * 3 // 15
  return cost, prize


_bg_tasks = set()


async def notify_big_lottery(bot: Bot, cost: int, prize: int) -> None:
  """Рассылает всем пользователям уведомление о крупной лотерее."""
  text = (
      "🔥 <b>Началась крупная лотерея!</b>\n\n"
      f"🏆 Приз: <b>от {prize} токенов</b> (+ взносы участников)\n"
      f"🎟 Участие: <b>{cost} токен(ов)</b>\n"
      f"👥 Минимум участников: <b>{lottery_min_players(prize)}</b>\n"
      f"⏱ Длится {LOTTERY_HOURS} ч."
  )
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT user_id, banned_until FROM users") as cur:
      rows = await cur.fetchall()
  now = datetime.datetime.now()
  for uid, banned_until in rows:
    if banned_until:
      try:
        if datetime.datetime.strptime(banned_until, DT_FORMAT) > now:
          continue
      except Exception:
        pass
    try:
      await bot.send_message(uid, text, reply_markup=lottery_join_keyboard())
    except Exception:
      pass
    await asyncio.sleep(0.05)


def lottery_join_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text="🎰 Участвовать", callback_data="lottery_open")]
      ]
  )


async def create_lottery_round(bot: Bot = None) -> None:
  cost, prize = roll_lottery_params()
  ends_at = datetime.datetime.now() + datetime.timedelta(hours=LOTTERY_HOURS)
  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        "INSERT INTO lottery_rounds (entry_cost, prize, ends_at, finished)"
        " VALUES (?, ?, ?, 0)",
        (cost, prize, ends_at.strftime(DT_FORMAT)),
    )
    await db.commit()
  if bot is not None and prize >= LOTTERY_NOTIFY_PRIZE:
    task = asyncio.create_task(notify_big_lottery(bot, cost, prize))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def get_current_round():
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT id, entry_cost, prize, ends_at FROM lottery_rounds"
        " WHERE finished = 0 ORDER BY id DESC LIMIT 1"
    ) as cur:
      return await cur.fetchone()


def lottery_again_keyboard() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🎰 Попытать удачу ещё раз", callback_data="lottery_open"
              )
          ]
      ]
  )


async def build_lottery_view(user_id: int):
  """Возвращает (текст, клавиатура) для экрана лотереи."""
  rnd = await get_current_round()
  if not rnd:
    return "⏳ Новый розыгрыш скоро начнётся. Загляни чуть позже!", None

  round_id, cost, prize, ends_at_str = rnd
  ends_at = datetime.datetime.strptime(ends_at_str, DT_FORMAT)
  left_min = max(0, int((ends_at - datetime.datetime.now()).total_seconds() // 60))
  hours, minutes = divmod(left_min, 60)

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT COUNT(*) FROM lottery_participants WHERE round_id = ?",
        (round_id,),
    ) as cur:
      players = (await cur.fetchone())[0]
    async with db.execute(
        "SELECT 1 FROM lottery_participants WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ) as cur:
      joined = await cur.fetchone() is not None

  text = (
      "🎰 <b>Лотерея</b>\n\n"
      f"🏆 Приз: <b>{prize + cost * players} токенов</b>"
      f" ({prize} + {cost * players} от взносов участников)\n"
      f"🎟 Участие: <b>{cost} токен(ов)</b>\n"
      f"👥 Участников: <b>{players}</b> (минимум для розыгрыша:"
      f" {lottery_min_players(prize)})\n"
      f"⏱ До конца розыгрыша: <b>{hours} ч {minutes} мин</b>\n\n"
      "Победитель выбирается случайно среди всех участников."
      " Токены за участие списываются сразу."
  )
  if joined:
    return text + "\n\n✅ Ты уже участвуешь в этом розыгрыше!", None

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text=f"🎟 Участвовать ({cost} токен.)",
                  callback_data=f"lottery_join_{round_id}",
              )
          ]
      ]
  )
  return text, keyboard


async def finish_lottery_round(bot: Bot, round_id: int, prize: int) -> None:
  async with aiosqlite.connect(DB_NAME) as db:
    # Сначала помечаем раунд завершённым, чтобы не выдать приз дважды
    await db.execute(
        "UPDATE lottery_rounds SET finished = 1 WHERE id = ?", (round_id,)
    )
    await db.commit()
    async with db.execute(
        "SELECT user_id FROM lottery_participants WHERE round_id = ?",
        (round_id,),
    ) as cur:
      players = [row[0] for row in await cur.fetchall()]

    async with db.execute(
        "SELECT entry_cost FROM lottery_rounds WHERE id = ?", (round_id,)
    ) as cur:
      row = await cur.fetchone()
    cost = row[0] if row and row[0] else 0

    min_players = lottery_min_players(prize)
    if players and len(players) < min_players:
      # Мало участников: приз не разыгрывается, токены за участие возвращаем
      for uid in players:
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE user_id = ?",
            (cost, uid),
        )
      await db.commit()
      for uid in players:
        try:
          await bot.send_message(
              uid,
              "😔 В розыгрыше не набралось нужное число участников"
              f" ({len(players)} из {min_players}). Приз не разыгран, тебе"
              f" вернули <b>{cost} токен(ов)</b>.\n\nХотите ещё попытать"
              " удачу?",
              reply_markup=lottery_again_keyboard(),
          )
        except Exception:
          pass
        await asyncio.sleep(0.05)
      return

    # Каждый взнос участника добавляется к призу
    prize = prize + cost * len(players)
    winner = random.choice(players) if players else None
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
    try:
      if uid == winner:
        await bot.send_message(
            uid,
            f"🎉 <b>Ты выиграл в лотерее!</b>\n🏆 Тебе начислено:"
            f" <b>{prize} токенов</b>.\n\nХотите ещё попытать удачу?",
            reply_markup=lottery_again_keyboard(),
        )
      else:
        await bot.send_message(
            uid,
            "😔 В этот раз удача была не на твоей стороне. Победитель уже"
            " получил приз.\n\nХотите ещё попытать удачу?",
            reply_markup=lottery_again_keyboard(),
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

      round_id, cost, prize, ends_at_str = rnd
      ends_at = datetime.datetime.strptime(ends_at_str, DT_FORMAT)
      delay = (ends_at - datetime.datetime.now()).total_seconds()
      if delay > 0:
        await asyncio.sleep(delay)
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
  round_id = int(callback.data.split("_")[2])

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT entry_cost, ends_at, finished FROM lottery_rounds WHERE id = ?",
        (round_id,),
    ) as cur:
      rnd = await cur.fetchone()

    if not rnd or rnd[2] == 1:
      await callback.answer("⏳ Этот розыгрыш уже завершён.", show_alert=True)
      return
    cost = rnd[0]
    try:
      ends_at = datetime.datetime.strptime(rnd[1], DT_FORMAT)
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

    # Списание токенов одним запросом: сработает, только если хватает токенов
    cur = await db.execute(
        "UPDATE users SET tokens = tokens - ? WHERE user_id = ? AND"
        " COALESCE(tokens, 0) >= ?",
        (cost, user_id, cost),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(
          f"❌ Недостаточно токенов! Участие стоит {cost}.", show_alert=True
      )
      return
    await db.execute(
        "INSERT INTO lottery_participants (round_id, user_id) VALUES (?, ?)",
        (round_id, user_id),
    )
    await db.commit()

  await callback.answer("🎟 Ты в игре! Удачи!", show_alert=True)
  await callback.message.answer(
      f"🎟 Ты участвуешь в лотерее! Списано: <b>{cost} токен(ов)</b>.\n"
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

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT referrer_id FROM users WHERE user_id = ?", (user_id,)
    ) as cursor:
      existing_user = await cursor.fetchone()

    if existing_user is None:
      ref_to_save = referrer_id if referrer_id else 0
      await db.execute(
          "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
          " referrer_id, ref_activated) VALUES (?, 0, 0, 0, ?, 1)",
          (user_id, ref_to_save),
      )
      await db.commit()

      if referrer_id:
        await db.execute(
            """
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
            VALUES (?, 7, 0, 0, 0, 1)
            ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + 7
            """,
            (referrer_id,),
        )
        await db.commit()
        try:
          await bot.send_message(
              referrer_id,
              "🎉 По вашей рефке зарегистрировался новый"
              " фембойчик!\n❤️ Вам начислено 7 токенов!",
          )
        except Exception:
          pass

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

  # /promocode КОД — активируется сразу
  if code_text:
    reply = await apply_promocode(user_id, code_text)
    await message.answer(reply, reply_markup=get_main_keyboard(user_id))
    return

  # /promocode без кода — спрашиваем код отдельным сообщением
  await message.answer("🎟 Введите промокод:")
  await state.set_state(PromocodeState.code)


# --- АДМИН-КОМАНДЫ ---


def parse_args(command: CommandObject, count: int):
  """Разбирает аргументы команды: нужно ровно count целых чисел."""
  parts = (command.args or "").split()
  if len(parts) != count or not all(p.isdigit() for p in parts):
    return None
  return [int(p) for p in parts]


@router.message(Command("premium"))
async def cmd_premium(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  usage = (
      "ℹ️ Использование:\n<code>/premium add [дни] [ID]</code>\n"
      "Пример: <code>/premium add 30 5681420389</code>"
  )
  args = (command.args or "").split()
  if (
      len(args) != 3
      or args[0].lower() != "add"
      or not args[1].isdigit()
      or not args[2].isdigit()
      or int(args[1]) <= 0
  ):
    await message.answer(usage)
    return

  days = int(args[1])
  target_id = int(args[2])

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


@router.message(Command("token", "tokens"))
async def cmd_token(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  usage = (
      "ℹ️ Использование:\n<code>/token add [сколько] [ID]</code>\n"
      "Пример: <code>/token add 50 5681420389</code>"
  )
  args = (command.args or "").split()
  if (
      len(args) != 3
      or args[0].lower() != "add"
      or not args[1].isdigit()
      or not args[2].isdigit()
      or int(args[1]) <= 0
  ):
    await message.answer(usage)
    return

  amount = int(args[1])
  target_id = int(args[2])

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
  args = parse_args(command, 2)
  if not args:
    await message.answer(
        "ℹ️ Использование:\n<code>/ban [ID] [дни]</code>\n"
        "Пример: <code>/ban 5681420389 7</code>\n"
        "<i>(0 дней = бан навсегда)</i>"
    )
    return
  target_id, days = args
  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя забанить админа.")
    return
  term = await ban_user(target_id, days)
  await message.answer(
      f"🚫 Пользователь <code>{target_id}</code> заблокирован {term}."
  )
  try:
    await bot.send_message(
        target_id, f"🚫 Ваш профиль заблокирован {term}."
    )
  except Exception:
    pass


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer(
        "ℹ️ Использование:\n<code>/unban [ID]</code>\n"
        "Пример: <code>/unban 5681420389</code>"
    )
    return
  target_id = args[0]
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
  args = parse_args(command, 1)
  if not args:
    await message.answer(
        "ℹ️ Использование:\n<code>/warn [ID]</code>\n"
        "Пример: <code>/warn 5681420389</code>"
    )
    return
  target_id = args[0]
  if target_id == ADMIN_ID:
    await message.answer("❌ Нельзя выдать предупреждение админу.")
    return
  warns, banned = await add_warn(target_id)
  if banned:
    await message.answer(
        f"⚠️ Пользователь <code>{target_id}</code> получил"
        f" {warns}/{MAX_WARNS} предупреждений и заблокирован навсегда"
        " (премиум снят, токены обнулены)."
    )
    try:
      await bot.send_message(
          target_id,
          f"🚫 Вы получили {warns}/{MAX_WARNS} предупреждений. Ваш профиль"
          " заблокирован навсегда, премиум-подписка снята, токены обнулены.",
      )
    except Exception:
      pass
  else:
    await message.answer(
        f"⚠️ Пользователю <code>{target_id}</code> выдано предупреждение"
        f" ({warns}/{MAX_WARNS})."
    )
    try:
      await bot.send_message(
          target_id,
          f"⚠️ Вам выдано предупреждение ({warns}/{MAX_WARNS}). При"
          f" {MAX_WARNS} предупреждениях профиль будет заблокирован,"
          " премиум снят, токены обнулены.",
      )
    except Exception:
      pass


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, command: CommandObject, bot: Bot):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer(
        "ℹ️ Использование:\n<code>/unwarn [ID]</code>\n"
        "Пример: <code>/unwarn 5681420389</code>"
    )
    return
  target_id = args[0]
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
  args = parse_args(command, 1)
  if not args:
    await message.answer(
        "ℹ️ Использование:\n<code>/unpremium [ID]</code>\n"
        "Пример: <code>/unpremium 5681420389</code>"
    )
    return
  target_id = args[0]
  await remove_premium(target_id)
  await message.answer(
      f"✅ Премиум у пользователя <code>{target_id}</code> снят."
  )


@router.message(Command("untoken"))
async def cmd_untoken(message: Message, command: CommandObject):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return
  args = parse_args(command, 2)
  if not args or args[1] <= 0:
    await message.answer(
        "ℹ️ Использование:\n<code>/untoken [ID] [число токенов]</code>\n"
        "Пример: <code>/untoken 5681420389 20</code>"
    )
    return
  target_id, amount = args
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
    # Забаненным токены не выдаём
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


@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, bot: Bot):
  if await is_subscribed(bot, callback.from_user.id):
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


# --- МЕНЮ И КНОПКИ (стоят выше состояний, чтобы кнопки всегда работали) ---


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
            " referrer_id, ref_activated) VALUES (?, 0, 0, 0, 0, 1)",
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
      # Текст кнопок не парсится как разметка, экранировать не нужно.
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
        " каждого приглашенного друга вы получаете 7 токенов!\n• Получайте 15%"
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

      async with db.execute(
          """
          SELECT u.user_id, u.is_premium, COUNT(r.user_id) as ref_count
          FROM users u
          JOIN users r ON r.referrer_id = u.user_id
          GROUP BY u.user_id
          HAVING ref_count > 0
          ORDER BY ref_count DESC
          LIMIT 10
          """
      ) as cursor:
        top_list = await cursor.fetchall()

    footer = (
        "<i>(каждую неделю сбрасывается топ и выдается премиум подписка на 14"
        " дней)</i>"
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
        "📌 Как получить токены?\n\n1. Возьмите фото."
        " https://t.me/JmcDeveloper/11\n\n2. Оставляйте в"
        " комментариях под постами/ответами другим людям. \n3. Отправляйте"
        " доказательства (скриншоты) в директ канала."
        " https://t.me/JmcDeveloper?direct\n\n💰 1 отправка фото в"
        " комментариях = 1 токен! ГЛАВНОЕ: тема должна касаться по типу"
        " сестры и брата и тд.\n\n📖 ДЕТАЛЬНО РАСПИСАЛ ТУТ:"
        " https://t.me/JmcDeveloper/18"
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


# --- ВВОД ПРОМОКОДА (после кнопки или /promocode без кода) ---


@router.message(StateFilter(PromocodeState.code))
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = (message.text or "").strip()
  await state.clear()
  reply = await apply_promocode(user_id, code_text)
  await message.answer(reply, reply_markup=get_main_keyboard(user_id))


# --- АДМИНКА: ВЫДАЧА ТОКЕНОВ И ПРЕМИУМА ---


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


# --- СОЗДАНИЕ ПРОМОКОДОВ И ТОВАРОВ ---


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


# --- CALLBACK QUERY HANDLERS ---


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
    await callback.answer(RESTRICTED_TEXT, show_alert=True)
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
          f" {esc(title)} бесплатно по подписке!\n\n🔗 Ссылка:"
          f" {esc(link)}"
      )
      await callback.message.answer(text)
      await callback.answer()
      return

    final_price = (
        max(1, int(base_price * 0.2)) if buyers_count == 0 else base_price
    )

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
            """
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
            VALUES (?, ?, 0, 0, 0, 1)
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
        f" Применилась скидка 80%!\nСписано: {final_price} токенов.\n💲 Ваш"
        f" остаток: {new_tokens} токенов.\n\n🔗 Твоя ссылка на скачивание:"
        f" {esc(link)}"
    )
  else:
    text = (
        f"✅ Спасибо за покупку фула {esc(title)}!\n💰 Списано:"
        f" {final_price} токенов. Остаток: {new_tokens} токенов.\n\n🔗 Твоя"
        f" ссылка на скачивание: {esc(link)}"
    )

  await callback.message.answer(text)
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

EXCHANGE_COST = 1  # сколько токенов платит тот, кто берёт обмен
RESTRICT_HOURS = 72  # на сколько часов ограничивается обвиняемый
MAX_OPEN_OFFERS = 5  # сколько открытых обменов может держать один человек
EX_PAGE_SIZE = 5
EX_SEARCH = {}  # user_id -> строка поиска
# Ссылка обязана быть на mega.nz
LINK_RE = re.compile(r"^https://mega\.nz/file/\S+$", re.I)
LINK_RULE = (
    "⚠️ <b>Ссылка обязана быть в mega.nz</b> и начинаться с"
    " <code>https://mega.nz/file/</code>"
)
LINK_ERROR = (
    "❌ Ссылка не подходит. Она обязана начинаться с https://mega.nz/file/"
    " Отправь ещё раз."
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
  """datetime окончания ограничения обменов/покупок или None."""
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
    " ограничены до решения администратора."
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
  try:
    await bot.send_message(target_id, text)
  except Exception:
    pass


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
    await callback.answer(RESTRICTED_TEXT, show_alert=True)
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
      f"Отправь <b>свою ссылку</b> для обмена.\n{LINK_RULE}",
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


# --- список и поиск ---


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
      "Или ID человека / номер обмена — только цифры.",
      ex_kb([[("❌ Отмена", "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExSearchState.query))
async def ex_search_do(message: Message, state: FSMContext):
  await state.clear()
  EX_SEARCH[message.from_user.id] = (message.text or "").strip()[:60]
  await ex_render_list(message, message.from_user.id, 0)


# --- просмотр, удаление, мои обмены ---


@router.callback_query(F.data.startswith("ex:view:"))
async def ex_view(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "open":
    await callback.answer("Этот обмен уже недоступен.", show_alert=True)
    return
  text = (
      f"🔄 <b>Обмен #{o['id']}</b>\nОтдаёт: <b>{esc(o['give'])}</b>\nХочет:"
      f" <b>{esc(o['want'])}</b>\nОбменщик ID: <code>{o['owner_id']}</code>\n"
      f"Стоимость обмена: {EXCHANGE_COST} токен"
  )
  if o["owner_id"] == callback.from_user.id:
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
    await callback.answer(RESTRICTED_TEXT, show_alert=True)
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
      "Отправь <b>свою ссылку</b>. После этого спишется"
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


# --- жалоба ---


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
  warns, banned = await add_warn(r["complainer_id"])
  await send_warn_notice(bot, r["complainer_id"], warns, banned)
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


# --- ГЛОБАЛЬНЫЙ ЛОВЕЦ ОШИБОК ---


@router.errors()
async def global_error_handler(event: ErrorEvent):
  logging.exception("Необработанная ошибка: %s", event.exception)


# --- ФОНОВЫЕ ЗАДАЧИ ---


async def weekly_reset_loop(bot: Bot):
  while True:
    await asyncio.sleep(604800)
    try:
      async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT u.user_id, COUNT(r.user_id) as ref_count
            FROM users u
            JOIN users r ON r.referrer_id = u.user_id
            GROUP BY u.user_id
            HAVING ref_count > 0
            ORDER BY ref_count DESC
            LIMIT 1
            """
        ) as cursor:
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

        await db.execute("UPDATE users SET referrer_id = 0, ref_activated = 1")
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
