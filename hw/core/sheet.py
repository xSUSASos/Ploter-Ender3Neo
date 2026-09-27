# -*- coding: utf-8 -*-
"""
Шаблон-пропись для сбора почерка и обратное чтение с фотографии.

Как это работает:
  1. make_template()  печатает лист: сетка ячеек, в каждой — направляющие
     (линия заглавных / средняя / базовая) и подпись, какую букву писать.
     По углам — чёрные квадраты-реперы плюс пятый, задающий ориентацию.
  2. Вы пишете буквы ручкой прямо в ячейках.
  3. detect_sheet() находит реперы на фото и выпрямляет лист (гомография),
     после чего геометрия листа известна с точностью до пикселя.
  4. slice_cells() режет выпрямленный лист на ячейки и отдаёт каждую вместе
     с положением базовой линии и линии заглавных — этого достаточно,
     чтобы привести букву к единицам шрифта.

Разлиновка и подписи печатаются светло-серым: порог по уровню чернил
в vectorize их отбрасывает, а тёмную ручку оставляет.
"""

import json
import math
import os
from dataclasses import dataclass, field, asdict

import numpy as np
from PIL import Image, ImageDraw

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

from . import font_vector as FV

MM = 25.4

# доли высоты ячейки, отсчёт от её верха
F_ASC = 0.186      # верх диакритики (Й, Ё)
F_CAP = 0.300      # линия заглавных
F_XH = 0.443       # средняя линия (высота строчных)
F_BASE = 0.700     # базовая линия
F_DESC = 0.814     # низ выносных (у, р, д)

LABEL_BOX = (0.02, 0.02, 0.26, 0.17)   # где печатается подпись-образец


# ------------------------------------------------------------------ описание

@dataclass
class TemplateSpec:
    """Геометрия шаблона. Нужна и для печати, и для чтения фото."""
    sheet_w: float = 210.0
    sheet_h: float = 297.0
    marker_inset: float = 10.0     # от края листа до центра репера, мм
    marker_size: float = 7.0       # сторона квадрата-репера, мм
    orient_offset: float = 16.0    # смещение пятого репера от верх-левого, мм
    orient_size: float = 4.0

    grid_left: float = 14.0
    grid_top: float = 26.0
    grid_right: float = 14.0
    grid_bottom: float = 16.0
    cols: int = 8
    rows: int = 11

    variants: int = 3
    pages: list = field(default_factory=list)   # [[символ, символ, ...], ...]
    title: str = ""

    # ---------------------------------------------------------- геометрия
    @property
    def cell_w(self):
        return (self.sheet_w - self.grid_left - self.grid_right) / self.cols

    @property
    def cell_h(self):
        return (self.sheet_h - self.grid_top - self.grid_bottom) / self.rows

    def cell_rect(self, row, col):
        """Прямоугольник ячейки в мм: (x0, y0, x1, y1), Y вниз от верха листа."""
        x0 = self.grid_left + col * self.cell_w
        y0 = self.grid_top + row * self.cell_h
        return (x0, y0, x0 + self.cell_w, y0 + self.cell_h)

    def marker_centers(self):
        i = self.marker_inset
        return [(i, i),
                (self.sheet_w - i, i),
                (self.sheet_w - i, self.sheet_h - i),
                (i, self.sheet_h - i)]

    def orient_center(self):
        """Пятый репер — под левым верхним. Он снимает неоднозначность
        поворота на 90/180 градусов и не мешает заголовку."""
        i = self.marker_inset
        return (i, i + self.orient_offset)

    def capacity(self):
        return self.cols * self.rows

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        s = cls()
        for k, v in d.items():
            if hasattr(s, k):
                setattr(s, k, v)
        return s

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def plan_pages(chars, spec):
    """Разложить символы (с повторами по числу вариантов) по страницам."""
    seq = []
    for ch in chars:
        seq.extend([ch] * spec.variants)
    cap = spec.capacity()
    spec.pages = [seq[i:i + cap] for i in range(0, len(seq), cap)]
    return spec


# -------------------------------------------------------------- рисование

def _draw_polys(drw, polys, ox, oy, scale, fill, width, flip_y=True):
    """Полилинии шрифта (Y вверх) -> PIL (Y вниз)."""
    for p in polys:
        if len(p) < 2:
            continue
        pts = [(ox + x * scale, oy - y * scale if flip_y else oy + y * scale)
               for (x, y) in p]
        drw.line(pts, fill=fill, width=width, joint="curve")


def draw_text_vec(drw, s, x_mm, y_mm, size_mm, px_per_mm, fill=(0, 0, 0),
                  width=1, tracking=0.0):
    """Напечатать строку встроенным векторным шрифтом. y_mm — базовая линия."""
    scale = size_mm / FV.CAP * px_per_mm
    pen = x_mm * px_per_mm
    base = y_mm * px_per_mm
    for ch in s:
        adv, polys = FV.get_glyph(ch)
        _draw_polys(drw, polys, pen, base, scale, fill, width)
        pen += (adv + tracking) * scale
    return pen / px_per_mm - x_mm


# Цвета печати шаблона. Все светлее порога чернил (по умолчанию 0.68),
# иначе разлиновка попадёт в векторизацию вместе с буквой.
# В долях от белого: 205/255 = 0.80, 222/255 = 0.87, 214/255 = 0.84.
GUIDE = (205, 205, 205)      # базовая линия — сплошная
GUIDE_SOFT = (222, 222, 222)  # остальные — пунктиром
LABEL = (186, 186, 186)      # подпись (её ещё и затирают при нарезке)
FRAME = (214, 214, 214)      # рамка ячейки
GHOST = (230, 230, 230)      # бледный образец начертания


def render_page(spec, page_index, dpi=200, show_reference=True):
    """Отрисовать одну страницу шаблона -> PIL.Image (RGB, белый фон)."""
    s = dpi / MM
    W = int(round(spec.sheet_w * s))
    H = int(round(spec.sheet_h * s))
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    lw = max(1, int(round(dpi / 200)))

    # реперы
    half = spec.marker_size / 2.0
    for (cx, cy) in spec.marker_centers():
        d.rectangle([(cx - half) * s, (cy - half) * s,
                     (cx + half) * s, (cy + half) * s], fill=(0, 0, 0))
    ox, oy = spec.orient_center()
    oh = spec.orient_size / 2.0
    d.rectangle([(ox - oh) * s, (oy - oh) * s,
                 (ox + oh) * s, (oy + oh) * s], fill=(0, 0, 0))

    # заголовок
    chars = spec.pages[page_index] if page_index < len(spec.pages) else []
    head_x = spec.marker_inset + spec.marker_size / 2.0 + 6.0
    head = spec.title or "ОБРАЗЕЦ ПОЧЕРКА"
    draw_text_vec(d, head, head_x, spec.grid_top - 12.0, 4.0, s,
                  (110, 110, 110), lw)
    note = "лист %d из %d   пишите тёмной ручкой между линиями" % (
        page_index + 1, max(1, len(spec.pages)))
    draw_text_vec(d, note, head_x, spec.grid_top - 5.5, 2.8, s,
                  (168, 168, 168), lw)

    for idx in range(min(len(chars), spec.capacity())):
        row, col = divmod(idx, spec.cols)
        ch = chars[idx]
        x0, y0, x1, y1 = spec.cell_rect(row, col)
        cw, chh = x1 - x0, y1 - y0

        d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], outline=FRAME, width=lw)

        def hline(frac, color, dash=0):
            y = (y0 + frac * chh) * s
            if dash <= 0:
                d.line([x0 * s, y, x1 * s, y], fill=color, width=lw)
            else:
                step = dash * s
                xx = x0 * s
                on = True
                while xx < x1 * s:
                    nx = min(xx + step, x1 * s)
                    if on:
                        d.line([xx, y, nx, y], fill=color, width=lw)
                    xx, on = nx, not on

        hline(F_CAP, GUIDE_SOFT, 1.4)
        hline(F_XH, GUIDE_SOFT, 1.0)
        hline(F_BASE, GUIDE)                       # базовая — сплошная
        hline(F_DESC, GUIDE_SOFT, 1.4)

        # подпись: какую букву писать
        draw_text_vec(d, ch, x0 + LABEL_BOX[0] * cw + 0.4,
                      y0 + LABEL_BOX[3] * chh - 0.4, 3.0, s, LABEL, lw)

        # бледный образец начертания — писать поверх него не нужно,
        # он стоит правее подписи, у левого края строки
        if show_reference and FV.has_glyph(ch):
            cap_mm = (F_BASE - F_CAP) * chh
            scale = cap_mm / FV.CAP * s
            adv, polys = FV.get_glyph(ch)
            gx = (x0 + 0.30 * cw) * s
            gy = (y0 + F_BASE * chh) * s
            _draw_polys(d, polys, gx, gy, scale, GHOST, lw)

    return im


def make_template(chars, spec=None, dpi=200, variants=3, show_reference=True):
    """-> (spec, [PIL.Image, ...])"""
    spec = spec or TemplateSpec()
    spec.variants = variants
    plan_pages(chars, spec)
    pages = [render_page(spec, i, dpi=dpi, show_reference=show_reference)
             for i in range(len(spec.pages))]
    return spec, pages


def save_template(chars, out_dir, spec=None, dpi=200, variants=3,
                  pdf=True, show_reference=True):
    """Сохранить шаблон: PNG на каждую страницу, общий PDF и spec.json."""
    os.makedirs(out_dir, exist_ok=True)
    spec, pages = make_template(chars, spec, dpi, variants, show_reference)
    files = []
    for i, im in enumerate(pages):
        p = os.path.join(out_dir, "propis_%02d.png" % (i + 1))
        im.save(p, dpi=(dpi, dpi))
        files.append(p)
    if pdf and pages:
        pdf_path = os.path.join(out_dir, "propis.pdf")
        pages[0].save(pdf_path, "PDF", resolution=dpi,
                      save_all=True, append_images=pages[1:])
        files.append(pdf_path)
    spec_path = os.path.join(out_dir, "propis_spec.json")
    spec.save(spec_path)
    files.append(spec_path)
    return spec, files


# ----------------------------------------------------- чтение с фотографии

def _find_markers(gray, spec, tol_lo=0.30, tol_hi=3.0, keep=24):
    """
    Найти чёрные квадраты-реперы.

    Ожидаемый размер репера считается из геометрии листа в предположении,
    что лист занимает почти весь кадр; допуск широкий (0.3x..3x), так что
    снимок «с запасом полей» или с небольшим наклоном тоже подойдёт.

    -> список (cx, cy, сторона, «похожесть на репер»), лучшие первыми.
    """
    from .vectorize import flatten_illumination
    h, w = gray.shape
    norm = flatten_illumination(gray)
    bw = ((norm < 0.55) * 255).astype(np.uint8)
    if cv2 is not None:
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    long_px = float(max(w, h))
    long_mm = float(max(spec.sheet_w, spec.sheet_h))
    expect = spec.marker_size / long_mm * long_px      # ожидаемая сторона, px
    lo, hi = expect * tol_lo, expect * tol_hi

    n, lab, stats, cent = cv2.connectedComponentsWithStats(bw, 8)
    out = []
    for i in range(1, n):
        _, _, bwid, bhei, a = stats[i]
        if bwid < 3 or bhei < 3:
            continue
        side = math.sqrt(float(a))
        if not (lo <= side <= hi):
            continue
        ar = bwid / float(bhei)
        if not (0.62 < ar < 1.62):
            continue
        fill = a / float(bwid * bhei)
        if fill < 0.68:                               # репер залит целиком
            continue
        # чем ближе к ожидаемому размеру, квадрату и полной заливке — тем лучше
        score = (fill
                 - abs(math.log(side / expect)) * 0.6
                 - abs(math.log(ar)) * 0.5)
        out.append((float(cent[i][0]), float(cent[i][1]), side, score))
    out.sort(key=lambda t: -t[3])
    return out[:keep]


def _pick_corner_markers(cand):
    """Из кандидатов выбрать четвёрку, наиболее разнесённую по углам кадра."""
    if len(cand) < 4:
        return None
    pts = [(c[0], c[1]) for c in cand]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    picks = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        best, bi = None, None
        for i, (x, y) in enumerate(pts):
            v = sx * (x - cx) + sy * (y - cy)
            if best is None or v > best:
                best, bi = v, i
        picks.append(bi)
    if len(set(picks)) < 4:
        return None
    return [pts[i] for i in picks]


def _order_corners(pts):
    """4 точки -> порядок ЛВ, ПВ, ПН, ЛН."""
    p = np.array(pts, dtype=np.float32)
    ssum = p[:, 0] + p[:, 1]
    sdif = p[:, 0] - p[:, 1]
    return np.array([p[np.argmin(ssum)], p[np.argmax(sdif)],
                     p[np.argmax(ssum)], p[np.argmin(sdif)]], dtype=np.float32)


def detect_sheet(img, spec, work_dpi=200, auto_orient=True):
    """
    Фото листа -> выпрямленное изображение всей страницы в масштабе work_dpi.

    Возвращает (rectified_image, info). info["rotations"] — сколько раз
    пришлось довернуть на 90°, info["markers"] — найденные реперы.
    """
    if cv2 is None:
        raise RuntimeError("для чтения фото нужен opencv (pip install opencv-python)")
    from .vectorize import to_gray
    if hasattr(img, "getexif"):
        # телефон часто сохраняет снимок «боком» с пометкой о повороте
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    gray = to_gray(img)
    cand = _find_markers(gray, spec)
    quad = _pick_corner_markers(cand)
    if quad is None:
        raise ValueError("на фото найдено меньше четырёх реперов (%d). "
                         "Снимите лист целиком, ровнее и без бликов." % len(cand))
    corners = _order_corners(quad)

    s = work_dpi / MM
    W = int(round(spec.sheet_w * s))
    H = int(round(spec.sheet_h * s))
    dst = np.array([(x * s, y * s) for (x, y) in spec.marker_centers()],
                   dtype=np.float32)

    best = None
    for rot in range(4 if auto_orient else 1):
        src = np.roll(corners, -rot, axis=0).astype(np.float32)
        M = cv2.getPerspectiveTransform(src, dst)
        warp = cv2.warpPerspective(gray, M, (W, H),
                                   flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT,
                                   borderValue=255)
        score = _orient_score(warp, spec, s)
        if best is None or score > best[0]:
            best = (score, rot, warp)
        if not auto_orient:
            break

    score, rot, warp = best
    return warp, {"rotations": rot, "orient_score": score,
                  "markers": cand[:4], "px_per_mm": s}


def _orient_score(warp, spec, s):
    """Насколько темно там, где должен быть пятый (ориентирующий) репер."""
    ox, oy = spec.orient_center()
    r = max(2, int(spec.orient_size * s * 0.6))
    x, y = int(ox * s), int(oy * s)
    h, w = warp.shape
    x0, x1 = max(0, x - r), min(w, x + r)
    y0, y1 = max(0, y - r), min(h, y + r)
    if x1 <= x0 or y1 <= y0:
        return -1.0
    patch = warp[y0:y1, x0:x1]
    return float(255.0 - patch.mean())


def _line_offset(prof, centre):
    """
    Найти в профиле тёмную линию рядом с ожидаемым местом.
    prof — медианная «темнота» поперёк линии, centre — где линия должна
    быть по гомографии. -> (сдвиг в пикселях, контраст) или None, если
    линии не видно (тогда остаёмся при расчётном положении).
    """
    if len(prof) < 5:
        return None
    i = int(np.argmax(prof))
    peak = float(prof[i])
    contrast = peak - float(np.median(prof))
    # рамка печатается светло-серым (~0.84 от бумаги): ждём провал хотя бы 3 %
    if contrast < 0.03:
        return None
    # уточняем вершину параболой по трём точкам
    x = float(i)
    if 0 < i < len(prof) - 1:
        a, b, c = prof[i - 1], prof[i], prof[i + 1]
        den = a - 2 * b + c
        if abs(den) > 1e-9:
            x = i + 0.5 * (a - c) / den
    return x - centre, contrast


# наклоны линии рамки, которые перебираем (пикселей на пиксель)
_SLOPES = np.linspace(-0.08, 0.08, 17)


def _find_line(dark, a0, a1, across, reach, horizontal):
    """
    Линия рамки между a0 и a1 вдоль неё, около координаты across поперёк.

    Изгиб бумаги наклоняет рамку внутри ячейки на несколько пикселей;
    медиана по строке тогда размазывает линию, и её не видно. Поэтому
    перебираем наклоны и берём тот, где линия контрастнее всего.
    -> (сдвиг середины отрезка, наклон) или None.
    """
    lim = dark.shape[0] if horizontal else dark.shape[1]
    a = np.arange(int(a0), int(a1))
    if len(a) < 8:
        return None
    mid = 0.5 * (a[0] + a[-1])
    base = int(round(across - reach))
    span = np.arange(int(2 * reach) + 1)[:, None]
    best = None
    for k in _SLOPES:
        idx = base + span + np.round(k * (a - mid)).astype(int)[None, :]
        if idx.min() < 0 or idx.max() >= lim:
            continue
        vals = dark[idx, a[None, :]] if horizontal else dark[a[None, :], idx]
        res = _line_offset(np.median(vals, axis=1), across - base)
        if res and (best is None or res[1] > best[2]):
            best = (res[0], float(k), res[1])
    return None if best is None else (best[0], best[1])


def _grid_offsets(rect, spec, px_per_mm, search_mm=2.6, passes=4):
    """
    Подогнать сетку ячеек по реальным рамкам на фото.

    Гомография по четырём реперам точна только для идеально плоского
    листа. Стоит бумаге чуть выгнуться — и ячейки в середине съезжают
    на миллиметр-два, а вместе с ними базовая линия: буквы шрифта
    «прыгают» по высоте и выходят кривыми. Поэтому каждую линию сетки
    ищем заново — отдельно для каждого её отрезка между соседними
    ячейками, вместе с наклоном, так что учитывается изгиб, а не только
    сдвиг.

    -> (dy, sy, dx, sx): dy[r][c] — сдвиг середины горизонтальной линии r
       на отрезке столбца c, sy — её наклон; dx/sx — то же для
       вертикальной линии c на отрезке строки r. Всё в пикселях.
    """
    from .vectorize import flatten_illumination
    dark = 1.0 - flatten_illumination(rect, lift=max(9, int(1.2 * px_per_mm)))
    s = px_per_mm
    rows, cols = spec.rows, spec.cols
    reach = search_mm * s
    # сильный изгиб уводит середину листа дальше окна поиска; поэтому
    # ищем в несколько проходов, каждый раз вокруг того места, куда
    # указывают уже найденные соседи, — поправка «доползает» от краёв
    pdy = [[0.0] * cols for _ in range(rows + 1)]
    pdx = [[0.0] * (cols + 1) for _ in range(rows)]
    psy = [[0.0] * cols for _ in range(rows + 1)]
    psx = [[0.0] * (cols + 1) for _ in range(rows)]
    for _ in range(max(1, passes)):
        dy = [[None] * cols for _ in range(rows + 1)]
        sy = [[None] * cols for _ in range(rows + 1)]
        dx = [[None] * (cols + 1) for _ in range(rows)]
        sx = [[None] * (cols + 1) for _ in range(rows)]
        for r in range(rows + 1):
            for c in range(cols):
                y = (spec.grid_top + r * spec.cell_h) * s + pdy[r][c]
                xa = (spec.grid_left + (c + 0.12) * spec.cell_w) * s
                xb = (spec.grid_left + (c + 0.88) * spec.cell_w) * s
                got = _find_line(dark, xa, xb, y, reach, True)
                if got:
                    dy[r][c], sy[r][c] = pdy[r][c] + got[0], got[1]
        for c in range(cols + 1):
            for r in range(rows):
                x = (spec.grid_left + c * spec.cell_w) * s + pdx[r][c]
                ya = (spec.grid_top + (r + 0.12) * spec.cell_h) * s
                yb = (spec.grid_top + (r + 0.88) * spec.cell_h) * s
                got = _find_line(dark, ya, yb, x, reach, False)
                if got:
                    dx[r][c], sx[r][c] = pdx[r][c] + got[0], got[1]
        ndy, ndx = _fill_offsets(dy, 3.0), _fill_offsets(dx, 3.0)
        moved = max(max(abs(a - b) for ra, rb in zip(ndy, pdy) for a, b in zip(ra, rb)),
                    max(abs(a - b) for ra, rb in zip(ndx, pdx) for a, b in zip(ra, rb)))
        pdy, pdx = ndy, ndx
        psy, psx = _fill_offsets(sy, 0.03), _fill_offsets(sx, 0.03)
        if moved < 0.3:
            break
    return pdy, psy, pdx, psx


def _fill_offsets(grid, tol):
    """
    Где линию не нашли (ячейка пустая по краю, блик), подставить среднее
    по найденным соседям; совсем не нашли — медиану по листу. Заодно
    отбрасываем выбросы (отличие от соседей больше tol): штрих буквы,
    случайно попавший в полосу поиска, не должен тянуть за собой ячейку.
    """
    n, m = len(grid), len(grid[0]) if grid else 0
    vals = [v for row in grid for v in row if v is not None]
    if not vals:
        return [[0.0] * m for _ in range(n)]
    out = [[None] * m for _ in range(n)]
    for i in range(n):
        for j in range(m):
            nb = [grid[a][b] for a in range(max(0, i - 1), min(n, i + 2))
                  for b in range(max(0, j - 1), min(m, j + 2))
                  if grid[a][b] is not None and (a, b) != (i, j)]
            v = grid[i][j]
            if nb:
                med = float(np.median(nb))
                if v is None or (len(nb) >= 3 and abs(v - med) > tol):
                    v = med
            out[i][j] = float(v) if v is not None else float(np.median(vals))
    return out


def _best_shift(a, b, maxs):
    """Сдвиг b относительно a (в пикселях), при котором профили совпадают лучше."""
    a = a - a.mean()
    b = b - b.mean()
    scores = {k: float(np.dot(a, np.roll(b, k))) for k in range(-maxs, maxs + 1)}
    k = max(scores, key=scores.get)
    # сдвигаем, только если совпадение заметно лучше, чем без сдвига
    if k and scores[k] <= scores[0] * 1.05 + 1e-9:
        return 0
    return k


def _align_template(cell, tcell, maxs=3):
    """
    Досадить кусок шаблона на ячейку фото с точностью до пикселя.

    Подгонка по рамкам оставляет 1–2 px на реальном листе и больше — на
    сильно выгнутом; шаблон служит маской «здесь напечатано, это не ручка»,
    и промах мимо линии выпускал её в букву. Ищем сдвиг по совпадению
    напечатанных линий, а перо (самое тёмное) из расчёта исключаем.
    """
    from .vectorize import ink_map
    norm = ink_map(cell)
    dark = 1.0 - norm
    dark[norm < 0.35] = float(np.median(dark))
    tpl = 1.0 - tcell.astype(np.float32) / 255.0
    # только по вертикали и по медиане строк: её задают горизонтальные
    # линии разлиновки во всю ширину ячейки. По горизонтали сдвиг сбивали
    # вертикальные штрихи самой буквы — шаблон «прилипал» бледным образцом
    # к букве, обведённой поверх него, и она стиралась вместе с ним
    dy = _best_shift(np.median(dark, axis=1), np.median(tpl, axis=1), maxs)
    if not dy:
        return tcell
    T = np.float32([[1, 0, 0], [0, 1, dy]])
    return cv2.warpAffine(tcell, T, (tcell.shape[1], tcell.shape[0]),
                          flags=cv2.INTER_NEAREST,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def _corner(offs, r, c, spec, s, axis):
    """
    Поправка в угле сетки (r, c): среднее по двум отрезкам линии, которые
    сходятся в этот угол, — каждый продолжен до угла со своим наклоном.
    """
    dy, sy, dx, sx = offs
    if axis == "y":                     # горизонтальная линия r
        half = 0.5 * spec.cell_w * s
        vals = [dy[r][j] + sy[r][j] * (half if j == c - 1 else -half)
                for j in (c - 1, c) if 0 <= j < len(dy[r])]
    else:                               # вертикальная линия c
        half = 0.5 * spec.cell_h * s
        vals = [dx[i][c] + sx[i][c] * (half if i == r - 1 else -half)
                for i in (r - 1, r) if 0 <= i < len(dx)]
    return float(sum(vals) / len(vals)) if vals else 0.0


def slice_cells(rect, spec, page_index, px_per_mm, pad=0.06, refine=True,
                with_template=True):
    """
    Выпрямленный лист -> список ячеек.

    Каждый элемент: dict с ключами
        char, variant, image (numpy uint8), baseline, cap  (в пикселях ячейки)
        template — та же ячейка пустого шаблона (что было напечатано до
                   того, как по листу прошлась ручка), или None

    refine — подогнать каждую ячейку по её напечатанной рамке (см.
    _grid_offsets). Без этого изогнутый лист даёт кривые буквы.

    with_template — приложить к ячейке кусок пустого шаблона. На реальной
    печати разлиновка, рамки и бледные образцы выходят темнее задуманного,
    и один порог по яркости их не отсекает; зная, где именно шаблон
    напечатан, векторизация отличает ручку от печати (vectorize.
    suppress_template).
    """
    chars = spec.pages[page_index] if page_index < len(spec.pages) else []
    seen = {}
    out = []
    H, W = rect.shape[:2]
    offs = _grid_offsets(rect, spec, px_per_mm) if refine else None
    tpl = None
    if with_template:
        tpl = np.asarray(render_page(spec, page_index,
                                     dpi=px_per_mm * MM).convert("L"))
    for idx in range(min(len(chars), spec.capacity())):
        row, col = divmod(idx, spec.cols)
        ch = chars[idx]
        v = seen.get(ch, 0)
        seen[ch] = v + 1

        x0, y0, x1, y1 = spec.cell_rect(row, col)
        cw, chh = x1 - x0, y1 - y0
        if offs is not None:
            # ячейку выпрямляем отдельно по её четырём углам: так
            # учитывается не только сдвиг, но и местный поворот и перекос
            # изогнутой бумаги
            src = np.float32([
                (x0 * px_per_mm + _corner(offs, row, col, spec, px_per_mm, "x"),
                 y0 * px_per_mm + _corner(offs, row, col, spec, px_per_mm, "y")),
                (x1 * px_per_mm + _corner(offs, row, col + 1, spec, px_per_mm, "x"),
                 y0 * px_per_mm + _corner(offs, row, col + 1, spec, px_per_mm, "y")),
                (x1 * px_per_mm + _corner(offs, row + 1, col + 1, spec, px_per_mm, "x"),
                 y1 * px_per_mm + _corner(offs, row + 1, col + 1, spec, px_per_mm, "y")),
                (x0 * px_per_mm + _corner(offs, row + 1, col, spec, px_per_mm, "x"),
                 y1 * px_per_mm + _corner(offs, row + 1, col, spec, px_per_mm, "y"))])
            cwp, chp = cw * px_per_mm, chh * px_per_mm
            dst = np.float32([(0, 0), (cwp, 0), (cwp, chp), (0, chp)])
            M = cv2.getPerspectiveTransform(src, dst)
            full = cv2.warpPerspective(rect, M, (int(round(cwp)), int(round(chp))),
                                       flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_CONSTANT,
                                       borderValue=255)
            ox, oy = 0.0, 0.0
            tfull = None
            if tpl is not None:
                # шаблон уже в «правильной» геометрии: просто сдвигаем
                T = np.float32([[1, 0, -x0 * px_per_mm], [0, 1, -y0 * px_per_mm]])
                tfull = cv2.warpAffine(tpl, T, (full.shape[1], full.shape[0]),
                                       flags=cv2.INTER_LINEAR,
                                       borderMode=cv2.BORDER_CONSTANT,
                                       borderValue=255)
        else:
            full, ox, oy = rect, x0, y0
            tfull = tpl
        fh, fw = full.shape[:2]
        # чуть отступаем внутрь, чтобы не захватить рамку ячейки
        px0 = int(round((ox + pad * cw) * px_per_mm))
        px1 = int(round((ox + cw - pad * cw) * px_per_mm))
        py0 = int(round((oy + pad * chh) * px_per_mm))
        py1 = int(round((oy + chh - pad * chh) * px_per_mm))
        px0, py0 = max(0, px0), max(0, py0)
        px1, py1 = min(fw, px1), min(fh, py1)
        if px1 - px0 < 8 or py1 - py0 < 8:
            continue
        cell = full[py0:py1, px0:px1].copy()
        tcell = tfull[py0:py1, px0:px1].copy() if tfull is not None else None
        if tcell is not None and cv2 is not None:
            tcell = _align_template(cell, tcell)

        # затираем зону подписи-образца, чтобы она не попала в векторизацию
        lx1 = int(round((LABEL_BOX[2] * cw - pad * cw) * px_per_mm))
        ly1 = int(round((LABEL_BOX[3] * chh - pad * chh) * px_per_mm))
        if lx1 > 0 and ly1 > 0:
            if tcell is not None:
                # подпись, пропечатанная темнее задуманного, расползается за
                # край зоны на пару пикселей и становится тёмной, как ручка.
                # В полосе шириной 1 мм вокруг зоны стираем только то, что
                # сам шаблон там напечатал, — штрихи ручки не трогаем
                m = int(round(1.0 * px_per_mm))
                ey, ex = min(cell.shape[0], ly1 + m), min(cell.shape[1], lx1 + m)
                printed = tcell[:ey, :ex] < 245
                if cv2 is not None and printed.any():
                    printed = cv2.dilate(printed.astype(np.uint8),
                                         np.ones((5, 5), np.uint8)).astype(bool)
                cell[:ey, :ex][printed] = 255
                tcell[:max(0, ly1), :max(0, lx1)] = 255
                tcell[:ey, :ex][printed] = 255
            cell[:max(0, ly1), :max(0, lx1)] = 255

        def gy(frac):
            return (frac * chh - pad * chh) * px_per_mm

        out.append({"char": ch, "variant": v, "image": cell, "template": tcell,
                    "baseline": gy(F_BASE), "cap": gy(F_CAP),
                    "guides": [gy(F_CAP), gy(F_XH), gy(F_BASE), gy(F_DESC)],
                    "row": row, "col": col, "index": idx})
    return out
