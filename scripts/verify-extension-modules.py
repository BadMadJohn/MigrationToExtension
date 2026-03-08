#!/usr/bin/env python3
"""
Программная верификация собранных модулей расширения.

Проверяет модули расширения по 5 типам проверок:
1. Инвариант — снятие маркеров из _ext должно давать _typ
2. Префикс — новые процедуры начинаются с префикса расширения
3. Орфанные ссылки — старые имена не остались в модулях после переименования
4. Структура BSL — парность Процедура/КонецПроцедуры, маркеров
5. Перекрёстные ссылки — вызовы процедур из других модулей (информационно)

Это шаг 5 пайплайна миграции:
  reduce → extract → generate → assemble → **verify**

Использование:
    python scripts/verify-extension-modules.py <work_dir> [-c config.json] [--report verify-report.txt]
    python scripts/verify-extension-modules.py work --json
"""
import re
import os
import sys
import json
import argparse
import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Чтение BSL-файлов ──────────────────────────────────────────────────────

def read_bsl(path: str) -> list[str]:
    """Читает BSL-файл, возвращает список строк БЕЗ переводов строк."""
    for enc in ["utf-8-sig", "utf-8", "cp1251"]:
        try:
            with open(path, "r", encoding=enc) as f:
                return [line.rstrip("\n").rstrip("\r") for line in f.readlines()]
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось прочитать файл: {path}")


# ─── Парсинг заголовков процедур ─────────────────────────────────────────────

RE_PROC_DECL = re.compile(
    r'^(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z0-9_]+)\s*\(',
    re.IGNORECASE
)
RE_PROC_END = re.compile(
    r'^(КонецПроцедуры|КонецФункции)\s*(//.*)?$',
    re.IGNORECASE
)
RE_CHANGE_CONTROL = re.compile(
    r'^&ИзменениеИКонтроль\s*\(',
    re.IGNORECASE
)


def extract_body(lines: list[str]) -> list[str]:
    """Извлекает тело процедуры (между объявлением и КонецПроцедуры/КонецФункции)."""
    start = None
    end = None

    paren_depth = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            pm = RE_PROC_DECL.match(stripped)
            if pm:
                paren_depth += line.count("(") - line.count(")")
                if paren_depth <= 0:
                    start = i + 1
                continue
        if start is None and paren_depth > 0:
            paren_depth += line.count("(") - line.count(")")
            if paren_depth <= 0:
                start = i + 1
            continue
        if start is not None:
            em = RE_PROC_END.match(stripped)
            if em:
                end = i
                break

    if start is None:
        return lines  # fallback
    if end is None:
        end = len(lines)

    return lines[start:end]


def strip_markers(lines: list[str]) -> list[str]:
    """Убирает все блоки #Вставка...#КонецВставки и аннотации #Удаление/#КонецУдаления."""
    result = []
    skip_insert = False

    for line in lines:
        stripped = line.strip()

        if stripped == "#Вставка":
            skip_insert = True
            continue
        if stripped == "#КонецВставки":
            skip_insert = False
            continue
        if skip_insert:
            continue

        if stripped == "#Удаление":
            continue
        if stripped == "#КонецУдаления":
            continue

        result.append(line)

    return result


def parse_proc_name_from_file(lines: list[str]) -> Optional[str]:
    """Извлекает имя процедуры/функции из строк файла."""
    for line in lines:
        m = RE_PROC_DECL.match(line.strip())
        if m:
            return m.group(2)
    return None


# ─── Модель результатов ──────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Результат одной проверки."""
    ok: bool
    item: str           # что проверялось
    detail: str = ""    # детали при ошибке


@dataclass
class VerifyReport:
    """Агрегированный отчёт верификации."""
    invariant_results: list[CheckResult] = field(default_factory=list)
    prefix_results: list[CheckResult] = field(default_factory=list)
    orphan_results: list[CheckResult] = field(default_factory=list)
    structure_results: list[CheckResult] = field(default_factory=list)
    crossref_results: list[CheckResult] = field(default_factory=list)


# ─── 1. Проверка инварианта ──────────────────────────────────────────────────

def check_invariant(work_dir: str) -> list[CheckResult]:
    """Проверяет инвариант для каждого _ext.bsl: strip_markers(ext_body) == typ_body."""
    results = []
    work_path = Path(work_dir)

    for ext_file in sorted(work_path.rglob("*_ext.bsl")):
        proc_name = ext_file.stem[:-4]  # убираем _ext
        typ_file = ext_file.parent / f"{proc_name}_typ.bsl"
        rel_path = str(ext_file.relative_to(work_path))

        if not typ_file.exists():
            # Нет типового файла — пропускаем (может быть только _new)
            continue

        try:
            ext_lines = read_bsl(str(ext_file))
            typ_lines = read_bsl(str(typ_file))

            ext_body = extract_body(ext_lines)
            typ_body = extract_body(typ_lines)

            cleaned = strip_markers(ext_body)

            if cleaned == typ_body:
                results.append(CheckResult(ok=True, item=rel_path))
            else:
                diff = list(difflib.unified_diff(
                    typ_body, cleaned,
                    fromfile="типовая (ожидание)",
                    tofile="результат без маркеров (факт)",
                    lineterm=""
                ))
                diff_text = "\n".join(diff[:20])
                results.append(CheckResult(
                    ok=False,
                    item=rel_path,
                    detail=diff_text,
                ))
        except Exception as e:
            results.append(CheckResult(ok=False, item=rel_path, detail=str(e)))

    return results


# ─── 2. Проверка префикса ───────────────────────────────────────────────────

def check_prefix_new_files(work_dir: str, prefix: str) -> list[CheckResult]:
    """Проверяет, что имена процедур в _new.bsl начинаются с префикса."""
    results = []
    work_path = Path(work_dir)
    prefix_with_underscore = prefix + "_"

    for new_file in sorted(work_path.rglob("*_new.bsl")):
        rel_path = str(new_file.relative_to(work_path))
        try:
            lines = read_bsl(str(new_file))
            name = parse_proc_name_from_file(lines)
            if name is None:
                results.append(CheckResult(
                    ok=False, item=rel_path,
                    detail="Не удалось определить имя процедуры",
                ))
                continue

            if name.startswith(prefix_with_underscore):
                results.append(CheckResult(ok=True, item=rel_path))
            else:
                results.append(CheckResult(
                    ok=False, item=rel_path,
                    detail=f"Имя '{name}' не начинается с '{prefix_with_underscore}'",
                ))
        except Exception as e:
            results.append(CheckResult(ok=False, item=rel_path, detail=str(e)))

    return results


def check_prefix_module_files(work_dir: str, prefix: str) -> list[CheckResult]:
    """Проверяет, что ВСЕ процедуры в _module.bsl начинаются с префикса.

    Исключение: процедуры внутри &ИзменениеИКонтроль (переименованы с префиксом,
    но имя в аннотации — оригинальное, что корректно).
    """
    results = []
    work_path = Path(work_dir)
    prefix_with_underscore = prefix + "_"

    for module_file in sorted(work_path.rglob("_module.bsl")):
        rel_path = str(module_file.relative_to(work_path))
        try:
            lines = read_bsl(str(module_file))

            # Находим все объявления процедур, определяя контекст (под &ИзменениеИКонтроль или нет)
            is_change_control = False
            errors = []

            for i, line in enumerate(lines):
                stripped = line.strip()

                if RE_CHANGE_CONTROL.match(stripped):
                    is_change_control = True
                    continue

                m = RE_PROC_DECL.match(stripped)
                if m:
                    proc_name = m.group(2)
                    if is_change_control:
                        # Под &ИзменениеИКонтроль — имя должно иметь префикс
                        if not proc_name.startswith(prefix_with_underscore):
                            errors.append(
                                f"Строка {i+1}: '{proc_name}' под &ИзменениеИКонтроль "
                                f"без префикса '{prefix_with_underscore}'"
                            )
                    else:
                        # Обычная процедура — тоже должна иметь префикс
                        if not proc_name.startswith(prefix_with_underscore):
                            errors.append(
                                f"Строка {i+1}: '{proc_name}' без префикса '{prefix_with_underscore}'"
                            )
                    is_change_control = False
                    continue

                # Если после &ИзменениеИКонтроль идёт не объявление, а директива — сохраняем флаг
                # (директива контекста между аннотацией и объявлением)

        except Exception as e:
            results.append(CheckResult(ok=False, item=rel_path, detail=str(e)))
            continue

        if errors:
            results.append(CheckResult(
                ok=False, item=rel_path,
                detail="\n".join(errors),
            ))
        else:
            results.append(CheckResult(ok=True, item=rel_path))

    return results


# ─── 3. Проверка орфанных ссылок ─────────────────────────────────────────────

def check_orphan_references(work_dir: str) -> list[CheckResult]:
    """Проверяет, что старые имена (до переименования) не встречаются в _module.bsl."""
    results = []
    work_path = Path(work_dir)

    # Читаем assembly-report.json
    report_path = work_path / "assembly-report.json"
    if not report_path.exists():
        return [CheckResult(
            ok=True,
            item="assembly-report.json",
            detail="Файл не найден, проверка пропущена",
        )]

    try:
        with open(report_path, "r", encoding="utf-8") as f:
            assembly_report = json.load(f)
    except Exception as e:
        return [CheckResult(ok=False, item="assembly-report.json", detail=str(e))]

    for module_entry in assembly_report.get("modules", []):
        renames = module_entry.get("renames", {})
        if not renames:
            continue

        output_file = module_entry.get("output_file")
        if not output_file or not os.path.exists(output_file):
            results.append(CheckResult(
                ok=False,
                item=module_entry.get("path", "?"),
                detail=f"Файл модуля не найден: {output_file}",
            ))
            continue

        rel_path = module_entry.get("path", output_file)

        try:
            lines = read_bsl(output_file)
            orphans_found = []

            for old_name in renames.keys():
                pattern = re.compile(
                    r'(?<![А-Яа-яЁёA-Za-z0-9_])' + re.escape(old_name) + r'(?![А-Яа-яЁёA-Za-z0-9_])'
                )
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    # Пропускаем строки с &ИзменениеИКонтроль
                    if stripped.startswith('&ИзменениеИКонтроль'):
                        continue
                    if pattern.search(line):
                        orphans_found.append(
                            f"Строка {i+1}: '{old_name}' → {line.strip()[:80]}"
                        )

            if orphans_found:
                results.append(CheckResult(
                    ok=False, item=rel_path,
                    detail="\n".join(orphans_found),
                ))
            else:
                results.append(CheckResult(ok=True, item=rel_path))

        except Exception as e:
            results.append(CheckResult(ok=False, item=rel_path, detail=str(e)))

    return results


# ─── 4. Проверка структуры BSL ───────────────────────────────────────────────

def check_bsl_structure(work_dir: str) -> list[CheckResult]:
    """Проверяет парность процедур/функций и маркеров в _module.bsl."""
    results = []
    work_path = Path(work_dir)

    for module_file in sorted(work_path.rglob("_module.bsl")):
        rel_path = str(module_file.relative_to(work_path))
        try:
            lines = read_bsl(str(module_file))
            errors = []

            # Проверка парности Процедура/КонецПроцедуры и Функция/КонецФункции
            proc_stack = []  # [(kind, line_num)]
            for i, line in enumerate(lines):
                stripped = line.strip()
                m = RE_PROC_DECL.match(stripped)
                if m:
                    kind = m.group(1)
                    proc_stack.append((kind, i + 1))
                    continue

                em = RE_PROC_END.match(stripped)
                if em:
                    end_kind = em.group(1)
                    if not proc_stack:
                        errors.append(f"Строка {i+1}: {end_kind} без открывающего объявления")
                    else:
                        start_kind, start_line = proc_stack.pop()
                        # Проверяем соответствие: Процедура↔КонецПроцедуры, Функция↔КонецФункции
                        expected_end = "КонецПроцедуры" if start_kind.lower() == "процедура" else "КонецФункции"
                        if end_kind.lower() != expected_end.lower():
                            errors.append(
                                f"Строка {i+1}: {end_kind} не соответствует {start_kind} "
                                f"(строка {start_line})"
                            )

            for kind, line_num in proc_stack:
                errors.append(f"Строка {line_num}: {kind} без {('КонецПроцедуры' if kind.lower() == 'процедура' else 'КонецФункции')}")

            # Проверка парности маркеров
            insert_stack = []   # [line_num]
            delete_stack = []   # [line_num]

            for i, line in enumerate(lines):
                stripped = line.strip()

                if stripped == "#Вставка":
                    if insert_stack:
                        errors.append(f"Строка {i+1}: вложенный #Вставка (предыдущий на строке {insert_stack[-1]})")
                    insert_stack.append(i + 1)
                elif stripped == "#КонецВставки":
                    if not insert_stack:
                        errors.append(f"Строка {i+1}: #КонецВставки без #Вставка")
                    else:
                        insert_stack.pop()
                elif stripped == "#Удаление":
                    if delete_stack:
                        errors.append(f"Строка {i+1}: вложенный #Удаление (предыдущий на строке {delete_stack[-1]})")
                    delete_stack.append(i + 1)
                elif stripped == "#КонецУдаления":
                    if not delete_stack:
                        errors.append(f"Строка {i+1}: #КонецУдаления без #Удаление")
                    else:
                        delete_stack.pop()

            for line_num in insert_stack:
                errors.append(f"Строка {line_num}: #Вставка без #КонецВставки")
            for line_num in delete_stack:
                errors.append(f"Строка {line_num}: #Удаление без #КонецУдаления")

            if errors:
                results.append(CheckResult(
                    ok=False, item=rel_path,
                    detail="\n".join(errors),
                ))
            else:
                results.append(CheckResult(ok=True, item=rel_path))

        except Exception as e:
            results.append(CheckResult(ok=False, item=rel_path, detail=str(e)))

    return results


# ─── 5. Перекрёстные ссылки между модулями ───────────────────────────────────

def check_cross_references(work_dir: str) -> list[CheckResult]:
    """Проверяет, не вызывает ли модуль процедуры из _new другого модуля (информационно)."""
    results = []
    work_path = Path(work_dir)

    # Собираем все новые процедуры по модулям
    # {module_rel_path: [proc_name, ...]}
    new_procs_by_module: dict[str, list[str]] = {}
    all_new_proc_names: set[str] = set()

    for new_file in sorted(work_path.rglob("*_new.bsl")):
        module_rel = str(new_file.parent.relative_to(work_path))
        try:
            lines = read_bsl(str(new_file))
            name = parse_proc_name_from_file(lines)
            if name:
                if module_rel not in new_procs_by_module:
                    new_procs_by_module[module_rel] = []
                new_procs_by_module[module_rel].append(name)
                all_new_proc_names.add(name)
        except Exception:
            pass

    if not all_new_proc_names:
        return results

    # Для каждого _module.bsl ищем вызовы процедур из чужих модулей
    for module_file in sorted(work_path.rglob("_module.bsl")):
        module_rel = str(module_file.parent.relative_to(work_path))
        own_procs = set(new_procs_by_module.get(module_rel, []))
        foreign_procs = all_new_proc_names - own_procs

        if not foreign_procs:
            continue

        try:
            lines = read_bsl(str(module_file))
            found_refs = []

            for proc_name in foreign_procs:
                pattern = re.compile(
                    r'(?<![А-Яа-яЁёA-Za-z0-9_])' + re.escape(proc_name) + r'(?![А-Яа-яЁёA-Za-z0-9_])'
                )
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        # Определяем, из какого модуля эта процедура
                        source_module = None
                        for mod_path, procs in new_procs_by_module.items():
                            if proc_name in procs and mod_path != module_rel:
                                source_module = mod_path
                                break
                        found_refs.append(
                            f"Строка {i+1}: вызов '{proc_name}' "
                            f"(из {source_module or '?'})"
                        )

            if found_refs:
                results.append(CheckResult(
                    ok=True,  # информационно, не ошибка
                    item=module_rel,
                    detail="Перекрёстные ссылки:\n" + "\n".join(found_refs),
                ))

        except Exception:
            pass

    return results


# ─── Форматирование отчёта ───────────────────────────────────────────────────

def format_report(report: VerifyReport) -> str:
    """Форматирует отчёт верификации в текстовый формат."""
    lines = []
    lines.append("# Отчёт верификации модулей расширения")
    lines.append("")

    # Подсчёт модулей
    module_files = set()
    for r in report.structure_results:
        module_files.add(r.item)
    total_modules = len(module_files) if module_files else 0

    lines.append(f"Всего модулей: {total_modules}")
    lines.append("Всего проверок: 5 типов")
    lines.append("")

    has_errors = False

    # 1. Инвариант
    lines.append("## 1. Инвариант")
    inv_total = len(report.invariant_results)
    inv_ok = sum(1 for r in report.invariant_results if r.ok)
    inv_fail = inv_total - inv_ok
    lines.append(f"  Проверено: {inv_total} процедур")
    lines.append(f"  Пройдено: {inv_ok}, Ошибок: {inv_fail}")
    if inv_fail > 0:
        has_errors = True
        for r in report.invariant_results:
            if not r.ok:
                lines.append(f"  [FAIL] {r.item}")
                if r.detail:
                    for dl in r.detail.split("\n"):
                        lines.append(f"    {dl}")
    lines.append("")

    # 2. Префикс
    lines.append("## 2. Префикс")
    # Разделяем _new (предупреждения) и _module (ошибки)
    new_results = [r for r in report.prefix_results if "_new.bsl" in r.item]
    mod_results = [r for r in report.prefix_results if "_module.bsl" in r.item]

    pfx_total = len(report.prefix_results)
    pfx_ok = sum(1 for r in report.prefix_results if r.ok)
    new_warn = sum(1 for r in new_results if not r.ok)
    mod_fail = sum(1 for r in mod_results if not r.ok)
    lines.append(f"  Проверено: {pfx_total} процедур")
    lines.append(f"  Корректно: {pfx_ok}, Предупреждения (_new): {new_warn}, Ошибки (_module): {mod_fail}")
    if new_warn > 0:
        for r in new_results:
            if not r.ok:
                lines.append(f"  [WARN] {r.item}: {r.detail} (будет переименовано при сборке)")
    if mod_fail > 0:
        has_errors = True
        for r in mod_results:
            if not r.ok:
                lines.append(f"  [FAIL] {r.item}: {r.detail}")
    lines.append("")

    # 3. Орфанные ссылки
    lines.append("## 3. Орфанные ссылки")
    orp_total = len(report.orphan_results)
    orp_fail = sum(1 for r in report.orphan_results if not r.ok)
    lines.append(f"  Проверено: {orp_total} модуля с переименованиями")
    lines.append(f"  Орфанных: {orp_fail}")
    if orp_fail > 0:
        has_errors = True
        for r in report.orphan_results:
            if not r.ok:
                lines.append(f"  [FAIL] {r.item}")
                if r.detail:
                    for dl in r.detail.split("\n"):
                        lines.append(f"    {dl}")
    lines.append("")

    # 4. Структура BSL
    lines.append("## 4. Структура BSL")
    str_total = len(report.structure_results)
    str_ok = sum(1 for r in report.structure_results if r.ok)
    str_fail = str_total - str_ok
    lines.append(f"  Проверено: {str_total} модулей")
    lines.append(f"  Корректно: {str_ok}, Ошибок: {str_fail}")
    if str_fail > 0:
        has_errors = True
        for r in report.structure_results:
            if not r.ok:
                lines.append(f"  [FAIL] {r.item}")
                if r.detail:
                    for dl in r.detail.split("\n"):
                        lines.append(f"    {dl}")
    lines.append("")

    # 5. Перекрёстные ссылки
    lines.append("## 5. Перекрёстные ссылки (информационно)")
    xref_total = len(report.crossref_results)
    if xref_total > 0:
        lines.append(f"  Найдено: {xref_total} модулей с перекрёстными ссылками")
        for r in report.crossref_results:
            lines.append(f"  [INFO] {r.item}")
            if r.detail:
                for dl in r.detail.split("\n"):
                    lines.append(f"    {dl}")
    else:
        lines.append("  Перекрёстных ссылок не обнаружено")
    lines.append("")

    # Итого
    if has_errors:
        lines.append("Итого: ЕСТЬ ОШИБКИ")
    else:
        lines.append("Итого: ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")

    return "\n".join(lines)


def build_json_report(report: VerifyReport) -> dict:
    """Формирует JSON-представление отчёта."""
    def results_to_list(results: list[CheckResult]) -> list[dict]:
        return [
            {"ok": r.ok, "item": r.item, "detail": r.detail}
            for r in results
        ]

    inv_total = len(report.invariant_results)
    inv_fail = sum(1 for r in report.invariant_results if not r.ok)
    pfx_total = len(report.prefix_results)
    pfx_fail = sum(1 for r in report.prefix_results if not r.ok)
    orp_total = len(report.orphan_results)
    orp_fail = sum(1 for r in report.orphan_results if not r.ok)
    str_total = len(report.structure_results)
    str_fail = sum(1 for r in report.structure_results if not r.ok)

    all_passed = (inv_fail + pfx_fail + orp_fail + str_fail) == 0

    return {
        "all_passed": all_passed,
        "invariant": {
            "total": inv_total,
            "passed": inv_total - inv_fail,
            "failed": inv_fail,
            "details": results_to_list(report.invariant_results),
        },
        "prefix": {
            "total": pfx_total,
            "passed": pfx_total - pfx_fail,
            "failed": pfx_fail,
            "details": results_to_list(report.prefix_results),
        },
        "orphan_references": {
            "total": orp_total,
            "failed": orp_fail,
            "details": results_to_list(report.orphan_results),
        },
        "bsl_structure": {
            "total": str_total,
            "passed": str_total - str_fail,
            "failed": str_fail,
            "details": results_to_list(report.structure_results),
        },
        "cross_references": {
            "total": len(report.crossref_results),
            "details": results_to_list(report.crossref_results),
        },
    }


# ─── Главная логика ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Верификация собранных модулей расширения"
    )
    parser.add_argument("work_dir", help="Рабочий каталог с модулями расширения")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--report", help="Путь к файлу текстового отчёта")
    parser.add_argument("--json", action="store_true",
                        help="Сохранить результаты в work/verify-report.json")
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

    # Запуск проверок
    print("Проверка 1/5: Инвариант...")
    invariant_results = check_invariant(args.work_dir)

    print("Проверка 2/5: Префикс (_new)...")
    prefix_new_results = check_prefix_new_files(args.work_dir, prefix)

    print("Проверка 2/5: Префикс (_module)...")
    prefix_module_results = check_prefix_module_files(args.work_dir, prefix)
    # Результаты prefix: _new — предупреждения (сборочный скрипт переименует),
    # _module — ошибки (финальный результат)
    prefix_results = prefix_new_results + prefix_module_results

    print("Проверка 3/5: Орфанные ссылки...")
    orphan_results = check_orphan_references(args.work_dir)

    print("Проверка 4/5: Структура BSL...")
    structure_results = check_bsl_structure(args.work_dir)

    print("Проверка 5/5: Перекрёстные ссылки...")
    crossref_results = check_cross_references(args.work_dir)

    # Собираем отчёт
    report = VerifyReport(
        invariant_results=invariant_results,
        prefix_results=prefix_results,
        orphan_results=orphan_results,
        structure_results=structure_results,
        crossref_results=crossref_results,
    )

    # Форматируем и выводим
    report_text = format_report(report)

    if args.report:
        os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"\nОтчёт сохранён: {args.report}")
    else:
        print()
        print(report_text)

    # JSON-отчёт
    if args.json:
        json_path = os.path.join(args.work_dir, "verify-report.json")
        json_report = build_json_report(report)
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_report, f, ensure_ascii=False, indent=2)
        print(f"JSON-отчёт: {json_path}")

    # Exit code — ошибки _new.bsl не считаются (сборочный скрипт переименует)
    has_errors = any(not r.ok for r in invariant_results)
    has_errors = has_errors or any(not r.ok for r in prefix_module_results)
    has_errors = has_errors or any(not r.ok for r in orphan_results)
    has_errors = has_errors or any(not r.ok for r in structure_results)

    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
