# -*- coding: utf-8 -*-
"""
Эксперимент: почерк со свободного листа и соединения букв.

Лист «пишем» встроенным шрифтом на бумаге в клетку — раздельно и слитно,
с наклоном, — портим косым светом и шумом и проверяем, что программа
находит строки, наклон, границы букв и собирает из них шрифт. Отдельно —
что соединения букв не рвут и не теряют штрихи.
"""

import io
import math
import os
import random
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from hw.core import freehand as FH, joins as JN, layout as L
from hw.core.config import Config
from hw.core.glyphset import CAP, FontSet, Glyph
from hw.core.humanize import Human, affine

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  — " + extra) if extra else ""))
    if not cond:
        fails.append(name)


LINES = ["мама мыла раму", "Привет, как дела?", "шиш любовь надежда"]


def make_sheet(cursive=False, slant=0.0, ppm=12, size_mm=7, pen=3, seed=1):
    """-> (картинка, [(базовая линия, [[(лево, право) чернил буквы], ...]), ...])."""
    W, H = int(160 * ppm), int(40 * ppm + len(LINES) * size_mm * 2.0 * ppm)
    im = Image.new("RGB", (W, H), (246, 244, 238))
    d = ImageDraw.Draw(im)
    for x in range(0, W, 5 * ppm):
        d.line([(x, 0), (x, H)], fill=(160, 185, 215), width=2)
    for y in range(0, H, 5 * ppm):
        d.line([(0, y), (W, y)], fill=(160, 185, 215), width=2)
    fs = FontSet()
    sc = size_mm / CAP * ppm
    truth = []
    y = 25 * ppm
    for text in LINES:
        x = 12 * ppm
        tl = []
        for word in text.split():
            bounds, prev = [], None
            for ch in word:
                g = fs.pick(ch)[0]
                ss = affine(g.strokes, shear=slant)
                pts = [[(x + px * sc, y - py * sc) for px, py in s] for s in ss]
                for s in pts:
                    if len(s) > 1:
                        d.line(s, fill=(25, 25, 60), width=pen, joint="curve")
                if cursive:
                    pin, pout = JN.auto_points(ss)
                    if prev is not None and pin is not None:
                        d.line(JN.connector(prev, None, (x + pin[0] * sc, y - pin[1] * sc), None),
                               fill=(25, 25, 60), width=pen, joint="curve")
                    prev = (x + pout[0] * sc, y - pout[1] * sc) if pout else None
                xs = [q[0] for s in pts for q in s] or [x]
                bounds.append((min(xs), max(xs)))
                x += g.advance * sc
            tl.append(bounds)
            x += fs.pick(" ")[0].advance * sc * 1.1
        truth.append((y, tl))
        y += size_mm * 2.0 * ppm
    a = np.asarray(im).astype(float)
    gy, gx = np.mgrid[0:H, 0:W]
    a *= (0.8 + 0.2 * gx / W)[..., None]
    a += np.random.default_rng(seed).normal(0, 4, a.shape)
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    return im, truth


def cut_scores(fs, truth):
    """Доля разрезов, попавших между чернилами соседних букв (с допуском)."""
    good = total = 0
    for ln, (_y, tl) in zip(fs.lines, truth):
        for wb, tb in zip(ln.words, tl):
            wl = (tb[-1][1] - tb[0][0]) / len(tb)
            for k in range(1, len(tb)):
                c = wb[k] / fs.factor
                lo, hi = sorted((tb[k - 1][1], tb[k][0]))
                total += 1
                good += max(0, lo - c, c - hi) < 0.12 * wl
    return good / max(total, 1)


print("свободный лист, раздельные буквы на клетке:")
im, truth = make_sheet()
fs = FH.FreeSheet(im)
fs.set_text("\n".join(LINES))
check("найдено 3 строки", len(fs.lines) == 3 and not fs.warnings, str(fs.warnings))
check("клетка убрана: в строках нет длинных линий",
      all(ln.box[2] - ln.box[0] < 0.8 * fs.image.width for ln in fs.lines))
ok = all(abs(ln.base / fs.factor - y) < 0.1 * 7 * 12 for ln, (y, _t) in zip(fs.lines, truth))
check("базовая линия на месте", ok,
      " ".join("%.0f/%.0f" % (ln.base / fs.factor, y) for ln, (y, _t) in zip(fs.lines, truth)))
check("слова разделены верно", all(len(ln.words) == len(ln.text_words) and not ln.note
                                   for ln in fs.lines))
sc = cut_scores(fs, truth)
check("буквы разрезаны по просветам", sc >= 0.95, "%.0f%%" % (100 * sc))
gl = fs.glyphs()
chars = "".join(c for c, _g, _w in gl)
check("буквы вырезаны все", chars == "".join(LINES).replace(" ", ""), chars)
g_m = next(g for c, g, _w in gl if c == "м")
xs = [p[0] for s in g_m.strokes for p in s]
ys = [p[1] for s in g_m.strokes for p in s]
check("«м» нормирована по высоте строчных", abs(max(ys) - 9.0) < 1.6 and min(ys) > -1.2,
      "y %.1f..%.1f" % (min(ys), max(ys)))
check("«м» без кусков соседей", max(xs) - min(xs) < 1.5 * FontSet().pick("м")[0].advance,
      "ширина %.1f" % (max(xs) - min(xs)))

print("свободный лист, слитно и с наклоном 12°:")
im, truth = make_sheet(cursive=True, slant=12)
fs = FH.FreeSheet(im)
fs.set_text("\n".join(LINES))
check("найдено 3 строки", len(fs.lines) == 3)
sl = [math.degrees(math.atan(ln.slant)) for ln in fs.lines]
check("наклон найден (12±4°)", all(abs(v - 12) < 4 for v in sl),
      " ".join("%.1f" % v for v in sl))
check("слова разделены верно", all(len(ln.words) == len(ln.text_words) for ln in fs.lines))
gl = fs.glyphs()
check("букв вырезано не меньше 90%", len(gl) >= 0.9 * len("".join(LINES).replace(" ", "")),
      str(len(gl)))
inner = [g for c, g, (li, wi, k) in gl if 0 < k < len(fs.lines[li].text_words[wi]) - 1]
check("у букв внутри слова вход и выход — на линиях разреза",
      sum(g.entry is not None and g.exit is not None for g in inner) >= 0.7 * len(inner),
      "%d из %d" % (sum(g.entry is not None and g.exit is not None for g in inner), len(inner)))

print("правка руками:")
ln = fs.lines[0]
b = ln.words[0]
old = b[1]
fs.move_boundary(ln, 0, 1, b[2] + 100)          # за соседнюю границу не уходит
check("граница упирается в соседнюю", b[1] < b[2], "%.1f < %.1f" % (b[1], b[2]))
fs.move_boundary(ln, 0, 1, old)
check("граница вернулась", abs(b[1] - old) < 1e-9)
fs.move_boundary(ln, 1, 0, -1e6)                # край слова — не за предыдущее слово
check("край слова не заходит на соседнее", ln.words[1][0] > ln.words[0][-1])

print("пустой и неверный текст:")
fs2 = FH.FreeSheet(im)
fs2.set_text("одна строка")
check("строк в тексте меньше — предупреждение", bool(fs2.warnings) and len(fs2.lines) == 1)
fs2.set_text("")
check("пустой текст — ни одной строки", fs2.lines == [])

print("соединения букв:")
g = Glyph("x", 10, [[(0, 0), (5, 9), (10, 0)], [(2, 12), (2.5, 12.5)]])
st, w_in, w_out = JN.prepare(g.strokes, (0, 0), (10, 0))
check("вход и выход на одном штрихе: главный кусок последним",
      JN.locate(st, w_in) == (0, 0) and JN.locate(st, w_out) == (10, 0), str((w_in, w_out)))
st, w_in, w_out = JN.prepare([[(0, 0), (4, 0)], [(6, 0), (10, 0)]], (2, 0), (8, 0))
ink = sum(math.dist(s[i], s[i + 1]) for s in st for i in range(len(s) - 1))
check("точка посреди штриха: штрих разрезан, линия та же", abs(ink - 8) < 1e-6
      and JN.locate(st, w_in) == (2, 0) and JN.locate(st, w_out) == (8, 0))
con = JN.connector((0, 0), (-1, 0), (5, 0), (1, 0))
check("соединение не закручивается петлёй", all(con[i][0] <= con[i + 1][0] + 1e-9
                                                for i in range(len(con) - 1)))

d = Glyph("а", 8, [[(0, 0), (1, 1)]], entry=(0.5, 0.5), exit=(1.0, 1.0), join_r=False).to_dict()
g2 = Glyph.from_dict(d)
check("вход/выход/запрет сохраняются в файл",
      g2.entry == (0.5, 0.5) and g2.exit == (1.0, 1.0) and g2.join_r is False and g2.join_l is None)
check("старые шрифты не меняются", "entry" not in Glyph("б", 8, [[(0, 0), (1, 1)]]).to_dict())


def pen_downs(joins, font, text="мама мыла раму надежда", human=True):
    cfg = Config()
    cfg.human.joins = joins
    cfg.human.seed = 5
    cfg.human.enabled = human
    pages = L.build_pages(text, font, cfg.page, Human(cfg.human))
    return pages[0].strokes


font = FontSet()
a, b = pen_downs(False, font), pen_downs(True, font)
check("со соединениями перо отрывается реже", len(b) < len(a), "%d -> %d" % (len(a), len(b)))
ink = lambda ss: sum(math.dist(s[i], s[i + 1]) for s in ss for i in range(len(s) - 1))
check("соединения добавляют линию, а не теряют её", ink(b) > ink(a), "%.0f -> %.0f мм" % (ink(a), ink(b)))
c = pen_downs(True, font, "12 3,4")
check("цифры и знаки не соединяются", len(c) == len(pen_downs(False, font, "12 3,4")))
off = FontSet()
off.add(Glyph("м", 10, [[(0, 0), (3, 9), (6, 0), (9, 9), (10, 0)]], join_r=False))
check("запрет «не соединять справа» работает",
      len(pen_downs(True, off, "мм", human=False)) == 2)
own = FontSet()
for ch, gg, _w in fs.glyphs():
    own.add(gg)
check("шрифт со свободного листа пишет текст с соединениями",
      len(pen_downs(True, own, "мама мыла раму")) > 0)

print("командная строка:")
from hw import cli
tmp = tempfile.mkdtemp(prefix="hw_free_")
im.save(os.path.join(tmp, "лист.png"))
out = os.path.join(tmp, "шрифт.json")
rc = cli.main(["freehand", "-p", os.path.join(tmp, "лист.png"), "-T", "\n".join(LINES), "-o", out])
check("freehand -> шрифт", rc == 0 and os.path.exists(out) and len(FontSet.load(out).variants) > 10)
rc = cli.main(["text", "--text", "мама", "--font", out, "--joins", "-o",
               os.path.join(tmp, "t.gcode")])
check("text --joins", rc == 0 and os.path.exists(os.path.join(tmp, "t.gcode")))

print()
print("ИТОГ:", "все проверки пройдены" if not fails else "СБОИ: " + ", ".join(fails))
sys.exit(1 if fails else 0)
