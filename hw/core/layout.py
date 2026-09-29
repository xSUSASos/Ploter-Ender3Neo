# -*- coding: utf-8 -*-
"""
Раскладка текста по листу.

Здесь текст превращается в набор штрихов в миллиметрах в системе координат
ЛИСТА: начало в левом нижнем углу, X вправо, Y вверх. Привязка листа к столу
принтера и поворот делаются позже, в gcode.py, — так одну и ту же раскладку
можно и показать в превью, и напечатать, и положить на стол как угодно.

Умеет:
  * перенос по словам (и по буквам, если слово длиннее строки);
  * выравнивание влево / по центру / вправо / по ширине;
  * абзацы, красную строку, отбивку между абзацами;
  * автоподбор размера, чтобы текст поместился на страницу;
  * разлив на несколько страниц;
  * описки с зачёркиванием и кляксы;
  * разлиновку «под тетрадь»;
  * таблицы: строки вида «| a | b |» (как в Markdown) или ячейки через
    табуляцию (так их копируют из Word и Excel) — рамка рисуется пером;
  * формулы LaTeX: $...$ в строке, $$...$$ отдельной строкой по центру.
"""

import math
import re
import unicodedata
from dataclasses import dataclass, field

from .glyphset import CAP
from . import humanize as HM
from . import joins as JN
from . import mathtex as MT


@dataclass
class Word:
    text: str
    struck: bool = False        # это зачёркиваемая (ошибочная) копия
    width: float = 0.0          # мм, номинальная
    # слово с формулой: [("t", текст) | ("b", Box формулы)], иначе None
    parts: list = None
    hi: float = 0.0             # выше базовой линии, единицы шрифта (формулы)
    lo: float = 0.0             # ниже базовой линии


@dataclass
class Line:
    words: list = field(default_factory=list)
    gaps: list = field(default_factory=list)   # ширина пробелов, мм
    indent: float = 0.0
    last_of_par: bool = False
    width: float = 0.0
    gap_before: float = 0.0   # доп. отбивка сверху (между абзацами), мм
    y: float = 0.0            # базовая линия, мм вниз от верха текстового блока
    # строка таблицы: cells — [(смещение_мм, ширина_мм, [Word], по_центру)],
    # table — общая для всех строк одной таблицы, row — номер ряда
    cells: list = None
    table: object = None
    row: int = 0
    center: bool = False      # формула $$…$$ — по центру строки
    height_mm: float = 0.0    # формула выше обычной строки: сколько над базовой
    depth_mm: float = 0.0     # ...и под ней


@dataclass
class Table:
    """Геометрия таблицы, общая для всех её строк (мм)."""
    x0: float                 # левый край рамки на листе
    cols: list                # ширины столбцов
    pad: float                # поле ячейки слева и справа
    up: float                 # от базовой линии первой строки ряда до верхней черты
    down: float               # от базовой линии последней строки ряда до нижней
    row_gap: float            # добавка к межстрочному между рядами
    top_extra: float          # сдвиг вниз, если ряд начинает страницу
    margin: float = 0.0       # отбивка до и после таблицы
    shift: float = 0.0        # текст ячеек ниже базовой линии строки, мм


@dataclass
class Page:
    strokes: list = field(default_factory=list)     # [[(x, y), ...], ...] мм
    guides: list = field(default_factory=list)      # разлиновка, отдельно
    n_lines: int = 0
    size_mm: float = 0.0
    art: list = field(default_factory=list)         # рисунок, мм листа
    pen_mm: float = 0.0     # толщина линии стержня для превью; 0 — от размера

    def bbox(self):
        pts = [p for s in list(self.strokes) + list(self.art) for p in s]
        if not pts:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def ink_length(self):
        return sum(math.dist(s[i], s[i + 1])
                   for s in list(self.strokes) + list(self.art)
                   for i in range(len(s) - 1))


# ----------------------------------------------------------------- метрики

def glyph_advance(fontset, ch, size_mm, tracking_mm):
    """Номинальная ширина символа в мм."""
    # pick() подставит встроенный глиф, если своего начертания нет, а
    # запасной шрифт выключен; get_variants()[0] в этом случае падал
    g = fontset.pick(ch)[0]
    return g.advance * size_mm / CAP + tracking_mm


def word_width(fontset, word, size_mm, tracking_mm):
    return sum(glyph_advance(fontset, c, size_mm, tracking_mm) for c in word)


def space_width(fontset, size_mm, cfg):
    return glyph_advance(fontset, " ", size_mm, 0.0) * cfg.word_spacing


# ------------------------------------------------------- разбор и ошибки

# невидимые символы, которые приходят с текстом из Word и из браузера:
# мягкий перенос, пробелы нулевой ширины, BOM, знаки ударения
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"
                                    "́̀"), None)


def clean_text(text):
    """
    Привести текст к виду, который шрифт понимает.

    «ё» и «й» в скопированном тексте часто записаны двумя кодами:
    буква плюс отдельные точки/кратка. Шрифт такой пары не знает и
    печатал квадратик — NFC склеивает их обратно в одну букву.
    Мягкий перенос раньше печатался дефисом посреди слова.
    """
    return unicodedata.normalize("NFC", text).translate(_INVISIBLE)


# разделитель шапки в Markdown: |---|:---:|
_MD_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _pipe_row(raw):
    s = raw.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_tab_row(raw):
    # табуляция внутри текста, а не только отступ в начале абзаца
    return "\t" in raw.strip()


def split_blocks(text):
    """
    Текст -> [("par", [слова]) | ("table", [[ячейка, ...], ...])].

    Таблица — подряд идущие строки, начинающиеся с «|», или хотя бы две
    строки подряд с табуляцией между ячейками.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(raw)
    while i < n:
        ln = raw[i]
        if ln.strip().startswith("|"):
            rows = []
            while i < n and raw[i].strip().startswith("|"):
                if not _MD_SEP.match(raw[i]):
                    rows.append(_pipe_row(raw[i]))
                i += 1
        elif _is_tab_row(ln) and i + 1 < n and _is_tab_row(raw[i + 1]):
            rows = []
            while i < n and _is_tab_row(raw[i]):
                rows.append([c.strip() for c in raw[i].strip(" ").split("\t")])
                i += 1
        else:
            out.append(("par", _split_words(ln)))
            i += 1
            continue
        if rows:
            ncol = max(len(r) for r in rows)
            out.append(("table", [r + [""] * (ncol - len(r)) for r in rows]))
    return out


def _split_words(raw):
    """Строка -> слова; слово с формулой — список частей (см. mathtex.tokens)."""
    if ("$" in raw or "\\(" in raw or "\\[" in raw) and MT.has_math(raw):
        out = []
        for parts in MT.tokens(raw):
            if any(pt[0] == "m" for pt in parts):
                out.append(parts)
            else:
                out.append("".join(pt[1] for pt in parts))
        return out
    return raw.split()


def _make_word(item, metrics):
    """Строка или части с формулой -> Word."""
    if isinstance(item, str):
        return Word(item)
    parts, text, hi, lo = [], "", 0.0, 0.0
    for pt in item:
        if pt[0] == "t":
            parts.append(pt)
            text += pt[1]
            hi = max(hi, CAP)
        else:
            box = MT.layout(pt[1], metrics, display=pt[2])
            parts.append(("b", box))
            text += "$" + pt[1] + "$"
            hi, lo = max(hi, box.h), max(lo, box.d)
    return Word(text, parts=parts, hi=hi, lo=lo)


def _wwidth(fontset, w, size_mm, cfg):
    if not w.parts:
        return word_width(fontset, w.text, size_mm, cfg.tracking_mm)
    k = size_mm / CAP
    return sum(word_width(fontset, pt[1], size_mm, cfg.tracking_mm) if pt[0] == "t"
               else pt[1].w * k for pt in w.parts)


def split_paragraphs(text):
    """Текст -> список абзацев, абзац -> список слов."""
    pars = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        pars.append(raw.split())
    return pars


def apply_errors(words, human, metrics=None):
    """
    Вставить описки и зачёркивания.

    Слово, попавшее под зачёркивание, пишется дважды: сначала «неудачная»
    копия (её потом перечеркнут), затем правильная. Текст остаётся читаемым.
    """
    cfg = human.cfg
    rng = human.rng
    out = []
    strike_p = cfg.strike_rate if cfg.enabled else 0.0
    typo_p = cfg.typo_rate if cfg.enabled else 0.0
    # описка без зачёркивания испортила бы текст, поэтому она всегда
    # сопровождается исправлением
    p = max(strike_p, typo_p)
    for w in words:
        if not isinstance(w, str):
            out.append(_make_word(w, metrics))
            continue
        if len(w) > 2 and p > 0 and rng.random() < p:
            bad = HM.make_typo(w, rng) if (typo_p > 0 and rng.random() <
                                           (typo_p / p if p else 0.0)) else w
            out.append(Word(bad, struck=True))
        out.append(Word(w))
    return out


# ------------------------------------------------------------- перенос

def wrap(words, fontset, cfg, size_mm, human, first_indent):
    """Слова одного абзаца -> список Line."""
    maxw = cfg.text_w
    sp = space_width(fontset, size_mm, cfg)
    lines = []
    cur = Line(indent=first_indent)
    cur.width = first_indent

    for w in words:
        w.width = _wwidth(fontset, w, size_mm, cfg)
        gap = sp * human.space_factor() if cur.words else 0.0
        need = cur.width + gap + w.width

        if cur.words and need > maxw + 1e-6:
            lines.append(cur)
            cur = Line()
            gap = 0.0
            need = w.width

        # слово длиннее всей строки — режем по буквам
        if not cur.words and w.width > maxw and not w.parts:
            for piece in _split_long(w, fontset, cfg, size_mm, maxw):
                if cur.words:
                    lines.append(cur)
                    cur = Line()
                cur.words.append(piece)
                cur.gaps.append(0.0)
                cur.width = piece.width
            continue

        if cur.words:
            cur.gaps.append(gap)
        else:
            cur.gaps.append(0.0)
        cur.words.append(w)
        cur.width = need

    if cur.words:
        lines.append(cur)
    if lines:
        lines[-1].last_of_par = True
    return lines


def _split_long(w, fontset, cfg, size_mm, maxw):
    """Разрезать слишком длинное слово по буквам, с переносом-дефисом."""
    hyph = glyph_advance(fontset, "-", size_mm, cfg.tracking_mm)
    pieces, buf, bw = [], "", 0.0
    for ch in w.text:
        cw = glyph_advance(fontset, ch, size_mm, cfg.tracking_mm)
        if buf and bw + cw + hyph > maxw:
            pieces.append(Word(buf + "-", w.struck,
                               bw + hyph))
            buf, bw = "", 0.0
        buf += ch
        bw += cw
    if buf:
        pieces.append(Word(buf, w.struck, bw))
    return pieces


def measure(text, fontset, cfg, human, size_mm):
    """Текст -> список Line с проставленными отбивками между абзацами."""
    all_lines = []
    # красная строка при выравнивании по центру или вправо только сбивала бы
    # строку вбок, поэтому применяем её лишь к левому краю и ширине
    indent = cfg.first_line_indent if cfg.align in ("left", "justify") else 0.0
    after_table = 0.0       # место под нижней чертой таблицы
    metrics = MT.metrics_for(fontset)
    for kind, data in split_blocks(text):
        if kind == "table":
            lines = table_lines(data, fontset, cfg, size_mm, metrics)
            if not lines:
                continue
            lines[0].gap_before = (cfg.paragraph_gap if all_lines else 0.0) \
                + lines[0].table.margin + after_table
            after_table = lines[0].table.margin
            all_lines.extend(lines)
            continue
        if not data:
            ln = Line(last_of_par=True)                  # пустая строка
            ln.gap_before, after_table = after_table, 0.0
            all_lines.append(ln)
            continue
        ws = apply_errors(data, human, metrics)
        display = (len(data) == 1 and not isinstance(data[0], str)
                   and len(data[0]) == 1 and data[0][0][2])
        lines = wrap(ws, fontset, cfg, size_mm, human,
                     0.0 if display else indent)
        k = size_mm / CAP
        for ln in lines:
            ln.center = display
            ln.height_mm = max((w.hi for w in ln.words), default=0.0) * k
            ln.depth_mm = max((w.lo for w in ln.words), default=0.0) * k
        if lines and all_lines:
            lines[0].gap_before = cfg.paragraph_gap + after_table
        after_table = 0.0
        all_lines.extend(lines)
    return len(all_lines), all_lines


# ------------------------------------------------------------- таблицы

def _col_widths(natural, minimal, avail):
    """
    Ширины столбцов (без полей), чтобы сумма влезла в avail.
    Узкие столбцы получают сколько просят, остаток делится поровну между
    широкими — так столбец с числами не сжимается из-за длинного текста рядом.
    """
    if sum(natural) <= avail:
        return list(natural)
    out = [None] * len(natural)
    left = list(range(len(natural)))
    rest = avail
    while left:
        share = rest / len(left)
        small = [j for j in left if natural[j] <= share]
        if not small:
            for j in left:
                out[j] = share
            break
        for j in small:
            out[j] = natural[j]
            rest -= natural[j]
        left = [j for j in left if j not in small]
    return [max(o, m) for o, m in zip(out, minimal)]


def _cell_wrap(words, fontset, cfg, size_mm, width, sp):
    """Слова ячейки -> строки [[Word, ...], ...] не шире width."""
    lines, cur, cw = [], [], 0.0
    for w in words:
        w.width = _wwidth(fontset, w, size_mm, cfg)
        pieces = [w] if (w.width <= width + 1e-6 or w.parts) else \
            _split_long(w, fontset, cfg, size_mm, max(width, size_mm))
        for pc in pieces:
            need = cw + (sp if cur else 0.0) + pc.width
            if cur and need > width + 1e-6:
                lines.append(cur)
                cur, cw = [], 0.0
                need = pc.width
            cur.append(pc)
            cw = need
    if cur:
        lines.append(cur)
    return lines


def table_lines(rows, fontset, cfg, size_mm, metrics=None):
    """Ряды таблицы -> строки Line с общей геометрией Table."""
    metrics = metrics or MT.metrics_for(fontset)
    rows = [[[_make_word(t, metrics) for t in _split_words(c)] for c in r]
            for r in rows]
    rows = [r for r in rows if any(r)] or rows
    ncol = max((len(r) for r in rows), default=0)
    if not ncol:
        return []
    sp = space_width(fontset, size_mm, cfg)
    ww = lambda w: _wwidth(fontset, w, size_mm, cfg)   # noqa: E731
    pad = 0.45 * size_mm
    natural, minimal = [], []
    for j in range(ncol):
        cells = [r[j] for r in rows]
        natural.append(max(1.6 * size_mm, max(
            (sum(ww(t) for t in c) + sp * (len(c) - 1) for c in cells if c),
            default=0.0)))
        minimal.append(min(natural[-1], 1.6 * size_mm))
    grid = bool(getattr(cfg, "grid", False))
    cell = max(0.5, float(getattr(cfg, "grid_cell", 5.0)))
    inner = _col_widths(natural, minimal, cfg.text_w - 2 * pad * ncol)
    cols = [w + 2 * pad for w in inner]
    if grid:
        # в тетради в клетку столбцы кратны клетке, как чертят от руки
        cols = [math.ceil(w / cell - 1e-6) * cell for w in cols]
        while sum(cols) > cfg.text_w + 1e-6 and max(cols) > cell:
            k = cols.index(max(cols))
            cols[k] -= cell
    total = sum(cols)
    x0 = cfg.margin_left
    if cfg.align == "center":
        x0 += max(0.0, (cfg.text_w - total) / 2.0)
    elif cfg.align == "right":
        x0 += max(0.0, cfg.text_w - total)

    lh = size_mm * cfg.line_spacing
    descent = size_mm * 0.29
    if grid:
        # ряд — две клетки, черты по линиям клетки, текст посередине ряда
        row_gap = up = down = top_extra = margin = lh
        shift = size_mm / 2.0
    else:
        # буквы — посередине ряда, выносным (у, р, д) снизу хватает места
        row_gap = max(0.25 * size_mm, 1.9 * size_mm - lh)
        both = lh + row_gap
        up, down = (both + size_mm) / 2.0, (both - size_mm) / 2.0
        top_extra, margin, shift = up - size_mm, row_gap + 0.35 * size_mm, 0.0
    tbl = Table(x0=x0, cols=cols, pad=pad, up=up, down=down, row_gap=row_gap,
                top_extra=top_extra, margin=margin, shift=shift)

    out = []
    offs = [sum(cols[:j]) for j in range(ncol)]
    for r, row in enumerate(rows):
        wrapped = [_cell_wrap(c, fontset, cfg, size_mm, cols[j] - 2 * pad, sp)
                   for j, c in enumerate(row)]
        n = max(1, max(len(w) for w in wrapped))
        for k in range(n):
            cells = []
            for j, w in enumerate(wrapped):
                words = w[k] if k < len(w) else []
                # короткое — по центру ячейки, длинный текст — от левого края
                cells.append((offs[j], cols[j], words, len(w) <= 1))
            out.append(Line(cells=cells, table=tbl, row=r, last_of_par=True,
                            width=total,
                            gap_before=(row_gap if (k == 0 and r) else 0.0)))
    return out


def _render_table_line(line, fontset, human, cfg, size_mm, base_mm, line_index):
    t = line.table
    sp = space_width(fontset, size_mm, cfg)
    strokes = []
    for (off, width, words, center) in line.cells:
        if not words:
            continue
        total = sum(w.width for w in words) + sp * (len(words) - 1)
        x = t.x0 + off + ((width - total) / 2.0 if center else t.pad)
        for i, w in enumerate(words):
            if i:
                x += sp * human.space_factor()
            ws, wwidth, _ = _render_word(w, fontset, human, cfg, size_mm,
                                         x, base_mm - t.shift, line_index)
            strokes.extend(ws)
            x += wwidth
    return strokes


def _hand_line(p0, p1, human, size_mm):
    """Черта рамки: у руки она чуть гуляет и не попадает точно в угол."""
    hc = human.cfg
    if not hc.enabled:
        return [p0, p1]
    rng = human.rng
    ln = math.dist(p0, p1) or 1.0
    ux, uy = (p1[0] - p0[0]) / ln, (p1[1] - p0[1]) / ln
    over = 0.06 * size_mm
    a = rng.uniform(-over, over * 0.5)
    b = rng.uniform(-over, over)
    q0 = (p0[0] - ux * a, p0[1] - uy * a)
    q1 = (p1[0] + ux * b, p1[1] + uy * b)
    amp = max(0.0, float(hc.tremor)) * size_mm * 0.6
    return HM.tremor([[q0, q1]], amp, size_mm * 6.0, rng)[0]


def _table_borders(chunk, top, human, size_mm):
    """Рамки таблиц, попавших на страницу: сначала горизонтали, потом вертикали."""
    out = []
    i = 0
    while i < len(chunk):
        t = chunk[i].table
        if t is None:
            i += 1
            continue
        j = i
        while j < len(chunk) and chunk[j].table is t:
            j += 1
        seg = chunk[i:j]
        ys = [top - (seg[0].y - t.up)]
        for a, b in zip(seg, seg[1:]):
            if b.row != a.row:
                ys.append(top - (a.y + t.down))
        ys.append(top - (seg[-1].y + t.down))
        xs = [t.x0]
        for w in t.cols:
            xs.append(xs[-1] + w)
        # змейкой: перо не возвращается через всю таблицу к началу черты
        for k, y in enumerate(ys):
            a, b = (xs[0], y), (xs[-1], y)
            out.append(_hand_line(a, b, human, size_mm) if k % 2 == 0
                       else _hand_line(b, a, human, size_mm))
        vx = xs if len(ys) % 2 == 0 else xs[::-1]
        for k, x in enumerate(vx):
            a, b = (x, ys[-1]), (x, ys[0])
            out.append(_hand_line(a, b, human, size_mm) if k % 2 == 0
                       else _hand_line(b, a, human, size_mm))
        i = j
    return out


def paginate(lines, cfg, size_mm, first_skip=0.0):
    """
    Разложить строки по страницам, накапливая высоту.

    Считаем не «сколько строк влезет по среднему», а честно ведём
    координату базовой линии: только так работают отбивки между абзацами
    и не срезается последняя строка с выносными элементами.

    Побочно проставляет каждой строке её y — расстояние вниз от верха
    текстового блока до базовой линии.

    first_skip — на сколько мм опустить начало текста на первой странице
    (над ним рисунок).
    """
    lh = size_mm * cfg.line_spacing
    avail = cfg.text_h
    descent = size_mm * 0.29          # запас под у, р, д, ц
    pages, cur = [], []
    y = None
    grid = bool(getattr(cfg, "grid", False))

    def snap(v):
        # в клетку добавка — целыми строками, иначе строки сойдут с линий
        if v <= 1e-6:
            return 0.0
        return math.ceil(v / lh - 1e-6) * lh if grid else v

    prev_depth = descent
    prev = None
    for ln in lines:
        # строка таблицы: сверху нужна черта, снизу — черта под рядом
        top_extra = ln.table.top_extra if ln.table is not None else 0.0
        below = max(descent, ln.table.down) if ln.table is not None else descent
        below = max(below, ln.depth_mm)
        # формула с дробью выше заглавных или ниже выносных: между базовыми
        # линиями нужно не меньше, чем глубина прошлой строки + высота этой
        height = max(ln.height_mm, size_mm)
        if ln.table is not None and (prev is None or prev.table is not ln.table):
            height = max(height, ln.table.up)          # верхняя черта таблицы
        over = snap(prev_depth + height + 0.25 * size_mm - lh)             if (height > size_mm or prev_depth > descent) else 0.0
        top_extra += snap(ln.height_mm - size_mm)
        if y is None:
            y = size_mm + first_skip + top_extra
            if first_skip > 0 and y + below > avail + 1e-6:
                pages.append([])            # под рисунком не осталось места
                y = size_mm + top_extra
        else:
            y += lh + max(ln.gap_before, over)
        if cur and y + below > avail + 1e-6:
            pages.append(cur)
            cur = []
            y = size_mm + top_extra
        ln.y = y
        prev_depth = max(descent, ln.depth_mm)
        prev = ln
        cur.append(ln)
    if cur:
        pages.append(cur)
    return pages or [[]]


def fit_size(text, fontset, cfg, human, first_skip=0.0):
    """
    Подобрать наибольший размер шрифта, при котором текст влезает
    на одну страницу. Меряем с отключёнными искажениями — иначе
    результат «плавал» бы от запуска к запуску.

    -> (размер_мм, влезло_ли). Если не влезло даже при минимальном
    размере, возвращается этот минимум и False: молча выдавать
    двадцать страниц вместо запрошенной одной — плохая услуга.
    """
    saved = human.cfg.enabled
    human.cfg.enabled = False
    try:
        def one_page(size):
            _n, lines = measure(text, fontset, cfg, human, size)
            return len(paginate(lines, cfg, size, first_skip)) <= 1

        lo, hi = cfg.autofit_min_size, cfg.autofit_max_size
        best, fits = lo, False
        if one_page(lo):
            fits = True
            for _ in range(28):
                mid = (lo + hi) / 2.0
                if one_page(mid):
                    best, lo = mid, mid
                else:
                    hi = mid
                if hi - lo < 0.02:
                    break
        return round(best, 2), fits
    finally:
        human.cfg.enabled = saved


# ------------------------------------------------------------- отрисовка

def _render_word(word, fontset, human, cfg, size_mm, x_mm, base_mm, line_index):
    """
    Нарисовать слово. -> (штрихи в мм, фактическая ширина, bbox по Y).
    Координаты листа: X вправо, Y вверх.
    """
    if word.parts:
        strokes, pen = [], x_mm
        ylo, yhi = base_mm, base_mm + size_mm
        for pt in word.parts:
            if pt[0] == "t":
                st, w, (a, b) = _render_word(Word(pt[1]), fontset, human, cfg,
                                             size_mm, pen, base_mm, line_index)
            else:
                st, w, (a, b) = MT.render(pt[1], fontset, human, size_mm, pen,
                                          base_mm, line_index)
            strokes.extend(st)
            pen += w
            ylo, yhi = min(ylo, a), max(yhi, b)
        return strokes, pen - x_mm, (ylo, yhi)
    scale = size_mm / CAP
    hcfg = human.cfg
    strokes = []
    pen = x_mm
    ylo, yhi = base_mm, base_mm
    joins = bool(getattr(hcfg, "joins", False))
    reach = float(getattr(hcfg, "join_reach", 0.9)) * size_mm
    prev = None     # выход предыдущей буквы: (номер штриха или None, точка, направление)

    text = word.text
    for k, ch in enumerate(text):
        g = human.pick_variant(fontset, ch)
        gs = g.strokes
        want_l = joins and prev is not None and JN.allows_left(g)
        want_r = joins and k + 1 < len(text) and JN.allows_right(g)
        w_in = w_out = None
        if (want_l or want_r) and gs:
            p_in, p_out = JN.points(g)
            gs, w_in, w_out = JN.prepare(gs, p_in if want_l else None,
                                         p_out if want_r else None)
        if hcfg.enabled and gs:
            gs = HM.humanize_glyph(gs, hcfg, human.rng, size_units=CAP,
                                   protect=(0, len(gs) - 1))
        gs = [s for s in gs if len(s) >= 2]
        dy = human.baseline_offset(pen, line_index, size_mm)
        mm = [[(pen + x * scale, base_mm + dy + y * scale) for (x, y) in s]
              for s in gs]
        first = len(strokes)
        strokes.extend(mm)
        for s in mm:
            for (_, yy) in s:
                ylo = min(ylo, yy)
                yhi = max(yhi, yy)

        # соединение с предыдущей буквой
        p_in = JN.locate(mm, w_in) if want_l else None
        if prev is not None and p_in is not None and math.dist(prev[1], p_in) <= reach:
            t_in = JN.tangent(mm[0] if w_in == "first" else mm[-1], False,
                              0.25 * size_mm)
            con = JN.connector(prev[1], prev[2], p_in, t_in)
            if w_in == "first":
                # перо не отрывается: соединение переходит прямо в букву
                strokes[first] = con + strokes[first][1:]
            else:
                strokes.insert(first, con)
            i_prev = prev[0]
            if i_prev == first - 1 and strokes[i_prev][-1] == prev[1]:
                # ...и из предыдущей буквы тоже
                strokes[i_prev] = strokes[i_prev] + strokes[first][1:]
                strokes.pop(first)
        prev = None
        if want_r and mm and w_out is not None:
            p_out = JN.locate(mm, w_out)
            # последний штрих буквы кончается в точке выхода; склейка только
            # дописывает штрихи спереди, так что ищем его по этой точке
            idx = next((i for i in range(len(strokes) - 1, -1, -1)
                        if strokes[i][-1] == p_out), None)
            prev = (idx, p_out, JN.tangent(mm[-1], True, 0.25 * size_mm))

        # клякса
        if hcfg.enabled and hcfg.blot_rate > 0 and human.rng.random() < hcfg.blot_rate:
            strokes.append(HM.blot(pen + g.advance * scale * 0.5,
                                   base_mm + dy + size_mm * 0.35,
                                   size_mm * 0.16, human.rng))

        pen += g.advance * scale + cfg.tracking_mm

    width = pen - x_mm
    if yhi <= ylo:
        yhi = ylo + size_mm
    return strokes, width, (ylo, yhi)


def render_line(line, fontset, human, cfg, size_mm, base_mm, line_index,
                justify=False):
    """Одна строка -> штрихи в мм."""
    if line.cells is not None:
        return _render_table_line(line, fontset, human, cfg, size_mm, base_mm,
                                  line_index)
    strokes = []
    if not line.words:
        return strokes

    extra = 0.0
    if justify and len(line.words) > 1 and not line.last_of_par:
        slack = cfg.text_w - line.width
        gaps = max(1, len(line.words) - 1)
        if slack > 0:
            extra = slack / gaps

    if cfg.align == "center" or line.center:
        x = cfg.margin_left + (cfg.text_w - line.width) / 2.0
    elif cfg.align == "right":
        x = cfg.margin_left + cfg.text_w - line.width
    else:
        x = cfg.margin_left + line.indent

    for i, w in enumerate(line.words):
        if i:
            x += line.gaps[i] + extra
        t = (x - cfg.margin_left) / max(cfg.text_w, 1e-6)
        slope = human.word_slope(t, size_mm)
        ws, wwidth, (ylo, yhi) = _render_word(
            w, fontset, human, cfg, size_mm, x, base_mm + slope, line_index)
        strokes.extend(ws)

        if w.struck:
            style = human.cfg.strike_style
            for p in HM.strike_path(x, ylo, x + wwidth, yhi, style, human.rng):
                strokes.append(p)

        x += wwidth
    return strokes


def build_pages(text, fontset, cfg, human, report=None, first_skip=0.0):
    """
    Полная раскладка. -> список Page.

    Возвращает столько страниц, сколько нужно тексту; при overflow="clip"
    лишнее отбрасывается, при "shrink" размер уменьшается до одной страницы.

    report — необязательный словарь, куда складывается, что именно
    произошло с автоподбором: интерфейсу это нужно, чтобы честно сказать
    «не влезает даже при минимальном размере».

    first_skip — отступ сверху на первой странице, мм (место под рисунок).
    """
    text = clean_text(text)
    cfg = grid_cfg(cfg)
    if getattr(cfg, "grid", False) and first_skip > 0:
        # под рисунком тоже встаём на линию клетки
        step = grid_step(cfg)
        first_skip = math.ceil(first_skip / step - 1e-6) * step
    size = cfg.size_mm
    fits = True
    if cfg.autofit:
        size, fits = fit_size(text, fontset, cfg, human, first_skip)
    elif cfg.overflow == "shrink":
        _n, probe = measure(text, fontset, cfg, human, size)
        if len(paginate(probe, cfg, size, first_skip)) > 1:
            size, fits = fit_size(text, fontset, cfg, human, first_skip)

    _n, lines = measure(text, fontset, cfg, human, size)
    chunks = paginate(lines, cfg, size, first_skip)
    if cfg.overflow == "clip":
        chunks = chunks[:1]
    lh = size * cfg.line_spacing

    pages = []
    top = cfg.sheet_h - cfg.margin_top
    index = 0
    for chunk in chunks:
        pg = Page(size_mm=size, n_lines=len(chunk))
        for ln in chunk:
            pg.strokes.extend(render_line(
                ln, fontset, human, cfg, size, top - ln.y, index,
                justify=(cfg.align == "justify")))
            index += 1
        pg.strokes.extend(_table_borders(chunk, top, human, size))
        if cfg.ruling:
            step = cfg.ruling_step or lh
            pg.guides = _ruling(cfg, step)
            if not pages and first_skip > 0:       # под рисунком линовки нет
                pg.guides = [g for g in pg.guides if g[0][1] < top - first_skip]
        pages.append(pg)
    pages = pages or [Page(size_mm=size)]
    hc = human.cfg
    style = getattr(hc, "calli_style", "off")
    if style in ("broad", "pointed"):
        # до поворота: угол пера отсчитывается от строки, а не от листа
        for pg in pages:
            pg.strokes = HM.calligraphy(pg.strokes, style, float(hc.calli_width),
                                        float(hc.calli_angle), float(hc.calli_pen))
            pg.pen_mm = float(hc.calli_pen)
    angle = float(getattr(cfg, "text_angle", 0.0) or 0.0)
    if abs(angle) > 1e-6:
        k = rotate_text(pages, cfg, angle, getattr(cfg, "text_angle_fit", True))
        if report is not None:
            report["angle_scale"] = k
    if report is not None:
        report.update(size_mm=size, autofit_ok=fits, pages=len(pages),
                      autofit_min=cfg.autofit_min_size)
    return pages


def grid_step(cfg):
    """Шаг строк в тетради в клетку, мм."""
    return max(0.5, float(cfg.grid_cell)) * max(1, int(cfg.grid_every))


def grid_cfg(cfg):
    """
    Настройки листа для тетради в клетку -> обычные настройки, при которых
    базовая линия каждой строки попадает точно на линию клетки.

    Первая базовая линия — на grid_first от верха листа, дальше ровно через
    шаг. Отбивка между абзацами округляется до целых строк, автоподбор и
    «уменьшить» выключаются: они меняли бы размер и сбивали строки с линий.
    """
    if not getattr(cfg, "grid", False):
        return cfg
    from dataclasses import replace
    step = grid_step(cfg)
    fill = float(cfg.grid_fill)
    size = fill * step if fill > 0 else float(cfg.size_mm)
    first = max(size, float(cfg.grid_first))
    gap = float(cfg.paragraph_gap)
    gap = math.ceil(gap / step - 1e-6) * step if gap > 0 else 0.0
    return replace(cfg, size_mm=size, line_spacing=step / size,
                   margin_top=first - size, paragraph_gap=gap,
                   autofit=False, ruling=False,
                   overflow="pages" if cfg.overflow == "shrink" else cfg.overflow)


def grid_baselines(cfg):
    """Высоты (мм от низа листа) всех строк, что уместятся на листе в клетку."""
    g = grid_cfg(cfg)
    step = grid_step(cfg)
    y = cfg.sheet_h - (g.margin_top + g.size_mm)
    out = []
    while y - g.size_mm * 0.29 >= g.margin_bottom - 1e-6:
        out.append(y)
        y -= step
    return out


def rotate_text(pages, cfg, angle, fit=True):
    """
    Повернуть текст (и разлиновку) каждой страницы на angle градусов
    против часовой вокруг центра полей.

    Под углом строка длиннее своей проекции, поэтому текст, свёрстанный
    по ширине полей, после поворота вылезает за них. При fit весь лист
    одинаково ужимается, пока самая «выпирающая» страница не войдёт в поля.
    -> применённый масштаб (1.0 — без уменьшения).
    """
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    x0, x1 = cfg.margin_left, cfg.margin_left + cfg.text_w
    y0, y1 = cfg.margin_bottom, cfg.sheet_h - cfg.margin_top
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    def rot(strokes):
        return [[(cx + (x - cx) * ca - (y - cy) * sa,
                  cy + (x - cx) * sa + (y - cy) * ca) for (x, y) in s]
                for s in strokes]

    for pg in pages:
        pg.strokes = rot(pg.strokes)
        pg.guides = rot(pg.guides)

    k = 1.0
    if fit:
        for pg in pages:
            for s in list(pg.strokes) + list(pg.guides):
                for (x, y) in s:
                    dx, dy = x - cx, y - cy
                    if abs(dx) > 1e-9:
                        k = min(k, (x1 - cx) / abs(dx))
                    if abs(dy) > 1e-9:
                        k = min(k, (y1 - cy) / abs(dy))
        k = max(0.05, k)
        if k < 1.0 - 1e-6:
            for pg in pages:
                pg.strokes = [[(cx + (x - cx) * k, cy + (y - cy) * k) for (x, y) in s]
                              for s in pg.strokes]
                pg.guides = [[(cx + (x - cx) * k, cy + (y - cy) * k) for (x, y) in s]
                             for s in pg.guides]
                pg.size_mm *= k
    return k


def _ruling(cfg, step):
    """Линии «тетрадной» разлиновки на всю ширину текстового блока."""
    out = []
    y = cfg.sheet_h - cfg.margin_top - step
    while y > cfg.margin_bottom:
        out.append([(cfg.margin_left, y), (cfg.margin_left + cfg.text_w, y)])
        y -= step
    return out


def stats(pages):
    """Сводка для интерфейса: страниц, штрихов, длина линии, габарит."""
    ink = sum(p.ink_length() for p in pages)
    nst = sum(len(p.strokes) for p in pages)
    bb = None
    for p in pages:
        b = p.bbox()
        if b == (0.0, 0.0, 0.0, 0.0):
            continue
        bb = b if bb is None else (min(bb[0], b[0]), min(bb[1], b[1]),
                                   max(bb[2], b[2]), max(bb[3], b[3]))
    return {"pages": len(pages), "strokes": nst, "ink_mm": ink,
            "bbox": bb or (0.0, 0.0, 0.0, 0.0),
            "lines": sum(p.n_lines for p in pages)}
