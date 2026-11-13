# corpus/

Замороженный корпус v2 (компонент `corpus`): `darp_corpus_v2.jsonl` — 20 атак по трём осям
FR-1.21 (цели `break` / `slow` / `burn`) и 20 сопоставимых чистых контролей FR-1.22 (английский,
«dirty English»), с ground truth в терминах архетипов заказов и происхождением; `MANIFEST.json` —
sha256 файла и каждой записи, покрытие осей. Корпус v2 (issue #29, отклонение канона FR-1.21):
пары p21–p24 цели `shift` удалены вместе с перефразами в `holdout_v2/` — эскалация нейтральный
исход процесса, а не нарушение; остальные записи побайтно совпадают с v1 (тег `archive/rev11`).
Не редактировать вручную: источник — `darp_range/corpus/entries.py`, заморозка —
`python3 -m darp_range.corpus freeze`, проверка — `python3 -m darp_range.corpus verify`.
Производные наборы: `holdout_v2/` (перефразы; удаление пар — `scripts/make_holdout_v2.py
--prune`), `heldout_v3/`, `trusted_v7/` (`scripts/make_heldout_v3.py`, `scripts/make_trusted_v7.py`).
