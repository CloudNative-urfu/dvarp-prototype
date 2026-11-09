# FP-DoS (LLM Safeguard is a Double-Edged Sword: Exploiting False Positives for Denial-of-Service Attacks) [arXiv 2410.02916] — дизассембли методологии
Verdict: Fully portable. Атака целиком разворачивается в ограничениях полигона: OSS/≤8B гвардрейлы (LlamaGuard-7b/2-8B/3-8B, Vicuna-7B), публичные датасеты, публичный MIT-код с параметрами экспериментов, метрика успеха — это напрямую R полигона (доля несправедливых отказов на легитимном трафике). Закрытые ветки (ChatGPT fine-tuning) выпадают, но не нужны для репликации ядра. Портируемость: публичный код + данные по ссылке + протокол в тексте.
По-простому: Авторы подобрали служебный текст (подсказку), из-за которого защитный фильтр начинает отказывать безобидным запросам. Это атака «отказ в обслуживании» через ложные срабатывания (FP): жертве достаточно получить вредоносный текст, чтобы фильтр начал отказывать самим себе.

- Код: https://github.com/zqzqz/AdvLLM (MIT; автор-корреспондент zqzqz = Qingzhao Zhang; README ссылается на arXiv 2410.02916; данные в папке `data`: safe/unsafe промпты + токсик-словник).

## 1. Harness
- Три роли: «We assume the existence of three parties, server, user, and attacker» — Server — LLM с фильтром-guardrail на входе; User — шаблон промпта из конфигов; Attacker — правит шаблон функцией g или травит fine-tuning. Вход один: «Each request m is constructed by concatenating a user-provided prompt, denoted by m_{u}, and a prompt template, denoted by m_{t}, i.e., m=m_{u}\oplus m_{t}»; цель: f(m_u⊕m_t)=0 ∧ f′(m_u⊕g(m_t))=1 — фильтр должен забраковать подмену как FP.
- 4 ветви: «We consider four threat models with varying attacker capabilities: (1) white-box prompt injection... (2) black-box prompt injection... (3) poisoned fine-tuning... (4) poisoned fine-tuning with backdoor»; агентной петли нет — «пассивный guardrail» → FR-1.8.

## 2. Источники и срез данных
- Безобидные (safe) промпты — из Alpaca, HumanEval, AGIEval: «We collect the safe prompts of general short questions from Alpaca, programming questions from HumanEval, and math problems, logical reasoning, and reading comprehension tasks from Agieval»; сплит train (оптимизация атаки) / test (оценка). Unsafe-семя — HarmBench: «We collect unsafe prompts from HarmBench», предфильтр по loss и токсик-словнику.
- В репо: `new_filtered_unsafe_prompt.json`, `toxic_words.txt` (README); размер test-safe — not stated, в примерах --num_cases 20.

## 3. Протокол фиксации исходов
- Успех = доля safe-промптов, где сработал safeguard-фильтр: «We assess success rate for all attacks, which is the ratio of safeguard warnings triggered on the set of safe prompts»; фиксируется вердикт-лейблом, а не траекторией, и это ровно R полигона (доля несправедливых отказов) при двустороннем нагружении.
- Стелс: длина подсказки в символах + семантика: «The loss of length is the number of characters in a candidate's plain text, favoring shorter candidates»; сходство мерит SentenceBERT (реализация: «The semantic similarity is assessed using SentenceBERT»); цель — таргет-ответ: «We also choose a target response as the text that will be generated on detection of unsafe content, e.g., “unsafe” for Llama Guard models and “I’m sorry” for safety-aligned models». Разбор break/refused/ignored — not stated.

## 4. Учёт затрат
- Бюджет атакующего: длина + число итераций; в loss входят длина и семантика: «The stealthiness loss involves criteria of length and semantics». В poisoned-ветке расход объёмом данных: «We implement the poisoned fine-tuning attacks on ChatGPT models via OpenAI fine-tune APIs, using 10 and 20 poisoned samples for the baseline attack and the backdoor attack respectively».
- Издержки guardrail/сервера (latency, токены, деньги) и разбивка по компонентам — not stated.

## 5. Статистика
- «For each experiment setting, we repeat the attack 20 times and report the averaged metrics» — только средние; seeds/CI — not stated. Перенос подсказки: «transferring the attack to Llama Guard 2, which is also derived from Llama 3 variants, resulted in a 65.8% success rate. Instead, transfer attacks on GPT-4o achieved a mere 0.7% success rate» → LG-3→LG-2 65.8%, Vicuna 54.8%, GPT-4o 0.7%.
- Ablation-сетка: multi vs single-task; фильтр None/Moderate/Strict; вставка prefix/suffix/random: «Token filter: None, Moderate, and Strict...»; плюс срезы по длине и категории промпта.
- Контрмеры Robey и Yuan: «random patching and resilient optimization severely reduce the true positive rate (TPR) to below 30% and increase the false positive rate (FPR) above 10%, rendering the safeguard system ineffective» — защита ломает штатный трафик, чистой победы нет.

## 6. Trap-деталь
- Где расколются репликаторы: строгий фильтр почти не режет успех, но растёт длина: «the strict filter, which bans specific toxic words, makes adversarial prompts longer»; Moderate сохраняет стелс — выбор токен-фильтра остаётся открытым параметром контрмер.
- В white-box токены удаляются по attention-важности последнего слоя, без градиентного пересчёта семантики: «we use the last layer of attention values to determine which tokens in the adversarial prompt are not important for the target response, thus they have a higher priority to be deleted».

## Прямой перенос
- Максимизация FP-отказов универсальной подсказкой ~30 символов со стелс-бюджетом: «the attacker can use our optimization process to automatically generate seemingly safe adversarial prompts, approximately only 30 characters long, that universally block over 97% of user requests on Llama Guard 3»; success rate → R → FR-1.19, FR-1.21, FR-1.23, NFR-2.11.
- GCG white-box MIT + LlamaGuard-7b/2-8B/3-8B (≤8B, english) → NFR-2.4/2.5/2.6; сплит train/test + ablation «scope×filter×insertion» → FR-1.22.
- Оценка контрмер по TPR/FPR после них: «they significantly impair the effectiveness of safeguards on normal data without attack» → FR-1.23, NFR-2.12.

## Адаптация
- В оригинале атакующий правит шаблон клиента; в полигоне доступен только текстовый канал FR-2.11 — suffix вставляется в атакуемое поле, склеиваемое в m = m_u ⊕ m_t: «In prompt injection scenarios, the attacker modifies the prompt template m_{t} using function g».
- Poisoned-ветка противоречит замороженной защите (NFR-2.10) — переиспользовать как стресс-тест, что пайплайн не позволяет дообучать защиту; механика: «We implement attacks that maliciously fine-tune the LLM using poisoned dataset. Our focus is on crafting the poisoned data itself, while keeping the fine-tuning process and loss function unchanged».
- Vicuna (safety-aligned; guardrail — сама модель) как вариант g_simple в бейзлайнах полигона.

## Непереносимо
- Ветки ChatGPT/GPT-4o/GPT-3.5 (black-box, fine-tune API OpenAI) закрыты — вне NFR-2.4.
- Сценарные real-world vectors (CVE, фишинг, агентность) описаны, числами не замерены: «In this section, we present a few example scenarios where the attack vectors are practical in real applications, with more details in Appendix A».

## Что осталось непонятным
- Seeds и разброс по 20 повторам — без CI; зафиксировано только «we repeat the attack 20 times and report the averaged metrics».
- Размер test-safe по каждому эксперименту; критерии деления train/test: «We divide the safe prompts into a training set used to input the attack algorithm, and a test set designated solely for evaluation».
- Как парсится ответ LlamaGuard (string-match по «unsafe»?) — not stated.
- Веса w1/w2 в loss и числа k1/k2: символы заданы — «Parameters include the number of iterations N, loss function weights (w_{1},w_{2}), number of token substitution and deletion (k_{1},k_{2})», значения в HTML не видны.
- «Универсальность» single vs multi-task: 97.1 vs 96.3 без CI; в тексте лишь «The success rate of single-task attacks is marginally higher than multi-task attacks in Table 1, with adversarial prompts of comparable lengths».
- Poisoned-backdoor на GPT-4o даёт лишь 12.5% — объяснения нет; в тексте только «The attack on GPT-4o only achieve < 16% success rate».
