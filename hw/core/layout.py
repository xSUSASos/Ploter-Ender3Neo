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
  * разлиновку «под тетрадь».
"""

import math
import unicodedata
from dataclasses import dataclass, field

from .glyphset import CAP
from . import humanize as HM


@dataclass
class Word:
    text: str
    struck: bool = False        # это зачёркиваемая (ошибочная) копия
    width: float = 0.0          # мм, номинальная


@dataclass
class Line:
    words: list = field(default_factory=list)
    gaps: list = field(default_factory=list)   # ширина пробелов, мм
    indent: float = 0.0
    last_of_par: bool = False
    width: float = 0.0
    gap_before: float = 0.0   # доп. отбивка сверху (между абзацами), мм
    y: float = 0.0            # базовая линия, мм вниз от верха текстового блока


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


def split_paragraphs(text):
    """Текст -> список абзацев, абзац -> список слов."""
    pars = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        pars.append(raw.split())
    return pars


def apply_errors(words, human):
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
        w.width = word_width(fontset, w.text, size_mm, cfg.tracking_mm)
        gap = sp * human.space_factor() if cur.words else 0.0
        need = cur.width + gap + w.width

        if cur.words and need > maxw + 1e-6:
            lines.append(cur)
            cur = Line()
            gap = 0.0
            need = w.width

        # слово длиннее всей строки — режем по буквам
        if not cur.words and w.width > maxw:
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
    for words in split_paragraphs(text):
        if not words:
            all_lines.append(Line(last_of_par=True))     # пустая строка
            continue
        ws = apply_errors(words, human)
        lines = wrap(ws, fontset, cfg, size_mm, human, indent)
        if lines and all_lines:
            lines[0].gap_before = cfg.paragraph_gap
        all_lines.extend(lines)
    return len(all_lines), all_lines


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
    for ln in lines:
        if y is None:
            y = size_mm + first_skip
            if first_skip > 0 and y + descent > avail + 1e-6:
                pages.append([])            # под рисунком не осталось места
                y = size_mm
        else:
            y += lh + ln.gap_before
        if cur and y + descent > avail + 1e-6:
            pages.append(cur)
            cur = []
            y = size_mm
        ln.y = y
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
    scale = size_mm / CAP
    hcfg = human.cfg
    strokes = []
    pen = x_mm
    ylo, yhi = base_mm, base_mm

    for ch in word.text:
        g = human.pick_variant(fontset, ch)
        gs = g.strokes
        if hcfg.enabled and gs:
            gs = HM.humanize_glyph(gs, hcfg, human.rng, size_units=CAP)
        dy = human.baseline_offset(pen, line_index, size_mm)
        for s in gs:
            if len(s) < 2:
                continue
            pts = [(pen + x * scale, base_mm + dy + y * scale) for (x, y) in s]
            strokes.append(pts)
            for (_, yy) in pts:
                ylo = min(ylo, yy)
                yhi = max(yhi, yy)

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
    strokes = []
    if not line.words:
        return strokes

    extra = 0.0
    if justify and len(line.words) > 1 and not line.last_of_par:
        slack = cfg.text_w - line.width
        gaps = max(1, len(line.words) - 1)
        if slack > 0:
            extra = slack / gaps

    if cfg.align == "center":
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
