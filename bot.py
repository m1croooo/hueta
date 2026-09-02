from __future__ import annotations

import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from schedule_service import load_schedule, get_groups, get_day_schedule, format_day_schedule, format_week_schedule
from users_service import (
    load_users, save_users, get_user, set_user_group, set_auto_send,
    set_send_time, set_show_teachers, get_send_time, get_show_teachers,
    DEFAULT_SEND_TIME, DEFAULT_SHOW_TEACHERS, ALLOWED_SEND_TIMES
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Загрузка токена ---
load_dotenv()

token = os.getenv("BOT_TOKEN")
if not token or ":" not in token:
    try:
        with open(".env", "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if raw and ":" in raw and "=" not in raw:
                token = raw
            elif raw and "BOT_TOKEN" not in raw:
                token = raw.split()[-1] if raw.split() else None
    except FileNotFoundError:
        pass

if not token or ":" not in token:
    print("Ошибка: BOT_TOKEN не найден. Проверьте файл .env (ожидается строка BOT_TOKEN=... или просто токен).", file=sys.stderr)
    sys.exit(1)

# --- Загрузка расписания ---
schedule = load_schedule()
groups = get_groups(schedule)
logging.info(f"Загружено групп: {len(groups)}")

# --- Хранилище пользователей (JSON) ---
users = load_users()
logging.info(f"Загружено пользователей: {len(users)}")

# --- Маппинг weekday -> короткий день ---
WEEKDAY_TO_SHORT = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}

ALMATY_TZ = ZoneInfo("Asia/Almaty")

bot = Bot(token=token)
dp = Dispatcher()
scheduler = None

# защита от дублей авторассылки: user_id_str -> date ISO (YYYY-MM-DD)
_last_auto_sent: dict[str, str] = {}


def build_groups_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for g in groups:
        btn = InlineKeyboardButton(text=g, callback_data=f"group:{g}")
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_main_reply_keyboard() -> ReplyKeyboardMarkup:
    try:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
                [KeyboardButton(text="Неделя"), KeyboardButton(text="Моя группа")],
                [KeyboardButton(text="Настройки")],
            ],
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False,
        )
    except TypeError:
        # fallback если версия aiogram не поддерживает is_persistent
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
                [KeyboardButton(text="Неделя"), KeyboardButton(text="Моя группа")],
                [KeyboardButton(text="Настройки")],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )


def _get_settings_values(user_id: int | str) -> tuple[str, bool, str, bool]:
    """Возвращает (group, auto_send, send_time, show_teachers) с дефолтами."""
    u = get_user(users, user_id)
    if not u:
        return "", True, DEFAULT_SEND_TIME, DEFAULT_SHOW_TEACHERS
    group = u.get("group", "")
    auto_send = bool(u.get("auto_send", True))
    send_time = get_send_time(users, user_id)
    show_teachers = get_show_teachers(users, user_id)
    return group, auto_send, send_time, show_teachers


def _build_settings_text(user_id: int | str) -> str:
    group, auto_send, send_time, show_teachers = _get_settings_values(user_id)
    status = "ВКЛ" if auto_send else "ВЫКЛ"
    teach = "ВКЛ" if show_teachers else "ВЫКЛ"
    return f"Настройки\n\nГруппа: {group}\nАвторассылка: {status}\nВремя: {send_time}\nПреподаватели: {teach}"


def build_settings_inline(user_id: int | str) -> InlineKeyboardMarkup:
    _, auto_send, _, show_teachers = _get_settings_values(user_id)
    auto_text = "Выключить авторассылку" if auto_send else "Включить авторассылку"
    teach_text = f"Преподаватели: {'ВКЛ' if show_teachers else 'ВЫКЛ'}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить время", callback_data="change_time")],
        [InlineKeyboardButton(text=teach_text, callback_data="toggle_teachers")],
        [InlineKeyboardButton(text=auto_text, callback_data="toggle_autosend")],
    ])


def build_time_picker_inline(current_time: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for t in ALLOWED_SEND_TIMES:
        label = f"✓ {t}" if t == current_time else t
        btn = InlineKeyboardButton(text=label, callback_data=f"set_time:{t}")
        row.append(btn)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_current_day_short(offset: int = 0) -> str | None:
    """Возвращает короткий код дня (Пн..Вс) для сегодня+offset (локальное время)."""
    target = datetime.now() + timedelta(days=offset)
    return WEEKDAY_TO_SHORT[target.weekday()]


def get_today_short_almaty() -> str:
    """Текущий день недели в Asia/Almaty."""
    now = datetime.now(ALMATY_TZ)
    return WEEKDAY_TO_SHORT[now.weekday()]


async def send_daily_schedule():
    """Задача для APScheduler: рассылка каждую минуту, с учетом индивидуального send_time."""
    day_short = get_today_short_almaty()
    if day_short in ("Сб", "Вс"):
        return
    now = datetime.now(ALMATY_TZ)
    cur_hm = now.strftime("%H:%M")
    today_str = now.date().isoformat()
    logging.debug(f"Scheduler tick {cur_hm} {day_short}")
    for uid_str, data in list(users.items()):
        if not data.get("auto_send", False):
            continue
        group = data.get("group")
        if not group or group not in schedule:
            continue
        # индивидуальное время
        send_time = data.get("send_time", DEFAULT_SEND_TIME)
        if send_time not in ALLOWED_SEND_TIMES:
            logging.warning(f"Невалидное send_time '{send_time}' для {uid_str}, используется {DEFAULT_SEND_TIME}")
            send_time = DEFAULT_SEND_TIME
        if send_time != cur_hm:
            continue
        # защита от дублей: если сегодня уже отправляли — пропуск
        if _last_auto_sent.get(uid_str) == today_str:
            logging.info(f"Авторассылка пропущена (дубль) для {uid_str} на {today_str}")
            continue
        try:
            chat_id = int(uid_str)
        except (ValueError, TypeError):
            logging.warning(f"Авторассылка: пропуск некорректного user_id {uid_str!r}")
            continue
        lessons = get_day_schedule(schedule, group, day_short)
        if lessons is None:
            continue
        if not lessons:
            continue
        show_teachers = bool(data.get("show_teachers", DEFAULT_SHOW_TEACHERS))
        text = format_day_schedule(group, day_short, lessons, show_teachers=show_teachers)
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            logging.info(f"Авторассылка отправлена {chat_id} ({group}) в {cur_hm}")
            _last_auto_sent[uid_str] = today_str
        except Exception as e:
            logging.error(f"Авторассылка ошибка для {chat_id}: {e}")
            continue


# --- Хэндлеры ---

@dp.message(CommandStart())
async def cmd_start(message: Message):
    u = get_user(users, message.from_user.id)
    if u and u.get("group") in schedule:
        group = u["group"]
        kb = build_main_reply_keyboard()
        await message.answer(f"{group}\n\nВыбери действие.", reply_markup=kb)
        return
    if u and u.get("group") not in schedule:
        uid_str = str(message.from_user.id)
        if uid_str in users and "group" in users[uid_str]:
            del users[uid_str]["group"]
            save_users(users)
        logging.info(f"Сброшена несуществующая группа для {message.from_user.id}")
    kb = build_groups_keyboard()
    await message.answer("Выбери свою группу:", reply_markup=kb)


@dp.callback_query(F.data.startswith("group:"))
async def on_group_selected(callback: CallbackQuery):
    group = callback.data[len("group:"):]
    if group not in schedule:
        await callback.answer("Группа не найдена", show_alert=True)
        return
    uid = callback.from_user.id
    had_group = bool(get_user(users, uid) and get_user(users, uid).get("group") in schedule)
    set_user_group(users, uid, group)
    await callback.answer()
    kb = build_main_reply_keyboard()
    # различаем первое сохранение и изменение
    if had_group:
        text = f"Группа изменена: {group}"
    else:
        text = f"Группа выбрана: {group}"
    try:
        await callback.message.edit_text(text)
    except Exception:
        pass
    await callback.message.answer(text, reply_markup=kb)


@dp.message(Command("test_notify"))
async def cmd_test_notify(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        await message.answer("Сначала выбери группу.")
        return
    day_short = get_today_short_almaty()
    if day_short in ("Сб", "Вс"):
        await message.answer("Сегодня занятий нет.")
        return
    lessons = get_day_schedule(schedule, group, day_short)
    if lessons is None:
        await message.answer("Сначала выбери группу.")
        return
    if not lessons:
        await message.answer("Сегодня занятий нет.")
        return
    show_teachers = get_show_teachers(users, message.from_user.id)
    text = format_day_schedule(group, day_short, lessons, show_teachers=show_teachers)
    await message.answer(text)


# Текстовые handlers для ReplyKeyboardMarkup

@dp.message(F.text == "Сегодня")
async def on_today_text(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        kb = build_groups_keyboard()
        await message.answer("Сначала выбери группу.", reply_markup=kb)
        return
    day_short = get_current_day_short(0)
    if day_short in ("Сб", "Вс"):
        await message.answer("Сегодня занятий нет.", reply_markup=build_main_reply_keyboard())
        return
    lessons = get_day_schedule(schedule, group, day_short)
    if lessons is None:
        await message.answer("Сначала выбери группу.", reply_markup=build_groups_keyboard())
        return
    show_teachers = get_show_teachers(users, message.from_user.id)
    text = format_day_schedule(group, day_short, lessons, show_teachers=show_teachers)
    await message.answer(text, reply_markup=build_main_reply_keyboard())


@dp.message(F.text == "Завтра")
async def on_tomorrow_text(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        kb = build_groups_keyboard()
        await message.answer("Сначала выбери группу.", reply_markup=kb)
        return
    day_short = get_current_day_short(1)
    if day_short in ("Сб", "Вс"):
        await message.answer("Завтра занятий нет.", reply_markup=build_main_reply_keyboard())
        return
    lessons = get_day_schedule(schedule, group, day_short)
    if lessons is None:
        await message.answer("Сначала выбери группу.", reply_markup=build_groups_keyboard())
        return
    show_teachers = get_show_teachers(users, message.from_user.id)
    text = format_day_schedule(group, day_short, lessons, show_teachers=show_teachers)
    await message.answer(text, reply_markup=build_main_reply_keyboard())


@dp.message(F.text == "Неделя")
async def on_week_text(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        kb = build_groups_keyboard()
        await message.answer("Сначала выбери группу.", reply_markup=kb)
        return
    parts = format_week_schedule(group, schedule)
    for part in parts:
        await message.answer(part, reply_markup=build_main_reply_keyboard())


@dp.message(F.text == "Моя группа")
async def on_my_group(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        kb = build_groups_keyboard()
        await message.answer("Выбери свою группу:", reply_markup=kb)
        return
    kb = build_groups_keyboard()
    await message.answer(f"Текущая группа: {group}", reply_markup=kb)


@dp.message(F.text == "Настройки")
async def on_settings(message: Message):
    u = get_user(users, message.from_user.id)
    group = u.get("group") if u else None
    if not group or group not in schedule:
        kb = build_groups_keyboard()
        await message.answer("Сначала выбери группу.", reply_markup=kb)
        return
    text = _build_settings_text(message.from_user.id)
    kb = build_settings_inline(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "toggle_autosend")
async def on_toggle_autosend(callback: CallbackQuery):
    u = get_user(users, callback.from_user.id)
    if not u or not u.get("group") or u.get("group") not in schedule:
        await callback.answer("Сначала выбери группу", show_alert=True)
        kb = build_groups_keyboard()
        await callback.message.answer("Выбери свою группу:", reply_markup=kb)
        return
    current = bool(u.get("auto_send", True))
    new_val = not current
    set_auto_send(users, callback.from_user.id, new_val)
    await callback.answer(f"Авторассылка {'включена' if new_val else 'выключена'}")
    # обновляем сообщение Настройки
    text = _build_settings_text(callback.from_user.id)
    kb = build_settings_inline(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "toggle_teachers")
async def on_toggle_teachers(callback: CallbackQuery):
    u = get_user(users, callback.from_user.id)
    if not u or not u.get("group") or u.get("group") not in schedule:
        await callback.answer("Сначала выбери группу", show_alert=True)
        kb = build_groups_keyboard()
        await callback.message.answer("Выбери свою группу:", reply_markup=kb)
        return
    current = get_show_teachers(users, callback.from_user.id)
    new_val = not current
    set_show_teachers(users, callback.from_user.id, new_val)
    await callback.answer(f"Преподаватели {'ВКЛ' if new_val else 'ВЫКЛ'}")
    text = _build_settings_text(callback.from_user.id)
    kb = build_settings_inline(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "change_time")
async def on_change_time(callback: CallbackQuery):
    u = get_user(users, callback.from_user.id)
    if not u or not u.get("group") or u.get("group") not in schedule:
        await callback.answer("Сначала выбери группу", show_alert=True)
        return
    current = get_send_time(users, callback.from_user.id)
    kb = build_time_picker_inline(current)
    text = f"Выбери время авторассылки:\nТекущее: {current}"
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("set_time:"))
async def on_set_time(callback: CallbackQuery):
    u = get_user(users, callback.from_user.id)
    if not u or not u.get("group") or u.get("group") not in schedule:
        await callback.answer("Сначала выбери группу", show_alert=True)
        return
    new_time = callback.data.split(":", 1)[1]
    if new_time not in ALLOWED_SEND_TIMES:
        await callback.answer("Недоступное время", show_alert=True)
        return
    set_send_time(users, callback.from_user.id, new_time)
    await callback.answer(f"Время авторассылки: {new_time}")
    # Подтверждение как обновление сообщения, затем возврат в Настройки
    try:
        await callback.message.edit_text(f"Время авторассылки: {new_time}")
    except Exception:
        pass
    # Вернуть в экран Настройки
    text = _build_settings_text(callback.from_user.id)
    kb = build_settings_inline(callback.from_user.id)
    try:
        await callback.message.answer(text, reply_markup=kb)
    except Exception:
        # если не удалось отправить новое, пробуем отредактировать
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass


@dp.message()
async def on_any_message(message: Message):
    # игнорируем команды (уже обработаны)
    if message.text and message.text.startswith("/"):
        return
    u = get_user(users, message.from_user.id)
    if u and u.get("group") in schedule:
        kb = build_main_reply_keyboard()
        await message.answer("Используй кнопки меню.", reply_markup=kb)
    else:
        if u and u.get("group") not in schedule:
            uid_str = str(message.from_user.id)
            if uid_str in users and "group" in users[uid_str]:
                del users[uid_str]["group"]
                save_users(users)
        kb = build_groups_keyboard()
        await message.answer("Выбери свою группу:", reply_markup=kb)


async def main():
    global scheduler
    logging.info("Бот запускается (polling)...")

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as e:
        logging.error(f"APScheduler не установлен: {e}. Добавьте apscheduler в requirements.txt и установите.")
        sys.exit(1)

    scheduler = AsyncIOScheduler(timezone=ALMATY_TZ)
    scheduler.add_job(
        send_daily_schedule,
        CronTrigger(day_of_week="mon-fri", hour="*", minute="*", timezone=ALMATY_TZ),
        id="daily_send",
        name="Ежеминутная проверка авторассылки Asia/Almaty",
        replace_existing=True,
    )
    scheduler.start()
    logging.info("Scheduler запущен: Пн-Пт каждую минуту Asia/Almaty (индивидуальное время)")

    try:
        await dp.start_polling(bot)
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            logging.info("Scheduler остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
