# MasterClaw v2 — закрытие повторного ревью

Документ фиксирует решения по
`2026-07-13-v2-followup-review.md`. Исправления выполнены без слоя обратной совместимости и без
schema/prompt version numbers согласно текущему решению владельца проекта.

| Пункт ревью | Решение |
|---|---|
| Локализация детерминированного слоя | Закрыто: единый RU/EN-каталог для всех game-bound команд, статусов, ошибок, pool/roll blocks, pending prompts и terminal notices. До привязки игры management UI использует RU по умолчанию. |
| Fallback после immutable roll | Закрыто: ошибка consequence возвращает уже закоммиченную механику без scene patch и narrative delivery. |
| Narrator-rights semantics | Закрыто: живой fragment определяет `disabled`, `minor`, `significant`, `madness`, hard limits и `gm_automatic`. |
| Секционная генерация мира | Закрыто: outline -> public sections -> secret plot -> consistency critic -> publish; ids/factions проверяет код. |
| История нескольких сцен | Закрыто: при наличии actor location выбираются сообщения только авторов той же сцены. |
| Версии prompt fragments | Не возвращены намеренно. Вместо них каждый LLM call получает SHA-256 fingerprint фактических system rules + schema + tool + transport. |
| `len/4` token estimate | Закрыто: tokenizer берётся через LiteLLM по модели роли; для неизвестной модели используется консервативный UTF-8 fallback. |
| `gm_automatic` вне терминологии | Закрыто типом `OutcomeAuthority`; roll-only `NarratorRights` не расширяется значением без броска. |
| «декларация»/«заявка» | UI унифицирован на «игровая заявка»; declaration остаётся именем поля/контракта в коде. |
| Толстые модули и adapter coverage | Локализация и worldgen вынесены из `MessageApplication` в отдельные модули. Массовое дробление SQLite не выполнялось без доменной необходимости. Добавлены прямые тесты Discord ingress/outbox и CLI init/backup/parser. |

Регрессионные тесты проверяют английскую механику и команды, отсутствие публикации мира после
critic/cross-reference failure, scene-local history, consequence fallback, модельный tokenizer,
prompt fingerprint и полноту narrator-rights fragment.

Итоговая локальная проверка после исправлений: 103 теста, 83% line coverage; Discord adapter вырос
с 0% до 59%, CLI — с 0% до 51%. Дальнейшее повышение покрытия остаётся обычной эволюцией тестов,
а не блокером текущего игрового контура.
