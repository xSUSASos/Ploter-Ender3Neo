# -*- coding: utf-8 -*-
"""
Почерк со свободного листа (эксперимент).

Пропись не нужна: пишете что угодно на любом листе — белом, в клетку, в
линейку, — фотографируете и вписываете в программу, ЧТО там написано,
строка в строку. Дальше программа:

    1. находит чернила и убирает клетку/линейку — на линиях чернилами
       считается только то, что заметно темнее самой линии;
    2. делит лист на строки (по плотности чернил) и сверяет их число
       с числом строк текста;
    3. в строке находит базовую линию, линию строчных и наклон письма;
    4. делит строку на слова по самым широким просветам;
    5. режет слово на буквы. Раздельные буквы — по просветам, слитное
       письмо — по самым «тонким» местам вдоль наклона, рядом с тем
       местом, где букву ждёт ширина её встроенного образца;
    6. строит осевую линию всей строки и раскладывает её по буквам.

Разрезы, края слов, базовую линию и линию строчных можно двигать руками
(окно «Свободный лист»): буквы пересобираются сразу, без повторной
векторизации — осевые линии строки считаются один раз.

Координаты: «рабочая» картинка (фото, приведённое к удобному масштабу),
пиксели, ось Y вниз. u = x − (base − y)·slant — «выпрямленный» X: вдоль
наклона письма он постоянен, на базовой линии совпадает с x.
"""

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.signal import find_peaks

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

from PIL import Image, ImageOps

from . import font_vector as FV
from . import vectorize as VZ
from .glyphset import CAP, XH, Glyph, order_strokes

# на такую высоту строчных (px) приводится фото: при ней векторизация
# прописи отлажена лучше всего
TARGET_XH = 40.0


@dataclass
class FreeLine:
    text: str = ""
    ids: list = field(default_factory=list)      # номера пятен чернил строки
    box: tuple = (0, 0, 0, 0)                    # x0, y0, x1, y1 (рабочие px)
    base: float = 0.0                            # базовая линия, y
    xh: float = 0.0                              # линия строчных, y
    slant: float = 0.0                           # tg наклона письма
    words: list = field(default_factory=list)    # по слову: границы букв в u
    note: str = ""                               # что пошло не так
    _paths: list = None                          # осевые линии строки (кэш)
    _sw: float = 3.0

    @property
    def text_words(self):
        return self.text.split()

    def u(self, x, y):
        return x - (self.base - y) * self.slant

    def x_at(self, u, y):
        """Точка разреза u на высоте y."""
        return u + (self.base - y) * self.slant


def _load(img):
    if isinstance(img, str):
        img = Image.open(img)
    try:
        img = ImageOps.exif_transpose(img)      # фото с телефона «лежит на боку»
    except Exception:                           # noqa: BLE001
        pass
    return img.convert("RGB")


def _resize(img, factor):
    w, h = img.size
    return img.resize((max(1, int(w * factor)), max(1, int(h * factor))),
                      Image.LANCZOS if factor < 1 else Image.BICUBIC)


def remove_ruling(mask, norm, min_len):
    """
    Убрать клетку и линейку тетради. Длинные прямые (по горизонтали и по
    вертикали) находятся морфологическим открытием, и на них чернилами
    остаётся только то, что заметно темнее самой линии — там, где ручка
    пересекает клетку, штрих не рвётся.
    """
    if cv2 is None or not mask.any():
        return mask
    m = mask.astype(np.uint8)
    L = max(9, int(min_len))
    h = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    v = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    lines = cv2.dilate(h | v, np.ones((3, 3), np.uint8)) > 0
    if not lines.any():
        return mask
    line_level = float(np.median(norm[lines & mask]))
    out = mask.copy()
    out[lines] = norm[lines] < 0.72 * line_level
    return out


class FreeSheet:
    """
    Разбор одного фото. Порядок работы:

        fs = FreeSheet(photo, ink_level=0.68, ruled=True)
        fs.set_text("строка один\\nстрока два")     # раскладка по строкам
        for ch, glyph, where in fs.glyphs():       # буквы
            ...
    """

    def __init__(self, img, ink_level=0.68, ruled=True, max_side=5200):
        src = _load(img)
        self.ink_level = float(ink_level)
        self.ruled = bool(ruled)
        # 1) прикидка на уменьшенной копии: какой высоты буквы
        probe_f = min(1.0, 1800.0 / max(src.size))
        probe = _resize(src, probe_f) if probe_f < 1 else src
        _m, _lab, _n, hm = self._ink(probe, first=True)
        # 2) рабочий масштаб: строчные ~TARGET_XH px
        f = probe_f * (TARGET_XH * 1.25 / max(hm, 4.0))
        f = min(f, max_side / max(src.size), 3.0)
        self.factor = f
        self.image = _resize(src, f) if abs(f - 1) > 1e-3 else src
        self.mask, self.labels, self.n, self.h_med = self._ink(self.image)
        self.lines = []
        self.warnings = []
        self._find_rows()

    # ------------------------------------------------------ чернила
    def _ink(self, img, first=False):
        gray = VZ.to_gray(img)
        norm = VZ.flatten_illumination(gray, lift=31 if not first else 15)
        mask = norm < self.ink_level
        # толщина пера и размер букв нужны раньше, чем убрана клетка
        hm0 = self._letter_h(mask)
        if self.ruled:
            mask = remove_ruling(mask, norm, 1.6 * hm0)
        mask = VZ.clean_mask(mask, min_area=max(6, int(0.004 * hm0 * hm0)),
                             norm=norm, ink_level=self.ink_level)
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), int))
        keep = self._filter(lab, n, mask.shape, hm0)
        mask = keep[lab]
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), int))
        hm = self._letter_h(mask)
        if not first:
            self.norm = norm
        return mask, lab, n, hm

    @staticmethod
    def _letter_h(mask):
        """Типичная высота пятна чернил — по ней судим о размере букв."""
        lab, n = ndimage.label(mask, structure=np.ones((3, 3), int))
        if not n:
            return 20.0
        sl = ndimage.find_objects(lab)
        hs = np.array([s[0].stop - s[0].start for s in sl], float)
        ws = np.array([s[1].stop - s[1].start for s in sl], float)
        area = np.bincount(lab.ravel())[1:].astype(float)
        big = (hs >= 6) & (hs < mask.shape[0] * 0.25) & (ws < mask.shape[1] * 0.5)
        if not big.any():
            return 20.0
        # медиана, взвешенная по площади: шумовых крошек на фото сотни,
        # и простая медиана давала «буквы высотой 7 px»
        hs, area = hs[big], area[big]
        o = np.argsort(hs)
        c = np.cumsum(area[o])
        return float(hs[o][np.searchsorted(c, 0.5 * c[-1])])

    @staticmethod
    def _filter(lab, n, shape, hm):
        """Выбросить края стола, тени у края листа и поля тетради."""
        keep = np.zeros(n + 1, bool)
        H, W = shape
        for i, s in enumerate(ndimage.find_objects(lab), start=1):
            if s is None:
                continue
            h = s[0].stop - s[0].start
            w = s[1].stop - s[1].start
            if s[0].start == 0 or s[1].start == 0 or s[0].stop == H or s[1].stop == W:
                continue                          # стол, тень, край листа, обрывки клетки
            if h > 4.5 * hm and w < 0.6 * hm:
                continue                          # вертикальная черта полей
            if w > 0.8 * W:
                continue
            keep[i] = True
        return keep

    # ------------------------------------------------------ строки
    def _find_rows(self):
        prof = self.mask.sum(axis=1).astype(float)
        self.profile = ndimage.gaussian_filter1d(prof, max(1.0, 0.22 * self.h_med))
        pk, _props = find_peaks(self.profile, distance=max(3, int(0.75 * self.h_med)),
                                prominence=0.08 * max(self.profile.max(), 1e-9))
        self.peaks = [int(p) for p in pk]

    def _assign(self, peaks):
        """Пятна чернил -> строки: по медиане строк пятна, к ближайшему пику."""
        rows = {}
        if not peaks:
            return rows
        P = np.asarray(peaks, float)
        objs = ndimage.find_objects(self.labels)
        ys = np.indices(self.mask.shape)[0]
        medians = ndimage.median(ys, self.labels, index=np.arange(1, self.n + 1))
        for i, (s, m) in enumerate(zip(objs, np.atleast_1d(medians)), start=1):
            if s is None:
                continue
            j = int(np.argmin(np.abs(P - m)))
            rows.setdefault(j, []).append(i)
        return rows

    def set_text(self, text):
        """Разложить строки текста по строкам на фото и порезать на буквы."""
        want = [t for t in text.replace("\r", "").split("\n") if t.strip()]
        self.warnings = []
        peaks = list(self.peaks)
        if len(peaks) > len(want) and want:
            # слабые пики — точки, подчёркивания, шум: не строки
            med = float(np.median([self.profile[p] for p in peaks]))
            strong = [p for p in peaks if self.profile[p] >= 0.25 * med]
            if len(strong) >= len(want):
                peaks = strong
        rows = self._assign(peaks)
        if len(peaks) > len(want) and want:
            # переписали не весь лист — берём первые строки сверху
            self.warnings.append(
                "на фото строк %d, в тексте %d — взяты первые %d сверху"
                % (len(peaks), len(want), len(want)))
        elif len(peaks) < len(want):
            self.warnings.append(
                "на фото найдено строк: %d, в тексте: %d — лишние строки текста "
                "пропущены, сверьте текст" % (len(peaks), len(want)))
        self.lines = []
        for j, t in enumerate(want[:len(peaks)]):
            ids = rows.get(j, [])
            if not ids:
                continue
            ln = FreeLine(text=t, ids=ids)
            self._measure(ln)
            self.auto_cut(ln)
            self.lines.append(ln)
        return self.lines

    def line_mask(self, ln):
        x0, y0, x1, y1 = ln.box
        return np.isin(self.labels[y0:y1, x0:x1], ln.ids)

    def _drop_strays(self, ln):
        """
        Выбросить из строки одинокие крошки вдали от текста: обрывок клетки
        или пятнышко у поля растягивали строку, и самым широким «пробелом»
        оказывался просвет до крошки, а не между словами.
        """
        objs = ndimage.find_objects(self.labels)
        area = ndimage.sum(self.mask, self.labels, ln.ids)
        big = max(area) if len(area) else 0
        keep = []
        for i, a in zip(ln.ids, area):
            s = objs[i - 1]
            if a >= 0.15 * big:
                keep.append(i)
                continue
            near = min((max(0, max(objs[j - 1][1].start - s[1].stop,
                                   s[1].start - objs[j - 1][1].stop))
                        for j in ln.ids if j != i), default=0)
            if near <= 1.5 * self.h_med:
                keep.append(i)
        ln.ids = keep or ln.ids

    def _measure(self, ln):
        self._drop_strays(ln)
        objs = ndimage.find_objects(self.labels)
        bx = [objs[i - 1] for i in ln.ids if objs[i - 1] is not None]
        y0 = min(s[0].start for s in bx)
        y1 = max(s[0].stop for s in bx)
        x0 = min(s[1].start for s in bx)
        x1 = max(s[1].stop for s in bx)
        ln.box = (x0, y0, x1, y1)
        m = self.line_mask(ln)
        ln._sw = VZ.stroke_width(m)
        # зона строчных: где чернил гуще всего
        p = ndimage.gaussian_filter1d(m.sum(axis=1).astype(float), max(1.0, 0.08 * self.h_med))
        r = int(p.argmax())
        lvl = 0.38 * p[r]
        top = r
        while top > 0 and p[top - 1] >= lvl:
            top -= 1
        bot = r
        while bot < len(p) - 1 and p[bot + 1] >= lvl:
            bot += 1
        if bot - top < 0.35 * self.h_med:               # одни заглавные/цифры
            top = max(0, int(bot - 0.6 * self.h_med))
        ln.base = float(y0 + bot)
        ln.xh = float(y0 + top)
        ln.slant = self._slant(self.paths(ln))

    @staticmethod
    def _slant(paths):
        """
        Наклон письма — по крутым отрезкам осевой линии (нисходящие штрихи).
        Пологие (соединения, перекладины, дуги) не в счёт: в слитном
        письме соединения идут наискось и завышали наклон вдвое.
        """
        ts, ws = [], []
        for p in paths:
            for (xa, ya), (xb, yb) in zip(p, p[1:]):
                dy = abs(yb - ya)
                if dy < 1e-6:
                    continue
                t = (xb - xa) / (ya - yb)       # >0 — верх правее низа
                if abs(t) > 0.9:                # положе ~42° от вертикали
                    continue
                ts.append(t)
                ws.append(dy)
        if not ts:
            return 0.0
        o = np.argsort(ts)
        c = np.cumsum(np.asarray(ws)[o])
        return float(np.asarray(ts)[o][np.searchsorted(c, 0.5 * c[-1])])

    # ------------------------------------------------------ разрезы
    def _columns(self, ln):
        """Сколько чернил в каждом столбце u (выпрямленном по наклону)."""
        m = self.line_mask(ln)
        ys, xs = np.nonzero(m)
        x0, y0 = ln.box[0], ln.box[1]
        u = (xs + x0) - (ln.base - (ys + y0)) * ln.slant
        lo = int(math.floor(u.min())) if len(u) else 0
        hist = np.bincount((u - lo).astype(int)) if len(u) else np.zeros(1)
        return lo, hist.astype(float)

    def auto_cut(self, ln):
        """Слова по самым широким просветам, буквы — по самым тонким местам."""
        words = ln.text_words
        lo, hist = self._columns(ln)
        if not words or not hist.any():
            ln.words = []
            return
        occ = hist > 0
        nz = np.nonzero(occ)[0]
        a, b = int(nz[0]), int(nz[-1]) + 1
        gaps = []
        i = a
        while i < b:
            if not occ[i]:
                j = i
                while j < b and not occ[j]:
                    j += 1
                gaps.append((j - i, i, j))
                i = j
            else:
                i += 1
        need = len(words) - 1
        if len(gaps) >= need:
            sep = sorted(sorted(gaps, reverse=True)[:need], key=lambda g: g[1])
            spans, s0 = [], a
            for _w, gi, gj in sep:
                spans.append((s0, gi))
                s0 = gj
            spans.append((s0, b))
        else:
            ln.note = "слова не разделились по просветам — поставил по ширине"
            wts = [self._wid(w) for w in words]
            tot = sum(wts) + 0.6 * need * self._wid(" ")
            spans, pos = [], float(a)
            for k, w in enumerate(wts):
                wlen = (b - a) * w / tot
                spans.append((int(pos), int(pos + wlen)))
                pos += wlen + (b - a) * 0.6 * self._wid(" ") / tot
        sw = max(1.0, ln._sw)
        ln.words = []
        for w, (s, e) in zip(words, spans):
            # края слова по чернилам: выбросим пустые края промежутка
            seg = np.nonzero(occ[s:e])[0]
            if len(seg):
                s, e = s + int(seg[0]), s + int(seg[-1]) + 1
            cuts = self._cut_word(hist, s, e, w, sw)
            ln.words.append([float(lo + c) for c in [s] + cuts + [e]])

    @staticmethod
    def _wid(ch):
        return sum(FV.get_glyph(c)[0] for c in ch) or 1.0

    def _cut_word(self, hist, s, e, word, sw, lam=2.2):
        n = len(word)
        if n < 2:
            return []
        wts = [FV.get_glyph(c)[0] for c in word]
        tot = sum(wts)
        exp, acc = [], 0.0
        for wt in wts[:-1]:
            acc += wt
            exp.append(s + (e - s) * acc / tot)
        wl = (e - s) / n
        win = max(2, int(0.45 * wl))
        minw = max(1, int(0.25 * wl))
        # цена разреза — сколько чернил он пересекает. Настоящий просвет
        # (раздельные буквы) — лучший разрез, даже узкий; сглаженная добавка
        # тянет разрез к середине просвета или тонкого соединения
        cost = (hist / sw + 0.3 * ndimage.gaussian_filter1d(hist, max(1.0, sw)) / sw
                - 0.8 * (hist == 0))
        cand = [list(range(max(s + 1, int(c - win)), min(e - 1, int(c + win)) + 1)) or [int(c)]
                for c in exp]
        # динамика: C[k][c] = лучшая цена разрезов 0..k при k-м разрезе в c
        C, back = [], []
        for k, cs in enumerate(cand):
            row, br = [], []
            for c in cs:
                own = cost[min(c, len(cost) - 1)] + lam * ((c - exp[k]) / wl) ** 2
                if k == 0:
                    row.append(own)
                    br.append(-1)
                    continue
                best, bi = math.inf, -1
                for pi, pc in enumerate(cand[k - 1]):
                    if c - pc >= minw and C[k - 1][pi] < best:
                        best, bi = C[k - 1][pi], pi
                if bi < 0:                          # тесно — берём ближайший
                    bi = int(np.argmin(C[k - 1]))
                    best = C[k - 1][bi] + 5.0
                row.append(own + best)
                br.append(bi)
            C.append(row)
            back.append(br)
        k = len(cand) - 1
        i = int(np.argmin(C[k]))
        out = []
        while k >= 0:
            out.append(cand[k][i])
            i = back[k][i]
            k -= 1
        out = out[::-1]
        for j in range(1, len(out)):                # строго по возрастанию
            out[j] = max(out[j], out[j - 1] + 1)
        return out

    # ------------------------------------------------------ буквы
    def paths(self, ln):
        """Осевые линии строки (рабочие px) — считаются один раз."""
        if ln._paths is None:
            m = self.line_mask(ln)
            ps = VZ.vectorize_mask(m, **{k: VZ.DEFAULTS[k] for k in
                                         ("min_branch", "smooth", "simplify_tol",
                                          "merge_gap", "min_path_len")})
            x0, y0 = ln.box[0], ln.box[1]
            ln._paths = [[(x + x0, y + y0) for (x, y) in p] for p in ps]
        return ln._paths

    def letter_strokes(self, ln, lo, hi):
        """Куски осевых линий строки между разрезами lo и hi (по u)."""
        out = []
        for p in self.paths(ln):
            out.extend(_clip(p, ln, lo, hi))
        keep = 0.1 * max(ln.base - ln.xh, 4.0)
        return [s for s in out if len(s) > 1 and _plen(s) >= keep
                or (len(s) > 1 and _is_dot(s, ln))]

    def glyphs(self, lines=None):
        """-> [(символ, Glyph, (строка, слово, буква))]."""
        res = []
        for li, ln in enumerate(self.lines):
            if lines is not None and li not in lines:
                continue
            for wi, (word, bnd) in enumerate(zip(ln.text_words, ln.words)):
                for k, ch in enumerate(word):
                    if k + 1 >= len(bnd):
                        break
                    g = self.make_glyph(ln, ch, bnd[k], bnd[k + 1],
                                        first=(k == 0), last=(k == len(word) - 1))
                    if g is not None:
                        res.append((ch, g, (li, wi, k)))
        return res

    def make_glyph(self, ln, ch, lo, hi, first=True, last=True):
        ss = self.letter_strokes(ln, lo, hi)
        if not ss:
            return None
        xh_px = max(ln.base - ln.xh, 4.0)
        sc = XH / xh_px
        bear = 0.08 * CAP
        left = bear if first else 0.0
        conv = lambda p: ((p[0] - lo) * sc + left, (ln.base - p[1]) * sc)
        strokes = [[conv(p) for p in s] for s in ss]
        adv = (hi - lo) * sc + left + (bear if last else 0.0)
        g = Glyph(ch, max(adv, 2.0), order_strokes(strokes), source="user",
                  note="свободный лист")
        # в слитном письме буква была соединена с соседями там, где штрих
        # пересекает линию разреза, — там же ставим вход и выход
        tol = 0.6
        ends = [p for s in ss for p in (s[0], s[-1])]
        on_lo = [p for p in ends if abs(ln.u(*p) - lo) < tol]
        on_hi = [p for p in ends if abs(ln.u(*p) - hi) < tol]
        low = lambda p: abs((ln.base - p[1]) - 0.3 * xh_px)   # ближе к низу строчных
        if on_lo and not first:
            g.entry = tuple(round(v, 2) for v in conv(min(on_lo, key=low)))
        if on_hi and not last:
            g.exit = tuple(round(v, 2) for v in conv(min(on_hi, key=low)))
        return g

    # ------------------------------------------------------ правка руками
    def move_boundary(self, ln, wi, k, u):
        """Сдвинуть k-ю границу слова wi (0 и последняя — края слова)."""
        b = ln.words[wi]
        lo = b[k - 1] + 1 if k > 0 else (ln.words[wi - 1][-1] + 1 if wi > 0 else -1e9)
        hi = b[k + 1] - 1 if k + 1 < len(b) else (ln.words[wi + 1][0] - 1
                                                   if wi + 1 < len(ln.words) else 1e9)
        b[k] = float(min(max(u, lo), hi))
        return b[k]


def _plen(s):
    return sum(math.dist(s[i], s[i + 1]) for i in range(len(s) - 1))


def _is_dot(s, ln):
    """Точка над ё/й/i: маленькая замкнутая петелька выше строчных."""
    xs = [p[0] for p in s]
    ys = [p[1] for p in s]
    size = max(max(xs) - min(xs), max(ys) - min(ys))
    return size < 0.3 * max(ln.base - ln.xh, 4.0) and min(ys) < ln.xh + 0.2 * (ln.base - ln.xh)


def _clip(path, ln, lo, hi):
    """Часть полилинии, у которой lo ≤ u < hi; концы — на линиях разреза."""
    out, cur = [], []
    us = [ln.u(x, y) for (x, y) in path]
    inside = lambda v: lo <= v < hi
    for i, (p, v) in enumerate(zip(path, us)):
        if i:
            q, w = path[i - 1], us[i - 1]
            for edge in (lo, hi):
                if (w - edge) * (v - edge) < 0:
                    t = (edge - w) / (v - w)
                    cp = (q[0] + (p[0] - q[0]) * t, q[1] + (p[1] - q[1]) * t)
                    if cur:
                        cur.append(cp)
                        out.append(cur)
                        cur = []
                    else:
                        cur = [cp]
        if inside(v):
            cur.append(p)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [s for s in out if len(s) > 1]
