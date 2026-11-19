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


# ─── ПЕРЕВОДЫ ────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    # ── общие ──────────────────────────────────────────────────────────────
    "appeal": {
        "ru": "Если вы ограничены от бота или заблокированы, напишите @ucu52, если вы получили наказание незаслуженно.",
        "en": "If you are restricted or banned, write to @ucu52 if you believe the punishment is unfair.",
    },
    "sub_required": {
        "ru": "🔒 Чтобы пользоваться ботом, подпишись на наш канал и нажми «Я подписался».",
        "en": "🔒 To use the bot, subscribe to our channel and press \"I subscribed\".",
    },
    "sub_btn_subscribe": {"ru": "📢 Подписаться на канал", "en": "📢 Subscribe to channel"},
    "sub_btn_check":     {"ru": "✅ Я подписался",         "en": "✅ I subscribed"},
    "sub_thanks":        {"ru": "✅ Спасибо за подписку! Выбирай раздел в меню.", "en": "✅ Thanks for subscribing! Choose a section in the menu."},
    "sub_not_yet":       {"ru": "❌ Ты ещё не подписан на канал!", "en": "❌ You haven't subscribed yet!"},
    "cancelled":         {"ru": "Отменено.", "en": "Cancelled."},
    "no_access":         {"ru": "⛔️ У тебя нет доступа!", "en": "⛔️ You don't have access!"},
    "restricted_alert":  {
        "ru": "🔒 Вы ограничены от обменов и покупок. Если это незаслуженно — напишите @ucu52",
        "en": "🔒 You are restricted from exchanges and purchases. Write @ucu52 if unfair.",
    },
    "restricted_text": {
        "ru": "🔒 На вас подана жалоба. Обмены и покупки в магазине временно ограничены до решения администратора.\n\n{appeal}",
        "en": "🔒 A complaint has been filed against you. Exchanges and purchases are temporarily restricted pending admin review.\n\n{appeal}",
    },
    # ── старт / язык ───────────────────────────────────────────────────────
    "choose_lang":   {"ru": "🌐 Выберите язык / Choose language:", "en": "🌐 Choose language / Выберите язык:"},
    "lang_set_ru":   {"ru": "🇷🇺 Язык установлен: Русский", "en": "🇷🇺 Language set: Russian"},
    "lang_set_en":   {"ru": "🇬🇧 Language set: English", "en": "🇬🇧 Language set: English"},
    "welcome":       {"ru": "👋 Привет! Добро пожаловать в бота. Выбирай нужный раздел на клавиатуре ниже:", "en": "👋 Hello! Welcome to the bot. Choose a section from the keyboard below:"},
    # ── главное меню (кнопки) ──────────────────────────────────────────────
    "btn_profile":   {"ru": "👤 Мой Профиль",          "en": "👤 My Profile"},
    "btn_shop":      {"ru": "🛒 Магазин",               "en": "🛒 Shop"},
    "btn_ushop":     {"ru": "💎 Уникальный магазин",    "en": "💎 Unique Shop"},
    "btn_refs":      {"ru": "👥 Реферальная система",   "en": "👥 Referral System"},
    "btn_top":       {"ru": "🏆 Топ игроков",           "en": "🏆 Top Players"},
    "btn_howto":     {"ru": "🪙 Как получить токены?",  "en": "🪙 How to get tokens?"},
    "btn_subs":      {"ru": "💳 Подписки",              "en": "💳 Subscriptions"},
    "btn_exchange":  {"ru": "🔄 Обмены",                "en": "🔄 Exchanges"},
    "btn_lottery":   {"ru": "🎰 Лотерея",               "en": "🎰 Lottery"},
    "btn_roulette":  {"ru": "🎲 Рулетка",               "en": "🎲 Roulette"},
    "btn_admin":     {"ru": "⚙️ Админ-панель",          "en": "⚙️ Admin Panel"},
    # ── профиль ───────────────────────────────────────────────────────────
    "profile_title":        {"ru": "👤 <b>Ваш Профиль</b>", "en": "👤 <b>Your Profile</b>"},
    "profile_id":           {"ru": "🆔 Ваш ID",             "en": "🆔 Your ID"},
    "profile_tokens":       {"ru": "💲 Баланс токенов",     "en": "💲 Token balance"},
    "profile_ytokens":      {"ru": "💎 Уникальные токены",  "en": "💎 Unique tokens"},
    "profile_ftokens":      {"ru": "📦 Фулловые токены",    "en": "📦 Full tokens"},
    "profile_premium":      {"ru": "👑 Премиум-подписка",   "en": "👑 Premium subscription"},
    "profile_upremium":     {"ru": "💎 Уникальная подписка","en": "💎 Unique subscription"},
    "profile_aura":         {"ru": "✨ Активная аура",       "en": "✨ Active aura"},
    "profile_bought":       {"ru": "📦 Собрано фулов",      "en": "📦 Purchased items"},
    "profile_warns":        {"ru": "⚠️ Предупреждения",     "en": "⚠️ Warnings"},
    "profile_exchanges":    {"ru": "🔄 Обменов проведено",  "en": "🔄 Exchanges completed"},
    "profile_prem_active":  {"ru": "✅ До {date} ({days} дн.)", "en": "✅ Until {date} ({days} d.)"},
    "profile_prem_no":      {"ru": "❌ Не активна",         "en": "❌ Not active"},
    "profile_prem_expired": {"ru": "❌ Не активна (истекла)","en": "❌ Not active (expired)"},
    "profile_aura_no":      {"ru": "❌ Нет",                "en": "❌ None"},
    "profile_aura_active":  {"ru": "✨ <b>{name}</b> ({pct}% бонусов, ещё {days} дн.)", "en": "✨ <b>{name}</b> ({pct}% bonus, {days} d. left)"},
    "btn_promo":            {"ru": "🎟 Ввести промокод",    "en": "🎟 Enter promo code"},
    # ── магазин ───────────────────────────────────────────────────────────
    "shop_empty":    {"ru": "📭 В магазине пока пусто. Либо же вы все раскупили!", "en": "📭 The shop is empty. Maybe you bought everything!"},
    "shop_title":    {"ru": "🛍 <b>Доступные фулы:</b>\n<i>(Нажми на нужный, чтобы посмотреть описание)</i>", "en": "🛍 <b>Available items:</b>\n<i>(Tap one to see description)</i>"},
    "shop_first_buy":{"ru": "🔥 {title}", "en": "🔥 {title}"},
    "shop_item":     {"ru": "📁 {title}", "en": "📁 {title}"},
    "shop_desc_price_first": {"ru": "💰 Цена: <s>{price}</s> → <b>{final} токенов</b> (скидка 80%!)", "en": "💰 Price: <s>{price}</s> → <b>{final} tokens</b> (80% off!)"},
    "shop_desc_price":       {"ru": "💰 Цена: <b>{price} токенов</b>", "en": "💰 Price: <b>{price} tokens</b>"},
    "shop_desc_bought":      {"ru": "👥 Купили за токены: <b>{n}</b>", "en": "👥 Purchased with tokens: <b>{n}</b>"},
    "shop_btn_buy":          {"ru": "🛒 Купить",   "en": "🛒 Buy"},
    "shop_btn_back":         {"ru": "⬅️ Назад",   "en": "⬅️ Back"},
    "shop_deleted":          {"ru": "❌ Этот товар был удален.", "en": "❌ This item has been deleted."},
    "shop_no_tokens":        {"ru": "❌ Недостаточно токенов!\nЦена: {price} токенов, а у вас: {bal}.", "en": "❌ Not enough tokens!\nPrice: {price} tokens, you have: {bal}."},
    "shop_bought_first":     {"ru": "🎉 Поздравляем! Ты первый покупатель фулла {title}!\n🔥 Применилась скидка 80%!\nСписано: {price} токенов.\n💲 Ваш остаток: {bal} токенов.\n\n🔗 Ссылка (нажми, чтобы показать):\n{link}", "en": "🎉 Congrats! You're the first buyer of {title}!\n🔥 80% discount applied!\nCharged: {price} tokens.\n💲 Your balance: {bal} tokens.\n\n🔗 Link (tap to reveal):\n{link}"},
    "shop_bought":           {"ru": "✅ Спасибо за покупку фула {title}!\n💰 Списано: {price} токенов. Остаток: {bal} токенов.\n\n🔗 Ссылка (нажми, чтобы показать):\n{link}", "en": "✅ Thanks for purchasing {title}!\n💰 Charged: {price} tokens. Balance: {bal} tokens.\n\n🔗 Link (tap to reveal):\n{link}"},
    "shop_prem_free":        {"ru": "👑 Премиум-доступ применен!\n\n✅ Вы получили фулл {title} бесплатно по подписке!\n\n🔗 Ссылка (нажми, чтобы показать):\n{link}", "en": "👑 Premium access applied!\n\n✅ You received {title} for free with your subscription!\n\n🔗 Link (tap to reveal):\n{link}"},
    "shop_fulltoken_used":   {"ru": "✅ Успешная покупка фулла {title} за фулловый токен!\n\n🔗 Ссылка (нажми, чтобы показать):\n{link}", "en": "✅ Purchased {title} using a full token!\n\n🔗 Link (tap to reveal):\n{link}"},
    "shop_fulltoken_ask":    {"ru": "❓ У вас есть фулловый токен. Использовать его или заплатить обычными токенами ({price} шт.)?", "en": "❓ You have a full token. Use it or pay with regular tokens ({price})?"},
    "shop_btn_usefull":      {"ru": "📦 Использовать Фулловый токен", "en": "📦 Use Full Token"},
    "shop_btn_usenorm":      {"ru": "🪙 Обычными ({price} токенов)",  "en": "🪙 Regular ({price} tokens)"},
    "shop_fulltoken_gone":   {"ru": "❌ У вас больше нет фулловых токенов.", "en": "❌ You have no more full tokens."},
    "ref_bonus_msg":         {"ru": "💰 Реферальный бонус!\nВаш реферал совершил покупку.\n🎁 Вам начислено {n} токенов (15% от покупки).", "en": "💰 Referral bonus!\nYour referral made a purchase.\n🎁 You received {n} tokens (15% of purchase)."},
    # ── уникальный магазин ────────────────────────────────────────────────
    "ushop_empty":           {"ru": "📭 Уникальный магазин пуст.", "en": "📭 Unique shop is empty."},
    "ushop_title":           {"ru": "💎 <b>Уникальный магазин</b>\nНажми на товар для описания и покупки:", "en": "💎 <b>Unique Shop</b>\nTap an item to see description and purchase:"},
    "ushop_no_sub":          {"ru": "🔒 <i>Недоступно по подписке — только за токены</i>", "en": "🔒 <i>Not available with subscription — tokens only</i>"},
    "ushop_amount":          {"ru": "📦 Осталось: <b>{n}</b>", "en": "📦 In stock: <b>{n}</b>"},
    "ushop_bought":          {"ru": "👥 Купили за токены: <b>{n}</b>", "en": "👥 Purchased with tokens: <b>{n}</b>"},
    "ushop_btn_sub":         {"ru": "💎 Получить по подписке (бесплатно)", "en": "💎 Get with subscription (free)"},
    "ushop_btn_ytokens":     {"ru": "💎 Купить за {n} уник. токенов", "en": "💎 Buy for {n} unique tokens"},
    "ushop_btn_tokens":      {"ru": "🪙 Купить за {n} обычных токенов", "en": "🪙 Buy for {n} regular tokens"},
    "ushop_no_tokens_y":     {"ru": "❌ Недостаточно уникальных токенов! Нужно: {n}.", "en": "❌ Not enough unique tokens! Required: {n}."},
    "ushop_no_tokens_t":     {"ru": "❌ Недостаточно токенов! Нужно: {n}.", "en": "❌ Not enough tokens! Required: {n}."},
    "ushop_bought_y":        {"ru": "💎 <b>Успешная покупка!</b>\nВы приобрели: <b>{title}</b> за {n} уникальных токенов.\nВсё уже начислено в профиль.", "en": "💎 <b>Purchase successful!</b>\nYou got: <b>{title}</b> for {n} unique tokens.\nEverything has been added to your profile."},
    "ushop_bought_t":        {"ru": "🪙 <b>Успешная покупка!</b>\nВы приобрели: <b>{title}</b> за {n} обычных токенов.\nВсё уже начислено в профиль.", "en": "🪙 <b>Purchase successful!</b>\nYou got: <b>{title}</b> for {n} regular tokens.\nEverything has been added to your profile."},
    "ushop_bought_sub":      {"ru": "💎 <b>Получено по подписке!</b>\nВы получили: <b>{title}</b> бесплатно по уникальной подписке.\nВсё уже начислено в профиль.", "en": "💎 <b>Received via subscription!</b>\nYou got: <b>{title}</b> for free with your unique subscription.\nEverything has been added to your profile."},
    "ushop_no_sub_err":      {"ru": "❌ Этот товар недоступен по подписке — только за токены.", "en": "❌ This item is not available with subscription — tokens only."},
    "ushop_gone":            {"ru": "❌ Товар закончился.", "en": "❌ Item is out of stock."},
    "ushop_bad_cmd":         {"ru": "❌ Команда товара настроена неверно. Напиши админу.", "en": "❌ Item command is misconfigured. Contact admin."},
    "ushop_error":           {"ru": "⚠️ Произошла ошибка при выдаче товара. Напиши админу.", "en": "⚠️ An error occurred while issuing the item. Contact admin."},
    "ushop_no_uprem":        {"ru": "❌ У вас нет активной уникальной подписки.", "en": "❌ You don't have an active unique subscription."},
    # ── рефералы ─────────────────────────────────────────────────────────
    "ref_title":    {"ru": "👥 <b>Реферальная система</b>", "en": "👥 <b>Referral System</b>"},
    "ref_link_txt": {"ru": "Приглашай друзей по своей ссылке:", "en": "Invite friends with your link:"},
    "ref_bonus_txt":{"ru": "🎁 За каждого приглашенного (после подписки на канал) — <b>{n} токенов</b>!", "en": "🎁 For each invited friend (after subscribing to the channel) — <b>{n} tokens</b>!"},
    "ref_income_txt":{"ru": "💰 А также вы получаете <b>15% дохода</b> от трат ваших рефералов токенами в обычном магазине!", "en": "💰 You also receive <b>15% income</b> from your referrals' token purchases in the regular shop!"},
    "ref_new_user": {"ru": "🎉 По вашей рефке зарегистрировался новый пользователь!\n❤️ Вам начислено {n} токенов!", "en": "🎉 A new user registered with your referral link!\n❤️ You received {n} tokens!"},
    # ── лотерея ───────────────────────────────────────────────────────────
    "lottery_title":     {"ru": "🎰 <b>Лотерея</b>", "en": "🎰 <b>Lottery</b>"},
    "lottery_prize":     {"ru": "🏆 Приз: <b>{total} токенов</b> ({base} + {pool} от взносов)", "en": "🏆 Prize: <b>{total} tokens</b> ({base} + {pool} from entries)"},
    "lottery_cost":      {"ru": "🎟 Участие: <b>{cost}</b>", "en": "🎟 Entry: <b>{cost}</b>"},
    "lottery_cost_auto": {"ru": " (растёт вместе с призом)", "en": " (grows with prize)"},
    "lottery_players":   {"ru": "👥 Участников: <b>{n}</b> (минимум для розыгрыша: {min})", "en": "👥 Players: <b>{n}</b> (minimum to draw: {min})"},
    "lottery_time":      {"ru": "⏱ До конца розыгрыша: <b>{h} ч {m} мин</b>", "en": "⏱ Time left: <b>{h}h {m}min</b>"},
    "lottery_desc_paid": {"ru": "Половина токенов за участие добавляется к призу.", "en": "Half the entry tokens are added to the prize."},
    "lottery_desc_free": {"ru": "Участие бесплатное.", "en": "Entry is free."},
    "lottery_joined":    {"ru": "\n\n✅ Ты уже участвуешь в этом розыгрыше!", "en": "\n\n✅ You're already in this draw!"},
    "lottery_btn_join":  {"ru": "🎟 Участвовать ({cost})", "en": "🎟 Join ({cost})"},
    "lottery_btn_free":  {"ru": "🎟 Участвовать (бесплатно)", "en": "🎟 Join (free)"},
    "lottery_btn_notif_off": {"ru": "🔕 Отключить уведомления", "en": "🔕 Disable notifications"},
    "lottery_btn_notif_on":  {"ru": "🔔 Включить уведомления",  "en": "🔔 Enable notifications"},
    "lottery_notif_off": {"ru": "Уведомления о крупных лотереях отключены 🔕!", "en": "Big lottery notifications disabled 🔕!"},
    "lottery_notif_on":  {"ru": "Уведомления о крупных лотереях включены 🔔!", "en": "Big lottery notifications enabled 🔔!"},
    "lottery_win":       {"ru": "🎉 <b>Ты выиграл в лотерее!</b>\n🏆 Тебе начислено: <b>{prize} токенов</b>.\n\nХотите ещё попытать удачу?", "en": "🎉 <b>You won the lottery!</b>\n🏆 You received: <b>{prize} tokens</b>.\n\nWant to try again?"},
    "lottery_lose":      {"ru": "😔 В этот раз удача была не на твоей стороне. Победитель уже получил приз.\n\nХотите ещё попытать удачу?", "en": "😔 Luck wasn't on your side this time. The winner has already received the prize.\n\nWant to try again?"},
    "lottery_no_players":{"ru": "😔 В розыгрыше не набралось нужное число участников ({cur} из {min}). Приз не разыгран.{refund}\n\nХотите ещё попытать удачу?", "en": "😔 Not enough players ({cur} of {min}). No prize drawn.{refund}\n\nWant to try again?"},
    "lottery_refund":    {"ru": " Тебе вернули <b>{n} токен(ов)</b>.", "en": " You were refunded <b>{n} token(s)</b>."},
    "lottery_joined_msg":{"ru": "🎟 Ты участвуешь в лотерее!{charged}\nРезультат придёт сюда, когда закончится розыгрыш.", "en": "🎟 You're in the lottery!{charged}\nResults will arrive here when the draw ends."},
    "lottery_charged":   {"ru": " Списано: <b>{n} токен(ов)</b>.", "en": " Charged: <b>{n} token(s)</b>."},
    "lottery_free_entry":{"ru": " Участие бесплатное.", "en": " Entry is free."},
    "lottery_already":   {"ru": "✅ Ты уже участвуешь!", "en": "✅ You're already in!"},
    "lottery_ended":     {"ru": "⏳ Этот розыгрыш уже завершён.", "en": "⏳ This draw has already ended."},
    "lottery_ending":    {"ru": "⏳ Розыгрыш уже заканчивается.", "en": "⏳ The draw is about to end."},
    "lottery_no_tokens": {"ru": "❌ Недостаточно токенов! Участие стоит {n}.", "en": "❌ Not enough tokens! Entry costs {n}."},
    "lottery_price_changed": {"ru": "💱 Цена участия изменилась: теперь {cost}. Проверь и нажми «Участвовать» ещё раз.", "en": "💱 Entry price changed: now {cost}. Check and tap join again."},
    "lottery_soon":      {"ru": "⏳ Новый розыгрыш скоро начнётся. Загляни чуть позже!", "en": "⏳ A new draw will start soon. Check back later!"},
    "lottery_btn_again": {"ru": "🎰 Попытать удачу ещё раз", "en": "🎰 Try again"},
    # ── рулетка ───────────────────────────────────────────────────────────
    "roulette_intro":    {"ru": "🎲 <b>Рулетка 50/50</b>\n\nСтавишь токены — и либо удваиваешь их, либо теряешь.\nВыиграл: получаешь ставку ×2 (ставка 4 → на баланс вернётся 8).\nПроиграл: ставка сгорает.\n\n💰 Ставка: от <b>{min}</b> до <b>{max}</b> токенов.\nТвой баланс: <b>{bal}</b> токенов.\n\n✍️ Напиши в чат, сколько токенов ставишь:", "en": "🎲 <b>Roulette 50/50</b>\n\nBet tokens — either double them or lose them.\nWin: get bet ×2 (bet 4 → receive 8).\nLose: bet is gone.\n\n💰 Bet: from <b>{min}</b> to <b>{max}</b> tokens.\nYour balance: <b>{bal}</b> tokens.\n\n✍️ Type how many tokens you want to bet:"},
    "roulette_win":      {"ru": "🎉 <b>Победа!</b> Ставка {bet} → <b>+{bet}</b> токенов.\nБаланс: <b>{bal}</b>", "en": "🎉 <b>Win!</b> Bet {bet} → <b>+{bet}</b> tokens.\nBalance: <b>{bal}</b>"},
    "roulette_lose":     {"ru": "💥 <b>Проигрыш.</b> Ставка {bet} токенов сгорела.\nБаланс: <b>{bal}</b>", "en": "💥 <b>Loss.</b> Bet of {bet} tokens burned.\nBalance: <b>{bal}</b>"},
    "roulette_bad_bet":  {"ru": "Напиши число от {min} до {max} — сколько токенов ставишь.", "en": "Type a number from {min} to {max} — how many tokens to bet."},
    "roulette_no_tokens":{"ru": "❌ Не хватает токенов. Баланс: <b>{bal}</b>. Напиши ставку поменьше или нажми другую кнопку меню.", "en": "❌ Not enough tokens. Balance: <b>{bal}</b>. Try a smaller bet or choose another menu option."},
    "roulette_btn_again":{"ru": "🔁 Сыграть снова ({n} токен.)", "en": "🔁 Play again ({n} tok.)"},
    "roulette_btn_new":  {"ru": "💰 Другая ставка", "en": "💰 Different bet"},
    # ── обмены ────────────────────────────────────────────────────────────
    "ex_menu_text":      {"ru": "🔄 <b>Обмены ссылками</b>\nВыставляй свой обмен или бери чужой. Чтобы провести обмен, нужно заплатить {n} токен.", "en": "🔄 <b>Link Exchanges</b>\nList your exchange or take someone else's. An exchange costs {n} token."},
    "ex_btn_new":        {"ru": "➕ Выставить обмен",   "en": "➕ Create exchange"},
    "ex_btn_all":        {"ru": "📋 Все обмены",         "en": "📋 All exchanges"},
    "ex_btn_search":     {"ru": "🔍 Поиск",              "en": "🔍 Search"},
    "ex_btn_my":         {"ru": "📦 Мои обмены",         "en": "📦 My exchanges"},
    "ex_btn_cancel":     {"ru": "❌ Отмена",             "en": "❌ Cancel"},
    "ex_btn_back":       {"ru": "⬅️ В обмены",           "en": "⬅️ Back to exchanges"},
    "ex_btn_delete":     {"ru": "🗑 Удалить",            "en": "🗑 Delete"},
    "ex_btn_take":       {"ru": "🤝 Провести обмен",     "en": "🤝 Make exchange"},
    "ex_btn_complain":   {"ru": "🚨 Пожаловаться",       "en": "🚨 Complain"},
    "ex_give_ask":       {"ru": "Что ты <b>отдаёшь</b>? (например: Пантера)", "en": "What are you <b>giving</b>? (e.g. Pantera)"},
    "ex_want_ask":       {"ru": "Что хочешь <b>получить</b>? (например: Сердцеед)", "en": "What do you want to <b>receive</b>? (e.g. Heartthrob)"},
    "ex_link_ask":       {"ru": "Отправь <b>свою ссылку</b> для обмена.\n{rule}", "en": "Send <b>your link</b> for the exchange.\n{rule}"},
    "ex_created":        {"ru": "✅ Обмен #{id} выставлен: <b>{give}</b> → <b>{want}</b>.\nТебе придёт уведомление, когда кто-то возьмётся за него.", "en": "✅ Exchange #{id} created: <b>{give}</b> → <b>{want}</b>.\nYou'll be notified when someone takes it."},
    "ex_too_many":       {"ru": "У тебя уже {n} открытых обменов. Удали лишний.", "en": "You already have {n} open exchanges. Delete one first."},
    "ex_own":            {"ru": "Это твой обмен.", "en": "This is your exchange."},
    "ex_taken":          {"ru": "Этот обмен уже забрали или удалили.", "en": "This exchange has already been taken or deleted."},
    "ex_no_tokens":      {"ru": "Нужно {n} токен для обмена.", "en": "You need {n} token to make an exchange."},
    "ex_too_late":       {"ru": "Не успел — этот обмен уже забрали.", "en": "Too late — this exchange has already been taken."},
    "ex_no_balance":     {"ru": "Не хватает токенов (нужно {n}).", "en": "Not enough tokens (need {n})."},
    "ex_done_taker":     {"ru": "🤝 <b>Обмен #{id}</b>: {give} → {want}\nСсылка обменщика (ID <code>{owner}</code>):\n{link}\n\nСписано {n} токен.", "en": "🤝 <b>Exchange #{id}</b>: {give} → {want}\nOwner's link (ID <code>{owner}</code>):\n{link}\n\nCharged {n} token."},
    "ex_notif_owner":    {"ru": "🔔 ID <code>{uid}</code> взялся за твой обмен #{id}: {give} → {want}\nЕго ссылка:\n{link}", "en": "🔔 ID <code>{uid}</code> took your exchange #{id}: {give} → {want}\nTheir link:\n{link}"},
    "ex_view_open":      {"ru": "🔄 <b>Обмен #{id}</b>\nОтдаёт: <b>{give}</b>\nХочет: <b>{want}</b>\nСтоимость обмена: {n} токен", "en": "🔄 <b>Exchange #{id}</b>\nGiving: <b>{give}</b>\nWants: <b>{want}</b>\nExchange cost: {n} token"},
    "ex_view_done_own":  {"ru": "✅ <b>Обмен #{id} завершен!</b>\n🆔 ID обменщика: <code>{uid}</code>\n🔗 Ссылка взявшегося:\n{link}", "en": "✅ <b>Exchange #{id} completed!</b>\n🆔 Taker ID: <code>{uid}</code>\n🔗 Taker's link:\n{link}"},
    "ex_view_done_take": {"ru": "✅ <b>Обмен #{id} завершен!</b>\n🆔 ID владельца: <code>{uid}</code>\n🔗 Ссылка владельца:\n{link}", "en": "✅ <b>Exchange #{id} completed!</b>\n🆔 Owner ID: <code>{uid}</code>\n🔗 Owner's link:\n{link}"},
    "ex_search_ask":     {"ru": "Напиши слово (или два): <b>Пантера</b>, <b>Сердцеед</b>\nИли ID человека / номер обмена — только цифры.", "en": "Type a word (or two): <b>Pantera</b>, <b>Heartthrob</b>\nOr person ID / exchange number — digits only."},
    "ex_my_empty":       {"ru": "📦 У вас нет активных обменов.", "en": "📦 You have no active exchanges."},
    "ex_my_title":       {"ru": "📦 <b>Ваши обмены:</b>", "en": "📦 <b>Your exchanges:</b>"},
    "ex_deleted":        {"ru": "🗑 Обмен удален.", "en": "🗑 Exchange deleted."},
    "ex_unavail":        {"ru": "Этот обмен уже недоступен.", "en": "This exchange is no longer available."},
    "ex_link_rule":      {"ru": "⚠️ Ссылка должна быть с mega.nz или cloud.mail.ru", "en": "⚠️ Link must be from mega.nz or cloud.mail.ru"},
    "ex_link_error":     {"ru": "❌ Неверная ссылка. ⚠️ Ссылка должна быть с mega.nz или cloud.mail.ru", "en": "❌ Invalid link. ⚠️ Link must be from mega.nz or cloud.mail.ru"},
    "ex_complain_confirm": {"ru": "⚠️ <b>Вы уверены, что хотите подать жалобу на этого пользователя?</b>\n\n• При ложной жалобе вы получите <b>1 предупреждение</b>.\n• Если вас реально обманули — смело отправляйте жалобу.", "en": "⚠️ <b>Are you sure you want to file a complaint against this user?</b>\n\n• A false complaint will give you <b>1 warning</b>.\n• If you were genuinely scammed — go ahead and complain."},
    "ex_complain_sent":  {"ru": "🚨 Жалоба отправлена админу.", "en": "🚨 Complaint sent to admin."},
    "ex_complain_dup":   {"ru": "Ты уже отправил жалобу по этому обмену.", "en": "You already filed a complaint for this exchange."},
    "ex_restricted_notif": {"ru": "🚨 На вас подана жалоба по обмену #{id}. Обмены и покупки в магазине временно ограничены до решения администратора.", "en": "🚨 A complaint was filed against you for exchange #{id}. Exchanges and purchases are temporarily restricted."},
    # ── промокод ──────────────────────────────────────────────────────────
    "promo_ask":         {"ru": "🎟 Введите промокод:", "en": "🎟 Enter promo code:"},
    "promo_ok_tokens":   {"ru": "✅ Промокод активирован!\n🎁 Вам зачислено: <b>{n} токенов</b>", "en": "✅ Promo code activated!\n🎁 You received: <b>{n} tokens</b>"},
    "promo_ok_premium":  {"ru": "✅ Промокод активирован!\n👑 Премиум на <b>{n} дней</b> (до {date})!", "en": "✅ Promo code activated!\n👑 Premium for <b>{n} days</b> (until {date})!"},
    "promo_not_found":   {"ru": "❌ Неверный или несуществующий промокод.", "en": "❌ Invalid or non-existent promo code."},
    "promo_used":        {"ru": "❌ Вы уже активировали этот промокод!", "en": "❌ You have already activated this promo code!"},
    "promo_limit":       {"ru": "❌ У этого промокода закончились активации.", "en": "❌ This promo code has run out of activations."},
    "promo_empty":       {"ru": "❌ Промокод не указан.", "en": "❌ No promo code entered."},
    "promo_bad_reward":  {"ru": "❌ Неизвестный тип награды у промокода.", "en": "❌ Unknown reward type for this promo code."},
    # ── топ ───────────────────────────────────────────────────────────────
    "top_menu":          {"ru": "🏆 <b>Выберите категорию топа:</b>", "en": "🏆 <b>Choose a leaderboard:</b>"},
    "top_btn_refs":      {"ru": "👥 Топ по рефералам", "en": "👥 Top by referrals"},
    "top_btn_lottery":   {"ru": "🎰 Топ по лотерее",   "en": "🎰 Top by lottery"},
    "top_btn_roulette":  {"ru": "🎲 Топ по рулетке",   "en": "🎲 Top by roulette"},
    "top_btn_back":      {"ru": "⬅️ Назад к выбору топов", "en": "⬅️ Back to leaderboards"},
    "top_refs_title":    {"ru": "🏆 <b>Топ-10 по рефералам</b>",          "en": "🏆 <b>Top-10 by referrals</b>"},
    "top_lot_title":     {"ru": "🏆 <b>Топ-10 по победам в лотерее</b>",  "en": "🏆 <b>Top-10 by lottery wins</b>"},
    "top_rl_title":      {"ru": "🏆 <b>Топ-10 по победам в рулетке</b>",  "en": "🏆 <b>Top-10 by roulette wins</b>"},
    "top_unit_refs":     {"ru": "реф.",   "en": "refs."},
    "top_unit_lot":      {"ru": "побед",  "en": "wins"},
    "top_unit_rl":       {"ru": "побед",  "en": "wins"},
    "top_empty":         {"ru": "Пока пусто.", "en": "Empty for now."},
    "top_reset_timer":   {"ru": "⏳ <b>Сброс топа:</b> через {d} дн. {h} ч.", "en": "⏳ <b>Leaderboard reset:</b> in {d}d {h}h"},
    "top_reset_soon":    {"ru": "⏳ <b>Сброс топа:</b> скоро сброс...", "en": "⏳ <b>Leaderboard reset:</b> coming soon..."},
    "top_reward_txt":    {"ru": "🎁 <b>Награда 1 места (на выбор):</b> Премиум на {pd} дней <u>ИЛИ</u> {yt} уникальных токена!", "en": "🎁 <b>1st place reward (choose):</b> Premium for {pd} days <u>OR</u> {yt} unique tokens!"},
    # ── подписки ──────────────────────────────────────────────────────────
    "subs_menu":         {"ru": "💳 <b>Подписки</b>\nВыбери тип подписки:", "en": "💳 <b>Subscriptions</b>\nChoose subscription type:"},
    "sub_btn_premium":   {"ru": "👑 Премиум-подписка",   "en": "👑 Premium subscription"},
    "sub_btn_unique":    {"ru": "💎 Уникальная подписка", "en": "💎 Unique subscription"},
    "sub_btn_back":      {"ru": "⬅️ Назад",              "en": "⬅️ Back"},
    "sub_prem_text":     {"ru": "👑 <b>Премиум-подписка на 1 МЕСЯЦ (30 дней)</b>\n\n<b>Преимущества:</b>\n• Покупай АБСОЛЮТНО ЛЮБЫЕ фулы бесплатно в обычном магазине!\n\n💳 <b>Способ 1:</b> Заплатить 50 Звёзд\n(любым подарком на профиль @ucu52)\n\n📸 <b>Способ 2 (за 100 фото):</b> Кинуть 100 фото в комментарии в тт\nПодробнее: https://t.me/JmcDeveloper/18\n\n<i>Если была хотя бы одна дублированная фото — подписка не выдаётся.</i>\nГотово? Отправляй сюда: https://t.me/JmcDeveloper?direct\n<b>(И СРАЗУ ГОВОРИТЕ ЧТО ДЛЯ ПРЕМИУМ ПОДПИСКИ)</b>", "en": "👑 <b>Premium subscription for 1 MONTH (30 days)</b>\n\n<b>Benefits:</b>\n• Buy ANY items for free in the regular shop!\n\n💳 <b>Option 1:</b> Pay 50 Stars\n(any gift to @ucu52)\n\n📸 <b>Option 2 (100 photos):</b> Post 100 photos in TT comments\nDetails: https://t.me/JmcDeveloper/18\n\n<i>If even one photo is a duplicate — subscription is not granted.</i>\nDone? Send here: https://t.me/JmcDeveloper?direct\n<b>(MENTION IT'S FOR PREMIUM SUBSCRIPTION)</b>"},
    "sub_unique_text":   {"ru": "💎 <b>Уникальная подписка на 1 МЕСЯЦ (30 дней)</b>\n\n<b>Преимущества:</b>\n• Покупай любые товары в 💎 Уникальном магазине бесплатно!\n• <i>Работает только для товаров без метки «только за токены».</i>\n\n💳 <b>Как купить:</b> Заплатить 100 Звёзд\n(любым подарком на профиль @ucu52)", "en": "💎 <b>Unique subscription for 1 MONTH (30 days)</b>\n\n<b>Benefits:</b>\n• Get any items in 💎 Unique Shop for free!\n• <i>Works only for items without the «tokens only» tag.</i>\n\n💳 <b>How to buy:</b> Pay 100 Stars\n(any gift to @ucu52)"},
    "sub_btn_buy_stars_50":  {"ru": "⭐ Отправить Подарок 50 Stars",  "en": "⭐ Send Gift 50 Stars"},
    "sub_btn_buy_stars_100": {"ru": "⭐ Отправить Подарок 100 Stars", "en": "⭐ Send Gift 100 Stars"},
    # ── как получить токены ───────────────────────────────────────────────
    "howto_menu":        {"ru": "🪙 <b>Как получить токены?</b>\nВыбери тип токена:", "en": "🪙 <b>How to get tokens?</b>\nChoose token type:"},
    "howto_btn_tokens":  {"ru": "🪙 Обычные токены",   "en": "🪙 Regular tokens"},
    "howto_btn_ytokens": {"ru": "💎 Уникальные токены", "en": "💎 Unique tokens"},
    "howto_btn_ftokens": {"ru": "📦 Фулловые токены",   "en": "📦 Full tokens"},
    "howto_btn_back":    {"ru": "⬅️ Назад",             "en": "⬅️ Back"},
    "howto_tokens_text": {"ru": "🪙 <b>Как получить обычные токены?</b>\n\n1. Возьмите фото: https://t.me/JmcDeveloper/11\n\n2. Оставляйте его в комментариях под постами/ответами другим людям <u>(под тему сестра и брата)</u>.\n\n3. Отправляйте доказательства (скриншоты) в директ канала: https://t.me/JmcDeveloper?direct\n\n💰 <b>1 отправка фото в комментариях = 1 токен!</b>\n\n📖 Детально расписал тут: https://t.me/JmcDeveloper/18\n<i>(не выдам токены если не посмотрите 👆)</i>", "en": "🪙 <b>How to get regular tokens?</b>\n\n1. Take a photo: https://t.me/JmcDeveloper/11\n\n2. Post it in comments under posts/replies to others <u>(topic: sister and brother)</u>.\n\n3. Send proof (screenshots) to the channel DM: https://t.me/JmcDeveloper?direct\n\n💰 <b>1 photo posted in comments = 1 token!</b>\n\n📖 Detailed guide: https://t.me/JmcDeveloper/18\n<i>(tokens won't be given if you don't read this 👆)</i>"},
    "howto_ytokens_text":{"ru": "💎 <b>Как получить уникальные токены?</b>\n\nИх можно только купить: <b>15 звёзд = 1 уникальный токен.</b>\n\n<b>Как купить?</b> Кинь любой подарок стоимостью 15 звёзд на аккаунт @ucu52.\n\n<b>Преимущества:</b>\n• Там выходят фулы на 1 день позже чем в стандартный магазин\n• Уникальные товары: аура, фулловые токены и другое", "en": "💎 <b>How to get unique tokens?</b>\n\nYou can only buy them: <b>15 Stars = 1 unique token.</b>\n\n<b>How to buy?</b> Send any gift worth 15 Stars to @ucu52.\n\n<b>Benefits:</b>\n• Items appear 1 day later than in the regular shop\n• Unique items: aura, full tokens and more"},
    "howto_ftokens_text":{"ru": "📦 <b>Как получить фулловые токены?</b>\n\nИх можно купить в <b>💎 Уникальном магазине</b> за уникальные токены (или получить от администратора).\n\n<b>Преимущество:</b>\nПозволяет забрать абсолютно любой фулл в обычном магазине <b>бесплатно</b> — просто используй фулловый токен при покупке!", "en": "📦 <b>How to get full tokens?</b>\n\nYou can buy them in <b>💎 Unique Shop</b> for unique tokens (or receive them from admin).\n\n<b>Benefit:</b>\nLets you grab any item in the regular shop <b>for free</b> — just use a full token at checkout!"},
}


def t(key: str, lang: str = "ru", **kwargs) -> str:
  """Возвращает перевод строки. Подставляет kwargs через .format()."""
  s = STRINGS.get(key, {}).get(lang) or STRINGS.get(key, {}).get("ru", key)
  if kwargs:
    try:
      s = s.format(**kwargs)
    except KeyError:
      pass
  return s


async def get_lang(user_id: int) -> str:
  """Возвращает язык пользователя ('ru' или 'en')."""
  async with connect() as db:
    async with db.execute(
        "SELECT lang FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
  return (row[0] if row and row[0] in ("ru", "en") else "ru")


async def set_lang(user_id: int, lang: str) -> None:
  async with connect() as db:
    await ensure_user(db, user_id)
    await db.execute(
        "UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id)
    )
    await db.commit()


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
            restricted_until TEXT DEFAULT NULL,
            u_premium INTEGER DEFAULT 0,
            u_premium_expires TEXT DEFAULT NULL,
            lang TEXT DEFAULT 'ru'
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
            token_buyers INTEGER DEFAULT 0,
            no_sub INTEGER DEFAULT 0
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
            wins INTEGER DEFAULT 0,
            session_start INTEGER DEFAULT 0,
            session_last_bet INTEGER DEFAULT 0
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
            "u_premium": "INTEGER DEFAULT 0",
            "u_premium_expires": "TEXT DEFAULT NULL",
            "lang": "TEXT DEFAULT 'ru'",
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
            "no_sub": "INTEGER DEFAULT 0",
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
            "session_start": "INTEGER DEFAULT 0",
            "session_last_bet": "INTEGER DEFAULT 0",
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
  ref_lang = await get_lang(referrer_id)
  await safe_send(
      bot,
      referrer_id,
      t("ref_new_user", ref_lang, n=REF_BONUS),
  )


def sub_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("sub_btn_subscribe", lang), url=CHANNEL_URL)],
          [InlineKeyboardButton(text=t("sub_btn_check", lang), callback_data="check_sub")],
      ]
  )


def _sub_text(lang: str = "ru") -> str:
  return t("sub_required", lang)


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

    # Пытаемся определить язык (если пользователь уже есть в БД)
    try:
      _lang = await get_lang(user.id)
    except Exception:
      _lang = "ru"
    sub_msg = _sub_text(_lang)
    if isinstance(event, CallbackQuery):
      if event.message:
        await event.message.answer(sub_msg, reply_markup=sub_keyboard(_lang))
      await event.answer()
    else:
      await event.answer(sub_msg, reply_markup=sub_keyboard(_lang))
    return None


router.message.outer_middleware(SubscribeMiddleware())
router.callback_query.outer_middleware(SubscribeMiddleware())


def get_main_keyboard(user_id, lang: str = "ru"):
  def b(key): return KeyboardButton(text=t(key, lang))
  kb = [
      [b("btn_profile"),  b("btn_shop")],
      [b("btn_ushop")],
      [b("btn_refs"),     b("btn_top")],
      [b("btn_howto"),    b("btn_subs")],
      [b("btn_exchange"), b("btn_lottery")],
      [b("btn_roulette")],
  ]
  if user_id == ADMIN_ID:
    kb.append([b("btn_admin")])
  return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# Все возможные тексты кнопок меню (ru + en) — для диспатча в menu_router
_ALL_MENU_TEXTS: set[str] = {t(k, lg) for k in (
    "btn_profile","btn_shop","btn_ushop","btn_refs","btn_top",
    "btn_howto","btn_subs","btn_exchange","btn_lottery","btn_roulette","btn_admin",
) for lg in ("ru","en")} | {"🛍 Магазин"}


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


def u_premium_active(u_prem, expires_str) -> bool:
  if u_prem != 1:
    return False
  exp = parse_dt(expires_str)
  return exp is None or exp > datetime.datetime.now()


async def grant_u_premium(target_id: int, days: int) -> datetime.datetime:
  """Выдаёт/продлевает уникальную подписку."""
  now = datetime.datetime.now()
  async with connect() as db:
    await ensure_user(db, target_id)
    async with db.execute(
        "SELECT u_premium, u_premium_expires FROM users WHERE user_id = ?",
        (target_id,),
    ) as cur:
      row = await cur.fetchone()
    base = now
    if row and row[0] == 1:
      cur_exp = parse_dt(row[1])
      if cur_exp and cur_exp > now:
        base = cur_exp
    new_expire = base + datetime.timedelta(days=days)
    await db.execute(
        "UPDATE users SET u_premium = 1, u_premium_expires = ? WHERE user_id = ?",
        (new_expire.strftime(DT_FORMAT), target_id),
    )
    await db.commit()
  return new_expire


async def apply_promocode(user_id: int, code_text: str) -> str:
  code_text = (code_text or "").strip()
  if not code_text:
    return t("promo_empty", await get_lang(user_id))
  try:
    async with connect() as db:
      async with db.execute(
          "SELECT code, reward_type, reward_value, max_uses, used_count"
          " FROM promocodes WHERE LOWER(code) = LOWER(?)",
          (code_text,),
      ) as cur:
        promo = await cur.fetchone()
      if not promo:
        return t("promo_not_found", await get_lang(user_id))

      real_code, reward_type, reward_value, _max_uses, _used = promo
      try:
        val = int(reward_value)
      except (TypeError, ValueError):
        return t("promo_bad_reward", await get_lang(user_id))
      if reward_type not in ("tokens", "premium"):
        return t("promo_bad_reward", await get_lang(user_id))

      lang = await get_lang(user_id)
      await ensure_user(db, user_id)
      try:
        await db.execute(
            "INSERT INTO promo_activations (user_id, code) VALUES (?, ?)",
            (user_id, real_code),
        )
      except aiosqlite.IntegrityError:
        return t("promo_used", lang)

      cur = await db.execute(
          "UPDATE promocodes SET used_count = COALESCE(used_count, 0) + 1"
          " WHERE code = ? AND (COALESCE(max_uses, 0) = 0 OR"
          " COALESCE(used_count, 0) < max_uses)",
          (real_code,),
      )
      if cur.rowcount == 0:
        await db.rollback()
        return t("promo_limit", lang)

      if reward_type == "tokens":
        await db.execute(
            "UPDATE users SET tokens = COALESCE(tokens, 0) + ? WHERE"
            " user_id = ?",
            (val, user_id),
        )
        reply = t("promo_ok_tokens", lang, n=val)
      else:
        new_expire = await apply_premium(db, user_id, val)
        reply = t("promo_ok_premium", lang, n=val, date=new_expire.strftime('%d.%m.%Y'))
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

  if existing_user is None:
    # Новый пользователь — предлагаем выбрать язык
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang_en"),
            ]
        ]
    )
    await message.answer(
        t("choose_lang", "ru"),  # двуязычная фраза уже в словаре
        reply_markup=kb,
    )
  else:
    lang = await get_lang(user_id)
    await message.answer(
        t("welcome", lang),
        reply_markup=get_main_keyboard(user_id, lang),
    )


@router.callback_query(F.data.in_({"setlang_ru", "setlang_en"}))
async def setlang_cb(callback: CallbackQuery):
  user_id = callback.from_user.id
  lang = callback.data.split("_")[1]
  await set_lang(user_id, lang)
  try:
    await callback.message.delete()
  except Exception:
    pass
  await callback.message.answer(
      t("lang_set_ru" if lang == "ru" else "lang_set_en", lang),
  )
  await callback.message.answer(
      t("welcome", lang),
      reply_markup=get_main_keyboard(user_id, lang),
  )
  await callback.answer()


@router.message(Command("lang"))
async def cmd_lang(message: Message):
  """Позволяет сменить язык в любой момент."""
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang_ru"),
              InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang_en"),
          ]
      ]
  )
  await message.answer(t("choose_lang", "ru"), reply_markup=kb)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
  await state.clear()
  lang = await get_lang(message.from_user.id)
  await message.answer(
      t("cancelled", lang),
      reply_markup=get_main_keyboard(message.from_user.id, lang),
  )


@router.message(Command("promocode"))
async def cmd_promocode(
    message: Message, command: CommandObject, state: FSMContext
):
  await state.clear()
  user_id = message.from_user.id
  code_text = (command.args or "").strip()
  lang = await get_lang(user_id)
  if code_text:
    reply = await apply_promocode(user_id, code_text)
    await message.answer(reply, reply_markup=get_main_keyboard(user_id, lang))
    return
  await message.answer(t("promo_ask", lang))
  await state.set_state(PromocodeState.code)


@router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery, bot: Bot):
  uid = callback.from_user.id
  lang = await get_lang(uid)
  if await is_subscribed(bot, uid):
    await activate_referral(bot, uid)
    try:
      await callback.message.delete()
    except Exception:
      pass
    await callback.message.answer(
        t("sub_thanks", lang),
        reply_markup=get_main_keyboard(uid, lang),
    )
    await callback.answer()
  else:
    await callback.answer(t("sub_not_yet", lang), show_alert=True)


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


@router.message(Command("upremium"))
async def cmd_upremium(message: Message, command: CommandObject, bot: Bot):
  """Выдать/продлить уникальную подписку. /upremium [дни] [ID]"""
  if await deny(message):
    return
  args = [a for a in (command.args or "").split() if a.lower() != "add"]
  if len(args) != 2 or not all(a.isdigit() for a in args) or int(args[0]) <= 0:
    await message.answer(
        "ℹ️ Использование: <code>/upremium [дни] [ID]</code>\n"
        "Пример: <code>/upremium 30 5681420389</code>"
    )
    return
  days, target_id = int(args[0]), int(args[1])
  try:
    new_expire = await grant_u_premium(target_id, days)
  except Exception as e:
    logging.exception("Ошибка выдачи уник. подписки")
    await message.answer(f"❌ Ошибка: {esc(e)}")
    return
  formatted = new_expire.strftime("%d.%m.%Y")
  await message.answer(
      f"✅ Уникальная подписка выдана <code>{target_id}</code> на {days} дн."
      f" (до {formatted})."
  )
  await safe_send(
      bot,
      target_id,
      f"💎 <b>Вам активирована Уникальная подписка на {days} дней!</b>\n"
      f"📅 До <b>{formatted}</b>.\n\n"
      "Теперь любые товары в 💎 Уникальном магазине доступны бесплатно!",
  )


@router.message(Command("uunpremium"))
async def cmd_uunpremium(message: Message, command: CommandObject):
  """Снять уникальную подписку. /uunpremium [ID]"""
  if await deny(message):
    return
  args = parse_args(command, 1)
  if not args:
    await message.answer("ℹ️ Использование: <code>/uunpremium [ID]</code>")
    return
  async with connect() as db:
    await db.execute(
        "UPDATE users SET u_premium = 0, u_premium_expires = NULL WHERE user_id = ?",
        (args[0],),
    )
    await db.commit()
  await message.answer(f"✅ Уникальная подписка у <code>{args[0]}</code> снята.")


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


def tops_menu_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("top_btn_refs", lang), callback_data="top_refs")],
          [InlineKeyboardButton(text=t("top_btn_lottery", lang), callback_data="top_lottery")],
          [InlineKeyboardButton(text=t("top_btn_roulette", lang), callback_data="top_roulette")],
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
  lang = await get_lang(callback.from_user.id)
  async with connect() as db:
    next_reset = await get_next_reset(db)
    if top_type == "refs":
      async with db.execute(REF_TOP_SQL, (10,)) as cursor:
        rows = await cursor.fetchall()
      title = t("top_refs_title", lang)
      unit = t("top_unit_refs", lang)
    elif top_type == "lottery":
      async with db.execute(
          "SELECT user_id, is_premium, COALESCE(lottery_wins, 0) AS lw FROM"
          " users WHERE lw > 0 ORDER BY lw DESC LIMIT 10"
      ) as cursor:
        rows = await cursor.fetchall()
      title = t("top_lot_title", lang)
      unit = t("top_unit_lot", lang)
    else:
      async with db.execute(
          "SELECT u.user_id, u.is_premium, COALESCE(r.wins, 0) AS w FROM users"
          " u JOIN roulette_state r ON u.user_id = r.user_id WHERE w > 0"
          " ORDER BY w DESC LIMIT 10"
      ) as cursor:
        rows = await cursor.fetchall()
      title = t("top_rl_title", lang)
      unit = t("top_unit_rl", lang)

  user_word = "Пользователь" if lang == "ru" else "User"
  items = [
      f"{i}. {user_word} <code>{r[0]}</code>{' 👑' if r[1] == 1 else ''} —"
      f" <b>{r[2]}</b> {unit}"
      for i, r in enumerate(rows, 1)
  ]
  delta = next_reset - datetime.datetime.now()
  if delta.total_seconds() > 0:
    timer_text = t("top_reset_timer", lang, d=delta.days, h=delta.seconds // 3600)
  else:
    timer_text = t("top_reset_soon", lang)
  footer = timer_text + "\n" + t("top_reward_txt", lang,
                                  pd=TOP_REWARD_PREMIUM_DAYS, yt=TOP_REWARD_YTOKENS)
  text = title + "\n\n" + ("\n".join(items) if items else t("top_empty", lang)) + f"\n\n{footer}"
  kb = InlineKeyboardMarkup(
      inline_keyboard=[[InlineKeyboardButton(
          text=t("top_btn_back", lang), callback_data="back_to_tops"
      )]]
  )
  try:
    await callback.message.edit_text(text, reply_markup=kb)
  except Exception:
    pass
  await callback.answer()


@router.callback_query(F.data == "back_to_tops")
async def back_to_tops(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  try:
    await callback.message.edit_text(
        t("top_menu", lang), reply_markup=tops_menu_keyboard(lang)
    )
  except Exception:
    pass
  await callback.answer()


# --- ПОДПИСКИ ---

@router.callback_query(F.data == "sub_premium")
async def sub_premium_cb(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("sub_btn_buy_stars_50", lang), url="https://t.me/ucu52")],
          [InlineKeyboardButton(text=t("sub_btn_back", lang), callback_data="sub_back")],
      ]
  )
  text = t("sub_prem_text", lang)
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "sub_unique")
async def sub_unique_cb(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  keyboard = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("sub_btn_buy_stars_100", lang), url="https://t.me/ucu52")],
          [InlineKeyboardButton(text=t("sub_btn_back", lang), callback_data="sub_back")],
      ]
  )
  text = t("sub_unique_text", lang)
  try:
    await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
  except Exception:
    await callback.message.answer(text, reply_markup=keyboard, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "sub_back")
async def sub_back_cb(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("sub_btn_premium", lang), callback_data="sub_premium")],
          [InlineKeyboardButton(text=t("sub_btn_unique", lang), callback_data="sub_unique")],
      ]
  )
  try:
    await callback.message.edit_text(t("subs_menu", lang), reply_markup=kb)
  except Exception:
    await callback.message.answer(t("subs_menu", lang), reply_markup=kb)
  await callback.answer()


# --- КАК ПОЛУЧИТЬ ТОКЕНЫ ---


_HOWTO_KEY_MAP = {
    "howto_tokens": "howto_tokens_text",
    "howto_ytokens": "howto_ytokens_text",
    "howto_ftokens": "howto_ftokens_text",
}


@router.callback_query(F.data.in_({"howto_tokens", "howto_ytokens", "howto_ftokens"}))
async def howto_cb(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("howto_btn_back", lang), callback_data="howto_back")]
      ]
  )
  text = t(_HOWTO_KEY_MAP[callback.data], lang)
  try:
    await callback.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
  except Exception:
    await callback.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "howto_back")
async def howto_back(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("howto_btn_tokens", lang), callback_data="howto_tokens")],
          [InlineKeyboardButton(text=t("howto_btn_ytokens", lang), callback_data="howto_ytokens")],
          [InlineKeyboardButton(text=t("howto_btn_ftokens", lang), callback_data="howto_ftokens")],
      ]
  )
  try:
    await callback.message.edit_text(t("howto_menu", lang), reply_markup=kb)
  except Exception:
    await callback.message.answer(t("howto_menu", lang), reply_markup=kb)
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


def lottery_again_keyboard(user_notify: int = 1, lang: str = "ru") -> InlineKeyboardMarkup:
  toggle_key = "lottery_btn_notif_off" if user_notify == 1 else "lottery_btn_notif_on"
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(
              text=t("lottery_btn_again", lang), callback_data="lottery_open"
          )],
          [InlineKeyboardButton(
              text=t(toggle_key, lang), callback_data="lottery_toggle_notify"
          )],
      ]
  )


async def build_lottery_view(user_id: int, lang: str = "ru"):
  """Возвращает (текст, клавиатура) для экрана лотереи."""
  rnd = await get_current_round()
  if not rnd:
    return t("lottery_soon", lang), None

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

  rand_winner = ("Победитель выбирается случайно среди всех участников. "
                 if lang == "ru" else
                 "Winner is chosen randomly among all participants. ")
  text = (
      f"{t('lottery_title', lang)}\n\n"
      + t("lottery_prize", lang, total=st['total'], base=prize, pool=st['pool']) + "\n"
      + t("lottery_cost", lang, cost=cost_text(cost))
      + ("" if fixed else t("lottery_cost_auto", lang)) + "\n"
      + t("lottery_players", lang, n=st['players'], min=lottery_min_players(prize)) + "\n"
      + t("lottery_time", lang, h=hours, m=minutes) + "\n\n"
      + rand_winner
      + (t("lottery_desc_paid", lang) if cost else t("lottery_desc_free", lang))
  )

  toggle_key = "lottery_btn_notif_off" if user_notify == 1 else "lottery_btn_notif_on"
  toggle_row = [
      InlineKeyboardButton(text=t(toggle_key, lang), callback_data="lottery_toggle_notify")
  ]

  if joined:
    return (
        text + t("lottery_joined", lang),
        InlineKeyboardMarkup(inline_keyboard=[toggle_row]),
    )

  join_btn_text = (
      t("lottery_btn_free", lang)
      if cost == 0
      else t("lottery_btn_join", lang, cost=cost_text(cost))
  )
  join_row = [InlineKeyboardButton(text=join_btn_text, callback_data=f"lottery_join_{round_id}_{cost}")]
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
        ulang = await get_lang(uid)
        refund_txt = (
            t("lottery_refund", ulang, n=paid_map[uid])
            if paid_map[uid] else ""
        )
        await safe_send(
            bot,
            uid,
            t("lottery_no_players", ulang,
              cur=len(players), min=min_players, refund=refund_txt),
            reply_markup=lottery_again_keyboard(notify_map[uid], ulang),
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
    ulang = await get_lang(uid)
    if uid == winner:
      text = t("lottery_win", ulang, prize=prize)
    else:
      text = t("lottery_lose", ulang)
    await safe_send(
        bot, uid, text, reply_markup=lottery_again_keyboard(notify_map[uid], ulang)
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
  lang = await get_lang(callback.from_user.id)
  text, keyboard = await build_lottery_view(callback.from_user.id, lang)
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

  lang = await get_lang(user_id)
  notif_key = "lottery_notif_off" if new_val == 0 else "lottery_notif_on"
  await callback.answer(t(notif_key, lang), show_alert=True)
  try:
    text, kb = await build_lottery_view(user_id, lang)
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

    lang = await get_lang(user_id)
    if not row or row[5] == 1:
      await callback.answer(t("lottery_ended", lang), show_alert=True)
      return
    rnd = row[:5]
    ends_at = parse_dt(rnd[3]) or datetime.datetime.now()
    if ends_at <= datetime.datetime.now():
      await callback.answer(t("lottery_ending", lang), show_alert=True)
      return

    async with db.execute(
        "SELECT 1 FROM lottery_participants WHERE round_id = ? AND user_id = ?",
        (round_id, user_id),
    ) as cur:
      if await cur.fetchone():
        await callback.answer(t("lottery_already", lang), show_alert=True)
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
            t("lottery_no_tokens", lang, n=cost), show_alert=True
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
        t("lottery_price_changed", lang, cost=cost_text(cost)),
        show_alert=True,
    )
    try:
      text, kb = await build_lottery_view(user_id, lang)
      await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
      pass
    return

  in_word = "🎟 Ты в игре! Удачи!" if lang == "ru" else "🎟 You're in! Good luck!"
  await callback.answer(in_word, show_alert=True)
  charged_txt = t("lottery_charged", lang, n=cost) if cost else t("lottery_free_entry", lang)
  await callback.message.answer(t("lottery_joined_msg", lang, charged=charged_txt))
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
      _lang_del = await get_lang(callback.from_user.id)
      await callback.answer(t("shop_deleted", _lang_del), show_alert=True)
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
      lang = await get_lang(user_id)
      async with db.execute(
          "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
      ) as c:
        brow = await c.fetchone()
      await callback.answer(
          t("shop_no_tokens", lang, price=final_price, bal=brow[0] if brow else 0)[:200],
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

  lang = await get_lang(user_id)
  if ref_bonus > 0:
    ref_lang = await get_lang(referrer_id)
    await safe_send(
        bot,
        referrer_id,
        t("ref_bonus_msg", ref_lang, n=ref_bonus),
    )

  if buyers_count == 0:
    text = t("shop_bought_first", lang, title=esc(title),
             price=final_price, bal=new_tokens, link=hidden_link(link))
  else:
    text = t("shop_bought", lang, title=esc(title),
             price=final_price, bal=new_tokens, link=hidden_link(link))
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
    _lang_nd = await get_lang(callback.from_user.id)
    await callback.answer(t("shop_deleted", _lang_nd), show_alert=True)
    return
  title, price, buyers_count, description, photo_id, token_buyers = row
  final_price = max(1, int(parse_price(price) * 0.2)) if buyers_count == 0 else parse_price(price)
  first_buy = buyers_count == 0

  lang = await get_lang(callback.from_user.id)
  cap = f"📁 <b>{esc(title)}</b>\n"
  if first_buy:
    cap += t("shop_desc_price_first", lang, price=price, final=final_price) + "\n"
  else:
    cap += t("shop_desc_price", lang, price=price) + "\n"
  cap += t("shop_desc_bought", lang, n=token_buyers) + "\n"
  if description:
    cap += f"\n{description}"

  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(text=t("shop_btn_buy", lang), callback_data=f"buy_{p_id}")],
          [InlineKeyboardButton(text=t("shop_btn_back", lang), callback_data="back_to_shop")],
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
  lang = await get_lang(callback.from_user.id)
  for p_id, title, price, buyers_count, token_buyers in products:
    if buyers_count == 0:
      btn_text = t("shop_first_buy", lang, title=title, price=price)
    else:
      btn_text = t("shop_item", lang, title=title, price=price)
    keyboard_buttons.append(
        [InlineKeyboardButton(text=btn_text[:60], callback_data=f"desc_{p_id}")]
    )
  try:
    await callback.message.edit_text(
        t("shop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )
  except Exception:
    await callback.message.answer(
        t("shop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )
  await callback.answer()


@router.callback_query(F.data.startswith("buy_"))
async def process_buy(callback: CallbackQuery, bot: Bot):
  user_id = callback.from_user.id
  lang = await get_lang(user_id)
  if await get_restriction(user_id):
    await callback.answer(t("restricted_alert", lang)[:200], show_alert=True)
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
      lang = await get_lang(user_id)
      await callback.message.answer(
          t("shop_prem_free", lang, title=esc(title), link=hidden_link(link)),
          disable_web_page_preview=True,
      )
      await callback.answer()
      return

  if full_tokens > 0:
    final_price = await shop_price(user_id, parse_price(price_raw), buyers_count)
    lang = await get_lang(user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=t("shop_btn_usefull", lang),
                callback_data=f"usefull_{p_id}",
            )],
            [InlineKeyboardButton(
                text=t("shop_btn_usenorm", lang, price=final_price),
                callback_data=f"usenorm_{p_id}",
            )],
        ]
    )
    await callback.message.answer(
        t("shop_fulltoken_ask", lang, price=final_price),
        reply_markup=kb,
    )
    await callback.answer()
    return

  await pay_with_tokens(callback, bot, p_id)


@router.callback_query(F.data.startswith("usenorm_"))
async def process_buy_norm(callback: CallbackQuery, bot: Bot):
  _lang_norm = await get_lang(callback.from_user.id)
  if await get_restriction(callback.from_user.id):
    await callback.answer(t("restricted_alert", _lang_norm)[:200], show_alert=True)
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
  user_id = callback.from_user.id
  lang_res = await get_lang(user_id)
  if await get_restriction(user_id):
    await callback.answer(t("restricted_alert", lang_res)[:200], show_alert=True)
    return
  p_id = int(callback.data.split("_")[1])

  async with connect() as db:
    async with db.execute(
        "SELECT title, link FROM products WHERE id = ?", (p_id,)
    ) as cur:
      prod = await cur.fetchone()
    if not prod:
      lang_res = await get_lang(user_id)
      await callback.answer(t("shop_deleted", lang_res), show_alert=True)
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
      lang = await get_lang(user_id)
      await callback.answer(t("shop_fulltoken_gone", lang), show_alert=True)
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
  lang = await get_lang(user_id)
  await callback.message.answer(
      t("shop_fulltoken_used", lang, title=esc(title), link=hidden_link(link)),
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
  user_id = callback.from_user.id

  async with connect() as db:
    async with db.execute(
        "SELECT title, price, COALESCE(token_price,0), amount, description,"
        " photo_id, COALESCE(token_buyers,0), COALESCE(no_sub,0)"
        " FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      row = await cur.fetchone()
    async with db.execute(
        "SELECT COALESCE(u_premium,0), u_premium_expires FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      urow = await cur.fetchone()

  if not row or row[3] <= 0:
    _lang_us = await get_lang(callback.from_user.id)
    await callback.answer(t("ushop_gone", _lang_us), show_alert=True)
    return
  title, y_price, t_price, amount, description, photo_id, token_buyers, no_sub = row
  has_u_prem = urow and u_premium_active(urow[0], urow[1]) if urow else False

  lang = await get_lang(user_id)
  cap = f"💎 <b>{esc(title)}</b>\n"
  if y_price > 0:
    yp_lbl = "💎 Цена:" if lang == "ru" else "💎 Price:"
    yp_unit = "уникальных токенов" if lang == "ru" else "unique tokens"
    cap += f"{yp_lbl} <b>{y_price} {yp_unit}</b>\n"
  if t_price > 0:
    tp_lbl = "🪙 Или:" if lang == "ru" else "🪙 Or:"
    tp_unit = "обычных токенов" if lang == "ru" else "regular tokens"
    cap += f"{tp_lbl} <b>{t_price} {tp_unit}</b>\n"
  if no_sub:
    cap += t("ushop_no_sub", lang) + "\n"
  cap += t("ushop_amount", lang, n=amount) + "\n"
  cap += t("ushop_bought", lang, n=token_buyers) + "\n"
  if description:
    cap += f"\n{description}"

  buttons = []
  if has_u_prem and not no_sub:
    buttons.append([InlineKeyboardButton(
        text=t("ushop_btn_sub", lang),
        callback_data=f"usubget_{up_id}",
    )])
  if y_price > 0:
    buttons.append([InlineKeyboardButton(
        text=t("ushop_btn_ytokens", lang, n=y_price),
        callback_data=f"ubuy_{up_id}",
    )])
  if t_price > 0:
    buttons.append([InlineKeyboardButton(
        text=t("ushop_btn_tokens", lang, n=t_price),
        callback_data=f"utbuy_{up_id}",
    )])
  buttons.append([InlineKeyboardButton(text=t("shop_btn_back", lang), callback_data="back_to_ushop")])
  kb = InlineKeyboardMarkup(inline_keyboard=buttons)

  if photo_id:
    await callback.message.answer_photo(photo_id, caption=cap, reply_markup=kb)
  else:
    await callback.message.answer(cap, reply_markup=kb, disable_web_page_preview=True)
  await callback.answer()


@router.callback_query(F.data == "back_to_ushop")
async def back_to_ushop(callback: CallbackQuery):
  lang = await get_lang(callback.from_user.id)
  async with connect() as db:
    async with db.execute(
        "SELECT id, title, price, COALESCE(token_price,0), amount,"
        " COALESCE(token_buyers,0) FROM unique_products WHERE amount > 0"
    ) as cursor:
      u_products = await cursor.fetchall()
  if not u_products:
    await callback.answer(t("ushop_empty", lang), show_alert=True)
    return
  stock_word = "ост." if lang == "ru" else "left"
  kb_buttons = []
  for up_id, title, price, token_price, amount, token_buyers in u_products:
    btn_text = (f"💎 {title} ({stock_word}: {amount})")[:60]
    kb_buttons.append(
        [InlineKeyboardButton(text=btn_text, callback_data=f"udesc_{up_id}")]
    )
  try:
    await callback.message.edit_text(
        t("ushop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )
  except Exception:
    await callback.message.answer(
        t("ushop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )
  await callback.answer()


@router.callback_query(F.data.startswith("usubget_"))
async def process_usubget(callback: CallbackQuery):
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])
  lang = await get_lang(user_id)

  async with connect() as db:
    async with db.execute(
        "SELECT COALESCE(u_premium,0), u_premium_expires FROM users WHERE user_id = ?",
        (user_id,),
    ) as cur:
      urow = await cur.fetchone()
    if not urow or not u_premium_active(urow[0], urow[1]):
      await callback.answer(t("ushop_no_uprem", lang), show_alert=True)
      return

    async with db.execute(
        "SELECT title, amount, command, COALESCE(no_sub,0)"
        " FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[1] <= 0:
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return
    title, _amount, command_str, no_sub = prod
    if no_sub:
      await callback.answer(t("ushop_no_sub_err", lang), show_alert=True)
      return

    parsed = parse_unique_command(command_str)
    if parsed is None:
      await callback.answer(t("ushop_bad_cmd", lang), show_alert=True)
      return

    cur = await db.execute(
        "UPDATE unique_products SET amount = amount - 1 WHERE id = ? AND amount > 0",
        (up_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return
    await db.commit()

  try:
    await apply_unique_command(user_id, title, parsed)
  except Exception:
    logging.exception("Ошибка выдачи уник. товара (по подписке)")
    await callback.message.answer(t("ushop_error", lang))
    await callback.answer()
    return

  await callback.message.answer(t("ushop_bought_sub", lang, title=esc(title)))
  await callback.answer()


@router.callback_query(F.data.startswith("utbuy_"))
async def process_utbuy(callback: CallbackQuery):
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])
  lang = await get_lang(user_id)

  async with connect() as db:
    async with db.execute(
        "SELECT title, COALESCE(token_price,0), amount, command"
        " FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[2] <= 0:
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return
    title, tp, _amount, command_str = prod
    if tp <= 0:
      not_avail = "❌ Этот товар нельзя купить за обычные токены." if lang == "ru" else "❌ This item cannot be purchased with regular tokens."
      await callback.answer(not_avail, show_alert=True)
      return

    parsed = parse_unique_command(command_str)
    if parsed is None:
      await callback.answer(t("ushop_bad_cmd", lang), show_alert=True)
      return

    cur = await db.execute(
        "UPDATE unique_products SET amount = amount - 1 WHERE id = ? AND amount > 0",
        (up_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return

    await ensure_user(db, user_id)
    cur = await db.execute(
        "UPDATE users SET tokens = tokens - ? WHERE user_id = ?"
        " AND COALESCE(tokens, 0) >= ?",
        (tp, user_id, tp),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(t("ushop_no_tokens_t", lang, n=tp), show_alert=True)
      return

    await db.execute(
        "UPDATE unique_products SET token_buyers = COALESCE(token_buyers,0) + 1 WHERE id = ?",
        (up_id,),
    )
    await db.commit()

  try:
    await apply_unique_command(user_id, title, parsed)
  except Exception:
    logging.exception("Ошибка выдачи уник. товара (за токены)")
    await callback.message.answer(t("ushop_error", lang))
    await callback.answer()
    return

  await callback.message.answer(t("ushop_bought_t", lang, title=esc(title), n=tp))
  await callback.answer()


@router.callback_query(F.data.startswith("ubuy_"))
async def process_ubuy(callback: CallbackQuery):
  user_id = callback.from_user.id
  up_id = int(callback.data.split("_")[1])
  lang = await get_lang(user_id)

  async with connect() as db:
    async with db.execute(
        "SELECT title, price, amount, command FROM unique_products WHERE id = ?",
        (up_id,),
    ) as cur:
      prod = await cur.fetchone()
    if not prod or prod[2] <= 0:
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return
    title, price, _amount, command_str = prod

    parsed = parse_unique_command(command_str)
    if parsed is None:
      await callback.answer(t("ushop_bad_cmd", lang), show_alert=True)
      return

    cur = await db.execute(
        "UPDATE unique_products SET amount = amount - 1 WHERE id = ? AND amount > 0",
        (up_id,),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(t("ushop_gone", lang), show_alert=True)
      return

    await ensure_user(db, user_id)
    cur = await db.execute(
        "UPDATE users SET y_tokens = y_tokens - ? WHERE user_id = ?"
        " AND COALESCE(y_tokens, 0) >= ?",
        (price, user_id, price),
    )
    if cur.rowcount == 0:
      await db.rollback()
      await callback.answer(t("ushop_no_tokens_y", lang, n=price), show_alert=True)
      return
    await db.execute(
        "UPDATE unique_products SET token_buyers = COALESCE(token_buyers,0) + 1 WHERE id = ?",
        (up_id,),
    )
    await db.commit()

  try:
    await apply_unique_command(user_id, title, parsed)
  except Exception:
    logging.exception("Ошибка выдачи товара уник. магазина")
    await callback.message.answer(t("ushop_error", lang))
    await callback.answer()
    return

  await callback.message.answer(t("ushop_bought_y", lang, title=esc(title), n=price))
  await callback.answer()


# --- ГЛАВНОЕ МЕНЮ ---


@router.message(F.text.func(lambda txt: txt in _ALL_MENU_TEXTS))
async def menu_router(message: Message, bot: Bot, state: FSMContext):
  await state.clear()
  user_id = message.from_user.id
  text = message.text
  lang = await get_lang(user_id)

  if text == t("btn_profile", lang):
    async with connect() as db:
      await ensure_user(db, user_id)
      await db.commit()
      async with db.execute(
          "SELECT COALESCE(tokens, 0), COALESCE(y_tokens, 0),"
          " COALESCE(full_tokens, 0), is_premium, COALESCE(bought_folders, 0),"
          " premium_expires, COALESCE(warns, 0), COALESCE(exchanges, 0),"
          " COALESCE(u_premium, 0), u_premium_expires"
          " FROM users WHERE user_id = ?",
          (user_id,),
      ) as cursor:
        (tokens, y_tokens, full_tokens, is_prem, bought, prem_expires, warns,
         exchanges, u_prem, u_prem_expires) = await cursor.fetchone()

    prem_status = t("profile_prem_no", lang)
    if is_prem == 1:
      if premium_active(is_prem, prem_expires):
        exp_date = parse_dt(prem_expires)
        if exp_date:
          days_left = (exp_date - datetime.datetime.now()).days
          prem_status = t("profile_prem_active", lang, date=exp_date.strftime('%d.%m.%Y'), days=days_left)
        else:
          prem_status = "✅ " + ("Активна" if lang == "ru" else "Active")
      else:
        await remove_premium(user_id)
        prem_status = t("profile_prem_expired", lang)

    # Уникальная подписка
    u_prem_status = t("profile_prem_no", lang)
    if u_premium_active(u_prem, u_prem_expires):
      u_exp = parse_dt(u_prem_expires)
      if u_exp:
        u_days_left = (u_exp - datetime.datetime.now()).days
        u_prem_status = t("profile_prem_active", lang, date=u_exp.strftime('%d.%m.%Y'), days=u_days_left)
      else:
        u_prem_status = "✅ " + ("Активна" if lang == "ru" else "Active")
    elif u_prem == 1:
      async with connect() as db:
        await db.execute(
            "UPDATE users SET u_premium = 0, u_premium_expires = NULL"
            " WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()

    aura = await get_user_aura(user_id)
    if aura:
      aura_days = (aura["expires"] - datetime.datetime.now()).days
      aura_status = t("profile_aura_active", lang,
                      name=esc(aura['name']), pct=aura['percent'], days=aura_days)
    else:
      aura_status = t("profile_aura_no", lang)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text=t("btn_promo", lang), callback_data="enter_promocode"
        )]]
    )
    units = "шт." if lang == "ru" else "pcs."
    await message.answer(
        f"{t('profile_title', lang)}\n\n"
        f"{t('profile_id', lang)}: <code>{user_id}</code>\n"
        f"{t('profile_tokens', lang)}: {tokens}\n"
        f"{t('profile_ytokens', lang)}: {y_tokens}\n"
        f"{t('profile_ftokens', lang)}: {full_tokens}\n"
        f"{t('profile_premium', lang)}: {prem_status}\n"
        f"{t('profile_upremium', lang)}: {u_prem_status}\n"
        f"{t('profile_aura', lang)}: {aura_status}\n"
        f"{t('profile_bought', lang)}: {bought} {units}\n"
        f"{t('profile_warns', lang)}: {warns}/{MAX_WARNS}\n"
        f"{t('profile_exchanges', lang)}: {exchanges}",
        reply_markup=keyboard,
    )

  elif text in (t("btn_shop", lang), "🛍 Магазин", t("btn_shop", "ru"), t("btn_shop", "en")):
    async with connect() as db:
      async with db.execute(
          "SELECT id, title, price, buyers_count, token_buyers FROM products"
      ) as cursor:
        products = await cursor.fetchall()

    if not products:
      await message.answer(t("shop_empty", lang))
      return

    keyboard_buttons = []
    for p_id, ptitle, price, buyers_count, token_buyers in products:
      if buyers_count == 0:
        btn_text = t("shop_first_buy", lang, title=ptitle, price=price)
      else:
        btn_text = t("shop_item", lang, title=ptitle, price=price)
      keyboard_buttons.append(
          [InlineKeyboardButton(text=btn_text[:60], callback_data=f"desc_{p_id}")]
      )
    await message.answer(
        t("shop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons),
    )

  elif text in (t("btn_ushop", lang), t("btn_ushop", "ru"), t("btn_ushop", "en")):
    async with connect() as db:
      async with db.execute(
          "SELECT id, title, price, COALESCE(token_price,0), amount,"
          " COALESCE(token_buyers,0) FROM unique_products WHERE amount > 0"
      ) as cursor:
        u_products = await cursor.fetchall()

    if not u_products:
      await message.answer(t("ushop_empty", lang))
      return

    stock_word = "ост." if lang == "ru" else "left"
    kb_buttons = []
    for up_id, utitle, price, token_price, amount, token_buyers in u_products:
      btn_text = (f"💎 {utitle} ({stock_word}: {amount})")[:60]
      kb_buttons.append(
          [InlineKeyboardButton(text=btn_text, callback_data=f"udesc_{up_id}")]
      )
    await message.answer(
        t("ushop_title", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
    )

  elif text in (t("btn_refs", lang), t("btn_refs", "ru"), t("btn_refs", "en")):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    await message.answer(
        f"{t('ref_title', lang)}\n\n"
        f"{t('ref_link_txt', lang)}\n<code>{ref_link}</code>\n\n"
        f"{t('ref_bonus_txt', lang, n=REF_BONUS)}\n"
        f"{t('ref_income_txt', lang)}"
    )

  elif text in (t("btn_top", lang), t("btn_top", "ru"), t("btn_top", "en")):
    await message.answer(
        t("top_menu", lang), reply_markup=tops_menu_keyboard(lang)
    )

  elif text in (t("btn_howto", lang), t("btn_howto", "ru"), t("btn_howto", "en")):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("howto_btn_tokens", lang), callback_data="howto_tokens")],
            [InlineKeyboardButton(text=t("howto_btn_ytokens", lang), callback_data="howto_ytokens")],
            [InlineKeyboardButton(text=t("howto_btn_ftokens", lang), callback_data="howto_ftokens")],
        ]
    )
    await message.answer(t("howto_menu", lang), reply_markup=kb)

  elif text in (t("btn_subs", lang), t("btn_subs", "ru"), t("btn_subs", "en")):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("sub_btn_premium", lang), callback_data="sub_premium")],
            [InlineKeyboardButton(text=t("sub_btn_unique", lang), callback_data="sub_unique")],
        ]
    )
    await message.answer(t("subs_menu", lang), reply_markup=kb)

  elif text in (t("btn_lottery", lang), t("btn_lottery", "ru"), t("btn_lottery", "en")):
    lottery_text, lottery_kb = await build_lottery_view(user_id, lang)
    await message.answer(lottery_text, reply_markup=lottery_kb)

  elif text in (t("btn_exchange", lang), t("btn_exchange", "ru"), t("btn_exchange", "en")):
    await message.answer(await ex_menu_text(lang), reply_markup=exchange_menu_kb(lang))

  elif text in (t("btn_roulette", lang), t("btn_roulette", "ru"), t("btn_roulette", "en")):
    await state.set_state(RouletteState.bet)
    await message.answer(await roulette_intro_text(user_id, lang))

  elif text in (t("btn_admin", lang), t("btn_admin", "ru"), t("btn_admin", "en")):
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
  lang = await get_lang(user_id)
  await state.clear()
  reply = await apply_promocode(user_id, code_text)
  await message.answer(reply, reply_markup=get_main_keyboard(user_id, lang))


@router.callback_query(F.data == "enter_promocode")
async def promo_prompt(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  lang = await get_lang(callback.from_user.id)
  await state.set_state(PromocodeState.code)
  await callback.message.answer(t("promo_ask", lang))
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
  await state.clear()
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
  await state.clear()
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
          [InlineKeyboardButton(text="🔒 Переключить «Только токены»", callback_data=f"uef_nosub_toggle_{p_id}")],
      ]
  )
  await callback.message.answer("Что изменить в уникальном товаре?", reply_markup=kb)
  await callback.answer()


@router.callback_query(F.data.startswith("uef_nosub_toggle_"))
async def uef_nosub_toggle(callback: CallbackQuery):
  if callback.from_user.id != ADMIN_ID:
    await callback.answer()
    return
  p_id = int(callback.data.split("_")[3])
  async with connect() as db:
    async with db.execute(
        "SELECT COALESCE(no_sub,0) FROM unique_products WHERE id = ?", (p_id,)
    ) as cur:
      row = await cur.fetchone()
    new_val = 0 if (row[0] if row else 0) else 1
    await db.execute(
        "UPDATE unique_products SET no_sub = ? WHERE id = ?", (new_val, p_id)
    )
    await db.commit()
  status = "🔒 Только за токены (подписка не работает)" if new_val else "✅ Доступно по подписке"
  await callback.answer(f"Статус: {status}", show_alert=True)


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


def exchange_menu_kb(lang: str = "ru") -> InlineKeyboardMarkup:
  return ex_kb([
      [(t("ex_btn_new", lang), "ex:new"), (t("ex_btn_all", lang), "ex:all")],
      [(t("ex_btn_search", lang), "ex:search"), (t("ex_btn_my", lang), "ex:my")],
  ])


async def ex_menu_text(lang: str = "ru") -> str:
  return t("ex_menu_text", lang, n=EXCHANGE_COST)


@router.callback_query(F.data == "ex:menu")
async def ex_menu_cb(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  lang = await get_lang(callback.from_user.id)
  await ex_show(callback, await ex_menu_text(lang), exchange_menu_kb(lang))
  await callback.answer()


@router.callback_query(F.data == "ex:cancel")
async def ex_cancel(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  lang = await get_lang(callback.from_user.id)
  await ex_show(callback, t("cancelled", lang),
                ex_kb([[( t("ex_btn_back", lang), "ex:menu")]]))
  await callback.answer()


@router.callback_query(F.data == "ex:noop")
async def ex_noop(callback: CallbackQuery):
  await callback.answer()


# --- создание обмена ---


@router.callback_query(F.data == "ex:new")
async def ex_new(callback: CallbackQuery, state: FSMContext):
  uid = callback.from_user.id
  lang = await get_lang(uid)
  if await get_restriction(uid):
    await callback.answer(t("restricted_alert", lang)[:200], show_alert=True)
    return
  cnt = await ex_one(
      "SELECT COUNT(*) n FROM ex_offers WHERE owner_id = ? AND status = 'open'",
      (uid,),
  )
  if cnt["n"] >= MAX_OPEN_OFFERS:
    await callback.answer(
        t("ex_too_many", lang, n=MAX_OPEN_OFFERS), show_alert=True,
    )
    return
  await state.set_state(NewOfferState.give)
  await ex_show(
      callback,
      t("ex_give_ask", lang),
      ex_kb([[(t("ex_btn_cancel", lang), "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(NewOfferState.give))
async def ex_new_give(message: Message, state: FSMContext):
  lang = await get_lang(message.from_user.id)
  text = txt(message)[:60]
  if not text:
    await message.answer(t("ex_give_ask", lang))
    return
  await state.update_data(give=text)
  await state.set_state(NewOfferState.want)
  await message.answer(
      t("ex_want_ask", lang),
      reply_markup=ex_kb([[(t("ex_btn_cancel", lang), "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.want))
async def ex_new_want(message: Message, state: FSMContext):
  lang = await get_lang(message.from_user.id)
  text = txt(message)[:60]
  if not text:
    await message.answer(t("ex_want_ask", lang))
    return
  await state.update_data(want=text)
  await state.set_state(NewOfferState.link)
  await message.answer(
      t("ex_link_ask", lang, rule=t("ex_link_rule", lang)),
      reply_markup=ex_kb([[(t("ex_btn_cancel", lang), "ex:cancel")]]),
  )


@router.message(StateFilter(NewOfferState.link))
async def ex_new_link(message: Message, state: FSMContext):
  lang = await get_lang(message.from_user.id)
  link = txt(message)
  if not LINK_RE.match(link):
    await message.answer(t("ex_link_error", lang))
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
      t("ex_created", lang, id=offer_id, give=esc(data['give']), want=esc(data['want'])),
      reply_markup=ex_kb([[(t("btn_exchange", lang), "ex:menu")]]),
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
  lang = await get_lang(callback.from_user.id)
  await state.set_state(ExSearchState.query)
  await ex_show(
      callback,
      t("ex_search_ask", lang),
      ex_kb([[(t("ex_btn_cancel", lang), "ex:cancel")]]),
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
  lang = await get_lang(uid)
  if not o or o["status"] not in ("open", "taken"):
    await callback.answer(t("ex_unavail", lang), show_alert=True)
    return

  report_kb = ex_kb([
      [(t("ex_btn_complain", lang), f"ex:confirm_rep:{offer_id}")],
      [(t("ex_btn_back", lang), "ex:menu")],
  ])
  if o["status"] == "open":
    text = t("ex_view_open", lang, id=o['id'], give=esc(o['give']),
             want=esc(o['want']), n=EXCHANGE_COST)
    if admin:
      admin_lbl = "🛠 <b>Admin:</b>" if lang == "en" else "🛠 <b>Админ:</b>"
      text += (
          f"\n\n{admin_lbl}\n"
          f"🆔 ID: <code>{o['owner_id']}</code>\n"
          f"🔗 Link:\n{esc(o['owner_link'])}"
      )
    if o["owner_id"] == uid or admin:
      kb = ex_kb([
          [(t("ex_btn_delete", lang), f"ex:del:{offer_id}")],
          [("⬅️", "ex:list:0")],
      ])
    else:
      kb = ex_kb([
          [(t("ex_btn_take", lang), f"ex:take:{offer_id}")],
          [("⬅️", "ex:list:0")],
      ])
  elif uid == o["owner_id"]:
    text = t("ex_view_done_own", lang, id=o['id'], uid=o['taker_id'], link=esc(o['taker_link']))
    kb = report_kb
  elif uid == o["taker_id"]:
    text = t("ex_view_done_take", lang, id=o['id'], uid=o['owner_id'], link=esc(o['owner_link']))
    kb = report_kb
  elif admin:
    text = (
        f"✅ <b>Exchange #{o['id']} (done)</b>\n"
        f"{esc(o['give'])} → {esc(o['want'])}\n\n"
        f"👤 Owner: <code>{o['owner_id']}</code>\n{esc(o['owner_link'])}\n\n"
        f"🤝 Taker: <code>{o['taker_id']}</code>\n{esc(o['taker_link'])}"
    )
    kb = ex_kb([[("⬅️", "ex:list:0")]])
  else:
    await callback.answer(t("ex_unavail", lang), show_alert=True)
    return

  await ex_show(callback, text, kb)
  await callback.answer()


@router.callback_query(F.data == "ex:my")
async def ex_my_cb(callback: CallbackQuery, state: FSMContext):
  await state.clear()
  uid = callback.from_user.id
  lang = await get_lang(uid)
  rows = await ex_all(
      "SELECT * FROM ex_offers WHERE (owner_id = ? OR taker_id = ?) AND status"
      " IN ('open', 'taken') ORDER BY id DESC LIMIT 10",
      (uid, uid),
  )
  if not rows:
    await ex_show(
        callback,
        t("ex_my_empty", lang),
        ex_kb([[(t("ex_btn_back", lang), "ex:menu")]]),
    )
    await callback.answer()
    return
  icon = {"open": "🟢", "taken": "✅"}
  buttons = [
      [(f"{icon[r['status']]} #{r['id']} {r['give']} → {r['want']}"[:60],
        f"ex:view:{r['id']}")]
      for r in rows
  ]
  buttons.append([(t("ex_btn_back", lang), "ex:menu")])
  await ex_show(callback, t("ex_my_title", lang), ex_kb(buttons))
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
  lang = await get_lang(callback.from_user.id)
  if not rc:
    await callback.answer(t("ex_unavail", lang), show_alert=True)
    return
  await ex_show(callback, t("ex_deleted", lang), ex_kb([[(t("btn_exchange", lang), "ex:menu")]]))
  await callback.answer()


# --- взять обмен ---


@router.callback_query(F.data.startswith("ex:take:"))
async def ex_take(callback: CallbackQuery, state: FSMContext):
  offer_id = int(callback.data.split(":")[2])
  uid = callback.from_user.id
  lang = await get_lang(uid)
  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  if not o or o["status"] != "open":
    await callback.answer(t("ex_taken", lang), show_alert=True)
    return
  if o["owner_id"] == uid:
    await callback.answer(t("ex_own", lang), show_alert=True)
    return
  if await get_restriction(uid):
    await callback.answer(t("restricted_alert", lang)[:200], show_alert=True)
    return
  bal = await ex_one(
      "SELECT COALESCE(tokens, 0) t FROM users WHERE user_id = ?", (uid,)
  )
  if not bal or bal["t"] < EXCHANGE_COST:
    await callback.answer(t("ex_no_tokens", lang, n=EXCHANGE_COST), show_alert=True)
    return
  await state.set_state(ExTakeState.link)
  await state.update_data(offer_id=offer_id)
  give_txt = (
      f"Exchange #{offer_id}: <b>{esc(o['give'])}</b> → <b>{esc(o['want'])}</b>\n\n"
      if lang == "en" else
      f"Обмен #{offer_id}: <b>{esc(o['give'])}</b> → <b>{esc(o['want'])}</b>\n\n"
  )
  cost_note = (
      f"Send <b>your link</b>. {EXCHANGE_COST} token will be charged.\n"
      if lang == "en" else
      f"Отправь <b>свою ссылку</b>. Спишется {EXCHANGE_COST} токен.\n"
  )
  await ex_show(
      callback,
      give_txt + cost_note + t("ex_link_rule", lang),
      ex_kb([[(t("ex_btn_cancel", lang), "ex:cancel")]]),
  )
  await callback.answer()


@router.message(StateFilter(ExTakeState.link))
async def ex_take_link(message: Message, state: FSMContext, bot: Bot):
  uid = message.from_user.id
  lang = await get_lang(uid)
  link = txt(message)
  if not LINK_RE.match(link):
    await message.answer(t("ex_link_error", lang))
    return
  offer_id = (await state.get_data())["offer_id"]
  await state.clear()
  if await get_restriction(uid):
    await message.answer(t("restricted_text", lang, appeal=t("appeal", lang)))
    return

  now_ts = int(datetime.datetime.now().timestamp())
  rc, _ = await ex_exec(
      "UPDATE ex_offers SET status = 'taken', taker_id = ?, taker_link = ?,"
      " taken = ? WHERE id = ? AND status = 'open' AND owner_id != ?",
      (uid, link, now_ts, offer_id, uid),
  )
  if not rc:
    await message.answer(t("ex_too_late", lang))
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
    await message.answer(t("ex_no_balance", lang, n=EXCHANGE_COST))
    return

  o = await ex_one("SELECT * FROM ex_offers WHERE id = ?", (offer_id,))
  await ex_exec(
      "UPDATE users SET exchanges = COALESCE(exchanges, 0) + 1"
      " WHERE user_id IN (?, ?)",
      (o["owner_id"], uid),
  )
  report_kb = ex_kb([[(t("ex_btn_complain", lang), f"ex:confirm_rep:{offer_id}")]])
  await message.answer(
      t("ex_done_taker", lang, id=offer_id, give=esc(o['give']),
        want=esc(o['want']), owner=o['owner_id'],
        link=esc(o['owner_link']), n=EXCHANGE_COST),
      reply_markup=report_kb,
      disable_web_page_preview=True,
  )
  owner_lang = await get_lang(o["owner_id"])
  owner_report_kb = ex_kb([[(t("ex_btn_complain", owner_lang), f"ex:confirm_rep:{offer_id}")]])
  await safe_send(
      bot,
      o["owner_id"],
      t("ex_notif_owner", owner_lang, uid=uid, id=offer_id,
        give=esc(o['give']), want=esc(o['want']), link=esc(link)),
      reply_markup=owner_report_kb,
      disable_web_page_preview=True,
  )


# --- жалобы ---


@router.callback_query(F.data.startswith("ex:confirm_rep:"))
async def ex_confirm_report(callback: CallbackQuery):
  offer_id = int(callback.data.split(":")[2])
  lang = await get_lang(callback.from_user.id)
  yes_txt = "✅ Да, отправить жалобу" if lang == "ru" else "✅ Yes, file complaint"
  no_txt  = t("ex_btn_cancel", lang)
  kb = ex_kb([
      [(yes_txt, f"ex:rep:{offer_id}")],
      [(no_txt, f"ex:view:{offer_id}")],
  ])
  await ex_show(callback, t("ex_complain_confirm", lang), kb)
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

  lang = await get_lang(uid)
  try:
    _, report_id = await ex_exec(
        "INSERT INTO ex_reports (offer_id, complainer_id, accused_id, created)"
        " VALUES (?, ?, ?, ?)",
        (offer_id, uid, accused, int(datetime.datetime.now().timestamp())),
    )
  except aiosqlite.IntegrityError:
    await callback.answer(t("ex_complain_dup", lang), show_alert=True)
    return

  until = datetime.datetime.now() + datetime.timedelta(hours=RESTRICT_HOURS)
  await ex_exec(
      "UPDATE users SET restricted_until = ? WHERE user_id = ?",
      (until.strftime(DT_FORMAT), accused),
  )
  await callback.answer(t("ex_complain_sent", lang), show_alert=True)
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass
  accused_lang = await get_lang(accused)
  await safe_send(
      bot,
      accused,
      t("ex_restricted_notif", accused_lang, id=offer_id),
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


def roulette_decide(
    current_tokens: int,
    roll_win: bool,
    aura_percent: int,
    session_start: int = 0,
) -> bool:
  """
  session_start — баланс в начале сессии (до первой ставки в этой серии).
  Если игрок вырос с 3 до 10+ токенов — шанс начинает падать.
  Если вырос с 3 до 15+ — падает сильнее.
  """
  # Базовый шанс 55%; аура добавляет/отнимает (clamp 0..1)
  base_chance = max(0.0, min(1.0, 0.55 + aura_percent / 100.0))

  # Скрытый штраф за накопление (порог 50)
  if current_tokens >= 50 and base_chance > 0.30:
    base_chance = 0.30

  # Скрытый штраф за рост в сессии (3→10 или 3→15)
  if session_start > 0:
    growth = current_tokens - session_start
    if growth >= 12:          # вырос на 12+ токенов за сессию
      base_chance = min(base_chance, 0.35)
    elif growth >= 7:          # вырос на 7+ токенов за сессию
      base_chance = min(base_chance, 0.42)

  return random.random() < base_chance


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

      await db.execute(
          "INSERT OR IGNORE INTO roulette_state (user_id) VALUES (?)", (user_id,)
      )
      # Читаем session_start: если давно не играл (> 10 мин) — сбрасываем
      now_ts = int(datetime.datetime.now().timestamp())
      async with db.execute(
          "SELECT COALESCE(session_start,0), COALESCE(session_last_bet,0)"
          " FROM roulette_state WHERE user_id = ?",
          (user_id,),
      ) as c:
        srow = await c.fetchone()
      s_start, s_last = (srow[0], srow[1]) if srow else (0, 0)
      # Сессия сбрасывается если прошло > 10 минут с последней ставки
      if now_ts - s_last > 600 or s_start == 0:
        s_start = current_bal + bet  # баланс до этой ставки
      await db.execute(
          "UPDATE roulette_state SET session_start = ?, session_last_bet = ?"
          " WHERE user_id = ?",
          (s_start, now_ts, user_id),
      )

      win = roulette_decide(current_bal, random.random() < 0.5, aura_percent, s_start)

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


def roulette_result_kb(bet: int, lang: str = "ru") -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
      inline_keyboard=[
          [InlineKeyboardButton(
              text=t("roulette_btn_again", lang, n=bet),
              callback_data=f"rl_again_{bet}",
          )],
          [InlineKeyboardButton(text=t("roulette_btn_new", lang), callback_data="rl_new")],
      ]
  )


async def roulette_intro_text(user_id: int, lang: str = "ru") -> str:
  async with connect() as db:
    async with db.execute(
        "SELECT COALESCE(tokens, 0) FROM users WHERE user_id = ?", (user_id,)
    ) as cur:
      row = await cur.fetchone()
  balance = row[0] if row else 0
  return t("roulette_intro", lang, min=ROULETTE_MIN, max=ROULETTE_MAX, bal=balance)


def roulette_result_text(bet: int, res: dict, lang: str = "ru") -> str:
  if res["win"]:
    return t("roulette_win", lang, bet=bet, bal=res["balance"])
  return t("roulette_lose", lang, bet=bet, bal=res["balance"])


@router.message(StateFilter(RouletteState.bet))
async def roulette_bet(message: Message, state: FSMContext):
  raw = txt(message)
  lang = await get_lang(message.from_user.id)
  if not raw.isdigit() or not ROULETTE_MIN <= int(raw) <= ROULETTE_MAX:
    await message.answer(t("roulette_bad_bet", lang, min=ROULETTE_MIN, max=ROULETTE_MAX))
    return
  bet = int(raw)
  res = await roulette_play(message.from_user.id, bet)
  if "error" in res:
    await message.answer(t("roulette_no_tokens", lang, bal=res["balance"]))
    return
  await state.clear()
  await message.answer(
      roulette_result_text(bet, res, lang), reply_markup=roulette_result_kb(bet, lang)
  )


@router.callback_query(F.data.startswith("rl_again_"))
async def roulette_again(callback: CallbackQuery):
  bet = int(callback.data.split("_")[2])
  lang = await get_lang(callback.from_user.id)
  if not ROULETTE_MIN <= bet <= ROULETTE_MAX:
    await callback.answer()
    return
  res = await roulette_play(callback.from_user.id, bet)
  if "error" in res:
    await callback.answer(
        t("roulette_no_tokens", lang, bal=res["balance"])[:200], show_alert=True
    )
    return
  await callback.answer()
  try:
    await callback.message.edit_reply_markup(reply_markup=None)
  except Exception:
    pass
  await callback.message.answer(
      roulette_result_text(bet, res, lang), reply_markup=roulette_result_kb(bet, lang)
  )


@router.callback_query(F.data == "rl_new")
async def roulette_new(callback: CallbackQuery, state: FSMContext):
  lang = await get_lang(callback.from_user.id)
  await state.set_state(RouletteState.bet)
  await callback.answer()
  await callback.message.answer(await roulette_intro_text(callback.from_user.id, lang))


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
