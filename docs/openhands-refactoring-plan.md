# MasterClaw v2 — план архитектурного рефакторинга на OpenHands SDK

> Статус: проектирование, без реализации.  
> База анализа: ветка `develop`, commit `03cff1b`.  
> Целевая ветка: `develop-v2`.

## 1. Цель и исходная проблема

MasterClaw v1 — набор больших инструкций для универсального агента microClaw. Модель сама определяет режим, читает нужные файлы, выбирает инструменты, соблюдает порядок механик и записывает состояние. Скрипты уже закрывают часть детерминированной работы, но их обязательный вызов всё ещё обеспечивается главным образом промтом.

Это создаёт четыре системных риска:

1. дешёвая модель пропускает шаг, инструмент или валидацию;
2. длинная история вытесняет актуальные правила и состояние;
3. один агент одновременно маршрутизирует, применяет механику, пишет прозу и управляет файлами;
4. markdown-файлы являются одновременно хранилищем, пользовательским представлением и нестрогим API для модели.

Цель v2 — сделать LLM ограниченным вычислительным компонентом внутри детерминированного приложения. Код должен решать, **какая задача выполняется, какой контекст нужен, какие действия разрешены, что необходимо проверить и что можно сохранить**. Модель должна выполнять только те операции, которые трудно надёжно формализовать: смысловая классификация в неоднозначных случаях, творческая генерация, интерпретация декларации и формулировка ответа.

## 2. Основные архитектурные принципы

1. **State first, history second.** Каноническое состояние игры важнее истории чата. История — источник аудита и краткого локального контекста, но не база фактов.
2. **Сначала автоматический роутинг.** Режим определяется кодом до обращения к LLM.
3. **Одна LLM-задача — один контракт.** У каждого вызова узкий вход, структурированный выход, лимит токенов и собственные проверки.
4. **LLM не пишет каноническое состояние напрямую.** Она предлагает typed patch; приложение валидирует и атомарно применяет его.
5. **Механика не является задачей LLM.** Кубы, пул, резерв, лимиты, права рассказчика и схемы персонажей вычисляются кодом.
6. **Минимальные capability sets.** Каждому pipeline выдаются только нужные инструменты. Набор инструментов OpenHands нельзя менять при resume одной conversation, поэтому разные capability sets должны жить в разных типах conversation или выполняться вне агентского цикла.
7. **Fail closed.** Не прошедший gate результат не публикуется и не коммитится.
8. **Идемпотентность.** Повтор Discord event, retry LLM или рестарт процесса не должен удваивать бросок, сообщение или изменение состояния.
9. **Разделение core и adapters.** Игровой движок не зависит от Discord, OpenRouter или файлового формата.
10. **Промты — версионируемый материал.** Текущие souls/skills переразбираются на небольшие контекстные модули, но не остаются исполняемой архитектурой.

## 3. Что есть в текущей реализации

### 3.1 Полезные активы для переноса

- `skills/rules/SKILL.md` — наиболее полный источник правил BlackBirdPie;
- `skills/characters/SKILL.md` — схема персонажа и ограничения;
- `skills/actions/SKILL.md` — последовательность обработки декларации;
- `skills/narrator`, `world`, `worldgen`, `session`, `scenes`, `narrative` — материал для task-specific prompts;
- locale templates `ru`/`en` — основа presentation layer;
- `build_pool.py` — проверяемая сборка пула;
- `roll.py` — реальный RNG и вычисление прав рассказчика;
- `turn_commit.py` — прототип атомарного применения нескольких изменений;
- `scene_note.py` — прототип строгой записи scene/NPC sheets и графа сцен;
- `session_snapshot.py` — прототип materialized view для краткого контекста;
- `render_response.py`, `lint_lang.py`, `post_narrative.py` — прототипы output gates;
- накопленные модельные тесты в `CLAUDE.md` — начальный regression corpus.

### 3.2 Что требуется заменить

- souls как монолитные системные промты;
- самостоятельное чтение моделью файлов и выбор skills;
- markdown как каноническое изменяемое хранилище;
- ручной tool loop для обязательных операций;
- привязанные к microClaw model/channel management scripts;
- webhook как основную Discord-интеграцию;
- периодическое «перечитать всё каждые 10–15 сообщений»;
- надежду на то, что модель сама выполнит `turn_commit` после броска.

## 4. Предлагаемая схема компонентов

```text
Discord / Admin API / CLI
          |
          v
  Ingress + identity + deduplication
          |
          v
  Deterministic Mode Router
     |         |          |
  WORLD     PREPARE      PLAY
     |         |          |
     +---- Command / Intent Router ----+
                                       |
                                       v
                         State-machine transition planner
                                       |
                         +-------------+-------------+
                         |                           |
                 deterministic services       LLM pipelines
                 rules, dice, schemas,         classify/plan/
                 permissions, rendering        narrate/generate
                         |                           |
                         +-------------+-------------+
                                       v
                              Validation gates
                                       |
                                atomic commit
                                       |
                         outbox -> Discord publisher
```

Рекомендуемые модули Python-проекта:

```text
src/masterclaw/
  app/                 # use cases и orchestration
  domain/              # модели, правила, state machine, invariants
  pipelines/           # узкие LLM pipelines и их контракты
  context/             # selectors, budgets, prompt assembly
  prompts/             # статические фрагменты и версии
  gates/               # pre/post/transition/publish validators
  storage/             # repositories, unit of work, migrations
  adapters/discord/    # bot, commands, message mapping
  adapters/llm/        # OpenHands, provider/model registry
  adapters/rng/        # crypto RNG / seeded RNG for tests
  observability/       # traces, metrics, audit
tests/
  unit/ contract/ integration/ scenarios/ model_eval/
```

## 5. Каноническое состояние

### 5.1 Хранилище

Для первого production-варианта рекомендуется PostgreSQL; SQLite допустим для локальной разработки и single-instance prototype. Markdown следует оставить экспортным и человекочитаемым форматом, а не primary storage.

Минимальные агрегаты:

- `World`: метаданные, публичный guide, секретный plot, NPC, locations, factions, world version;
- `Game`: world reference, mode, lifecycle status, locale, style, narrator-rights policy, Discord bindings;
- `Session`: active participants, current scene, phase, pending interaction, turn sequence;
- `Character`: traits, aspects, flags, conditions, reserve, experience, revision;
- `Scene`: stable facts, sensory details, exits/links, present entities, state changes;
- `NPC`: identity, motivation, knowledge, relationships, interaction memory;
- `PendingAction`: декларация, actor, proposed pool, difficulty, confirmation status, expiry;
- `Roll`: immutable RNG result, pool, difficulty, hits, narrator rights, idempotency key;
- `DomainEvent`: append-only audit trail;
- `OutboxMessage`: предназначенная для Discord публикация и её delivery status.

Каждый изменяемый агрегат получает `revision`. Commit выполняется с optimistic concurrency: патч, рассчитанный для revision N, не применяется к N+1.

### 5.2 Снимки и история

Для контекста создаются materialized projections:

- `session_brief` — текущая сцена, участники, активные угрозы и незакрытые вопросы;
- `character_brief` — только релевантные действию элементы листа;
- `recent_events` — последние значимые domain events, а не все Discord-сообщения;
- `scene_neighborhood` — текущая сцена плюс ближайшие связи;
- `npc_memory` — только сведения выбранных NPC;
- `world_constraints` — неизменяемые факты, релевантные текущей задаче.

Полная event history хранится для аудита и пересборки projections, но никогда автоматически не отправляется модели целиком.

## 6. Верхнеуровневое дерево решений

### 6.1 Первый автоматический split

```text
Есть активная game/session binding для канала?
  нет -> WORLD_MANAGEMENT или PREPARATION по команде/явному workflow
  да  -> lifecycle state игры
          DRAFT/PREPARING -> PREPARATION
          ACTIVE          -> PLAY
          PAUSED/FINISHED -> session command gate
```

Приоритет сигналов:

1. slash command / UI action;
2. сохранённая channel binding и pending state;
3. точная команда из parser/registry;
4. дешёвый rule-based intent classifier;
5. узкий LLM classifier только для оставшейся неоднозначности;
6. безопасный запрос уточнения без изменения state.

Модель не должна выбирать верхнеуровневый режим из общего промта.

### 6.2 WORLD_MANAGEMENT

Ветви: создать мир, продолжить draft, изменить секцию, валидировать, показать preview, опубликовать версию, архивировать.

Контекст: worldgen fragment, пользовательский brief, существующая редактируемая секция, cross-section constraints, locale. Не загружаются правила хода, активные персонажи, session log и Discord-диалоги.

Генерация выполняется секционно: outline -> structured sections -> cross-reference validation -> consistency critic -> publish. Один огромный вызов «создай весь мир» не используется.

### 6.3 PREPARATION

Ветви: создать игру, выбрать мир, настроить стиль/права, привязать каналы, создать/присоединить персонажа, проверить readiness, запустить session.

Запуск разрешён только если readiness gate подтверждает обязательные сущности и непротиворечивые bindings. Диалоговые вопросы формируются из machine-readable списка недостающих полей.

### 6.4 PLAY

Сначала проверяется `PendingAction`:

- ожидается подтверждение пула -> принимаются confirm/change/cancel;
- ожидается narration от игрока -> валидируется предложенная narration;
- ожидается выбор -> принимается только один из разрешённых вариантов или cancel;
- pending state отсутствует -> классифицируется новое сообщение.

Новое сообщение относится к одной из ветвей:

- out-of-game command;
- вопрос о сцене/правилах;
- чистая player narration;
- декларация действия;
- смешанное сообщение narration + declaration;
- session management;
- нерелевантное/неоднозначное.

Для декларации:

```text
parse declaration
 -> validate actor/equipment/permissions
 -> determine auto-success vs roll-required
 -> if roll: propose traits/aspects/flag/difficulty
 -> deterministic pool validation
 -> persist PendingAction
 -> ask player confirmation
 -> on confirm: atomic RNG roll
 -> deterministic result + rights calculation
 -> LLM outcome narration from bounded facts
 -> validate proposed state patch
 -> atomic commit + outbox
```

Implemented vertical-slice detail: the base pool is proposed without reserve dice; the player supplies reserve in the pending confirmation response. The immutable roll stores the Discord confirmation event id. If the worker crashes after committing the roll but before committing delivery, replay resumes from the stored roll and cannot invoke RNG or spend reserve again. Mechanical output and narrative-channel prose are inserted into the outbox with the same inbox-batch transaction.

The `AUTOMATIC` branch must not infer persistent consequences from prose. Until a typed state-patch contract and transition gates are implemented, it may identify that no roll is required but is not considered a complete production action path.

### 6.5 Active-time and advancement flow

The game clock starts when preparation transitions to `ACTIVE`. Every persisted player message advances credited time by `min(time since the preceding game event, 5 minutes)`; duplicates and out-of-order events add zero. This keeps continuous Discord play close to wall-clock time while capping long pauses.

Progression is enabled or disabled in session settings before transition to `ACTIVE` and cannot be toggled during play. When enabled, crossing each new full 30-minute interval automatically awards one XP to every character. Awarded intervals are persisted, so duplicate and out-of-order Discord events cannot grant XP twice. A short one-shot runs with progression disabled.

Advancement is a separate policy from starting-character validation. Raising a trait costs its new level and adds one aspect; there is no level-6 advancement ceiling. Learning a new trait costs 3 XP and creates it at level 2 with two aspects. A bounded GM/state pipeline decides whether the current scene provides enough safety and downtime; its permit is bound to the scene revision. Learning also requires a persisted justification. Discord players have equal permissions.

## 7. Context Engine

### 7.1 Слои контекста

Каждый LLM request собирается из пяти блоков:

1. `static_system_message` — короткая стабильная роль, безопасность и формат ответа; пригоден для prompt caching;
2. `task_contract` — конкретная задача, JSON schema результата, запрещённые действия;
3. `rule_slice` — только правила, нужные этому pipeline;
4. `state_slice` — projections с revision и provenance;
5. `interaction_slice` — текущее сообщение и небольшой локальный диалог, если необходим.

OpenHands разделяет cacheable static system message и dynamic context; это следует использовать намеренно, не склеивая их в один постоянно меняющийся промт.

### 7.2 Реестр зависимостей контекста

Вместо свободного чтения файлов вводится декларация:

```yaml
pipeline: action_interpretation
requires:
  rules: [declaration_validation, difficulty, equipment]
  state: [session_brief, actor_character, current_scene]
  history: {domain_events: 6, chat_messages: 2}
forbids:
  - secret_world_plot_unless_relevant
  - other_private_character_data
budget:
  input_tokens: 12000
  output_tokens: 1200
```

Context builder проверяет наличие, ACL, revision, размер и provenance каждого блока. При превышении бюджета сокращаются projections по заранее заданной политике; модель не суммаризирует state на лету как единственный источник истины.

### 7.3 Переработка текущих промтов

Текущие файлы разбираются на атомарные fragments:

- canonical rules — по разделам механики;
- procedure hints — только там, где решение действительно остаётся модели;
- narrative policies — перспектива, темп, стиль, права;
- world generation rubrics;
- locale phrasing/examples;
- anti-patterns — в основном превращаются в кодовые gates и тесты.

Каждый fragment получает `id`, `version`, `scope`, `dependencies`, `token_estimate` и regression tests. Дубли из souls/skills удаляются после сравнения с каноническим источником.

## 8. LLM pipelines на OpenHands SDK

OpenHands используется как provider-agnostic execution layer: `LLM` поверх LiteLLM, typed custom tools, conversation/event instrumentation, retry/metrics и при необходимости remote agent server. Но основной игровой workflow не должен становиться одной бесконечной OpenHands conversation.

Рекомендуемые pipelines:

| Pipeline | LLM нужна | Выход | Примечание |
|---|---:|---|---|
| mode routing | обычно нет | enum | только fallback classifier |
| intent classification | иногда | typed intent | дешёвая модель, низкий budget |
| declaration interpretation | да | structured proposal | без права менять state |
| difficulty proposal | опционально | value + evidence | финальные рамки проверяет код |
| outcome narration | да | prose + fact references | roll result уже задан |
| scene answer | да | prose | bounded scene context |
| player narration review | иногда | accept/reject + reasons | hard limits частично кодом |
| world outline/section | да | typed section | секционная генерация |
| consistency critic | да/правила | findings | отдельная модель/вызов |
| summarization projection | да, offline | candidate brief | не канонический state |

Для каждого pipeline задаются primary/fallback models, timeout, retry policy, max tokens, temperature, price ceiling и circuit breaker. Fallback не должен менять schema или capability set.

OpenRouter подключается модельным id вида `openrouter/<provider>/<model>` через OpenHands `LLM`; секреты передаются через secret/config layer, не попадают в prompts, event logs и database. Другие провайдеры используют тот же registry interface.

### 8.1 Где применять OpenHands Conversation

- bounded LLM jobs можно выполнять короткими conversation с ограничением iterations;
- persistence OpenHands полезен для технического resume и аудита pipeline;
- доменное состояние остаётся в MasterClaw storage, а не в `ConversationState`;
- built-in Bash/FileEditor не выдаются игровым pipelines;
- игровые операции оформляются typed custom tools либо, предпочтительно, вызываются оркестратором до/после LLM;
- stuck detection включается, max iterations обычно 1–3;
- production isolation через remote agent server рассматривается отдельно, если pipelines действительно исполняют код. Для pure structured LLM calls достаточно standalone SDK.

## 9. Автоматические gates и validators

### 9.1 До LLM

- authentication, channel/game/player authorization;
- Discord event deduplication;
- rate limit и session lock;
- mode/phase/pending-state validation;
- completeness и freshness context projections;
- secret-field redaction;
- input length, attachment type и locale detection;
- budget/provider availability gate.

### 9.2 После LLM

- Pydantic/JSON schema parsing;
- enum/range/required-field validation;
- no unknown fields;
- referenced entity existence;
- evidence references действительно присутствуют во входном context;
- запрет новых фактов вне разрешённого creative scope;
- locale/script lint;
- narrative perspective и narrator-rights bounds;
- word/style budget;
- prompt/tool leakage и Discord-safe rendering.

### 9.3 Перед commit

- domain invariants: 18 points, reserve 0..7, max one flag, valid aspects;
- roll result происходит только из RNG service;
- confirmed pool совпадает с rolled pool;
- pending action id и revision совпадают;
- transition разрешён state machine;
- все обязательные side effects входят в одну transaction;
- outbox message создан в той же transaction.

### 9.4 После commit / перед publish

- projection rebuild или incremental update успешен;
- rendering прошёл locale/style gates;
- message chunking соответствует лимитам Discord;
- idempotency key отсутствует среди delivered messages;
- narrative и game-channel payload согласованы по одному committed event set.

При repair допускается максимум один ограниченный повтор LLM с machine-readable ошибками. Далее — safe fallback template или запрос уточнения. Невалидный ответ нельзя «починить» свободным агентским циклом.

## 10. Discord bot

Полная bot-интеграция заменяет зависимость от microClaw channel adapter и частичную webhook-схему.

### 10.1 Возможности

- gateway message events и команды с `/` в том же durable ingress;
- привязка channel к game/session;
- Discord user -> player/character mapping;
- разделение public game и narrative channels;
- текстовые confirm/cancel/reserve и понятные ошибки;
- равные права игроков; операторские операции относятся к созданию мира;
- независимые сцены внутри одной игры;
- отправка через bot API; webhook оставить как compatibility option.

### 10.2 Надёжность

Ingress быстро подтверждает interaction, записывает event и ставит job в очередь. Worker берёт per-session lock, выполняет pipeline и создаёт outbox. Publisher независимо доставляет сообщение с retry/backoff. Это исключает потерю хода при timeout Discord и двойную обработку при retry.

Bot token, provider keys и webhook credentials хранятся в secret manager/environment. В state хранится только secret reference или encrypted value.

## 11. Транзакции, конкурентность и восстановление

- один последовательный command stream на session;
- optimistic lock на aggregate revisions;
- idempotency key: Discord event id + logical action id;
- immutable roll record создаётся один раз;
- unit of work атомарно сохраняет domain events, state и outbox;
- crash recovery повторяет недоставленный outbox, но не domain command;
- admin correction — новая компенсирующая команда, не тихое редактирование истории;
- экспорт markdown генерируется из state и может быть пересоздан.

## 12. Наблюдаемость и стоимость

Для каждого запроса нужен correlation chain: `discord_event_id -> command_id -> pipeline_run_id -> llm_request_id -> domain_event_ids -> outbox_id`.

Метрики:

- latency по стадиям;
- input/output/cache tokens и стоимость по model/pipeline/game;
- schema failure, repair, fallback и clarification rates;
- gate rejection reasons;
- context composition: fragment ids, revisions, token counts;
- duplicate suppression;
- transaction conflicts;
- Discord delivery failures;
- доля ходов без LLM и среднее число LLM calls на ход.

Сохраняются hash/version промтов и sanitized LLM I/O. Секреты и приватный hidden context редактируются по политике retention.

## 13. Тестовая стратегия

### 13.1 Детерминированные тесты

- unit tests всех правил и state transitions;
- property-based tests для pool/reserve/narrator-rights;
- schema and migration tests;
- transaction/idempotency/concurrency tests;
- Discord contract tests с mocked API;
- snapshot tests locale renderers;
- prompt assembly tests: точный список разрешённых fragments и отсутствие запрещённых.

### 13.2 Model evaluation

Сценарии из `CLAUDE.md` превращаются в versioned fixtures. Для каждой поддерживаемой модели измеряются:

- structured-output validity;
- factual grounding;
- корректность выбора релевантных traits/aspects;
- соблюдение прав рассказчика;
- narrative quality rubric;
- стоимость, latency, retry rate;
- успешность на adversarial и длинных сессиях.

Модель допускается к pipeline, а не ко всему продукту. Дешёвая модель может пройти classifier suite и не пройти narration/worldgen suite.

### 13.3 Ключевые end-to-end сценарии

- создание и публикация мира по секциям;
- подготовка игры до readiness;
- обычное действие без броска;
- действие с подтверждением, резервом и броском;
- cancel/change pending pool;
- narrator rights на всех границах hits/difficulty;
- смешанное сообщение;
- duplicate Discord delivery;
- restart между confirm и roll и между commit и publish;
- смена provider/model без изменения доменного результата;
- 100+ ходов без роста полного prompt history;
- конфликт двух сообщений в одной session;
- неправильный/вредоносный LLM JSON;
- утечка plot/private context в public answer.

## 14. Этапы миграции

### Этап 0 — specification freeze

- формализовать правила и спорные места;
- каталогизировать prompts и дубли;
- сохранить golden fixtures текущего поведения;
- согласовать вопросы из раздела 17.

**Выход:** versioned domain specification и acceptance matrix.

### Этап 1 — domain core без LLM и Discord

- Pydantic/domain models;
- state machine;
- mechanics services;
- repositories и migrations;
- import/export текущих markdown данных;
- deterministic gates.

**Выход:** все правила проходят unit/property tests.

### Этап 2 — context engine и prompt registry

- fragments из текущих souls/skills;
- dependency manifests;
- projections/selectors;
- budgets, ACL и provenance;
- prompt assembly snapshots.

**Выход:** для каждого pipeline известен точный минимальный context.

### Этап 3 — OpenHands provider layer

- LLM registry для OpenRouter и других провайдеров;
- typed pipeline contracts;
- retry/fallback/circuit breakers;
- telemetry;
- classifier, action interpretation, narration, worldgen pipelines.

**Выход:** contract/model eval suites проходят на целевых моделях.

### Этап 4 — orchestration и transactional workflow

- deterministic router;
- pending interactions;
- command handlers;
- atomic commit/outbox;
- repair/fallback policies.

**Выход:** headless end-to-end game работает через test harness.

### Этап 5 — Discord adapter

- bot commands/events/UI components;
- identity/bindings/permissions;
- queue, locks, outbox publisher;
- admin/operator interface.

**Выход:** staging campaign проходит restart/duplicate/load tests.

### Этап 6 — deployment acceptance

- clean start без импорта v1;
- model-contract smoke test для трёх ролей;
- staging Discord game с restart/duplicate проверками;
- проверка backup/restore и rollback контейнера;
- обновлённые setup/operations/runbooks.

**Выход:** v2 становится основной системой; v1 остаётся read-only fallback на ограниченный срок.

## 15. Критерии готовности v2

- верхний режим определяется без общего LLM agent loop;
- ни один бросок и критическая механика не вычисляются моделью;
- LLM не имеет прямого write-доступа к state;
- каждый commit валиден, атомарен и идемпотентен;
- prompt size зависит от задачи и state slice, а не от длины сессии;
- restart не теряет pending action и не повторяет бросок/публикацию;
- дешёвые модели безопасно ограничены pipelines, которые прошли eval;
- Discord bot поддерживает полный игровой и операторский workflow;
- все provider/model вызовы наблюдаемы по стоимости и качеству;
- clean start не требует импорта данных v1 (согласованное решение); совместимость схемы v2 контролируется миграциями;
- документация содержит deployment, backup, recovery и incident procedures.

## 16. Основные риски и меры

| Риск | Мера |
|---|---|
| OpenHands ориентирован прежде всего на software agents | использовать SDK как LLM/tool/event substrate, а доменную orchestration держать в MasterClaw |
| слишком много маленьких LLM calls увеличат latency | батчить только логически совместимые задачи, кэшировать static prompts, parallelize read-only critics |
| структурированный output остаётся нестабилен на дешёвых моделях | строгие schemas, один repair, fallback templates, per-pipeline qualification |
| schema drift после обновлений v2 | последовательные транзакционные миграции и fail-closed schema version check |
| Discord retries создадут дубли | inbox/outbox и idempotency keys |
| hidden plot попадёт в context | selector ACL, provenance audit и leakage tests |
| prompt fragments снова начнут дублироваться | canonical ids, dependency graph и CI duplicate checks |
| narrative quality снизится из-за узкого context | расширять projections по измеряемым eval failures, не возвращать полный history |

## 17. Согласованные решения

Решения владельца проекта от 2026-07-13:

1. Канонических режимов ровно три: `WORLD_MANAGEMENT`, `PREPARATION`, `PLAY`. Операторская работа входит в `WORLD_MANAGEMENT`; отдельной admin plane как четвёртого режима нет.
2. Единственный пользовательский канал первой версии — Discord. Telegram и web/admin UI не нужны; CLI используется для запуска и обслуживания демона.
3. Импорт и совместимость с существующими играми/мирами v1 не требуются. Допустим clean start.
4. Production — отдельный изолированный Linux droplet. Базовый deployment: Docker Compose с одним application container, persistent volume для SQLite и systemd unit, управляющим Compose. Kubernetes не нужен.
5. SQLite достаточно; параллельных игр и нескольких application workers не ожидается.
6. Discord ingress обрабатывается последовательно. Несколько сообщений, пришедших до ответа, собираются в batch и разбираются по порядку.
7. Игроки могут находиться в разных сценах. Независимые действия не требуют общей строгой очерёдности на уровне игрового мира, хотя commit выполняется последовательно. Зависимые действия сериализуются по затронутым сценам/сущностям.
8. При накоплении сообщений система после каждого обработанного сообщения повторно сопоставляет последующие сообщения с актуальным pending state: более позднее сообщение может оказаться ответом на вопрос, возникший при обработке более раннего.
9. Ответ на batch компонуется в один читаемый Discord-блок; внутри него можно адресно ответить или задать вопросы нескольким игрокам.
10. Мир могут менять все участники. Это не отменяет schema, consistency и destructive-action gates.
11. Личные сообщения, секретные сообщения и приватные знания персонажей в первой версии не нужны.
12. Отдельный narrative channel нужен. Предпочтителен Discord bot API; webhook остаётся только возможным fallback, а не архитектурным требованием.
13. Единственный LLM-провайдер первой версии — OpenRouter.
14. Динамическая маршрутизация по цене/доступности не нужна. Модели конфигурируются по стабильным ролям: быстрые модели для state/classification decisions, reasoning-модели для разбора заявки и планирования ответа, linguistic-модели для нарратива.
15. Правила BlackBirdPie зафиксированы. Обнаруженные противоречия документируются и выносятся владельцу проекта; реализация не выбирает трактовку самостоятельно.

### 17.1 Пока не зафиксировано

Эти параметры не блокируют начальный фундамент и остаются конфигурационными/отложенными решениями:

- конкретная baseline-матрица моделей OpenRouter по ролям;
- нужен ли публично проверяемый RNG или достаточно серверного CSPRNG и immutable audit record;
- retention для Discord events и sanitized LLM I/O;
- языки сверх `ru`/`en`;
- целевые SLA;
- необходимость shadow/canary режима;
- политика публикации крупных сюжетных изменений (все игроки имеют равные права, но destructive gates остаются обязательными).

## 18. Принятые технические defaults

До отдельного решения используются: SQLite в WAL mode; один process/worker и последовательный command stream; Discord bot API; OpenRouter; `ru`/`en`; серверный CSPRNG с audit record; standalone OpenHands SDK для ограниченных LLM pipelines без shell/file tools; Docker Compose на Linux droplet; persistent inbox/outbox; per-role model configuration без автоматического переключения между ролями.

## 19. Использованные актуальные возможности OpenHands SDK

При реализации следует перепроверить API по зафиксированной версии dependency. План опирается на текущие официальные возможности:

- provider abstraction через `openhands.sdk.LLM` и LiteLLM;
- OpenRouter model ids и API key configuration;
- разделение `static_system_message` и dynamic context для prompt caching;
- typed custom tools: Action, Observation, Executor;
- Conversation с persistence, event log, callbacks, metrics и stuck detection;
- ограничение: при resume набор tools должен совпадать;
- local и remote workspace/conversation с общей API surface;
- confirmation/security policies — дополнительный слой, но не замена доменным gates.

Официальные источники, которые нужно закрепить на выбранной версии перед реализацией:

- [OpenHands Software Agent SDK](https://docs.openhands.dev/sdk/index);
- [SDK architecture overview](https://docs.openhands.dev/sdk/arch/overview);
- [Agent and context architecture](https://docs.openhands.dev/sdk/arch/agent);
- [LLM/provider abstraction](https://docs.openhands.dev/sdk/arch/llm);
- [OpenRouter configuration](https://docs.openhands.dev/openhands/usage/llms/openrouter);
- [Custom typed tools](https://docs.openhands.dev/sdk/guides/custom-tools);
- [Conversation persistence](https://docs.openhands.dev/sdk/guides/convo-persistence);
- [Security and action confirmation](https://docs.openhands.dev/sdk/guides/security).
