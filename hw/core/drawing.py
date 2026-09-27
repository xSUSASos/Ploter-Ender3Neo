# -*- coding: utf-8 -*-
"""
Рисунки пером: картинка, SVG или эскиз мышью -> штрихи в миллиметрах листа.

Источники:
    * картинка (PNG/JPG/...) — превращается в линии одним из режимов:
        outline       контуры тёмных областей
        centerline    осевая линия (линейные рисунки, раскраски, подписи)
        edges         края (фотографии)
        hatch         штриховка по тону, от 1 до 4 направлений
        outline+hatch контур и штриховка вместе
    * SVG — линии берутся как есть (path, line, polyline, polygon, rect,
      circle, ellipse, use), с учётом transform;
    * эскиз — линии, нарисованные мышью прямо на листе; они уже в мм листа
      и не двигаются настройками размещения.

Картинка и SVG живут в своих координатах (Y вниз). place_transform()
вписывает их в поля листа с поворотом, отражением и привязкой к краю;
штриховка строится с учётом этого преобразования, поэтому шаг и угол
штриховки задаются прямо на бумаге, в миллиметрах и градусах.
"""

import math
import os
import random
import re
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageOps

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

from scipy import ndimage

from . import vectorize as VZ
from . import humanize as HM

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")
SVG_EXT = (".svg",)

MODES = {
    "outline": "контур",
    "centerline": "осевая линия",
    "edges": "края (фото)",
    "hatch": "штриховка",
    "outline+hatch": "контур + штриховка",
}
LAYOUTS = {
    "over": "поверх текста",
    "above": "сверху, текст под ним",
    "alone": "только рисунок",
}
ANCHORS = {
    "center": "по центру",
    "top": "вверху",
    "bottom": "внизу",
    "left": "слева",
    "right": "справа",
    "top-left": "вверху слева",
    "top-right": "вверху справа",
    "bottom-left": "внизу слева",
    "bottom-right": "внизу справа",
}


# ------------------------------------------------------------ геометрия

def path_length(p):
    return sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))


def bbox_of(paths):
    pts = [q for p in paths for q in p]
    if not pts:
        return None
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ================================================================== SVG

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SKIP_TAGS = {"defs", "clipPath", "mask", "symbol", "marker", "pattern",
              "style", "script", "title", "desc", "metadata", "text",
              "linearGradient", "radialGradient", "filter", "foreignObject"}


class _Scan:
    """Разбор строки d: числа и флаги дуг могут идти без разделителей."""

    def __init__(self, s):
        self.s, self.i, self.n = s, 0, len(s)

    def _skip(self):
        while self.i < self.n and self.s[self.i] in " \t\r\n,":
            self.i += 1

    def cmd(self):
        self._skip()
        if self.i < self.n and self.s[self.i].isalpha():
            self.i += 1
            return self.s[self.i - 1]
        return None

    def has_num(self):
        self._skip()
        return self.i < self.n and (self.s[self.i] in "+-." or self.s[self.i].isdigit())

    def num(self):
        self._skip()
        m = _NUM.match(self.s, self.i)
        if not m:
            raise ValueError("ожидалось число в позиции %d" % self.i)
        self.i = m.end()
        return float(m.group())

    def flag(self):
        self._skip()
        if self.i >= self.n or self.s[self.i] not in "01":
            raise ValueError("ожидался флаг дуги в позиции %d" % self.i)
        self.i += 1
        return self.s[self.i - 1] == "1"


def _cubic(p0, p1, p2, p3, n=24):
    out = []
    for k in range(1, n + 1):
        t = k / n
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _quad(p0, p1, p2, n=16):
    out = []
    for k in range(1, n + 1):
        t = k / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _arc(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """Эллиптическая дуга SVG (конечные точки) -> ломаная. Спецификация SVG, F.6.5."""
    if (x1, y1) == (x2, y2):
        return []
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    rx, ry = abs(rx), abs(ry)
    ph = math.radians(phi)
    cp, sp = math.cos(ph), math.sin(ph)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2.0
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2.0

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    t1 = ang(1.0, 0.0, ux, uy)
    dt = ang(ux, uy, vx, vy)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(4, int(math.ceil(abs(dt) / (math.pi / 24))))
    out = []
    for k in range(1, n + 1):
        t = t1 + dt * k / n
        px, py = rx * math.cos(t), ry * math.sin(t)
        out.append((cp * px - sp * py + cx, sp * px + cp * py + cy))
    out[-1] = (x2, y2)
    return out


def parse_path(d):
    """Атрибут d элемента <path> -> список ломаных."""
    sc = _Scan(d or "")
    paths, cur = [], []
    x = y = sx = sy = 0.0
    cmd = None
    prev = ""                       # предыдущая команда — для S и T
    ctrl = None                     # последняя контрольная точка

    def flush():
        if len(cur) > 1:
            paths.append(list(cur))

    while True:
        c = sc.cmd()
        if c is None:
            if cmd is None or not sc.has_num():
                break
            c = cmd
        rel = c.islower()
        C = c.upper()
        if C != "Z" and not cur:
            cur = [(x, y)]
        if C == "M":
            nx, ny = sc.num(), sc.num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            flush()
            cur = [(x, y)]
            sx, sy = x, y
            cmd = "l" if rel else "L"   # дальнейшие пары — это линии
        elif C == "L":
            nx, ny = sc.num(), sc.num()
            x, y = (x + nx, y + ny) if rel else (nx, ny)
            cur.append((x, y))
            cmd = c
        elif C == "H":
            v = sc.num()
            x = x + v if rel else v
            cur.append((x, y))
            cmd = c
        elif C == "V":
            v = sc.num()
            y = y + v if rel else v
            cur.append((x, y))
            cmd = c
        elif C in "CS":
            if C == "C":
                a = [sc.num() for _ in range(6)]
                if rel:
                    a = [a[i] + (x if i % 2 == 0 else y) for i in range(6)]
                p1, p2, p3 = (a[0], a[1]), (a[2], a[3]), (a[4], a[5])
            else:
                a = [sc.num() for _ in range(4)]
                if rel:
                    a = [a[i] + (x if i % 2 == 0 else y) for i in range(4)]
                p1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) \
                    if prev in "CS" and ctrl else (x, y)
                p2, p3 = (a[0], a[1]), (a[2], a[3])
            cur.extend(_cubic((x, y), p1, p2, p3))
            ctrl = p2
            x, y = p3
            cmd = c
        elif C in "QT":
            if C == "Q":
                a = [sc.num() for _ in range(4)]
                if rel:
                    a = [a[i] + (x if i % 2 == 0 else y) for i in range(4)]
                p1, p2 = (a[0], a[1]), (a[2], a[3])
            else:
                a = [sc.num(), sc.num()]
                if rel:
                    a = [a[0] + x, a[1] + y]
                p1 = (2 * x - ctrl[0], 2 * y - ctrl[1]) \
                    if prev in "QT" and ctrl else (x, y)
                p2 = (a[0], a[1])
            cur.extend(_quad((x, y), p1, p2))
            ctrl = p1
            x, y = p2
            cmd = c
        elif C == "A":
            rx, ry, phi = sc.num(), sc.num(), sc.num()
            fa, fs = sc.flag(), sc.flag()
            nx, ny = sc.num(), sc.num()
            if rel:
                nx, ny = x + nx, y + ny
            cur.extend(_arc(x, y, rx, ry, phi, fa, fs, nx, ny))
            x, y = nx, ny
            cmd = c
        elif C == "Z":
            if cur and cur[-1] != (sx, sy):
                cur.append((sx, sy))
            flush()
            cur = []
            x, y = sx, sy
            cmd = None
        else:
            raise ValueError("неизвестная команда пути: %s" % c)
        if C not in "CSQT":
            ctrl = None
        prev = C
    flush()
    return paths


def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def parse_transform(s):
    """Атрибут transform -> матрица (a, b, c, d, e, f)."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(
            r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s or ""):
        v = [float(t) for t in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mul(m, t)
    return m


def _len(el, name, default=0.0):
    v = el.get(name)
    if v is None:
        return default
    m = _NUM.match(v.strip())
    return float(m.group()) if m else default


def _tag(el):
    t = el.tag
    return t.rsplit("}", 1)[-1] if isinstance(t, str) else ""


def _hidden(el):
    if el.get("display") == "none" or el.get("visibility") == "hidden":
        return True
    st = (el.get("style") or "").replace(" ", "")
    return "display:none" in st or "visibility:hidden" in st


def _ellipse(cx, cy, rx, ry, n=72):
    return [(cx + rx * math.cos(2 * math.pi * k / n),
             cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]


def _shape(el, tag):
    if tag == "path":
        return parse_path(el.get("d"))
    if tag == "line":
        return [[(_len(el, "x1"), _len(el, "y1")), (_len(el, "x2"), _len(el, "y2"))]]
    if tag in ("polyline", "polygon"):
        v = [float(t) for t in _NUM.findall(el.get("points") or "")]
        pts = list(zip(v[0::2], v[1::2]))
        if tag == "polygon" and len(pts) > 2:
            pts.append(pts[0])
        return [pts] if len(pts) > 1 else []
    if tag == "rect":
        x, y = _len(el, "x"), _len(el, "y")
        w, h = _len(el, "width"), _len(el, "height")
        if w <= 0 or h <= 0:
            return []
        return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]
    if tag == "circle":
        r = _len(el, "r")
        return [_ellipse(_len(el, "cx"), _len(el, "cy"), r, r)] if r > 0 else []
    if tag == "ellipse":
        rx, ry = _len(el, "rx"), _len(el, "ry")
        return [_ellipse(_len(el, "cx"), _len(el, "cy"), rx, ry)] if rx > 0 and ry > 0 else []
    return []


def load_svg(path):
    """SVG-файл -> список ломаных в координатах SVG (Y вниз)."""
    root = ET.parse(path).getroot()
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    out = []

    def walk(el, m, depth):
        if depth > 40:
            return
        tag = _tag(el)
        if tag in _SKIP_TAGS or _hidden(el):
            return
        m = _mul(m, parse_transform(el.get("transform")))
        if tag == "use":
            href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
            ref = ids.get(href.lstrip("#"))
            if ref is not None and ref is not el:
                t = _mul(m, (1, 0, 0, 1, _len(el, "x"), _len(el, "y")))
                walk_children = _tag(ref) in ("symbol", "g", "svg")
                if walk_children:
                    for ch in ref:
                        walk(ch, t, depth + 1)
                else:
                    walk(ref, t, depth + 1)
            return
        a, b, c, d, e, f = m
        for p in _shape(el, tag):
            out.append([(a * x + c * y + e, b * x + d * y + f) for (x, y) in p])
        for ch in el:
            walk(ch, m, depth + 1)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 0)
    return [p for p in out if len(p) > 1]


# ============================================================== картинка

def load_gray(path, max_side=2400):
    """Картинка -> яркость float32 0..1 (1 — белое), прозрачное — белое."""
    im = Image.open(path)
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:                                   # noqa: BLE001
        pass
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert("L")
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    return np.asarray(im, np.float32) / 255.0


def hatch_mask(mask, direction, step, min_len=1.0, sample=0.5):
    """
    Заштриховать маску параллельными линиями.

    direction — единичный вектор линий (в пикселях маски), step — шаг
    между линиями, px. Соседние линии идут навстречу друг другу, чтобы
    перо не возвращалось на холостом ходу через весь рисунок.
    """
    h, w = mask.shape
    if step <= 0 or not mask.any():
        return []
    dx, dy = direction
    nx, ny = -dy, dx
    corners = ((0, 0), (w, 0), (0, h), (w, h))
    pn = [x * nx + y * ny for x, y in corners]
    pd = [x * dx + y * dy for x, y in corners]
    t = np.arange(min(pd) - 1, max(pd) + 1, sample)
    out = []
    k = 0
    for off in np.arange(min(pn) + step / 2.0, max(pn), step):
        xs = off * nx + t * dx
        ys = off * ny + t * dy
        xi = np.rint(xs).astype(np.int64)
        yi = np.rint(ys).astype(np.int64)
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        if not ok.any():
            continue
        v = np.zeros(len(t), np.int8)
        v[ok] = mask[yi[ok], xi[ok]]
        dv = np.diff(np.concatenate(([0], v, [0])))
        starts = np.flatnonzero(dv == 1)
        ends = np.flatnonzero(dv == -1) - 1
        segs = [[(float(xs[a]), float(ys[a])), (float(xs[b]), float(ys[b]))]
                for a, b in zip(starts, ends) if (b - a) * sample >= min_len]
        if not segs:
            continue
        if k % 2:
            segs = [s[::-1] for s in reversed(segs)]
        out.extend(segs)
        k += 1
    return out


class ArtSource:
    """Картинка или SVG из файла. Тяжёлая обработка кешируется."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        self._cache = {}
        if ext in SVG_EXT:
            self.kind = "svg"
            self.svg = load_svg(path)
            if not self.svg:
                raise ValueError("в SVG не нашлось ни одной линии")
            self.gray = None
        else:
            self.kind = "image"
            self.gray = load_gray(path)
            self.svg = None

    @property
    def is_image(self):
        return self.kind == "image"

    def describe(self):
        if self.kind == "svg":
            return "%s — SVG, линий %d" % (self.name, len(self.svg))
        h, w = self.gray.shape
        return "%s — картинка %d×%d px" % (self.name, w, h)

    # --------------------------------------------------------- подготовка
    def tone(self, d):
        """Тон 0..1 (1 — бумага) в рабочем размере, с контрастом и размытием."""
        key = ("tone", int(d.detail), round(float(d.blur), 3),
               bool(d.autocontrast), bool(d.invert))
        if key in self._cache:
            return self._cache[key]
        g = self.gray
        side = max(64, int(d.detail))
        h, w = g.shape
        k = side / float(max(h, w))
        if abs(k - 1.0) > 1e-3:
            nw, nh = max(2, int(round(w * k))), max(2, int(round(h * k)))
            if cv2 is not None:
                g = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_AREA if k < 1
                               else cv2.INTER_CUBIC)
            else:
                g = np.asarray(Image.fromarray((g * 255).astype(np.uint8))
                               .resize((nw, nh), Image.LANCZOS), np.float32) / 255.0
        if d.invert:
            g = 1.0 - g
        if d.autocontrast:
            lo, hi = np.percentile(g, 1), np.percentile(g, 99.5)
            if hi - lo > 0.04:
                g = np.clip((g - lo) / (hi - lo), 0.0, 1.0)
        if d.blur > 0:
            g = ndimage.gaussian_filter(g, float(d.blur))
        g = g.astype(np.float32)
        self._cache[key] = g
        return g

    def mask(self, d, thr=None):
        thr = float(d.threshold if thr is None else thr)
        tone = self.tone(d)
        m = tone < thr
        area = max(4, int((max(tone.shape) / 350.0) ** 2 * 8))
        return VZ.clean_mask(m, min_area=area, close_holes=False) if m.any() else m

    # ------------------------------------------------------------ линии
    def base_paths(self, d):
        """
        -> (линии без штриховки, рамка для размещения) в своих координатах.
        Рамка у картинки — по тёмному содержимому, а не по кадру: белые
        поля вокруг рисунка на бумаге не нужны.
        """
        if self.kind == "svg":
            return self.svg, bbox_of(self.svg)
        mode = d.mode
        key = ("paths", mode, round(float(d.threshold), 4), int(d.detail),
               round(float(d.blur), 3), bool(d.autocontrast), bool(d.invert),
               round(float(d.edge_sens), 3))
        if key in self._cache:
            return self._cache[key]
        tone = self.tone(d)
        h, w = tone.shape
        paths = []
        if mode == "edges":
            paths = self._edges(tone, d)
            box = bbox_of(paths)
        else:
            m = self.mask(d)
            if mode in ("outline", "outline+hatch"):
                paths = self._outline(m)
            elif mode == "centerline":
                paths = VZ.vectorize_mask(m, min_branch=3, smooth=5,
                                          simplify_tol=0.5, merge_gap=2.0,
                                          min_path_len=3.0) if m.any() else []
            ys, xs = np.nonzero(m)
            box = (float(xs.min()), float(ys.min()), float(xs.max()),
                   float(ys.max())) if len(xs) else None
        if box is None or box[2] - box[0] < 1 and box[3] - box[1] < 1:
            box = (0.0, 0.0, float(w - 1), float(h - 1))
        res = (paths, box)
        self._cache[key] = res
        return res

    @staticmethod
    def _outline(m):
        if not m.any():
            return []
        if cv2 is None:
            raise RuntimeError("для контуров нужен opencv-python")
        cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_LIST,
                                 cv2.CHAIN_APPROX_NONE)
        out = []
        for c in cs:
            pts = [(float(p[0][0]), float(p[0][1])) for p in c]
            if len(pts) < 3:
                continue
            pts.append(pts[0])
            out.append(VZ.smooth_path(pts, window=5, passes=1))
        return out

    @staticmethod
    def _edges(tone, d):
        if cv2 is None:
            raise RuntimeError("для краёв нужен opencv-python")
        u8 = np.clip(tone * 255.0, 0, 255).astype(np.uint8)
        hi = 25.0 + (1.0 - float(d.edge_sens)) * 200.0
        e = cv2.Canny(u8, hi * 0.4, hi, L2gradient=True) > 0
        if not e.any():
            return []
        sk = VZ.thin(e)
        paths = VZ.trace_skeleton(sk, min_branch=3)
        paths = [VZ.smooth_path(p, window=3, passes=1) for p in paths]
        paths = VZ.merge_paths(paths, gap=1.5) if len(paths) < 3000 else paths
        return [p for p in paths if path_length(p) >= 4.0]

    def hatch(self, d, direction, step_px, min_px):
        """Штриховка по тонам: чем темнее место, тем больше направлений."""
        levels = max(1, min(4, int(d.hatch_levels)))
        key = ("hatch", levels, round(float(d.threshold), 4), int(d.detail),
               round(float(d.blur), 3), bool(d.autocontrast), bool(d.invert),
               round(direction[0], 5), round(direction[1], 5),
               round(step_px, 4), round(min_px, 3))
        if key in self._cache:
            return self._cache[key]
        extra = (0.0, 90.0, -45.0, 45.0)
        out = []
        for i in range(levels):
            thr = float(d.threshold) * (levels - i) / levels
            m = self.mask(d, thr)
            if not m.any():
                continue
            a = math.radians(extra[i])
            ca, sa = math.cos(a), math.sin(a)
            dv = (direction[0] * ca - direction[1] * sa,
                  direction[0] * sa + direction[1] * ca)
            out.extend(hatch_mask(m, dv, step_px, min_len=min_px))
        self._cache[key] = out
        return out


# ============================================================ размещение

class Placement:
    """Своё пространство (Y вниз) -> лист (мм, Y вверх)."""

    def __init__(self, unit, k, src_c, dst_c, box):
        self.unit = unit          # поворот·отражение·переворот Y, без масштаба
        self.k = k                # мм на единицу источника
        self.src_c = src_c
        self.dst_c = dst_c
        self.box = box            # (x0, y0, x1, y1) на листе

    def apply(self, paths):
        (a, b), (c, d) = self.unit
        k = self.k
        sx, sy = self.src_c
        tx, ty = self.dst_c
        out = []
        for p in paths:
            q = np.asarray(p, float)
            if len(q) < 2:
                continue
            x, y = q[:, 0] - sx, q[:, 1] - sy
            X = k * (a * x + b * y) + tx
            Y = k * (c * x + d * y) + ty
            out.append(list(zip(X.tolist(), Y.tolist())))
        return out

    def to_source_dir(self, deg):
        """Направление на бумаге (градусы) -> единичный вектор в источнике."""
        (a, b), (c, d) = self.unit
        px, py = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        # unit ортогональна: обратная — транспонированная
        vx, vy = a * px + c * py, b * px + d * py
        n = math.hypot(vx, vy) or 1.0
        return (vx / n, vy / n)


def place_transform(bbox, d, page):
    x0, y0, x1, y1 = bbox
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    r = math.radians(float(d.rotate))
    c, s = math.cos(r), math.sin(r)
    mx = -1.0 if d.mirror else 1.0
    unit = ((c * mx, s), (s * mx, -c))            # R · diag(mx, -1)
    W0 = abs(unit[0][0]) * w + abs(unit[0][1]) * h
    H0 = abs(unit[1][0]) * w + abs(unit[1][1]) * h

    ax0 = page.margin_left
    ax1 = ax0 + page.text_w
    ay0 = page.margin_bottom
    ay1 = page.sheet_h - page.margin_top
    aw, ah = max(ax1 - ax0, 1.0), max(ay1 - ay0, 1.0)
    if d.width_mm > 0 and d.height_mm > 0:
        k = min(d.width_mm / W0, d.height_mm / H0)
    elif d.width_mm > 0:
        k = d.width_mm / W0
    elif d.height_mm > 0:
        k = d.height_mm / H0
    else:
        # над текстом рисунок во весь лист не нужен: оставим место тексту
        k = min(aw / W0, (ah * 0.5 if d.layout == "above" else ah) / H0)
    W, H = W0 * k, H0 * k

    anchor = d.anchor or "center"
    if d.layout == "above":
        anchor = "top" + ("-left" if "left" in anchor else
                          "-right" if "right" in anchor else "")
    if "left" in anchor:
        tx = ax0 + W / 2.0
    elif "right" in anchor:
        tx = ax1 - W / 2.0
    else:
        tx = (ax0 + ax1) / 2.0
    if "top" in anchor:
        ty = ay1 - H / 2.0
    elif "bottom" in anchor:
        ty = ay0 + H / 2.0
    else:
        ty = (ay0 + ay1) / 2.0
    tx += float(d.offset_x)
    ty -= float(d.offset_y)
    return Placement(unit, k, ((x0 + x1) / 2.0, (y0 + y1) / 2.0), (tx, ty),
                     (tx - W / 2.0, ty - H / 2.0, tx + W / 2.0, ty + H / 2.0))


def set_placement(d, bbox, page, k, center):
    """
    Поставить картинку так, чтобы у неё был масштаб k (мм на единицу
    источника) и центр center на листе, — при текущих повороте, отражении
    и привязке. Меняет d.width_mm, d.height_mm, d.offset_x, d.offset_y:
    это то, что делает мышь, когда картинку тянут, растягивают и крутят.
    """
    x0, y0, x1, y1 = bbox
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    r = math.radians(float(d.rotate))
    c, s = math.cos(r), math.sin(r)
    W0 = abs(c) * w + abs(s) * h
    d.width_mm = max(0.5, k * W0)
    d.height_mm = 0.0
    d.offset_x = d.offset_y = 0.0
    pl = place_transform(bbox, d, page)
    d.offset_x = center[0] - pl.dst_c[0]
    d.offset_y = pl.dst_c[1] - center[1]


def transform_points(strokes, op):
    """
    Преобразовать штрихи (мм листа). op:
        ("move", dx, dy)        сдвиг
        ("scale", k, cx, cy)    масштаб вокруг точки
        ("rot", deg, cx, cy)    поворот против часовой вокруг точки
        ("mirror", cx)          отражение слева направо относительно x = cx
    """
    kind = op[0]
    if kind == "move":
        dx, dy = op[1], op[2]
        return [[(x + dx, y + dy) for x, y in s] for s in strokes]
    if kind == "scale":
        k, cx, cy = op[1], op[2], op[3]
        return [[(cx + (x - cx) * k, cy + (y - cy) * k) for x, y in s] for s in strokes]
    if kind == "rot":
        a = math.radians(op[1])
        ca, sa = math.cos(a), math.sin(a)
        cx, cy = op[2], op[3]
        return [[(cx + (x - cx) * ca - (y - cy) * sa,
                  cy + (x - cx) * sa + (y - cy) * ca) for x, y in s] for s in strokes]
    if kind == "mirror":
        cx = op[1]
        return [[(2 * cx - x, y) for x, y in s] for s in strokes]
    raise ValueError(kind)


def transform_art(d, source, page, op):
    """
    То же преобразование для картинки из файла: пересчитать её настройки
    размещения так, чтобы она повторила движение выделения.
    """
    paths, bbox = source.base_paths(d)
    pl = place_transform(bbox, d, page)
    k = pl.k
    cx, cy = transform_points([[pl.dst_c]], op)[0][0]
    kind = op[0]
    if kind == "scale":
        k *= op[1]
    elif kind == "rot":
        d.rotate = (float(d.rotate) + op[1] + 180.0) % 360.0 - 180.0
    elif kind == "mirror":
        # отражение повёрнутой картинки = повёрнутая в другую сторону отражённая
        d.mirror = not d.mirror
        d.rotate = -float(d.rotate)
    set_placement(d, bbox, page, k, (cx, cy))


def art_box(source, d, page):
    """Рамка картинки на листе (x0, y0, x1, y1) или None."""
    if source is None:
        return None
    _paths, bbox = source.base_paths(d)
    return place_transform(bbox, d, page).box


def art_corners(source, d, page):
    """Четыре угла картинки на листе — с поворотом, для рамки выделения."""
    _paths, bbox = source.base_paths(d)
    pl = place_transform(bbox, d, page)
    x0, y0, x1, y1 = bbox
    return pl.apply([[(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]])[0]


# ============================================================ доводка

def order_paths(paths, join=0.0, start=(0.0, 0.0)):
    """
    Жадно упорядочить штрихи: следующий — ближайший к концу текущего,
    при необходимости задом наперёд. Штрих, начинающийся ближе join мм,
    дописывается к текущему без подъёма пера.
    """
    paths = [p for p in paths if len(p) > 1]
    n = len(paths)
    if n < 2:
        return [list(p) for p in paths]
    S = np.array([p[0] for p in paths], float)
    E = np.array([p[-1] for p in paths], float)
    used = np.zeros(n, bool)
    cur = np.array(start, float)
    out = []
    for _ in range(n):
        ds = np.hypot(S[:, 0] - cur[0], S[:, 1] - cur[1])
        de = np.hypot(E[:, 0] - cur[0], E[:, 1] - cur[1])
        ds[used] = np.inf
        de[used] = np.inf
        i, j = int(ds.argmin()), int(de.argmin())
        if ds[i] <= de[j]:
            p, dist = paths[i], ds[i]
        else:
            i, p, dist = j, paths[j][::-1], de[j]
        used[i] = True
        if out and dist <= join:
            out[-1].extend(p[1:] if dist < 1e-9 else p)
        else:
            out.append(list(p))
        cur = np.array(p[-1], float)
    return out


def finish(paths, d, seed=1):
    """Упрощение, отсев коротких, дрожание, повторные проходы, порядок."""
    out = []
    tol = float(d.simplify)
    for p in paths:
        if tol > 0 and len(p) > 2:
            p = VZ.rdp(p, tol)
        if len(p) >= 2 and path_length(p) >= float(d.min_len):
            out.append([(float(x), float(y)) for x, y in p])
    if d.tremor > 0 and out:
        out = HM.tremor(out, amp=float(d.tremor),
                        wavelength=max(2.0, 25.0 * float(d.tremor)),
                        rng=random.Random(seed))
    passes = max(1, int(d.passes))
    if passes > 1:
        more = []
        for p in out:
            q = list(p)
            for i in range(1, passes):
                q.extend((p[::-1] if i % 2 else p)[1:])
            more.append(q)
        out = more
    if d.optimize:
        out = order_paths(out, join=float(d.join_gap))
    return out


def build_art(source, sketch, d, page, seed=1):
    """
    Всё вместе: -> (штрихи в мм листа, рамка картинки на листе или None).
    source — ArtSource или None, sketch — штрихи эскиза в мм листа.
    """
    parts, box = [], None
    if source is not None:
        paths, bbox = source.base_paths(d)
        pl = place_transform(bbox, d, page)
        parts.extend(pl.apply(paths))
        if source.is_image and d.mode in ("hatch", "outline+hatch"):
            step = max(0.1, float(d.hatch_step)) / pl.k
            min_px = max(0.5, float(d.min_len) / pl.k)
            parts.extend(pl.apply(source.hatch(d, pl.to_source_dir(d.hatch_angle),
                                               step, min_px)))
        box = pl.box
    parts.extend([list(s) for s in (sketch or []) if len(s) > 1])
    return finish(parts, d, seed=seed), box


def text_skip(strokes, page, gap):
    """Насколько опустить первую строку, чтобы текст начался под рисунком."""
    ys = [q[1] for s in strokes for q in s]
    if not ys:
        return 0.0
    top = page.sheet_h - page.margin_top
    return max(0.0, top - min(ys) + float(gap))


# ============================================================== эскиз

def smooth_sketch(pts, window=5):
    """Линия с мыши -> сглаженная и упрощённая (мм)."""
    if len(pts) > 2 and window >= 3:
        pts = VZ.smooth_path(pts, window=int(window) | 1, passes=2, keep_ends=True)
    return VZ.rdp(pts, 0.04) if len(pts) > 2 else list(pts)


def rect_path(a, b):
    (x0, y0), (x1, y1) = a, b
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def ellipse_path(a, b, n=None):
    (x0, y0), (x1, y1) = a, b
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = abs(x1 - x0) / 2.0, abs(y1 - y0) / 2.0
    if n is None:
        n = max(24, min(180, int(2 * math.pi * max(rx, ry) / 0.8)))
    return _ellipse(cx, cy, rx, ry, n)


def dist_to_path(pt, path):
    """Расстояние от точки до ломаной."""
    best = float("inf")
    px, py = pt
    for i in range(len(path) - 1):
        ax, ay = path[i]
        bx, by = path[i + 1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def save_sketch(path, strokes, page=None):
    import json
    data = {"format": "hw-sketch", "version": 1,
            "strokes": [[[round(x, 3), round(y, 3)] for x, y in s] for s in strokes]}
    if page is not None:
        data["sheet"] = [page.sheet_w, page.sheet_h]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def load_sketch(path):
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("format") != "hw-sketch":
        raise ValueError("это не файл эскиза")
    return [[(float(x), float(y)) for x, y in s]
            for s in data.get("strokes", []) if len(s) > 1]
