from __future__ import annotations

import json
import sys

SCHEDULE_PATH = "schedule.json"

# Короткие коды -> полное название (оставлено для совместимости)
DAY_FULL_NAMES = {
    "Пн": "Понедельник",
    "Вт": "Вторник",
    "Ср": "Среда",
    "Чт": "Четверг",
    "Пт": "Пятница",
    "Сб": "Суббота",
    "Вс": "Воскресенье",
}

WEEKDAYS_ORDER = ["Пн", "Вт", "Ср", "Чт", "Пт"]

# Словарь сокращений для длинных предметов (только отображение, schedule.json не меняется)
SHORT_SUBJECTS = {
    "Применение базовых знаний экономики и основ предпринимательства": "Основы предпринимательства",
    "Начальная военная и технологическая подготовка": "НВТП",
    "Алғашқы әскери мен технологиялық дайындық": "АӘТД",
}

TELEGRAM_LIMIT = 4096


def load_schedule(path: str = SCHEDULE_PATH) -> dict:
    """Загружает schedule.json. При ошибке выводит понятное сообщение и завершает программу."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Ошибка: файл {path} не найден. Поместите schedule.json в корень проекта.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Ошибка: файл {path} повреждён / невалидный JSON: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при чтении {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict) or not data:
        print(f"Ошибка: {path} пуст или имеет неверную структуру (ожидается объект с группами).", file=sys.stderr)
        sys.exit(1)

    return data


def get_groups(schedule: dict) -> list[str]:
    """Возвращает отсортированный список групп из расписания."""
    return sorted(schedule.keys())


def get_day_schedule(schedule: dict, group: str, day: str) -> list[dict] | None:
    """Возвращает список занятий группы на день. None если группа не найдена."""
    group_data = schedule.get(group)
    if group_data is None:
        return None
    return group_data.get(day, [])


def _parse_time(time_range: str) -> tuple[int, int]:
    """'08:00-08:45' -> (480, 525) минут от начала суток. Поддерживает '-' и '–'."""
    normalized = time_range.replace("–", "-").replace("—", "-")
    start_s, end_s = normalized.split("-")
    sh, sm = map(int, start_s.strip().split(":"))
    eh, em = map(int, end_s.strip().split(":"))
    return sh * 60 + sm, eh * 60 + em


def _get_start_time(time_range: str) -> str:
    """'08:00-08:45' -> '08:00'"""
    normalized = time_range.replace("–", "-").replace("—", "-")
    if "-" in normalized:
        return normalized.split("-")[0].strip()
    return normalized.strip()


def shorten_subject(subject: str) -> str:
    """Сокращает только очень длинные названия, остальные оставляет как есть."""
    s = subject.strip()
    return SHORT_SUBJECTS.get(s, s)


def format_day_schedule(group: str, day: str, lessons: list[dict], show_teachers: bool = False) -> str:
    """Форматирует расписание одного дня (Сегодня/Завтра).

    Формат без преподавателей (show_teachers=False) — максимально компактный:
        <группа> · <день>
        08:00  Предмет
        08:50  Предмет
        ☕ 55 мин
        11:20  Предмет

    Формат с преподавателями (show_teachers=True) — каждая пара отдельным блоком:
        <группа> · <день>
        08:00  Предмет
               └ Преподаватель

        08:50  Предмет
               └ Преподаватель

        ☕ 55 мин

        11:20  Предмет
               └ Преподаватель

    - только время начала, 2 пробела между временем и предметом
    - без номеров, без тире
    - большая перемена вычисляется по времени, выводится как '☕ N мин' отдельным блоком (пустая строка до и после)
    - если show_teachers, преподаватель — вторая строка с отступом и символом └
    - если show_teachers=False, между парами пустых строк нет
    """
    header = f"{group} · {day}"

    if not lessons:
        return f"{header}\n\nЗанятий нет."

    lines = [header, ""]

    prev_end: int | None = None

    for idx, lesson in enumerate(lessons):
        time_raw = lesson.get("time", "")
        subject_raw = lesson.get("subject", "").strip()
        subject = shorten_subject(subject_raw)

        try:
            cur_start, cur_end = _parse_time(time_raw)
        except Exception:
            cur_start, cur_end = None, None

        if prev_end is not None and cur_start is not None:
            gap = cur_start - prev_end
            if gap > 15:
                # большая перемена — отдельный блок с пустой строкой до и после
                if show_teachers:
                    # если предыдущий блок уже оставил пустую строку-разделитель, не дублировать
                    if lines and lines[-1] != "":
                        lines.append("")
                else:
                    lines.append("")
                lines.append(f"☕ {gap} мин")
                lines.append("")
            else:
                # обычный переход между парами: в режиме с преподавателями — одна пустая строка между блоками
                if show_teachers and idx > 0:
                    if lines and lines[-1] != "":
                        lines.append("")
        else:
            if show_teachers and idx > 0:
                if lines and lines[-1] != "":
                    lines.append("")

        start = _get_start_time(time_raw)
        lines.append(f"{start}  {subject}")
        if show_teachers:
            teacher = lesson.get("teacher", "").strip()
            if teacher:
                lines.append(f"       └ {teacher}")

        if cur_end is not None:
            prev_end = cur_end
        else:
            prev_end = None

    text = "\n".join(lines).rstrip()
    return text


def format_week_schedule(group: str, schedule: dict) -> list[str]:
    """Форматирует всю неделю (Пн-Пт) в компактном виде.

    Возвращает список из 1 или 2 сообщений (Telegram-лимит 4096).
    Никогда не разделяет один день между сообщениями.
    """
    # Собираем блоки по дням
    day_blocks: list[str] = []
    for day in WEEKDAYS_ORDER:
        lessons = schedule.get(group, {}).get(day, []) if group in schedule else []
        block_lines = [day]
        if not lessons:
            block_lines.append("Нет занятий")
        else:
            for lesson in lessons:
                start = _get_start_time(lesson.get("time", ""))
                subject = shorten_subject(lesson.get("subject", "").strip())
                block_lines.append(f"{start} {subject}")
        day_blocks.append("\n".join(block_lines))

    header = f"{group} · Неделя"
    # Первый вариант — одно сообщение
    full_text = header + "\n\n" + "\n\n".join(day_blocks)

    if len(full_text) <= TELEGRAM_LIMIT:
        return [full_text]

    # Нужно разделить на 2 сообщения, не разбивая день
    # Ищем точку разбиения: пытаемся разделить примерно пополам по дням
    # Перебираем варианты split_at от 1 до 4 и выбираем балансировку
    best_split = None
    for split_at in range(1, len(day_blocks)):
        part1 = header + "\n\n" + "\n\n".join(day_blocks[:split_at])
        part2 = "\n\n".join(day_blocks[split_at:])
        if len(part1) <= TELEGRAM_LIMIT and len(part2) <= TELEGRAM_LIMIT:
            # выбираем split ближе к середине
            if best_split is None or abs(split_at - len(day_blocks) / 2) < abs(best_split - len(day_blocks) / 2):
                best_split = split_at

    if best_split is not None:
        part1 = header + "\n\n" + "\n\n".join(day_blocks[:best_split])
        part2 = "\n\n".join(day_blocks[best_split:])
        return [part1, part2]

    # Fallback: если даже после перебора не помещается (очень маловероятно),
    # отправляем по частям по дням (но по условию не более 2, поэтому режем пополам)
    mid = len(day_blocks) // 2
    part1 = header + "\n\n" + "\n\n".join(day_blocks[:mid])
    part2 = "\n\n".join(day_blocks[mid:])
    return [part1, part2]
