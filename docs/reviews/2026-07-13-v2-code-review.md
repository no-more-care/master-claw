# MasterClaw v2 — независимое ревью реализации (2026-07-13)

> Контекст: 2026-07-13 весь `src/masterclaw/` был написан заново на ветке `develop-v2` за два коммита
> (`d390951` — фундамент, `c6da00e` — телеметрия и safeguards) по мотивам
> `docs/openhands-refactoring-plan.md`. Автор реализации закончил словами «код готов, дальше нужны
> реальные тесты». Это ревью — проверка кода до разворачивания: что реально работает, что не
> проверено, что стоит доделать до выхода на стейджинг.
>
> Метод: прочитан весь `src/masterclaw/` (домен, приложение, пайплайны, контекст, хранилище,
> адаптеры, cli), прогнан `pytest` (83/83 зелёных), выполнена попытка реального импорта
> `openhands.sdk` в установленном `.venv`, проверены `pyproject.toml`, CI-workflow, Docker/Compose/
> systemd-файлы и локали.

## 1. Итог одним абзацем

Детерминированное ядро (роутинг режимов, механика BlackBirdPie, state machine, хранилище с
idempotency/outbox) написано аккуратно, малыми чистыми функциями, и реально покрыто тестами —
это самая сильная часть работы, она попадает точно в то, что описывал план. Слабое место —
именно то, ради чего затевался переезд: интеграция с OpenHands SDK ни разу не была
исполнена — ни одним тестом, ни в текущем локальном окружении. «Код готов» здесь означает
«домен и оркестрация готовы»; LLM-контур (реальные вызовы, реальный Discord, реальный
OpenRouter) — открытый вопрос, который сам план и `v2-implementation-status.md` тоже прямо
признают («cannot be performed safely from a source checkout without deployment secrets»).

## 2. Что реализовано и как это устроено

### 2.1 Роутинг режимов — полностью детерминированный

`src/masterclaw/domain/routing.py:17` — `ModeRouter.route()` — чистая функция без LLM. Слэш-команда
или отсутствие игры в канале → `WORLD_MANAGEMENT`; `DRAFT`/`PREPARING` → `PREPARATION`; `ACTIVE` →
`PLAY`. В точности соответствует разделу 6.1 плана: код решает верхний уровень, модель никогда не
выбирает режим.

### 2.2 Диспетчеризация сообщения (`MessageApplication`)

`src/masterclaw/app/message_handler.py:88` (`_dispatch`) — центральная точка входа для одного
сообщения:

1. считает `command` (первое слово, если начинается с `/`);
2. вызывает `ModeRouter.route`;
3. если это `PLAY` и есть игра — обновляет activity-clock (`record_activity`);
4. slash-команды (`/world`, `/game`, `/character`, `/xp`, `/advance`, `/help`) разбираются
   `_handle_command` без единого обращения к LLM — это чистый Python `if/elif` по `shlex.split`;
5. если сообщение — подтверждение уже закоммиченного броска (по `event_id`), результат
   восстанавливается из БД без повторного броска (`roll_for_confirmation_event`) — реализует
   crash-safe resume из раздела 6.4 плана; проверено тестом
   `test_committed_roll_can_resume_from_same_discord_event_without_open_pending`;
6. `_deterministic_intent` (строка 149) кодом перехватывает очевидные случаи (команда, «да»/«нет»,
   число как ответ на пул) — к LLM-классификатору интента обращаются только когда код сам не
   уверен. Это ровно тот «дешёвый rule-based classifier перед LLM», который требует раздел 6.1;
7. только `ACTION_DECLARATION`/`MIXED_NARRATION_ACTION` в режиме `PLAY` уходят в
   `_handle_action_declaration`.

### 2.3 Пайплайны — узкие typed-контракты поверх одного примитива

`src/masterclaw/pipelines/base.py:21` — `BoundedJsonPipeline`: один системный промпт, JSON-схема из
Pydantic-модели встраивается в запрос, ответ парсится `model_validate_json`; при ошибке — **один**
repair-запрос с описанием ошибки валидации, при повторном провале — исключение
`PipelineValidationError`. Это прямая реализация раздела 9.4 плана («максимум один ограниченный
повтор LLM»).

Каждый из 8 пайплайнов (`pipelines/intent.py`, `action.py`, `consequence.py`, `narrative.py`,
`player_narration.py`, `advancement.py`, `character_creation.py`, `worldgen.py`) — это отдельная
Pydantic-схема с `extra="forbid"` и узкий системный промпт, который явно перечисляет, чего пайплайн
**не может** делать («never roll dice», «do not modify characters», «cannot change game state»).
Схемы валидируют структуру домена (например `ActionInterpretation.validate_resolution_fields`
запрещает `roll` без `trait_names`/`difficulty`, `NarrativeResult.reject_internal_formatting`
режет утечку служебных терминов вроде `system prompt`/`tool_call` в прозу). Это соответствует
разделам 8 и 9.2 плана.

Модельная роль на пайплайн зафиксирована в `context/manifests.py:32` (`MANIFESTS`) —
`ModelRole.STATE` для классификации/gate-решений, `REASONING` для интерпретации и генерации,
`NARRATIVE` для прозы, как и предписано разделом 17 п.14 плана. Сейчас все три роли в
`.env.example` указывают на одну и ту же модель (`grok-4.5`) — это осознанное решение владельца на
время полевого тестирования, не дефект реализации.

### 2.4 Сборка контекста

`src/masterclaw/context/assembler.py:22` — `ContextAssembler.assemble()` берёт манифест пайплайна
(`ContextManifest`), подтягивает файлы правил из `prompts/manifest.json` по `rule_fragments` и
JSON-сериализует переданные `state_projections`, считает `estimated_tokens` (грубая оценка `len/4`)
и роняет сборку, если она превышает `input_token_budget` манифеста. Понятие «контракта на контекст»
из раздела 7.2 плана реализовано буквально.

### 2.5 Механика — чистые функции, без побочных эффектов

`src/masterclaw/domain/mechanics.py` — `validate_pool`, `roll_pool` (реальный `secrets.randbelow`,
инъекция `die` для тестов), `narrator_rights`, `reserve_after_roll`, `validate_character`
(3–9 черт, 18 очков, аспекты = уровень, обязательный `RELATIONSHIP`-флаг). Всё — чистые функции без
скрытого состояния, что делает их тривиально тестируемыми (`tests/test_mechanics.py`, 119 строк).
LLM здесь появляется только на этапе **предложения** какие traits/aspects использовать
(`ActionInterpretation`) — сам расчёт пула, бросок и права рассказчика код проверяет ещё раз
(`ActionService.confirm_roll`, `src/masterclaw/app/action_service.py:98`) независимо от того, что
предложила модель. Это реализует «LLM не пишет каноническое состояние напрямую» из раздела 2.4
плана.

### 2.6 Хранилище и надёжность

`src/masterclaw/storage/sqlite.py` — единственный класс `SQLiteStore` (см. §3.6 ниже про размер).
Ключевые механизмы, которые реально протестированы:

- `enqueue`/`claim_pending`/`complete_batch`/`fail_batch` — durable inbox с idempotency по
  `event_id`, восстановление зависших `processing`-записей при рестарте
  (`recover_interrupted_work`);
- `commit_roll` — один бросок на `PendingInteraction`, идемпотентен по `confirmation_event_id`
  (`roll_for_confirmation_event`);
- optimistic concurrency через `expected_revision` почти на каждой мутации (`update_world_content`,
  `apply_scene_patch`, `update_character_progression`, `resolve_pending` и т.д.) — совпадает с
  «revision на каждый агрегат» из раздела 5.1 плана;
- `pending_outbox`/`mark_delivered`/`mark_delivery_failed` — транзакционный outbox, чанки уже
  порезаны на Discord-safe куски (`response_format.split_discord_message`) до записи в БД, у
  каждого чанка свой idempotency-суффикс.

### 2.7 Discord-адаптер и деплой

`src/masterclaw/adapters/discord_bot.py` — тонкий слой: on_message пишет в durable inbox
(`store.enqueue`) и планирует debounce-обработку канала; `on_ready` восстанавливает прерванную
работу и публикует зависший outbox. Индикатор «печатает» держится на всё время обработки батча.
Топология деплоя (`Dockerfile`, `compose.yaml`, `deploy/masterclaw.service`,
`deploy/masterclaw-backup.*`) соответствует зафиксированным в плане дефолтам: один контейнер,
персистентный volume, WAL SQLite, systemd поверх Compose, docker healthcheck дергает
`masterclaw doctor`.

## 3. Обнаруженные проблемы

Приоритет: **Critical** — блокирует любую реальную (не unit-test) проверку; **High** — не блокирует
запуск, но оставляет систему без защиты от вполне вероятного сбоя; **Medium** — не мешает сейчас,
но повышает стоимость дальнейшей работы; **Low** — стоит знать, чинить не срочно.

### 3.1 [Critical] Интеграция с OpenHands SDK не выполнена ни разу

`grep -rl openhands tests/` — пусто. Все 83 теста проходят через `CompletionPort`-протокол
(`src/masterclaw/pipelines/base.py:13`) с фейковыми реализациями (см. `SequenceCompletion` в
`tests/test_play_flow.py:18`). Реальный код, который импортирует `openhands.sdk.LLM/Message/
TextContent` и вызывает `self._llm.completion(messages)` — `src/masterclaw/adapters/openhands.py:22-61`
— не запускается ни в одном тесте и не запускается в CI. Само название параметров
(`openrouter_app_name`, `cache_prompt=True` на `TextContent`, `response.metrics.accumulated_cost`,
`response.raw_response.usage.prompt_tokens_details`) — это предположения о текущей форме API SDK
1.33.0; комментарий в коде честно об этом предупреждает («Return type follows the installed SDK
version»), но сейчас ничто не проверяет, что эти предположения верны.

**Проверить перед реальным тестированием:** `masterclaw model-smoke` с настоящим
`MASTERCLAU_OPENROUTER_API_KEY` — это единственный путь, который реально дергает `OpenHandsLLMRegistry
.create()` (`src/masterclaw/model_smoke.py:12`). До этого момента нельзя быть уверенным, что
конструктор `LLM(...)` и разбор `response` не упадут на реальном ответе OpenRouter.

### 3.2 [Critical] Локальное окружение не может импортировать `openhands.sdk`

В `.venv` репозитория `pip check` показывает 16 отсутствующих прямых зависимостей `openhands-sdk`
(`litellm`, `httpx`, `fastmcp`, `rich` и т.д.) — `import openhands.sdk` падает на
`ModuleNotFoundError: No module named 'rich'` уже на инициализации пакета. Также нет lock-файла
(`uv.lock`/`requirements*.txt`) с зафиксированными транзитивными версиями — `pip install -e ".[dev]"`
не гарантирует одинаковый набор пакетов на разных машинах/датах. Это может быть особенностью именно
этой локальной установки, а не бага в `pyproject.toml`, но без lock-файла это нельзя ни
подтвердить, ни исключить — стоит проверить `docker compose build` + `masterclaw model-smoke` на
целевом Linux-хосте прежде, чем доверять окружению.

### 3.3 [High] CI не ловит ни 3.1, ни 3.2

`.github/workflows/ci.yml` гоняет `ruff check`, `ruff format --check`, `pytest -q` и отдельно
`docker build` (без запуска контейнера). Ни `masterclaw doctor`, ни `masterclaw model-smoke` не
вызываются нигде в CI. Поломка импорта SDK, дрейф его API или неверная конфигурация ролей моделей
попадут в `develop-v2` незамеченными — их обнаружит только человек, руками запустивший
`model-smoke` перед стейджингом (это уже написано в `docs/v2-deployment.md` как чеклист, но нигде
не автоматизировано).

### 3.4 [High] `recent_domain_events` / `recent_chat_messages` из манифестов не используются

Каждый `ContextManifest` в `src/masterclaw/context/manifests.py` объявляет
`recent_domain_events`/`recent_chat_messages` (например `ACTION_INTERPRETATION`: 6 событий, 2
сообщения; `OUTCOME_NARRATION`: 4 события, 1 сообщение). Но `ContextAssembler.assemble()`
(`src/masterclaw/context/assembler.py:28-63`) читает только `rule_fragments` и `state_projections`
— оба поля истории нигде не читаются (`grep -rn "recent_domain_events\|recent_chat_messages"
src/masterclaw` находит их только в определении манифеста). На практике это значит: пайплайны
интерпретации действия и нарратива сейчас получают **только текущий срез состояния**
(`session_brief`/`actor_character`/`current_scene`/`roll_result`), без единого события недавней
истории или реплики диалога, хотя манифест обещает бюджет именно под них. Раздел 7.2 плана прямо
описывает `history: {domain_events: N, chat_messages: N}` как часть context contract — здесь это
объявлено, но не подключено. Пока сцена меняется только через `apply_scene_patch`
(`_ensure_scene_consequence`), это может не бросаться в глаза на коротких сессиях, но на длинных
диалогах (несколько реплик подряд без явного изменения `scene facts`) нарратив рискует терять нить
разговора — то самое, чего пытался избежать раздел 5.2 плана через `recent_events`.

**Что делать:** либо реализовать выборку `recent_domain_events`/`recent_chat_messages` в
`ContextAssembler`/вызывающем коде и подмешивать в `state_projections`, либо явно удалить эти поля
из манифеста, если решено, что снапшота состояния достаточно — но текущее расхождение между
объявленным контрактом и фактическим поведением стоит устранить осознанно, а не оставлять как
скрытый дефолт.

### 3.5 [Medium] Нет инструмента для покрытия тестами

В `pyproject.toml` нет `pytest-cov`/`coverage`. «83 passed» ничего не говорит о том, какая доля
кода реально исполняется тестами — особенно учитывая находку 3.1 (adapters/openhands.py — 0%
гарантированно). Рекомендуется добавить `pytest-cov` и хотя бы отслеживать тренд, не обязательно
ставить жёсткий порог с самого начала.

### 3.6 [Medium] Два «толстых» модуля-накопителя

- `src/masterclaw/app/message_handler.py` — один класс `MessageApplication` на 772 строки: и
  диспетчеризация, и разбор всех slash-команд, и обработка деклараций действия, и рендер исхода
  броска, и работа с consequence-пайплайном. План (раздел 2, принцип 3) настаивает на «одна
  LLM-задача — один узкий контракт»; на уровне не-LLM оркестрации это правило сейчас не
  соблюдается — по мере добавления команд (мир, оператор, продвинутая прокачка) файл будет расти
  дальше в одном месте.
- `src/masterclaw/storage/sqlite.py` — один класс `SQLiteStore` на 1494 строки на все агрегаты
  (worlds, games, scenes, characters, pending interactions, rolls, inbox, outbox, telemetry,
  dead-letter). При текущем масштабе (SQLite, один воркер, явно принятое решение — раздел 17 п.5
  плана) это не блокер, но это самый дорогой в поддержке файл репозитория уже сейчас.

Ни то, ни другое не мешает первому стейджинг-тесту; стоит иметь в виду при следующей крупной
итерации (например, разбить `SQLiteStore` на repository-классы по агрегатам, а
`MessageApplication` — на отдельные command-handlers).

### 3.7 [Medium] `CLAUDE.md` и `docs/architecture.md` описывают v1, не v2

`CLAUDE.md` (корень репозитория) целиком описывает старую архитектуру microClaw: souls/skills,
деплой на `/root/.microclaw/`, ветку `develop`, модель `kimi-k2.5` — ни слова про `develop-v2`,
OpenHands SDK или `masterclaw`. То же для `docs/architecture.md` (v1-документ). `README.md` уже
обновлён и явно помечает v1-материал как «archived legacy» — но `CLAUDE.md` (файл, который читают
именно Claude Code/Sol-сессии как основной контекст проекта) остался прежним. Риск: следующая
сессия, ориентирующаяся по `CLAUDE.md`, будет рассуждать в терминах microClaw-архитектуры о
проекте, который уже полностью переписан.

**Рекомендация:** обновить `CLAUDE.md` под `develop-v2` (или хотя бы добавить наверх заметный
блок-редирект на `docs/openhands-refactoring-plan.md` и `docs/v2-implementation-status.md`, как
это уже сделано в `README.md`).

### 3.8 [Low] Docker healthcheck не проверяет LLM-связность

`compose.yaml:7-12` — healthcheck дергает `masterclaw doctor`, который проверяет только конфиг, БД
и наличие `prompts/manifest.json` (`src/masterclaw/cli.py:49-57`) — ни одного реального вызова к
OpenRouter. Контейнер может быть «healthy» и при этом ронять каждое обращение к LLM (неверный
`model` id, исчерпанный ключ, недоступная роль). `v2-deployment.md` уже требует ручного
`model-smoke` в чеклисте приёмки — стоит рассмотреть либо периодический (не при каждом healthcheck,
чтобы не жечь токены) фоновый прогон `model-smoke`, либо хотя бы алерт при первом провале
LLM-вызова в проде (телеметрия в `llm_calls` уже это фиксирует, не хватает только оповещения).

### 3.9 [Low] Нет автоматического оповещения при системном сбое доставки в Discord

`src/masterclaw/adapters/discord_bot.py:85-99` (`_publish_outbox`) — при ошибке `channel.send`
запись помечается `mark_delivery_failed` и повторно пробуется на следующем цикле; если ошибка
системная (боту не хватает прав, канал удалён), доставка будет молча буксовать до тех пор, пока
оператор не заглянет в `masterclaw dead-letter list` руками. Это соответствует плану (раздел 11 —
«compensating command, not silent edit»), но операционно стоит когда-нибудь добавить хотя бы лог-
based алерт поверх `failed_outbox`.

## 4. Что не является проблемой (осознанные решения, зафиксированные владельцем)

- Единая модель (`grok-4.5`) для всех трёх ролей в `.env.example` — временный выбор на время
  полевого тестирования, не архитектурный дефект;
- отсутствие поддержки провайдеров кроме OpenRouter, Telegram/web UI, k8s — прямо исключено
  разделом 17 плана;
- один SQLite-воркер без параллельных игр — явное решение раздела 17 п.5;
- отсутствие импорта данных v1 — «clean start», раздел 17 п.3.

## 5. Рекомендованный порядок действий перед стейджингом

1. Прогнать `docker compose build` и `masterclaw doctor` + `masterclaw model-smoke` с реальными
   `MASTERCLAW_DISCORD_TOKEN`/`MASTERCLAW_OPENROUTER_API_KEY` на целевом Linux-хосте — это первая и
   единственная реальная проверка находок 3.1/3.2.
2. Решить находку 3.4 (`recent_domain_events`/`recent_chat_messages`) осознанно — реализовать или
   явно выпилить из манифеста, прежде чем судить о качестве нарратива на живых сессиях: сейчас
   любая деградация «модель забывает, что было пару реплик назад» будет закономерным следствием
   архитектуры, а не капризом модели.
3. Добавить `masterclaw doctor`/`model-smoke` (или их безопасный dry-run эквивалент без реальных
   токенов) в CI, чтобы дрейф SDK API ловился автоматически, а не только на стейджинге.
4. Обновить `CLAUDE.md` под v2 (находка 3.7) — до, а не после следующей сессии Sol.
5. Пункты 3.5/3.6/3.8/3.9 — не блокеры первого теста; вернуться к ним после первого живого прогона
   кампании, когда будет понятно, что реально болит на практике.
