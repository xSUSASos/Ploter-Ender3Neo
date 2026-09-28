# -*- coding: utf-8 -*-
"""
Превращение ровных букв в живое письмо.

Всё, что отличает руку от принтера, собрано здесь:
  * каждая буква слегка своего размера, наклона и положения;
  * перо дрожит вдоль штриха (плавный шум, а не случайные скачки);
  * строка «плывёт» — базовая линия волнится и понемногу съезжает;
  * интервалы между словами гуляют;
  * иногда человек ошибается: пишет слово с опиской, зачёркивает
    и пишет заново; иногда ставит кляксу или недоводит штрих.

Геометрия здесь считается в единицах шрифта (CAP = 14), кроме зачёркиваний
и клякс — они строятся сразу в миллиметрах на уровне раскладки.
"""

import math
import random

from .glyphset import resample, CAP


# ------------------------------------------------------------- плавный шум

class Noise1D:
    """
    Гладкий одномерный шум: сумма синусов со случайными фазами.

    Дешевле и предсказуемее шума Перлина, а для дрожания пера выглядит
    ровно так же — важно лишь, чтобы соседние точки штриха были
    скоррелированы, иначе получится «щетина», а не рука.
    """

    def __init__(self, rng, octaves=3, base=1.0):
        self.terms = []
        amp = 1.0
        tot = 0.0
        for k in range(octaves):
            f = base * (1.7 ** k) * rng.uniform(0.8, 1.25)
            self.terms.append((f, rng.uniform(0.0, 2.0 * math.pi), amp))
            tot += amp
            amp *= 0.55
        self.norm = tot or 1.0

    def __call__(self, t):
        return sum(a * math.sin(t * f + p) for f, p, a in self.terms) / self.norm


# ------------------------------------------------------ аффинные искажения

def affine(strokes, sx=1.0, sy=1.0, rot=0.0, shear=0.0, dx=0.0, dy=0.0,
           pivot=(0.0, 0.0)):
    """
    Масштаб -> наклон (сдвиг) -> поворот -> перенос.

    shear задаётся в градусах и наклоняет букву вправо, как курсив:
    смещение по X пропорционально высоте над базовой линией.
    """
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    sh = math.tan(math.radians(shear))
    px, py = pivot
    out = []
    for s in strokes:
        ns = []
        for (x, y) in s:
            x0, y0 = (x - px) * sx, (y - py) * sy
            x0 += sh * y0
            ns.append((px + x0 * ca - y0 * sa + dx,
                       py + x0 * sa + y0 * ca + dy))
        out.append(ns)
    return out


def tremor(strokes, amp, wavelength, rng, step=None):
    """
    Дрожание пера: смещение каждой точки по нормали к штриху.

    Штрих сначала пересемплируется, иначе на длинной прямой (её описывают
    всего две точки) дрожать будет нечему.
    """
    if amp <= 0:
        return [list(s) for s in strokes]
    step = step or max(0.25, wavelength / 6.0)
    nx = Noise1D(rng, octaves=3, base=2.0 * math.pi / max(wavelength, 1e-6))
    ny = Noise1D(rng, octaves=3, base=2.0 * math.pi / max(wavelength, 1e-6))
    # шум считается прямо здесь, без вызова Noise1D на каждую точку:
    # те же слагаемые в том же порядке, поэтому результат тот же до бита
    xt, yt = tuple(nx.terms), tuple(ny.terms)
    xn, yn = nx.norm, ny.norm
    amp_b = amp * 0.35
    sin, hypot, dist = math.sin, math.hypot, math.dist
    out = []
    for s in strokes:
        p = resample(s, step)
        n = len(p)
        if n < 2:
            out.append(list(s))
            continue
        last = n - 1
        acc = 0.0
        ns = []
        prev = p[0]
        for i in range(n):
            x, y = cur = p[i]
            if i:
                acc += dist(prev, cur)
            qj = p[i + 1] if i < last else cur
            qk = prev
            tx, ty = qj[0] - qk[0], qj[1] - qk[1]
            ln = hypot(tx, ty) or 1.0
            # нормаль к касательной
            ox, oy = -ty / ln, tx / ln
            a = amp * (sum([am * sin(acc * f + ph) for f, ph, am in xt]) / xn)
            b = amp_b * (sum([am * sin(acc * f + ph) for f, ph, am in yt]) / yn)
            ns.append((x + ox * a + b, y + oy * a))
            prev = cur
        out.append(ns)
    return out


# --------------------------------------------------------- буква целиком

def humanize_glyph(strokes, cfg, rng, size_units=CAP):
    """
    Исказить один глиф. strokes — в единицах шрифта.
    size_units — сколько единиц шрифта приходится на «размер» (обычно CAP).
    """
    if not cfg.enabled or not strokes:
        return [list(s) for s in strokes]

    out = [list(s) for s in strokes]

    # пропуск отдельных штрихов — перо не пишет
    if cfg.skip_rate > 0 and len(out) > 1:
        kept = [s for s in out if rng.random() >= cfg.skip_rate]
        out = kept or out

    sc = 1.0 + rng.uniform(-cfg.jitter_size, cfg.jitter_size)
    rot = rng.uniform(-cfg.jitter_rot, cfg.jitter_rot)
    shear = cfg.slant + rng.uniform(-cfg.slant_jitter, cfg.slant_jitter)
    # вбок двигаем осторожнее, чем по высоте: соседние буквы сдвигаются
    # независимо, и при прежних ±14 % высоты две буквы навстречу друг
    # другу съедали весь межбуквенный зазор (~20 % высоты) — «с» и «к»
    # слипались, «аю» читалось как «оо». Предел — треть зазора с каждой
    # стороны, так что между соседями всегда остаётся просвет.
    dx_lim = min(cfg.jitter_pos * size_units * 0.5, 0.07 * size_units)
    dx = rng.uniform(-dx_lim, dx_lim)
    dy = rng.uniform(-cfg.jitter_pos, cfg.jitter_pos) * size_units * 0.6

    out = affine(out, sx=sc, sy=sc, rot=rot, shear=shear, dx=dx, dy=dy)

    if cfg.tremor > 0:
        out = tremor(out,
                     amp=cfg.tremor * size_units,
                     wavelength=max(0.3, cfg.tremor_scale * size_units),
                     rng=rng)
    return out


# ------------------------------------------------------------ каллиграфия

CALLI_STYLES = {"off": "нет", "broad": "широкое перо", "pointed": "острое перо"}


def _smooth(vals, window):
    if window < 2 or len(vals) < 3:
        return list(vals)
    half = window // 2
    out = []
    for i in range(len(vals)):
        seg = vals[max(0, i - half):i + half + 1]
        out.append(sum(seg) / len(seg))
    return out


def calligraphy(strokes, style, width, angle=40.0, pen=0.35, max_copies=9):
    """
    Толщина линии, которой у шарикового стержня нет: каждый штрих
    проходится несколько раз рядом с собой, туда-обратно, не отрывая пера.

    broad   — широкое перо под углом angle к строке: копии штриха сдвинуты
              вдоль кончика пера. Штрих поперёк пера выходит широким, вдоль
              пера — волосяным, как у настоящего плакатного пера.
    pointed — острое перо: копии расходятся по нормали к штриху, и тем
              сильнее, чем круче штрих идёт вниз (нажим на нисходящих);
              на восходящих линия остаётся волосяной.

    Координаты в мм, Y вверх. Возвращает новые штрихи.
    """
    if style not in ("broad", "pointed") or width <= pen * 0.6:
        return [list(s) for s in strokes]
    k = int(min(max_copies, max(2, math.ceil(width / max(pen * 0.8, 0.05)) + 1)))
    ts = [-1.0 + 2.0 * i / (k - 1) for i in range(k)]
    half = width / 2.0
    out = []
    if style == "broad":
        a = math.radians(angle)
        ox, oy = math.cos(a) * half, math.sin(a) * half
        for s in strokes:
            if len(s) < 2:
                out.append(list(s))
                continue
            path = []
            for i, t in enumerate(ts):
                cp = [(x + ox * t, y + oy * t) for (x, y) in s]
                path.extend(cp if i % 2 == 0 else cp[::-1])
            out.append(path)
        return out

    for s in strokes:
        p = resample(s, max(0.2, pen * 0.6))
        if len(p) < 3:
            out.append(list(s))
            continue
        n = len(p)
        nx, ny, press = [], [], []
        for i in range(n):
            j, h = min(i + 1, n - 1), max(i - 1, 0)
            tx, ty = p[j][0] - p[h][0], p[j][1] - p[h][1]
            ln = math.hypot(tx, ty) or 1.0
            nx.append(-ty / ln)
            ny.append(tx / ln)
            press.append(max(0.0, -ty / ln))         # вниз — нажим
        press = _smooth(press, max(3, n // 8))
        # к концам штриха нажим сходит на нет — перо ставят и снимают легко
        taper = [min(1.0, min(i, n - 1 - i) / max(1.0, n * 0.12)) for i in range(n)]
        w = [half * press[i] * taper[i] for i in range(n)]
        if max(w) < pen * 0.4:
            out.append(list(p))
            continue
        path = []
        for c, t in enumerate(ts):
            cp = [(p[i][0] + nx[i] * w[i] * t, p[i][1] + ny[i] * w[i] * t)
                  for i in range(n)]
            path.extend(cp if c % 2 == 0 else cp[::-1])
        out.append(path)
    return out


def blot(x, y, r, rng, loops=3):
    """Клякса: несколько неровных петелек на одном месте."""
    pts = []
    for k in range(loops * 12 + 1):
        a = 2.0 * math.pi * k / 12.0
        rr = r * (0.55 + 0.45 * (k / (loops * 12.0))) * rng.uniform(0.8, 1.2)
        pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
    return pts


# ------------------------------------------------------------ описки

_ROWS = [
    "йцукенгшщзхъ",
    "фывапролджэ",
    "ячсмитьбю",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "1234567890",
]


def _build_adjacency():
    adj = {}
    for row in _ROWS:
        for i, ch in enumerate(row):
            n = set()
            if i:
                n.add(row[i - 1])
            if i + 1 < len(row):
                n.add(row[i + 1])
            adj.setdefault(ch, set()).update(n)
    return adj


_ADJ = _build_adjacency()


def make_typo(word, rng):
    """
    Испортить слово так, как это делает рука: соседняя клавиша,
    перестановка, удвоение или пропуск буквы.
    """
    if len(word) < 2:
        return word
    kinds = ["swap", "double", "drop", "near"]
    kind = rng.choice(kinds)
    i = rng.randrange(len(word))
    lo = word.lower()

    if kind == "swap" and len(word) > 2:
        i = min(i, len(word) - 2)
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if kind == "double":
        return word[:i + 1] + word[i] + word[i + 1:]
    if kind == "drop" and len(word) > 2:
        return word[:i] + word[i + 1:]
    near = _ADJ.get(lo[i])
    if near:
        c = rng.choice(sorted(near))
        if word[i].isupper():
            c = c.upper()
        return word[:i] + c + word[i + 1:]
    return word[:i + 1] + word[i] + word[i + 1:]


# --------------------------------------------------- зачёркивание (в мм)

def strike_path(x0, y0, x1, y1, style, rng, thickness=1.0):
    """
    Линия зачёркивания поверх слова. Координаты в мм, Y вверх.
    y0/y1 — низ и верх слова.
    """
    ym = (y0 + y1) * 0.5
    h = max(0.6, (y1 - y0))
    w = max(0.6, (x1 - x0))

    if style == "zigzag":
        n = max(3, int(w / max(1.2, h * 0.55)))
        pts = []
        for k in range(n + 1):
            t = k / n
            pts.append((x0 + w * t,
                        ym + (h * 0.28 if k % 2 == 0 else -h * 0.28)
                        + rng.uniform(-0.1, 0.1) * h))
        return [pts]

    if style == "scribble":
        pts = []
        loops = max(2, int(w / max(1.6, h * 0.8)))
        steps = loops * 14
        for k in range(steps + 1):
            t = k / steps
            a = 2.0 * math.pi * loops * t
            pts.append((x0 + w * t + rng.uniform(-0.08, 0.08) * h,
                        ym + math.sin(a) * h * 0.34 + rng.uniform(-0.05, 0.05) * h))
        return [pts]

    # обычная черта, слегка непрямая и с вылетом за края
    over = 0.25 * h
    n = 7
    pts = []
    for k in range(n + 1):
        t = k / n
        pts.append((x0 - over + (w + 2 * over) * t,
                    ym + math.sin(t * math.pi) * h * 0.06
                    + rng.uniform(-0.045, 0.045) * h))
    return [pts]


# ------------------------------------------------------------- состояние

class Human:
    """
    Держит генератор случайных чисел и «медленные» переменные строки —
    волну базовой линии и общий снос, — чтобы искажения были связными,
    а не независимыми от буквы к букве.
    """

    def __init__(self, cfg, seed=None):
        self.cfg = cfg
        s = cfg.seed if seed is None else seed
        self.rng = random.Random(s if s else None)
        self.base_noise = Noise1D(self.rng, octaves=2, base=0.35)
        self._last_variant = {}


    def baseline_offset(self, x_mm, line_index, size_mm):
        """Смещение базовой линии в точке x_mm, мм."""
        c = self.cfg
        if not c.enabled:
            return 0.0
        wave = c.baseline_wave * size_mm * self.base_noise(x_mm / max(size_mm, 0.1))
        drift = c.baseline_drift * size_mm * math.sin(line_index * 1.7 + 0.6)
        return wave + drift

    def word_slope(self, t, size_mm):
        """Съезжание слов к концу строки: t — доля пройденной строки."""
        c = self.cfg
        if not c.enabled or not c.word_slope:
            return 0.0
        return -c.word_slope * size_mm * t

    def pick_variant(self, fontset, ch):
        c = self.cfg
        last = self._last_variant.get(ch) if c.avoid_repeat else None
        g, i = fontset.pick(ch, self.rng, mix=c.variant_mix if c.enabled else 0.0,
                            last_index=last, avoid_repeat=c.avoid_repeat)
        self._last_variant[ch] = i
        return g

    def space_factor(self):
        c = self.cfg
        if not c.enabled or c.space_jitter <= 0:
            return 1.0
        return 1.0 + self.rng.uniform(-c.space_jitter, c.space_jitter)

    def pressure_z(self, z_draw, dist_mm):
        """Модуляция Z для имитации нажима."""
        c = self.cfg
        if not c.enabled or c.pressure <= 0:
            return z_draw
        return z_draw + c.pressure * math.sin(dist_mm / max(c.pressure_scale, 0.1))
