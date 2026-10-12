import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup

BOT_TOKEN = "8549927929:AAE_aDtLHVjkR9h50ABM233OZprEa6l-GvM"
ADMIN_ID = 5681420389

CHANNEL_USERNAME = "@ucu52"  # Замени на юзернейм своего канала

LINKS = {
    "altuxa1": "https://mega.nz/folder/LgRyxA4I#_twNUIbFBHlz0rNhwPZ8UA",
    "altuxa2": "https://mega.nz/folder/GsglkToB#a8DZlf-pqUm0wbRziVFWnA",
    "altuxa3": "https://mega.nz/folder/35JAXBiL#5oNGxR1cNwzeSBorDStpag",
    "altuxa4": "https://mega.nz/folder/6ohjyZwI#etdlXEYAbT15i4uMOTS8sg",
    "altuxa5": "https://mega.nz/folder/3x4VFahR#kR0gcoHejTj-kI89jq-Dug",
    "altuxa6": "https://mega.nz/folder/PthnVCDZ#0UTHgooZDYGyyJSxUrMQrw",
    "altuxa7": "https://mega.nz/folder/WlpDSIYK#UxqlTXMgZQLp5QZ8bco7Gg",
    "altuxa8": "https://mega.nz/folder/yhYWYASA#oqMZEgQu5r0bOCjecYeg",
    "altuxa9": "https://mega.nz/folder/P4IxnyWY#Cv0WZ36W_C5U74PjidU9sw",
    "altuxa10": "https://mega.nz/folder/z5J1lQAZ#GZzYehJxTqh08e7tJ-F5-Q",
    "altuxa11": "https://mega.nz/folder/2khEVbLY#GYYp716jlQCHnB0yOPmyMg",
    "altuxa12": "https://mega.nz/folder/ToIGgIga#sDm6-gvxscU3EmqdB1GZvQ",
    "altuxa13": "https://mega.nz/folder/mohg1bAJ#ImRQIAkaPM13iKyuSMOZbw",
    "altuxa14": "https://mega.nz/folder/35IDEZJQ#5Cd7DT8ngvS67RVsdZ-2_g",
    "kochbratan": "https://mega.nz/file/Xw5HhTzb#zqbkhu_by_sJNQTRGK-7TIMlKk8POah5fFmyPshp3F0",
    "pantera": "https://mega.nz/folder/L9InSR5Q#5cdRLP1pzaiXvwh_3MK7uw",
    "sestraIbratya": "https://mega.nz/folder/qlomBJAb#IisOTAVUYTcPJmquWs1LA",
    "voronezhPODIEZD": "https://mega.nz/file/ntABTZoa#yR2cPZ5UK1A7PKUuS2cPKJxBAcz948qVbmUtBdLzoJU",
    "sobakaIdevochka": "https://mega.nz/file/slBGQCCK#iZUANS5xf61dwxy5P8vWm0zAg0EQ690E2H3GXrWDcjE",
}

ITEM_TITLES = {
    "kochbratan": "📁 Коч братан",
    "pantera": "📁 Пантера",
    "sestraIbratya": "📁 Сестра и братья",
    "voronezhPODIEZD": "📁 Воронеж подъезд",
    "sobakaIdevochka": "📁 Собака и девочка",
}

for i in range(1, 15):
    ITEM_TITLES[f"altuxa{i}"] = f"📁 Альтушка {i}"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_balances = {}
user_referrers = {}
user_premium_until = {}
user_purchases = {}
purchased_items_global = set()
promo_codes = {}

class PromoState(StatesGroup):
    waiting_for_code = State()

class AdminState(StatesGroup):
    add_token_id = State()
    add_token_amount = State()
    add_prem_id = State()
    add_prem_days = State()
    promo_name = State()
    promo_type = State()
    promo_value = State()
    promo_uses = State()

def is_premium_active(user_id: int) -> bool:
    if user_id in user_premium_until:
        if datetime.now() < user_premium_until[user_id]:
            return True
    return False

def get_item_price(item_key: str) -> int:
    if item_key not in purchased_items_global:
        return 3
    return 15

async def check_subscription(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            return True
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
    return False

def main_kb(user_id: int):
    kb = [
        [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="👤 Мой Профиль")],
        [KeyboardButton(text="👑 Премиум-подписка"), KeyboardButton(text="🪙 Как получить токены?")],
        [KeyboardButton(text="👥 Реферальная система")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def main_shop_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Папки с Альтушками (14 пак.)", callback_data="category_altuxa")],
        [InlineKeyboardButton(text="📁 Коч братан", callback_data="item_kochbratan")],
        [InlineKeyboardButton(text="📁 Пантера", callback_data="item_pantera")],
        [InlineKeyboardButton(text="📁 Сестра и братья", callback_data="item_sestraIbratya")],
        [InlineKeyboardButton(text="📁 Воронеж подъезд", callback_data="item_voronezhPODIEZD")],
        [InlineKeyboardButton(text="📁 Собака и девочка", callback_data="item_sobakaIdevochka")]
    ])

def altuxa_shop_kb(user_id: int):
    user_bought = user_purchases.get(user_id, set())
    keyboard = []
    
    for i in range(1, 15):
        key = f"altuxa{i}"
        is_bought = key in user_bought
        price = get_item_price(key)
        
        if is_bought:
            btn_text = f"✅ Альтушка {i} (Куплено — открыть)"
        elif price == 3:
            btn_text = f"🔥 Альтушка {i} — 3 токена (-80% ПЕРВЫЙ!)"
        else:
            btn_text = f"📁 Альтушка {i} — 15 токенов"
            
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"item_{key}")])
        
    keyboard.append([InlineKeyboardButton(text="◀️ Назад в магазин", callback_data="back_to_shop")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def buy_options_kb(item_code: str, price: int):
    buttons = [
        [InlineKeyboardButton(text=f"🪙 Купить за токены ({price})", callback_data=f"buy_tokens_{item_code}")]
    ]
    
    # Кнопка оплаты Звездами выдается ТОЛЬКО для остальных товаров (Альтушкам не показываем)
    if not item_code.startswith("altuxa"):
        buttons.append([InlineKeyboardButton(text="⭐️ Купить за Звезды (15 шт. напрямую)", callback_data=f"buy_stars_{item_code}")])
        
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_shop")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪙 Выдать Token", callback_data="admin_add_tokens")],
        [InlineKeyboardButton(text="👑 Выдать Premium status", callback_data="admin_add_prem")],
        [InlineKeyboardButton(text="🎟 Создать промокод", callback_data="admin_create_promo")]
    ])

@dp.message()
async def check_sub_middleware(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not await check_subscription(user_id):
        sub_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME.replace('@', '')}")],
            [InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")]
        ])
        await message.answer(
            "❌ **Доступ заблокирован!**\n\nЧтобы пользоваться ботом, вы должны быть подписаны на наш канал.",
            reply_markup=sub_kb,
            parse_mode="Markdown"
        )
        return

    if message.text == "/start":
        await state.clear()
        user_balances.setdefault(user_id, 0)
        args = message.text.split()
        if len(args) > 1 and args[1].isdigit():
            referrer_id = int(args[1])
            if referrer_id != user_id and user_id not in user_referrers:
                user_referrers[user_id] = referrer_id
                user_balances[referrer_id] = user_balances.get(referrer_id, 0) + 15
                try:
                    await bot.send_message(
                        referrer_id, 
                        "🎉 К вам перешли по реферальной ссылке, Вы получили 15 токенов и 10% от его покупок токенами"
                    )
                except:
                    pass
        await message.answer("👋 **Главное меню:**", reply_markup=main_kb(user_id), parse_mode="Markdown")

    elif message.text == "🪙 Как получить токены?":
        text = (
            "📌 **Как получить токены?**\n\n"
            "1. Возьмите фото. https://t.me/JmcDeveloper/11 \n"
            "2. Оставляйте его в комментариях под постами/ответами другим людям.\n"
            "3. Отправляйте доказательства (скриншоты) в директ канала. https://t.me/JmcDeveloper?direct \n\n"
            "💰 **1 отправка фото в комментариях = 1 токен!**"
        )
        await message.answer(text, parse_mode="Markdown")

    elif message.text == "👤 Мой Профиль":
        bal = user_balances.get(user_id, 0)
        if is_premium_active(user_id):
            exp_date = user_premium_until[user_id].strftime("%d.%m.%Y %H:%M")
            prem_status = f"👑 Активна до {exp_date}"
        else:
            prem_status = "❌ Не активна"

        bought_count = len(user_purchases.get(user_id, set()))
        profile_text = (
            f"👤 **Ваш Профиль**\n\n"
            f"🆔 Ваш ID: `{user_id}`\n"
            f"💰 Баланс: **{bal} токенов**\n"
            f"👑 Премиум-подписка: **{prem_status}**\n"
            f"📦 Куплено товаров: **{bought_count} шт.**"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="enter_promo")]
        ])
        await message.answer(profile_text, reply_markup=kb, parse_mode="Markdown")

    elif message.text == "👑 Премиум-подписка":
        text = (
            "👑 **Премиум-подписка на 1 МЕСЯЦ (30 дней)**\n\n"
            "Дает право ПОКУПАТЬ **АБСОЛЮТНО ЛЮБЫЕ папки бесплатно** (даже с 0 токенов на балансе)!\n\n"
            "💳 **Стоимость:**\n"
            "• **50 Звезд** (любым подарком в Telegram за 50 звезд на профиль **@ucu52**)."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Отправить Подарок 50 Stars (@ucu52)", callback_data="buy_prem_stars")]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

    elif message.text == "👥 Реферальная система":
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
        text = (
            "👥 **Реферальная система**\n\n"
            "Приглашайте друзей и получайте бонусы:\n"
            "• **+15 токенов** за каждого приглашенного друга.\n"
            "• **10% токенов** от всех потраченных им токенов в магазине!\n\n"
            f"🔗 Ваша реферальная ссылка:\n`{ref_link}`"
        )
        await message.answer(text, parse_mode="Markdown")

    elif message.text == "🛒 Магазин":
        await message.answer("🛒 **Выберите категорию или товар:**", reply_markup=main_shop_kb(), parse_mode="Markdown")

    elif message.text == "⚙️ Админ-панель" and user_id == ADMIN_ID:
        await message.answer("⚙️ **Административная панель:**", reply_markup=admin_panel_kb(), parse_mode="Markdown")

    else:
        current_state = await state.get_state()
        if current_state is not None:
            return
        else:
            await message.answer("Используйте кнопки меню для навигации 👇", reply_markup=main_kb(user_id))

@dp.callback_query(F.data == "check_sub")
async def callback_check_sub(call: types.CallbackQuery):
    user_id = call.from_user.id
    if await check_subscription(user_id):
        await call.message.delete()
        await call.message.answer("✅ Спасибо за подписку! Добро пожаловать.", reply_markup=main_kb(user_id))
    else:
        await call.answer("❌ Вы всё еще не подписаны на канал!", show_alert=True)

@dp.callback_query(F.data == "back_to_shop")
async def back_to_shop_handler(call: types.CallbackQuery):
    if not await check_subscription(call.from_user.id): return
    await call.message.edit_text("🛒 **Выберите категорию или товар:**", reply_markup=main_shop_kb(), parse_mode="Markdown")

@dp.callback_query(F.data == "category_altuxa")
async def show_altuxa_category(call: types.CallbackQuery):
    user_id = call.from_user.id
    if not await check_subscription(user_id): return
    left = 14 - sum(1 for i in range(1, 15) if f"altuxa{i}" in user_purchases.get(user_id, set()))
    text = f"🔥 **Папки с Альтушками**\nОсталось не куплено у вас: **{left} из 14**\nВыберите папку ниже:"
    await call.message.edit_text(text, reply_markup=altuxa_shop_kb(user_id), parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data.startswith("item_"))
async def select_item(call: types.CallbackQuery):
    user_id = call.from_user.id
    if not await check_subscription(user_id): return
    item_key = call.data.replace("item_", "")
    user_bought = user_purchases.get(user_id, set())
    link = LINKS.get(item_key, "https://t.me/")

    if item_key in user_bought:
        await call.message.answer(f"✅ Вы уже покупали этот товар!\nВаша ссылка: {link}")
        await call.answer()
        return

    price = get_item_price(item_key)
    title = ITEM_TITLES.get(item_key, item_key)
    discount_note = "\n🔥 **ВНИМАНИЕ! Скидка -80% первому покупателю этого пака!** (всего 3 токена вместо 15)" if price == 3 else ""

    text = f"📹 **{title}**\nЦена: **{price} токенов**{discount_note}"
    await call.message.answer(text, reply_markup=buy_options_kb(item_key, price), parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data.startswith("buy_tokens_"))
async def process_buy_tokens(call: types.CallbackQuery):
    user_id = call.from_user.id
    if not await check_subscription(user_id): return
    item_key = call.data.replace("buy_tokens_", "")
    bal = user_balances.get(user_id, 0)
    link = LINKS.get(item_key, "https://t.me/")
    user_bought = user_purchases.setdefault(user_id, set())

    if item_key in user_bought:
        await call.message.answer(f"✅ У вас уже куплен этот товар!\nСсылка: {link}")
        await call.answer()
        return

    if is_premium_active(user_id):
        user_bought.add(item_key)
        await call.message.answer(f"👑 **Премиум-доступ активен!**\nВаша ссылка абсолютно бесплатно: {link}")
        await call.answer()
        return

    cost = get_item_price(item_key)
    if bal >= cost:
        user_balances[user_id] -= cost
        user_bought.add(item_key)
        purchased_items_global.add(item_key)

        if user_id in user_referrers:
            referrer_id = user_referrers[user_id]
            ref_bonus = max(1, int(cost * 0.10))
            user_balances[referrer_id] = user_balances.get(referrer_id, 0) + ref_bonus
            try:
                await bot.send_message(referrer_id, f"🎁 Ваш реферал потратил токены в магазине! Вам начислено **+{ref_bonus} токенов** (10% от его покупки).")
            except:
                pass

        await call.message.answer(f"✅ Успешно куплено! Ваша ссылка: {link}")
    else:
        await call.message.answer(f"❌ Недостаточно токенов! Нужен {cost}, у вас {bal}.\n💡 Купите Премиум-подписку или зарабатывайте токены в комментариях!")
    await call.answer()

@dp.callback_query(F.data.startswith("buy_stars_"))
async def process_buy_stars(call: types.CallbackQuery):
    if not await check_subscription(call.from_user.id): return
    item_key = call.data.replace("buy_stars_", "")
    purchased_items_global.add(item_key)
    await call.message.answer("⭐ Для покупки этого товара за 15 Звезд отправьте подарок/звезды напрямую на аккаунт **@ucu52**.")
    await call.answer()

@dp.callback_query(F.data == "enter_promo")
async def start_promo_input(call: types.CallbackQuery, state: FSMContext):
    if not await check_subscription(call.from_user.id): return
    await state.set_state(PromoState.waiting_for_code)
    await call.message.answer("🎟 Отправьте промокод текстом в чат:")
    await call.answer()

@dp.callback_query(F.data == "buy_prem_stars")
async def buy_prem_stars(call: types.CallbackQuery):
    if not await check_subscription(call.from_user.id): return
    await call.message.answer("⭐ Для покупки Премиум-подписки на 1 месяц отправьте **любой подарок за 50 звезд** напрямую на аккаунт **@ucu52**.")
    await call.answer()

# Админ-панель
@dp.callback_query(F.data == "admin_add_tokens")
async def start_add_tokens(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.add_token_id)
    await call.message.answer("🔢 Введите **Telegram ID** пользователя, которому хотите выдать токены:")
    await call.answer()

@dp.callback_query(F.data == "admin_add_prem")
async def start_add_prem(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.add_prem_id)
    await call.message.answer("🔢 Введите **Telegram ID** пользователя для выдачи Премиума:")
    await call.answer()

@dp.callback_query(F.data == "admin_create_promo")
async def start_create_promo(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminState.promo_name)
    await call.message.answer("🎟 Введите **название промокода** (например: `BONUS2026`):")
    await call.answer()

@dp.callback_query(AdminState.promo_type, F.data.startswith("promo_type_"))
async def process_promo_type_cb(call: types.CallbackQuery, state: FSMContext):
    p_type = call.data.replace("promo_type_", "")
    await state.update_data(promo_type=p_type)
    await state.set_state(AdminState.promo_value)
    if p_type == "tokens":
        await call.message.answer("🪙 Введите **количество токенов**, которое получит игрок:")
    else:
        await call.message.answer("📅 Введите **количество дней Премиума**, которое получит игрок:")
    await call.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
