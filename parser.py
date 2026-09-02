#!/usr/bin/env python3
"""
Надежный парсер PDF-расписания первого курса.
Использует PyMuPDF (fitz) find_tables + анализ координат/границ таблиц.
Проверяет результат и сохраняет schedule.json
"""
import os
import re
import json
import sys
import fitz  # PyMuPDF

TIME_SLOTS = [
    "08:00-08:45",
    "08:50-09:35",
    "09:40-10:25",
    "11:20-12:05",
    "12:10-12:55",
    "13:00-13:45",
    "13:50-14:35",
    "14:40-15:25",
]

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт"]
VALID_DAYS = set(DAYS)
VALID_TIMES = set(TIME_SLOTS)

PDF_CANDIDATES = ["schedule.pdf", "Schedule.pdf", "Shedule.pdf", "Shed ule.pdf"]

def find_pdf():
    # 1. явные кандидаты
    for name in PDF_CANDIDATES:
        if os.path.exists(name):
            return name
    # 2. любой pdf в корне (case-insensitive)
    for f in os.listdir("."):
        if f.lower().endswith(".pdf"):
            return f
    return None

def parse_cell(cell_text):
    """Разбирает содержимое ячейки на (subject, teacher).
    - cell_text: строка с \n внутри (как отдает find_tables)
    - возвращает (subject, teacher) или (None, None) если ячейка пустая/мусор
    """
    if cell_text is None:
        return None, None
    txt = cell_text.strip()
    if not txt or txt == ":" or txt == "":
        return None, None

    # find_tables отдает multi-line через \n
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    if not lines:
        return None, None
    # мусор типа одиночного ":"
    if len(lines) == 1 and lines[0] in (":", "-", ""):
        return None, None
    # если ячейка явно содержит только ":" - пропускаем
    if txt == ":" :
        return None, None

    if len(lines) == 1:
        # одинокое значение - считаем предметом без преподавателя
        # но такие случаи в расписании не должны быть, все равно сохраним
        return lines[0], ""

    # Эвристика для аудиторий: последняя строка - только цифры (например "213")
    # тогда преподаватель = предпоследняя + последняя
    if len(lines) >= 3 and re.match(r"^\d+$", lines[-1]):
        teacher = f"{lines[-2]} {lines[-1]}"
        subject = " ".join(lines[:-2])
        return subject.strip(), teacher.strip()

    # Стандарт: последняя строка - преподаватель, остальное - предмет
    teacher = lines[-1]
    subject = " ".join(lines[:-1])
    # почистить множественные пробелы
    subject = re.sub(r"\s+", " ", subject).strip()
    teacher = re.sub(r"\s+", " ", teacher).strip()
    if not subject:
        return None, None
    return subject, teacher

def is_group_row(row):
    """Групповая строка: первый столбец - название группы, остальные None/пусто"""
    if not row or not row[0]:
        return False
    first = row[0].strip()
    if first in ("День",) + tuple(DAYS):
        return False
    # группа содержит "-" и обычно буквы
    # в PDF группа занимает всю ширину, остальные ячейки None
    others = row[1:]
    if all(c is None or (isinstance(c, str) and c.strip() == "") for c in others):
        # дополнительная проверка: содержит "-" или известный шаблон
        if "-" in first:
            return True
        # на всякий случай: БҚ-131 и т.п.
        return True
    return False

def main():
    pdf_path = find_pdf()
    if not pdf_path:
        print("ERROR: не найден schedule.pdf (искал Schedule.pdf / Shedule.pdf / *.pdf)", file=sys.stderr)
        sys.exit(1)

    print(f"Reading {pdf_path}")
    doc = fitz.open(pdf_path)
    print(f"Pages: {len(doc)}")

    schedule = {}
    found_groups = []
    total_filled_cells = 0
    warnings = []

    current_group = None
    # Для отчета по страницам
    groups_per_page = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        try:
            tables = page.find_tables()
        except Exception as e:
            warnings.append(f"Page {page_idx}: find_tables error: {e}")
            continue

        if not tables or len(tables.tables) == 0:
            warnings.append(f"Page {page_idx}: таблицы не найдены")
            continue

        # В этом PDF каждая страница содержит 1 большую таблицу,
        # которая внутри объединяет 2-3 группы подряд.
        # Поэтому итерируемся по всем таблицам (обычно 1) и по их строкам.
        for table in tables:
            rows = table.extract()
            if not rows:
                continue

            # Счетчик групп на странице
            page_group_count = 0
            # Ожидаем структуру:
            # [группа] -> [День header] -> 5 дней -> [группа] -> ...
            for r_idx, row in enumerate(rows):
                # Пропускаем полностью пустые строки
                if row is None:
                    continue
                # Нормализовать None -> ""
                # is_group_row ожидает именно None в пустых, оставляем как есть
                if is_group_row(row):
                    group_name = row[0].strip()
                    # нормализовать пробелы
                    group_name = re.sub(r"\s+", " ", group_name)
                    current_group = group_name
                    if current_group not in schedule:
                        schedule[current_group] = {d: [] for d in DAYS}
                        found_groups.append(current_group)
                        page_group_count += 1
                    else:
                        warnings.append(f"Duplicate group {current_group} on page {page_idx}")
                    continue

                # Заголовок "День | 8:00-8:45 | ... " - пропускаем
                if row[0] and row[0].strip() == "День":
                    # можно проверить соответствие времени колонок, но доверяем индексу
                    # для отчета проверим заголовки
                    header = [c.strip() if c else "" for c in row]
                    # header[1:] должны содержать времена без ведущего нуля
                    # не критично, но предупредим если не совпало
                    continue

                # Строка дня недели
                if row[0] and row[0].strip() in VALID_DAYS:
                    if current_group is None:
                        warnings.append(f"Page {page_idx} row {r_idx}: день без группы: {row[0]}")
                        continue
                    day = row[0].strip()
                    # колонки 1..8 соответствуют TIME_SLOTS[0..7]
                    for col_idx in range(1, 9):
                        if col_idx >= len(row):
                            continue
                        cell = row[col_idx]
                        time = TIME_SLOTS[col_idx - 1]
                        subject, teacher = parse_cell(cell) if cell else (None, None)
                        if subject is None:
                            continue
                        # проверки
                        if time not in VALID_TIMES:
                            warnings.append(f"Invalid time {time} for {current_group} {day}")
                            continue
                        if not subject:
                            warnings.append(f"Empty subject for {current_group} {day} {time}")
                            continue
                        schedule[current_group][day].append({
                            "time": time,
                            "subject": subject,
                            "teacher": teacher
                        })
                        total_filled_cells += 1
                    continue

                # прочие строки игнорируем
            if page_group_count > 0:
                groups_per_page.append((page_idx, page_group_count))

    # --- Проверки ---
    print(f"Found groups: {len(schedule)}")
    if groups_per_page:
        for p, cnt in groups_per_page:
            print(f"  Page {p}: {cnt} group(s)")

    # Детальный список групп
    for g in found_groups:
        print(f"  - {g}")

    errors = []

    if len(schedule) < 10:
        errors.append(f"Найдено только {len(schedule)} групп, ожидалось >=10")

    for group, days in schedule.items():
        # проверка дней
        for d in days.keys():
            if d not in VALID_DAYS:
                errors.append(f"Group {group}: недопустимый день {d}")
        # хотя бы один день с занятиями
        has_any = any(len(v) > 0 for v in days.values())
        if not has_any:
            errors.append(f"Group {group}: нет ни одного занятия")
        for day, lessons in days.items():
            for l in lessons:
                if l["time"] not in VALID_TIMES:
                    errors.append(f"{group} {day}: недопустимое время {l['time']}")
                if not l["subject"] or not l["subject"].strip():
                    errors.append(f"{group} {day} {l['time']}: пустой subject")
                # проверка на дробление ячейки: subject не должен быть слишком коротким обрывком?
                # например если subject - одно слово из длинного названия, это ошибка дробления
                # но мы уже склеиваем через join, так что проверка не нужна жесткая

    print(f"Total filled cells: {total_filled_cells}")

    if warnings:
        print("Warnings:")
        for w in warnings[:20]:
            print(f"  ! {w}")
        if len(warnings) > 20:
            print(f"  ... and {len(warnings)-20} more warnings")

    if errors:
        print("ERRORS (проверки не пройдены):")
        for e in errors:
            print(f"  ✗ {e}")
        # не выходим с ошибкой, но предупреждаем
    else:
        print("All checks passed.")

    # --- Сохранение ---
    out_path = "schedule.json"
    # Сортировка групп для детерминизма
    ordered = {k: schedule[k] for k in sorted(schedule.keys())}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)

    print(f"Saved {out_path}")

    # Краткий отчет по нескольким группам для визуальной проверки
    if schedule:
        sample_groups = found_groups[:3]
        # если групп много, берем с разных страниц: первую, среднюю, последнюю
        if len(found_groups) >= 3:
            sample_groups = [found_groups[0], found_groups[len(found_groups)//2], found_groups[-1]]
        print("\nSample schedules:")
        for g in sample_groups:
            print(f"\n== {g} ==")
            for d in DAYS:
                lessons = schedule[g].get(d, [])
                print(f"  {d}: {len(lessons)} занятий")
                for ls in lessons[:3]:  # первые 3 для краткости
                    print(f"    {ls['time']} | {ls['subject']} | {ls['teacher']}")

    # Итог для пользователя
    if errors:
        print("\nПарсер завершил работу с ошибками проверки - проверьте schedule.json вручную.")
        sys.exit(1)
    else:
        print("\nГотово. Проверьте schedule.json.")

if __name__ == "__main__":
    main()
