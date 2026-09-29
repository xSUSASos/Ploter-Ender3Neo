# -*- coding: utf-8 -*-
"""
Таблицы и формулы LaTeX в тексте.

Проверяем, что таблица из Markdown и из Excel (табуляция) распознаётся,
рамка ложится вокруг текста и не задевает соседние строки, а формулы
в $...$ раскладываются в два этажа, не налезают на строки рядом и не
теряют знаков.
"""

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hw.core.config import Config                      # noqa: E402
from hw.core.glyphset import FontSet                   # noqa: E402
from hw.core.humanize import Human                     # noqa: E402
from hw.core import font_vector as FV, layout as L, mathtex as MT   # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  — " + extra) if extra else ""))
    if not cond:
        fails.append(name)


def pages(text, grid=False, human=False, **kw):
    cfg = Config()
    cfg.page.grid = grid
    for k, v in kw.items():
        setattr(cfg.page, k, v)
    cfg.human.enabled = human
    cfg.human.seed = 7
    return L.build_pages(text, FontSet(), cfg.page, Human(cfg.human)), cfg.page


def ys(strokes):
    return [p[1] for s in strokes for p in s]


print("знаки для формул:")
need = "ωφ√∞αβγδελμπστΔΣΩ−₁₂∫∂"
check("греческие и математические знаки есть в шрифте",
      all(FV.has_glyph(c) for c in need),
      "нет: " + "".join(c for c in need if not FV.has_glyph(c)))
check("ω не квадратик", FV.get_glyph("ω") != FV.get_glyph(""))

print("таблицы:")
md = "Точки:\n| t | h(t) |\n|---|---|\n| 0 | 0 |\n| 0,4 | 5,06 |\nКонец."
b = L.split_blocks(md)
check("Markdown: абзац, таблица, абзац", [k for k, _ in b] == ["par", "table", "par"],
      str([k for k, _ in b]))
check("строка-разделитель |---| не считается рядом", len(b[1][1]) == 3, str(b[1][1]))
tab = "ω\tL\n0,25\t18,0\n2,5\t15,05"
b = L.split_blocks(tab)
check("табуляция из Excel — таблица 3×2",
      b and b[0][0] == "table" and len(b[0][1]) == 3 and len(b[0][1][0]) == 2)
check("одиночная табуляция — не таблица (отступ абзаца)",
      [k for k, _ in L.split_blocks("\tКрасная строка\nтекст")] == ["par", "par"])

for grid in (False, True):
    pg, pc = pages(md, grid=grid)
    _n, lines = L.measure(L.clean_text(md), FontSet(), L.grid_cfg(pc) if grid else pc,
                          Human(Config().human), (L.grid_cfg(pc) if grid else pc).size_mm)
    L.paginate(lines, L.grid_cfg(pc) if grid else pc,
               (L.grid_cfg(pc) if grid else pc).size_mm)
    rows = [ln for ln in lines if ln.table is not None]
    t = rows[0].table
    tag = " (клетка)" if grid else ""
    check("3 ряда таблицы" + tag, len({ln.row for ln in rows}) == 3)
    check("рамка: 4 горизонтали и 3 вертикали" + tag,
          len(L._table_borders([ln for ln in lines], 0.0, Human(Config().human),
                               pc.size_mm)) == 7)
    # черты между рядами не режут буквы: у каждого ряда запас сверху и снизу
    size = (L.grid_cfg(pc) if grid else pc).size_mm
    check("над заглавными и под выносными есть место" + tag,
          t.up + t.shift - size >= 0.2 * size and t.down - t.shift - 0.29 * size >= 0.1 * size,
          "up=%.2f down=%.2f shift=%.2f size=%.2f" % (t.up, t.down, t.shift, size))
    after = lines[lines.index(rows[-1]) + 1]
    check("строка после таблицы ниже нижней черты" + tag,
          after.y - size > rows[-1].y + t.down, "%.2f > %.2f" % (after.y - size,
                                                              rows[-1].y + t.down))

wide = "| " + " | ".join(["очень длинный текст в ячейке номер %d" % i for i in range(4)]) + " |"
_n, lines = L.measure(wide, FontSet(), Config().page, Human(Config().human), 5.0)
t = lines[0].table
check("широкая таблица помещается в поля", sum(t.cols) <= Config().page.text_w + 1e-6,
      "%.1f > %.1f" % (sum(t.cols), Config().page.text_w))
check("длинный текст ячейки переносится на строки", len(lines) > 1)

print("формулы:")
parts = MT.tokens("и $a + b$, где $x^2$")
check("формула с пробелами — одно слово", len(parts) == 4 and parts[1][0][0] == "m",
      str(parts))
check("знак после формулы остаётся в слове", parts[1][-1] == ("t", ","))
m = MT.metrics_for(FontSet())
frac = MT.layout(r"\frac{1}{1+0,16\omega^2}", m)
check("дробь в два этажа: выше заглавной и ниже базовой линии",
      frac.h > 14 and frac.d > 2, "h=%.1f d=%.1f" % (frac.h, frac.d))
lines_in = [it for it in frac.items if it[0] == "l"]
check("у дроби есть черта", len(lines_in) == 1)
chars = "".join(it[1] for it in frac.items if it[0] == "g")
check("ничего не потеряно", chars == "11+0,16ω2", chars)
sq = MT.layout(r"\sqrt{2}", m)
check("корень: знак с крышкой над подкоренным",
      any(it[0] == "l" and len(it[1]) == 5 for it in sq.items))
sup = MT.layout("e^{-t}", m)
g = [it for it in sup.items if it[0] == "g"]
check("степень выше и мельче основы", g[1][3] > 5 and g[1][4] < 1.0)
sub = MT.layout(r"\omega_c", m)
g = [it for it in sub.items if it[0] == "g"]
check("индекс ниже базовой линии", g[1][3] < 0)
dec = MT.layout("0,16", m)
x = [it[2] for it in dec.items if it[0] == "g"]
check("десятичная запятая без пробела", x[2] - x[1] < 6.5, str(x))
unk = MT.layout(r"\foo", m)
check("незнакомая команда пишется именем",
      "".join(it[1] for it in unk.items if it[0] == "g") == "foo")
lr = MT.layout(r"\left(\frac{a}{b}\right)", m)
paren = [it for it in lr.items if it[0] == "g" and it[1] == "("][0]
check("\\left( растягивается по высоте дроби", paren[5] > 1.1, "sy=%.2f" % paren[5])

text = "Строка сверху\n$$A = \\frac{8}{\\sqrt{1 + 0,16\\omega^2}}$$\nСтрока снизу"
for grid in (False, True):
    pg, pc = pages(text, grid=grid, human=True)
    _n, lines = L.measure(text, FontSet(), L.grid_cfg(pc) if grid else pc,
                          Human(Config().human), (L.grid_cfg(pc) if grid else pc).size_mm)
    L.paginate(lines, L.grid_cfg(pc) if grid else pc,
               (L.grid_cfg(pc) if grid else pc).size_mm)
    size = (L.grid_cfg(pc) if grid else pc).size_mm
    a, f, c = lines
    tag = " (клетка)" if grid else ""
    check("формула не налезает на строку выше" + tag,
          f.y - f.height_mm > a.y + 0.29 * size, "%.2f / %.2f" % (f.y - f.height_mm, a.y))
    check("и на строку ниже" + tag, c.y - size > f.y + f.depth_mm)
    check("$$…$$ по центру" + tag, f.center)
    if grid:
        step = L.grid_step(pc)
        check("строки остались на линиях клетки",
              all(abs((ln.y - lines[0].y) / step - round((ln.y - lines[0].y) / step)) < 1e-6
                  for ln in lines))

pg, _ = pages("Формула: $\\frac{a}{b}$ и таблица\n| $\\omega$ | $x^2$ |\n| 1 | 2 |",
              human=True)
check("формулы в ячейках таблицы рисуются", pg and len(pg[0].strokes) > 20)

print("\nИТОГ: " + ("все проверки пройдены" if not fails else "СБОИ: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
