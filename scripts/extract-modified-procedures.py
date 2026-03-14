#!/usr/bin/env python3
"""
Извлечение доработанных процедур/функций из модулей 1С.

Читает сокращённый отчёт о сравнении (РеестрИзменений.txt),
находит соответствующие .bsl файлы в доработанной конфигурации,
определяет какие процедуры/функции содержат изменения,
и извлекает их в отдельные файлы.

Использование:
    python scripts/extract-modified-procedures.py <отчёт> [-c config.json] [--dry-run]
"""
import re
import os
import sys
import json
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Модель данных ───────────────────────────────────────────────────────────

@dataclass
class ProcedureInfo:
    """Информация о процедуре/функции в BSL-модуле."""
    name: str
    kind: str               # "Процедура" или "Функция"
    start_line: int         # первая строка (включая директивы/комментарии перед)
    end_line: int           # последняя строка (КонецПроцедуры/КонецФункции)
    decl_line: int          # строка с объявлением "Процедура X(" / "Функция X("
    is_export: bool = False
    context_directive: Optional[str] = None   # &НаСервере, &НаКлиенте и т.д.
    annotations: list = field(default_factory=list)  # &Перед, &После и т.д.


@dataclass
class LineRange:
    """Диапазон изменённых строк."""
    start: int
    end: int
    change_type: str  # "+", "~", "-"


@dataclass
class ModuleEntry:
    """Запись модуля из отчёта."""
    object_type: str        # ОбщийМодуль, Документ, Справочник и т.д.
    object_name: str
    module_type: str        # Модуль, Модуль объекта, Модуль менеджера, Модуль формы X
    form_name: Optional[str]
    command_name: Optional[str]
    line_ranges: list       # list[LineRange]
    report_signatures: list  # сигнатуры из отчёта


# ─── Маппинг типов объектов к каталогам ──────────────────────────────────────

OBJECT_TYPE_TO_DIR = {
    "ОбщийМодуль": "CommonModules",
    "Справочник": "Catalogs",
    "Документ": "Documents",
    "Перечисление": "Enums",
    "РегистрСведений": "InformationRegisters",
    "РегистрНакопления": "AccumulationRegisters",
    "РегистрБухгалтерии": "AccountingRegisters",
    "ПланВидовХарактеристик": "ChartsOfCharacteristicTypes",
    "ПланСчетов": "ChartsOfAccounts",
    "ПланОбмена": "ExchangePlans",
    "Обработка": "DataProcessors",
    "Отчет": "Reports",
    "БизнесПроцесс": "BusinessProcesses",
    "Задача": "Tasks",
    "Константа": "Constants",
    "ЖурналДокументов": "DocumentJournals",
    "РегистрРасчета": "CalculationRegisters",
    "ПланВидовРасчета": "ChartsOfCalculationTypes",
    "ОбщаяФорма": "CommonForms",
    "ОбщаяКоманда": "CommonCommands",
    "СервисИнтеграции": "IntegrationServices",
    "ХранилищеНастроек": "SettingsStorages",
}

MODULE_TYPE_TO_FILE = {
    "Модуль": "Ext/Module.bsl",
    "Модуль объекта": "Ext/ObjectModule.bsl",
    "Модуль менеджера": "Ext/ManagerModule.bsl",
    "Модуль набора записей": "Ext/RecordSetModule.bsl",
    "Модуль команды": "Ext/CommandModule.bsl",
}


# ─── Парсер отчёта ───────────────────────────────────────────────────────────

def parse_reduced_report(report_path: str) -> list[ModuleEntry]:
    """Парсит сокращённый отчёт о сравнении."""
    with open(report_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    entries = []
    current_obj_type = None
    current_obj_name = None

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # Заголовок объекта: ## ТипОбъекта.ИмяОбъекта
        m = re.match(r'^## (\S+)\.(.+)$', line)
        if m:
            current_obj_type = m.group(1)
            current_obj_name = m.group(2)
            i += 1
            continue

        # Тип модуля: "  Модуль объекта" / "  Модуль менеджера" / "  Модуль формы ИмяФормы"
        m = re.match(r'^  (Модуль(?:\s+объекта|\s+менеджера|\s+набора записей|\s+формы\s+\S+|\s+команды\s+\S+|\s+команды)?)$', line)
        if not m:
            m = re.match(r'^  (Модуль)$', line)
        if m and current_obj_type:
            module_type = m.group(1)
            form_name = None
            command_name = None
            fm = re.match(r'Модуль формы (.+)', module_type)
            if fm:
                form_name = fm.group(1)
            cm = re.match(r'Модуль команды (.+)', module_type)
            if cm:
                command_name = cm.group(1)

            # Следующая строка — "    Строки: ..."
            line_ranges = []
            report_sigs = []

            i += 1
            while i < len(lines):
                next_line = lines[i].rstrip()
                if next_line.startswith("    Строки: "):
                    ranges_str = next_line[len("    Строки: "):]
                    line_ranges = parse_line_ranges(ranges_str)
                elif next_line.startswith("    Найденные сигнатуры: "):
                    sigs_str = next_line[len("    Найденные сигнатуры: "):]
                    report_sigs = [s.strip() for s in sigs_str.split(";")]
                elif next_line.startswith("    "):
                    pass  # другие метаданные
                else:
                    break
                i += 1

            entries.append(ModuleEntry(
                object_type=current_obj_type,
                object_name=current_obj_name,
                module_type=module_type,
                form_name=form_name,
                command_name=command_name,
                line_ranges=line_ranges,
                report_signatures=report_sigs,
            ))
            continue

        i += 1

    return entries


def parse_line_ranges(s: str) -> list[LineRange]:
    """Парсит строку вида '+28-31, ~120, -77'."""
    ranges = []
    for part in s.split(", "):
        part = part.strip()
        if not part:
            continue
        change_type = part[0]  # +, ~, -
        rest = part[1:]
        m = re.match(r'(\d+)-(\d+)', rest)
        if m:
            ranges.append(LineRange(int(m.group(1)), int(m.group(2)), change_type))
        else:
            m = re.match(r'(\d+)', rest)
            if m:
                n = int(m.group(1))
                ranges.append(LineRange(n, n, change_type))
    return ranges


# ─── Парсер BSL-модулей ─────────────────────────────────────────────────────

# Регулярки для BSL
RE_PROC_START = re.compile(
    r'^(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z0-9_]+)\s*\(',
    re.IGNORECASE
)
RE_PROC_END = re.compile(
    r'^(КонецПроцедуры|КонецФункции)\s*(//.*)?$',
    re.IGNORECASE
)
RE_DIRECTIVE = re.compile(
    r'^&(НаСервере|НаКлиенте|НаСервереБезКонтекста|НаКлиентеНаСервереБезКонтекста|НаКлиентеНаСервере)',
    re.IGNORECASE
)
RE_ANNOTATION = re.compile(
    r'^&(Перед|После|Вместо|ИзменениеИКонтроль)\s*\(',
    re.IGNORECASE
)
RE_EXPORT = re.compile(r'\bЭкспорт\b', re.IGNORECASE)


def parse_bsl_procedures(file_path: str) -> list[ProcedureInfo]:
    """Парсит BSL-файл и возвращает список процедур/функций с их границами."""
    encodings = ["utf-8-sig", "utf-8", "cp1251"]
    content = None
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        return []

    lines = content.splitlines()
    procedures = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Ищем начало процедуры/функции
        m = RE_PROC_START.match(line)
        if m:
            kind = m.group(1)
            name = m.group(2)
            decl_line = i + 1  # 1-based

            # Ищем директивы и аннотации выше объявления
            prefix_start = i
            context_directive = None
            annotations = []

            # Сканируем вверх от объявления
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                if not prev or prev.startswith("//"):
                    # Пустая строка или комментарий — проверяем примыкает ли
                    if not prev:
                        break  # пустая строка = конец блока
                    # Комментарий примыкающий к процедуре
                    prefix_start = j
                    j -= 1
                    continue
                dm = RE_DIRECTIVE.match(prev)
                if dm:
                    context_directive = prev
                    prefix_start = j
                    j -= 1
                    continue
                am = RE_ANNOTATION.match(prev)
                if am:
                    annotations.insert(0, prev)
                    prefix_start = j
                    j -= 1
                    continue
                break

            start_line = prefix_start + 1  # 1-based

            # Проверяем Экспорт в строке объявления (может быть на следующих строках)
            is_export = False
            # Ищем закрывающую скобку и Экспорт
            k = i
            while k < len(lines):
                if RE_EXPORT.search(lines[k]):
                    is_export = True
                    break
                if ")" in lines[k]:
                    # Проверяем и эту строку и следующую (Экспорт может быть после ")")
                    if RE_EXPORT.search(lines[k]):
                        is_export = True
                    elif k + 1 < len(lines) and RE_EXPORT.search(lines[k + 1]):
                        is_export = True
                    break
                k += 1

            # Ищем КонецПроцедуры/КонецФункции
            end_line = None
            depth = 0
            for k in range(i + 1, len(lines)):
                kline = lines[k].strip()
                # Вложенные процедуры в BSL невозможны, ищем просто КонецПроцедуры/КонецФункции
                if RE_PROC_END.match(kline):
                    end_line = k + 1  # 1-based
                    break

            if end_line is None:
                # Не нашли конец — берём до конца файла
                end_line = len(lines)

            procedures.append(ProcedureInfo(
                name=name,
                kind=kind.capitalize() if kind[0] in 'пП' else kind.capitalize(),
                start_line=start_line,
                end_line=end_line,
                decl_line=decl_line,
                is_export=is_export,
                context_directive=context_directive,
                annotations=annotations,
            ))

            # Переходим к строке после КонецПроцедуры
            i = end_line  # 0-based = end_line (т.к. end_line 1-based)
            continue

        i += 1

    return procedures


# ─── Резолвер путей к файлам ─────────────────────────────────────────────────

def resolve_bsl_path(config_path: str, entry: ModuleEntry) -> Optional[str]:
    """Определяет путь к .bsl файлу по записи из отчёта."""
    obj_dir = OBJECT_TYPE_TO_DIR.get(entry.object_type)
    if not obj_dir:
        return None

    base = Path(config_path) / obj_dir / entry.object_name

    if entry.form_name:
        # Модуль формы
        bsl = base / "Forms" / entry.form_name / "Ext" / "Form" / "Module.bsl"
    elif entry.command_name:
        # Модуль команды объекта
        bsl = base / "Commands" / entry.command_name / "Ext" / "CommandModule.bsl"
    elif entry.object_type == "ОбщийМодуль":
        bsl = base / "Ext" / "Module.bsl"
    elif entry.object_type == "ОбщаяФорма":
        # Общая форма — модуль внутри Ext/Form/
        bsl = base / "Ext" / "Form" / "Module.bsl"
    elif entry.object_type == "ОбщаяКоманда":
        bsl = base / "Ext" / "CommandModule.bsl"
    else:
        module_file = MODULE_TYPE_TO_FILE.get(entry.module_type)
        if module_file:
            bsl = base / module_file
        else:
            # Fallback
            bsl = base / "Ext" / "Module.bsl"

    return str(bsl) if bsl.exists() else None


# ─── Определение затронутых процедур ─────────────────────────────────────────

def find_affected_procedures(
    procedures: list[ProcedureInfo],
    line_ranges: list[LineRange]
) -> list[ProcedureInfo]:
    """Находит процедуры, содержащие изменённые строки."""
    affected = []
    for proc in procedures:
        for lr in line_ranges:
            # Проверяем пересечение диапазонов
            if lr.start <= proc.end_line and lr.end >= proc.start_line:
                if proc not in affected:
                    affected.append(proc)
                break
    return affected


# ─── Извлечение процедур в файлы ────────────────────────────────────────────

def read_bsl_lines(bsl_path: str) -> Optional[list[str]]:
    """Читает BSL-файл, возвращает список строк или None."""
    encodings = ["utf-8-sig", "utf-8", "cp1251"]
    for enc in encodings:
        try:
            with open(bsl_path, "r", encoding=enc) as f:
                return f.readlines()
        except UnicodeDecodeError:
            continue
    return None


def extract_procedure_to_file(
    bsl_path: str,
    proc: ProcedureInfo,
    output_dir: str,
    suffix: str = ""
) -> str:
    """Извлекает процедуру из BSL-файла в отдельный файл с суффиксом."""
    content = read_bsl_lines(bsl_path)
    if content is None:
        return ""

    # Извлекаем строки процедуры (1-based → 0-based)
    proc_lines = content[proc.start_line - 1 : proc.end_line]
    proc_text = "".join(proc_lines)

    # Создаём выходной файл
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{proc.name}{suffix}.bsl")
    with open(out_path, "w", encoding="utf-8-sig") as f:
        f.write(proc_text)

    return out_path


def find_procedure_by_name(procedures: list[ProcedureInfo], name: str) -> Optional[ProcedureInfo]:
    """Ищет процедуру по имени (без учёта регистра)."""
    name_lower = name.lower()
    for proc in procedures:
        if proc.name.lower() == name_lower:
            return proc
    return None


# ─── Главная логика ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Извлечение доработанных процедур из модулей 1С"
    )
    parser.add_argument("report", help="Путь к сокращённому отчёту (РеестрИзменений.txt)")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет извлечено, без создания файлов")
    parser.add_argument("-o", "--output", help="Путь к файлу итогового отчёта")
    args = parser.parse_args()

    # Читаем конфиг
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    custom_config_path = config["customConfigPath"]
    base_config_path = config.get("baseConfigPath", "")
    work_dir = config.get("workDir", "work")

    if not os.path.isdir(custom_config_path):
        print(f"Ошибка: каталог доработанной конфигурации не найден: {custom_config_path}",
              file=sys.stderr)
        sys.exit(1)

    has_base = bool(base_config_path) and os.path.isdir(base_config_path)
    if not has_base:
        print("Предупреждение: типовая конфигурация не указана или не найдена, "
              "классификация new/mod недоступна", file=sys.stderr)

    # Парсим отчёт
    entries = parse_reduced_report(args.report)
    print(f"Прочитано записей из отчёта: {len(entries)}")

    # Обрабатываем каждый модуль
    report_lines = []
    report_lines.append("# Реестр доработанных процедур и функций")
    report_lines.append(f"# Доработанная: {custom_config_path}")
    if has_base:
        report_lines.append(f"# Типовая: {base_config_path}")
    report_lines.append("#")
    report_lines.append("# Суффиксы файлов: _mod = изменённая, _typ = типовая версия, _new = новая")
    report_lines.append("")

    total_procedures = 0
    total_modified = 0
    total_new = 0
    total_modules = 0
    total_files_created = 0

    # Кэш распарсенных модулей типовой конфигурации
    base_procedures_cache: dict[str, list[ProcedureInfo]] = {}

    for entry in entries:
        bsl_path = resolve_bsl_path(custom_config_path, entry)

        module_label = f"{entry.object_type}.{entry.object_name}"
        if entry.form_name:
            module_label += f".Форма.{entry.form_name}"
        elif entry.command_name:
            module_label += f".Команда.{entry.command_name}"

        if bsl_path is None:
            report_lines.append(f"## {module_label} — {entry.module_type}")
            report_lines.append(f"  ОШИБКА: файл не найден в доработанной конфигурации")
            report_lines.append("")
            continue

        # Парсим BSL-файл доработанной конфигурации
        procedures = parse_bsl_procedures(bsl_path)

        if not procedures:
            report_lines.append(f"## {module_label} — {entry.module_type}")
            report_lines.append(f"  ОШИБКА: не удалось распарсить процедуры в {bsl_path}")
            report_lines.append("")
            continue

        # Находим затронутые процедуры
        affected = find_affected_procedures(procedures, entry.line_ranges)

        if not affected:
            report_lines.append(f"## {module_label} — {entry.module_type}")
            report_lines.append(f"  Изменения вне процедур (область инициализации модуля)")
            report_lines.append(f"  Строки: {format_ranges(entry.line_ranges)}")
            report_lines.append("")
            continue

        total_modules += 1

        # Резолвим типовой модуль и парсим его процедуры
        base_bsl_path = None
        base_procs: list[ProcedureInfo] = []
        if has_base:
            base_bsl_path = resolve_bsl_path(base_config_path, entry)
            if base_bsl_path:
                if base_bsl_path not in base_procedures_cache:
                    base_procedures_cache[base_bsl_path] = parse_bsl_procedures(base_bsl_path)
                base_procs = base_procedures_cache[base_bsl_path]

        # Формируем каталог для извлечённых процедур
        module_dir_name = entry.module_type.replace(" ", "_")
        if entry.form_name:
            out_subdir = os.path.join(
                work_dir, entry.object_type, entry.object_name,
                "Forms", entry.form_name
            )
        elif entry.command_name:
            out_subdir = os.path.join(
                work_dir, entry.object_type, entry.object_name,
                "Commands", entry.command_name
            )
        else:
            out_subdir = os.path.join(
                work_dir, entry.object_type, entry.object_name,
                module_dir_name
            )

        report_lines.append(f"## {module_label} — {entry.module_type}")
        report_lines.append(f"  Файл (mod): {bsl_path}")
        if base_bsl_path:
            report_lines.append(f"  Файл (typ): {base_bsl_path}")
        report_lines.append(f"  Всего процедур в модуле: {len(procedures)}")
        report_lines.append(f"  Доработанных: {len(affected)}")
        report_lines.append(f"  Каталог: {out_subdir}")

        for proc in affected:
            # Определяем какие именно строки изменены в этой процедуре
            proc_changes = []
            for lr in entry.line_ranges:
                if lr.start <= proc.end_line and lr.end >= proc.start_line:
                    proc_changes.append(lr)

            changes_str = format_ranges(proc_changes)
            export_str = " Экспорт" if proc.is_export else ""
            ctx_str = f" [{proc.context_directive}]" if proc.context_directive else ""

            # Классификация: новая или изменённая
            base_proc = find_procedure_by_name(base_procs, proc.name) if has_base else None
            if has_base:
                if base_proc:
                    classification = "mod"
                    class_label = "изменённая"
                    total_modified += 1
                else:
                    classification = "new"
                    class_label = "новая"
                    total_new += 1
            else:
                classification = "mod"  # без типовой считаем всё изменённым
                class_label = "?"
                total_modified += 1

            report_lines.append(
                f"  - [{class_label}] {proc.kind} {proc.name}{export_str}{ctx_str}"
                f"  (строки {proc.start_line}-{proc.end_line}, изменения: {changes_str})"
            )

            total_procedures += 1

            # Извлекаем в файлы
            if not args.dry_run:
                if classification == "new":
                    # Новая процедура — один файл с суффиксом _new
                    out = extract_procedure_to_file(bsl_path, proc, out_subdir, "_new")
                    if out:
                        total_files_created += 1
                else:
                    # Изменённая — два файла: _mod (доработанная) и _typ (типовая)
                    out = extract_procedure_to_file(bsl_path, proc, out_subdir, "_mod")
                    if out:
                        total_files_created += 1
                    if base_proc and base_bsl_path:
                        out = extract_procedure_to_file(base_bsl_path, base_proc, out_subdir, "_typ")
                        if out:
                            total_files_created += 1

        report_lines.append("")

    # Сводка
    report_lines.append("---")
    report_lines.append(f"# Итого модулей с доработками: {total_modules}")
    report_lines.append(f"# Итого доработанных процедур/функций: {total_procedures}")
    if has_base:
        report_lines.append(f"#   изменённых (mod): {total_modified}")
        report_lines.append(f"#   новых (new): {total_new}")
    if not args.dry_run:
        report_lines.append(f"# Создано файлов: {total_files_created}")

    report_text = "\n".join(report_lines)

    # Выводим / сохраняем
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"Отчёт сохранён: {args.output}")
    else:
        print(report_text)

    print(f"\nМодулей: {total_modules}, процедур: {total_procedures} "
          f"(mod: {total_modified}, new: {total_new})", end="")
    if not args.dry_run:
        print(f", файлов создано: {total_files_created}")
    else:
        print(" (dry-run, файлы не создавались)")


def format_ranges(ranges: list[LineRange]) -> str:
    """Форматирует диапазоны компактно."""
    parts = []
    for r in ranges:
        if r.start == r.end:
            parts.append(f"{r.change_type}{r.start}")
        else:
            parts.append(f"{r.change_type}{r.start}-{r.end}")
    return ", ".join(parts)


if __name__ == "__main__":
    main()
