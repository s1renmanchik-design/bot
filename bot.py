import asyncio
import datetime
import os
import traceback
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


# --- СТАРТ И КОМАНДЫ ---


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
    else:
      if existing_user[0] == 0 and referrer_id:
        await db.execute(
            "UPDATE users SET referrer_id = ? WHERE user_id = ?",
            (referrer_id, user_id),
        )
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


# --- ВВОД И АКТИВАЦИЯ ПРОМОКОДА ---


@router.message(PromocodeState.code)
async def promo_check(message: Message, state: FSMContext):
  user_id = message.from_user.id
  code_text = str(message.text).strip()
  await state.clear()

  try:
    async with aiosqlite.connect(DB_NAME) as db:
      async with db.execute(
          "SELECT reward_type, reward_value, max_uses, used_count FROM"
          " promocodes WHERE LOWER(code) = LOWER(?)",
          (code_text,),
      ) as cursor:
        promo = await cursor.fetchone()

      if not promo:
        await message.answer(
            "❌ Неверный или несуществующий промокод.",
            reply_markup=get_main_keyboard(user_id),
        )
        return

      reward_type, reward_value, max_uses, used_count = promo

      async with db.execute(
          "SELECT 1 FROM promo_activations WHERE user_id = ? AND LOWER(code) ="
          " LOWER(?)",
          (user_id, code_text),
      ) as cursor:
        used_check = await cursor.fetchone()

      if used_check:
        await message.answer(
            "❌ Вы уже активировали этот промокод!",
            reply_markup=get_main_keyboard(user_id),
        )
        return

      if max_uses > 0 and used_count >= max_uses:
        await message.answer(
            "❌ У этого промокода закончилось количество активаций.",
            reply_markup=get_main_keyboard(user_id),
        )
        return

      try:
        val = int(reward_value)
      except ValueError:
        val = 0

      if reward_type == "tokens":
        await db.execute(
            """
                    INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated)
                    VALUES (?, ?, 0, 0, 0, 1)
                    ON CONFLICT(user_id) DO UPDATE SET tokens = COALESCE(tokens, 0) + ?
                """,
            (user_id, val, val),
        )
        await message.answer(
            f"✅ Промокод успешно активирован!\n🎁 Вам зачислено: **{val}"
            " токенов**",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id),
        )

      elif reward_type == "premium":
        async with db.execute(
            "SELECT is_premium, premium_expires FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
          u_info = await cursor.fetchone()

        now = datetime.datetime.now()
        base_date = now
        if u_info and u_info[0] == 1 and u_info[1]:
          try:
            curr_exp = datetime.datetime.strptime(
                u_info[1], "%Y-%m-%d %H:%M:%S"
            )
            if curr_exp > now:
              base_date = curr_exp
          except Exception:
            pass

        new_expire = base_date + datetime.timedelta(days=val)
        expire_str = new_expire.strftime("%Y-%m-%d %H:%M:%S")
        formatted_date = new_expire.strftime("%d.%m.%Y")

        await db.execute(
            """
                    INSERT INTO users (user_id, tokens, is_premium, bought_folders, referrer_id, ref_activated, premium_expires)
                    VALUES (?, 0, 1, 0, 0, 1, ?)
                    ON CONFLICT(user_id) DO UPDATE SET is_premium = 1, premium_expires = ?
                """,
            (user_id, expire_str, expire_str),
        )

        await message.answer(
            f"✅ Промокод успешно активирован!\n👑 Вам выдана Премиум-подписка на"
            f" **{val} дней** (до {formatted_date})!",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id),
        )

      await db.execute(
          "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
          (user_id, str(code_text)),
      )
      await db.execute(
          "UPDATE promocodes SET used_count = used_count + 1 WHERE LOWER(code)"
          " = LOWER(?)",
          (code_text,),
      )
      await db.commit()

  except Exception as err:
    print(f"ОШИБКА ПРОМОКОДА: {traceback.format_exc()}")
    await message.answer(
        f"❌ Ошибка в базе данных: {err}",
        reply_markup=get_main_keyboard(user_id),
    )


# --- FSM ХЕНДЛЕРЫ АДМИНКИ И ТОВАРОВ ---


@router.message(CreatePromoState.code)
async def process_promo_code(message: Message, state: FSMContext):
  await state.update_data(code=str(message.text.strip()))
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
    await message.answer("❌ Ошибка! Введите целое число (цифрами).")
    return
  await state.update_data(value=str(message.text.strip()))
  await message.answer(
      "🔢 Введите **количество использований** (например: 10 или 100):\n*(Введите"
      " 0 для безлимита)*",
      parse_mode="Markdown",
  )
  await state.set_state(CreatePromoState.uses)


@router.message(CreatePromoState.uses)
async def process_promo_uses(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! Введите число использований цифрами.")
    return
  max_uses = int(message.text.strip())
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
      f"✅ **Промокод успешно создан!**\n\n🎟 Код: `{code}`\n🎁 Награда:"
      f" {reward_desc}\n👥 Лимит: {uses_text}",
      parse_mode="Markdown",
      reply_markup=get_main_keyboard(message.from_user.id),
  )


@router.message(GivePremiumState.user_id)
async def process_give_premium_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  target_id = int(message.text.strip())
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
  days = int(message.text.strip())
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()

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
        curr_exp = datetime.datetime.strptime(u_info[1], "%Y-%m-%d %H:%M:%S")
        if curr_exp > now:
          base_date = curr_exp
      except Exception:
        pass

    new_expire = base_date + datetime.timedelta(days=days)
    expire_date = new_expire.strftime("%Y-%m-%d %H:%M:%S")
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
      f"✅ **Премиум успешно выдан!**\n\n🆔 Пользователь: `{target_id}`\n📅"
      f" Срок: **{days} дней** (до {formatted_date})",
      parse_mode="Markdown",
      reply_markup=get_main_keyboard(message.from_user.id),
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


@router.message(GiveTokensState.user_id)
async def process_give_tokens_id(message: Message, state: FSMContext):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    return
  target_id = int(message.text.strip())
  await state.update_data(target_id=target_id)
  await message.answer(
      f"🪙 Сколько **токенов** начислить пользователю `{target_id}`?\n*(Введи"
      " целое число, например: 50)*",
      parse_mode="Markdown",
  )
  await state.set_state(GiveTokensState.amount)


@router.message(GiveTokensState.amount)
async def process_give_tokens_amount(message: Message, state: FSMContext, bot: Bot):
  if not message.text.isdigit():
    await message.answer("❌ Ошибка! Введите количество токенов цифрами.")
    return
  amount = int(message.text.strip())
  data = await state.get_data()
  target_id = data["target_id"]
  await state.clear()

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
      f"✅ **Токены успешно выданы!**\n\n🆔 Пользователь: `{target_id}`\n🪙"
      f" Начислено: **+{amount} токенов**",
      parse_mode="Markdown",
      reply_markup=get_main_keyboard(message.from_user.id),
  )
  try:
    await bot.send_message(
        target_id,
        f"🎁 **Администратор начислил вам {amount} токенов!** Проверьте баланс"
        " в профиле.",
        parse_mode="Markdown",
    )
  except Exception:
    pass


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
        exp_date = datetime.datetime.strptime(
            prem_expires, "%Y-%m-%d %H:%M:%S"
        )
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
        f"👤 Ваш Профиль\n\n🆔 Ваш ID: `{user_id}`\n💲 Баланс: {tokens if tokens"
        f" is not None else 0} токенов\n👑 Премиум-подписка: {prem_status}\n📦"
        f" Куплено папок: {bought} шт."
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
        "🛍 Доступные фулы:\n*(Нажми на нужную, чтобы забрать ссылку)*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

  elif text == "👥 Реферальная система":
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    ref_text = (
        "👥 Реферальная система\n\nПриглашай друзей по своей персональной"
        f" ссылке:\n`{ref_link}`\n\n🎁 Бонусы:\n• За каждого приглашенного друга"
        " вы получаете 7 токенов!\n• Получайте 15% от суммы всех покупок"
        " токенами ваших рефералов!"
    )
    await message.answer(ref_text, parse_mode="Markdown")

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
            exp_dt = datetime.datetime.strptime(p_user[2], "%Y-%m-%d %H:%M:%S")
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

    if not top_list:
      await message.answer(
          "🏆 В топе пока никого нет.\nПриглашайте друзей по реферальной ссылке,"
          " чтобы занять первое место!\n\n*(каждую неделю сбрасывается топ и"
          " выдается премиум подписка на 14 дней)*"
      )
      return

    text_top = "🏆 **Топ-10 игроков по рефералам:**\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for i, row in enumerate(top_list, start=1):
      uid, is_prem, count = row
      prefix = medals[i - 1] if i <= 3 else f"{i}."
      crown = " 👑" if is_prem == 1 else ""
      text_top += f"{prefix} Пользователь `{uid}`{crown} — **{count}** реф.\n"

    text_top += (
        "\n*(каждую неделю сбрасывается топ и выдается премиум подписка на 14"
        " дней)*"
    )
    await message.answer(text_top, parse_mode="Markdown")

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


# --- CALLBACKS И ПОКУПКА ---


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await state.clear()
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

    tokens = u_data[0] if (u_data and u_data[0] is not None) else 0
    is_prem = u_data[1] if u_data else 0
    referrer_id = u_data[3] if u_data else 0
    prem_expires = u_data[4] if u_data else None

    if is_prem == 1 and prem_expires:
      try:
        exp_date = datetime.datetime.strptime(
            prem_expires, "%Y-%m-%d %H:%M:%S"
        )
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
