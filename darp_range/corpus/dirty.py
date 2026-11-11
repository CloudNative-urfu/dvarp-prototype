"""Эвристика «dirty English» (NFR-2.7): признаки неотполированного текста non-native автора.

Это не классификатор качества языка, а грубая проверка корпуса: тексты не должны быть
литературными, и стиль не должен отличать атаки от контролей (иначе детектор учится
на стиле, а не на атаке). Каждый признак — отдельное регулярное выражение с именем.
"""

from __future__ import annotations

import re

_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # строчное «i» как местоимение
    ("lowercase_i", re.compile(r"(?:^|[\s,.(])i(?=[\s'’,.!?])")),
    # пропущенный апостроф
    ("no_apostrophe", re.compile(r"\b(?:dont|cant|wont|im|didnt|doesnt|isnt|ive|thats|"
                                 r"its not|whats|wasnt|arent|couldnt)\b", re.I)),
    # согласование подлежащего и сказуемого
    ("agreement", re.compile(r"\b(?:it|he|she|this|that|item|order|box|policy|system) "
                             r"(?:dont|have|say|come|arrive|work|need|show)\b", re.I)),
    ("is_not_my", re.compile(r"\bis not (?:my|mine|why)\b", re.I)),
    ("u_ur_pls", re.compile(r"\b(?:u|ur|pls|plz|thx|bc|ok so|lol|btw)\b", re.I)),
    ("double_punct", re.compile(r"[!?]{2,}|\.\.\.")),
    ("typo", re.compile(r"\b(?:recieve|recieved|adress|definately|untill|wich|thier|becuase|"
                        r"alot|realy|aswell|payed|dissapointed|dissapoint|refound|beggining|"
                        r"responce|wierd|garantee|immediatly|tommorow|sended|buyed|"
                        r"comission|neighboor)\b", re.I)),
    # калька / нестандартные конструкции
    ("calque", re.compile(r"\b(?:since \d+ (?:days|weeks)|i am waiting since|give me back|"
                          r"please to|money back right now|how much time i|for make|"
                          r"explain me|say me|i want that you|is broken already|"
                          r"more better|make the refund|do the return|came broken|"
                          r"dont fit|it fit|not fit me|wrong size for me)\b", re.I)),
    # предложение с маленькой буквы после точки
    ("lower_sentence", re.compile(r"[.!?] [a-z]")),
    # строка начинается с маленькой буквы
    ("lower_start", re.compile(r"^[a-z]", re.M)),
)

MARKER_NAMES = tuple(n for n, _ in _MARKERS)


def dirty_markers(text: str) -> list[str]:
    """Имена признаков, найденных в тексте (каждый — не более одного раза)."""
    return [name for name, rx in _MARKERS if rx.search(text)]
