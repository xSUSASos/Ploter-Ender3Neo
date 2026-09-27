# -*- coding: utf-8 -*-
"""
Каллиграфический шрифт из образца прописи (tools/azbuka.png — страница
дореволюционной азбуки, общественное достояние).

Каждая буква задана номерами связных компонент на бинаризованной картинке
(разметка сделана вручную по увеличенным полосам). Буква вырезается,
увеличивается в 4 раза — осевая линия выходит гладкой, — векторизуется тем
же конвейером, что и фото прописи, и приводится к единицам шрифта по
базовой линии и высоте заглавных своей строки.

    python tools/make_callig_font.py            -> hw/core/font_callig.py
    python tools/make_callig_font.py --preview  +  tools/callig_preview.png

Запускать нужно только при правке разметки: результат лежит в репозитории.
"""

import json
import math
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from hw.core import vectorize as VZ                                   # noqa: E402
from hw.core.glyphset import Glyph, normalize_strokes, order_strokes, CAP  # noqa: E402

SRC = os.path.join(HERE, "azbuka.png")
OUT = os.path.join(ROOT, "hw", "core", "font_callig.py")
THR = 0.8          # порог «чернил» относительно бумаги: ниже — волосяные рвутся
UP = 4             # во сколько раз увеличивать букву перед векторизацией

# строка: [(символ, [компоненты])], компоненты базовой линии, компоненты заглавных
ROWS = [
    ([("А", [10]), ("а", [15]), ("Б", [7]), ("б", [11]), ("В", [8]),
      ("в", [9, 14, 18]), ("Г", [4]), ("г", [13]), ("Д", [5]), ("д", [12]),
      ("д", [6])],
     [15, 9, 13], [10, 7, 8, 4, 5]),
    ([("Е", [28]), ("е", [38]), ("Ж", [27]), ("ж", [36]), ("З", [26]),
      ("з", [35]), ("И", [25]), ("и", [37]), ("К", [22, 24]), ("к", [34])],
     [38, 36, 37, 34], [28, 27, 26, 25, 22]),
    ([("Л", [56]), ("л", [61]), ("М", [55]), ("м", [60]), ("Н", [52]),
      ("н", [59, 62]), ("О", [53]), ("о", [58]), ("П", [51]), ("п", [57])],
     [61, 60, 59, 58, 57], [56, 55, 52, 53, 51]),
    ([("Р", [75, 77]), ("р", [84]), ("С", [76]), ("с", [83]), ("Т", [74]),
      ("т", [82]), ("У", [72]), ("у", [81]), ("Ф", [73, 70]), ("ф", [79]),
      ("Х", [69]), ("х", [80])],
     [83, 82, 80], [75, 76, 74, 72, 69]),
    ([("Ц", [96]), ("ц", [105]), ("Ч", [95]), ("ч", [101]),
      ("Ш", [94, 93, 97]), ("ш", [103, 104]), ("Щ", [91, 92]), ("щ", [102]),
      ("ъ", [98]), ("ы", [99]), ("ь", [100])],
     [103, 98, 99, 100], [95, 94]),
    ([("Э", [117]), ("э", [125]), ("Ю", [115, 118]), ("ю", [123]),
      ("Я", [114]), ("я", [121]), ("й", [120, 119])],
     [125, 123, 121], [117, 115, 114]),
    ([("1", [151]), ("2", [148]), ("3", [149]), ("4", [153]), ("5", [152]),
      ("6", [145]), ("7", [150]), ("8", [146]), ("9", [147]), ("0", [144])],
     [151, 148, 149, 152, 146, 144], None),
]

# запятые образца, сросшиеся с концом буквы: вырезаем прямоугольником
# (x0, y0, x1, y1) в пикселях исходной картинки
CUT = {"к": (561, 195, 571, 206), "х": (564, 331, 573, 341)}

# боковые пробелы, доли CAP: строчные почти касаются друг друга — в прописи
# выход одной буквы переходит во вход следующей
BEARING_LOWER = 0.0
BEARING_UPPER = 0.06
BEARING_DIGIT = 0.08


def load():
    a = np.asarray(Image.open(SRC).convert("L"), float)
    bg = ndimage.gaussian_filter(ndimage.maximum_filter(a, 15), 10)
    norm = a / np.maximum(bg, 1.0)
    lab, _n = ndimage.label(norm < THR, np.ones((3, 3)))
    return norm, lab


def comp_box(lab, ids):
    ys, xs = np.nonzero(np.isin(lab, ids))
    return xs.min(), ys.min(), xs.max(), ys.max()


def glyph_strokes(norm, lab, ids, base, span, cut=None):
    """Буква -> штрихи в пикселях исходной картинки (Y вниз)."""
    x0, y0, x1, y1 = comp_box(lab, ids)
    p = 6
    x0, y0 = max(0, x0 - p), max(0, y0 - p)
    x1, y1 = min(norm.shape[1], x1 + p + 1), min(norm.shape[0], y1 + p + 1)
    crop = norm[y0:y1, x0:x1].astype(np.float32)
    own = np.isin(lab[y0:y1, x0:x1], ids)
    own = ndimage.binary_dilation(own, iterations=2)       # чуть шире — край мазка
    if cut:
        cx0, cy0, cx1, cy1 = cut
        own[max(0, cy0 - y0):max(0, cy1 - y0), max(0, cx0 - x0):max(0, cx1 - x0)] = False
    h, w = crop.shape
    big = cv2.resize(crop, (w * UP, h * UP), interpolation=cv2.INTER_CUBIC)
    big = ndimage.gaussian_filter(big, 1.2)
    own_big = cv2.resize(own.astype(np.uint8), (w * UP, h * UP),
                         interpolation=cv2.INTER_NEAREST) > 0
    mask = (big < THR) & own_big
    mask = VZ.clean_mask(mask, min_area=30, close_holes=True)
    paths = VZ.vectorize_mask(mask, min_branch=6, smooth=7, simplify_tol=1.2,
                              merge_gap=6.0, min_path_len=10.0)
    paths = [[(x0 + x / UP, y0 + y / UP) for (x, y) in pth] for pth in paths]
    return drop_crumbs(paths, base, span)


def drop_crumbs(paths, base, span):
    """
    Убрать крошки у базовой линии: запятые и точки из образца, прилипшие
    к букве («к,», «п.»), и оборванные кусочки волосяных линий.
    """
    if len(paths) < 2:
        return paths
    keep = []
    longest = max(VZ_len(p) for p in paths)
    for p in paths:
        ln = VZ_len(p)
        cy = sum(q[1] for q in p) / len(p)
        if ln < 0.2 * span and ln < 0.5 * longest and cy > base - 0.3 * span:
            continue
        keep.append(p)
    return keep


def VZ_len(p):
    return sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))


def build():
    norm, lab = load()
    boxes = {}
    caps = []
    for glyphs, base_ids, cap_ids in ROWS:
        if cap_ids:
            base = float(np.median([comp_box(lab, [i])[3] for i in base_ids]))
            top = float(np.median([comp_box(lab, [i])[1] for i in cap_ids]))
            caps.append(base - top)
    cap_span = float(np.mean(caps))

    out = {}
    for glyphs, base_ids, cap_ids in ROWS:
        base = float(np.median([comp_box(lab, [i])[3] for i in base_ids]))
        if cap_ids:
            top = float(np.median([comp_box(lab, [i])[1] for i in cap_ids]))
        else:                                   # цифры — в масштабе букв
            top = base - cap_span
        for ch, ids in glyphs:
            st = glyph_strokes(norm, lab, ids, base, base - top, CUT.get(ch))
            if ch.isdigit():
                sb = BEARING_DIGIT
            elif ch.isupper():
                sb = BEARING_UPPER
            else:
                sb = BEARING_LOWER
            adv, ns = normalize_strokes(st, base, top, side_bearing=sb,
                                        min_advance=3.0)
            adv, ns = body_metrics(ns, sb, xh=XH_BAND)
            g = Glyph(ch, adv, order_strokes(ns), source="callig",
                      note="пропись")
            out.setdefault(ch, []).append(g)
            boxes[ch] = comp_box(lab, ids)
    derive(out)
    return out


XH_BAND = 5.7      # высота строчных в единицах шрифта (замер по а, и, м, н, о…)


def body_metrics(strokes, bearing, xh):
    """
    Ширина буквы по её «телу» — полосе строчных от базовой линии до верха
    строчных, а не по всей рамке. У наклонного письма рамку раздувают
    хвосты (у «р», «у», «д» уходят влево) и росчерки заглавных; если
    считать по ним, буквы разъезжаются. В прописи тела стоят вплотную,
    а хвосты нависают над соседями.
    """
    pts = [p for s in strokes for p in s if -0.3 <= p[1] <= xh + 0.3]
    if len(pts) < 2:
        pts = [p for s in strokes for p in s]
    x0 = min(p[0] for p in pts)
    x1 = max(p[0] for p in pts)
    b = bearing * CAP
    out = [[(x - x0 + b, y) for (x, y) in s] for s in strokes]
    return max(2.0, x1 - x0 + 2 * b), out


def _shift(g, dx=0.0, dy=0.0, k=1.0):
    return [[(x * k + dx, y * k + dy) for (x, y) in s] for s in g.strokes]


def derive(font):
    """Буквы, которых нет на образце: собираем из имеющихся."""
    # Й = И + дужка от й; Ё/ё = Е/е + две точки
    ii = font["й"][0]
    base_i = font["и"][0]
    xs_i = [p[0] for s in base_i.strokes for p in s]
    ys_i = [p[1] for s in base_i.strokes for p in s]
    top_i = max(ys_i)
    breve = [s for s in ii.strokes if min(p[1] for p in s) > top_i + 0.5]
    if not breve:                               # на всякий случай — самый верхний
        breve = [max(ii.strokes, key=lambda s: min(p[1] for p in s))]
    bx = [p[0] for s in breve for p in s]
    by = [p[1] for s in breve for p in s]
    for up, low in (("И", "Й"),):
        g = font[up][0]
        gx = [p[0] for s in g.strokes for p in s]
        gy = [p[1] for s in g.strokes for p in s]
        k = 1.5
        cx = (max(gx) + min(gx)) / 2.0 + 0.12 * CAP   # с учётом наклона
        dx = cx - k * (max(bx) + min(bx)) / 2.0
        dy = max(gy) + 1.2 - k * min(by)
        br = [[(x * k + dx, y * k + dy) for (x, y) in s] for s in breve]
        font[low] = [Glyph(low, g.advance, [list(s) for s in g.strokes] + br,
                           "callig", "собрано: И + дужка")]

    def dots(g, lift):
        gx = [p[0] for s in g.strokes for p in s]
        gy = [p[1] for s in g.strokes for p in s]
        cx = (max(gx) + min(gx)) / 2.0 + 0.1 * CAP
        y = max(gy) + lift
        r = 0.45
        out = []
        for ox in (-1.4, 1.4):
            out.append([(cx + ox + r * math.cos(t * math.pi / 4),
                         y + r * math.sin(t * math.pi / 4)) for t in range(9)])
        return out

    for src, dst, lift in (("Е", "Ё", 1.6), ("е", "ё", 1.8)):
        g = font[src][0]
        font[dst] = [Glyph(dst, g.advance, [list(s) for s in g.strokes] + dots(g, lift),
                           "callig", "собрано: %s + точки" % src)]


def write(font):
    data = {ch: [g.to_dict() for g in gs] for ch, gs in sorted(font.items())}
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write('"""\nКаллиграфический шрифт «Пропись»: буквы дореволюционной азбуки\n'
                "(общественное достояние), векторизованные по осевой линии.\n"
                "Сгенерировано tools/make_callig_font.py — руками не править.\n"
                '"""\n\n')
        f.write("DATA = %s\n" % text)
    print("букв %d, начертаний %d -> %s"
          % (len(font), sum(len(v) for v in font.values()), OUT))


def preview(font, path):
    from PIL import ImageDraw
    chars = sorted(font)
    cols = 12
    cell = 90
    rows = (sum(len(font[c]) for c in chars) + cols - 1) // cols
    im = Image.new("RGB", (cols * cell, rows * cell), "white")
    d = ImageDraw.Draw(im)
    i = 0
    s = cell / (CAP * 1.9)
    for ch in chars:
        for g in font[ch]:
            ox, oy = (i % cols) * cell, (i // cols) * cell
            base = oy + cell * 0.72
            d.line([ox, base, ox + cell, base], fill="#e4e4ea")
            d.line([ox, base - CAP * s, ox + cell, base - CAP * s], fill="#f0f0f4")
            for st in g.strokes:
                if len(st) > 1:
                    d.line([(ox + 6 + x * s, base - y * s) for x, y in st],
                           fill="#101828", width=2)
            d.text((ox + 3, oy + 2), ch, fill="#c33")
            i += 1
    im.save(path)
    print("превью:", path)


if __name__ == "__main__":
    f = build()
    write(f)
    if "--preview" in sys.argv:
        preview(f, os.path.join(HERE, "callig_preview.png"))
