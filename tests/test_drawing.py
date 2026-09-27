# -*- coding: utf-8 -*-
"""Рисунки пером и поворот текста: ядро, G-code, окно."""
import sys, io, os, math, tempfile, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HW_STATE"] = os.path.join(tempfile.mkdtemp(prefix="hwstate_"), "state.json")

from PIL import Image, ImageDraw

from hw.core.config import Config
from hw.core.glyphset import FontSet
from hw.core.humanize import Human
from hw.core import drawing as DR, gcode as GC, layout as L

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fails.append(name)


def inside(strokes, box, tol=0.6):
    x0, y0, x1, y1 = box
    return all(x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
               for s in strokes for x, y in s)


TMP = tempfile.mkdtemp(prefix="hwdraw_")
im = Image.new("RGB", (900, 700), "white")
d = ImageDraw.Draw(im)
d.ellipse([100, 100, 500, 500], fill=(60, 60, 60))
d.polygon([(600, 300), (800, 300), (700, 450)], fill="black")
d.line([80, 640, 860, 640], fill="black", width=5)
IMG = os.path.join(TMP, "pic.png")
im.save(IMG)

# линейный рисунок для осевой линии: «человечек» тонкими линиями
la = Image.new("L", (600, 600), 255)
dd = ImageDraw.Draw(la)
dd.ellipse([250, 60, 350, 160], outline=0, width=6)
dd.line([300, 160, 300, 380], fill=0, width=6)
dd.line([200, 240, 400, 240], fill=0, width=6)
dd.line([300, 380, 220, 540], fill=0, width=6)
dd.line([300, 380, 380, 540], fill=0, width=6)
LINEART = os.path.join(TMP, "man.png")
la.save(LINEART)

SVG = os.path.join(TMP, "t.svg")
with open(SVG, "w", encoding="utf-8") as f:
    f.write('''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
 viewBox="0 0 100 100"><defs><circle id="c" cx="0" cy="0" r="5"/></defs>
<g transform="translate(10,10) rotate(45 20 20)"><rect width="40" height="40"/></g>
<path d="M10 80 C20 60 40 60 50 80 S80 100 90 80 Q95 70 90 60 T80 40 A10 5 30 0110 20z"/>
<path d="m5 5 10 0 0 10z"/><use xlink:href="#c" x="70" y="20"/>
<ellipse cx="50" cy="50" rx="10" ry="4" style="display:none"/></svg>''')

print("SVG:")
paths = DR.parse_path("M0 0 L10 0 l0 10 h-10 v-10 z")
check("путь с H/V/Z разобран", len(paths) == 1 and paths[0][-1] == (0.0, 0.0)
      and (10.0, 10.0) in paths[0], str(paths))
arc = DR.parse_path("M0 0 A10 10 0 0 1 20 0")[0]
check("дуга — полуокружность радиуса 10",
      all(abs(math.dist(p, (10, 0)) - 10) < 1e-6 for p in arc)
      and min(p[1] for p in arc) < -9.9, "точек %d" % len(arc))
check("флаги дуги без пробелов", len(DR.parse_path("M0 0a5 5 0 0110 0")[0]) > 4)
m = DR.parse_transform("translate(10 5) scale(2)")
check("transform: translate·scale", m == (2, 0, 0, 2, 10, 5), str(m))
src = DR.ArtSource(SVG)
check("SVG: 4 фигуры, скрытая пропущена", len(src.svg) == 4, src.describe())
use = [p for p in src.svg if len(p) == 73]
check("<use> на кружок со сдвигом", use and abs(DR.bbox_of(use)[0] - 65) < 0.01)

print("картинка:")
cfg = Config()
pic = DR.ArtSource(IMG)
area = (cfg.page.margin_left, cfg.page.margin_bottom,
        cfg.page.margin_left + cfg.page.text_w, cfg.page.sheet_h - cfg.page.margin_top)
counts = {}
for mode in DR.MODES:
    cfg.draw.mode = mode
    t = time.time()
    st, box = DR.build_art(pic, [], cfg.draw, cfg.page)
    counts[mode] = len(st)
    check("режим %-14s даёт линии в полях" % mode, st and inside(st, area),
          "линий %d, %.2f с" % (len(st), time.time() - t))
check("контур: овал, треугольник и полоса", counts["outline"] >= 3,
      str(counts["outline"]))

cfg.draw.mode = "hatch"
cfg.draw.hatch_step = 1.0
n1 = len(DR.build_art(pic, [], cfg.draw, cfg.page)[0])
cfg.draw.hatch_step = 2.0
n2 = len(DR.build_art(pic, [], cfg.draw, cfg.page)[0])
check("штриховка реже при большем шаге", n2 < n1 * 0.7, "%d -> %d" % (n1, n2))
cfg.draw.hatch_levels, cfg.draw.hatch_angle, cfg.draw.optimize = 1, 0.0, False
st, _ = DR.build_art(pic, [], cfg.draw, cfg.page)
flat = sum(1 for s in st if abs(s[0][1] - s[-1][1]) < 1e-6)
check("угол штриховки 0° — линии горизонтальны на бумаге", flat == len(st),
      "%d из %d" % (flat, len(st)))
cfg.draw.rotate = 90
st, _ = DR.build_art(pic, [], cfg.draw, cfg.page)
flat = sum(1 for s in st if abs(s[0][1] - s[-1][1]) < 1e-6)
check("и при повёрнутой картинке тоже", flat == len(st), "%d из %d" % (flat, len(st)))
cfg.draw.rotate, cfg.draw.optimize = 0.0, True

man = DR.ArtSource(LINEART)
cfg.draw.mode = "centerline"
st, _ = DR.build_art(man, [], cfg.draw, cfg.page)
check("осевая линия человечка — одна линия на штрих, без двойных контуров",
      2 <= len(st) <= 8, "линий %d" % len(st))

print("размещение:")
cfg = Config()
cfg.draw.mode = "outline"
cfg.draw.width_mm, cfg.draw.anchor = 50.0, "top-left"
st, box = DR.build_art(pic, [], cfg.draw, cfg.page)
check("ширина 50 мм", abs((box[2] - box[0]) - 50) < 0.01, "%.2f" % (box[2] - box[0]))
check("привязка вверху слева", abs(box[0] - cfg.page.margin_left) < 0.01
      and abs(box[3] - (cfg.page.sheet_h - cfg.page.margin_top)) < 0.01)
cfg.draw.offset_x, cfg.draw.offset_y = 10, 5
_st, box2 = DR.build_art(pic, [], cfg.draw, cfg.page)
check("сдвиг вправо и вниз", abs(box2[0] - box[0] - 10) < 1e-6
      and abs(box[3] - box2[3] - 5) < 1e-6)
cfg.draw.offset_x = cfg.draw.offset_y = 0
cfg.draw.mirror = True
stm, _ = DR.build_art(pic, [], cfg.draw, cfg.page)
check("отражение не меняет рамку", inside(stm, box))

print("доводка:")
segs = [[(float(i * 10), 0.0), (float(i * 10 + 5), 0.0)] for i in range(10)][::-1]
cfgd = Config().draw
o = DR.order_paths(segs, join=0.0)
travel = sum(math.dist(o[i][-1], o[i + 1][0]) for i in range(len(o) - 1))
check("порядок: холостые между отрезками минимальны", travel <= 45.01, "%.1f" % travel)
o = DR.order_paths([[(0, 0), (5, 0)], [(5.2, 0), (9, 0)]], join=0.3)
check("близкие штрихи склеены без подъёма пера", len(o) == 1)
cfgd.passes = 3
out = DR.finish([[(0.0, 0.0), (10.0, 0.0)]], cfgd)
check("обвести 3 раза — туда-обратно-туда", len(out) == 1 and len(out[0]) == 4
      and out[0][-1] == (10.0, 0.0), str(out))

print("G-code:")
cfg = Config()
cfg.draw.speed_pct = 50
art, _ = DR.build_art(pic, [], cfg.draw, cfg.page)
res = GC.generate([L.Page(art=art)], cfg)
check("рисунок в G-code, без нагрева", "; рисунок" in res.text and not GC.validate(res.text))
check("скорость рисунка 50%", " F%d" % (cfg.pen.feed_draw // 2) in res.text)
check("не выходит за стол", not res.warnings, "; ".join(res.warnings))

print("текст под рисунком:")
cfg = Config()
cfg.draw.layout = "above"
art, box = DR.build_art(pic, [], cfg.draw, cfg.page)
skip = DR.text_skip(art, cfg.page, cfg.draw.gap_mm)
hu = Human(cfg.human)
pages = L.build_pages("Строка текста. " * 40, FontSet(), cfg.page, hu, first_skip=skip)
top_text = max(y for s in pages[0].strokes for _x, y in s)
check("рисунок не выше половины полей", box[3] - box[1] <= cfg.page.text_h * 0.5 + 0.01)
check("текст начинается ниже рисунка", top_text < min(y for s in art for _x, y in s),
      "текст до %.1f, рисунок от %.1f" % (top_text, min(y for s in art for _x, y in s)))
big = L.build_pages("Короткий текст.", FontSet(), cfg.page, hu, first_skip=cfg.page.text_h)
check("рисунок на весь лист — текст уходит на второй", len(big) == 2
      and not big[0].strokes and big[1].strokes)

print("поворот текста:")
cfg = Config()
cfg.human.enabled = False
hu = Human(cfg.human)
base = L.build_pages("Привет мир", FontSet(), cfg.page, hu)[0]
cfg.page.text_angle = 90.0
cfg.page.text_angle_fit = False
rot = L.build_pages("Привет мир", FontSet(), cfg.page, hu)[0]
bw = base.bbox()
rw = rot.bbox()
check("на 90° ширина строки стала высотой",
      abs((bw[2] - bw[0]) - (rw[3] - rw[1])) < 0.05,
      "%.2f vs %.2f" % (bw[2] - bw[0], rw[3] - rw[1]))
cfg.page.text_angle = 30.0
cfg.page.text_angle_fit = True
rep = {}
pages = L.build_pages("Длинная строка текста. " * 20, FontSet(), cfg.page, hu, report=rep)
x0, x1 = cfg.page.margin_left, cfg.page.margin_left + cfg.page.text_w
y0, y1 = cfg.page.margin_bottom, cfg.page.sheet_h - cfg.page.margin_top
check("под 30° текст ужат и не вылезает из полей",
      rep.get("angle_scale", 1) < 1 and inside(pages[0].strokes, (x0, y0, x1, y1), 0.01),
      "масштаб %.2f" % rep.get("angle_scale", 1))
cfg.page.text_angle = 17.0
res = GC.generate(pages, cfg, hu, page_index=0)
check("повёрнутый текст — чистый G-code", not GC.validate(res.text))

print("тетрадь в клетку:")


def baselines(page, step=5.0):
    """Низы строк из «н»: без выносных низ буквы и есть базовая линия."""
    ys = sorted({round(min(y for _x, y in s), 3) for s in page.strokes}, reverse=True)
    groups = []
    for y in ys:
        if not groups or groups[-1][0] - y > step / 2.0:
            groups.append([y])
        else:
            groups[-1].append(y)
    return [min(g) for g in groups]


cfg = Config()
cfg.human.enabled = False
cfg.page.grid, cfg.page.grid_cell, cfg.page.grid_first = True, 5.0, 20.0
hu = Human(cfg.human)
txt = " ".join(["нннн"] * 400)
pages = L.build_pages(txt, FontSet(), cfg.page, hu)
b1 = baselines(pages[0])
want = [cfg.page.sheet_h - 20.0 - 5.0 * k for k in range(len(b1))]
check("строки точно на линиях клетки, в каждой", len(pages) > 1 and len(b1) > 20
      and all(abs(a - b) < 1e-6 for a, b in zip(b1, want)),
      "строк на листе %d, первая %.2f" % (len(b1), b1[0]))
check("высота заглавной — 0.8 клетки", abs(pages[0].size_mm - 4.0) < 1e-9)
check("строк столько, сколько обещано в окне",
      len(b1) == len(L.grid_baselines(cfg.page)), "%d / %d"
      % (len(b1), len(L.grid_baselines(cfg.page))))
b2 = baselines(pages[1])
check("на втором листе тоже с первой строки", abs(b2[0] - (cfg.page.sheet_h - 20)) < 1e-6)
cfg.page.grid_every = 2
b = baselines(L.build_pages(txt, FontSet(), cfg.page, hu)[0], 10.0)
check("через клетку — шаг 10 мм", all(abs((b[i] - b[i + 1]) - 10.0) < 1e-6
                                        for i in range(len(b) - 1)))
cfg.page.grid_every, cfg.page.paragraph_gap = 1, 3.0
b = baselines(L.build_pages("нннн\nнннн", FontSet(), cfg.page, hu)[0])
check("отбивка абзаца округлена до целой клетки", abs((b[0] - b[1]) - 10.0) < 1e-6,
      "%.2f" % (b[0] - b[1]))
cfg.page.paragraph_gap = 0.0
b = baselines(L.build_pages("нннн", FontSet(), cfg.page, hu, first_skip=13.0)[0])
check("под рисунком строка тоже на линии", abs(b[0] - (cfg.page.sheet_h - 35.0)) < 1e-6,
      "%.2f" % b[0])
cfg.page.autofit = True
b = baselines(L.build_pages(txt, FontSet(), cfg.page, hu)[0])
check("автоподбор не сбивает клетку", abs(b[0] - (cfg.page.sheet_h - 20)) < 1e-6
      and abs(b[0] - b[1] - 5.0) < 1e-6)

print("каллиграфия:")
from hw.core import humanize as HM
from hw.core.config import HUMAN_PRESETS
bar = [[(0.0, 0.0), (0.0, 10.0)]]          # вертикаль
hor = [[(0.0, 0.0), (10.0, 0.0)]]          # горизонталь
def spread(strokes):
    b = DR.bbox_of(strokes)
    return b[2] - b[0], b[3] - b[1]
bw = spread(HM.calligraphy(bar, "broad", 1.0, 0.0, 0.3))[0]
hw_ = spread(HM.calligraphy(hor, "broad", 1.0, 0.0, 0.3))[1]
check("широкое перо под 0°: вертикаль широкая, горизонталь волосяная",
      abs(bw - 1.0) < 1e-6 and hw_ < 1e-6, "%.2f / %.2f" % (bw, hw_))
down = [[(0.0, 10.0), (0.0, 0.0)]]
up = [[(0.0, 0.0), (0.0, 10.0)]]
wd = spread(HM.calligraphy(down, "pointed", 1.0, 0.0, 0.3))[0]
wu = spread(HM.calligraphy(up, "pointed", 1.0, 0.0, 0.3))[0]
check("острое перо: нажим вниз, вверх — волосяная", wd > 0.8 and wu < 0.05,
      "вниз %.2f, вверх %.2f" % (wd, wu))
one = HM.calligraphy(bar, "broad", 1.0, 30.0, 0.3)
check("проходы идут одним штрихом, перо не отрывается", len(one) == 1)
for name in ("Каллиграфия", "Каллиграфия, острое перо"):
    cfg = Config(); cfg.human = HUMAN_PRESETS[name]()
    hu = Human(cfg.human)
    pg = L.build_pages("Привет, мир", FontSet(), cfg.page, hu)
    plain = Config(); plain.human.enabled = False
    pl = L.build_pages("Привет, мир", FontSet(), plain.page, Human(plain.human))
    res = GC.generate(pg, cfg, hu, page_index=0)
    check("набор «%s»: линия толще, штрихов столько же, G-code чистый" % name,
          pg[0].ink_length() > 2.5 * pl[0].ink_length()
          and len(pg[0].strokes) == len(pl[0].strokes)
          and pg[0].pen_mm > 0 and not GC.validate(res.text))

print("каллиграфическая пропись:")
cf = FontSet.load(FontSet.CALLIG)
need = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
           "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ0123456789")
have = {c for c in cf.variants}
check("в прописи все буквы и цифры", need <= have, "нет: %s" % "".join(sorted(need - have)))
check("у «д» два начертания, как в образце", cf.count("д") == 2)
xh = [max(p[1] for s in cf.get_variants(c)[0].strokes for p in s) for c in "аимнос"]
check("строчные в прописи ниже заглавных в ~2.4 раза", 4.5 < sum(xh) / len(xh) < 7.0,
      "%.2f" % (sum(xh) / len(xh)))
cfg = Config()
cfg.human = HUMAN_PRESETS["Каллиграфия, острое перо"]()
hu = Human(cfg.human)
pg = L.build_pages("Ёжик в тумане, 1987 год.", cf, cfg.page, hu)
res = GC.generate(pg, cfg, hu, page_index=0)
check("текст прописью — чистый G-code", pg[0].strokes and not GC.validate(res.text))

print("поворот листа на столе:")
cfg = Config()
cfg.page.rotate = 90.0
old = lambda p: (cfg.page.origin_x + cfg.page.sheet_h - p[1], cfg.page.origin_y + p[0])
check("90° как раньше", all(math.dist(GC.sheet_to_bed(p, cfg.page), old(p)) < 1e-9
                           for p in ((0, 0), (10, 20), (148, 210))))
cfg.page.rotate = 37.0
cs = [GC.sheet_to_bed(p, cfg.page) for p in ((0, 0), (148, 0), (0, 210), (148, 210))]
w, h = GC.sheet_extent(cfg.page)
check("37°: габарит начинается в углу листа на столе",
      abs(min(c[0] for c in cs) - cfg.page.origin_x) < 1e-9
      and abs(min(c[1] for c in cs) - cfg.page.origin_y) < 1e-9
      and abs(max(c[0] for c in cs) - min(c[0] for c in cs) - w) < 1e-9)
check("стороны листа не искажены", abs(math.dist(cs[0], cs[1]) - 148) < 1e-9
      and abs(math.dist(cs[0], cs[2]) - 210) < 1e-9)

print("окно:")
import tkinter.messagebox as mb
mb.askyesno = lambda *a, **k: True
from hw.gui.app import App
from types import SimpleNamespace as NS
app = App()
app.withdraw()
for _ in range(40):
    app.update()
    if app._photo is not None:
        break
    time.sleep(0.02)
have = {(f.section, f.attr) for f in app.fields}
need = {("draw", "mode"), ("draw", "layout"), ("draw", "hatch_step"),
        ("draw", "passes"), ("page", "text_angle"), ("human", "calli_style")}
check("настройки рисунка и поворота привязаны", need <= have, str(sorted(need - have)))

app.nb.select(app.tab_draw)
app.dcanvas.configure(width=500, height=640)
app.update()
app.redraw_draw()
check("холст рисунка отрисован", app._dmap is not None and app._dphoto is not None)


def ev(x, y, shift=False):
    return NS(x=x, y=y, state=1 if shift else 0)


def drag(pts, shift=False):
    app._d_press(ev(*pts[0], shift))
    for p in pts[1:]:
        app._d_move(ev(*p, shift))
    app._d_release(ev(*pts[-1], shift))
    app.update()


ox, oy, s, sh = app._dmap
cx, cy = ox + 70 * s, oy + 100 * s
app.v_tool.set("pen")
drag([(cx + 3 * i, cy + 20 * math.sin(i / 5)) for i in range(40)])
check("перо: штрих добавлен", len(app._sketch) == 1)
check("и сразу попал в раскладку",
      app.pages[app._art_page].art and "рисунок" in app.status.cget("text"),
      app.status.cget("text")[-40:])
app.v_tool.set("line")
drag([(cx, cy + 100), (cx + 100, cy + 108)], shift=True)
ln = app._sketch[-1]
check("линия с Shift — ровно горизонтальна", abs(ln[0][1] - ln[1][1]) < 1e-6)
app.v_tool.set("ellipse")
drag([(cx, cy + 150), (cx + 60, cy + 190)], shift=True)
e = app._sketch[-1]
bb = DR.bbox_of([e])
check("эллипс с Shift — круг", abs((bb[2] - bb[0]) - (bb[3] - bb[1])) < 0.05)
app.v_tool.set("rect")
drag([(cx, cy + 220), (cx + 50, cy + 260)])
check("прямоугольник — замкнутый", app._sketch[-1][0] == app._sketch[-1][-1]
      and len(app._sketch) == 4)
app.v_tool.set("eraser")
mid = app._to_px(e[0])
drag([mid])
check("ластик стёр круг", len(app._sketch) == 3 and all(q is not e for q in app._sketch))
app._ctrl_key(NS(widget=app.dcanvas, keysym="Cyrillic_ya", keycode=90))
check("Ctrl+Z вернул стёртое", len(app._sketch) == 4)

app._load_art(IMG)
f = next(x for x in app.fields if x.attr == "mode")
f.var.set(DR.MODES["hatch"])
app.rebuild()
check("картинка + штриховка в раскладке", app.cfg.draw.mode == "hatch"
      and len(app.pages[0].art) > 50, "линий %d" % len(app.pages[0].art))
check("только рисунок: текста нет", not app.pages[0].strokes)
f = next(x for x in app.fields if x.attr == "layout")
f.var.set(DR.LAYOUTS["above"])
app.rebuild()
check("сверху рисунок, под ним текст", app.pages[0].strokes and app.pages[0].art)

app.v_preset.set("Каллиграфия, острое перо")
app.apply_preset()
check("набор каллиграфии применяется из окна", app.cfg.human.calli_style == "pointed"
      and next(x for x in app.fields if x.attr == "calli_style").var.get() == "острое перо")

for fl in app.fields:
    if fl.attr == "grid":
        fl.var.set(True)
app.rebuild()
res, n = app.grid_marks_gcode()
check("режим клетки из окна и метки строк", app.cfg.page.grid
      and n == len(L.grid_baselines(app.cfg.page)) and res.strokes == 2 * n
      and not GC.validate(res.text) and "на листе строк" in app.lbl_grid_info.cget("text"),
      "меток %d, штрихов %d, %s" % (n, res.strokes, app.lbl_grid_info.cget("text")[:30]))
for fl in app.fields:
    if fl.attr == "grid":
        fl.var.set(False)
app.rebuild()

f = next(x for x in app.fields if x.attr == "text_angle")
f.echo_var.set("123,5")
f._echo_typed()
app.rebuild()
check("точный угол вписывается в поле у ползунка", abs(app.cfg.page.text_angle - 123.5) < 1e-9)
f.echo_var.set("0")
f._echo_typed()
f = next(x for x in app.fields if x.section == "page" and x.attr == "rotate")
f.echo_var.set("45")
f._echo_typed()
app.rebuild()
check("лист поворачивается на 45° и виден на столе",
      app.cfg.page.rotate == 45.0 and app._bed_photo is not None)
f.echo_var.set("0")
f._echo_typed()

app.use_calligraphy()
check("кнопка каллиграфии: пропись, острое перо, крупнее",
      app.cfg.font_path == FontSet.CALLIG and app.cfg.human.calli_style == "pointed"
      and app.cfg.page.size_mm >= 8 and app.fontset.count("д") == 2)

app._save_state()
app2 = App()
app2.withdraw()
for _ in range(60):
    app2.update()
    if app2._photo is not None:
        break
    time.sleep(0.02)
check("эскиз и картинка восстановились при следующем запуске",
      len(app2._sketch) == 4 and app2._art is not None)
check("и каллиграфический шрифт тоже", app2.fontset.count("д") == 2
      and app2.cfg.font_path == FontSet.CALLIG)
app2.destroy()

print("граница на принтере:")
import re
cfg = Config()
pg0 = L.Page(strokes=[[(20.0, 30.0), (100.0, 150.0)]])
res = GC.frame_gcode(pg0, cfg)
zs = [float(m) for m in re.findall(r"^G0 Z([-\d.]+)", res.text, re.M)]
check("перо ни разу не опускается и не пишет",
      "G1" not in res.text and zs and min(zs) >= cfg.pen.travel_z() + 2.0 - 1e-6
      and "M280" not in res.text
      and not GC.validate(res.text), "Z: %s" % sorted(set(zs)))
xy = [(float(a), float(b)) for a, b in re.findall(r"^G0 X([-\d.]+) Y([-\d.]+)", res.text, re.M)]
want = {GC.sheet_to_bed(p, cfg.page) for p in ((20, 150), (100, 150), (100, 30), (20, 30))}
check("объезжает ровно углы содержимого, два круга, с паузами",
      {(round(a, 3), round(b, 3)) for a, b in xy[:4]} ==
      {(round(a, 3), round(b, 3)) for a, b in want}
      and res.text.count("G4 P600") == 8, "точек %d" % len(xy))
cfg.page.rotate = 30.0
res = GC.frame_gcode(pg0, cfg, what="sheet")
xy = [(float(a), float(b)) for a, b in re.findall(r"^G0 X([-\d.]+) Y([-\d.]+)", res.text, re.M)]
check("при повёрнутом листе рамка повёрнута вместе с ним",
      abs(math.dist(xy[0], xy[1]) - cfg.page.sheet_w) < 0.01
      and abs(math.dist(xy[1], xy[2]) - cfg.page.sheet_h) < 0.01)

print("двигать рисунок мышью:")
app.nb.select(app.tab_draw)
app.update()
app.redraw_draw()
app._art = None
app._sketch = [[(40.0, 150.0), (60.0, 150.0)], [(40.0, 100.0), (60.0, 120.0)]]
app._sketch_undo = []
app.rebuild()
app.update()
app.v_tool.set("select")
app._tool_cursor()
P = app._to_px


def mdrag(a, b, shift=False, steps=5):
    app._d_press(ev(*a, shift))
    for i in range(1, steps + 1):
        app._d_move(ev(a[0] + (b[0] - a[0]) * i / steps,
                       a[1] + (b[1] - a[1]) * i / steps, shift))
    app._d_release(ev(*b, shift))
    app.update()


mdrag(P((50, 150)), P((50, 150)))
check("щелчок по линии выделяет её", app._sel == {0} and not app._sel_art)
a = P((50, 150))
s_ = app._dmap[2]
mdrag(a, (a[0] + 10 * s_, a[1] + 5 * s_))
st0 = app._sketch[0]
check("тянешь — линия едет вместе с мышью", abs(st0[0][0] - 50) < 0.01
      and abs(st0[0][1] - 145) < 0.01, str(st0))
app.sketch_undo()
check("Ctrl+Z возвращает на место", abs(app._sketch[0][0][0] - 40) < 1e-9)
app._sel_clear()
mdrag(P((30, 160)), P((70, 90)))
check("рамкой выделяются обе линии", app._sel == {0, 1})
(x0, y0, x1, y1), c, corners, rot = app._sel_handles()
w0 = DR.bbox_of(app._sel_outline())
mdrag(corners[1], (c and (corners[1][0] + (corners[1][0] - (x0 + x1) / 2),
                          corners[1][1] + (corners[1][1] - (y0 + y1) / 2))))
w1 = DR.bbox_of(app._sel_outline())
check("за угол — размер вдвое", abs((w1[2] - w1[0]) / (w0[2] - w0[0]) - 2.0) < 0.05,
      "%.2f" % ((w1[2] - w1[0]) / (w0[2] - w0[0])))
app.sketch_undo()
(x0, y0, x1, y1), c, corners, rot = app._sel_handles()
cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
r_ = math.hypot(rot[0] - cx, rot[1] - cy)
mdrag(rot, (cx + r_, cy), shift=True)          # с верха на правую сторону: -90°
seg = app._sketch[0]
check("за кружок — поворот (Shift — ровно по 15°)", abs(seg[0][0] - seg[1][0]) < 0.01
      and abs(abs(seg[0][1] - seg[1][1]) - 20) < 0.01, str(seg))
app.sketch_undo()
app._d_wheel(NS(delta=120))
w2 = DR.bbox_of(app._sel_outline())
check("колесо — крупнее на 10%", abs((w2[2] - w2[0]) / (w0[2] - w0[0]) - 1.1) < 0.01)
app.sketch_undo()
app._sel_nudge(NS(state=1), 1, 0)
check("стрелка с Shift — сдвиг на 5 мм", abs(app._sketch[0][0][0] - 45) < 1e-9)
app.sketch_undo()
app.v_sel_scale.set("50")
app.sel_apply("scale")
w3 = DR.bbox_of(app._sel_outline())
check("масштаб 50% кнопкой", abs((w3[2] - w3[0]) / (w0[2] - w0[0]) - 0.5) < 0.01)
app.sketch_undo()
app.sel_apply("mirror")
check("отразить ↔", abs(app._sketch[1][0][0] - 60) < 1e-9 and abs(app._sketch[1][1][0] - 40) < 1e-9)
app.sketch_undo()
app.sel_copy()
app.sel_paste()
check("Ctrl+C / Ctrl+V — копия со сдвигом", len(app._sketch) == 4 and app._sel == {2, 3}
      and abs(app._sketch[2][0][0] - 45) < 1e-9)
app.sel_delete()
check("Delete удаляет выделенное", len(app._sketch) == 2)

# картинка из файла тем же инструментом
app._load_art(IMG)
for fl in app.fields:
    if fl.attr == "layout":
        fl.var.set(DR.LAYOUTS["alone"])
    if fl.attr == "width_mm":
        fl.var.set("60")
app.rebuild()
app.update()
box0 = DR.art_box(app._art, app.cfg.draw, app.cfg.page)
cxy = ((box0[0] + box0[2]) / 2, (box0[1] + box0[3]) / 2)
app._sel_clear()
app._sketch = []
mdrag(P(cxy), P(cxy))
check("щелчок по картинке выделяет её", app._sel_art)
mdrag(P(cxy), P((cxy[0] - 10, cxy[1] + 20)))
box1 = DR.art_box(app._art, app.cfg.draw, app.cfg.page)
art_now = DR.bbox_of(app.pages[app._art_page].art)
check("картинка сдвинулась, поля размещения обновились",
      abs(box1[0] - box0[0] + 10) < 0.01 and abs(box1[3] - box0[3] - 20) < 0.01
      and abs(float(next(x for x in app.fields if x.attr == "offset_x").var.get())
              - app.cfg.draw.offset_x) < 1e-6
      and art_now[0] >= box1[0] - 0.6, str(box1))
app._d_wheel(NS(delta=-120))
box2 = DR.art_box(app._art, app.cfg.draw, app.cfg.page)
check("колесо уменьшает картинку вокруг центра",
      abs((box2[2] - box2[0]) / (box1[2] - box1[0]) - 1 / 1.1) < 0.01
      and abs((box2[0] + box2[2]) - (box1[0] + box1[2])) < 0.01)
c1 = app._sel_center()
app.v_sel_rot.set("90")
app.sel_apply("rot")
c2 = app._sel_center()
check("поворот картинки на 90° вокруг центра", abs(app.cfg.draw.rotate - 90) < 1e-6
      and math.dist(c1, c2) < 0.05, "центр %s -> %s" % (c1, c2))
app.sketch_undo()
check("отмена возвращает и картинку", abs(app.cfg.draw.rotate) < 1e-6)

app.v_dark.set(True)
app.apply_theme()
from hw.gui.app import THEMES
check("тёмная тема красит холст рисунка",
      app.dcanvas.cget("background") == THEMES["dark"]["canvas"])
app.destroy()

print("\nитог: %s" % ("всё в порядке" if not fails else "провалено: %s" % fails))
sys.exit(1 if fails else 0)
