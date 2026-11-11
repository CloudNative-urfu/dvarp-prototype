# Payments dispute (Compliance-Scored Best-of-N Guardrail Orchestration) [arXiv 2606.01513] — методологический разбор
Вердикт: **Частично переносимо.** Кода и данных нет: «Raw dispute records, evidence screenshots, customer messages, and production code cannot be released. To support review without exposing proprietary data, the paper specifies the request interface, compliance-score formula, evidence-normalization algorithm, best-of-N selection loop, and asynchronous telemetry pattern»; но три алгоритма с псевдокодом, статистический конвейер по агрегатным счётчикам и принцип «синхронное исполнение / асинхронный мониторинг» изложены воспроизводимо.
По-простому: eBay описывает производственный guardrail для споров о платежах: система генерирует N вариантов ответа и берёт лучший по композитному скору. Блокировка запроса идёт синхронно, а телеметрия и hot-reload параметров — в фоне.

Ссылка на код: не найдена; авторы обещают де-идентифицированный пакет отдельно после корпоративного одобрения: «A de-identified artifact package may be released separately after organizational approval».

## 1. Harness
- Production-контур eBay: guardrail-слой — единая поверхность для PII, moderation, schema-валидации и domain-правил вместо цепочки сервисов-гейтов: «We introduce a unified guardrail layer that (i) standardizes policy enforcement across use cases, (ii) supports multimodal inputs (text and images), and (iii) uses an explicit compliance score to drive best-of-N selection and early exit».
- Композитный score C(y) = Σ w_j·g_j(y) / Σ w_j ∈[0,1], ×100: «C(y)=\frac{\sum_{j=1}^{m}w_{j}g_{j}(y)}{\sum_{j=1}^{m}w_{j}}\in[0,1]», Best-of-N: «The core runtime loop is “best-of-N under a latency budget.” For each request, the orchestrator generates candidates in parallel and scores each against Eq. (1). If a candidate meets a compliance threshold τ, the system exits early» — параллельные головы H с общим таймаутом T_max. SelectionMetadata: «The guardrail layer emits request metadata including candidate attempts, latency, compliance score, rejection reasons, fallback decisions, and configuration version».
- Enforcement и скоринг — синхронно в request path; телеметрия, дашборды, hot-reload — асинхронно: «Validation and compliance scoring occur synchronously within the request path... Telemetry aggregation, dashboards, and configuration updates are applied asynchronously to avoid request-path latency».

## 2. Источники и срез данных
- Агрегаты исходов variable vs. control: Overall 45.7% (301/659) vs 34.6% (536/1548); Fraud 50.5% (501) vs 48.7% (708); INR adjusted 30.3% (152) vs 22.7% (840); Evidence Ranking 17.30% (445) vs 14.53% (406) — «TABLE III: Downstream outcome readouts (variable vs. control). “Win%” is by count; “WinAmt%” is amount-weighted».
- Responsible-AI: «the corpus contains 770 unique reviews, with 318 labeled reviews available for estimation across five reviewers. Projected to the full corpus, approximately 574/770 reviews are rated 3/5, 157/770 are rated 4–5, and 39/770 are rated 1–2; the projected mean rating is 3.17». OCR-срез: «the slice contains 70 cases, five analysts, and 350 ratings... the global weighted median of per-case mean Overall OCR scores is 3.2. Pairwise ordinal agreement is moderate to strong: the highest reported weighted kappa alignments are 0.753, 0.741, and 0.722».
- 74.5% ревью содержат нарратив, только ~77/770 — структурный флаг: «Narrative reviewer detail appears in 74.5% of labeled exports... while only about 77/770 contain any structured issue flag... indicating that a flag-only dashboard would miss much of the review nuance» — урок журналу состояния.

## 3. Протокол фиксации исходов
- Дизайн явно НЕ randomized A/B — только ассоциации: «The payments outcome readouts in Table III are operational scenario comparisons, not a randomized A/B test... we interpret the observed differences as associations and avoid claiming that the guardrail system alone caused the win-rate lift. Residual confounding is possible from case mix, dispute reason, network policy, seller behavior, evidence availability, and time-window effects».
- Item-исход: win по счёту и win по сумме; count-исходы — через CI на пропорциях; amount-weighted — effect size без интервалов: «Amount-weighted win rates are reported as effect sizes only: the PDFs provide aggregate amount-weighted percentages but not the per-dispute dollar amounts needed for valid bootstrap, permutation, or design-based confidence intervals».
- Per-request: «the early-exit selector returns a compliant summary or routes below-threshold/high-risk cases to human review»; недоступные поля не заполняются: «Section VIII treats this as a limitation rather than filling the missing fields with unverified values».

## 4. Учёт затрат
- E[Cost] = c·E[N_calls] + m·E[N_manual]: «where c is per-call model cost and m is per-item manual handling cost. The unified guardrail layer reduces E[N_manual] by enforcing PII, moderation, and schema constraints before output is emitted, while bounding E[N_calls] through early exit at threshold τ» — параметризация C_guard с выделенной человеческой компонентой.
- Публичный readout: 5 попыток / бюджет 20 с / 91% compliance: «a typical run uses 5 candidate attempts, completes within a 20s request budget, and reaches 91% compliance»; latency-перцентили, per-summary cost, fallback rate, ablations N/τ не публикуются: «Request-level latency percentiles, cost, fallback rate, human-review reduction, and ablation metrics are not reported in this version because they require validated request-level telemetry exports».
- Разложение по компонентам (модель/токены/фильтры/человек) — not stated (в тексте cost есть только в агрегатах) — для FR-1.17 достраивать самому.

## 5. Статистика
- Wins = round(count × Win%): «we recover approximate wins by multiplying the reported dispute counts by the reported percentages and rounding to the nearest integer»; Wilson 95% CI, Newcombe/Wilson разность, two-proportion z-тест, RR/OR: «We then compute Wilson 95% confidence intervals for each rate, Newcombe/Wilson intervals for rate differences, two-proportion z-tests, relative risk (RR), and odds-ratio effect sizes» — Overall +11.0 pp [6.6,15.5] p<0.001; INR +7.5 pp [0.2,15.7] p=0.045; Fraud +1.8 NS; Ranking +2.8 NS: «Fraud and local evidence-ranking deltas are directionally positive but not statistically significant from the aggregate count data».
- Amount-weighted без item-долларов признана невалидной для CI; нужны per-dispute суммы + bootstrap/permutation/design-based: «Statistically valid confidence intervals for amount-weighted outcomes require per-dispute dollar amounts and an appropriate bootstrap, permutation, or design-based test»; предписан exact item-level анализ до camera-ready: «it should be replaced by exact item-level analyses before camera-ready submission».
- Страты Overall/Fraud/INR/Ranking — аналог разбора по домену/каналу.

## 6. Trap-деталь
- Где расколются репликаторы: заголовок читается как «A/B-эксперимент», но дизайн помечен не-рандомизированным: «Causal interpretation would require a randomized experiment, a matched cohort design, or covariate-adjusted item-level analysis. We therefore describe observed deltas as associations». Авторы явно отказались от причинной интерпретации, от CI на amount-weighted и от request-level ablations без валидированной телеметрии: «it does not contain enough request-level records to report validated ablations for single-candidate generation, N sensitivity, early-exit threshold sweeps, guardrail-weight perturbations» — проговорённая граница инференса.

## Прямой перенос
- Композитный guardrail-score C(y) с весами + ранний выход по τ («If a candidate meets a compliance threshold τ, the system exits early») → FR-1.5, FR-1.23; синхронный гейт + асинхронный мониторинг/hot-reload: «background worker: aggregate metrics; update dashboards; propose θ changes; hot-reload» → FR-1.16.
- SelectionMetadata → каркас журнала состояния («candidate attempts, latency, compliance score, rejection reasons, fallback decisions, and configuration version») → FR-1.18, FR-1.19.
- E[Cost]=c·calls+m·manual → NFR-2.9; Wilson/Newcombe по счётным агрегатам («Wilson 95% confidence intervals... for aggregate count readouts») → FR-1.23, FR-1.19.

## Адаптация
- Прежние «разрозненные гейты + manual triage» против unified layer = бейслайн g_proc vs новый guardrail: «Prior to a unified guardrail layer, production systems often stitched together separate PII redaction, content moderation, and format validation steps, leading to fragmented logic, slower request paths, and higher operational cost» → NFR-2.12.
- Стратификация по типам диспутов (Overall/Fraud/INR/Ranking) → attacker/legitimate-каналы → FR-1.1; ранний выход C(y)≥τ — снижение queue-latency («completes within a 20s request budget») → FR-1.15, FR-1.16.
- Псевдокод всех трёх алгоритмов статьи достаточен для пере-реализации; веса w_j и порог τ брать из своей калибровки — в статье они не раскрыты: «learned calibration of weights w_{j} in Eq. (1), request-level ablations for N, τ...» числится как future work.

## Непереносимо
- Операционные readouts без рандомизации не дают причинного lift: «we avoid claiming that the guardrail system alone caused the win-rate lift» — переносить только статистический слой, не выводы; полигону нужен paired D/A-дизайн → NFR-2.8.
- Мультимодальная часть (OCR, proof-of-delivery) не переносится: «A single guardrail surface for text and image evidence (e.g., proof-of-delivery screenshots)» — только принцип единой enforcement-поверхности.

## Что осталось непонятным
- Как выставляются веса w_j и порог τ в проде — калибровка анонсирована как future work: «Future work includes... learned calibration of weights w_{j} in Eq. (1), request-level ablations for N, τ, and guardrail weights».
- Как распределён бюджет 20 с между параллельными головами и попытками — публичный readout даёт только агрегаты, перцентили не раскрыты («Request-level latency percentiles... are not reported in this version»).
- Как агрегирован «91% compliance»: по финальному кандидату, по первой попытке или по всем — not stated (в тексте только «reaches 91% compliance»).
