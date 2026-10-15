import asyncio
import datetime
import html
import logging
import os
import sys

from aiohttp import web
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
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

logging.basicConfig(level=logging.INFO)

# ВАЖНО: токен только из переменной окружения TOKEN (на хостинге).
TOKEN = os.getenv("TOKEN")
if not TOKEN:
  sys.exit("Не задана переменная окружения TOKEN")

PORT = int(os.getenv("PORT", 10000))
DB_NAME = "bot.db"

ADMIN_ID = 5681420389

DT_FORMAT = "%Y-%m-%d %H:%M:%S"


def esc(value) -> str:
  """Экранирует текст для HTML-разметки Telegram."""
  return html.escape(str(value), quote=False)


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
            premium_expires TEXT DEFAULT NULL
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

    # Миграция: добавляем недостающие колонки в таблицы старой базы
    migrations = {
        "users": {
            "tokens": "INTEGER DEFAULT 0",
            "is_premium": "INTEGER DEFAULT 0",
            "bought_folders": "INTEGER DEFAULT 0",
            "referrer_id": "INTEGER DEFAULT 0",
            "ref_activated": "INTEGER DEFAULT 1",
            "premium_expires": "TEXT DEFAULT NULL",
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
  ]
  if user_id == ADMIN_ID:
    kb.append([KeyboardButton(text="⚙️ Админ-панель")])
  return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


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
              "🎉 По вашей реферальной ссылке зарегистрировался новый"
              " пользователь!\n🎁 Вам начислено 7 токенов!",
          )
        except Exception:
          pass

  await message.answer(
      "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре"
      " ниже:",
      reply_markup=get_main_keyboard(user_id),
  )


@router.message(Command("promocode"))
async def cmd_promocode(message: Message, state: FSMContext):
  await state.clear()
  await message.answer("🎟 Введите промокод:")
  await state.set_state(PromocodeState.code)


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
          "SELECT tokens, is_premium, bought_folders, premium_expires FROM"
          " users WHERE user_id = ?",
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
        tokens, is_prem, bought, prem_expires = 0, 0, 0, None
      else:
        tokens, is_prem, bought, prem_expires = user_data

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
        f"📦 Куплено папок: {bought} шт."
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

    text_top = "🏆 <b>Топ-10 игроков по рефералам:</b>\n\n"
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
        " https://t.me/JmcDeveloper/11\n\n2. Оставляйте рекламу моей игры в"
        " комментариях под постами/ответами другим людям. \n3. Отправляйте"
        " доказательства (скриншоты) в директ канала."
        " https://t.me/JmcDeveloper?direct\n\n💰 1 отправка фото в"
        " комментариях = 1 токен! ГЛАВНОЕ: тема должна касаться по типу"
        " майнкрафта и тд."
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
        " АБСОЛЮТНО ЛЮБЫЕ стримы бесплатно (даже с 0 токенов на балансе)!\n\n💳"
        " Стоимость:\n• 50 Звезд (любым подарком в Telegram за 50 звезд на"
        " профиль @ucu52)."
    )
    await message.answer(prem_text, reply_markup=keyboard)

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
                    text="📢 Сделать рассылку всем",
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


# --- ВВОД ПРОМОКОДА ---


@router.message(StateFilter(PromocodeState.code))
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = (message.text or "").strip()
  await state.clear()
  kb = get_main_keyboard(user_id)

  try:
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT code, reward_type, reward_value, max_uses, used_count "
          "FROM promocodes WHERE LOWER(code) = LOWER(?)",
          (code_text,),
      ) as cur:
        promo = await cur.fetchone()

      if not promo:
        await message.answer(
            "❌ Неверный или несуществующий промокод.", reply_markup=kb
        )
        return

      real_code, reward_type, reward_value, max_uses, used_count = promo

      async with db.execute(
          "SELECT 1 FROM promo_activations WHERE user_id = ? AND"
          " LOWER(code) = LOWER(?)",
          (user_id, real_code),
      ) as cur:
        if await cur.fetchone():
          await message.answer(
              "❌ Вы уже активировали этот промокод!", reply_markup=kb
          )
          return

      if max_uses > 0 and used_count >= max_uses:
        await message.answer(
            "❌ У этого промокода закончились активации.", reply_markup=kb
        )
        return

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
        await message.answer(
            "❌ Неизвестный тип награды у промокода.", reply_markup=kb
        )
        return

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

    await message.answer(reply, reply_markup=kb)

  except Exception as e:
    logging.exception("Ошибка промокода")
    await message.answer(f"❌ Ошибка обработки: {esc(e)}", reply_markup=kb)


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
      expire_date = new_expire.strftime(DT_FORMAT)
      formatted_date = new_expire.strftime("%d.%m.%Y")

      await db.execute(
          """
          INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
          VALUES (?, 0, 1, 0, 0, 1, ?)
          ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
          """,
          (target_id, expire_date, expire_date),
      )
      await db.commit()

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
async def process_promo_uses(message: Message, state: FSMContext):
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
      f"✅ <b>Промокод успешно создан!</b>\n\n🎟 Код: <code>{esc(code)}</code>\n"
      f"🎁 Награда: {reward_desc}\n👥 Лимит: {uses_text}",
      reply_markup=get_main_keyboard(message.from_user.id),
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
          f" {esc(title)} бесплатно по подписке!\n\n🔗 Ссылка на скачивание:"
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

  asyncio.create_task(auto_backup_loop(bot))
  asyncio.create_task(weekly_reset_loop(bot))

  await bot.delete_webhook(drop_pending_updates=True)
  print(
      "Бот успешно запущен, авто-бэкап (5 минут) и еженедельный сброс активны,"
      f" веб-сервер слушает порт {PORT}!"
  )
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
