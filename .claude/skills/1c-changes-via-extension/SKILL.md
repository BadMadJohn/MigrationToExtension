---
name: 1c-changes-via-extension
description: "Доработки типовых модулей через расширения конфигурации"
---

#1C Changes via extension Skill

## Extension Migration Rules

Changed module must be saved as new file with original name and `Ext.bsl` the end.
The name of the modified function must be renamed to `Extension identifier prefix` + `Original Function Name`.
Functions marked with the `&ИзменениеИКонтроль("Original Function Name")` annotation before their definition must follow these rules when porting to an extension:
Example:
```
&ИзменениеИКонтроль("ПолучитьРезультатРасчета")
Функция казк_ПолучитьРезультатРасчета()
```

1. **Insertions** — wrap new code blocks in `#Вставка...#КонецВставки`:
   ```bsl
   #Вставка
   // новые фрагменты кода
   #КонецВставки
   ```

2. **Modifications** — add the modified version inside `#Вставка...#КонецВставки`, then wrap original lines in `#Удаление...#КонецУдаления`:
   ```bsl
   #Вставка
   // изменённые фрагменты кода
   #КонецВставки
   #Удаление
   // исходные фрагменты кода
   #КонецУдаления
   ```

3. **Code inside `#Вставка...#КонецВставки`** — can be modified freely; the above rules do not apply inside it.

4. **Invariant rule** — if you remove all `#Удаление...#КонецУдаления` annotations and all `#Вставка...#КонецВставки` blocks (together with their content), the remaining code must be identical to the original function. it is very **Important**. Check this at the end of changes apply.

## BSL Extension Conventions

- Override procedures use the `&Вместо("OriginalProcedureName")` annotation and must be named with the extension prefix: `ExtPrefix_OriginalProcedureName`.
- New procedures in `_Ext.bsl` file have no prefix annotation and are added directly to the new module file.
