"""
Заделать разрывы в штрихах шрифта hw-font/1.

Откуда разрывы: при распознавании с фото разлиновка шаблона (линии
заглавных, строчных, базовая и нижняя) стирается, а вместе с ней — и
чернила там, где буква эту линию пересекала.  Поэтому шьём только
поперёк линий шаблона:

  * конец к концу — два конца по разные стороны линии, рядом с ней и
    смотрят друг на друга: соединяются плавной дугой в один штрих;
  * конец к линии — конец у линии шаблона направлен в другой штрих по ту
    сторону линии (ножка Т под перекладиной): дотягивается до касания.

Всё, что лежит не на линии шаблона, не трогается — это настоящие
отрывы пера.  Мелкие одиночные кляксы вдали от буквы убираются.

    python tools/heal_font.py шрифт.json [-o выход.json]
"""
import argparse
import json
import math
import sys

# доли высоты прописной (cap)
GAP = 0.3           # самый длинный заделываемый разрыв
BAND = 0.125        # концы разрыва не дальше этого от линии шаблона
SPECK = 0.06        # клякса: размер меньше этого ...
SPECK_FAR = 0.12    # ... и дальше этого от остальной буквы

# линии шаблона (hw/core/sheet.py) в долях cap от базовой
F_CAP, F_XH, F_BASE, F_DESC = 0.300, 0.443, 0.700, 0.814

NO_HEAL = set(".:\"'")
KEEP_SPECKS = set("ЪъЁёЙй")  # мелкий флажок или точка здесь — часть буквы


def _guides(cap, xh):
    return [cap, xh, 0.0, -(F_DESC - F_BASE) / (F_BASE - F_CAP) * cap]


def _across(a, b, guides, band):
    """Отрезок a-b пересекает линию шаблона, и оба конца у самой линии."""
    for g in guides:
        da, db = a[1] - g, b[1] - g
        if abs(da) > band or abs(db) > band:
            continue
        if da * db <= 0 or min(abs(da), abs(db)) <= 0.15 * band:
            return True
    return False


def _pts(flat):
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]


def _flat(pts):
    out = []
    for x, y in pts:
        out += [round(x, 3), round(y, 3)]
    return out


def _length(p):
    return sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))


def _diag(p):
    xs = [q[0] for q in p]
    ys = [q[1] for q in p]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _tangent(p, at_end, reach):
    """Направление наружу на конце штриха, по точке на расстоянии reach."""
    seq = p[::-1] if at_end else p
    a = seq[0]
    b = None
    for q in seq[1:]:
        b = q
        if math.dist(a, q) >= reach:
            break
    if b is None:
        return None
    dx, dy = a[0] - b[0], a[1] - b[1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return None
    return dx / n, dy / n


def _hermite(a, ta, b, tb, n=6):
    """Дуга из a (выходит по ta) в b (входит против tb)."""
    d = math.dist(a, b)
    k = d * 0.45
    c1 = (a[0] + ta[0] * k, a[1] + ta[1] * k)
    c2 = (b[0] + tb[0] * k, b[1] + tb[1] * k)
    out = []
    for i in range(1, n):
        t = i / n
        u = 1 - t
        out.append((u ** 3 * a[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * b[0],
                    u ** 3 * a[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * b[1]))
    return out


def _seg_dist(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L = dx * dx + dy * dy
    t = 0 if L < 1e-12 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    q = (ax + t * dx, ay + t * dy)
    return math.dist(p, q), q


def _ray_hit(o, d, a, b):
    """Расстояние по лучу o + s*d до отрезка ab (или None)."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    den = d[0] * ey - d[1] * ex
    if abs(den) < 1e-12:
        return None
    wx, wy = a[0] - o[0], a[1] - o[1]
    s = (wx * ey - wy * ex) / den
    u = (wx * d[1] - wy * d[0]) / den
    if s > 1e-6 and -1e-6 <= u <= 1 + 1e-6:
        return s
    return None


def _seg_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _crosses(bridge, a, b, strokes, near=0.35):
    """Перемычка a..b пересекает какую-нибудь линию (кроме самых концов)."""
    line = [a] + bridge + [b]
    for o in strokes:
        for s, e in zip(o, o[1:]):
            if min(math.dist(s, a), math.dist(e, a), math.dist(s, b), math.dist(e, b)) < near:
                continue
            for u, w in zip(line, line[1:]):
                if _seg_cross(u, w, s, e):
                    return True
    return False


def _rot(v, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return v[0] * c - v[1] * s, v[0] * s + v[1] * c


def _protected(ch, strokes, cap, xh):
    """Штрихи, которые нельзя ни с чем сшивать: точки, кратка."""
    H = cap if ch.isupper() else xh
    prot = set()
    for i, p in enumerate(strokes):
        ys = [q[1] for q in p]
        lo, hi = min(ys), max(ys)
        if ch in "Йй" and lo >= 0.85 * H:
            prot.add(i)
        elif ch in "Ёё" and lo >= 0.78 * H and _diag(p) < 0.16 * cap:
            prot.add(i)
        elif ch in "!?" and hi < 0.16 * cap:
            prot.add(i)
        elif ch == ";" and lo > 0.15 * cap:
            prot.add(i)
    return prot


def heal_glyph(ch, flat_strokes, cap, xh):
    """Вернуть (новые штрихи, число сшивок, число дотяжек, убрано клякс)."""
    strokes = [_pts(s) for s in flat_strokes]
    strokes = [p for p in strokes if len(p) >= 1]
    if ch in NO_HEAL or len(strokes) == 0:
        return flat_strokes, 0, 0, 0
    reach = 0.07 * cap
    guides = _guides(cap, xh)
    band = BAND * cap

    # кляксы вдали от буквы
    removed = 0
    if ch.isalnum() and ch not in KEEP_SPECKS and len(strokes) > 1:
        prot0 = _protected(ch, strokes, cap, xh)
        keep = []
        for i, p in enumerate(strokes):
            if i not in prot0 and _diag(p) < SPECK * cap and _length(p) < 2 * SPECK * cap:
                near = min(_seg_dist(q, a, b)[0]
                           for j, o in enumerate(strokes) if j != i
                           for q in p
                           for a, b in (zip(o, o[1:]) if len(o) > 1 else [(o[0], o[0])]))
                if near > SPECK_FAR * cap:
                    removed += 1
                    continue
            keep.append(p)
        strokes = keep

    joins = touches = 0

    # 1) конец к концу, жадно по лучшей оценке
    while True:
        prot = _protected(ch, strokes, cap, xh)
        best = None
        for i, p in enumerate(strokes):
            if i in prot:
                continue
            for j in range(i, len(strokes)):
                if j in prot:
                    continue
                q = strokes[j]
                for ei in (0, 1):
                    for ej in (0, 1):
                        if i == j and ei >= ej:
                            continue
                        a = p[-1] if ei else p[0]
                        b = q[-1] if ej else q[0]
                        d = math.dist(a, b)
                        if d < 1e-6 or d > GAP * cap:
                            continue
                        if not _across(a, b, guides, band):
                            continue
                        if i == j and _length(p) < 3 * d:
                            continue
                        ta = _tangent(p, ei, reach)
                        tb = _tangent(q, ej, reach)
                        v = ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
                        ca = ta[0] * v[0] + ta[1] * v[1] if ta else 0.5
                        cb = -(tb[0] * v[0] + tb[1] * v[1]) if tb else 0.5
                        if d > 0.04 * cap and (ca < 0.4 or cb < 0.4):
                            continue
                        if d > 0.03 * cap and _crosses(
                                _hermite(a, ta or v, b, tb or (-v[0], -v[1])),
                                a, b, strokes):
                            continue
                        score = d * (3.2 - ca - cb)
                        if best is None or score < best[0]:
                            best = (score, i, ei, j, ej, ta, tb)
        if best is None:
            break
        _, i, ei, j, ej, ta, tb = best
        p, q = strokes[i], strokes[j]
        a = p[-1] if ei else p[0]
        b = q[-1] if ej else q[0]
        ta = ta or ((b[0] - a[0]) / math.dist(a, b), (b[1] - a[1]) / math.dist(a, b))
        tb = tb or ((a[0] - b[0]) / math.dist(a, b), (a[1] - b[1]) / math.dist(a, b))
        bridge = _hermite(a, ta, b, tb, n=max(2, min(8, int(math.dist(a, b) / (0.03 * cap)) + 1)))
        if i == j:                       # замкнуть петлю
            path = p if ei else p[::-1]  # идёт к концу a
            strokes[i] = path + bridge + [path[0]]
        else:
            pa = p if ei else p[::-1]    # заканчивается в a
            qb = q if not ej else q[::-1]  # начинается в b
            strokes[i] = pa + bridge + qb
            del strokes[j]
        joins += 1

    # 2) конец к чужой линии (Т-образное касание)
    prot = _protected(ch, strokes, cap, xh)
    ext = []
    for i, p in enumerate(strokes):
        if i in prot or len(p) < 2:
            continue
        for ei in (0, 1):
            a = p[-1] if ei else p[0]
            # уже касается чего-то
            touching = False
            for j, o in enumerate(strokes):
                if j == i or len(o) < 2:
                    continue
                if min(_seg_dist(a, s, e)[0] for s, e in zip(o, o[1:])) < 0.025 * cap:
                    touching = True
                    break
            if touching:
                continue
            if not any(abs(a[1] - g) <= band for g in guides):
                continue
            ta = _tangent(p, ei, reach)
            if ta is None:
                continue
            hit = None
            for ang in (0, -8, 8, -16, 16):
                dr = _rot(ta, ang)
                lim = GAP * cap
                for j, o in enumerate(strokes):
                    if j == i or j in prot or len(o) < 2:
                        continue
                    for s, e in zip(o, o[1:]):
                        t = _ray_hit(a, dr, s, e)
                        if t is not None and t <= lim:
                            h = (a[0] + dr[0] * t, a[1] + dr[1] * t)
                            if not _across(a, h, guides, band):
                                continue
                            w = t * (1 + abs(ang) / 40)
                            if hit is None or w < hit[0]:
                                hit = (w, (a[0] + dr[0] * t, a[1] + dr[1] * t))
            if hit:
                ext.append((i, ei, hit[1]))
    for i, ei, pt in ext:
        if ei:
            strokes[i] = strokes[i] + [pt]
        else:
            strokes[i] = [pt] + strokes[i]
        touches += 1

    return [_flat(p) for p in strokes], joins, touches, removed


def heal_font(data):
    cap = float(data.get("cap", 14.0))
    xh = float(data.get("xheight", 0.64 * cap))
    stats = dict(glyphs=0, joins=0, touches=0, specks=0, pieces_before=0, pieces_after=0)
    for g in data["glyphs"]:
        before = len(g["strokes"])
        new, j, t, r = heal_glyph(g["char"], g["strokes"], cap, xh)
        g["strokes"] = new
        stats["pieces_before"] += before
        stats["pieces_after"] += len(new)
        stats["joins"] += j
        stats["touches"] += t
        stats["specks"] += r
        if j or t or r:
            stats["glyphs"] += 1
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    with open(a.src, encoding="utf-8") as f:
        data = json.load(f)
    stats = heal_font(data)
    out = a.out or a.src.rsplit(".", 1)[0] + " (сшит).json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"{out}: исправлено глифов {stats['glyphs']} из {len(data['glyphs'])}, "
          f"сшивок {stats['joins']}, дотяжек {stats['touches']}, клякс убрано {stats['specks']}; "
          f"кусков {stats['pieces_before']} -> {stats['pieces_after']}")


if __name__ == "__main__":
    sys.exit(main())
