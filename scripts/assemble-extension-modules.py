#!/usr/bin/env python3
"""
Сборка модулей расширения из отдельных процедур.

Сканирует рабочий каталог, находит файлы _ext.bsl и _new.bsl,
собирает их в единые _module.bsl для каждого модуля.
Новые процедуры (_new) автоматически переименовываются с добавлением
префикса расширения, ссылки обновляются во всех телах процедур.

Это шаг 4 пайплайна миграции:
  reduce → extract → generate → **assemble** → ИИ-ревью

Использование:
    python scripts/assemble-extension-modules.py <work_dir> [-c config.json] [--dry-run]
    python scripts/assemble-extension-modules.py work --report assembly-report.txt
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
class ModuleDir:
    """Каталог модуля с файлами процедур."""
    path: str                   # абсолютный путь к каталогу
    rel_path: str               # относительный путь от work_dir
    ext_files: list[str] = field(default_factory=list)   # *_ext.bsl
    new_files: list[str] = field(default_factory=list)   # *_new.bsl


@dataclass
class ModuleResult:
    """Результат сборки одного модуля."""
    rel_path: str
    ext_count: int
    new_count: int
    renames: dict               # {OldName: NewName}
    output_file: Optional[str] = None


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


# ─── Парсинг имён процедур ──────────────────────────────────────────────────

RE_PROC_DECL = re.compile(
    r'^(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z0-9_]+)\s*\(',
    re.IGNORECASE | re.MULTILINE
)


def parse_proc_name(content: str) -> Optional[tuple[str, str]]:
    """Извлекает тип (Процедура/Функция) и имя из содержимого BSL-файла.

    Возвращает (kind, name) или None.
    """
    m = RE_PROC_DECL.search(content)
    if m:
        return m.group(1), m.group(2)
    return None


# ─── Переименование процедур ────────────────────────────────────────────────

def build_rename_map(new_contents: dict[str, str], prefix: str) -> dict[str, str]:
    """Строит карту переименований для новых процедур без префикса.

    Args:
        new_contents: {filename: content} для _new.bsl файлов
        prefix: префикс расширения (например "ЕТ")

    Returns:
        {OldName: NewName} — только для процедур, которые нужно переименовать
    """
    rename_map: dict[str, str] = {}
    prefix_with_underscore = prefix + "_"

    for filename, content in new_contents.items():
        parsed = parse_proc_name(content)
        if parsed is None:
            continue
        kind, name = parsed
        if not name.startswith(prefix_with_underscore):
            new_name = prefix_with_underscore + name
            rename_map[name] = new_name

    return rename_map


def rename_declaration(content: str, old_name: str, new_name: str) -> str:
    """Переименовывает объявление процедуры/функции в содержимом."""
    content = content.replace(f'Процедура {old_name}(', f'Процедура {new_name}(', 1)
    content = content.replace(f'Функция {old_name}(', f'Функция {new_name}(', 1)
    # На случай другого регистра ключевых слов
    content = content.replace(f'процедура {old_name}(', f'процедура {new_name}(', 1)
    content = content.replace(f'функция {old_name}(', f'функция {new_name}(', 1)
    return content


def apply_renames(content: str, rename_map: dict[str, str]) -> str:
    """Применяет все переименования к содержимому с учётом границ слов BSL.

    Пропускает строки с &ИзменениеИКонтроль (декораторы ссылаются на оригинальные имена).
    """
    if not rename_map:
        return content

    lines = content.split('\n')
    result_lines = []

    for line in lines:
        stripped = line.strip()
        # Не трогаем строки с декораторами перехвата
        if stripped.startswith('&ИзменениеИКонтроль'):
            result_lines.append(line)
            continue

        for old_name, new_name in rename_map.items():
            pattern = re.compile(
                r'(?<![А-Яа-яЁёA-Za-z0-9_])' + re.escape(old_name) + r'(?![А-Яа-яЁёA-Za-z0-9_])'
            )
            line = pattern.sub(new_name, line)
        result_lines.append(line)

    return '\n'.join(result_lines)


# ─── Сканирование рабочего каталога ──────────────────────────────────────────

def scan_module_dirs(work_dir: str) -> list[ModuleDir]:
    """Сканирует work_dir и находит каталоги модулей с _ext.bsl и/или _new.bsl."""
    work_path = Path(work_dir)
    dirs_map: dict[str, ModuleDir] = {}

    for bsl_file in sorted(work_path.rglob("*.bsl")):
        name = bsl_file.name
        # Пропускаем наш выходной файл и исходные файлы
        if name == "_module.bsl":
            continue
        if name.endswith("_typ.bsl") or name.endswith("_mod.bsl"):
            continue

        dir_path = str(bsl_file.parent)

        if name.endswith("_ext.bsl") or name.endswith("_new.bsl"):
            if dir_path not in dirs_map:
                try:
                    rel = str(bsl_file.parent.relative_to(work_path))
                except ValueError:
                    rel = dir_path
                dirs_map[dir_path] = ModuleDir(
                    path=dir_path,
                    rel_path=rel,
                )

            md = dirs_map[dir_path]
            if name.endswith("_ext.bsl"):
                md.ext_files.append(str(bsl_file))
            else:
                md.new_files.append(str(bsl_file))

    # Сортируем файлы внутри каждого каталога
    for md in dirs_map.values():
        md.ext_files.sort(key=lambda p: Path(p).name)
        md.new_files.sort(key=lambda p: Path(p).name)

    return sorted(dirs_map.values(), key=lambda m: m.rel_path)


# ─── Сборка одного модуля ───────────────────────────────────────────────────

def assemble_module(module_dir: ModuleDir, prefix: str, dry_run: bool = False) -> ModuleResult:
    """Собирает все процедуры каталога в один _module.bsl.

    1. Читает _ext и _new файлы
    2. Строит карту переименований для _new
    3. Переименовывает объявления в _new
    4. Применяет переименования ко всем телам
    5. Конкатенирует и записывает _module.bsl
    """
    # 1. Читаем содержимое файлов
    ext_contents: dict[str, str] = {}
    for f in module_dir.ext_files:
        ext_contents[Path(f).name] = read_bsl(f)

    new_contents: dict[str, str] = {}
    for f in module_dir.new_files:
        new_contents[Path(f).name] = read_bsl(f)

    # 2. Строим карту переименований
    rename_map = build_rename_map(new_contents, prefix)

    # 3. Переименовываем объявления в _new
    for filename in list(new_contents.keys()):
        content = new_contents[filename]
        parsed = parse_proc_name(content)
        if parsed and parsed[1] in rename_map:
            old_name = parsed[1]
            new_name = rename_map[old_name]
            content = rename_declaration(content, old_name, new_name)
            new_contents[filename] = content

    # 4. Применяем переименования ко всем телам (ext + new)
    if rename_map:
        for filename in list(ext_contents.keys()):
            ext_contents[filename] = apply_renames(ext_contents[filename], rename_map)
        for filename in list(new_contents.keys()):
            new_contents[filename] = apply_renames(new_contents[filename], rename_map)

    # 5. Собираем итоговое содержимое
    parts: list[str] = []

    # Сначала _ext (отсортированы при сканировании)
    for filename in sorted(ext_contents.keys()):
        parts.append(ext_contents[filename].rstrip())

    # Затем _new
    for filename in sorted(new_contents.keys()):
        parts.append(new_contents[filename].rstrip())

    assembled = "\n\n".join(parts)

    # 6. Записываем
    output_file = os.path.join(module_dir.path, "_module.bsl")
    if not dry_run:
        write_bsl(output_file, assembled)

    return ModuleResult(
        rel_path=module_dir.rel_path,
        ext_count=len(ext_contents),
        new_count=len(new_contents),
        renames=rename_map,
        output_file=output_file,
    )


# ─── Отчёт ──────────────────────────────────────────────────────────────────

def print_report(results: list[ModuleResult], report_path: Optional[str] = None):
    """Выводит текстовый отчёт о сборке."""
    lines = []
    lines.append("# Отчёт сборки модулей расширения")
    lines.append("")

    total_ext = sum(r.ext_count for r in results)
    total_new = sum(r.new_count for r in results)
    total_renamed = sum(len(r.renames) for r in results)

    lines.append(f"Всего модулей: {len(results)}")
    lines.append(f"Всего процедур: {total_ext + total_new} ({total_ext} _ext + {total_new} _new)")
    lines.append(f"Переименовано процедур: {total_renamed}")
    lines.append("")

    for r in results:
        lines.append(f"## {r.rel_path}")
        rename_info = f", переименовано: {len(r.renames)}" if r.renames else ""
        lines.append(f"  _ext: {r.ext_count}, _new: {r.new_count}{rename_info}")
        if r.renames:
            lines.append("  Переименования:")
            for old_name, new_name in sorted(r.renames.items()):
                lines.append(f"    {old_name} -> {new_name}")
        if r.output_file:
            lines.append(f"  Записан: {r.output_file}")
        lines.append("")

    report_text = "\n".join(lines)

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"Отчёт сохранён: {report_path}")
    else:
        print(report_text)


def save_json_report(results: list[ModuleResult], json_path: str):
    """Сохраняет JSON-отчёт о сборке."""
    total_ext = sum(r.ext_count for r in results)
    total_new = sum(r.new_count for r in results)
    total_renamed = sum(len(r.renames) for r in results)

    report = {
        "total_modules": len(results),
        "total_ext": total_ext,
        "total_new": total_new,
        "total_renamed": total_renamed,
        "modules": []
    }

    for r in results:
        entry = {
            "path": r.rel_path,
            "ext_count": r.ext_count,
            "new_count": r.new_count,
            "renames": r.renames,
            "output_file": r.output_file,
        }
        report["modules"].append(entry)

    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON-отчёт: {json_path}")


# ─── Главная логика ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Сборка модулей расширения из отдельных процедур (_ext + _new → _module)"
    )
    parser.add_argument("work_dir", help="Рабочий каталог с извлечёнными процедурами")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет собрано, без записи файлов")
    parser.add_argument("--report", help="Путь к файлу текстового отчёта")
    args = parser.parse_args()

    if not os.path.isdir(args.work_dir):
        print(f"Ошибка: каталог не найден: {args.work_dir}", file=sys.stderr)
        sys.exit(1)

    # Читаем конфиг — берём префикс расширения
    prefix = "Расш"
    if os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        prefix = config.get("extensionPrefix", prefix)
    else:
        print(f"Предупреждение: конфиг {args.config} не найден, используется префикс '{prefix}'",
              file=sys.stderr)

    # Сканируем каталоги модулей
    module_dirs = scan_module_dirs(args.work_dir)
    print(f"Найдено модулей: {len(module_dirs)}")

    if not module_dirs:
        print("Нечего собирать.")
        return

    # Собираем каждый модуль
    results: list[ModuleResult] = []
    for md in module_dirs:
        try:
            result = assemble_module(md, prefix, dry_run=args.dry_run)
            results.append(result)
        except Exception as e:
            print(f"Ошибка при сборке {md.rel_path}: {e}", file=sys.stderr)

    # Вывод отчёта
    print_report(results, args.report)

    # JSON-отчёт
    json_report_path = os.path.join(args.work_dir, "assembly-report.json")
    save_json_report(results, json_report_path)


if __name__ == "__main__":
    main()
