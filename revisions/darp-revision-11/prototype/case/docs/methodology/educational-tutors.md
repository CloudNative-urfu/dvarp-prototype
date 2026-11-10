# Educational tutors (Prompt Injection Defenses for Educational LLM Tutors) [arXiv 2605.06669 v2] — методологический разбор
Вердикт: **Полностью переносимо.** Полный пакет репликации публичен — Docker + датасет + скрипты с контрольными суммами: «The source code and scripts required to reproduce the benchmark pipeline, metrics computation, and tables are publicly released in a versioned repository with checksums at https://github.com/alemaiorano/educational-llm-guardrails-bench». Протокол head-to-head унифицированно инструментирован: один сплит, одна метрика, один таймер; статистический набор — bootstrap + McNemar + multi-seed + sweep по порогам — всё доступно.
По-простому: Авторы собрали набор из 480 сообщений (в основном с атаками) и проверили на нём несколько защитных фильтров для обучающего LLM-тьютора. Все защиты прогнались по одной схеме: один сплит, одна метрика и один таймер.

Ссылка на код: https://github.com/alemaiorano/educational-llm-guardrails-bench

## 1. Harness
- Защита — 4 слоя defense-in-depth, L1 = 30 regex: «This layer performs high-speed lexical analysis using 30 curated regular expression patterns» (EN+PT-BR); L2 JSON-schema+XSS, L3 delimiter/boundary/control-chars/length-caps, L4 consecutive-block/throttle асинхронно. Порядок: «The defense pipeline executes sequentially: Input Lexical Filtering (Layer 1) \rightarrow Input Sandboxing (Layer 3) \rightarrow LLM Inference \rightarrow Output Structural Validation (Layer 2). Behavioral Heuristics (Layer 4) operate asynchronously on the session trace.»
- Сравнивают адаптеры: multi-layer, Prompt Guard 2 86M, NeMo Guardrails (gpt-4.1) + ablations No-Defense/L1-only; порог Prompt Guard калибруют на 20%-сплите: «Prompt Guard used the Hugging Face model meta-llama/Llama-Prompt-Guard-2-86M with threshold calibration on a stratified 20% calibration split (target FPR ≤ 1%)».
- Один сплит, одна метрика, один таймер: «All wall-clock latency measurements were captured with a monotonic high-resolution timer on the same hardware infrastructure used for our multi-layer pipeline, ensuring comparability across adapters»; метрики: bypass, ложные срабатывания (FP), avg/p95, cost/1K, QPS, severity S0–S3 судьи-LLM (gpt-4.1), confusion matrix.

## 2. Источники и срез данных
- 480 synthetic: 369 injection / 111 benign; «The deliberate class skew (369 injection / 111 benign = 77% injection rate) stress-tests filter precision under adversarial conditions and reflects a worst-case operational scenario»; 6 семейств атак (Instruction Override … Obfuscated-Encoded); 5 персон, en/pt-BR, turn_index.
- Сплит 20% calibration / 80% holdout, без пересечений; безобидные — уточнение/подсказка/отладка: «Ground-truth labels are used only for post-hoc metric computation and are not accessible to any adapter at prediction time»; артефакты с checksums в versioned-репо. Синтетика отревьюена авторами. Синтетика отревьюена авторами: «Synthetic samples are produced from a deterministic persona-scenario question bank and manually reviewed by the authors for label consistency and pedagogical plausibility».

## 3. Протокол фиксации исходов
- Итог бинарный, blocked/passed, на каждый адаптер; FP=0 на 111/111 benign: «Table 2 summarizes per-class outcomes for the primary multi-layer adapter (111/111 benign passed; 198/369 injections blocked)».
- Прошедшую атаку оценивают по фактическому downstream-ответу по шкале S0–S3: «Of the 171 inputs that escaped the input gate, 71 were rejected at the provider’s responsible-AI layer before reaching the tutor and 100 reached the tutor; the tutor’s pedagogical system prompt refused or redirected all 100» → «The classifier therefore assigns S0 (no impact) to every bypass event in this evaluation: 171/171 (100%) S0, with 0 events at S1, S2, or S3 and a critical bypass rate (S2+S3) of 0%». Страты на одном holdout: адаптер×persona, family×language, threshold×конфиг.

## 4. Учёт затрат
- Время по слоям: L1 под-мс: «the layered architecture introduces deterministic overhead from local validation stages, with Layer 1 regex checks remaining sub-millisecond in our instrumentation»; L2/L3 пролетают бесплатно, L4 = 0 мс офлайн по конструкции; по адаптерам avg/p95.
- Деньги: NeMo $12.50/1K, локальные $0; «NeMo Guardrails incurs non-zero per-query monetary cost from LLM rail calls ($12.50/1K in this configuration) with latency exceeding 1 second—prohibitive for interactive educational workflows»; throughput: multi-layer ≈400 QPS, Prompt Guard ≈13, NeMo <1.
- Стоимость не разложена по компонентам → FR-1.17 требует достройки; есть только latency-by-layer и cost/1K: «Prompt Guard also appears as $0.00/1K direct API cost in this setup because inference is local; however, this table does not monetize local compute infrastructure».

## 5. Статистика
- Stratified bootstrap 3000 label-preserving: «Stratified bootstrap CIs : 3000 replicates with label-preserving resampling for bypass rate, FPR, and latency»; Clopper-Pearson one-sided upper FPR (0% → 2.67%); парный McNemar: «McNemar confirms that the injection-detection improvement from Layer 1 only to the full multi-layer defense is statistically significant (p<10^{-25})».
- 10 seeds (42,1337,2024,101,202,303,404,505,606,707) с перегенерацией датасета, ranking стабилен; «While sufficient to show directional consistency, a larger sweep (e.g., N\geq 30) would be required for robust long-tail stability analysis». Свип: 11 порогов Prompt Guard × 3 rail-конфига NeMo: «The sweep covers Prompt Guard across 11 thresholds (cached scores) and NeMo across three rail configurations, each executed in turn against Azure OpenAI», calibrated-default строка байт-в-байт совпадает с baseline-прогоном: «so the calibrated-default row matches Table 3 byte for byte».

## 6. Trap-деталь
- Где расколются репликаторы: L4 слой, который блокирует серии попыток, в одноходовом офлайн-прогоне блокирует **0 атак по конструкции**: «in our offline, single-turn benchmark evaluation, Layer 4 blocks zero attacks. This is expected, as behavioral heuristics rely on temporal metadata and repeated variations that are not captured in static datasets». Нужен онлайн-протокол (per-turn outcome, inter-arrival, сигнатуры повторов); план: «our planned online protocol instruments session traces with per-turn outcomes, inter-arrival timing, and repeated-attempt signatures» — заготовка FR-1.16/FR-1.19.
- Второй разлом — язык и страты: bypass EN 25.1% vs PT-BR 73.5% (+48.3 pp), Role Hijacking 0% vs 100% в PT-BR; «The multi-layer defense achieves 25.1% bypass on English queries but 73.5% on Portuguese, indicating that Layer 1 lexical patterns lack sufficient PT-BR coverage. Role hijacking attacks in Portuguese achieve 100% bypass»; без стратификации по языку сравнение адаптеров неверно.

## Прямой перенос
- 4-слойный defense-in-depth со временем по слоям и выбором operating point → FR-1.5, FR-1.16.
- Severity S0–S3 по реальному downstream-ответу — шаблон break vs refused-и-погас vs absorbed → FR-1.18, FR-1.19.
- Zero-FPR operating point + Clopper-Pearson bound → FR-1.22; унифицированный инструментарий (один holdout, монотонный таймер: «All wall-clock latency measurements were captured with a monotonic high-resolution timer») → NFR-2.12.

## Адаптация
- Свип по порогу защиты → L*-кривая (кривая рабочих точек, а не одна точка) → FR-1.24; оси bypass/FPR/latency/cost/1K/QPS — прообраз (V,R,Q,E,C) → FR-1.23.
- Генератор/судья Azure gpt-4.1 → self-hosted ≤8B: Prompt Guard 2-86M уже OSS ≤8B → NFR-2.6, NFR-2.4; 10 seeds + McNemar + bootstrap → NFR-2.8.
- Модель атакера: black-box student-level, фиксация 6 семейств + persona-стратификация: «We assume a black-box, student-level adversary: a user who interacts with the tutoring interface through its normal input channels (query fields, chat turns) but has no privileged access to system internals» → FR-1.1, NFR-2.11.

## Непереносимо
- Долларовое разложение по компонентам не взять: «this table does not monetize local compute infrastructure» → NFR-2.9 остаётся на нас.
- Непрямая инъекция (retrieval/LMS) выборочно вне скоупа: «the threat model excludes network-level attacks, direct model access, data-poisoning during training, and prompt-injection through third-party document retrieval (indirect injection via external data sources)»; вектор через документы потребует своего слоя → FR-1.1.

## Что осталось непонятным
- Какая loss-функция за калибровкой target FPR ≤1% у Prompt Guard — not stated (в тексте только «threshold calibration … (target FPR ≤ 1%)»).
- QPS не нормированы на нагрузку (batch/сценарий) — throughput числа есть, нормировка не проговорена; throughput на другом хосте реплицируется слабо.
- «Достаточный» порог consecutive blocks у L4 численно не раскрыт: «if a student triggers \geq T consecutive blocks across recent turns, Layer 4 escalates to a session-level intervention (e.g., temporary rate-limiting or mandatory cooldown)» — T не задано.
