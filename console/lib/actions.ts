// Polygon paths the console can launch. Shared by the client (forms) and the server (argv).
// The server builds argv only from these definitions: the client never sends a command line.

export type ParamKind = "configs" | "seeds" | "segment" | "name" | "run" | "int";

export type ParamDef = {
  key: string;
  label: string;
  kind: ParamKind;
  hint?: string;
  options?: { value: string; label: string }[];
  default?: string;
  min?: number;
  max?: number;
};

export type ActionId =
  | "configs-verify"
  | "agent-demo"
  | "attackers-demo"
  | "offline"
  | "verify-run"
  | "shop-smoke"
  | "live";

export type ActionDef = {
  id: ActionId;
  title: string;
  summary: string;
  detail: string;
  duration: string;
  usesModel: "never" | "optional" | "always";
  params: ParamDef[];
};

const MODEL_PARAM: ParamDef = {
  key: "model",
  label: "Модель",
  kind: "segment",
  options: [
    { value: "stub", label: "Заглушка" },
    { value: "live", label: "Живая модель" },
  ],
  default: "stub",
};

const NAME_PARAM = (prefix: string): ParamDef => ({
  key: "name",
  label: "Каталог результата",
  kind: "name",
  hint: "runs/<имя>, латиница, цифры, - и _",
  default: prefix,
});

export const ACTIONS: ActionDef[] = [
  {
    id: "configs-verify",
    title: "Проверка заморозки",
    summary: "Хеши конфигураций и defense-кода против configs.lock.json",
    detail:
      "Сверяет каждую замороженную конфигурацию и защитный код с хешами блокировки. Если хоть один файл изменён без перезаморозки, прогоны теряют силу.",
    duration: "меньше секунды",
    usesModel: "never",
    params: [],
  },
  {
    id: "agent-demo",
    title: "Агент возвратов",
    summary: "Корпус через канал: g0, g_in, g_ctx и усиленная конфигурация",
    detail:
      "Агент обслуживает атакующие и контрольные записи корпуса. Оценщик по состоянию БД считает нарушения; контрольные клиенты должны быть обслужены. В живом режиме идут две записи на настоящей модели.",
    duration: "заглушка ~10 с, живая модель ~1 мин",
    usesModel: "optional",
    params: [MODEL_PARAM, NAME_PARAM("agent-demo")],
  },
  {
    id: "attackers-demo",
    title: "Атакующий",
    summary: "Режимы D и A: корпус, LLM, шаблонный и случайный поиск",
    detail:
      "Проверяет атакующую сторону: память между попытками, остановку при успехе, лимиты запросов и реплик, нарушения протокола.",
    duration: "заглушка ~2 с",
    usesModel: "optional",
    params: [MODEL_PARAM, NAME_PARAM("attackers-demo")],
  },
  {
    id: "offline",
    title: "Офлайн-прогон",
    summary: "Сквозной прогон по конфигурациям: V, ΔV, Q, L*, Pareto",
    detail:
      "Раннер собирает поток легальных клиентов и атак, каждая конфигурация проходит его на своём снимке БД. На выходе таблица окупаемости L* и Pareto-фронт. Модель не вызывается.",
    duration: "5 конфигураций ~4 мин, все ~14 мин",
    usesModel: "never",
    params: [
      {
        key: "configs",
        label: "Конфигурации",
        kind: "configs",
        hint: "пусто = все замороженные",
        default: "g0,g_in,g_ctx,g_layered,g_simple",
      },
      { key: "seeds", label: "Seed'ы", kind: "seeds", default: "7", hint: "через запятую, до 5" },
      {
        key: "attackMode",
        label: "Атакующий",
        kind: "segment",
        options: [
          { value: "D", label: "D, корпус" },
          { value: "A", label: "A, адаптивный" },
        ],
        default: "D",
      },
      NAME_PARAM("offline"),
    ],
  },
  {
    id: "verify-run",
    title: "Перепроверка прогона",
    summary: "Цепочки хешей журналов, бюджет, полнота отчёта",
    detail:
      "Заново читает журналы готового прогона и проверяет, что цепочки хешей целы, отчёт полон, а учёт затрат сходится.",
    duration: "~5 с",
    usesModel: "never",
    params: [{ key: "run", label: "Прогон", kind: "run" }],
  },
  {
    id: "shop-smoke",
    title: "Контур магазина",
    summary: "17 сценариев: оператор, выплаты, сбои и рестарты сервисов",
    detail:
      "Поднимает сервис магазина и эмулятор платежей на 127.0.0.1 и гоняет сценарии ручной проверки возврата: одобрение, отказ, обрывы, дубли, конкурентные решения, падения процессов.",
    duration: "~45 с",
    usesModel: "never",
    params: [NAME_PARAM("shop-smoke")],
  },
  {
    id: "live",
    title: "Живой прогон",
    summary: "Агент и атакующий на локальной модели, бюджет 60 запросов",
    detail:
      "Тот же прогон, но агент, а в режиме A и атакующий, работают через OpenAI-совместимый API модели из настроек. Атакующий изолирован в песочнице bwrap.",
    duration: "от 3 до 5 мин",
    usesModel: "always",
    params: [
      {
        key: "attackMode",
        label: "Атакующий",
        kind: "segment",
        options: [
          { value: "A", label: "A, адаптивный" },
          { value: "D", label: "D, корпус" },
        ],
        default: "A",
      },
      { key: "configs", label: "Конфигурации", kind: "configs", default: "g0,g_in" },
      { key: "maxRequests", label: "Бюджет запросов", kind: "int", default: "60", min: 4, max: 200 },
      NAME_PARAM("live"),
    ],
  },
];

export const actionById = (id: string) => ACTIONS.find((a) => a.id === id);

export const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,47}$/;
export const SEEDS_RE = /^\d{1,6}(,\d{1,6}){0,4}$/;
