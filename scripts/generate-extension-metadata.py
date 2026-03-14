#!/usr/bin/env python3
"""
Генерация XML-метаданных расширения конфигурации 1С.

Читает deploy-report.json из рабочего каталога, определяет уникальные объекты
и формы, создаёт (или дополняет) XML-файлы метаданных расширения:
Configuration.xml, Languages/, Roles/, объекты и формы.

Это шаг 7 пайплайна миграции:
  reduce → extract → generate → assemble → ИИ-ревью → deploy → **metadata**

Использование:
    python scripts/generate-extension-metadata.py <work_dir> [-c config.json] [--dry-run]
    python scripts/generate-extension-metadata.py work --report metadata-report.txt
"""
import os
import sys
import json
import re
import uuid
import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ─── Маппинг русского типа → английский тип ─────────────────────────────────

OBJECT_TYPE_RU_TO_EN = {
    "ОбщийМодуль": "CommonModule",
    "Справочник": "Catalog",
    "Документ": "Document",
    "Перечисление": "Enum",
    "РегистрСведений": "InformationRegister",
    "РегистрНакопления": "AccumulationRegister",
    "РегистрБухгалтерии": "AccountingRegister",
    "ПланВидовХарактеристик": "ChartOfCharacteristicTypes",
    "ПланСчетов": "ChartOfAccounts",
    "ПланОбмена": "ExchangePlan",
    "Обработка": "DataProcessor",
    "Отчет": "Report",
    "БизнесПроцесс": "BusinessProcess",
    "Задача": "Task",
    "Константа": "Constant",
    "ЖурналДокументов": "DocumentJournal",
    "РегистрРасчета": "CalculationRegister",
    "ПланВидовРасчета": "ChartOfCalculationTypes",
    "ОбщаяФорма": "CommonForm",
    "ОбщаяКоманда": "CommonCommand",
    "СервисИнтеграции": "IntegrationService",
    "ХранилищеНастроек": "SettingsStorage",
}


# ─── Английский тип → каталог ───────────────────────────────────────────────

TYPE_TO_DIR = {
    "CommonModule": "CommonModules",
    "Catalog": "Catalogs",
    "Document": "Documents",
    "Enum": "Enums",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "AccountingRegister": "AccountingRegisters",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ChartOfAccounts": "ChartsOfAccounts",
    "ExchangePlan": "ExchangePlans",
    "DataProcessor": "DataProcessors",
    "Report": "Reports",
    "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks",
    "Constant": "Constants",
    "DocumentJournal": "DocumentJournals",
    "CalculationRegister": "CalculationRegisters",
    "ChartOfCalculationTypes": "ChartsOfCalculationTypes",
    "CommonForm": "CommonForms",
    "CommonCommand": "CommonCommands",
    "IntegrationService": "IntegrationServices",
    "SettingsStorage": "SettingsStorages",
}


# ─── Канонический порядок типов в ChildObjects ───────────────────────────────

TYPE_ORDER = [
    "Language", "Subsystem", "StyleItem", "Style",
    "CommonPicture", "SessionParameter", "Role", "CommonTemplate",
    "FilterCriterion", "CommonModule", "CommonAttribute", "ExchangePlan",
    "XDTOPackage", "WebService", "HTTPService", "WSReference",
    "EventSubscription", "ScheduledJob", "SettingsStorage", "FunctionalOption",
    "FunctionalOptionsParameter", "DefinedType", "CommonCommand", "CommandGroup",
    "Constant", "CommonForm", "Catalog", "Document",
    "DocumentNumerator", "Sequence", "DocumentJournal", "Enum",
    "Report", "DataProcessor", "InformationRegister", "AccumulationRegister",
    "ChartOfCharacteristicTypes", "ChartOfAccounts", "AccountingRegister",
    "ChartOfCalculationTypes", "CalculationRegister",
    "BusinessProcess", "Task", "IntegrationService",
]


# ─── GeneratedType паттерны по типу объекта ──────────────────────────────────

GENERATED_TYPES = {
    "Catalog": [
        ("CatalogObject", "Object"), ("CatalogRef", "Ref"),
        ("CatalogSelection", "Selection"), ("CatalogList", "List"),
        ("CatalogManager", "Manager"),
    ],
    "Document": [
        ("DocumentObject", "Object"), ("DocumentRef", "Ref"),
        ("DocumentSelection", "Selection"), ("DocumentList", "List"),
        ("DocumentManager", "Manager"),
    ],
    "Enum": [
        ("EnumRef", "Ref"), ("EnumManager", "Manager"), ("EnumList", "List"),
    ],
    "InformationRegister": [
        ("InformationRegisterRecord", "Record"),
        ("InformationRegisterManager", "Manager"),
        ("InformationRegisterSelection", "Selection"),
        ("InformationRegisterList", "List"),
        ("InformationRegisterRecordSet", "RecordSet"),
        ("InformationRegisterRecordKey", "RecordKey"),
        ("InformationRegisterRecordManager", "RecordManager"),
    ],
    "AccumulationRegister": [
        ("AccumulationRegisterRecord", "Record"),
        ("AccumulationRegisterManager", "Manager"),
        ("AccumulationRegisterSelection", "Selection"),
        ("AccumulationRegisterList", "List"),
        ("AccumulationRegisterRecordSet", "RecordSet"),
        ("AccumulationRegisterRecordKey", "RecordKey"),
    ],
    "ExchangePlan": [
        ("ExchangePlanObject", "Object"), ("ExchangePlanRef", "Ref"),
        ("ExchangePlanSelection", "Selection"), ("ExchangePlanList", "List"),
        ("ExchangePlanManager", "Manager"),
    ],
    "Report": [("ReportObject", "Object"), ("ReportManager", "Manager")],
    "DataProcessor": [("DataProcessorObject", "Object"), ("DataProcessorManager", "Manager")],
    "ChartOfAccounts": [
        ("ChartOfAccountsObject", "Object"), ("ChartOfAccountsRef", "Ref"),
        ("ChartOfAccountsSelection", "Selection"), ("ChartOfAccountsList", "List"),
        ("ChartOfAccountsManager", "Manager"),
    ],
    "ChartOfCharacteristicTypes": [
        ("ChartOfCharacteristicTypesObject", "Object"), ("ChartOfCharacteristicTypesRef", "Ref"),
        ("ChartOfCharacteristicTypesSelection", "Selection"), ("ChartOfCharacteristicTypesList", "List"),
        ("ChartOfCharacteristicTypesManager", "Manager"),
    ],
    "BusinessProcess": [
        ("BusinessProcessObject", "Object"), ("BusinessProcessRef", "Ref"),
        ("BusinessProcessSelection", "Selection"), ("BusinessProcessList", "List"),
        ("BusinessProcessManager", "Manager"),
    ],
    "Task": [
        ("TaskObject", "Object"), ("TaskRef", "Ref"),
        ("TaskSelection", "Selection"), ("TaskList", "List"),
        ("TaskManager", "Manager"),
    ],
    "DocumentJournal": [
        ("DocumentJournalSelection", "Selection"),
        ("DocumentJournalList", "List"),
        ("DocumentJournalManager", "Manager"),
    ],
    "Constant": [
        ("ConstantManager", "Manager"),
        ("ConstantValueManager", "ValueManager"),
        ("ConstantValueKey", "ValueKey"),
    ],
    "ChartOfCalculationTypes": [
        ("ChartOfCalculationTypesObject", "Object"), ("ChartOfCalculationTypesRef", "Ref"),
        ("ChartOfCalculationTypesSelection", "Selection"), ("ChartOfCalculationTypesList", "List"),
        ("ChartOfCalculationTypesManager", "Manager"),
        ("DisplacingCalculationTypes", "DisplacingCalculationTypes"),
        ("BaseCalculationTypes", "BaseCalculationTypes"),
        ("LeadingCalculationTypes", "LeadingCalculationTypes"),
    ],
    "CalculationRegister": [
        ("CalculationRegisterRecord", "Record"),
        ("CalculationRegisterManager", "Manager"),
        ("CalculationRegisterSelection", "Selection"),
        ("CalculationRegisterList", "List"),
        ("CalculationRegisterRecordSet", "RecordSet"),
        ("CalculationRegisterRecordKey", "RecordKey"),
    ],
    "AccountingRegister": [
        ("AccountingRegisterRecord", "Record"),
        ("AccountingRegisterManager", "Manager"),
        ("AccountingRegisterSelection", "Selection"),
        ("AccountingRegisterList", "List"),
        ("AccountingRegisterRecordSet", "RecordSet"),
        ("AccountingRegisterRecordKey", "RecordKey"),
    ],
}


# ─── Типы с ChildObjects ────────────────────────────────────────────────────

TYPES_WITH_CHILD_OBJECTS = {
    "Catalog", "Document", "ExchangePlan", "ChartOfAccounts",
    "ChartOfCharacteristicTypes", "ChartOfCalculationTypes",
    "BusinessProcess", "Task", "Enum",
    "InformationRegister", "AccumulationRegister", "AccountingRegister",
    "CalculationRegister", "DataProcessor", "Report",
}


# ─── CommonModule свойства для копирования ───────────────────────────────────

COMMON_MODULE_PROPS = [
    "Global", "ClientManagedApplication", "Server",
    "ExternalConnection", "ClientOrdinaryApplication", "ServerCall",
]


# ─── XML namespaces ─────────────────────────────────────────────────────────

XMLNS_DECL = 'xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:app="http://v8.1c.ru/8.2/managed-application/core" xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" xmlns:cmi="http://v8.1c.ru/8.2/managed-application/cmi" xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" xmlns:style="http://v8.1c.ru/8.1/data/ui/style" xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" xmlns:xpr="http://v8.1c.ru/8.3/xcf/predef" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'

NS = {
    "md": "http://v8.1c.ru/8.3/MDClasses",
    "xr": "http://v8.1c.ru/8.3/xcf/readable",
}


# ─── Модель данных ──────────────────────────────────────────────────────────

@dataclass
class ObjectInfo:
    """Информация об объекте метаданных для заимствования."""
    type_ru: str           # русский тип ("Документ")
    type_en: str           # английский тип ("Document")
    name: str              # имя объекта ("РеализацияТоваровУслуг")
    forms: list[str] = field(default_factory=list)  # список форм
    action: str = ""       # "created" / "skipped"
    form_actions: dict = field(default_factory=dict)  # {"ФормаДокумента": "created"/"skipped"}


@dataclass
class MetadataReport:
    """Отчёт о генерации метаданных."""
    created_scaffold: bool = False
    objects_created: int = 0
    objects_skipped: int = 0
    forms_created: int = 0
    forms_skipped: int = 0
    objects: list[dict] = field(default_factory=list)


# ─── Вспомогательные функции ────────────────────────────────────────────────

def new_uuid() -> str:
    """Генерирует новый UUID."""
    return str(uuid.uuid4())


def write_xml(path: str, content: str):
    """Записывает XML файл с UTF-8 BOM и CRLF."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(content)


def read_file(path: str) -> str:
    """Читает текстовый файл."""
    for enc in ["utf-8-sig", "utf-8", "cp1251"]:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Не удалось прочитать файл: {path}")


def read_source_uuid(xml_path: str, element_tag: str) -> str:
    """Читает UUID из source XML (атрибут uuid на указанном элементе).

    Args:
        xml_path: путь к XML файлу
        element_tag: локальное имя элемента (Language, Document, Form и т.д.)

    Returns:
        Строка UUID
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # Ищем элемент в namespace md
    el = root.find(f"md:{element_tag}", NS)
    if el is None:
        # Попробуем первый дочерний
        for child in root:
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if local == element_tag:
                el = child
                break
    if el is None:
        raise ValueError(f"Элемент <{element_tag}> не найден в {xml_path}")
    uid = el.get("uuid")
    if not uid:
        raise ValueError(f"Атрибут uuid не найден на <{element_tag}> в {xml_path}")
    return uid


def read_common_module_props(xml_path: str) -> dict[str, str]:
    """Читает свойства CommonModule из source XML."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    cm = root.find("md:CommonModule", NS)
    if cm is None:
        return {}
    props_el = cm.find("md:Properties", NS)
    if props_el is None:
        return {}
    result = {}
    for prop_name in COMMON_MODULE_PROPS:
        el = props_el.find(f"md:{prop_name}", NS)
        if el is not None and el.text:
            result[prop_name] = el.text
    return result


def ensure_prefix_underscore(prefix: str) -> str:
    """Гарантирует что префикс заканчивается на '_'."""
    if not prefix.endswith("_"):
        return prefix + "_"
    return prefix


# ─── Парсинг deploy-report.json ─────────────────────────────────────────────

def parse_deploy_report(report_path: str) -> list[ObjectInfo]:
    """Парсит deploy-report.json и извлекает уникальные объекты и формы.

    Returns:
        Список ObjectInfo с уникальными объектами и их формами.
    """
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    # Собираем объекты: ключ (type_ru, name) → ObjectInfo
    objects_map: dict[tuple[str, str], ObjectInfo] = {}

    for module in report.get("modules", []):
        path = module.get("path", "")
        if not path:
            continue

        parts = path.replace("\\", "/").split("/")
        if len(parts) < 2:
            continue

        type_ru = parts[0]
        obj_name = parts[1]

        type_en = OBJECT_TYPE_RU_TO_EN.get(type_ru)
        if type_en is None:
            print(f"Предупреждение: неизвестный тип объекта '{type_ru}', пропуск", file=sys.stderr)
            continue

        key = (type_ru, obj_name)
        if key not in objects_map:
            objects_map[key] = ObjectInfo(
                type_ru=type_ru,
                type_en=type_en,
                name=obj_name,
            )

        # Проверяем формы (не для ОбщаяФорма — это top-level)
        if len(parts) >= 4 and parts[2] == "Forms" and type_ru != "ОбщаяФорма":
            form_name = parts[3]
            if form_name not in objects_map[key].forms:
                objects_map[key].forms.append(form_name)

    return list(objects_map.values())


# ─── Scaffold (Configuration.xml + Languages + Roles) ───────────────────────

def create_scaffold(ext_dir: str, config: dict, base_config_path: str) -> bool:
    """Создаёт базовую структуру расширения если Configuration.xml не существует.

    Returns:
        True если scaffold был создан, False если уже существовал.
    """
    config_xml_path = os.path.join(ext_dir, "Configuration.xml")
    if os.path.isfile(config_xml_path):
        return False

    ext_name = config.get("extensionName", "Extension")
    ext_prefix = ensure_prefix_underscore(config.get("extensionPrefix", "Ext"))
    ext_purpose = config.get("extensionPurpose", "Customization")
    compat_mode = config.get("compatibilityMode", "Version8_3_24")

    # Генерируем UUID для всех ContainedObject
    config_uuid = new_uuid()
    contained_uuids = [new_uuid() for _ in range(7)]

    config_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<MetaDataObject {XMLNS_DECL} version="2.17">\r\n'
        f'\t<Configuration uuid="{config_uuid}">\r\n'
        '\t\t<InternalInfo>\r\n'
    )
    class_ids = [
        "9cd510cd-abfc-11d4-9434-004095e12fc7",
        "9fcd25a0-4822-11d4-9414-008048da11f9",
        "e3687481-0a87-462c-a166-9f34594f9bba",
        "9de14907-ec23-4a07-96f0-85521cb6b53b",
        "51f2d5d8-ea4d-4064-8892-82951750031e",
        "e68182ea-4237-4383-967f-90c1e3370bc7",
        "fb282519-d103-4dd3-bc12-cb271d631dfc",
    ]
    for i, class_id in enumerate(class_ids):
        config_xml += (
            '\t\t\t<xr:ContainedObject>\r\n'
            f'\t\t\t\t<xr:ClassId>{class_id}</xr:ClassId>\r\n'
            f'\t\t\t\t<xr:ObjectId>{contained_uuids[i]}</xr:ObjectId>\r\n'
            '\t\t\t</xr:ContainedObject>\r\n'
        )
    config_xml += (
        '\t\t</InternalInfo>\r\n'
        '\t\t<Properties>\r\n'
        '\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\r\n'
        f'\t\t\t<Name>{ext_name}</Name>\r\n'
        '\t\t\t<Synonym>\r\n'
        '\t\t\t\t<v8:item>\r\n'
        '\t\t\t\t\t<v8:lang>ru</v8:lang>\r\n'
        f'\t\t\t\t\t<v8:content>{ext_name}</v8:content>\r\n'
        '\t\t\t\t</v8:item>\r\n'
        '\t\t\t</Synonym>\r\n'
        '\t\t\t<Comment/>\r\n'
        f'\t\t\t<ConfigurationExtensionPurpose>{ext_purpose}</ConfigurationExtensionPurpose>\r\n'
        '\t\t\t<KeepMappingToExtendedConfigurationObjectsByIDs>true</KeepMappingToExtendedConfigurationObjectsByIDs>\r\n'
        f'\t\t\t<NamePrefix>{ext_prefix}</NamePrefix>\r\n'
        f'\t\t\t<ConfigurationExtensionCompatibilityMode>{compat_mode}</ConfigurationExtensionCompatibilityMode>\r\n'
        '\t\t\t<DefaultRunMode>ManagedApplication</DefaultRunMode>\r\n'
        '\t\t\t<UsePurposes>\r\n'
        '\t\t\t\t<v8:Value xsi:type="app:ApplicationUsePurpose">PlatformApplication</v8:Value>\r\n'
        '\t\t\t</UsePurposes>\r\n'
        '\t\t\t<ScriptVariant>Russian</ScriptVariant>\r\n'
        '\t\t\t<DefaultRoles>\r\n'
        f'\t\t\t\t<xr:Item xsi:type="xr:MDObjectRef">Role.{ext_prefix}ОсновнаяРоль</xr:Item>\r\n'
        '\t\t\t</DefaultRoles>\r\n'
        '\t\t\t<Vendor/>\r\n'
        '\t\t\t<Version/>\r\n'
        '\t\t\t<DefaultLanguage>Language.Русский</DefaultLanguage>\r\n'
        '\t\t\t<BriefInformation/>\r\n'
        '\t\t\t<DetailedInformation/>\r\n'
        '\t\t\t<Copyright/>\r\n'
        '\t\t\t<VendorInformationAddress/>\r\n'
        '\t\t\t<ConfigurationInformationAddress/>\r\n'
        '\t\t\t<InterfaceCompatibilityMode>TaxiEnableVersion8_2</InterfaceCompatibilityMode>\r\n'
        '\t\t</Properties>\r\n'
        '\t\t<ChildObjects>\r\n'
        '\t\t\t<Language>Русский</Language>\r\n'
        f'\t\t\t<Role>{ext_prefix}ОсновнаяРоль</Role>\r\n'
        '\t\t</ChildObjects>\r\n'
        '\t</Configuration>\r\n'
        '</MetaDataObject>'
    )
    write_xml(config_xml_path, config_xml)

    # Languages/Русский.xml
    lang_uuid = new_uuid()
    source_lang_uuid = ""
    source_lang_path = os.path.join(base_config_path, "Languages", "Русский.xml")
    if os.path.isfile(source_lang_path):
        source_lang_uuid = read_source_uuid(source_lang_path, "Language")

    lang_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<MetaDataObject {XMLNS_DECL} version="2.17">\r\n'
        f'\t<Language uuid="{lang_uuid}">\r\n'
        '\t\t<InternalInfo/>\r\n'
        '\t\t<Properties>\r\n'
        '\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\r\n'
        '\t\t\t<Name>Русский</Name>\r\n'
        '\t\t\t<Comment/>\r\n'
        f'\t\t\t<ExtendedConfigurationObject>{source_lang_uuid}</ExtendedConfigurationObject>\r\n'
        '\t\t\t<LanguageCode>ru</LanguageCode>\r\n'
        '\t\t</Properties>\r\n'
        '\t</Language>\r\n'
        '</MetaDataObject>'
    )
    lang_dir = os.path.join(ext_dir, "Languages")
    write_xml(os.path.join(lang_dir, "Русский.xml"), lang_xml)

    # Roles/{prefix}ОсновнаяРоль.xml
    role_uuid = new_uuid()
    role_name = f"{ext_prefix}ОсновнаяРоль"
    role_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<MetaDataObject {XMLNS_DECL} version="2.17">\r\n'
        f'\t<Role uuid="{role_uuid}">\r\n'
        '\t\t<Properties>\r\n'
        f'\t\t\t<Name>{role_name}</Name>\r\n'
        '\t\t\t<Synonym/>\r\n'
        '\t\t\t<Comment/>\r\n'
        '\t\t</Properties>\r\n'
        '\t</Role>\r\n'
        '</MetaDataObject>'
    )
    roles_dir = os.path.join(ext_dir, "Roles")
    write_xml(os.path.join(roles_dir, f"{role_name}.xml"), role_xml)

    return True


# ─── Работа с Configuration.xml (ChildObjects) ──────────────────────────────

def get_existing_child_objects(config_xml_text: str) -> set[tuple[str, str]]:
    """Извлекает существующие записи из ChildObjects Configuration.xml.

    Returns:
        Множество кортежей (TagName, InnerText), например ("Document", "РеализацияТоваровУслуг")
    """
    existing = set()
    # Ищем содержимое ChildObjects
    match = re.search(r'<ChildObjects>(.*?)</ChildObjects>', config_xml_text, re.DOTALL)
    if not match:
        return existing
    child_content = match.group(1)
    # Извлекаем все элементы вида <TagName>InnerText</TagName>
    for m in re.finditer(r'<(\w+)>([^<]+)</\1>', child_content):
        existing.add((m.group(1), m.group(2).strip()))
    return existing


def add_child_objects_to_config(config_xml_path: str, new_entries: list[tuple[str, str]]):
    """Добавляет новые элементы в ChildObjects Configuration.xml.

    Args:
        config_xml_path: путь к Configuration.xml
        new_entries: список (TypeTag, Name) для добавления
    """
    if not new_entries:
        return

    text = read_file(config_xml_path)
    existing = get_existing_child_objects(text)

    # Фильтруем дубликаты
    to_add = [(tag, name) for tag, name in new_entries if (tag, name) not in existing]
    if not to_add:
        return

    # Группируем по типам, сохраняя порядок внутри типа
    type_entries: dict[str, list[str]] = {}
    for tag, name in to_add:
        type_entries.setdefault(tag, []).append(name)

    # Собираем все существующие и новые, сортируем по каноническому порядку
    # Сначала парсим все существующие
    all_entries: dict[str, list[str]] = {}
    match = re.search(r'<ChildObjects>(.*?)</ChildObjects>', text, re.DOTALL)
    if match:
        child_content = match.group(1)
        for m in re.finditer(r'<(\w+)>([^<]+)</\1>', child_content):
            all_entries.setdefault(m.group(1), []).append(m.group(2).strip())

    # Добавляем новые
    for tag, names in type_entries.items():
        if tag not in all_entries:
            all_entries[tag] = []
        for name in names:
            if name not in all_entries[tag]:
                all_entries[tag].append(name)

    # Генерируем новый ChildObjects блок в каноническом порядке
    lines = []
    type_order_map = {t: i for i, t in enumerate(TYPE_ORDER)}
    sorted_types = sorted(all_entries.keys(), key=lambda t: type_order_map.get(t, 999))
    for tag in sorted_types:
        for name in all_entries[tag]:
            lines.append(f"\t\t\t<{tag}>{name}</{tag}>")

    new_child_objects = "\t\t<ChildObjects>\r\n" + "\r\n".join(lines) + "\r\n\t\t</ChildObjects>"

    # Заменяем ChildObjects блок
    if re.search(r'<ChildObjects/>', text):
        text = re.sub(r'<ChildObjects/>', new_child_objects, text)
    else:
        text = re.sub(r'<ChildObjects>.*?</ChildObjects>', new_child_objects, text, flags=re.DOTALL)

    write_xml(config_xml_path, text)


# ─── Генерация XML объектов ──────────────────────────────────────────────────

def generate_object_xml(obj: ObjectInfo, source_uuid: str,
                        cm_props: Optional[dict[str, str]] = None) -> str:
    """Генерирует XML заимствованного объекта.

    Args:
        obj: информация об объекте
        source_uuid: UUID из source конфигурации
        cm_props: свойства CommonModule (если применимо)

    Returns:
        Строка XML
    """
    obj_uuid = new_uuid()
    type_en = obj.type_en

    # InternalInfo с GeneratedType
    internal_info_content = ""
    gen_types = GENERATED_TYPES.get(type_en)
    if gen_types:
        gt_lines = []
        for gen_name_prefix, category in gen_types:
            type_id = new_uuid()
            value_id = new_uuid()
            gt_lines.append(
                f'\t\t\t<xr:GeneratedType name="{gen_name_prefix}.{obj.name}" category="{category}">\r\n'
                f'\t\t\t\t<xr:TypeId>{type_id}</xr:TypeId>\r\n'
                f'\t\t\t\t<xr:ValueId>{value_id}</xr:ValueId>\r\n'
                '\t\t\t</xr:GeneratedType>'
            )
        internal_info_content = "\r\n".join(gt_lines) + "\r\n"

    if internal_info_content:
        internal_info = f'\t\t<InternalInfo>\r\n{internal_info_content}\t\t</InternalInfo>'
    else:
        internal_info = '\t\t<InternalInfo/>'

    # Properties
    props_lines = [
        '\t\t<Properties>',
        '\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>',
        f'\t\t\t<Name>{obj.name}</Name>',
        '\t\t\t<Comment/>',
        f'\t\t\t<ExtendedConfigurationObject>{source_uuid}</ExtendedConfigurationObject>',
    ]
    # CommonModule — дополнительные свойства
    if type_en == "CommonModule" and cm_props:
        for prop_name in COMMON_MODULE_PROPS:
            if prop_name in cm_props:
                props_lines.append(f'\t\t\t<{prop_name}>{cm_props[prop_name]}</{prop_name}>')
    props_lines.append('\t\t</Properties>')

    # ChildObjects
    child_objects = ""
    if type_en in TYPES_WITH_CHILD_OBJECTS:
        if obj.forms:
            form_lines = [f'\t\t\t<Form>{fn}</Form>' for fn in obj.forms]
            child_objects = '\r\n\t\t<ChildObjects>\r\n' + '\r\n'.join(form_lines) + '\r\n\t\t</ChildObjects>'
        else:
            child_objects = '\r\n\t\t<ChildObjects/>'

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<MetaDataObject {XMLNS_DECL} version="2.17">\r\n'
        f'\t<{type_en} uuid="{obj_uuid}">\r\n'
        f'{internal_info}\r\n'
        + '\r\n'.join(props_lines) + '\r\n'
        + (child_objects + '\r\n' if child_objects else '')
        + f'\t</{type_en}>\r\n'
        '</MetaDataObject>'
    )
    return xml


def generate_form_xml(form_name: str, source_form_uuid: str) -> str:
    """Генерирует XML заимствованной формы."""
    form_uuid = new_uuid()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\r\n'
        f'<MetaDataObject {XMLNS_DECL} version="2.17">\r\n'
        f'\t<Form uuid="{form_uuid}">\r\n'
        '\t\t<InternalInfo/>\r\n'
        '\t\t<Properties>\r\n'
        '\t\t\t<ObjectBelonging>Adopted</ObjectBelonging>\r\n'
        f'\t\t\t<Name>{form_name}</Name>\r\n'
        '\t\t\t<Comment/>\r\n'
        f'\t\t\t<ExtendedConfigurationObject>{source_form_uuid}</ExtendedConfigurationObject>\r\n'
        '\t\t\t<FormType>Managed</FormType>\r\n'
        '\t\t</Properties>\r\n'
        '\t</Form>\r\n'
        '</MetaDataObject>'
    )
    return xml


# ─── Обновление ChildObjects объекта (добавление форм) ───────────────────────

def add_forms_to_object_xml(object_xml_path: str, form_names: list[str]):
    """Добавляет формы в ChildObjects существующего XML объекта."""
    text = read_file(object_xml_path)

    # Извлекаем существующие формы
    existing_forms = set()
    match = re.search(r'<ChildObjects>(.*?)</ChildObjects>', text, re.DOTALL)
    if match:
        for m in re.finditer(r'<Form>([^<]+)</Form>', match.group(1)):
            existing_forms.add(m.group(1).strip())

    new_forms = [f for f in form_names if f not in existing_forms]
    if not new_forms:
        return

    new_form_lines = [f'\t\t\t<Form>{fn}</Form>' for fn in new_forms]
    new_forms_text = "\r\n".join(new_form_lines)

    if re.search(r'<ChildObjects/>', text):
        # Пустой ChildObjects — заменяем
        replacement = f'\t\t<ChildObjects>\r\n{new_forms_text}\r\n\t\t</ChildObjects>'
        text = re.sub(r'\t*<ChildObjects/>', replacement, text)
    elif re.search(r'</ChildObjects>', text):
        # Добавляем перед закрывающим тегом
        text = text.replace('</ChildObjects>', f'{new_forms_text}\r\n\t\t</ChildObjects>')
    else:
        # Нет ChildObjects — добавляем перед закрывающим тегом объекта
        # Находим последний закрывающий тег вида </TypeName>
        m = re.search(r'(\t*)</(\w+)>\s*</MetaDataObject>', text)
        if m:
            indent = m.group(1)
            tag = m.group(2)
            child_block = f'{indent}\t<ChildObjects>\r\n{new_forms_text}\r\n{indent}\t</ChildObjects>\r\n{indent}</{tag}>'
            text = re.sub(rf'{re.escape(indent)}</{tag}>\s*</MetaDataObject>',
                          f'{child_block}\r\n</MetaDataObject>', text)

    write_xml(object_xml_path, text)


# ─── Основная логика генерации ───────────────────────────────────────────────

def generate_metadata(objects: list[ObjectInfo], ext_dir: str,
                      base_config_path: str, config: dict,
                      dry_run: bool = False) -> MetadataReport:
    """Генерирует все XML метаданных расширения.

    Args:
        objects: список объектов из deploy-report
        ext_dir: каталог расширения
        base_config_path: путь к типовой конфигурации
        config: словарь конфигурации проекта
        dry_run: только показать что будет сделано

    Returns:
        MetadataReport с результатами
    """
    report = MetadataReport()

    # 1. Scaffold
    if not dry_run:
        report.created_scaffold = create_scaffold(ext_dir, config, base_config_path)
    else:
        config_xml_path = os.path.join(ext_dir, "Configuration.xml")
        report.created_scaffold = not os.path.isfile(config_xml_path)

    if report.created_scaffold:
        print("Scaffold создан: Configuration.xml, Languages/, Roles/")

    # 2. Собираем элементы для ChildObjects Configuration.xml
    config_child_entries: list[tuple[str, str]] = []

    for obj in objects:
        type_en = obj.type_en
        type_dir = TYPE_TO_DIR.get(type_en)
        if type_dir is None:
            print(f"Предупреждение: нет каталога для типа {type_en}, пропуск", file=sys.stderr)
            continue

        obj_xml_path = os.path.join(ext_dir, type_dir, f"{obj.name}.xml")
        obj_report = {
            "type": type_en,
            "name": obj.name,
            "action": "",
            "forms": [],
        }

        # Добавляем в Configuration.xml ChildObjects
        config_child_entries.append((type_en, obj.name))

        # 3. Генерация XML объекта
        if os.path.isfile(obj_xml_path):
            obj.action = "skipped"
            obj_report["action"] = "skipped"
            report.objects_skipped += 1
            print(f"  [{type_en}] {obj.name}: объект уже существует, пропуск")
        else:
            obj.action = "created"
            obj_report["action"] = "created"
            report.objects_created += 1

            # Читаем source UUID
            source_xml_path = os.path.join(base_config_path, type_dir, f"{obj.name}.xml")
            source_uuid = ""
            cm_props = None

            if os.path.isfile(source_xml_path):
                try:
                    source_uuid = read_source_uuid(source_xml_path, type_en)
                except Exception as e:
                    print(f"  Предупреждение: не удалось прочитать UUID из {source_xml_path}: {e}",
                          file=sys.stderr)

                # CommonModule — дополнительные свойства
                if type_en == "CommonModule":
                    try:
                        cm_props = read_common_module_props(source_xml_path)
                    except Exception as e:
                        print(f"  Предупреждение: не удалось прочитать свойства CommonModule: {e}",
                              file=sys.stderr)
            else:
                print(f"  Предупреждение: source XML не найден: {source_xml_path}",
                      file=sys.stderr)

            if not dry_run:
                xml_content = generate_object_xml(obj, source_uuid, cm_props)
                write_xml(obj_xml_path, xml_content)

            print(f"  [{type_en}] {obj.name}: создан")

        # 4. Генерация форм
        for form_name in obj.forms:
            form_xml_path = os.path.join(ext_dir, type_dir, obj.name, "Forms", f"{form_name}.xml")

            if os.path.isfile(form_xml_path):
                obj.form_actions[form_name] = "skipped"
                obj_report["forms"].append({"name": form_name, "action": "skipped"})
                report.forms_skipped += 1
                print(f"    Форма {form_name}: уже существует, пропуск")
            else:
                obj.form_actions[form_name] = "created"
                obj_report["forms"].append({"name": form_name, "action": "created"})
                report.forms_created += 1

                # Читаем source form UUID
                source_form_path = os.path.join(
                    base_config_path, type_dir, obj.name, "Forms", f"{form_name}.xml"
                )
                source_form_uuid = ""
                if os.path.isfile(source_form_path):
                    try:
                        source_form_uuid = read_source_uuid(source_form_path, "Form")
                    except Exception as e:
                        print(f"    Предупреждение: не удалось прочитать UUID формы: {e}",
                              file=sys.stderr)
                else:
                    print(f"    Предупреждение: source form XML не найден: {source_form_path}",
                          file=sys.stderr)

                if not dry_run:
                    form_xml = generate_form_xml(form_name, source_form_uuid)
                    write_xml(form_xml_path, form_xml)

                print(f"    Форма {form_name}: создана")

            # Если объект уже существовал — добавляем форму в его ChildObjects
            if obj.action == "skipped" and not dry_run:
                add_forms_to_object_xml(obj_xml_path, [form_name])

        report.objects.append(obj_report)

    # 5. Обновляем Configuration.xml ChildObjects
    if config_child_entries and not dry_run:
        config_xml_path = os.path.join(ext_dir, "Configuration.xml")
        if os.path.isfile(config_xml_path):
            add_child_objects_to_config(config_xml_path, config_child_entries)

    return report


# ─── Отчёт ──────────────────────────────────────────────────────────────────

def print_text_report(report: MetadataReport, dry_run: bool,
                      report_path: Optional[str] = None):
    """Выводит текстовый отчёт о генерации метаданных."""
    lines = []
    lines.append("# Отчёт генерации метаданных расширения")
    if dry_run:
        lines.append("(dry-run — файлы не записывались)")
    lines.append("")

    if report.created_scaffold:
        lines.append("Scaffold: создан (Configuration.xml, Languages/, Roles/)")
    else:
        lines.append("Scaffold: уже существовал")
    lines.append("")

    lines.append(f"Объектов создано: {report.objects_created}")
    lines.append(f"Объектов пропущено (уже существуют): {report.objects_skipped}")
    lines.append(f"Форм создано: {report.forms_created}")
    lines.append(f"Форм пропущено (уже существуют): {report.forms_skipped}")
    lines.append("")

    for obj in report.objects:
        action_marker = "+" if obj["action"] == "created" else "="
        lines.append(f"  [{action_marker}] {obj['type']}.{obj['name']}")
        for form in obj.get("forms", []):
            form_marker = "+" if form["action"] == "created" else "="
            lines.append(f"      [{form_marker}] Form.{form['name']}")

    report_text = "\n".join(lines)

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"Текстовый отчёт: {report_path}")
    else:
        print()
        print(report_text)


def save_json_report(report: MetadataReport, json_path: str):
    """Сохраняет JSON-отчёт."""
    data = {
        "created_scaffold": report.created_scaffold,
        "objects_created": report.objects_created,
        "objects_skipped": report.objects_skipped,
        "forms_created": report.forms_created,
        "forms_skipped": report.forms_skipped,
        "objects": report.objects,
    }

    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"JSON-отчёт: {json_path}")


# ─── Главная ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Генерация XML-метаданных расширения конфигурации 1С"
    )
    parser.add_argument("work_dir", help="Рабочий каталог (содержит deploy-report.json)")
    parser.add_argument("-c", "--config", default="config.json",
                        help="Путь к конфигу проекта (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать что будет создано, без записи файлов")
    parser.add_argument("--report", help="Путь к файлу текстового отчёта")
    args = parser.parse_args()

    if not os.path.isdir(args.work_dir):
        print(f"Ошибка: каталог не найден: {args.work_dir}", file=sys.stderr)
        sys.exit(1)

    # Читаем конфиг
    if not os.path.exists(args.config):
        print(f"Ошибка: конфиг не найден: {args.config}", file=sys.stderr)
        sys.exit(1)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    ext_output = config.get("extensionOutputPath", "extension")
    if not os.path.isabs(ext_output):
        ext_output = os.path.abspath(ext_output)

    base_config_path = config.get("baseConfigPath", "")
    if not base_config_path:
        print("Ошибка: baseConfigPath не указан в конфиге", file=sys.stderr)
        sys.exit(1)

    # Читаем deploy-report.json
    deploy_report_path = os.path.join(args.work_dir, "deploy-report.json")
    if not os.path.isfile(deploy_report_path):
        print(f"Ошибка: deploy-report.json не найден: {deploy_report_path}", file=sys.stderr)
        sys.exit(1)

    # Парсим объекты
    objects = parse_deploy_report(deploy_report_path)
    if not objects:
        print("Нет объектов для генерации метаданных.")
        return

    print(f"Каталог расширения: {ext_output}")
    print(f"Типовая конфигурация: {base_config_path}")
    print(f"Объектов: {len(objects)}, форм: {sum(len(o.forms) for o in objects)}")
    print()

    # Генерируем метаданные
    report = generate_metadata(objects, ext_output, base_config_path, config,
                               dry_run=args.dry_run)

    # Текстовый отчёт
    print_text_report(report, dry_run=args.dry_run, report_path=args.report)

    # JSON-отчёт
    json_report_path = os.path.join(args.work_dir, "metadata-report.json")
    if not args.dry_run:
        save_json_report(report, json_report_path)

    # Итоговая статистика
    total = report.objects_created + report.objects_skipped
    print(f"\nИтого: {report.objects_created} объектов создано, "
          f"{report.objects_skipped} пропущено, "
          f"{report.forms_created} форм создано, "
          f"{report.forms_skipped} форм пропущено")


if __name__ == "__main__":
    main()
