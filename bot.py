import asyncio
import datetime
import os
from aiohttp import web
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ТОКЕН ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ RENDER
TOKEN = os.getenv("TOKEN", "8549927929:AAGLHW1GnV3mZq06TBELwFXo6UkjQfHi5fE")
PORT = int(os.getenv("PORT", 10000))
DB_NAME = "bot.db"

ADMIN_ID = 5681420389


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
    await db.commit()


# Состояния FSM
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


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
  await state.clear()
  user_id = message.from_user.id

  args = message.text.split()
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
          " referrer_id) VALUES (?, 0, 0, 0, ?)",
          (user_id, ref_to_save),
      )
      await db.commit()
      if referrer_id:
        await db.execute(
            "UPDATE users SET tokens = tokens + 15 WHERE user_id = ?",
            (referrer_id,),
        )
        await db.commit()
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
        await db.execute(
            "UPDATE users SET referrer_id = ? WHERE user_id = ?",
            (referrer_id, user_id),
        )
        await db.execute(
            "UPDATE users SET tokens = tokens + 15 WHERE user_id = ?",
            (referrer_id,),
        )
        await db.commit()
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


# --- FSM ХЕНДЛЕРЫ ---


@router.message(PromocodeState.code)
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = message.text.strip()
  await state.clear()

  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute(
        "SELECT reward_type, reward_value, max_uses, used_count FROM promocodes"
        " WHERE code = ?",
        (code_text,),
    ) as cursor:
      promo = await cursor.fetchone()

    if not promo:
      await message.answer("❌ Неверный или уже использованный промокод.")
      return

    reward_type, reward_value, max_uses, used_count = promo

    async with db.execute(
        "SELECT 1 FROM promo_activations WHERE user_id = ? AND code = ?",
        (user_id, code_text),
    ) as cursor:
      used_check = await cursor.fetchone()

    if used_check:
      await message.answer("❌ Вы уже активировали этот промокод!")
      return

    if max_uses > 0 and used_count >= max_uses:
      await message.answer(
          "❌ У этого промокода закончилось количество активаций."
      )
      return

    if reward_type == "tokens":
      tokens_amount = int(reward_value)
      await db.execute(
          "UPDATE users SET tokens = tokens + ? WHERE user_id = ?",
          (tokens_amount, user_id),
      )
      await message.answer(
          f"✅ Промокод успешно активирован!\n🎁 Вам зачислено: **{tokens_amount}"
          " токенов**",
          parse_mode="Markdown",
      )
    elif reward_type == "premium":
      days = int(reward_value)
      expire_date = (
          datetime.datetime.now() + datetime.timedelta(days=days)
      ).strftime("%Y-%m-%d %H:%M:%S")
      formatted_date = (
          datetime.datetime.now() + datetime.timedelta(days=days)
      ).strftime("%d.%m.%Y")
      await db.execute(
          "UPDATE users SET is_premium = 1, premium_expires = ? WHERE user_id"
          " = ?",
          (expire_date, user_id),
      )
      await message.answer(
          f"✅ Промокод успешно активирован!\n👑 Вам выдана Премиум-подписка на"
          f" **{days} дней** (до {formatted_date})!",
          parse_mode="Markdown",
      )

    await db.execute(
        "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
        (user_id, code_text),
    )
    await db.execute(
        "UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?",
        (code_text,),
    )
    await db.commit()


@router.message(EditProduct.new_value)
async def process_new_value(message: Message, state: FSMContext):
  data = await state.get_data()
  p_id = data["product_id"]
  field = data["field"]
  new_val = message.text.strip()
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


@router.message(CreatePromoState.code)
async def process_promo_code(message: Message, state: FSMContext):
  await state.update_data(code=message.text.strip())
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


@router.message(CreatePromoState.value)
async def process_promo_value(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! Введите целое число.")
    return
  await state.update_data(value=message.text)
  await message.answer(
      "🔢 Введите **количество использований** (например: 10 или 100):\n*(Введите"
      " 0 для безлимита)*",
      parse_mode="Markdown",
  )
  await state.set_state(CreatePromoState.uses)


@router.message(CreatePromoState.uses)
async def process_promo_uses(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! Введите число использований.")
    return
  max_uses = int(message.text)
  data = await state.get_data()
  code, reward_type, reward_value = data["code"], data["type"], data["value"]
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
      f"✅ **Промокод успешно создан!**\n\n🎟 Код: `{code}`\n🎁 Награда:"
      f" {reward_desc}\n👥 Лимит: {uses_text}",
      parse_mode="Markdown",
  )


@router.message(GivePremiumState.user_id)
async def process_give_premium_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  target_id = int(message.text)
  await state.update_data(target_id=target_id)
  await message.answer(
      f"⏳ На сколько **дней** выдать Премиум пользователю `{target_id}`?\n*(Введи"
      " число, например: 30)*",
      parse_mode="Markdown",
  )
  await state.set_state(GivePremiumState.days)


@router.message(GivePremiumState.days)
async def process_give_premium_days(message: Message, state: FSMContext, bot: Bot):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! Введите число дней цифрами.")
    return
  days = int(message.text)
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()

  expire_date = (
      datetime.datetime.now() + datetime.timedelta(days=days)
  ).strftime("%Y-%m-%d %H:%M:%S")
  formatted_date = (
      datetime.datetime.now() + datetime.timedelta(days=days)
  ).strftime("%d.%m.%Y")

  async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(
        """
            INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, premium_expires) 
            VALUES (?, 0, 1, 0, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
        """,
        (target_id, expire_date, expire_date),
    )
    await db.commit()

  await message.answer(
      f"✅ **Премиум успешно выдан!**\n\n🆔 Пользователь: `{target_id}`\n📅"
      f" Срок: **{days} дней** (до {formatted_date})",
      parse_mode="Markdown",
  )
  try:
    await bot.send_message(
        target_id,
        f"🎉 **Вам активирована Премиум-подписка на {days} дней!**\n📅 Срок"
        f" действия: до **{formatted_date}**.\n\nТеперь любые фулы в магазине"
        " доступны бесплатно!",
        parse_mode="Markdown",
    )
  except Exception:
    pass


@router.message(AddProduct.title)
async def process_title(message: Message, state: FSMContext):
  await state.update_data(title=message.text)
  await message.answer("💰 Введи цену в токенах (например: 15 или 50):")
  await state.set_state(AddProduct.price)


@router.message(AddProduct.price)
async def process_price(message: Message, state: FSMContext):
  await state.update_data(price=message.text)
  await message.answer(
      "🔗 Отправь ссылку на скачивание (Mega / облако):\n\n*(⚠️ Напоминание: у"
      " первого покупателя этого товара будет скидка 80%)*"
  )
  await state.set_state(AddProduct.link)


@router.message(AddProduct.link)
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
      f"✅ Фулл успешно добавлен в магазин!\n\n📌 Название: {title}\n💰 Цена:"
      f" {price} токенов\n🔗 Ссылка: {link}\n🎁 Активирована скидка 80% для"
      " первого покупателя!"
  )


@router.message(BroadcastState.message)
async def send_broadcast(message: Message, state: FSMContext):
  await state.clear()
  async with aiosqlite.connect(DB_NAME) as db:
    async with db.execute("SELECT user_id FROM users") as cursor:
      users = await cursor.fetchall()

  success, fail = 0, 0
  status_msg = await message.answer("⏳ Рассылка началась...")

  for user in users:
    try:
      await message.send_copy(chat_id=user[0])
      success += 1
      await asyncio.sleep(0.05)
    except Exception:
      fail += 1

  await status_msg.edit_text(
      f"✅ Рассылка завершена!\n\n📤 Доставлено: {success}\n❌ Заблокировали"
      f" бота: {fail}"
  )


# --- ГЛАВНОЕ МЕНЮ КНОПОК ---


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
            " referrer_id) VALUES (?, 0, 0, 0, 0)",
            (user_id,),
        )
        await db.commit()
        tokens, is_prem, bought, prem_expires = 0, 0, 0, None
      else:
        tokens, is_prem, bought, prem_expires = user_data

    prem_status = "❌ Не активна"
    if is_prem == 1 and prem_expires:
      exp_date = datetime.datetime.strptime(prem_expires, "%Y-%m-%d %H:%M:%S")
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
        f"👤 Ваш Профиль\n\n🆔 Ваш ID: `{user_id}`\n💲 Баланс: {tokens}"
        f" токенов\n👑 Премиум-подписка: {prem_status}\n📦 Куплено папок: {bought}"
        " шт."
    )
    await message.answer(
        profile_text, reply_markup=keyboard, parse_mode="Markdown"
    )

  elif text in ["🛒 Магазин", "🛍 Магазин"]:
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
        "🛍 Доступные фулы:\n*(Нажми на нужную, чтобы забрать"
        " ссылку)*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_text = (
        "👥 Реферальная система\n\nПриглашай друзей по своей персональной"
        f" ссылке:\n`{ref_link}`\n\n🎁 Бонусы:\n• За каждого приглашенного друга"
        " вы получаете 15 токенов!\n• Получайте 15% от суммы всех покупок"
        " токенами ваших рефералов!"
    )
    await message.answer(ref_text, parse_mode="Markdown")

  elif text == "🪙 Как получить токены?":
    tokens_text = (
        "📌 Как получить токены?\n\n1. Возьмите фото."
        " https://t.me/JmcDeveloper/11\n\n2. Оставляйте его в комментариях под"
        " постами/ответами другим людям. ГЛАВНОЕ: тема должна касаться по типу"
        " сестры и брата и тд.\n3. Отправляйте доказательства (скриншоты) в"
        " директ канала. https://t.me/JmcDeveloper?direct\n\n💰 1 отправка фото"
        " в комментариях = 1 токен!"
        " ГЛАВНОЕ: тема должна касаться по типу сестры и брата и тд."
        
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
        " АБСОЛЮТНО ЛЮБЫЕ фуллы бесплатно (даже с 0 токенов на балансе)!\n\n💳"
        " Стоимость:\n• 50 Звезд (любым подарком в Telegram за 50 звезд на профиль"
        " @ucu52)."
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
                    text="➕ Добавить фулл",
                    callback_data="add_product",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать товар", callback_data="edit_product_list"
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


# --- CALLBACK ИНЛАЙН КНОПКИ ---


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await callback.message.answer("🎟 Введите промокод:")
  await state.set_state(PromocodeState.code)
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

    tokens = u_data[0] if u_data else 0
    is_prem = u_data[1] if u_data else 0
    referrer_id = u_data[3] if u_data else 0
    prem_expires = u_data[4] if u_data else None

    if is_prem == 1 and prem_expires:
      exp_date = datetime.datetime.strptime(prem_expires, "%Y-%m-%d %H:%M:%S")
      if datetime.datetime.now() >= exp_date:
        is_prem = 0
        await db.execute(
            "UPDATE users SET is_premium = 0, premium_expires = NULL WHERE"
            " user_id = ?",
            (user_id,),
        )
        await db.commit()

    if is_prem == 1:
      await db.execute(
          "UPDATE products SET buyers_count = buyers_count + 1 WHERE id = ?",
          (p_id,),
      )
      await db.execute(
          "UPDATE users SET bought_folders = bought_folders + 1 WHERE user_id"
          " = ?",
          (user_id,),
      )
      await db.commit()
      text = (
          f"👑 Премиум-доступ применен!\n\n✅ Вы успешно получили фулл {title}"
          f" бесплатно по подписке!\n\n🔗 Ссылка на скачивание: {link}"
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
        "UPDATE users SET tokens = ?, bought_folders = bought_folders + 1 WHERE"
        " user_id = ?",
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
            "UPDATE users SET tokens = tokens + ? WHERE user_id = ?",
            (ref_bonus, referrer_id),
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
        f"🎉 Поздравляем! Ты первый покупатель фулла {title}!\n🔥 Применилась"
        f" скидка 80%!\nСписано: {final_price} токенов.\n💲 Ваш остаток:"
        f" {new_tokens} токенов.\n\n🔗 Твоя ссылка на скачивание: {link}"
    )
  else:
    text = (
        f"✅ Спасибо за покупку фула {title}!\n💰 Списано: {final_price}"
        f" токенов. Остаток: {new_tokens} токенов.\n\n🔗 Твоя ссылка на"
        f" скачивание: {link}"
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
      f"✏️ **Редактирование товара:** {product[0]}\n💰 Текущая цена:"
      f" {product[1]} токенов\n🔗 Ссылка: {product[2]}\n\nЧто именно хочешь"
      " изменить?",
      reply_markup=keyboard,
      parse_mode="Markdown",
  )
  await callback.answer()


@router.callback_query(F.data.startswith("field_"))
async def prompt_new_value(callback: CallbackQuery, state: FSMContext):
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


@router.callback_query(F.data == "admin_create_promo")
async def start_create_promo(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await callback.message.answer(
      "🎟 Введите текст промокода (например: `START100`):", parse_mode="Markdown"
  )
  await state.set_state(CreatePromoState.code)
  await callback.answer()


@router.callback_query(F.data.startswith("promo_type_"))
async def process_promo_type(callback: CallbackQuery, state: FSMContext):
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


@router.callback_query(F.data == "admin_give_premium")
async def start_give_premium(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  await callback.message.answer(
      "👑 Введи **Telegram ID** пользователя, которому нужно выдать Премиум:",
      parse_mode="Markdown",
  )
  await state.set_state(GivePremiumState.user_id)
  await callback.answer()


@router.callback_query(F.data == "add_product")
async def start_add_product(callback: CallbackQuery, state: FSMContext):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
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
  await callback.message.answer(
      "📢 Отправь текст, картинку или видео для рассылки всем пользователям бота:"
  )
  await state.set_state(BroadcastState.message)
  await callback.answer()


# --- ФОНОВАЯ ЗАДАЧА ДЛЯ АВТО-БЭКАПА КАЖДЫЕ 15 МИНУТ ---
async def auto_backup_loop(bot: Bot):
  while True:
    await asyncio.sleep(300)  # 900 секунд = ровно 15 минут
    try:
      if os.path.exists(DB_NAME):
        document = FSInputFile(DB_NAME)
        await bot.send_document(
            ADMIN_ID,
            document,
            caption=(
                "⏱ база данных обновилась:\n"
                f"📅 {datetime.datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
            ),
        )
    except Exception as e:
      print(f"Ошибка при отправке авто-бэкапа: {e}")


# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
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

  bot = Bot(token=TOKEN)
  dp = Dispatcher(storage=MemoryStorage())
  dp.include_router(router)

  # Запускаем фоновый цикл бэкапов каждые 15 минут
  asyncio.create_task(auto_backup_loop(bot))

  await bot.delete_webhook(drop_pending_updates=True)
  print(
      f"Бот успешно запущен, авто-бэкап (каждые 15 минут) активен, веб-сервер"
      f" слушает порт {PORT}!"
  )
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
