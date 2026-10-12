import asyncio
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
        bought_folders INTEGER DEFAULT 0
    )
"""
)

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
conn.commit()


# Состояния для FSM (добавление товара, рассылка, промокод, выдача премиума)
class AddProduct(StatesGroup):
  title = State()
  price = State()
  link = State()


class BroadcastState(StatesGroup):
  message = State()


class PromocodeState(StatesGroup):
  code = State()


class GivePremiumState(StatesGroup):
  user_id = State()


router = Router()


# --- ГЛАВНОЕ КНОПОЧНОЕ МЕНЮ (НИЖНЕЕ) ---
def get_main_keyboard(user_id):
  kb = [
      [KeyboardButton(text="👤 Мой Профиль"), KeyboardButton(text="🛍 Магазин")],
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
async def cmd_start(message: Message):
  user_id = message.from_user.id

  cursor.execute(
      "INSERT OR IGNORE INTO users (user_id, tokens, is_premium,"
      " bought_folders) VALUES (?, 0, 0, 0)",
      (user_id,),
  )
  conn.commit()

  await message.answer(
      "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре"
      " ниже:",
      reply_markup=get_main_keyboard(user_id),
  )


# --- ОБРАБОТКА КНОПОК ИЗ МЕНЮ ---
@router.message(F.text == "👤 Мой Профиль")
async def profile_handler(message: Message):
  user_id = message.from_user.id
  cursor.execute(
      "SELECT tokens, is_premium, bought_folders FROM users WHERE user_id = ?",
      (user_id,),
  )
  user_data = cursor.fetchone()

  tokens = user_data[0] if user_data else 0
  is_prem = user_data[1] if user_data else 0
  bought = user_data[2] if user_data else 0

  prem_status = "✅ Активна" if is_prem == 1 else "❌ Не активна"

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🎟 Ввести промокод", callback_data="enter_promocode"
              )
          ]
      ]
  )

  text = (
      f"👤 **Ваш Профиль**\n\n"
      f"🆔 Ваш ID: `{user_id}`\n"
      f"💲 Баланс: {tokens} токенов\n"
      f"👑 Премиум-подписка: {prem_status}\n"
      f"📦 Куплено папок: {bought} шт."
  )

  await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await callback.message.answer("🎟 Введите промокод:")
  await state.set_state(PromocodeState.code)
  await callback.answer()


@router.message(PromocodeState.code)
async def promo_check(message: Message, state: FSMContext):
  await state.clear()
  await message.answer("❌ Неверный или уже использованный промокод.")


@router.message(F.text == "👥 Реферальная система")
async def referral_handler(message: Message):
  bot_info = await message.bot.get_me()
  ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
  await message.answer(
      "👥 **Реферальная система**\n\nПриглашай друзей по своей ссылке и получай"
      f" бонусы:\n`{ref_link}`",
      parse_mode="Markdown",
  )


@router.message(F.text == "🪙 Как получить токены?")
async def tokens_handler(message: Message):
  text = (
      "📌 **Как получить токены?**\n\n"
      "1. Возьмите фото. https://t.me/JmcDeveloper/11\n\n"
      "2. Оставляйте его в комментариях под постами/ответами другим людям."
      " ГЛАВНОЕ: тема должна касаться по типу сестры и брата и тд.\n"
      "3. Отправляйте доказательства (скриншоты) в директ канала."
      " https://t.me/JmcDeveloper?direct\n\n"
      "💰 **1 отправка фото в комментариях = 1 токен!**"
  )
  await message.answer(
      text, parse_mode="Markdown", disable_web_page_preview=True
  )


@router.message(F.text == "👑 Премиум-подписка")
async def premium_handler(message: Message):
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
  text = (
      "👑 **Премиум-подписка на 1 МЕСЯЦ (30 дней)**\n\n"
      "Дает право покупать **АБСОЛЮТНО ЛЮБЫЕ** папки бесплатно (даже с 0"
      " токенов на балансе)!\n\n"
      "💳 **Стоимость:**\n"
      "• 50 Звезд (любым подарком в Telegram за 50 звезд на профиль @ucu52)."
  )
  await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")


# --- МАГАЗИН И ПАПКИ С АЛЬТУХАМИ ---
@router.message(F.text == "🛍 Магазин")
async def shop_handler(message: Message):
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
      btn_text = f"🔥 {title} — {price} (ПЕРВАЯ ПОКУПКА СО СКИДКОЙ 80%!)"
    else:
      btn_text = f"📁 {title} — {price}"

    keyboard_buttons.append(
        [InlineKeyboardButton(text=btn_text, callback_data=f"buy_{p_id}")]
    )

  keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
  await message.answer(
      "🛍 **Доступные папки с альтухами:**\n*(Нажми на нужную, чтобы забрать"
      " ссылку)*",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )


@router.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery):
  user_id = callback.from_user.id
  p_id = int(callback.data.split("_")[1])

  cursor.execute(
      "SELECT title, price, link, buyers_count FROM products WHERE id = ?",
      (p_id,),
  )
  product = cursor.fetchone()

  if not product:
    await callback.answer("❌ Этот товар был удален.", show_alert=True)
    return

  title, price, link, buyers_count = product

  # Проверяем премиум
  cursor.execute(
      "SELECT is_premium, bought_folders FROM users WHERE user_id = ?",
      (user_id,),
  )
  u_data = cursor.fetchone()
  is_prem = u_data[0] if u_data else 0
  bought_folders = u_data[1] if u_data else 0

  # Увеличиваем счетчик купленных папок
  new_buyers_count = buyers_count + 1
  cursor.execute(
      "UPDATE products SET buyers_count = ? WHERE id = ?",
      (new_buyers_count, p_id),
  )
  cursor.execute(
      "UPDATE users SET bought_folders = ? WHERE user_id = ?",
      (bought_folders + 1, user_id),
  )
  conn.commit()

  if is_prem == 1:
    text = (
        f"👑 **Премиум-доступ применен!**\n\n"
        f"✅ Вы успешно получили папку *{title}* бесплатно по подписке!\n\n"
        f"🔗 Ссылка на скачивание: {link}"
    )
  elif buyers_count == 0:
    text = (
        f"🎉 Поздравляем! Ты **первый покупатель** папки *{title}*!\n"
        f"🔥 Тебе применилась **скидка 80%**!\n\n"
        f"🔗 Твоя ссылка на скачивание: {link}"
    )
  else:
    text = (
        f"✅ Спасибо за покупку папки *{title}*!\n\n"
        f"🔗 Твоя ссылка на скачивание: {link}"
    )

  await callback.message.answer(text, parse_mode="Markdown")
  await callback.answer()


# ================= АДМИН-ПАНЕЛЬ =================


@router.message(F.text == "⚙️ Админ-панель")
async def admin_panel_text(message: Message):
  if message.from_user.id != ADMIN_ID:
    await message.answer("⛔️ У тебя нет доступа!")
    return

  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="➕ Добавить папки / альтухи", callback_data="add_product"
              )
          ],
          [
              InlineKeyboardButton(
                  text="🗑 Удалить товар", callback_data="delete_product_list"
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
                  text="📢 Сделать рассылку всем", callback_data="start_broadcast"
              )
          ],
      ]
  )

  await message.answer(
      "⚙️ **Панель администратора:**",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )


# --- ВЫДАЧА ПРЕМИУМА ЧЕРЕЗ АДМИНКУ ---
@router.callback_query(F.data == "admin_give_premium")
async def start_give_premium(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    return
  await callback.message.answer(
      "👑 Введи **Telegram ID** пользователя, которому нужно выдать Премиум:"
  )
  await state.set_state(GivePremiumState.user_id)
  await callback.answer()


@router.message(GivePremiumState.user_id)
async def process_give_premium(message: Message, state: FSMContext, bot: Bot):
  await state.clear()
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return

  target_id = int(message.text)

  # Обновляем премиум статус в базе
  cursor.execute(
      "INSERT OR IGNORE INTO users (user_id, tokens, is_premium,"
      " bought_folders) VALUES (?, 0, 1, 0)",
      (target_id,),
  )
  cursor.execute(
      "UPDATE users SET is_premium = 1 WHERE user_id = ?", (target_id,)
  )
  conn.commit()

  await message.answer(
      f"✅ **Премиум успешно выдан пользователю с ID:** `{target_id}`",
      parse_mode="Markdown",
  )

  # Оповещаем пользователя
  try:
    await bot.send_message(
        target_id,
        "🎉 **Вам активирована Премиум-подписка!**\nТеперь любые папки в"
        " магазине доступны бесплатно.",
        parse_mode="Markdown",
    )
  except Exception:
    pass


# --- ДОБАВЛЕНИЕ НОВОЙ ПАПКИ ---
@router.callback_query(F.data == "add_product")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    return
  await callback.message.answer(
      "📝 Введи **название** папки (например: *Альтухи #15*):"
  )
  await state.set_state(AddProduct.title)
  await callback.answer()


@router.message(AddProduct.title)
async def process_title(message: Message, state: FSMContext):
  await state.update_data(title=message.text)
  await message.answer("💰 Введи **цену** (например: *Бесплатно* или *50 руб*):")
  await state.set_state(AddProduct.price)


@router.message(AddProduct.price)
async def process_price(message: Message, state: FSMContext):
  await state.update_data(price=message.text)
  await message.answer(
      "🔗 Отправь **ссылку** на скачивание (Mega / облако):\n\n*(⚠️ Напоминание: у"
      " первого покупателя этого товара будет скидка 80%)*"
  )
  await state.set_state(AddProduct.link)


@router.message(AddProduct.link)
async def process_link(message: Message, state: FSMContext):
  data = await state.get_data()
  title = data["title"]
  price = data["price"]
  link = message.text

  cursor.execute(
      "INSERT INTO products (title, price, link, buyers_count) VALUES (?, ?, ?,"
      " 0)",
      (title, price, link),
  )
  conn.commit()

  await state.clear()
  await message.answer(
      f"✅ Папка успешно добавлена в магазин!\n\n📌 Название: {title}\n💰 Цена:"
      f" {price}\n🔗 Ссылка: {link}\n🎁 *Активирована скидка 80% для первого"
      " покупателя!*",
      parse_mode="Markdown",
  )


# --- УДАЛЕНИЕ ---
@router.callback_query(F.data == "delete_product_list")
async def delete_product_list(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    return

  cursor.execute("SELECT id, title FROM products")
  products = cursor.fetchall()

  if not products:
    await callback.message.edit_text("📭 Нет товаров для удаления.")
    return

  keyboard_buttons = []
  for p_id, title in products:
    keyboard_buttons.append(
        [
            InlineKeyboardButton(
                text=f"❌ Удалить: {title}", callback_data=f"del_prod_{p_id}"
            )
        ]
    )

  await callback.message.edit_text(
      "Выбери товар для удаления:",
      reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
  )


@router.callback_query(F.data.startswith("del_prod_"))
async def delete_product_execute(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    return
  p_id = callback.data.split("_")[2]

  cursor.execute("DELETE FROM products WHERE id = ?", (p_id,))
  conn.commit()

  await callback.answer("Товар удален!")
  await delete_product_list(callback)


# --- РАССЫЛКА ---
@router.callback_query(F.data == "start_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    return
  await callback.message.answer(
      "📢 Отправь текст, картинку или видео для рассылки **всем** пользователям"
      " бота:"
  )
  await state.set_state(BroadcastState.message)
  await callback.answer()


@router.message(BroadcastState.message)
async def send_broadcast(message: Message, state: FSMContext):
  await state.clear()

  cursor.execute("SELECT user_id FROM users")
  users = cursor.fetchall()

  success = 0
  fail = 0

  status_msg = await message.answer("⏳ Рассылка началась...")

  for user in users:
    u_id = user[0]
    try:
      await message.send_copy(chat_id=u_id)
      success += 1
      await asyncio.sleep(0.05)
    except Exception:
      fail += 1

  await status_msg.edit_text(
      f"✅ **Рассылка завершена!**\n\n📤 Доставлено: {success}\n❌ Заблокировали"
      f" бота: {fail}"
  )


# --- ЗАПУСК БОТА ---
async def main():
  bot = Bot(token=TOKEN)
  dp = Dispatcher(storage=MemoryStorage())
  dp.include_router(router)

  await bot.delete_webhook(drop_pending_updates=True)
  print("Бот успешно запущен и работает 24/7!")
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
