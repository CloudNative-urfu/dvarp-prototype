# Разборы методологий: 19 ★-работ + 3 OSS-ассета

Исходник — матрица ★ ближайших работ литобзора и каталог ассетов рамки. Вопрос: что из сборки
(harness, данные, протоколы, затраты) идёт в полигон напрямую / через адаптацию / непереносимо.
Шаблон каждого разбора: Harness · Данные · Исходы · Затраты · Статистика · Trap-деталь →
Перенос/Адаптация/Непереносимо (с FR/NFR) → Что осталось непонятным. Ссылки — на файл репо у работы.

Итог: полностью переносимо — 8, частично — 14, непереносимых нет.

| # | Работа [arXiv ID] | Вердикт | Что можно брать | Разбор | Код |
|---|---|---|---|---|---|
| 1 | AgentDojo [2406.13352] | **переносимо** | каркас: environment+tools+логи, defense-пайплайн, плагинный контракт (FR-1.5/1.11) | [разбор](agentdojo.md) | [ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) |
| 2 | ATOM [2610.23254] | **переносимо** | словарь исходов (FR-1.19), label-blind судья+quote-верификация (FR-1.18), 33k траекторий | [разбор](atom.md) | [AgentAutopsy-15C4](https://anonymous.4open.science/r/AgentAutopsy-15C4/) |
| 3 | Act or Escalate [2604.08588] | **переносимо** | τ* = 1 − c_ℓ/c_w = наш L* (FR-1.24), извлечение implicit threshold p* | [разбор](act-or-escalate.md) | [mattdisorbo/madm](https://github.com/mattdisorbo/madm) |
| 4 | Detection→Decision [2610.20907] | **переносимо** | замороженный протокол (NFR-2.8/2.10), кривые деферрала (FR-1.15/1.16) | [разбор](detection-to-decision.md) | [calibrated-decision-models](https://github.com/lanqianlong/calibrated-decision-models) |
| 5 | RAGWarrant [2609.34179] | **переносимо** | repro по хэшам в Docker (NFR-2.2/2.5), bootstrap-проверка paired runs | [разбор](ragwarrant.md) | [ragwarrant-governance](https://github.com/RAGWarrant/ragwarrant-governance) |
| 6 | Educational tutors [2605.06669] | **переносимо** | один таймер/holdout для всех адаптеров, severity S0–S3 (FR-1.19/1.22) | [разбор](educational-tutors.md) | [educational-llm-guardrails-bench](https://github.com/alemaiorano/educational-llm-guardrails-bench) |
| 7 | FP-DoS [2410.02916] | **переносимо** | режим slow/burn: FP-суффикс ~30 симв. против ≤8B OSS (FR-1.20, NFR-2.6) | [разбор](fp-dos.md) | [zqzqz/AdvLLM](https://github.com/zqzqz/AdvLLM) |
| 8 | PIAtlas/PICorpus [2610.24999] | **переносимо** (источник атак) | корпус 63.5k шаблонов, таксономия 6/34 (FR-1.21), выборка для режима A | [разбор](piatlas.md) | [piatlas](https://github.com/stevendamianakis/piatlas) |
| 9 | BELLS-O [2606.20668] | частично | унифицированный guardrail-интерфейс, отчётность детекция/FPR/latency/$ (FR-1.17/1.23); датасеты gated | [разбор](bells-o.md) | [BELLS-O](https://github.com/CentreSecuriteIA/BELLS-O) |
| 10 | Gate AI [2606.02959] | частично | стат-каркас: рабочая точка, leakage-анализ, matched-FPR CI (NFR-2.8/2.12) | [разбор](gate-ai.md) | нет |
| 11 | SoK [2506.10597] | частично | break = allow ∧ judge harmful (FR-1.19), атак-конфиги с бюджетами | [разбор](sok-jailbreak-guardrails.md) | [SoK4JailbreakGuardrails](https://github.com/xunguangwang/SoK4JailbreakGuardrails) |
| 12 | SafePyramid [2606.29887] | частично | прототип g_proc: guardrail по политике в контексте (FR-1.6), RMR/RDR | [разбор](safepyramid.md) | [safepyramid](https://github.com/bytedance/safepyramid) + [HF](https://huggingface.co/datasets/ByteDance/SafePyramid) |
| 13 | RouteGuard [2605.30693] | частично | консолидация таксономии, двухфазная переразметка, роутер+эксперты (FR-1.5) | [разбор](routeguard.md) | нет |
| 14 | AgentDoG [2601.18491] | частично | ATBench + OSS 4–8B + промпты: шаблон разметки траекторий | [разбор](agentdog.md) | [AgentDoG](https://github.com/AI45Lab/AgentDoG) + [HF](https://huggingface.co/collections/AI45Research/agentdog) |
| 15 | StepShield [2601.22136] | частично | temporal-метрики EIR/IG (FR-1.16/1.19), pre-execution протокол | [разбор](stepshield.md) | [glo26/stepshield](https://github.com/glo26/stepshield) |
| 16 | Policy Is Not Capability [2610.25060] | частично | аудит контаминации по hash, seed floor, де-контаминированный пересчёт (NFR-2.2/2.13) | [разбор](policy-is-not-capability.md) | нет |
| 17 | MutedRAG [2504.21680] | частично | break через FP: отравленный контент → guardrail отказывает легитимным (FR-1.22) | [разбор](mutedrag.md) | нет |
| 18 | Shield to Target [2606.14517] | частично | guardrail на критическом пути, cost-per-call, throughput/waiting (FR-1.15/1.16) | [разбор](shield-to-target.md) | нет |
| 19 | Oversight Capacity [2606.08919] | частично | cost-матрица 50:1, свип θ, AURC, «жирный» оператор в очередь (FR-1.15/1.16) | [разбор](oversight-capacity.md) | [headroom](https://github.com/turangenesis/headroom) |
| 20 | AGO [2610.01218] | частично | бета-биномиальный гейт ρ/δ/τ (FR-1.24), обязательная мета-оценка судьи (FR-1.18) | [разбор](ago.md) | нет |
| 21 | payments dispute [2606.01513] | частично | best-of-N оркестрация, Wilson/Newcombe по агрегатам; item-level недоступен | [разбор](payments-dispute.md) | обещан авторами отдельно |
| 22 | InjecAgent [2403.02691] | частично | корпус 1054 кейса, схема «payload в атакуемом поле» (FR-1.21); стенд одношаговый | [разбор](injecagent.md) | [InjecAgent](https://github.com/uiuc-kang-lab/InjecAgent) |

## С чего начать после zero-версии фабрики

1. **AgentDojo** — база harness'а (FR-1.10/1.11).
2. **PIAtlas + InjecAgent** — сырьё режима D: корпус атак по «механизм/цель/доставка» (FR-1.21).
3. **ATOM** — словарь исходов и независимый оценщик (FR-1.18/1.19).
4. **Act or Escalate** — математика эскалации L* (FR-1.24).
5. **Tutors + RAGWarrant** — дисциплина репро: holdout, таймер, хэши.
6. **BELLS-O + StepShield** — учёт затрат по компонентам (FR-1.17) и время вмешательства (FR-1.16).
7. **Detection→Decision** — заморозка протокола + политика деферрала.
8. **Shield-to-Target + FP-DoS + MutedRAG** — режимы slow/burn/shift без кода: реализация по тексту.
9. **SafePyramid + AgentDoG** — базовая линия g_proc и разметка траекторий.
10. **Oversight Capacity** — параметры оператора в модель очереди (симуляции перепроверить на живых прогонах).

## Что осталось непонятным (агрегат)

- Кода нет у 6 работ (№№10, 13, 16, 17, 18, 20) — воспроизведение по тексту.
- ATOM — anonymous.4open.science: проверить репо после раскрытия авторов.
- PIAtlas: абстракт противоречит v1 (5k+/42 vs 34 техники/131 субтехники) — см. разбор.
- Числа StepShield, Shield-to-Target, AGO, D→D, Oversight — на закрытых API: нужен свой прогон по их протоколам.
- Gate AI: ссылка на код не найдена (веб-поиск был недоступен) — перепроверить.
