# Codex Devil's Advocate

<p align="center">
  <img src="assets/C048235B-D8A8-42E4-A298-E52B5E3388A0.png" alt="Codex Devil's Advocate — токен-эффективная проверка кода для Codex" width="100%" />
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/English-18181b?style=for-the-badge" alt="English" /></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/Русский-dc2626?style=for-the-badge" alt="Русский" /></a>
</p>

<p align="center">
  <strong>Codex написал решение. Теперь дай ему противника.</strong><br/>
  Отдельный reviewer ищет слабые места и помогает сделать код лучше.
</p>

---

## Идея

После того как Codex закончил задачу, вызовите:

```text
$adversarial-review
```

Skill сначала проверяет совместимость текущего Codex runtime, и только потом тратит токены на тесты, полный manifest и reviewer:

```text
РЕАЛИЗАЦИЯ
   ↓
RUNTIME PREFLIGHT
   ├── STRICT + нельзя доказать identity/sandbox
   │      ↓
   │  INCONCLUSIVE — РАННИЙ STOP
   │
   └── runtime поддерживается / BEST_EFFORT
          ↓
ДЕШЁВЫЕ TESTS / BUILD / TYPECHECK
   ↓
ФИКСАЦИЯ SCOPE
   ↓
HASH MANIFEST + SNAPSHOT
   ↓
ОДИН ADVERSARIAL REVIEWER
   ↓
ОСНОВНОЙ CODEX ПРОВЕРЯЕТ FINDINGS
   ↓
ЦЕЛОСТНОСТЬ REVIEW ДОКАЗАНА?
   ├── ДА → PASS / FAIL
   ├── В BEST_EFFORT НЕ ХВАТАЕТ ТОЛЬКО RUNTIME ATTESTATION → UNVERIFIED
   └── ИНАЧЕ → INCONCLUSIVE
```

Никакого reviewer swarm, бесконечного review loop и повторного полного чтения репозитория после каждого fix.

---

## Режимы совместимости runtime

Некоторые версии/поверхности Codex умеют загрузить custom-agent TOML, но не дают основному агенту надёжно доказать, **какой именно custom agent реально был запущен** и **какой effective sandbox получил child**. `task_name`, ответ самого child или наличие TOML-файла не являются trusted runtime evidence.

Devil's Advocate теперь обрабатывает эту ситуацию явно.

### STRICT — по умолчанию

```text
$adversarial-review
```

STRICT требует доказать через runtime metadata:

```text
selected custom agent = adversarial_reviewer
effective sandbox     = read-only
```

Если текущий Codex не раскрывает эти данные, skill останавливается **до** запуска project tests, перечисления всего проекта, построения manifest и reviewer turn:

```text
Adversarial review: INCONCLUSIVE
Runtime attestation unavailable.
```

То есть несовместимый runtime больше не сжигает полный review budget, когда валидный PASS/FAIL всё равно технически недостижим.

### BEST_EFFORT — только по явному запросу

```text
Run $adversarial-review in BEST_EFFORT mode.
```

BEST_EFFORT сохраняет детерминированные гарантии:

- полный scope manifest;
- before/after snapshots worktree;
- исходный reviewer JSON без переписывания;
- protocol validation;
- независимую проверку findings основным Codex;
- ограниченный review/re-review budget.

Если exact custom-agent identity или effective sandbox невозможно подтвердить, итог будет:

```text
Adversarial review: UNVERIFIED
```

Findings всё равно можно использовать и отдельно проверять, но `UNVERIFIED` **никогда не выдаётся за сертифицированный PASS или FAIL**.

Если runtime вообще не позволяет выбрать custom agent по exact identity, BEST_EFFORT может использовать одного generic child с контрактом `references/best-effort-reviewer.md`. В STRICT это запрещено.

---

## Почему это важно

| Возможность | Что это даёт |
|---|---|
| **Runtime preflight первым** | Заведомо несовместимый Codex останавливается до дорогой работы |
| **STRICT по умолчанию** | PASS/FAIL требуют trusted identity и effective read-only sandbox metadata |
| **BEST_EFFORT по запросу** | Полезные findings не пропадают, а помечаются как UNVERIFIED |
| **Только 1 reviewer** | Нет дублирующих subagents |
| **Diff-first** | Сначала анализируются реальные изменения |
| **Evidence-first** | Для finding нужен trigger, execution path и неправильный результат |
| **Fail-closed core integrity** | Malformed output, неполный scope, mutation worktree или непросмотренная final version всё ещё дают INCONCLUSIVE |
| **Ограниченный re-review** | Один initial full review, максимум один escalation full review и два incremental recheck |

---

## Что ищет reviewer

Reviewer пытается опровергнуть корректность реализации и ищет реальные сбои:

- логические ошибки;
- нарушенные инварианты;
- некорректные state transitions;
- boundary cases;
- stale/partially initialized state;
- regressions;
- неправильные assumptions;
- error/failure paths;
- тесты, которые проходят, но не доказывают нужное поведение;
- concurrency/security проблемы, когда они действительно релевантны.

Хороший finding:

```text
trigger / state
→ достижимый execution path
→ нарушенный contract/invariant
→ наблюдаемый неправильный результат
```

---

## Совместимость reviewer protocol

Reviewer теперь явно получает требование возвращать `confidence` как integer `0–100`:

```json
"confidence": 99
```

Но реальные модели Codex иногда возвращают дробный вариант:

```json
"confidence": 0.99
```

Guard принимает оба формата **без переписывания исходного JSON**:

- integer: `0..100`;
- float: конечное значение `0.0..1.0`.

Это послабление относится только к `confidence`. Проверки identity, sandbox, manifest hash, review ID, reviewed paths, status и snapshots не ослабляются.

---

## Экономия токенов

После успешного preflight бюджет остаётся ограниченным:

```text
Initial full adversarial reviews: 1
Escalation full reviews: максимум 1
Incremental re-reviews: максимум 2
Одновременных reviewer subagents: 1
```

Неуспешный STRICT preflight использует **0 reviewer turns**.

---

## Frozen scope

После preflight skill выбирает target в таком порядке:

1. scope, явно указанный пользователем;
2. implementation текущей задачи;
3. staged / unstaged / untracked changes;
4. branch diff, если base можно определить надёжно.

Scope фиксируется через HEAD/merge-base SHA, file statuses, content hashes, Git-status hash и canonical diff hash. Untracked-файлы учитываются отдельно и проверяются целиком. Worktree снимается до и после каждого reviewer turn.

---

## False-positive filtering

Основной Codex независимо классифицирует каждую находку:

```text
CONFIRMED
REJECTED
UNCERTAIN
```

CRITICAL/HIGH finding можно отклонить только с объективным evidence: reproduction, точный regression test, documented contract, type invariant, validation logic или конкретный repository reference.

В degraded BEST_EFFORT эти классификации всё равно могут быть полезны, но весь review остаётся UNVERIFIED, пока runtime attestation отсутствует.

---

## Условие PASS

STRICT PASS требует:

```text
runtime identity + sandbox attestation доказаны
+ review integrity доказана
+ final scope hash совпадает с проверенной версией
+ нет CONFIRMED blocking defects
+ нет UNCERTAIN CRITICAL/HIGH findings
```

Blocking severity:

- `CRITICAL`
- `HIGH`
- `MEDIUM`

`UNVERIFIED` означает: детерминированные review artifacts пригодны, но reviewer identity/isolation не сертифицированы runtime-ом.

`INCONCLUSIVE` означает: сломалась более сильная core-integrity гарантия или STRICT preflight не прошёл.

---

# Установка

Требования: Git и Python 3.

## Поддерживаемые поверхности Codex

| Поверхность | Поддержка |
|---|---|
| Codex desktop app, CLI, IDE extension | STRICT работает, когда активный runtime раскрывает trusted custom-agent identity и effective sandbox metadata; иначе ранний INCONCLUSIVE |
| Те же local surfaces без нужной metadata | Можно явно включить BEST_EFFORT и получить UNVERIFIED review |
| Local plugin marketplace | Skill упакован как plugin; custom reviewer нужно установить companion installer-ом |
| Codex cloud, ChatGPT Work/web/mobile | Не поддерживаются для certified local workflow из-за отсутствия нужных локальных Git/Python/runtime гарантий |

Plugin manifest не устанавливает standalone `.codex/agents/*.toml`, поэтому companion installer всё ещё нужен для preferred custom reviewer.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Skill:

```text
%USERPROFILE%\.agents\skills\adversarial-review\
```

Reviewer:

```text
%USERPROFILE%\.codex\agents\adversarial-reviewer.toml
```

Если задан `CODEX_HOME`, reviewer устанавливается в `%CODEX_HOME%\agents\`.

### macOS / Linux

```bash
chmod +x install.sh
./install.sh
```

Установка:

```text
~/.agents/skills/adversarial-review/
~/.codex/agents/adversarial-reviewer.toml
```

Если задан `CODEX_HOME`, reviewer устанавливается в `$CODEX_HOME/agents/`.

После установки при необходимости перезапустите Codex.

---

# Использование

## STRICT

```text
$adversarial-review
```

Это режим по умолчанию. При отсутствии trusted identity/sandbox metadata skill быстро вернёт INCONCLUSIVE и не будет запускать дорогой full review.

## BEST_EFFORT

```text
Run $adversarial-review in BEST_EFFORT mode.
```

Используйте, если ваш Codex runtime не раскрывает достаточную custom-agent metadata, но findings всё равно нужны. Пока attestation отсутствует, ожидаемый результат — `UNVERIFIED`.

---

# Пример проблемы из реального runtime

Если spawn-ответ содержит только `task_name` и не раскрывает effective sandbox/identity metadata:

STRICT:

```text
Adversarial review: INCONCLUSIVE
- Mode: STRICT
- Full reviews: 0/2
- Runtime attestation: missing identity and sandbox
```

BEST_EFFORT:

```text
Adversarial review: UNVERIFIED
- Mode: BEST_EFFORT
- Full reviews: 1/2
- Runtime attestation: missing identity and sandbox
- Candidate findings: available
- Unverified reasons: reviewer identity/isolation not certified
```

---

# Архитектура

```text
Пользователь
 │
 ▼
Основной Codex Agent
 │
 ├── вызывает skill
 └── runtime preflight
        │
        ├── STRICT unsupported → INCONCLUSIVE / STOP
        │
        └── supported или BEST_EFFORT
                 ↓
        deterministic checks
                 ↓
           frozen manifest
                 ↓
       один reviewer thread
                 ↓
     protocol + snapshot checks
                 ↓
      verify/fix findings
                 ↓
 PASS / FAIL / UNVERIFIED / INCONCLUSIVE
```

---

# Структура репозитория

```text
.codex-plugin/
└── plugin.json

skills/
└── adversarial-review/
    ├── SKILL.md
    ├── scripts/
    │   └── review_guard.py
    ├── references/
    │   └── best-effort-reviewer.md
    └── agents/
        └── openai.yaml

.codex/
└── agents/
    └── adversarial-reviewer.toml

tests/
└── test_review_guard.py
```

`SKILL.md` управляет preflight, режимами, scope, review budget, finding verification и stop condition.

`review_guard.py` детерминированно выполняет preflight decision, manifest/snapshot validation, reviewer protocol validation и вычисляет `PASS`, `FAIL`, `INCONCLUSIVE` или `UNVERIFIED`.

`best-effort-reviewer.md` используется только для generic fallback в BEST_EFFORT.

---

# Заменяет ли это тесты?

Нет.

Нормальный supported-runtime workflow:

```text
runtime preflight
→ deterministic tests
→ adversarial reasoning
→ repair, если пользователь просил
→ regression tests
→ targeted recheck
```

---

# Гарантирует ли это отсутствие багов?

Нет.

Корректная strict-формулировка:

> **В доказанно проверенном scope не осталось подтверждённых blocking defects.**

---

## Быстрый старт

```text
1. Клонируйте или скачайте репозиторий
2. Запустите install.ps1 или install.sh
3. Перезапустите Codex при необходимости
4. Откройте code project
5. Завершите нетривиальную задачу
6. Запустите $adversarial-review — это STRICT
7. Если runtime не раскрывает trusted reviewer metadata, но findings нужны, явно запросите BEST_EFFORT
```

---

<p align="center">
  <strong>Ваш coding agent пишет решение.<br/>Devil's Advocate пытается доказать, что оно неправильное.</strong>
</p>

<p align="center">
  <code>$adversarial-review</code>
</p>
