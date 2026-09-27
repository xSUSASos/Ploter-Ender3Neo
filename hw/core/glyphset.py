# -*- coding: utf-8 -*-
"""
Единое представление глифа и набор шрифтов.

Глиф — это просто набор штрихов (полилиний) в единицах шрифта, где
базовая линия = 0, высота заглавной = CAP (14). Откуда взялись штрихи —
из встроенного векторного шрифта или из фотографии вашей руки — здесь
уже неважно.

У одного символа может быть НЕСКОЛЬКО вариантов начертания: именно это
и делает текст похожим на рукописный, а не на штамп.
"""

import json
import math
import os
from dataclasses import dataclass, field

from . import font_vector as FV

CAP = FV.CAP
XH = FV.XH


# --------------------------------------------------------------------- глиф

@dataclass
class Glyph:
    char: str
    advance: float
    strokes: list = field(default_factory=list)   # [[(x, y), ...], ...]
    source: str = "builtin"                       # builtin | user
    note: str = ""

    def bbox(self):
        pts = [p for s in self.strokes for p in s]
        if not pts:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def ink_length(self):
        return sum(math.dist(s[i], s[i + 1])
                   for s in self.strokes for i in range(len(s) - 1))

    def to_dict(self):
        return {
            "char": self.char,
            "advance": round(self.advance, 3),
            "source": self.source,
            "note": self.note,
            # штрихи пишем плоскими списками — файл получается вдвое компактнее
            "strokes": [[round(v, 2) for p in s for v in p] for s in self.strokes],
        }

    @classmethod
    def from_dict(cls, d):
        strokes = []
        for flat in d.get("strokes", []):
            strokes.append([(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)])
        return cls(char=d["char"], advance=float(d["advance"]),
                   strokes=strokes, source=d.get("source", "user"),
                   note=d.get("note", ""))

    def copy(self):
        return Glyph(self.char, self.advance,
                     [list(s) for s in self.strokes], self.source, self.note)


# ------------------------------------------------------------- нормализация

def normalize_strokes(strokes, baseline_y, cap_y, y_down=True,
                      side_bearing=0.10, min_advance=4.0):
    """
    Привести штрихи из «пиксельных» координат ячейки к единицам шрифта.

    baseline_y / cap_y — координаты базовой линии и линии заглавных
    в той же системе, что и strokes (обычно пиксели, ось Y вниз).
    side_bearing — боковые пробелы, в долях от CAP.

    Возвращает (advance, strokes) в единицах шрифта.
    """
    span = abs(cap_y - baseline_y)
    if span < 1e-6:
        raise ValueError("линия заглавных совпадает с базовой линией")
    scale = CAP / span

    out = []
    for s in strokes:
        ns = []
        for (x, y) in s:
            ny = (baseline_y - y) * scale if y_down else (y - baseline_y) * scale
            ns.append((x * scale, ny))
        if len(ns) > 1:
            out.append(ns)
    if not out:
        return (min_advance, [])

    xs = [p[0] for s in out for p in s]
    x0 = min(xs)
    bearing = side_bearing * CAP
    out = [[(x - x0 + bearing, y) for (x, y) in s] for s in out]
    width = max(xs) - x0
    advance = max(min_advance, width + 2 * bearing)
    return (advance, out)


def resample(stroke, step):
    """Равномерно пересемплировать полилинию с шагом step (единицы шрифта)."""
    if len(stroke) < 2 or step <= 0:
        return list(stroke)
    out = [stroke[0]]
    prev = stroke[0]
    acc = 0.0
    for cur in stroke[1:]:
        seg = math.dist(prev, cur)
        if seg < 1e-12:
            continue
        while acc + seg >= step:
            t = (step - acc) / seg
            prev = (prev[0] + (cur[0] - prev[0]) * t,
                    prev[1] + (cur[1] - prev[1]) * t)
            out.append(prev)
            seg = math.dist(prev, cur)
            acc = 0.0
            if seg < 1e-12:
                break
        acc += seg
        prev = cur
    if math.dist(out[-1], stroke[-1]) > 1e-9:
        out.append(stroke[-1])
    return out


def order_strokes(strokes):
    """
    Упорядочить штрихи внутри глифа так, чтобы перо меньше прыгало:
    жадный поиск ближайшего конца, с разрешением рисовать штрих задом наперёд.
    """
    if len(strokes) < 2:
        return list(strokes)
    remaining = list(strokes)
    # начинаем с самого верхнего-левого штриха — так пишет рука
    remaining.sort(key=lambda s: (-max(p[1] for p in s), min(p[0] for p in s)))
    out = [remaining.pop(0)]
    while remaining:
        cur = out[-1][-1]
        best_i, best_d, best_rev = 0, float("inf"), False
        for i, s in enumerate(remaining):
            d0 = math.dist(cur, s[0])
            d1 = math.dist(cur, s[-1])
            if d0 < best_d:
                best_i, best_d, best_rev = i, d0, False
            if d1 < best_d:
                best_i, best_d, best_rev = i, d1, True
        s = remaining.pop(best_i)
        out.append(s[::-1] if best_rev else s)
    return out


# ------------------------------------------------------------------- шрифт

class FontSet:
    """
    Набор начертаний: символ -> список вариантов Glyph.

    Если для символа нет пользовательских вариантов, берётся встроенный
    векторный глиф — так текст напечатается всегда, даже если вы оцифровали
    только часть алфавита.
    """

    def __init__(self, name="Мой почерк", flat=None):
        self.name = name
        # чем меньше, тем мельче дробятся кривые встроенного шрифта
        self.flat = FV.parse_path.__defaults__[0] if flat is None else flat
        self.variants = {}          # char -> [Glyph, ...]
        self.builtin_fallback = True

    # -------------------------------------------------------- наполнение
    def add(self, glyph, replace_index=None):
        lst = self.variants.setdefault(glyph.char, [])
        if replace_index is not None and 0 <= replace_index < len(lst):
            lst[replace_index] = glyph
        else:
            lst.append(glyph)
        return len(lst) - 1

    def remove(self, char, index):
        lst = self.variants.get(char)
        if lst and 0 <= index < len(lst):
            lst.pop(index)
            if not lst:
                self.variants.pop(char, None)
            return True
        return False

    def clear(self, char=None):
        if char is None:
            self.variants.clear()
        else:
            self.variants.pop(char, None)

    # -------------------------------------------------------- получение
    def _builtin(self, ch):
        adv, polys = FV.get_glyph(ch, self.flat)
        return Glyph(ch, adv, [list(p) for p in polys], "builtin")

    def has_user(self, ch):
        return bool(self.variants.get(ch))

    def count(self, ch):
        return len(self.variants.get(ch, ()))

    def get_variants(self, ch):
        """Все доступные варианты символа (пользовательские или встроенный)."""
        lst = self.variants.get(ch)
        if lst:
            return lst
        if self.builtin_fallback:
            return [self._builtin(ch)]
        return []

    def pick(self, ch, rng=None, mix=1.0, last_index=None, avoid_repeat=True):
        """
        Выбрать вариант начертания.
        mix: 0 — всегда первый вариант, 1 — равновероятно любой.
        Возвращает (Glyph, index).
        """
        lst = self.get_variants(ch)
        if not lst:
            # запасной шрифт выключен, а своего начертания нет: оставляем
            # пустое место той же ширины — сразу видно, чего не хватает
            b = self._builtin(ch)
            return (Glyph(ch, b.advance, [], "missing"), 0)
        if len(lst) == 1 or rng is None or mix <= 0 or rng.random() > mix:
            return (lst[0], 0)
        idxs = list(range(len(lst)))
        if avoid_repeat and last_index is not None:
            alt = [i for i in idxs if i != last_index]
            if alt:
                idxs = alt
        i = rng.choice(idxs)
        return (lst[i], i)

    def coverage(self, chars):
        """Сколько символов из набора оцифровано. -> (готово, всего, список пустых)."""
        missing = [c for c in chars if not self.has_user(c)]
        return (len(chars) - len(missing), len(chars), missing)

    # -------------------------------------------------------------- файл
    def to_dict(self):
        return {
            "format": "hw-font/1",
            "name": self.name,
            "cap": CAP,
            "xheight": XH,
            "glyphs": [g.to_dict() for lst in self.variants.values() for g in lst],
        }

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, separators=(",", ":"))

    # встроенный каллиграфический шрифт: вместо пути к файлу — эта метка
    CALLIG = "builtin:callig"

    @classmethod
    def calligraphic(cls):
        """Встроенная «Пропись»: буквы дореволюционной азбуки, осевые линии."""
        from .font_callig import DATA
        fs = cls(name="Каллиграфия (пропись)")
        for lst in DATA.values():
            for gd in lst:
                fs.add(Glyph.from_dict(gd))
        return fs

    @classmethod
    def load(cls, path):
        if path == cls.CALLIG:
            return cls.calligraphic()
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        fs = cls(name=d.get("name", "Мой почерк"))
        for gd in d.get("glyphs", []):
            fs.add(Glyph.from_dict(gd))
        return fs

    def __repr__(self):
        n = sum(len(v) for v in self.variants.values())
        return "<FontSet %r: %d символов, %d вариантов>" % (
            self.name, len(self.variants), n)
