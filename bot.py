import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)



BOT_TOKEN = ""
ADMIN_IDS = []
SERVER_NAME = "MyServer"
DB_PATH = "subscriptions.db"
PROXY = "socks5://8.220.143.77:1080"

PLANS = {
    "basic": {
        "name": "⚡ Basic",
        "days": 30,
        "stars": 100,
        "emoji": "⚡",
        "desc": "Базовый доступ к серверу",
        "features": ["✅ Доступ к основным каналам", "✅ Базовые привилегии", "✅ Поддержка в чате"],
    },
    "pro": {
        "name": "🔥 Pro",
        "days": 30,
        "stars": 250,
        "emoji": "🔥",
        "desc": "Расширенный доступ со всеми функциями",
        "features": ["✅ Всё из Basic", "✅ VIP-каналы", "✅ Приоритетная поддержка", "✅ Эксклюзивный контент"],
    },
    "premium": {
        "name": "👑 Premium",
        "days": 30,
        "stars": 500,
        "emoji": "👑",
        "desc": "Полный VIP-доступ без ограничений",
        "features": ["✅ Всё из Pro", "✅ Личный менеджер", "✅ Закрытые ивенты", "✅ Кастомная роль"],
    },
}



async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, joined_at TEXT)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, plan_id TEXT, started_at TEXT,
                expires_at TEXT, is_active INTEGER DEFAULT 1, stars_paid INTEGER DEFAULT 0)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, plan_id TEXT, stars_amount INTEGER,
                charge_id TEXT, paid_at TEXT)
        """)
        await db.commit()

async def upsert_user(user_id, username, full_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, joined_at) VALUES (?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name
        """, (user_id, username, full_name, datetime.now().isoformat()))
        await db.commit()

async def get_active_sub(user_id) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND is_active=1 ORDER BY expires_at DESC LIMIT 1",
            (user_id,)
        ) as c:
            row = await c.fetchone()
            if not row:
                return None
            sub = dict(row)
            if datetime.fromisoformat(sub["expires_at"]) < datetime.now():
                await db.execute("UPDATE subscriptions SET is_active=0 WHERE id=?", (sub["id"],))
                await db.commit()
                return None
            return sub

async def get_sub_history(user_id) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM subscriptions WHERE user_id=? ORDER BY started_at DESC LIMIT 10",
            (user_id,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]

async def create_sub(user_id, plan_id, days, stars) -> dict:
    now = datetime.now()
    expires = now + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE subscriptions SET is_active=0 WHERE user_id=?", (user_id,))
        cur = await db.execute(
            "INSERT INTO subscriptions (user_id,plan_id,started_at,expires_at,is_active,stars_paid) VALUES (?,?,?,?,1,?)",
            (user_id, plan_id, now.isoformat(), expires.isoformat(), stars)
        )
        await db.commit()
        return {"id": cur.lastrowid, "plan_id": plan_id,
                "started_at": now.isoformat(), "expires_at": expires.isoformat(), "stars_paid": stars}

async def save_payment(user_id, plan_id, stars, charge_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (user_id,plan_id,stars_amount,charge_id,paid_at) VALUES (?,?,?,?,?)",
            (user_id, plan_id, stars, charge_id, datetime.now().isoformat())
        )
        await db.commit()

async def get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "UPDATE subscriptions SET is_active=0 WHERE is_active=1 AND expires_at<?",
            (datetime.now().isoformat(),)
        )
        await db.commit()
        async with db.execute("SELECT COUNT(*) as c FROM users") as cur:
            total = (await cur.fetchone())["c"]
        async with db.execute("SELECT COUNT(*) as c FROM subscriptions WHERE is_active=1") as cur:
            active = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT plan_id, COUNT(*) as c FROM subscriptions WHERE is_active=1 GROUP BY plan_id"
        ) as cur:
            by_plan = {r["plan_id"]: r["c"] for r in await cur.fetchall()}
        async with db.execute("SELECT SUM(stars_amount) as s FROM payments") as cur:
            row = await cur.fetchone()
            total_stars = row["s"] or 0
    return {"total": total, "active": active, "by_plan": by_plan, "stars": total_stars}



def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛒 Купить подписку"), KeyboardButton(text="📋 Мои подписки")],
        [KeyboardButton(text="📊 Статистика сервера"), KeyboardButton(text="ℹ️ Помощь")],
    ], resize_keyboard=True)

def plans_kb():
    buttons = [[InlineKeyboardButton(
        text=f"{p['emoji']} {p['name']} — {p['stars']} ⭐",
        callback_data=f"plan:{pid}"
    )] for pid, p in PLANS.items()]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def plan_detail_kb(plan_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Купить за {PLANS[plan_id]['stars']} ⭐", callback_data=f"buy:{plan_id}")],
        [InlineKeyboardButton(text="◀️ К планам", callback_data="back:plans")],
    ])



router = Router()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

@router.message(CommandStart())
async def cmd_start(m: Message):
    await upsert_user(m.from_user.id, m.from_user.username, m.from_user.full_name)
    await m.answer(
        f"👋 Привет, <b>{m.from_user.first_name}</b>!\n\n"
        f"🖥 Добро пожаловать в бот сервера <b>{SERVER_NAME}</b>.\n\n"
        "🛒 Купить подписку\n📋 Посмотреть свои подписки\n📊 Статистика сервера",
        parse_mode="HTML", reply_markup=main_kb()
    )

@router.message(F.text == "🛒 Купить подписку")
async def show_plans(m: Message):
    await m.answer(
        "💎 <b>Выбери план подписки</b>\n\nВсе планы на <b>30 дней</b>. Оплата — <b>Telegram Stars ⭐</b>",
        parse_mode="HTML", reply_markup=plans_kb()
    )

@router.callback_query(F.data.startswith("plan:"))
async def plan_detail(call: CallbackQuery):
    plan_id = call.data.split(":")[1]
    p = PLANS[plan_id]
    features = "\n".join(p["features"])
    await call.message.edit_text(
        f"{p['emoji']} <b>{p['name']}</b>\n\n{p['desc']}\n\n<b>Включает:</b>\n{features}\n\n"
        f"⏳ Срок: <b>30 дней</b>\n💰 Стоимость: <b>{p['stars']} ⭐</b>",
        parse_mode="HTML", reply_markup=plan_detail_kb(plan_id)
    )
    await call.answer()

@router.callback_query(F.data.startswith("buy:"))
async def buy_plan(call: CallbackQuery, bot: Bot):
    plan_id = call.data.split(":")[1]
    p = PLANS[plan_id]
    await call.answer()
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"{p['emoji']} {p['name']}",
        description=f"{p['desc']} — 30 дней",
        payload=f"sub:{plan_id}:{call.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label=p["name"], amount=p["stars"])],
        start_parameter=f"buy_{plan_id}",
    )

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    parts = query.invoice_payload.split(":")
    if len(parts) == 3 and parts[0] == "sub" and parts[1] in PLANS:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Ошибка платежа")

@router.message(F.successful_payment)
async def payment_ok(m: Message):
    payment: SuccessfulPayment = m.successful_payment
    _, plan_id, _ = payment.invoice_payload.split(":")
    p = PLANS[plan_id]
    await save_payment(m.from_user.id, plan_id, payment.total_amount, payment.telegram_payment_charge_id)
    sub = await create_sub(m.from_user.id, plan_id, p["days"], payment.total_amount)
    expires = datetime.fromisoformat(sub["expires_at"])
    await m.answer(
        f"🎉 <b>Оплата прошла!</b>\n\n{p['emoji']} Подписка <b>{p['name']}</b> активирована!\n\n"
        f"📅 До: <b>{expires.strftime('%d.%m.%Y')}</b>\n"
        f"💳 Оплачено: <b>{payment.total_amount} ⭐</b>\n\nСпасибо! 🙏",
        parse_mode="HTML", reply_markup=main_kb()
    )

@router.message(F.text == "📋 Мои подписки")
async def my_subs(m: Message):
    active = await get_active_sub(m.from_user.id)
    history = await get_sub_history(m.from_user.id)
    lines = []

    if active:
        p = PLANS.get(active["plan_id"], {})
        expires = datetime.fromisoformat(active["expires_at"])
        started = datetime.fromisoformat(active["started_at"])
        days_left = max(0, (expires - datetime.now()).days)
        hours_left = max(0, int((expires - datetime.now()).total_seconds() // 3600) % 24)
        elapsed = (datetime.now() - started).days
        total = p.get("days", 30)
        filled = int(10 * elapsed / total)
        bar = "▓" * filled + "░" * (10 - filled)
        pct = min(100, int(elapsed / total * 100))

        lines += [
            "✅ <b>Активная подписка</b>", "",
            f"{p.get('emoji','📦')} <b>{p.get('name', active['plan_id'])}</b>",
            f"📅 Начало: {started.strftime('%d.%m.%Y')}",
            f"⏰ Истекает: {expires.strftime('%d.%m.%Y')}",
            f"⏳ Осталось: <b>{days_left} д. {hours_left} ч.</b>",
            f"[{bar}] {pct}% использовано",
            f"💳 Оплачено: {active['stars_paid']} ⭐",
        ]
    else:
        lines += ["❌ <b>Нет активной подписки</b>", "", "Нажми 🛒 <b>Купить подписку</b>"]

    past = [s for s in history if not s["is_active"]]
    if past:
        lines += ["", "📜 <b>История:</b>"]
        for s in past[:5]:
            p = PLANS.get(s["plan_id"], {})
            exp = datetime.fromisoformat(s["expires_at"])
            lines.append(f"  {p.get('emoji','•')} {p.get('name', s['plan_id'])} — {exp.strftime('%d.%m.%Y')}")

    await m.answer("\n".join(lines), parse_mode="HTML", reply_markup=main_kb())

@router.message(F.text == "📊 Статистика сервера")
async def stats(m: Message):
    s = await get_stats()
    plan_lines = "\n".join(
        f"  {p['emoji']} {p['name']}: <b>{s['by_plan'].get(pid, 0)}</b> чел."
        for pid, p in PLANS.items()
    )
    await m.answer(
        f"📊 <b>Статистика {SERVER_NAME}</b>\n\n"
        f"👥 Всего участников: <b>{s['total']}</b>\n"
        f"✅ С подпиской: <b>{s['active']}</b>\n\n"
        f"<b>По планам:</b>\n{plan_lines}\n\n"
        f"⭐ Всего Stars: <b>{s['stars']}</b>",
        parse_mode="HTML", reply_markup=main_kb()
    )

@router.message(F.text == "ℹ️ Помощь")
async def help_msg(m: Message):
    await m.answer(
        "ℹ️ <b>Помощь</b>\n\n"
        "🛒 <b>Купить подписку</b> — выбрать план и оплатить Stars\n"
        "📋 <b>Мои подписки</b> — активная подписка и история\n"
        "📊 <b>Статистика</b> — кол-во людей и подписок\n\n"
        "Оплата через <b>Telegram Stars ⭐</b>\n"
        "Купить Stars: Настройки → Telegram Stars",
        parse_mode="HTML", reply_markup=main_kb()
    )

@router.callback_query(F.data.startswith("back:"))
async def back_nav(call: CallbackQuery):
    if call.data == "back:plans":
        await call.message.edit_text(
            "💎 <b>Выбери план подписки</b>\n\nВсе планы на <b>30 дней</b>. Оплата — <b>Telegram Stars ⭐</b>",
            parse_mode="HTML", reply_markup=plans_kb()
        )
    await call.answer()



async def main():
    await init_db()
    from aiogram.client.session.aiohttp import AiohttpSession
    session = AiohttpSession(proxy=PROXY)
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logging.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())