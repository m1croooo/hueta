from __future__ import annotations

import json
import os
import sys

USERS_PATH = "users.json"

DEFAULT_SEND_TIME = "07:00"
DEFAULT_SHOW_TEACHERS = False

ALLOWED_SEND_TIMES = ["06:00", "06:30", "07:00", "07:30", "08:00", "08:30", "09:00"]


def load_users(path: str = USERS_PATH) -> dict:
    """Загружает users.json. Если файла нет — возвращает пустой словарь. Если повреждён — выводит ошибку и не удаляет файл."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Ошибка: файл {path} повреждён / невалидный JSON: {e}", file=sys.stderr)
        print(f"Файл {path} не будет перезаписан автоматически. Исправьте его вручную или удалите.", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"Ошибка при чтении {path}: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"Ошибка: файл {path} имеет неверную структуру (ожидается объект).", file=sys.stderr)
        return {}
    return data


def save_users(users: dict, path: str = USERS_PATH) -> None:
    """Сохраняет users.json атомарно."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Ошибка при сохранении {path}: {e}", file=sys.stderr)
        # попытаться удалить tmp
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def get_user(users: dict, user_id: int | str) -> dict | None:
    """Возвращает настройки пользователя или None."""
    return users.get(str(user_id))


def _ensure_defaults(entry: dict) -> dict:
    """Заполняет отсутствующие настройки значениями по умолчанию (не сохраняет)."""
    if "send_time" not in entry:
        entry["send_time"] = DEFAULT_SEND_TIME
    elif entry["send_time"] not in ALLOWED_SEND_TIMES:
        # оставим как есть, валидация при чтении предупредит
        pass
    if "show_teachers" not in entry:
        entry["show_teachers"] = DEFAULT_SHOW_TEACHERS
    return entry


def get_send_time(users: dict, user_id: int | str) -> str:
    """Возвращает send_time пользователя или дефолт. При невалидном значении — дефолт + warning."""
    import logging
    entry = users.get(str(user_id))
    if not entry:
        return DEFAULT_SEND_TIME
    val = entry.get("send_time", DEFAULT_SEND_TIME)
    if val not in ALLOWED_SEND_TIMES:
        logging.warning(f"Невалидное send_time '{val}' для {user_id}, используется {DEFAULT_SEND_TIME}")
        return DEFAULT_SEND_TIME
    return val


def get_show_teachers(users: dict, user_id: int | str) -> bool:
    """Возвращает show_teachers или дефолт."""
    entry = users.get(str(user_id))
    if not entry:
        return DEFAULT_SHOW_TEACHERS
    return bool(entry.get("show_teachers", DEFAULT_SHOW_TEACHERS))


def set_user_group(users: dict, user_id: int | str, group: str, path: str = USERS_PATH) -> None:
    """Устанавливает группу пользователя. Если auto_send не задан — включает по умолчанию. Сразу сохраняет."""
    uid = str(user_id)
    entry = users.get(uid, {})
    entry["group"] = group
    if "auto_send" not in entry:
        entry["auto_send"] = True
    if "send_time" not in entry:
        entry["send_time"] = DEFAULT_SEND_TIME
    if "show_teachers" not in entry:
        entry["show_teachers"] = DEFAULT_SHOW_TEACHERS
    users[uid] = entry
    save_users(users, path)


def set_auto_send(users: dict, user_id: int | str, value: bool, path: str = USERS_PATH) -> None:
    """Переключает авторассылку для пользователя. Сразу сохраняет."""
    uid = str(user_id)
    entry = users.get(uid)
    if entry is None:
        entry = {}
        users[uid] = entry
    entry["auto_send"] = bool(value)
    # если группы нет — оставляем только auto_send
    users[uid] = entry
    save_users(users, path)


def set_send_time(users: dict, user_id: int | str, value: str, path: str = USERS_PATH) -> None:
    """Устанавливает время авторассылки. Принимает только допустимые значения. Сразу сохраняет."""
    import logging
    if value not in ALLOWED_SEND_TIMES:
        logging.warning(f"Попытка установить невалидное send_time '{value}' для {user_id} — игнорируется")
        return
    uid = str(user_id)
    entry = users.get(uid)
    if entry is None:
        entry = {}
        users[uid] = entry
    entry["send_time"] = value
    users[uid] = entry
    save_users(users, path)


def set_show_teachers(users: dict, user_id: int | str, value: bool, path: str = USERS_PATH) -> None:
    """Переключает отображение преподавателей. Сразу сохраняет."""
    uid = str(user_id)
    entry = users.get(uid)
    if entry is None:
        entry = {}
        users[uid] = entry
    entry["show_teachers"] = bool(value)
    users[uid] = entry
    save_users(users, path)
