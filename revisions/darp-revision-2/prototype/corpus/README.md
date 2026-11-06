# corpus/

Замороженный корпус v1 (компонент `corpus`): `darp_corpus_v1.jsonl` — 24 атаки по трём осям
FR-1.21 и 24 сопоставимых чистых контроля FR-1.22 (английский, «dirty English»), с ground truth
в терминах архетипов заказов и происхождением; `MANIFEST.json` — sha256 файла и каждой записи,
покрытие осей. Не редактировать вручную: источник — `darp_range/corpus/entries.py`,
заморозка — `python3 -m darp_range.corpus freeze`, проверка — `python3 -m darp_range.corpus verify`.
Описание: `artifacts/components/corpus.md`.
