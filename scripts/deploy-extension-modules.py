#!/usr/bin/env python3
"""
Развёртывание собранных модулей расширения в каталог расширения.

Читает assembly-report.json из рабочего каталога, определяет целевые пути
в структуре расширения 1С и копирует _module.bsl файлы с правильной
структурой каталогов и именами файлов.

Это шаг 6 пайплайна миграции:
  reduce → extract → generate → assemble → ИИ-ревью → **deploy**

Использование:
    python scripts/deploy-extension-modules.py <work_dir> [-c config.json] [--dry-run]
    python scripts/deploy-extension-modules.py work --report deploy-report.txt
"""
import os
import sys
import json
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ─── Маппинг типов объектов (русский → английский каталог) ───────────────────

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

# ─── Маппинг типов модулей (имя каталога → путь файла в расширении) ──────────

MODULE_DIR_TO_FILE = {
    "Модуль": os.path.join("Ext", "Module.bsl"),
    "Модуль_объекта": os.path.join("Ext", "ObjectModule.bsl"),
    "Модуль_менеджера": os.path.join("Ext", "ManagerModule.bsl"),
    "Модуль_набора_записей": os.path.join("Ext", "RecordSetModule.bsl"),
    "Модуль_команды": os.path.join("Ext", "CommandModule.bsl"),
}


# ─── Модель данных ───────────────────────────────────────────────────────────

@dataclass
class DeployEntry:
    """Одна запись о развёртывании модуля."""
    rel_path: str           # исходный относительный путь (из assembly-report)
    source_file: str        # абсолютный путь к _module.bsl
    target_file: str        # абсолютный путь в каталоге расширения
    deployed: bool = False  # успешно скопирован
    error: Optional[str] = None


# ─── Чтение и запись BSL-файлов ──────────────────────────────────────────────

def read_bsl(path: str) -> str:
    """Читает BSL-файл, возвращает содержимое как строку."""
    for enc in ["utf-8-sig", "utf-8", "cp1251"]:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось прочитать файл: {path}")


def write_bsl(path: str, content: str):
    """Записывает BSL-файл с BOM и CRLF."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(content)


# ─── Определение целевого пути ───────────────────────────────────────────────

def resolve_target_path(rel_path: str, ext_dir: str) -> str:
    """Определяет целевой путь в каталоге расширения по относительному пути модуля.

    Args:
        rel_path: относительный путь вида "Документ\\ВозвратТоваровОтКлиента\\Модуль_объекта"
        ext_dir: абсолютный путь к каталогу расширения

    Returns:
        Абсолютный путь к целевому BSL-файлу

    Raises:
        ValueError: если тип объекта или модуля не распознан
    """
    # Нормализуем разделители и разбиваем
    parts = rel_path.replace("\\", "/").split("/")

    if len(parts) < 3:
        raise ValueError(f"Недостаточно частей в пути: {rel_path}")

    object_type_ru = parts[0]
    object_name = parts[1]

    # Маппинг типа объекта
    english_type = OBJECT_TYPE_TO_DIR.get(object_type_ru)
    if english_type is None:
        raise ValueError(f"Неизвестный тип объекта: {object_type_ru}")

    # Формы — особый случай
    if parts[2] == "Forms":
        if len(parts) < 4:
            raise ValueError(f"Неполный путь формы: {rel_path}")
        form_name = parts[3]
        target_rel = os.path.join(english_type, object_name, "Forms", form_name,
                                  "Ext", "Form", "Module.bsl")
    elif parts[2] == "Commands":
        # Команды объекта — аналогично формам
        if len(parts) < 4:
            raise ValueError(f"Неполный путь команды: {rel_path}")
        command_name = parts[3]
        target_rel = os.path.join(english_type, object_name, "Commands", command_name,
                                  "Ext", "CommandModule.bsl")
    elif object_type_ru == "ОбщаяФорма":
        # Общая форма — модуль внутри Ext/Form/
        target_rel = os.path.join(english_type, object_name,
                                  "Ext", "Form", "Module.bsl")
    else:
        # Обычный модуль
        module_type = parts[2]
        module_file = MODULE_DIR_TO_FILE.get(module_type)
        if module_file is None:
            raise ValueError(f"Неизвестный тип модуля: {module_type}")
        target_rel = os.path.join(english_type, object_name, module_file)

    return os.path.join(ext_dir, target_rel)


# ─── Развёртывание ───────────────────────────────────────────────────────────

def deploy_modules(entries: list[DeployEntry], dry_run: bool = False) -> list[DeployEntry]:
    """Копирует _module.bsl файлы в каталог расширения.

    Читает через read_bsl (с определением кодировки), записывает через write_bsl
    (UTF-8 BOM + CRLF).
    """
    for entry in entries:
        try:
            if not os.path.isfile(entry.source_file):
                entry.error = f"Файл не найден: {entry.source_file}"
                continue

            if dry_run:
                entry.deployed = True
                continue

            content = read_bsl(entry.source_file)
            write_bsl(entry.target_file, content)
            entry.deployed = True

        except Exception as e:
            entry.error = str(e)

    return entries


# ─── Отчёт ──────────────────────────────────────────────────────────────────

def print_report(entries: list[DeployEntry], dry_run: bool,
                 report_path: Optional[str] = None):
    """Выводит текстовый отчёт о развёртывании."""
    lines = []
    lines.append("# Отчёт развёртывания модулей расширения")
    if dry_run:
        lines.append("(dry-run — файлы не записывались)")
    lines.append("")

    deployed = [e for e in entries if e.deployed]
    failed = [e for e in entries if e.error]

    lines.append(f"Всего модулей: {len(entries)}")
    lines.append(f"Развёрнуто: {len(deployed)}")
    if failed:
        lines.append(f"Ошибок: {len(failed)}")
    lines.append("")

    for entry in entries:
        status = "OK" if entry.deployed else "ОШИБКА"
        lines.append(f"## {entry.rel_path}  [{status}]")
        lines.append(f"  Источник: {entry.source_file}")
        lines.append(f"  Цель:     {entry.target_file}")
        if entry.error:
            lines.append(f"  Ошибка:   {entry.error}")
        lines.append("")

    report_text = "\n".join(lines)

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"Отчёт сохранён: {report_path}")
    else:
        print(report_text)


def save_json_report(entries: list[DeployEntry], json_path: str):
    """Сохраняет JSON-отчёт о развёртывании."""
    deployed = [e for e in entries if e.deployed]
    failed = [e for e in entries if e.error]

    report = {
        "total_modules": len(entries),
        "deployed": len(deployed),
        "failed": len(failed),
        "modules": []
    }

    for entry in entries:
        module_entry = {
            "path": entry.rel_path,
            "source_file": entry.source_file,
            "target_file": entry.target_file,
            "deployed": entry.deployed,
        }
        if entry.error:
            module_entry["error"] = entry.error
        report["modules"].append(module_entry)

    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON-отчёт: {json_path}")


# ─── Главная логика ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Развёртывание собранных модулей расширения в каталог расширения"
    )
    parser.add_argument("work_dir", help="Рабочий каталог с собранными модулями")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет развёрнуто, без записи файлов")
    parser.add_argument("--report", help="Путь к файлу текстового отчёта")
    args = parser.parse_args()

    if not os.path.isdir(args.work_dir):
        print(f"Ошибка: каталог не найден: {args.work_dir}", file=sys.stderr)
        sys.exit(1)

    # Читаем конфиг — берём путь к каталогу расширения
    ext_output = "extension"
    if os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        ext_output = config.get("extensionOutputPath", ext_output)
    else:
        print(f"Предупреждение: конфиг {args.config} не найден, "
              f"используется каталог '{ext_output}'", file=sys.stderr)

    # Делаем ext_output абсолютным путём
    if not os.path.isabs(ext_output):
        ext_output = os.path.abspath(ext_output)

    # Читаем assembly-report.json
    assembly_report_path = os.path.join(args.work_dir, "assembly-report.json")
    if not os.path.isfile(assembly_report_path):
        print(f"Ошибка: отчёт сборки не найден: {assembly_report_path}", file=sys.stderr)
        sys.exit(1)

    with open(assembly_report_path, "r", encoding="utf-8") as f:
        assembly_report = json.load(f)

    modules = assembly_report.get("modules", [])
    if not modules:
        print("Нечего развёртывать: отчёт сборки пуст.")
        return

    print(f"Каталог расширения: {ext_output}")
    print(f"Модулей в отчёте сборки: {len(modules)}")

    # Строим список записей для развёртывания
    entries: list[DeployEntry] = []
    skipped = 0

    for module in modules:
        rel_path = module.get("path", "")
        output_file = module.get("output_file", "")

        if not rel_path or not output_file:
            skipped += 1
            continue

        # Абсолютный путь к исходному файлу
        source_file = output_file
        if not os.path.isabs(source_file):
            source_file = os.path.abspath(source_file)

        try:
            target_file = resolve_target_path(rel_path, ext_output)
        except ValueError as e:
            entries.append(DeployEntry(
                rel_path=rel_path,
                source_file=source_file,
                target_file="",
                error=str(e),
            ))
            continue

        entries.append(DeployEntry(
            rel_path=rel_path,
            source_file=source_file,
            target_file=target_file,
        ))

    if skipped:
        print(f"Пропущено записей (неполные данные): {skipped}", file=sys.stderr)

    if not entries:
        print("Нечего развёртывать.")
        return

    # Развёртываем
    deploy_modules(entries, dry_run=args.dry_run)

    # Вывод отчёта
    print_report(entries, dry_run=args.dry_run, report_path=args.report)

    # JSON-отчёт
    json_report_path = os.path.join(args.work_dir, "deploy-report.json")
    save_json_report(entries, json_report_path)

    # Итоговая статистика
    deployed = sum(1 for e in entries if e.deployed)
    failed = sum(1 for e in entries if e.error)
    if failed:
        print(f"\nРазвёрнуто: {deployed}, ошибок: {failed}")
        sys.exit(1)
    else:
        print(f"\nУспешно развёрнуто: {deployed} модулей")


if __name__ == "__main__":
    main()
