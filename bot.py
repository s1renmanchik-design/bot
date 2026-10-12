import asyncio
import datetime
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ТОКЕН ТВОЕГО БОТА
TOKEN = "8549927929:AAE_aDtLHVjkR9h50ABM233OZprEa6l-GvM"

# ТВОЙ ТЕЛЕГРАМ ID АДМИНА
ADMIN_ID = 5681420389

# КАНАЛ ДЛЯ ОБЯЗАТЕЛЬНОЙ ПОДПИСКИ
CHANNEL_ID = -1002925107145
CHANNEL_USERNAME = "iiris_cnn_bot"

# Инициализация базы данных SQLite
conn = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = conn.cursor()

# Создаем таблицы, если их нет
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        tokens INTEGER DEFAULT 0,
        is_premium INTEGER DEFAULT 0,
        bought_folders INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT 0,
        premium_expires TEXT DEFAULT NULL
    )
"""
)

try:
  cursor.execute("ALTER TABLE users ADD COLUMN premium_expires TEXT DEFAULT NULL")
except Exception:
  pass

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        price TEXT,
        link TEXT,
        buyers_count INTEGER DEFAULT 0
    )
"""
)

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS promocodes (
        code TEXT PRIMARY KEY,
        reward_type TEXT,
        reward_value TEXT,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0
    )
"""
)

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS promo_activations (
        user_id INTEGER,
        code TEXT,
        PRIMARY KEY (user_id, code)
    )
"""
)
conn.commit()


# Состояния для FSM
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


router = Router()


# --- ПРОВЕРКА ПОДПИСКИ НА КАНАЛ ---
async def check_subscription(user_id: int, bot: Bot) -> bool:
  try:
    member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
    if member.status in ["creator", "administrator", "member"]:
      return True
    return False
  except Exception:
    return True


async def not_subscribed_message(message: Message):
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="📢 Подписаться на канал",
                  url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}",
              )
          ],
          [
              InlineKeyboardButton(
                  text="🔄 Проверить подписку", callback_data="check_sub"
              )
          ],
      ]
  )
  await message.answer(
      "⛔️ **Для использования бота необходимо подписаться на наш канал!**\n\n"
      "Подпишитесь на канал, а затем нажмите кнопку ниже:",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )


# --- ГЛАВНОЕ КНОПОЧНОЕ МЕНЮ (НИЖНЕЕ) ---
def get_main_keyboard(user_id):
  kb = [
      [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="🛒 Магазин")],
      [
          KeyboardButton(text="👥 Реферальная система"),
          KeyboardButton(text="🪙 Как получить токены?"),
      ],
      [KeyboardButton(text="👑 Премиум-подписка")],
  ]
  if user_id == ADMIN_ID:
    kb.append([KeyboardButton(text="⚙️ Админ-панель")])

  return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# --- КОМАНДА /start ---
@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
  user_id = message.from_user.id

  if not await check_subscription(user_id, bot):
    await not_subscribed_message(message)
    return

  args = message.text.split()
  referrer_id = None
  if len(args) > 1 and args[1].isdigit():
    potential_ref = int(args[1])
    if potential_ref != user_id:
      referrer_id = potential_ref

  cursor.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
  existing_user = cursor.fetchone()

  if existing_user is None:
    ref_to_save = referrer_id if referrer_id else 0
    cursor.execute(
        "INSERT INTO users (user_id, tokens, is_premium, bought_folders,"
        " referrer_id) VALUES (?, 0, 0, 0, ?)",
        (user_id, ref_to_save),
    )
    conn.commit()

    if referrer_id:
      cursor.execute(
          "UPDATE users SET tokens = tokens + 15 WHERE user_id = ?",
          (referrer_id,),
      )
      conn.commit()
      try:
        await bot.send_message(
            referrer_id,
            "🎉 По реферальной ссылке зарегистрировался новый пользователь!\n🎁"
            " Вам начислено бонусных 15 токенов!",
        )
      except Exception:
        pass
  else:
    if existing_user[0] == 0 and referrer_id:
      cursor.execute(
          "UPDATE users SET referrer_id = ? WHERE user_id = ?",
          (referrer_id, user_id),
      )
      cursor.execute(
          "UPDATE users SET tokens = tokens + 15 WHERE user_id = ?",
          (referrer_id,),
      )
      conn.commit()
      try:
        await bot.send_message(
            referrer_id,
            "🎉 По вашей реферальной ссылке активировался пользователь!\n🎁 Вам"
            " начислено бонусных 15 токенов!",
        )
      except Exception:
        pass

  await message.answer(
      "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре"
      " ниже:",
      reply_markup=get_main_keyboard(user_id),
  )


@router.callback_query(F.data == "check_sub")
async def process_check_sub(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  if await check_subscription(user_id, bot):
    await callback.message.edit_text(
        "✅ Подписка подтверждена! Добро пожаловать."
    )
    await callback.message.answer(
        "Выбирай нужный раздел на клавиатуре:",
        reply_markup=get_main_keyboard(user_id),
    )
  else:
    await callback.answer(
        "❌ Вы еще не подписались на канал!", show_alert=True
    )


# --- ОБРАБОТКА КНОПОК ИЗ МЕНЮ ---
@router.message(
    F.text.in_(
        {
            "👤 Мой Профиль",
            "🛒 Магазин",
            "🛍 Магазин",
            "👥 Реферальная система",
            "🪙 Как получить токены?",
            "👑 Премиум-подписка",
            "⚙️ Админ-панель",
        }
    )
)
async def menu_router(message: Message, bot: Bot):
  user_id = message.from_user.id

  if not await check_subscription(user_id, bot):
    await not_subscribed_message(message)
    return

  text = message.text

  if text == "👤 Мой Профиль":
    cursor.execute(
        "SELECT tokens, is_premium, bought_folders, premium_expires FROM users"
        " WHERE user_id = ?",
        (user_id,),
    )
    user_data = cursor.fetchone()

    tokens = user_data[0] if user_data else 0
    is_prem = user_data[1] if user_data else 0
    bought = user_data[2] if user_data else 0
    prem_expires_str = user_data[3] if user_data else None

    prem_status = "❌ Не активна"
    if is_prem == 1 and prem_expires_str:
      try:
        expire_date = datetime.datetime.strptime(
            prem_expires_str, "%Y-%m-%d %H:%M:%S"
        )
        now = datetime.datetime.now()
        if now < expire_date:
          days_left = (expire_date - now).days
          formatted_date = expire_date.strftime("%d.%m.%Y")
          prem_status = f"✅ До {formatted_date} ({days_left} дн.)"
        else:
          cursor.execute(
              "UPDATE users SET is_premium = 0, premium_expires = NULL WHERE"
              " user_id = ?",
              (user_id,),
          )
          conn.commit()
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
        f"👤 Ваш Профиль\n\n"
        f"🆔 Ваш ID: {user_id}\n"
        f"💲 Баланс: {tokens} токенов\n"
        f"👑 Премиум-подписка: {prem_status}\n"
        f"📦 Куплено папок: {bought} шт."
    )
    await message.answer(profile_text, reply_markup=keyboard)

  elif text in ["🛒 Магазин", "🛍 Магазин"]:
    cursor.execute("SELECT id, title, price FROM products")
    products = cursor.fetchall()

    if not products:
      await message.answer(
          "📭 В магазине пока пусто. Админ еще не добавил папки с альтухами!"
      )
      return

    keyboard_buttons = []
    for prod in products:
      p_id, title, price = prod
      cursor.execute("SELECT buyers_count FROM products WHERE id = ?", (p_id,))
      res = cursor.fetchone()
      buyers_count = res[0] if res else 0

      if buyers_count == 0:
        btn_text = f"🔥 {title} — {price} токенов (ПЕРВАЯ ПОКУПКА -80%!)"
      else:
        btn_text = f"📁 {title} — {price} токенов"

      keyboard_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"buy_{p_id}")]
      )

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    await message.answer(
        "🛍 Доступные папки с альтухами:\n*(Нажми на нужную, чтобы забрать"
        " ссылку)*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_text = (
        "👥 Реферальная система\n\n"
        "Приглашай друзей по своей персональной ссылке:\n"
        f"{ref_link}\n\n"
        "🎁 Бонусы:\n"
        "• За каждого приглашенного друга вы получаете 15 токенов!\n"
        "• Получайте 15% от суммы всех покупок токенами ваших рефералов!"
    )
    await message.answer(ref_text)

  elif text == "🪙 Как получить токены?":
    tokens_text = (
        "📌 Как получить токены?\n\n"
        "1. Возьмите фото. https://t.me/JmcDeveloper/11\n\n"
        "2. Оставляйте его в комментариях под постами/ответами другим людям."
        " ГЛАВНОЕ: тема должна касаться по типу сестры и брата и тд.\n"
        "3. Отправляйте доказательства (скриншоты) в директ канала."
        " https://t.me/JmcDeveloper?direct\n\n"
        "💰 1 отправка фото в комментариях = 1 токен!"
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
        "👑 Премиум
