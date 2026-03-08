#!/usr/bin/env python3
"""
Генерация процедур расширения с маркерами &ИзменениеИКонтроль.

Читает пары файлов (_typ.bsl + _mod.bsl) из рабочего каталога,
сравнивает их через difflib и генерирует _ext.bsl рядом с исходными.

Новые процедуры (_new.bsl) на этом этапе не обрабатываются —
они будут включены в модуль расширения на этапе сборки.

Также генерирует JSON-отчёт с флагами needs_review для процедур,
содержащих крупные replace-блоки (кандидаты на ИИ-ревью).

Использование:
    python scripts/generate-extension-procedures.py <work_dir> [-c config.json] [--dry-run]
    python scripts/generate-extension-procedures.py work --review-threshold 5
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


# ─── Модель данных ───────────────────────────────────────────────────────────

@dataclass
class ProcedurePair:
    """Пара типовой и доработанной процедур."""
    name: str
    dir_path: str             # каталог, где лежат файлы
    module_path: str          # относительный путь (для отчёта)
    typ_file: str             # путь к _typ.bsl
    mod_file: str             # путь к _mod.bsl


@dataclass
class GenerationResult:
    """Результат генерации одной процедуры."""
    name: str
    module_path: str
    success: bool
    output_file: Optional[str] = None
    error: Optional[str] = None
    stats: dict = field(default_factory=dict)


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


def write_bsl(path: str, lines: list[str]):
    """Записывает BSL-файл с BOM."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        for line in lines:
            f.write(line + "\n")


# ─── Парсинг заголовка процедуры ────────────────────────────────────────────

RE_PROC_DECL = re.compile(
    r'^(Процедура|Функция)\s+([А-Яа-яЁёA-Za-z0-9_]+)\s*\(',
    re.IGNORECASE
)
RE_PROC_END = re.compile(
    r'^(КонецПроцедуры|КонецФункции)\s*(//.*)?$',
    re.IGNORECASE
)
RE_DIRECTIVE = re.compile(
    r'^&(НаСервере|НаКлиенте|НаСервереБезКонтекста|'
    r'НаКлиентеНаСервереБезКонтекста|НаКлиентеНаСервере)',
    re.IGNORECASE
)


def parse_proc_header(lines: list[str]) -> dict:
    """Извлекает из строк процедуры: директиву, тип, имя, ключевое слово конца."""
    info = {"directive": None, "kind": None, "name": None, "decl_idx": None, "end_keyword": None}
    for i, line in enumerate(lines):
        stripped = line.strip()
        dm = RE_DIRECTIVE.match(stripped)
        if dm:
            info["directive"] = stripped
            continue
        pm = RE_PROC_DECL.match(stripped)
        if pm:
            info["kind"] = pm.group(1)
            info["name"] = pm.group(2)
            info["decl_idx"] = i
            kind_lower = pm.group(1).lower()
            if kind_lower in ("процедура",):
                info["end_keyword"] = "КонецПроцедуры"
            else:
                info["end_keyword"] = "КонецФункции"
            break
    return info


# ─── Генерация diff с маркерами ─────────────────────────────────────────────

def generate_marked_procedure(
    typ_lines: list[str],
    mod_lines: list[str],
    prefix: str,
    review_threshold: int = 5
) -> tuple[list[str], dict]:
    """
    Генерирует процедуру расширения с маркерами #Вставка/#Удаление.

    Возвращает (result_lines, stats).
    Типовой код сохраняется побайтово — берётся из typ_lines.
    """
    typ_info = parse_proc_header(typ_lines)
    mod_info = parse_proc_header(mod_lines)

    if not typ_info["name"] or not mod_info["name"]:
        raise ValueError("Не удалось распарсить заголовок процедуры")

    original_name = typ_info["name"]
    ext_name = f"{prefix}_{original_name}"

    typ_body = extract_body(typ_lines)
    mod_body = extract_body(mod_lines)

    # Генерируем diff тела
    marked_body, stats = diff_to_markers(typ_body, mod_body, review_threshold=review_threshold)

    # Собираем результат
    result = []

    # Аннотация расширения
    result.append(f'&ИзменениеИКонтроль("{original_name}")')

    # Директива контекста (берём из типовой)
    if typ_info["directive"]:
        result.append(typ_info["directive"])

    # Заголовок с переименованием — берём из типовой, меняем только имя
    for i in range(len(typ_lines)):
        stripped = typ_lines[i].strip()
        pm = RE_PROC_DECL.match(stripped)
        if pm:
            # Заменяем имя в оригинальной строке (сохраняя форматирование)
            decl_line = typ_lines[i].replace(original_name, ext_name, 1)
            result.append(decl_line)
            # Если объявление многострочное — копируем остальные строки до закрытия скобки
            if ")" not in typ_lines[i]:
                for j in range(i + 1, len(typ_lines)):
                    result.append(typ_lines[j])
                    if ")" in typ_lines[j]:
                        break
            break

    # Тело с маркерами
    result.extend(marked_body)

    # Закрывающее ключевое слово (из типовой)
    result.append(typ_info["end_keyword"])

    return result, stats


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


def try_refine_replace(typ_block: list[str], mod_block: list[str]) -> tuple[list[str], bool]:
    """Try to break a large replace into finer-grained changes.

    Returns (result_lines, refined) where refined=True if sub-diff
    found at least one equal region within the block.
    """
    sub_matcher = difflib.SequenceMatcher(None, typ_block, mod_block, autojunk=False)
    sub_opcodes = sub_matcher.get_opcodes()

    # Check if sub-diff found any equal regions
    has_equal = any(tag == "equal" for tag, *_ in sub_opcodes)
    if not has_equal:
        return [], False

    # Generate markers from sub-opcodes (one level only, no recursion)
    result = []
    for tag, si1, si2, sj1, sj2 in sub_opcodes:
        if tag == "equal":
            result.extend(typ_block[si1:si2])
        elif tag == "insert":
            result.append("#Вставка")
            result.extend(mod_block[sj1:sj2])
            result.append("#КонецВставки")
        elif tag == "delete":
            result.append("#Удаление")
            result.extend(typ_block[si1:si2])
            result.append("#КонецУдаления")
        elif tag == "replace":
            result.append("#Вставка")
            result.extend(mod_block[sj1:sj2])
            result.append("#КонецВставки")
            result.append("#Удаление")
            result.extend(typ_block[si1:si2])
            result.append("#КонецУдаления")
    return result, True


def uncomment_line(line: str) -> str:
    """Strip first // from a commented line, preserving leading whitespace."""
    idx = line.find("//")
    if idx < 0:
        return line
    # Remove // but keep everything before and after
    return line[:idx] + line[idx+2:]


def clean_comment_only_replace(typ_lines: list[str], mod_lines: list[str]) -> list[str]:
    """Clean redundant commented code from a comment-only replace block.

    Identifies mod lines that are just commented-out versions of typ lines
    (redundant because #Удаление already shows the original).
    Keeps only non-matching "explanation" comment lines.
    """
    # Uncomment mod lines
    uncommented = [uncomment_line(line) for line in mod_lines]

    # Match uncommented mod lines against typ lines
    matcher = difflib.SequenceMatcher(None, uncommented, typ_lines, autojunk=False)

    # Find which mod lines are redundant (matched to typ)
    redundant_mod_indices = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i1, i2):
                redundant_mod_indices.add(idx)

    # Collect non-redundant (explanation) lines
    explanation_lines = [
        mod_lines[i] for i in range(len(mod_lines))
        if i not in redundant_mod_indices
    ]

    # Generate output
    result = []
    if explanation_lines:
        result.append("#Вставка")
        result.extend(explanation_lines)
        result.append("#КонецВставки")
    result.append("#Удаление")
    result.extend(typ_lines)
    result.append("#КонецУдаления")

    return result


def is_comment_only_replace(typ_lines: list[str], mod_lines: list[str]) -> bool:
    """Check if a replace block is just commenting out code.

    Returns True when all mod lines are comments (or blank) and
    not all typ lines were already comments.
    """
    all_mod_commented = all(
        line.strip().startswith("//") or line.strip() == ""
        for line in mod_lines
    )
    not_all_typ_commented = not all(
        line.strip().startswith("//") or line.strip() == ""
        for line in typ_lines
    )
    return all_mod_commented and not_all_typ_commented


def diff_to_markers(typ_body: list[str], mod_body: list[str],
                    review_threshold: int = 5) -> tuple[list[str], dict]:
    """
    Сравнивает тело типовой и доработанной процедуры.
    Возвращает размеченные строки и статистику.

    Типовой код идёт as-is из typ_body (с оригинальными пробелами/табами).

    Для replace-блоков >= review_threshold строк типовой:
    - пытается разбить через рекурсивный sub-diff (один уровень)
    - проверяет, является ли замена комментированием кода
    """
    matcher = difflib.SequenceMatcher(None, typ_body, mod_body, autojunk=False)
    opcodes = matcher.get_opcodes()

    result = []
    stats = {"equal_lines": 0, "insert_blocks": 0, "delete_blocks": 0,
             "replace_blocks": 0, "inserted_lines": 0, "deleted_lines": 0,
             "max_replace_typ_lines": 0, "replace_details": [],
             "refined_replaces": 0, "comment_only_replaces": 0,
             "comment_cleaned_replaces": 0, "unresolved_large_replaces": 0}

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            result.extend(typ_body[i1:i2])
            stats["equal_lines"] += i2 - i1

        elif tag == "insert":
            result.append("#Вставка")
            result.extend(mod_body[j1:j2])
            result.append("#КонецВставки")
            stats["insert_blocks"] += 1
            stats["inserted_lines"] += j2 - j1

        elif tag == "delete":
            result.append("#Удаление")
            result.extend(typ_body[i1:i2])
            result.append("#КонецУдаления")
            stats["delete_blocks"] += 1
            stats["deleted_lines"] += i2 - i1

        elif tag == "replace":
            typ_size = i2 - i1
            mod_size = j2 - j1

            is_large = typ_size >= review_threshold

            if is_large:
                # Try recursive sub-diff
                sub_result, sub_refined = try_refine_replace(
                    typ_body[i1:i2], mod_body[j1:j2])
                if sub_refined:
                    result.extend(sub_result)
                    stats["refined_replaces"] += 1
                    stats["replace_blocks"] += 1
                    stats["inserted_lines"] += mod_size
                    stats["deleted_lines"] += typ_size
                    stats["max_replace_typ_lines"] = max(stats["max_replace_typ_lines"], typ_size)
                    stats["replace_details"].append({
                        "typ_lines": typ_size,
                        "mod_lines": mod_size,
                        "refined": True,
                    })
                    continue

                # Check if comment-only
                if is_comment_only_replace(typ_body[i1:i2], mod_body[j1:j2]):
                    stats["comment_only_replaces"] += 1
                    stats["comment_cleaned_replaces"] += 1
                    # Clean redundant commented code
                    cleaned_result = clean_comment_only_replace(
                        typ_body[i1:i2], mod_body[j1:j2])
                    result.extend(cleaned_result)
                    stats["replace_blocks"] += 1
                    stats["inserted_lines"] += mod_size
                    stats["deleted_lines"] += typ_size
                    stats["max_replace_typ_lines"] = max(stats["max_replace_typ_lines"], typ_size)
                    stats["replace_details"].append({
                        "typ_lines": typ_size,
                        "mod_lines": mod_size,
                        "comment_cleaned": True,
                    })
                    continue
                else:
                    # Unresolved large replace
                    stats["unresolved_large_replaces"] += 1

            # Default: generate markers as before
            result.append("#Вставка")
            result.extend(mod_body[j1:j2])
            result.append("#КонецВставки")
            result.append("#Удаление")
            result.extend(typ_body[i1:i2])
            result.append("#КонецУдаления")
            stats["replace_blocks"] += 1
            stats["inserted_lines"] += mod_size
            stats["deleted_lines"] += typ_size
            stats["max_replace_typ_lines"] = max(stats["max_replace_typ_lines"], typ_size)
            stats["replace_details"].append({
                "typ_lines": typ_size,
                "mod_lines": mod_size,
            })

    return result, stats


# ─── Проверка инварианта ────────────────────────────────────────────────────

def validate_invariant(result_lines: list[str], typ_lines: list[str]) -> tuple[bool, str]:
    """
    Проверяет правило инварианта: если убрать все маркеры #Вставка/#Удаление
    с их содержимым, оставшийся код должен совпадать с типовой процедурой.

    Сравниваем только тело (без заголовка расширения и КонецПроцедуры).
    """
    result_body = extract_body_from_result(result_lines)
    typ_body = extract_body(typ_lines)

    cleaned = strip_markers(result_body)

    if cleaned == typ_body:
        return True, ""

    diff = list(difflib.unified_diff(
        typ_body, cleaned,
        fromfile="типовая (ожидание)",
        tofile="результат без маркеров (факт)",
        lineterm=""
    ))
    return False, "\n".join(diff[:30])


def extract_body_from_result(lines: list[str]) -> list[str]:
    """Извлекает тело из процедуры расширения (после объявления, до КонецПроцедуры)."""
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
        return lines
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


# ─── Оценка потребления токенов для ИИ-ревью ────────────────────────────────

def estimate_review_tokens(results: list[GenerationResult]) -> dict:
    """Estimate token consumption for AI review of needs_review procedures."""
    review_items = [r for r in results if r.stats.get("needs_review") and r.output_file]

    if not review_items:
        return {"review_count": 0, "total_files": 0, "total_chars": 0,
                "estimated_tokens": 0, "estimated_percent": 0,
                "recommendation": "ok", "items": []}

    total_chars = 0
    items = []
    for r in review_items:
        ext_dir = os.path.dirname(r.output_file)
        typ_path = os.path.join(ext_dir, f"{r.name}_typ.bsl")
        mod_path = os.path.join(ext_dir, f"{r.name}_mod.bsl")
        ext_path = r.output_file

        item_chars = 0
        for p in [typ_path, mod_path, ext_path]:
            if os.path.exists(p):
                item_chars += os.path.getsize(p)

        total_chars += item_chars
        items.append({
            "name": r.name,
            "module_path": r.module_path,
            "chars": item_chars,
            "estimated_tokens": int(item_chars * 0.7),  # ~0.7 tokens per char for Cyrillic BSL
        })

    # Each subagent gets: prompt (~500 tokens) + 3 files + response (~500 tokens)
    prompt_overhead = len(review_items) * 1000
    file_tokens = int(total_chars * 0.7)
    total_tokens = file_tokens + prompt_overhead

    # Session limit is approximately 1M tokens for Sonnet subagents
    SESSION_LIMIT = 1_000_000
    percent = round(total_tokens / SESSION_LIMIT * 100, 1)

    if percent < 30:
        recommendation = "ok"
    elif percent < 70:
        recommendation = "warning"
    else:
        recommendation = "critical"

    return {
        "review_count": len(review_items),
        "total_files": len(review_items) * 3,
        "total_chars": total_chars,
        "estimated_tokens": total_tokens,
        "estimated_percent": percent,
        "recommendation": recommendation,
        "items": items,
    }


# ─── Сканирование рабочего каталога ─────────────────────────────────────────

def scan_work_dir(work_dir: str) -> list[ProcedurePair]:
    """Сканирует рабочий каталог и находит пары _typ/_mod файлов."""
    pairs = []
    work_path = Path(work_dir)

    for bsl_file in sorted(work_path.rglob("*_typ.bsl")):
        proc_name = bsl_file.stem[:-4]  # убираем _typ
        mod_file = bsl_file.parent / f"{proc_name}_mod.bsl"

        if mod_file.exists():
            module_path = str(bsl_file.parent.relative_to(work_path))
            pairs.append(ProcedurePair(
                name=proc_name,
                dir_path=str(bsl_file.parent),
                module_path=module_path,
                typ_file=str(bsl_file),
                mod_file=str(mod_file),
            ))

    return pairs


# ─── Главная логика ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Генерация процедур расширения с маркерами &ИзменениеИКонтроль"
    )
    parser.add_argument("work_dir", help="Рабочий каталог с извлечёнными процедурами")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет обработано")
    parser.add_argument("--report", help="Путь к файлу текстового отчёта о генерации")
    parser.add_argument("--review-threshold", type=int, default=5,
                        help="Мин. размер replace-блока (строк типовой) для флага needs_review (default: 5)")
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

    # Сканируем пары _typ + _mod
    pairs = scan_work_dir(args.work_dir)
    print(f"Найдено пар _typ/_mod: {len(pairs)}")

    if not pairs:
        print("Нечего обрабатывать.")
        return

    results: list[GenerationResult] = []

    for pair in pairs:
        try:
            typ_lines = read_bsl(pair.typ_file)
            mod_lines = read_bsl(pair.mod_file)

            ext_lines, stats = generate_marked_procedure(typ_lines, mod_lines, prefix,
                                                            review_threshold=args.review_threshold)

            # Проверяем инвариант
            ok, diff_msg = validate_invariant(ext_lines, typ_lines)
            if not ok:
                stats["invariant_failed"] = True
                stats["invariant_diff"] = diff_msg

            # Флаг для ИИ-ревью: только если есть неразрешённые крупные replace-блоки
            needs_review = stats.get("unresolved_large_replaces", 0) > 0
            stats["needs_review"] = needs_review
            if not needs_review and stats.get("refined_replaces", 0) > 0:
                stats["skip_reason"] = "auto_refined"
            elif not needs_review and stats.get("comment_only_replaces", 0) > 0:
                stats["skip_reason"] = "auto_comment_only"

            if args.dry_run:
                results.append(GenerationResult(
                    name=pair.name, module_path=pair.module_path,
                    success=ok, stats=stats
                ))
                continue

            # Сохраняем _ext.bsl рядом с _typ и _mod
            out_file = os.path.join(pair.dir_path, f"{pair.name}_ext.bsl")
            write_bsl(out_file, ext_lines)

            results.append(GenerationResult(
                name=pair.name, module_path=pair.module_path,
                success=ok, output_file=out_file, stats=stats
            ))

        except Exception as e:
            results.append(GenerationResult(
                name=pair.name, module_path=pair.module_path,
                success=False, error=str(e), stats={}
            ))

    # Вывод результатов
    print_report(results, args.report)

    # JSON-отчёт (всегда сохраняется рядом с work_dir)
    json_report_path = os.path.join(args.work_dir, "generation-report.json")
    save_json_report(results, json_report_path)

    # Print estimation if there are procedures needing review
    estimation = estimate_review_tokens(results)
    if estimation["review_count"] > 0:
        print(f"\n--- Оценка потребления токенов для ИИ-ревью ---")
        print(f"Процедур для ревью: {estimation['review_count']}")
        print(f"Файлов для чтения: {estimation['total_files']}")
        print(f"Суммарный объём: {estimation['total_chars']:,} символов")
        print(f"Оценка токенов: ~{estimation['estimated_tokens']:,} ({estimation['estimated_percent']}% лимита)")
        if estimation["recommendation"] == "warning":
            print("ВНИМАНИЕ: значительное потребление токенов. Рекомендуется checkpoint.")
        elif estimation["recommendation"] == "critical":
            print("КРИТИЧНО: высокий риск исчерпания лимита. Рекомендуется разбить на сессии.")


def print_report(results: list[GenerationResult], report_path: Optional[str] = None):
    """Выводит отчёт о генерации."""
    lines = []
    lines.append("# Отчёт генерации процедур расширения")
    lines.append("")

    ok_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success)
    review_count = sum(1 for r in results if r.stats.get("needs_review"))

    lines.append(f"Всего: {len(results)}")
    lines.append(f"Успешно: {ok_count}, с ошибками: {fail_count}, требуют ИИ-ревью: {review_count}")
    lines.append("")

    # Группируем по модулю
    by_module: dict[str, list[GenerationResult]] = {}
    for r in results:
        if r.module_path not in by_module:
            by_module[r.module_path] = []
        by_module[r.module_path].append(r)

    for module_path, mod_results in sorted(by_module.items()):
        lines.append(f"## {module_path}")
        for r in mod_results:
            status = "OK" if r.success else "FAIL"
            review_mark = " [REVIEW]" if r.stats.get("needs_review") else ""
            s = r.stats
            if r.error:
                lines.append(f"  [{status}] {r.name} — ОШИБКА: {r.error}")
            elif s.get("invariant_failed"):
                lines.append(
                    f"  [{status}]{review_mark} {r.name} — ИНВАРИАНТ НЕ ПРОЙДЕН "
                    f"(вставок: {s.get('insert_blocks', 0)}, "
                    f"удалений: {s.get('delete_blocks', 0)}, "
                    f"замен: {s.get('replace_blocks', 0)})"
                )
            else:
                refined_info = ""
                if s.get("refined_replaces", 0) > 0:
                    refined_info += f", уточнено: {s['refined_replaces']}"
                if s.get("comment_only_replaces", 0) > 0:
                    refined_info += f", комм.замен: {s['comment_only_replaces']}"
                if s.get("skip_reason"):
                    refined_info += f" [{s['skip_reason']}]"
                lines.append(
                    f"  [{status}]{review_mark} {r.name} — "
                    f"вставок: {s.get('insert_blocks', 0)}, "
                    f"удалений: {s.get('delete_blocks', 0)}, "
                    f"замен: {s.get('replace_blocks', 0)}, "
                    f"макс.replace: {s.get('max_replace_typ_lines', 0)} стр., "
                    f"неизменённых: {s.get('equal_lines', 0)}{refined_info}"
                )
        lines.append("")

    report_text = "\n".join(lines)

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"Отчёт сохранён: {report_path}")
    else:
        print(report_text)


def save_json_report(results: list[GenerationResult], json_path: str):
    """Сохраняет JSON-отчёт для использования ИИ-ревью."""
    report = {
        "total": len(results),
        "success": sum(1 for r in results if r.success),
        "failed": sum(1 for r in results if not r.success),
        "needs_review": sum(1 for r in results if r.stats.get("needs_review")),
        "procedures": []
    }

    for r in results:
        entry = {
            "name": r.name,
            "module_path": r.module_path,
            "success": r.success,
            "output_file": r.output_file,
            "error": r.error,
            "needs_review": r.stats.get("needs_review", False),
            "insert_blocks": r.stats.get("insert_blocks", 0),
            "delete_blocks": r.stats.get("delete_blocks", 0),
            "replace_blocks": r.stats.get("replace_blocks", 0),
            "max_replace_typ_lines": r.stats.get("max_replace_typ_lines", 0),
            "equal_lines": r.stats.get("equal_lines", 0),
            "refined_replaces": r.stats.get("refined_replaces", 0),
            "comment_only_replaces": r.stats.get("comment_only_replaces", 0),
            "comment_cleaned_replaces": r.stats.get("comment_cleaned_replaces", 0),
            "unresolved_large_replaces": r.stats.get("unresolved_large_replaces", 0),
            "skip_reason": r.stats.get("skip_reason"),
        }
        # Для needs_review добавляем пути к исходным файлам
        if r.stats.get("needs_review") and r.output_file:
            ext_dir = os.path.dirname(r.output_file)
            entry["typ_file"] = os.path.join(ext_dir, f"{r.name}_typ.bsl")
            entry["mod_file"] = os.path.join(ext_dir, f"{r.name}_mod.bsl")
            entry["ext_file"] = r.output_file
        report["procedures"].append(entry)

    report["estimation"] = estimate_review_tokens(results)

    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON-отчёт: {json_path}")


if __name__ == "__main__":
    main()
