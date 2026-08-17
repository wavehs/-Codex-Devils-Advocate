# Codex Devil's Advocate

<p align="center">
  <img src="assets/C048235B-D8A8-42E4-A298-E52B5E3388A0.png" alt="Codex Devil's Advocate — токен-эффективная проверка кода для Codex" width="100%" />
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/English-18181b?style=for-the-badge" alt="English" /></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/Русский-dc2626?style=for-the-badge" alt="Русский" /></a>
</p>

<p align="center">
  <strong>Сомневаешься, что код реально готов?</strong><br/>
  Запусти skill и дай второму reviewer-у попытаться сломать его раньше, чем это сделают пользователи.
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/УСТАНОВКА-dc2626?style=for-the-badge" alt="Установка" /></a>
  <a href="#usage"><img src="https://img.shields.io/badge/ИСПОЛЬЗОВАНИЕ-991b1b?style=for-the-badge" alt="Использование" /></a>
  <a href="#idea"><img src="https://img.shields.io/badge/КАК%20ЭТО%20РАБОТАЕТ-991b1b?style=for-the-badge" alt="Как это работает" /></a>
  <a href="#token-efficiency"><img src="https://img.shields.io/badge/МАЛО%20ТОКЕНОВ-dc2626?style=for-the-badge" alt="Мало токенов" /></a>
</p>

---

## Сомневаешься, что код действительно готов?

Фича написана. Тесты зелёные. Codex говорит, что всё выглядит нормально.

Но остаётся один вопрос:

> **А что мы пропустили?**

Запусти:

```text
$adversarial-review
```

Это как отдать готовое изменение второму разработчику с одной задачей: **найти реальный способ, как код может сломаться.**

Нашёл настоящий баг? Codex проверит находку, исправит код и точечно перепроверит только изменённое место. Ничего серьёзного? На этом всё.

**Написал → усомнился → проверил → исправил → готово.**

Без толпы reviewer-ов. Без бесконечных циклов. Без лишнего расхода токенов ради очередного «всё выглядит хорошо».

---

<a id="idea"></a>
## Идея

После того как Codex закончил задачу, вызовите:

```text
$adversarial-review
```

Skill запускает ограниченный adversarial loop:

```text
РЕАЛИЗАЦИЯ
   ↓
ДЕШЁВЫЕ TESTS / BUILD / TYPECHECK
   ↓
ФИКСАЦИЯ SCOPE
   ↓
DIFF-FIRST ВЫБОР КОНТЕКСТА
   ↓
ОДИН READ-ONLY ADVERSARIAL REVIEWER
   ↓
ОСНОВНОЙ CODEX ПРОВЕРЯЕТ FINDINGS
   ↓
ЕСТЬ ПОДТВЕРЖДЁННЫЕ BLOCKING-БАГИ?
   ├── НЕТ → PASS
   └── ДА
        ↓
     ИСПРАВЛЕНИЕ
        ↓
   REGRESSION TESTS
        ↓
 ТОТ ЖЕ REVIEWER THREAD
 ТОЧЕЧНЫЙ RECHECK
        ↓
  ПРИ НЕОБХОДИМОСТИ FIX
        ↓
  ТОЧЕЧНЫЙ RECHECK #2
        ↓
       STOP
```

Никакого reviewer swarm. Никакого бесконечного review loop. Никакого полного перечитывания репозитория после каждого исправления.

---

## Чем этот подход отличается

| Возможность | Что это даёт |
|---|---|
| **Только 1 reviewer** | Нет нескольких агентов, читающих один и тот же код и дублирующих находки |
| **Read-only reviewer** | Reviewer не может менять код, который сам же оценивает |
| **Diff-first** | Сначала анализируются реальные изменения, а не весь репозиторий |
| **Evidence-first** | Для finding нужен конкретный trigger, execution path и неправильный результат |
| **Проверка основным агентом** | Findings reviewer-а не принимаются на веру |
| **Ограниченный re-review** | Один полный review + максимум два incremental recheck |
| **Глобальная установка** | Установили один раз — используете во всех проектах Codex |
| **Только ручной вызов** | Skill не расходует лимит, пока вы сами его не вызовете |

---

## Что ищет reviewer

Reviewer пытается опровергнуть корректность реализации и ищет реальные сбои, связанные с:

- логическими ошибками;
- нарушенными инвариантами;
- некорректными переходами состояний;
- граничными условиями;
- устаревшим или частично инициализированным состоянием;
- регрессиями;
- ошибочными предположениями;
- failure/error paths;
- тестами, которые проходят, но на самом деле ничего важного не доказывают;
- concurrency-проблемами, если они релевантны;
- security-проблемами, если они релевантны.

Reviewer специально инструктирован **не придумывать замечания только ради количества**.

Хороший finding должен выглядеть примерно так:

```text
trigger / state
→ достижимый execution path
→ нарушенный контракт или инвариант
→ наблюдаемый неправильный результат
```

Если такую цепочку нельзя обосновать, finding не должен считаться подтверждённым дефектом.

---

<a id="token-efficiency"></a>
## Экономия токенов заложена в архитектуру

Жёсткий бюджет по умолчанию:

```text
Полных adversarial review: 1
Incremental re-review: максимум 2
Одновременно reviewer subagents: 1
```

### Что это предотвращает

Наивный adversarial workflow часто выглядит так:

```text
Reviewer A читает репозиторий
→ fix
Reviewer B снова читает репозиторий
→ fix
Reviewer C снова читает репозиторий
→ ещё один финальный reviewer
```

Это может увеличить разнообразие мнений, но стоит дорого.

Devil's Advocate вместо этого переиспользует уже построенную reviewer-ом модель изменений:

```text
полный review
→ fix
→ только новый patch + связанные пути
→ fix при необходимости
→ последний точечный recheck
```

Самый дорогой этап — понимание контекста репозитория — остаётся ограниченным.

---

## Frozen scope

В начале review skill фиксирует одну область проверки и не расширяет её без причины.

Приоритет такой:

1. scope, явно указанный пользователем;
2. реализация, завершённая в текущей задаче;
3. staged + unstaged diff;
4. branch diff, если его можно определить надёжно и дёшево.

Это не даёт reviewer-у уходить в несвязанный legacy code и тратить токены на баги, не относящиеся к текущему изменению.

---

## Минимизация контекста

Reviewer начинает с:

```text
изменённые файлы
+ изменённые символы
+ связанные тесты
```

И расширяет контекст только когда это нужно для доказательства или опровержения дефекта:

```text
callers
callees
interfaces
contracts
state transitions
persistence boundaries
schemas
API boundaries
связанное regression-sensitive поведение
```

Generated files, lockfiles, vendor code, огромные fixtures и несвязанные модули игнорируются, пока не окажутся действительно важны для поведения.

---

## Фильтрация false positives

Adversarial не означает параноидальный.

Reviewer сначала пытается опровергнуть собственные подозрения. Затем основной Codex независимо классифицирует каждую находку:

```text
CONFIRMED
REJECTED
UNCERTAIN
```

Обычно только `CONFIRMED` должен приводить к изменению кода.

Это важно, потому что AI reviewer тоже может уверенно ошибаться.

---

## Условие остановки

Цель — не:

```text
ноль замечаний
```

Цель:

```text
ноль подтверждённых blocking correctness defects
```

Blocking severity:

- `CRITICAL`
- `HIGH`
- `MEDIUM`

Skill не продолжает расходовать токены из-за style preferences, теоретических рисков или мелких `LOW` замечаний.

---

<a id="installation"></a>
# Установка

## Глобальная установка — рекомендуется

Установите один раз и используйте skill во всех проектах Codex.

### Windows

Клонируйте или скачайте репозиторий, откройте PowerShell в его корне и выполните:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Skill будет скопирован в:

```text
%USERPROFILE%\.agents\skills\adversarial-review\
```

Custom reviewer будет установлен в:

```text
%USERPROFILE%\.codex\agents\adversarial-reviewer.toml
```

### macOS / Linux

```bash
chmod +x install.sh
./install.sh
```

Файлы будут установлены в:

```text
~/.agents/skills/adversarial-review/
~/.codex/agents/adversarial-reviewer.toml
```

Если Codex сразу не увидел skill, перезапустите его.

---

## Удаление

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
```

### macOS / Linux

```bash
chmod +x uninstall.sh
./uninstall.sh
```

---

<a id="usage"></a>
# Использование

## Самый простой вариант

Сначала дайте Codex обычным способом закончить реализацию задачи.

Затем запустите:

```text
$adversarial-review
```

Этого достаточно.

---

## Автоматически после выполнения задачи

Можно указать review прямо в исходном запросе:

```text
Implement the new inventory system.
Run the relevant tests.
Then run $adversarial-review.
```

Или:

```text
Refactor the betting state machine and use $adversarial-review after the implementation is complete.
```

---

## Когда стоит использовать

Хорошие кандидаты:

- нетривиальная новая функциональность;
- bug fix, затрагивающий state или business logic;
- state machines;
- persistence code;
- финансовая или численная логика;
- async workflows;
- concurrency-sensitive изменения;
- authentication / authorization;
- крупные refactor;
- поведение, где passing tests могут создавать ложную уверенность.

Обычно не нужен для:

- комментариев;
- форматирования;
- изменений текста;
- простых rename;
- мелких косметических UI-изменений.

Skill сделан explicit-only именно для того, чтобы вы сами решали, когда глубокая проверка стоит дополнительных токенов.

---

# Пример

Вы просите Codex:

```text
Implement inventory stacking.
```

Codex реализует функцию, тесты проходят.

Затем:

```text
$adversarial-review
```

Reviewer может найти:

```text
HIGH — partial stack merge can destroy quantity

Trigger:
Target stack has less free capacity than the incoming quantity.

Execution path:
mergeItem()
→ target quantity reaches max
→ source slot is cleared unconditionally

Actual:
Remaining source quantity is lost.

Expected:
Only the transferred amount should leave the source stack.
```

Основной Codex проверяет путь, подтверждает баг, исправляет его и добавляет regression test.

Тот же reviewer получает только новый patch и проверяет:

- действительно ли устранён исходный failure path;
- не добавил ли fix новую регрессию рядом;
- доказывает ли regression test нужное поведение.

Если blocking defects больше нет:

```text
Adversarial review: PASS

- Full reviews: 1
- Incremental re-reviews: 1/2
- Confirmed: Critical 0 / High 1 / Medium 0
- Fixed: 1
- Rejected false positives: 1
- Validation: targeted tests, build
- Remaining blocking defects: none
```

---

# Архитектура

```text
Пользователь
 │
 ▼
Основной Codex Agent
 │
 ├── реализует задачу
 ├── запускает deterministic checks
 └── вызывает skill
        │
        ▼
  adversarial_reviewer
        │
        ├── read-only
        ├── diff-first
        ├── восстанавливает contracts
        ├── ищет counterexamples
        └── возвращает evidence
        │
        ▼
Основной Codex Agent
 │
 ├── проверяет каждый finding
 ├── отбрасывает false positives
 ├── исправляет подтверждённые дефекты
 └── добавляет regression tests
        │
        ▼
Тот же reviewer thread
 │
 └── targeted incremental recheck
        │
        ▼
      PASS / FAIL
```

---

# Структура репозитория

```text
.agents/
└── skills/
    └── adversarial-review/
        ├── SKILL.md
        └── agents/
            └── openai.yaml

.codex/
└── agents/
    └── adversarial-reviewer.toml

install.ps1
install.sh
uninstall.ps1
uninstall.sh
```

### `SKILL.md`

Управляет workflow, бюджетом review, выбором scope, проверкой findings, циклом исправлений и условием остановки.

### `adversarial-reviewer.toml`

Определяет независимого read-only reviewer-а и его adversarial поведение.

### `openai.yaml`

Содержит metadata skill и отключает автоматический implicit запуск.

---

# Почему не попросить Codex самому проверить собственный код?

Потому что агент, который придумал и реализовал решение, уже несёт в себе те же предположения, на которых это решение построено.

Отдельный adversarial reviewer получает другую цель:

```text
Implementer:
"Сделай так, чтобы это работало."

Reviewer:
"Найди конкретный случай, доказывающий, что это не работает."
```

Именно изменение цели и является основой adversarial review.

---

# Почему не использовать 3–5 reviewer-ов?

Можно, но этот проект намеренно этого не делает.

Несколько reviewer-ов увеличивают:

- повторное чтение репозитория;
- дублирование reasoning;
- одинаковые findings;
- расход контекста;
- расход токенов.

Devil's Advocate задуман как **daily-driver review skill**, а не максимально дорогая система формальной верификации.

Для большинства задач один сильный reviewer с хорошими falsification-инструкциями и ограниченным repair loop — более практичный компромисс.

---

# Заменяет ли это тесты?

Нет.

Предполагаемый workflow:

```text
implementation
→ deterministic tests
→ adversarial reasoning
→ repair
→ regression tests
→ targeted recheck
```

Тесты и adversarial review ловят разные типы ошибок.

---

# Гарантирует ли это код без багов?

Нет — и проект намеренно этого не обещает.

Корректная финальная формулировка:

> **В проверенном scope не осталось подтверждённых blocking defects.**

Это намного честнее, чем утверждать, что реализация идеальна.

---

# Философия

Хороший AI reviewer не должен получать награду за длинный список замечаний.

Он должен находить **небольшое количество дефектов, которые выдерживают проверку**.

```text
Подозрение
   ↓
Состояние достижимо?
   ↓
Execution path можно проследить?
   ↓
Возникает наблюдаемое неправильное поведение?
   ↓
Finding выдерживает независимую проверку?
   ↓
CONFIRMED DEFECT
```

Один настоящий сложный баг полезнее двадцати слабых замечаний.

---

## Быстрый старт

```text
1. Клонируйте или скачайте репозиторий
2. Запустите install.ps1 или install.sh
3. При необходимости перезапустите Codex
4. Откройте любой проект
5. Завершите нетривиальную реализацию
6. Запустите $adversarial-review
7. Позвольте Codex проверить и исправить подтверждённые findings
```

---

<p align="center">
  <strong>Ваш coding agent пишет решение.<br/>Devil's Advocate пытается доказать, что оно неправильное.</strong>
</p>

<p align="center">
  <code>$adversarial-review</code>
</p>