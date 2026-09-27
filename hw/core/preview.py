# -*- coding: utf-8 -*-
"""
Превью раскладки: SVG (для файла и браузера) и растр (для окна программы).

Показывает лист, поля, разлиновку, сам текст и — по желанию — холостые
перемещения пера. Посмотреть превью до печати сильно дешевле, чем испортить
лист и потратить полчаса машинного времени.
"""

from PIL import Image, ImageDraw

INK = "#101828"
SHEET = "#ffffff"
EDGE = "#c9cdd6"
MARGIN = "#e3b7b7"
GUIDE = "#dfe3ea"
TRAVEL = "#9bb7d4"
GRID = "#d6e4f5"


def _y(cfg, y):
    """Координата листа (Y вверх) -> SVG/растр (Y вниз)."""
    return cfg.sheet_h - y


# ------------------------------------------------------------------- SVG

def _art_width(cfg, art_width):
    """Толщина линии рисунка, мм: как у пера, а по умолчанию — 0.5."""
    return max(0.05, float(art_width or 0.5))


def to_svg(pages, cfg, px_per_mm=4.0, show_travel=False, show_margins=True,
           page_index=None, gap_mm=8.0, art_width=None):
    """Одна или несколько страниц -> строка SVG."""
    sel = pages if page_index is None else [pages[page_index]]
    n = max(1, len(sel))
    total_w = cfg.sheet_w * n + gap_mm * (n - 1)
    W = total_w * px_per_mm
    H = cfg.sheet_h * px_per_mm

    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
         'viewBox="0 0 %.2f %.2f">' % (W, H, total_w, cfg.sheet_h)]
    p.append('<rect width="100%" height="100%" fill="#f4f5f7"/>')

    sw_text = max(0.18, cfg.size_mm * 0.045) if getattr(cfg, "size_mm", 0) else 0.3

    for i, pg in enumerate(sel):
        ox = i * (cfg.sheet_w + gap_mm)
        p.append('<g transform="translate(%.2f 0)">' % ox)
        p.append('<rect x="0" y="0" width="%.2f" height="%.2f" fill="%s" '
                 'stroke="%s" stroke-width="0.25"/>'
                 % (cfg.sheet_w, cfg.sheet_h, SHEET, EDGE))

        if show_margins:
            p.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                     'fill="none" stroke="%s" stroke-width="0.15" '
                     'stroke-dasharray="1.5 1.5"/>'
                     % (cfg.margin_left, cfg.margin_top,
                        cfg.text_w, cfg.text_h, MARGIN))

        for s in pg.guides:
            p.append(_path(s, cfg, GUIDE, 0.2))

        aw = _art_width(cfg, art_width)
        for s in getattr(pg, "art", []):
            p.append(_path(s, cfg, INK, aw))

        if show_travel:
            prev = None
            for s in list(getattr(pg, "art", [])) + list(pg.strokes):
                if prev is not None and s:
                    p.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" '
                             'stroke="%s" stroke-width="0.1" '
                             'stroke-dasharray="0.6 0.6"/>'
                             % (prev[0], _y(cfg, prev[1]), s[0][0],
                                _y(cfg, s[0][1]), TRAVEL))
                if s:
                    prev = s[-1]

        sw = max(0.18, pg.size_mm * 0.05) if pg.size_mm else sw_text
        if getattr(pg, "pen_mm", 0):
            sw = pg.pen_mm
        for s in pg.strokes:
            p.append(_path(s, cfg, INK, sw))
        p.append('</g>')

    p.append('</svg>')
    return "\n".join(p)


def _path(pts, cfg, color, width):
    if len(pts) < 2:
        return ""
    d = "M %.2f %.2f " % (pts[0][0], _y(cfg, pts[0][1]))
    d += " ".join("L %.2f %.2f" % (x, _y(cfg, y)) for (x, y) in pts[1:])
    return ('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
            'stroke-linecap="round" stroke-linejoin="round"/>' % (d, color, width))


def save_svg(path, pages, cfg, **kw):
    with open(path, "w", encoding="utf-8") as f:
        f.write(to_svg(pages, cfg, **kw))
    return path


# ----------------------------------------------------------------- растр

def to_image(page, cfg, px_per_mm=4.0, show_travel=False, show_margins=True,
             bg="#f4f5f7", art_width=None):
    """Одна страница -> PIL.Image. Используется в окне программы и для PNG."""
    W = int(round(cfg.sheet_w * px_per_mm))
    H = int(round(cfg.sheet_h * px_per_mm))
    im = Image.new("RGB", (max(1, W), max(1, H)), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, H - 1], fill=SHEET, outline=EDGE)

    def xy(pt):
        return (pt[0] * px_per_mm, _y(cfg, pt[1]) * px_per_mm)

    if getattr(cfg, "grid", False) and cfg.grid_cell > 0.5:
        # клетка тетради — только для глаз, в G-code её нет. Горизонтали
        # проходят через линию первой строки, вертикали — через левое поле
        c = cfg.grid_cell
        y0 = cfg.grid_first % c
        while y0 < cfg.sheet_h:
            d.line([0, y0 * px_per_mm, W, y0 * px_per_mm], fill=GRID)
            y0 += c
        x0 = cfg.margin_left % c
        while x0 < cfg.sheet_w:
            d.line([x0 * px_per_mm, 0, x0 * px_per_mm, H], fill=GRID)
            x0 += c

    if show_margins:
        d.rectangle([cfg.margin_left * px_per_mm, cfg.margin_top * px_per_mm,
                     (cfg.margin_left + cfg.text_w) * px_per_mm,
                     (cfg.margin_top + cfg.text_h) * px_per_mm],
                    outline=MARGIN)

    for s in page.guides:
        if len(s) > 1:
            d.line([xy(q) for q in s], fill=GUIDE, width=1)

    aw = max(1, int(round(_art_width(cfg, art_width) * px_per_mm)))
    for s in getattr(page, "art", []):
        if len(s) > 1:
            d.line([xy(q) for q in s], fill=INK, width=aw, joint="curve")

    if show_travel:
        prev = None
        for s in list(getattr(page, "art", [])) + list(page.strokes):
            if prev is not None and s:
                d.line([xy(prev), xy(s[0])], fill=TRAVEL, width=1)
            if s:
                prev = s[-1]

    w = max(1, int(round(page.size_mm * 0.055 * px_per_mm))) if page.size_mm else 1
    if getattr(page, "pen_mm", 0):
        w = max(1, int(round(page.pen_mm * px_per_mm)))
    for s in page.strokes:
        if len(s) > 1:
            d.line([xy(q) for q in s], fill=INK, width=w, joint="curve")
    return im


def save_png(path, page, cfg, px_per_mm=8.0, **kw):
    to_image(page, cfg, px_per_mm=px_per_mm, **kw).save(path)
    return path


# --------------------------------------------------------- вид на стол

def bed_image(page, cfg, size_px=230):
    """
    Стол принтера сверху: как лежит лист (с любым поворотом) и где на нём
    окажутся текст и рисунок. Верхний край листа выделен цветом — по нему
    видно, куда смотрит лист. cfg — полный Config (нужен размер стола).
    """
    from .gcode import sheet_to_bed
    m, pc = cfg.machine, cfg.page
    pad = 6
    s = (size_px - 2 * pad) / max(m.bed_x, m.bed_y, 1.0)
    W, H = int(m.bed_x * s) + 2 * pad, int(m.bed_y * s) + 2 * pad
    im = Image.new("RGB", (W, H), "#e9ebef")
    d = ImageDraw.Draw(im)

    def xy(p):
        return (pad + p[0] * s, H - pad - p[1] * s)

    d.rectangle([xy((0, m.bed_y)), xy((m.bed_x, 0))], fill="#c9ced6",
                outline="#8a929e")
    w, h = pc.sheet_w, pc.sheet_h
    corners = [sheet_to_bed(c, pc) for c in ((0, 0), (w, 0), (w, h), (0, h))]
    d.polygon([xy(c) for c in corners], fill=SHEET, outline="#7d8694")
    d.line([xy(corners[3]), xy(corners[2])], fill="#d9534f", width=3)
    for s_ in list(getattr(page, "art", [])) + list(page.strokes):
        if len(s_) > 1:
            d.line([xy(sheet_to_bed(q, pc)) for q in s_], fill=INK, width=1)
    r = 4
    ox, oy = xy((0, 0))
    d.ellipse([ox - r, oy - r, ox + r, oy + r], outline="#d9534f", width=2)
    return im


# ------------------------------------------------- превью одного глифа

def glyph_image(glyph, size=96, pad=0.14, bg="#ffffff", ink=INK,
                guides=True):
    """Картинка одного начертания — для списка вариантов в окне программы."""
    from .glyphset import CAP
    im = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(im)
    span = CAP * 1.7                       # немного места под выносные
    s = size * (1 - 2 * pad) / span
    base_y = size * (1 - pad) - (4.0 * s)  # базовая линия
    x0 = size * pad

    if guides:
        d.line([0, base_y, size, base_y], fill="#e8e8ee")
        d.line([0, base_y - CAP * s, size, base_y - CAP * s], fill="#f1f1f5")

    if not glyph.strokes:
        return im
    xs = [p[0] for st in glyph.strokes for p in st]
    off = (size - (max(xs) - min(xs)) * s) / 2.0 - min(xs) * s
    off = max(x0, off)
    for st in glyph.strokes:
        if len(st) < 2:
            continue
        d.line([(off + x * s, base_y - y * s) for (x, y) in st],
               fill=ink, width=max(1, int(size / 44)), joint="curve")
    return im
