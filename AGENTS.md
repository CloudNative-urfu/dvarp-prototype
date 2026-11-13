# AGENTS.md — правила работы для ИИ-агентов

Проект: **DARP prototype** — полигон оценки гвардрейлов для агента возвратов
интернет-магазина (снимок ревизии 11 в корне; `revisions/` — архив ревизий
2–11 с независимой критикой). Обзор репозитория — `README.md`.

Репозиторий: `CloudNative-urfu/dvarp-prototype`
Доска проекта: https://github.com/orgs/CloudNative-urfu/projects/1

Этот файл обязателен к прочтению перед любой работой в репозитории.

## 0. Модель веток

- `develop` — интеграционная ветка (default). Сюда merge'ятся PR с изменениями.
- `main` — релизная ветка, защищена. Напрямую пушить нельзя; изменения только
  через PR из `develop`.

## 1. Главное правило: issue ↔ доска

Любая задача оформляется **issue** в репозитории `CloudNative-urfu/dvarp-prototype`
и добавляется на доску.

Issue не может существовать вне доски. При создании issue агент в том же шаге
ОБЯЗАН:

1. Добавить issue на доску `CloudNative-urfu/projects/1` (статус `Backlog`).
2. Назначить исполнителя (**assignee**) — одного из участников команды
   (см. таблицу ниже).

Если issue создать нельзя (нет прав, нет доступа к доске) — сообщи об этом и
передай управление человеку.

## Команда

| GitHub | Имя |
| --- | --- |
| `axstiz` | Матвей Вахрушев |
| `desmitry` | Dmitry Sergeevich |
| `sser1to` | Никита Лецколюк |
| `BruhWhyMeHaveNoIdea` | Alexander |
| `geekiot-hub` | Kirill Samoylenkov |

Исполнителя агент назначает как **предполагаемого**, исходя из задачи и
загруженности. Автор задачи может изменить исполнителя.

## Разрешение владельца на коммиты

Владелец репозитория разрешает ИИ-агентам создавать Git-коммиты в ветках
задач, в том числе автоматически (сохранение результатов проверок,
критики, ревизий). Правила:

- Коммиты без криптографической подписи GPG/SSH (`commit.gpgsign=false`).
- Фактические даты и явно настроенное авторство.
- Не выполнять force-push и не переписывать опубликованные коммиты без
  отдельного разрешения владельца.
- Пуш — только в ветку задачи; прямой пуш в `develop` в контексте задачи
  запрещён (исключение — по явному запросу человека), в `main` — запрещён
  всегда.
- Не включать секреты, учётные данные, приватные ключи, токены и файлы
  окружения (`.env*`, `secrets/`, `*-secrets/`, ключи, сертификаты).

## Требования к окружению

- GitHub CLI `gh` со scopes `repo` и `project`; проверка `gh auth status`.
- Python >= 3.11; ruff, pre-commit, commitizen
  (`uv tool install ruff pre-commit commitizen`).
- Хуки: `pre-commit install && pre-commit install --hook-type commit-msg`.
- Если нужных прав/инструментов нет — не обходи ограничение, сообщи человеку.

## Workflow агента

Задачи на реализацию берём из статуса **`Ready`**, не из `Backlog`. В
`Backlog` — входящие и ещё несформулированные задачи.

### 1. Проверить, нет ли уже issue

```bash
gh issue list -R CloudNative-urfu/dvarp-prototype --state open --search "<ключевые слова>"
```

### 2. Создать issue + добавить на доску

```bash
issue_url=$(gh issue create -R CloudNative-urfu/dvarp-prototype \
  --title "<краткий заголовок>" \
  --body "<описание задачи>" \
  --assignee <github-login-исполнителя>)

PROJECT_ID=$(gh api graphql -f query='
  query { organization(login: "CloudNative-urfu") { projectV2(number: 1) { id } } }' \
  --jq '.data.organization.projectV2.id')

ISSUE_ID=$(gh api repos/CloudNative-urfu/dvarp-prototype/issues/<N> --jq '.node_id')

ITEM_ID=$(gh api graphql -f query='
  mutation($project: ID!, $content: ID!) {
    addProjectV2ItemById(input: { projectId: $project, contentId: $content }) {
      item { id }
    }
  }' -f project="$PROJECT_ID" -f content="$ISSUE_ID" \
  --jq '.data.addProjectV2ItemById.item.id')

gh project item-edit --id "$ITEM_ID" --project-id "$PROJECT_ID" \
  --field-id PVTSSF_lADOFDx2Cs4BmODnzhk37EQ \
  --single-select-option-id f75ad846   # Backlog
```

### 3. Реализация

- Ветка от `develop`: `feat/<номер-issue>-<slug>` (либо `fix/`, `docs/`,
  `chore/`, `refactor/`). Задача делается только в ветке; прямой пуш в
  `develop` в контексте задачи запрещён (исключение — по явному запросу
  человека).
- Статус issue на доске → `In progress`.
- Спецификация: содержательное изменение (код прототипа, эксперимент,
  измерение, процесс) обязательно оформляется каталогом
  `openspec/changes/<change-id>/` в той же ветке; технические задачи
  (инфраструктура, документация, `chore`) — без спецификации. В обоих
  случаях работа ведётся в ветке задачи и вливается только через PR.
- Коммиты — Conventional Commits (см. ниже).
- PR **в `develop`**, squash-merge; в описании — `Closes #<номер-issue>`,
  статус → `In review`. С `Closes #N` PR свяжется с issue и сам появится
  у него на доске («Linked pull requests»).
- Блокирующие проверки CI: `ruff` и `commit-msg` (по заголовку PR).

### 4. Завершение

После мержа PR в `develop`:

- Статус issue на доске → `Done` (issue закрывается сам по `Closes #N`).
- Содержательные изменения с openspec-каталогом: чеклист `tasks.md` закрыт,
  финальная верификация (см. README) выполнена или явно отложена с
  обоснованием.

## Доска

Issue добавляется на доску **DVARP Board** (`projectV2(number: 1)`) и
получает статус. Команды — см. раздел «Workflow агента» выше.

Семантика поля `Status` (id `PVTSSF_lADOFDx2Cs4BmODnzhk37EQ`):

| Статус | option-id | Когда |
| --- | --- | --- |
| Backlog | `f75ad846` | issue создан, ещё не готов к взятию |
| Ready | `61e4505c` | задача сформулирована, ждёт исполнителя |
| In progress | `47fc9ee4` | идёт реализация |
| In review | `df73e18b` | открыт PR |
| Done | `98236657` | смержено в `develop` |

## Соглашение о коммитах

Conventional Commits, английский. Формат `<type>(<scope>): <description>`.

- Типы: `feat`, `fix`, `docs`, `build`, `ci`, `chore`, `test`, `refactor`,
  `perf`, `style`, `revert`.
- Рекомендуемые scope: `research`, `infra`, `defense`, `attack`, `docs`.
- Примеры: `chore(research): preserve darp-revision-11 ...`,
  `feat(infra): add ruff baseline`.

Проверка:

- Локально — pre-commit hook `commitizen` (`commit-msg`).
- В CI — workflow `commit-msg` (**блокирующий**). PR мержатся squash'ем,
  поэтому проверяется заголовок PR: он должен быть conventional и совпадать
  с итоговым коммитом.

## Специфика прототипа

- Замороженный defense-код (`darp_range/{configs,guard_contract,
  guard_plugins,guard_policy,executor,tools}/`) защищён
  хешами `configs.lock.json` (NFR-2.10). Эти файлы не редактируются без
  процедуры перезаморозки; перезаморозка согласуется с владельцем.
- Каталог `revisions/` — архив: не линтуется, не изменяется.
- Юнит-тесты и полный сквозной verify (`python3 scripts/verify.py`,
  ~25–35 мин) выполняются локально и в CI не входят.
- Изменение без openspec-спецификации (если оно содержательное) не
  принимается — см. `README.md`.

## Запреты

- Создавать issue без исполнителя из команды.
- Оставлять issue вне доски `CloudNative-urfu/projects/1`.
- Пушить напрямую в `main`.
- Пушить напрямую в `develop` в контексте задачи — только ветка под задачу
  + PR (исключение — по явному запросу человека).
- Коммитить и пушить в ветки без задачи или вне контекста issue.
- Читать и изменять файлы секретов (`.env`, `secrets/`, ключи, токены,
  сертификаты).
