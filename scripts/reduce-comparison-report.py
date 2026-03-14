#!/usr/bin/env python3
"""
Сокращение подробного отчёта о сравнении конфигураций 1С.

Вход: подробный отчёт сравнения (ОтчетОСравнении.txt)
Выход: компактный список модулей с номерами изменённых строк
       и найденными сигнатурами процедур/функций.

Использование:
    python scripts/reduce-comparison-report.py <путь к отчёту> [-o <выходной файл>]
"""
import re
import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LineRange:
    """Диапазон строк изменения."""
    start: int
    end: int
    change_type: str  # "changed", "added", "removed"


@dataclass
class ModuleChanges:
    """Изменения в одном модуле."""
    object_path: str        # напр. "Документ.ПриходныйКассовыйОрдер"
    module_type: str        # напр. "Модуль объекта", "Модуль менеджера", "Модуль формы"
    form_name: Optional[str] = None  # имя формы, если это модуль формы
    line_ranges: list = field(default_factory=list)
    found_signatures: list = field(default_factory=list)  # найденные "Процедура X(" / "Функция X("


# Паттерны для парсинга
RE_OBJECT_HEADER = re.compile(
    r'^(\t+)- [*]{3}(Конфигурация|Подсистема|ОбщийМодуль|Справочник|Документ|'
    r'Перечисление|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|'
    r'ПланВидовХарактеристик|ПланСчетов|ПланОбмена|Обработка|Отчет|'
    r'РегламентноеЗадание|ПодпискаНаСобытия|HTTPСервис|ВебСервис|'
    r'БизнесПроцесс|Задача|ЖурналДокументов|Константа|ОпределяемыйТип|'
    r'РегистрРасчета|ПланВидовРасчета|ОбщаяФорма|ОбщаяКоманда|'
    r'СервисИнтеграции|ХранилищеНастроек)\.(.+)$'
)

RE_FORM_HEADER = re.compile(
    r'^(\t+)- [*]{3}.+\.Форма\.(.+)$'
)

RE_COMMAND_HEADER = re.compile(
    r'^(\t+)- [*]{3}.+\.Команда\.(.+)$'
)

RE_MODULE_MARKER = re.compile(
    r'^(\t+)- (Модуль(?:\s+объекта|\s+менеджера|\s+набора записей|\s+команды)?) - Различаются значения$'
)

RE_CHANGE_LINES = re.compile(
    r'^\t+Изменено:\s+(\d+)\s*-\s*(\d+)$'
)

RE_ADDED_LINES = re.compile(
    r'^\t+Объект присутствует только в основной конфигурации:\s+(\d+)\s*-\s*(\d+)$'
)

RE_REMOVED_LINES = re.compile(
    r'^\t+Объект присутствует только в конфигурации поставщика:\s+(\d+)\s*-\s*(\d+)$'
)

# Сигнатуры процедур/функций в кодовых строках
RE_PROC_SIGNATURE = re.compile(
    r'(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z0-9_]+)\s*\(',
    re.IGNORECASE
)

# Кодовая строка (в кавычках)
RE_CODE_LINE = re.compile(r'^\t+[<>]?\s*"(.+)"$')


def parse_report(lines: list[str]) -> list[ModuleChanges]:
    """Парсит отчёт и возвращает список изменений по модулям."""
    results: list[ModuleChanges] = []

    current_object_type = None
    current_object_name = None
    current_form_name = None
    current_command_name = None
    current_module: Optional[ModuleChanges] = None
    in_module_section = False

    # Стек для отслеживания иерархии объектов
    object_stack = []  # [(indent_level, type, name)]

    for line in lines:
        # Пропускаем пустые строки
        if not line.strip():
            continue

        # Определяем уровень отступа
        indent = len(line) - len(line.lstrip('\t'))

        # Проверяем заголовок формы ПЕРЕД объектом (иначе RE_OBJECT_HEADER перехватит)
        m = RE_FORM_HEADER.match(line)
        if m:
            current_form_name = m.group(2)
            in_module_section = False
            continue

        # Проверяем заголовок команды (аналогично формам)
        m = RE_COMMAND_HEADER.match(line)
        if m:
            current_command_name = m.group(2)
            in_module_section = False
            continue

        # Проверяем заголовок объекта
        m = RE_OBJECT_HEADER.match(line)
        if m:
            obj_indent = len(m.group(1))
            obj_type = m.group(2)
            obj_name = m.group(3)

            # Убираем из стека объекты с тем же или большим отступом
            while object_stack and object_stack[-1][0] >= obj_indent:
                object_stack.pop()

            object_stack.append((obj_indent, obj_type, obj_name))

            # Обновляем текущий объект (берём объект верхнего уровня после Конфигурации)
            for _, otype, oname in object_stack:
                if otype != 'Конфигурация' and otype != 'Подсистема':
                    current_object_type = otype
                    current_object_name = oname
                    break

            current_form_name = None
            current_command_name = None
            in_module_section = False
            continue

        # Проверяем маркер модуля
        m = RE_MODULE_MARKER.match(line)
        if m:
            module_type = m.group(2)
            if current_form_name:
                module_type = f"Модуль формы {current_form_name}"
            elif current_command_name:
                module_type = f"Модуль команды {current_command_name}"

            if current_object_type and current_object_name:
                obj_path = f"{current_object_type}.{current_object_name}"
                current_module = ModuleChanges(
                    object_path=obj_path,
                    module_type=module_type,
                    form_name=current_form_name
                )
                results.append(current_module)
                in_module_section = True
            continue

        if not in_module_section or current_module is None:
            # Если строка не относится к секции модуля, проверяем не начался ли новый объект
            # (нет маркера модуля, но есть другие маркеры — сбрасываем)
            if line.strip().startswith('- ***') or line.strip().startswith('- -->') or line.strip().startswith('- <--'):
                # Это какой-то другой элемент (реквизит, ТЧ, движения и т.д.)
                # Проверяем не форма ли
                if '.Форма.' not in line:
                    # Если это не вложенный объект с модулем, сбрасываем секцию
                    pass
            continue

        # В секции модуля — ищем строки с изменениями
        m = RE_CHANGE_LINES.match(line)
        if m:
            current_module.line_ranges.append(
                LineRange(int(m.group(1)), int(m.group(2)), "changed")
            )
            continue

        m = RE_ADDED_LINES.match(line)
        if m:
            current_module.line_ranges.append(
                LineRange(int(m.group(1)), int(m.group(2)), "added")
            )
            continue

        m = RE_REMOVED_LINES.match(line)
        if m:
            current_module.line_ranges.append(
                LineRange(int(m.group(1)), int(m.group(2)), "removed")
            )
            continue

        # Ищем сигнатуры процедур/функций в кодовых строках
        m_code = RE_CODE_LINE.match(line)
        if m_code:
            code_text = m_code.group(1).replace('·', ' ')
            m_sig = RE_PROC_SIGNATURE.search(code_text)
            if m_sig:
                sig_type = m_sig.group(1)
                sig_name = m_sig.group(2)
                sig = f"{sig_type} {sig_name}"
                if sig not in current_module.found_signatures:
                    current_module.found_signatures.append(sig)

    return results


def format_line_ranges(ranges: list[LineRange]) -> str:
    """Форматирует диапазоны строк компактно."""
    parts = []
    for r in ranges:
        prefix = {"changed": "~", "added": "+", "removed": "-"}[r.change_type]
        if r.start == r.end:
            parts.append(f"{prefix}{r.start}")
        else:
            parts.append(f"{prefix}{r.start}-{r.end}")
    return ", ".join(parts)


def format_output(modules: list[ModuleChanges]) -> str:
    """Форматирует результат для вывода."""
    output_lines = []
    output_lines.append("# Реестр изменённых модулей")
    output_lines.append(f"# Модулей с изменениями: {len(modules)}")
    output_lines.append("#")
    output_lines.append("# Обозначения строк: + добавлено, ~ изменено, - удалено (из типовой)")
    output_lines.append("")

    # Группируем по объекту
    objects: dict[str, list[ModuleChanges]] = {}
    for m in modules:
        if m.object_path not in objects:
            objects[m.object_path] = []
        objects[m.object_path].append(m)

    for obj_path, obj_modules in objects.items():
        output_lines.append(f"## {obj_path}")
        for mod in obj_modules:
            lines_str = format_line_ranges(mod.line_ranges)
            output_lines.append(f"  {mod.module_type}")
            output_lines.append(f"    Строки: {lines_str}")

            if mod.found_signatures:
                output_lines.append(f"    Найденные сигнатуры: {'; '.join(mod.found_signatures)}")

        output_lines.append("")

    # Сводка
    total_added = sum(
        r.end - r.start + 1
        for m in modules for r in m.line_ranges if r.change_type == "added"
    )
    total_changed = sum(
        r.end - r.start + 1
        for m in modules for r in m.line_ranges if r.change_type == "changed"
    )
    total_removed = sum(
        r.end - r.start + 1
        for m in modules for r in m.line_ranges if r.change_type == "removed"
    )

    output_lines.append("---")
    output_lines.append(f"# Итого объектов: {len(objects)}")
    output_lines.append(f"# Итого модулей: {len(modules)}")
    output_lines.append(f"# Строк добавлено: ~{total_added}")
    output_lines.append(f"# Строк изменено: ~{total_changed}")
    output_lines.append(f"# Строк удалено (из типовой): ~{total_removed}")

    return "\n".join(output_lines)


def main():
    parser = argparse.ArgumentParser(
        description="Сокращение подробного отчёта о сравнении конфигураций 1С"
    )
    parser.add_argument("report", help="Путь к файлу отчёта о сравнении")
    parser.add_argument("-o", "--output", help="Путь к выходному файлу (по умолчанию stdout)")
    parser.add_argument("-e", "--encoding", default="utf-8-sig",
                        help="Кодировка входного файла (по умолчанию utf-8-sig)")
    args = parser.parse_args()

    # Пробуем несколько кодировок
    content = None
    for enc in [args.encoding, "utf-8-sig", "utf-8", "cp1251", "utf-16"]:
        try:
            with open(args.report, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if content is None:
        print(f"Ошибка: не удалось прочитать файл {args.report}", file=sys.stderr)
        sys.exit(1)

    lines = content.splitlines()
    modules = parse_report(lines)

    result = format_output(modules)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Результат записан в {args.output}")
        print(f"Найдено модулей: {len(modules)}")
    else:
        print(result)


if __name__ == "__main__":
    main()
