# -*- coding: utf-8 -*-
"""
Соединения букв в слове — как в слитном письме (эксперимент).

У каждого начертания есть точка ВХОДА (сюда приходит линия от предыдущей
буквы) и точка ВЫХОДА (отсюда линия уходит к следующей). Их можно задать
руками (Glyph.entry / Glyph.exit), иначе программа берёт крайний левый и
крайний правый конец штриха — с поправкой на то, что соединяют обычно
внизу или посередине буквы, а не у верхней линии.

Соединительная линия — кривая Безье, которая выходит из буквы по
направлению последнего штриха и входит в следующую по направлению её
первого штриха. Если штрих кончается ровно в точке выхода, соединение
дописывается к нему, и перо не отрывается от бумаги.
"""

import math

from .glyphset import CAP, XH

# штрихи короче этого (в единицах шрифта) — точки и чёрточки над буквами:
# с них соединение не начинают
_DOT = 0.12 * CAP


def joinable_char(ch):
    """По умолчанию соединяются только буквы: не цифры и не знаки."""
    return ch.isalpha()


def allows_left(glyph):
    if glyph.join_l is not None:
        return bool(glyph.join_l)
    return joinable_char(glyph.char) and bool(glyph.strokes)


def allows_right(glyph):
    if glyph.join_r is not None:
        return bool(glyph.join_r)
    return joinable_char(glyph.char) and bool(glyph.strokes)


def _length(s):
    return sum(math.dist(s[i], s[i + 1]) for i in range(len(s) - 1))


def _height_penalty(y):
    """Соединение выше строчных или ниже строки — хуже."""
    return 0.6 * max(0.0, y - 0.75 * XH) + 0.6 * max(0.0, -y)


def auto_points(strokes):
    """
    -> (вход, выход): самая левая и самая правая точка буквы в нижней
    половине строчных. Конец штриха предпочтительнее — тогда соединение
    его продолжает, — но только если он почти так же удачно расположен:
    у печатной «а» концы оба справа вверху, и соединение, пришедшее туда,
    перечёркивало букву.
    """
    body = [s for s in strokes if len(s) > 1 and _length(s) >= _DOT]
    if not body:
        body = [s for s in strokes if s]
    if not body:
        return None, None
    pts = [p for s in body for p in _dense(s, 0.5)]
    ends = [p for s in body for p in (s[0], s[-1])]
    slack = 0.15 * CAP

    def pick(score):
        best = min(pts, key=score)
        end = min(ends, key=score)
        return tuple(end if score(end) <= score(best) + slack else best)

    entry = pick(lambda p: p[0] + _height_penalty(p[1]))
    exit_ = pick(lambda p: -p[0] + _height_penalty(p[1]))
    return entry, exit_


def _dense(s, step):
    """Точки полилинии не реже чем через step — чтобы найти и середину отрезка."""
    out = [s[0]]
    for a, b in zip(s, s[1:]):
        n = max(1, int(math.dist(a, b) / step))
        out.extend((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                   for k in range(1, n + 1))
    return out


def points(glyph):
    """Вход и выход начертания: заданные руками или найденные сами."""
    ae, ax = auto_points(glyph.strokes)
    return (glyph.entry or ae, glyph.exit or ax)


def nearest_on(strokes, pt):
    """
    Ближайшая к pt точка на штрихах.
    -> (номер штриха, номер отрезка, точка) или None.
    """
    best = None
    for i, s in enumerate(strokes):
        if len(s) == 1:
            d = math.dist(s[0], pt)
            if best is None or d < best[0]:
                best = (d, i, 0, tuple(s[0]))
            continue
        for j in range(len(s) - 1):
            (ax, ay), (bx, by) = s[j], s[j + 1]
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2))
            q = (ax + vx * t, ay + vy * t)
            d = math.dist(q, pt)
            if best is None or d < best[0]:
                best = (d, i, j, q)
    return None if best is None else best[1:]


def _cut(s, j, q):
    """Разрезать полилинию в точке q на отрезке j -> (до q, от q)."""
    a = list(s[:j + 1])
    b = list(s[j + 1:])
    if math.dist(a[-1], q) > 1e-6:
        a.append(q)
    if not b or math.dist(b[0], q) > 1e-6:
        b.insert(0, q)
    return a, b


def _keep(s):
    return len(s) > 1 and _length(s) > 1e-3


def prepare(strokes, entry=None, exit_=None):
    """
    Переставить штрихи так, чтобы буква начиналась в точке входа, а
    кончалась в точке выхода: тогда соединения с соседями продолжают
    штрих, а не рисуются отдельно.

    Точка входа или выхода посреди штриха — не беда: штрих разрезается
    в этом месте, линия на бумаге остаётся той же.

    -> (штрихи, где вход, где выход). «Где» — "first" (начало первого
    штриха), "last0" (начало последнего), "last" (конец последнего) или
    None, если точки нет.
    """
    ss = [list(s) for s in strokes if s]
    if not ss:
        return ss, None, None
    if entry is not None and exit_ is not None and math.dist(entry, exit_) < 0.05 * CAP:
        # вход и выход в одной точке: соединения сходятся к концу штриха
        out, _w, w_out = prepare(ss, None, exit_)
        return out, w_out, w_out
    ex = nearest_on(ss, exit_) if exit_ is not None else None
    en = nearest_on(ss, entry) if entry is not None else None

    if ex is not None and en is not None and ex[0] == en[0] and len(ss[ex[0]]) > 1:
        # вход и выход на одном штрихе: главный кусок — от входа до выхода
        i = ex[0]
        s = ss.pop(i)
        (ja, qa), (jb, qb) = (en[1], en[2]), (ex[1], ex[2])
        if (ja, _proj_t(s, ja, qa)) > (jb, _proj_t(s, jb, qb)):
            s = s[::-1]
            ja = nearest_on([s], qa)[1]
        head, rest = _cut(s, ja, qa)
        if len(rest) > 1:
            main, tail = _cut(rest, nearest_on([rest], qb)[1], qb)
        else:
            main, tail = rest, []
        others = [x for x in (head, tail) if _keep(x)] + ss
        if not others:          # буква в один штрих: он и первый, и последний
            return [main], "first", "last"
        return others + [main], "last0", "last"

    first = last = None
    if ex is not None:
        i, j, q = ex
        s = ss.pop(i)
        if len(s) > 1:
            a, b = _cut(s, j, q)
            last = a if _length(a) >= _length(b) else b[::-1]
            other = b if last is a else a
            if _keep(other):
                ss.append(other)
        else:
            last = s
        if en is not None:
            # номера штрихов сдвинулись — ищем вход заново
            en = nearest_on(ss, entry) if ss else None
    if en is not None:
        i, j, q = en
        s = ss.pop(i)
        if len(s) > 1:
            a, b = _cut(s, j, q)
            first = b if _length(b) >= _length(a) else a[::-1]
            other = a if first is b else b
            if _keep(other):
                ss.append(other)
        else:
            first = s
    out = ([first] if first else []) + ss + ([last] if last else [])
    return (out, "first" if first else None, "last" if last else None)


def _proj_t(s, j, q):
    a, b = s[j], s[j + 1]
    L = math.dist(a, b)
    return 0.0 if L < 1e-12 else math.dist(a, q) / L


def locate(strokes, where):
    """Точка по метке из prepare() на уже искажённых штрихах."""
    if not strokes or where is None:
        return None
    if where == "first":
        return strokes[0][0]
    if where == "last0":
        return strokes[-1][0]
    return strokes[-1][-1]


def tangent(stroke, at_end, reach):
    """Направление штриха у конца: усреднённое на длине reach."""
    pts = stroke[::-1] if at_end else stroke
    p0 = pts[0]
    acc = 0.0
    q = None
    for k in range(1, len(pts)):
        acc += math.dist(pts[k - 1], pts[k])
        q = pts[k]
        if acc >= reach:
            break
    if q is None or math.dist(p0, q) < 1e-9:
        return None
    dx, dy = p0[0] - q[0], p0[1] - q[1]
    if not at_end:
        dx, dy = -dx, -dy
    ln = math.hypot(dx, dy)
    return (dx / ln, dy / ln)


def connector(p0, t0, p1, t1, n=10):
    """
    Соединительная кривая от p0 (выход, направление t0) к p1 (вход,
    направление t1). Направления, смотрящие назад, выпрямляются к хорде,
    иначе кривая закручивалась петлёй.
    """
    cx, cy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(cx, cy)
    if d < 1e-9:
        return [tuple(p0), tuple(p1)]
    ch = (cx / d, cy / d)

    def tame(t):
        if t is None:
            return ch
        dot = t[0] * ch[0] + t[1] * ch[1]
        if dot < 0.3:
            k = 0.3 - dot + 0.5
            t = (t[0] + ch[0] * k * 2, t[1] + ch[1] * k * 2)
            ln = math.hypot(*t) or 1.0
            t = (t[0] / ln, t[1] / ln)
        return t

    t0, t1 = tame(t0), tame(t1)
    h = 0.38 * d
    a = (p0[0] + t0[0] * h, p0[1] + t0[1] * h)
    b = (p1[0] - t1[0] * h, p1[1] - t1[1] * h)
    out = []
    for k in range(n + 1):
        t = k / n
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * a[0] + 3 * u * t * t * b[0] + t ** 3 * p1[0],
                    u ** 3 * p0[1] + 3 * u * u * t * a[1] + 3 * u * t * t * b[1] + t ** 3 * p1[1]))
    return out
