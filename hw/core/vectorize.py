# -*- coding: utf-8 -*-
"""
Фотография буквы -> осевые линии (штрихи), пригодные для пера.

Конвейер:
    1. выравнивание освещённости  (деление на сильно размытый фон)
    2. бинаризация                (порог по уровню чернил или адаптивный)
    3. чистка                     (удаление мелких пятен и дырок)
    4. утоньшение до скелета      (Zhang–Suen, векторизованный на numpy)
    5. трассировка скелета в граф (концы, развилки, петли)
    6. обрезка усов, сглаживание, упрощение Дугласа–Пекера

На выходе — список полилиний в пиксельных координатах (ось Y вниз),
которые дальше нормализуются в единицы шрифта через glyphset.normalize_strokes.
"""

import math

import numpy as np

try:
    import cv2
except ImportError:                                   # pragma: no cover
    cv2 = None

from scipy import ndimage


# --------------------------------------------------------- подготовка кадра

def to_gray(img):
    """
    Любая картинка -> uint8 grayscale.

    Цветное фото сводим не к яркости, а к «чернильности»: смесь яркости
    и самого тёмного канала. Синяя ручка по яркости светлая (синий канал
    почти не весит), а в красном канале она такая же тёмная, как чёрная, —
    и тонкий синий штрих перестаёт теряться. Серая разлиновка во всех
    каналах одинакова, её это не затемняет.
    """
    a = np.asarray(img)
    if a.ndim == 2:
        return np.clip(a, 0, 255).astype(np.uint8)
    if a.shape[2] == 4:                       # подложить белый фон под альфу
        alpha = a[:, :, 3:4].astype(np.float32) / 255.0
        a = a[:, :, :3].astype(np.float32) * alpha + 255.0 * (1.0 - alpha)
    a = a[..., :3].astype(np.float32)
    lum = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    # баланс белого у телефона гуляет: сначала уравниваем каналы по бумаге
    paper = np.percentile(a.reshape(-1, 3), 90, axis=0)
    paper = np.maximum(paper, 1.0)
    low = (a * (paper.mean() / paper)).min(axis=2)
    g = 0.5 * lum + 0.5 * low
    return np.clip(g, 0, 255).astype(np.uint8)


def flatten_illumination(gray, sigma=None, lift=0):
    """
    Убрать неравномерную засветку: поделить кадр на оценку цвета бумаги.
    Возвращает float32 в диапазоне ~[0..1.2], где 1.0 — цвет бумаги.

    lift > 0 — перед размытием «поднять» фон максимумом по окну lift px:
    штрихи тоньше окна из оценки бумаги исчезают. Без этого на плотных
    буквах (Ж, Ш, Щ) чернила затемняли собственный фон, и светлая ручка
    на них пропадала кусками.
    """
    g = gray.astype(np.float32) + 1.0
    if sigma is None:
        sigma = max(gray.shape) / 12.0
    src = g
    if lift and lift > 1:
        k = int(lift) | 1
        k2 = max(3, k // 2) | 1
        if cv2 is not None:
            src = cv2.dilate(g, np.ones((k, k), np.uint8))
            src = cv2.erode(src, np.ones((k2, k2), np.uint8))
        else:
            src = ndimage.maximum_filter(g, size=k)
            src = ndimage.minimum_filter(src, size=k2)
    bg = _smooth_background(src, sigma)
    bg = np.maximum(bg, 1.0)
    return g / bg


def _smooth_background(src, sigma):
    """
    Гауссово размытие с большим радиусом. Фон плавный, поэтому при
    радиусе в сотни пикселей считаем его на уменьшенной копии и
    растягиваем обратно: на 10-мегапиксельном снимке с телефона это
    секунды вместо двадцати, а разница с полным расчётом — доли процента.
    """
    f = int(sigma // 6)
    h, w = src.shape
    if cv2 is None or f < 2 or min(h, w) // f < 16:
        return ndimage.gaussian_filter(src, sigma=sigma)
    small = cv2.resize(src, (max(1, w // f), max(1, h // f)),
                       interpolation=cv2.INTER_AREA)
    small = ndimage.gaussian_filter(small, sigma=sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def binarize(gray, ink_level=0.68, mode="level", block=31, offset=12,
             flatten=True):
    """
    -> bool-маска, True = чернила.

    mode="level"    : порог по доле от яркости бумаги. Хорошо отсекает
                      светло-серую разлиновку шаблона, оставляя тёмную ручку.
    mode="adaptive" : адаптивный порог OpenCV — устойчивее к плохому фото,
                      но может подхватить и разлиновку.
    mode="otsu"     : глобальный порог Оцу.
    """
    if mode == "adaptive" and cv2 is not None:
        b = max(3, int(block) | 1)
        m = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, b, float(offset))
        return m > 0
    if mode == "otsu" and cv2 is not None:
        _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        return m > 0
    return ink_map(gray, flatten) < float(ink_level)


def ink_map(gray, flatten=True):
    """Яркость относительно бумаги: 1.0 — бумага, чем меньше — тем гуще чернила."""
    if not flatten:
        return gray.astype(np.float32) / 255.0
    return flatten_illumination(gray, lift=max(9, min(gray.shape) // 10))


def clean_mask(mask, min_area=24, close_holes=True, norm=None, ink_level=0.68):
    """
    Убрать мелкий мусор и заштопать дырки в штрихах.

    Если передана карта norm, мелкое пятно не выбрасывается вслепую:
    точка ручкой (над ё, i, j, в «.», «:», «!») такая же тёмная, как
    штрихи буквы, а мусор от шума и фактуры бумаги едва переходит порог.
    Раньше порог по площади съедал точки целиком — и «.», «:», «ё»
    распознавались без них или не распознавались вовсе.
    """
    m = mask.copy()
    if close_holes and cv2 is not None:
        k = np.ones((3, 3), np.uint8)
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, k).astype(bool)
    if min_area > 0:
        lab, n = ndimage.label(m, structure=np.ones((3, 3), int))
        if n:
            sizes = np.bincount(lab.ravel())
            sizes[0] = 0
            keep = sizes >= min_area
            if norm is not None:
                keep |= _ink_dots(lab, n, sizes, keep, norm, ink_level)
            if not keep.any():                 # всё мелкое — оставим крупнейшее
                keep = np.zeros_like(sizes, bool)
                keep[int(sizes.argmax())] = True
            m = keep[lab]
    return m


def suppress_template(norm, mask, template, ink_level, grow=2):
    """
    Убрать из маски то, что напечатано на пустом шаблоне.

    На реальной печати разлиновка, рамки и бледные образцы выходят
    темнее задуманного (замер на фото: базовая линия ~0.63 от бумаги —
    темнее порога 0.68), и в буквы попадали пунктиры и куски линий.
    Зато ручка на порядок темнее печати (~0.05). Поэтому там, где шаблон
    что-то печатал (с запасом grow px на неточность совмещения),
    чернилами считаем только то, что заметно темнее самой печати.
    """
    printed = template < 245
    if not printed.any() or not mask.any():
        return mask
    zone = ndimage.binary_dilation(printed, iterations=grow)
    # уровень пера — по самым тёмным пикселям всей ячейки: знаки
    # препинания и точки часто целиком лежат на линиях (точка на базовой,
    # двоеточие на пунктире), и вне шаблона пера может не быть вовсе
    pen = float(np.percentile(norm[mask], 2))
    strong = norm < pen + 0.5 * (float(ink_level) - pen)
    far = zone & ~ndimage.binary_dilation(strong, iterations=3)
    if far.sum() < 20:
        return mask
    tpl_lvl = float(np.percentile(norm[far], 5))
    if pen >= tpl_lvl - 0.05:              # ничего темнее печати: ячейка пуста
        return mask & ~zone
    if tpl_lvl - pen < 0.12:               # перо не темнее печати — не различить
        return mask
    own = mask & ~zone
    # ближе к перу, чем к печати: там, где перо касается линии, их
    # размытые края складываются, и на середине оставался «хвост»
    cut = min(float(ink_level), pen + 0.35 * (tpl_lvl - pen))
    out = mask & ~(zone & (norm >= cut))
    # тонкие обрывки, целиком лежащие на напечатанных линиях и ни с чем
    # не связанные, — это сама печать в самых тёмных местах, а не ручка.
    # Настоящий штрих вдоль линии (низ L, Д, Ц) соединён с буквой, а точка
    # на базовой линии («.») или на бледном образце (над «ё») толщиной
    # с перо — их не трогаем
    lab, n = ndimage.label(out, structure=np.ones((3, 3), int))
    if n:
        ids = np.arange(1, n + 1)
        inside = np.asarray(ndimage.minimum(zone.astype(np.uint8), lab, ids), bool)
        thick = np.asarray(ndimage.maximum(ndimage.distance_transform_edt(out),
                                           lab, ids), float)
        drop = np.zeros(n + 1, bool)
        drop[1:] = inside & (thick < 0.3 * stroke_width(own if own.any() else out))
        out = out & ~drop[lab]
    return out


def _ink_dots(lab, n, sizes, big, norm, ink_level):
    """Какие из мелких пятен — настоящие точки ручкой. -> bool по меткам."""
    ids = np.arange(n + 1)
    darkest = ndimage.minimum(norm, lab, ids)
    darkest = np.asarray(darkest, dtype=np.float32)
    if big.any():
        core = float(np.median(norm[big[lab]]))
    else:
        core = float(darkest[1:].min())
    # середина между порогом и типичной густотой штриха
    need = core + 0.5 * (float(ink_level) - core)
    # точка ручкой — пятно не меньше ~полукруга толщины пера. На фото
    # настоящие точки от 0.5 sw², а пылинки и шум — до 0.3 sw²:
    # без этого порога принтер рисовал бы их кружочками
    sw = stroke_width(big[lab]) if big.any() else 1.0
    min_dot = max(4.0, 0.4 * sw * sw)
    ok = (sizes >= min_dot) & ~big & (darkest <= need)
    ok[0] = False
    return ok


def keep_largest(mask, n=1):
    """Оставить n самых крупных связных пятен (полезно для одной буквы в ячейке)."""
    lab, cnt = ndimage.label(mask, structure=np.ones((3, 3), int))
    if cnt <= n:
        return mask
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    top = np.argsort(sizes)[::-1][:n]
    return np.isin(lab, top)


def split_dots(mask, sw, size_factor=3.4):
    """
    Отделить «точки» (над ё, й, i, знаки препинания) от настоящих штрихов.

    Точка — это компактное пятно, чей габарит не больше size_factor толщин
    пера. Скелет такого пятна вырождается в одну точку и теряется, поэтому
    точки обрабатываются отдельно: их рисуем маленькой окружностью.

    -> (маска без точек, [(cx, cy, r), ...])
    """
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), int))
    if n == 0:
        return mask, []
    lim = max(3.0, size_factor * sw)
    dots = []
    drop = np.zeros(n + 1, bool)
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        # вытянутое пятно — это короткий штрих (запятая, апостроф), не точка
        if h <= lim and w <= lim and max(h, w) <= 1.8 * max(1, min(h, w)):
            cy, cx = ndimage.center_of_mass(lab[sl] == i)
            dots.append((float(sl[1].start + cx), float(sl[0].start + cy),
                         max(0.6, 0.25 * min(h, w))))
            drop[i] = True
    if not dots:
        return mask, []
    return mask & ~drop[lab], dots


def dot_paths(dots, points=10):
    """Точки -> крошечные замкнутые окружности, которые перо сможет нарисовать."""
    out = []
    for (cx, cy, r) in dots:
        out.append([(cx + r * math.cos(2 * math.pi * k / points),
                     cy + r * math.sin(2 * math.pi * k / points))
                    for k in range(points + 1)])
    return out


def remove_hlines(mask, ys, thickness=2, gap=2, look=3):
    """
    Стереть остатки горизонтальных направляющих на известных высотах,
    НЕ повредив вертикальные штрихи буквы, которые их пересекают.

    Признак пересечения простой: если прямо над полосой и прямо под ней
    в этом же столбце есть чернила — значит, через полосу идёт штрих,
    и трогать столбец нельзя. Всё остальное в полосе — сама линовка.
    """
    h, w = mask.shape
    out = mask.copy()
    t = max(1, int(round(thickness)))
    for y in ys:
        y = int(round(y))
        lo, hi = max(0, y - t), min(h, y + t + 1)
        if hi <= lo:
            continue
        a0, a1 = max(0, lo - gap - look), max(0, lo - gap)
        b0, b1 = min(h, hi + gap), min(h, hi + gap + look)
        above = mask[a0:a1].any(axis=0) if a1 > a0 else np.zeros(w, bool)
        below = mask[b0:b1].any(axis=0) if b1 > b0 else np.zeros(w, bool)
        crossing = above & below
        band = out[lo:hi].copy()
        band[:, ~crossing] = False
        out[lo:hi] = band
    return out


def stroke_width(mask):
    """Оценка толщины штриха: 2 * средний радиус по карте расстояний."""
    if not mask.any():
        return 1.0
    dt = ndimage.distance_transform_edt(mask)
    v = dt[mask]
    return max(1.0, 2.0 * float(np.percentile(v, 75)))


# ------------------------------------------------------- скелет (Zhang–Suen)

def _nb_stack(p):
    """Соседи P2..P9 по часовой стрелке для каждого пикселя (padded массив)."""
    return (p[:-2, 1:-1],   # P2  N
            p[:-2, 2:],     # P3  NE
            p[1:-1, 2:],    # P4  E
            p[2:, 2:],      # P5  SE
            p[2:, 1:-1],    # P6  S
            p[2:, :-2],     # P7  SW
            p[1:-1, :-2],   # P8  W
            p[:-2, :-2])    # P9  NW


def thin(mask, max_iter=200):
    """Утоньшение до линии в 1 пиксель (алгоритм Zhang–Suen)."""
    img = mask.astype(np.uint8)
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            p = np.pad(img, 1)
            P2, P3, P4, P5, P6, P7, P8, P9 = _nb_stack(p)
            seq = [P2, P3, P4, P5, P6, P7, P8, P9, P2]
            B = P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9
            A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
                    for i in range(8))
            if step == 0:
                c1 = (P2 * P4 * P6) == 0
                c2 = (P4 * P6 * P8) == 0
            else:
                c1 = (P2 * P4 * P8) == 0
                c2 = (P2 * P6 * P8) == 0
            # B >= 3 вместо 2 — поправка Лю–Ванга: классический Zhang–Suen
            # целиком стирает диагональ вида «/» (нижние плечи «»», «и»)
            kill = (img == 1) & (B >= 3) & (B <= 6) & (A == 1) & c1 & c2
            if kill.any():
                img[kill] = 0
                changed = True
        if not changed:
            break
    return _drop_stairs(img.astype(bool))


# (dy, dx) для N, E, S, W и диагоналей
_N, _E, _S, _W = (-1, 0), (0, 1), (1, 0), (0, -1)
_NE, _SE, _SW, _NW = (-1, 1), (1, 1), (1, -1), (-1, -1)
# угол «лесенки»: два перпендикулярных соседа есть, противоположная сторона пуста
_STAIRS = [((_N, _E), (_S, _W, _SW)),
           ((_E, _S), (_W, _N, _NW)),
           ((_S, _W), (_N, _E, _NE)),
           ((_W, _N), (_E, _S, _SE))]


def _drop_stairs(sk):
    """
    Zhang–Suen оставляет на диагоналях «лесенку» толщиной в два пикселя:
    у каждой ступеньки три соседа, и трассировщик принимал её за развилку,
    кроша наклонные штрихи и дуги на обрывки по 2 точки, которые потом
    выбрасывались как мусор. Убираем угловые пиксели ступенек по одному
    (последовательно — чтобы не порвать линию), связность сохраняется
    через диагональ.
    """
    s = np.pad(sk, 1)
    for y, x in np.argwhere(s):
        for need, empty in _STAIRS:
            if all(s[y + dy, x + dx] for dy, dx in need) and \
                    not any(s[y + dy, x + dx] for dy, dx in empty):
                s[y, x] = False
                break
    return s[1:-1, 1:-1]


# ----------------------------------------------------- трассировка скелета

_N8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _neighbours(sk, y, x):
    out = []
    h, w = sk.shape
    for dy, dx in _N8:
        ny, nx = y + dy, x + dx
        if 0 <= ny < h and 0 <= nx < w and sk[ny, nx]:
            out.append((ny, nx))
    return out


def trace_skeleton(sk, min_branch=4):
    """
    Скелет -> список путей [[(x, y), ...], ...] в пикселях (Y вниз).

    Обрабатываются и открытые ветви (от конца/развилки до конца/развилки),
    и замкнутые петли (буквы о, а, б, ф).
    """
    pts = [tuple(p) for p in np.argwhere(sk)]
    if not pts:
        return []
    deg = {p: len(_neighbours(sk, *p)) for p in pts}
    nodes = [p for p in pts if deg[p] != 2]

    used = set()                      # неориентированные рёбра

    def edge(a, b):
        return (a, b) if a <= b else (b, a)

    paths = []

    def walk(start, first):
        path = [start, first]
        used.add(edge(start, first))
        prev, cur = start, first
        while deg.get(cur, 0) == 2:
            nbs = [q for q in _neighbours(sk, *cur) if q != prev]
            if not nbs:
                break
            nxt = nbs[0]
            e = edge(cur, nxt)
            if e in used:
                break
            used.add(e)
            path.append(nxt)
            prev, cur = cur, nxt
        return path

    for nd in nodes:
        for nb in _neighbours(sk, *nd):
            if edge(nd, nb) in used:
                continue
            paths.append(walk(nd, nb))

    # замкнутые контуры: все точки степени 2, ни одного узла
    for p in pts:
        if deg[p] != 2:
            continue
        nbs = _neighbours(sk, *p)
        if all(edge(p, q) in used for q in nbs):
            continue
        nb = next(q for q in nbs if edge(p, q) not in used)
        path = walk(p, nb)
        if len(path) > 2 and path[-1] != p and p in _neighbours(sk, *path[-1]):
            path.append(p)
        paths.append(path)

    # обрезка коротких «усов»: ветвь, у которой ровно один конец свободный,
    # а длина меньше порога, — это артефакт скелетизации на стыке штрихов
    if min_branch > 0:
        def plen(pa):
            return sum(math.dist(pa[i], pa[i + 1]) for i in range(len(pa) - 1))

        pruned = []
        for pa in paths:
            free = (deg.get(pa[0], 0) == 1) + (deg.get(pa[-1], 0) == 1)
            if free == 1 and plen(pa) < min_branch:
                continue
            pruned.append(pa)
        paths = pruned or paths

    # (row, col) -> (x, y)
    return [[(float(c), float(r)) for (r, c) in pa] for pa in paths]


# ------------------------------------------------------------- постобработка

def smooth_path(path, window=5, passes=2, keep_ends=False):
    """
    Скользящее среднее — убирает пиксельную «лесенку», не трогая концы.

    keep_ends — концы закреплены, даже если путь замкнут. Нужно петлям,
    которые начинаются в узле (низ «в», «д», «з», «у», «8»): сглаживание
    по кругу уводило точку крепления внутрь петли, и петля отрывалась.
    """
    if len(path) < 3 or window < 3:
        return list(path)
    pts = list(path)
    half = window // 2
    closed = math.dist(pts[0], pts[-1]) < 1.5 and not keep_ends
    for _ in range(passes):
        out = []
        n = len(pts)
        for i in range(n):
            if not closed and (i < 1 or i > n - 2):
                out.append(pts[i])
                continue
            lo, hi = max(0, i - half), min(n, i + half + 1)
            seg = pts[lo:hi]
            out.append((sum(p[0] for p in seg) / len(seg),
                        sum(p[1] for p in seg) / len(seg)))
        pts = out
    if closed and math.dist(pts[0], pts[-1]) > 1e-9:
        pts[-1] = pts[0]
    return pts


def rdp(path, tol):
    """Упрощение Дугласа–Пекера (итеративное, без рекурсии)."""
    if len(path) < 3:
        return list(path)
    keep = [False] * len(path)
    keep[0] = keep[-1] = True
    stack = [(0, len(path) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = path[lo]
        bx, by = path[hi]
        dx, dy = bx - ax, by - ay
        den = math.hypot(dx, dy)
        best, bi = -1.0, -1
        for i in range(lo + 1, hi):
            px, py = path[i]
            d = (math.hypot(px - ax, py - ay) if den < 1e-12
                 else abs(dy * px - dx * py + bx * ay - by * ax) / den)
            if d > best:
                best, bi = d, i
        if best > tol and bi > 0:
            keep[bi] = True
            stack.append((lo, bi))
            stack.append((bi, hi))
    return [p for p, k in zip(path, keep) if k]


def merge_paths(paths, gap=2.5):
    """Склеить пути, концы которых почти совпадают — меньше отрывов пера."""
    if len(paths) < 2:
        return [list(p) for p in paths]
    pool = [list(p) for p in paths]
    out = []
    while pool:
        cur = pool.pop(0)
        joined = True
        while joined:
            joined = False
            for i, other in enumerate(pool):
                if math.dist(cur[-1], other[0]) <= gap:
                    cur = cur + other[1:]
                elif math.dist(cur[-1], other[-1]) <= gap:
                    cur = cur + other[::-1][1:]
                elif math.dist(cur[0], other[-1]) <= gap:
                    cur = other + cur[1:]
                elif math.dist(cur[0], other[0]) <= gap:
                    cur = other[::-1] + cur[1:]
                else:
                    continue
                pool.pop(i)
                joined = True
                break
        out.append(cur)
    return out


# ------------------------------------------------------------- всё вместе

DEFAULTS = dict(
    ink_level=0.68,
    mode="level",
    block=31,
    offset=12,
    min_area=24,
    largest_blobs=0,      # 0 = не ограничивать
    min_branch=4,
    smooth=5,
    simplify_tol=0.8,
    merge_gap=2.5,
    min_path_len=3.0,
)


def vectorize_mask(mask, min_branch=4, smooth=5, simplify_tol=0.8,
                   merge_gap=2.5, min_path_len=3.0, spur_factor=1.8):
    """
    Готовая бинарная маска -> список полилиний в пикселях.

    Пороги обрезки привязаны к толщине штриха: одна и та же настройка
    одинаково работает и для фото 800 px, и для скана 3000 px.
    """
    if not mask.any():
        return []
    sw = stroke_width(mask)
    body, dots = split_dots(mask, sw)
    paths = []
    if body.any():
        sk = thin(body)
        # ус скелета короче толщины пера; порог выше срезал настоящие
        # короткие плечи (перекладина f и t, хвост у 4) при толстой ручке
        paths = trace_skeleton(sk, min_branch=max(min_branch, 1.1 * sw))
        paths = straighten_junctions(paths, sk, sw)
        paths = extend_ends(paths, sk, body, sw)
        paths = [smooth_path(p, window=smooth, keep_ends=a)
                 for p, a in zip(paths, _attached(paths))]
        if merge_gap > 0:
            paths = merge_paths(paths, gap=max(merge_gap, 0.6 * sw))

    keep_len = max(min_path_len, spur_factor * sw)
    out = []
    for p in paths:
        p = rdp(p, simplify_tol) if simplify_tol > 0 else p
        if len(p) < 2:
            continue
        ln = sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))
        if ln >= keep_len:
            out.append(p)
    if body.any():
        out.extend(_orphans(body, out, sw))
    out.extend(dot_paths(dots))
    return out


def _attached(paths, tol=2.0):
    """Для каждого пути: касается ли его начало или конец другого пути."""
    pts = [np.asarray(p, float) for p in paths]
    out = []
    for i, p in enumerate(pts):
        hit = False
        for end in (p[0], p[-1]):
            for j, q in enumerate(pts):
                if j != i and len(q) and float(np.min(np.hypot(*(q - end).T))) < tol:
                    hit = True
                    break
            if hit:
                break
        out.append(hit)
    return out


def straighten_junctions(paths, sk, sw):
    """
    Выпрямить штрихи у развилок.

    Там, где сходятся несколько штрихов (центр Ж, Х, К, перекладина А),
    чернила сливаются в пятно, и скелет внутри него гнёт прямые штрихи
    в скобки «)(» — буква выходит кривой. Концы путей, упирающиеся
    в развилку, обрезаем на радиус пятна и доводим прямой до её центра.
    """
    if not paths:
        return paths
    p = np.pad(sk, 1).astype(np.uint8)
    deg = sum(np.roll(np.roll(p, dy, 0), dx, 1)
              for dy, dx in _N8)[1:-1, 1:-1] * sk
    junc = deg >= 3
    if not junc.any():
        return paths
    # соседние узлы — одна развилка
    grow = ndimage.binary_dilation(junc, iterations=max(1, int(round(sw * 0.6))))
    lab, n = ndimage.label(grow, structure=np.ones((3, 3), int))
    centers, radius = {}, {}
    for k in range(1, n + 1):
        ys, xs = np.nonzero(junc & (lab == k))
        if not len(xs):
            continue
        cx, cy = float(xs.mean()), float(ys.mean())
        spread = float(np.hypot(xs - cx, ys - cy).max())
        centers[k] = (cx, cy)
        radius[k] = max(1.5 * sw, spread + sw)

    def cluster(pt):
        x, y = int(round(pt[0])), int(round(pt[1]))
        if 0 <= y < lab.shape[0] and 0 <= x < lab.shape[1] and junc[y, x]:
            return int(lab[y, x])
        return 0

    # 1) обрезаем концы у развилок и запоминаем, откуда и куда шёл штрих
    cut_paths, rays = [], {}
    for pa in paths:
        pa = list(pa)
        ka, kb = cluster(pa[0]), cluster(pa[-1])
        if ka and ka == kb and all(
                math.dist(q, centers[ka]) < radius[ka] for q in pa):
            continue                       # перемычка внутри одного пятна
        head = tail = 0
        if kb in centers:
            c, r = centers[kb], radius[kb]
            cut = len(pa)
            while cut > 1 and math.dist(pa[cut - 1], c) < r:
                cut -= 1
            pa = pa[:cut]
            tail = kb
        if ka in centers:
            c, r = centers[ka], radius[ka]
            cut = 0
            while cut < len(pa) - 1 and math.dist(pa[cut], c) < r:
                cut += 1
            pa = pa[cut:]
            head = ka
        for k, end, back in ((head, pa[0], pa[min(len(pa) - 1, int(2 * sw))]),
                             (tail, pa[-1], pa[max(0, len(pa) - 1 - int(2 * sw))])):
            if k:
                rays.setdefault(k, []).append((end, back))
        cut_paths.append((head, pa, tail))

    # 2) центр развилки — точка, где сходятся продолжения штрихов
    #    (наименьшие квадраты). Среднее по пикселям узла в пятне из пяти
    #    штрихов (Ж) уезжало вбок и гнуло вертикаль.
    for k, rs in rays.items():
        A = np.zeros((2, 2))
        bvec = np.zeros(2)
        for end, back in rs:
            d = np.array(end, float) - np.array(back, float)
            ln = np.hypot(*d)
            if ln < 1e-6:
                continue
            d /= ln
            P = np.eye(2) - np.outer(d, d)
            A += P
            bvec += P @ np.array(end, float)
        if len(rs) >= 2 and abs(np.linalg.det(A)) > 1e-3:
            c = np.linalg.solve(A, bvec)
            if math.dist(c, centers[k]) < radius[k]:
                centers[k] = (float(c[0]), float(c[1]))

    out = []
    for head, pa, tail in cut_paths:
        if head:
            pa = [centers[head]] + pa
        if tail:
            pa = pa + [centers[tail]]
        out.append(pa)
    return out


def extend_ends(paths, sk, body, sw):
    """
    Дотянуть свободные концы штрихов до края чернил.

    Zhang–Suen подъедает концы наклонных штрихов — у «ёлочек», А, К, V
    кончики укорачивались на 1–2 толщины пера. Продлеваем конец по его
    направлению, пока под ним есть чернила, и останавливаемся на
    полтолщины раньше края — там, где шёл центр пера.
    """
    h, w = body.shape
    # свободный конец — тот, к которому не подходит ни один другой путь.
    # Смотрим на уже очищенные пути, а не на скелет: на кончике толстого
    # штриха скелет оставляет крошечную «вилку», её обрезали, но по
    # скелету конец выглядел развилкой и не продлевался
    pts = [np.asarray(pa, float) for pa in paths]

    def free(i, pt):
        q = np.asarray(pt, float)
        for j, other in enumerate(pts):
            if j != i and len(other) and                     float(np.min(np.hypot(*(other - q).T))) < 2.0:
                return False
        return True

    def straight(seq, kmax):
        """Сколько точек от конца лежит на одной прямой (не больше kmax)."""
        for k in range(min(kmax, len(seq) - 1), 1, -1):
            a = np.asarray(seq[0], float)
            b = np.asarray(seq[k], float)
            ab = b - a
            ln = float(np.hypot(*ab))
            if ln < 1e-6:
                continue
            mid = np.asarray(seq[1:k], float) - a
            dev = np.abs(mid[:, 0] * ab[1] - mid[:, 1] * ab[0]) / ln
            if float(dev.max(initial=0.0)) <= max(0.8, 0.25 * sw):
                return k
        return 0

    def grow(end, back):
        d = np.array(end, float) - np.array(back, float)
        ln = float(np.hypot(*d))
        if ln < 1e-6:
            return None
        d /= ln
        p = np.array(end, float)
        last = None
        for _ in range(int(4 * sw) + 2):
            q = p + d * 0.5
            x, y = int(round(q[0])), int(round(q[1]))
            if not (0 <= y < h and 0 <= x < w) or not body[y, x]:
                break
            p, last = q, q
        if last is None:
            return None
        tip = last - d * (0.5 * sw)
        if np.dot(tip - np.array(end, float), d) < 1.0:
            return None
        return (float(tip[0]), float(tip[1]))

    out = []
    for i, pa in enumerate(paths):
        pa = list(pa)
        # направление берём по участку в три толщины пера, но не длиннее
        # трети штриха: у короткого плеча («, ») длинная база цепляла угол
        k = max(2, min(int(round(3.0 * sw)), len(pa) // 3))
        # у самого кончика скелет загнут — этот кусок отрезаем и тянем
        # штрих заново от прямого участка
        cut = int(round(sw)) if len(pa) > int(round(sw)) * 2 + 2 * k + 2 else 0
        if len(pa) >= 3 and math.dist(pa[0], pa[-1]) > 1.5:
            # направление — только по прямому участку у конца: у короткого
            # плеча («, ») длинная база цепляла вершину угла, и конец
            # «продлевался» назад
            # продление принимаем, только если новый кончик ушёл дальше
            # старого: у настоящей дуги (крючок j, хвост у) прямая короче
            if free(i, pa[-1]):
                body_ = pa[:len(pa) - cut] if cut else pa
                kk = straight(body_[::-1], k)
                t = grow(body_[-1], body_[-1 - kk]) if kk else None
                anchor = body_[-1 - kk]
                if t and math.dist(t, anchor) > math.dist(pa[-1], anchor) + 1.0:
                    pa = body_ + [t]
            if free(i, pa[0]):
                body_ = pa[cut:] if cut else pa
                kk = straight(body_, k)
                t = grow(body_[0], body_[kk]) if kk else None
                anchor = body_[kk]
                if t and math.dist(t, anchor) > math.dist(pa[0], anchor) + 1.0:
                    pa = [t] + body_
        out.append(pa)
    return out


def _orphans(body, paths, sw):
    """
    Пятна чернил, от которых после обрезки не осталось ни одного пути
    (короткая запятая, апостроф, крупная точка), — вернуть отрезком
    по длинной оси или точкой. Иначе такие знаки распознавались пустыми.
    """
    lab, n = ndimage.label(body, structure=np.ones((3, 3), int))
    if n == 0:
        return []
    hit = np.zeros(n + 1, bool)
    h, w = lab.shape
    for pa in paths:
        for q in pa:
            x, y = int(round(q[0])), int(round(q[1]))
            y0, y1 = max(0, y - 1), min(h, y + 2)
            x0, x1 = max(0, x - 1), min(w, x + 2)
            hit[lab[y0:y1, x0:x1].ravel()] = True
    out = []
    for k in range(1, n + 1):
        if hit[k]:
            continue
        ys, xs = np.nonzero(lab == k)
        if len(xs) < 4:
            continue
        pts = np.stack([xs, ys], 1).astype(np.float64)
        c = pts.mean(0)
        u, s, vt = np.linalg.svd(pts - c, full_matrices=False)
        axis = vt[0]
        t = (pts - c) @ axis
        if t.max() - t.min() < 1.5 * sw:
            r = max(0.6, 0.25 * min(np.ptp(xs) + 1, np.ptp(ys) + 1))
            out.extend(dot_paths([(float(c[0]), float(c[1]), r)]))
        else:
            # концы отрезка — на половину толщины внутрь от краёв пятна
            a = c + axis * (t.min() + 0.5 * sw)
            b = c + axis * (t.max() - 0.5 * sw)
            out.append([(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))])
    return out


def vectorize_image(img, **kw):
    """
    Картинка (PIL/numpy) -> (штрихи, отладочные данные).

    Отладочные данные пригодятся GUI, чтобы показать, что именно
    программа сочла чернилами.
    """
    o = dict(DEFAULTS)
    remove_lines = o.pop("remove_lines", None)
    remove_lines = kw.pop("remove_lines", remove_lines)
    template = kw.pop("template", None)
    o.update(kw)
    gray = to_gray(img)
    norm = None
    if o["mode"] == "level":
        norm = ink_map(gray)
        mask = norm < float(o["ink_level"])
        if template is not None and template.shape == mask.shape:
            mask = suppress_template(norm, mask, template, o["ink_level"])
    else:
        mask = binarize(gray, ink_level=o["ink_level"], mode=o["mode"],
                        block=o["block"], offset=o["offset"])
    mask = clean_mask(mask, min_area=o["min_area"], norm=norm,
                      ink_level=o["ink_level"])
    if remove_lines:
        sw0 = stroke_width(mask)
        mask = remove_hlines(mask, remove_lines,
                             thickness=max(2, 0.6 * sw0))
        mask = clean_mask(mask, min_area=o["min_area"], norm=norm,
                          ink_level=o["ink_level"])
    if o["largest_blobs"]:
        mask = keep_largest(mask, o["largest_blobs"])
    strokes = vectorize_mask(mask,
                             min_branch=o["min_branch"],
                             smooth=o["smooth"],
                             simplify_tol=o["simplify_tol"],
                             merge_gap=o["merge_gap"],
                             min_path_len=o["min_path_len"])
    dbg = {"gray": gray, "mask": mask,
           "ink_px": int(mask.sum()),
           "stroke_width": stroke_width(mask)}
    return strokes, dbg
