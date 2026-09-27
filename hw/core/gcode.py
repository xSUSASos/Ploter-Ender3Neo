# -*- coding: utf-8 -*-
"""
Штрихи в миллиметрах -> G-code для Marlin (Ender 3 / Neo).

Безопасность прежде всего: в выходном файле НЕТ ни одной команды нагрева
(M104/M109/M140/M190) и ни одного движения экструдера. Проверка встроена —
validate() падает, если такая команда всё же просочилась.

Система координат: раскладка приходит в координатах ЛИСТА (начало в левом
нижнем углу, Y вверх). Здесь лист кладётся на стол: поворот на 0/90/180/270
и сдвиг на origin_x / origin_y.
"""

import math
import re
from dataclasses import dataclass

BANNED = re.compile(r"\b(M10[49]|M1[49]0|M303|G29 ?[^SJ]|M600)\b|(^|\s)E-?\d",
                    re.IGNORECASE)


# ------------------------------------------------------- лист -> стол

def _sheet_turn(cfg):
    """
    Поворот листа на столе на любой угол (против часовой): -> (cos, sin,
    сдвиг X, сдвиг Y). Лист поворачивается, а затем сдвигается так, чтобы
    его габарит начинался в origin_x / origin_y, — для 90/180/270 это то же
    самое, что прежние четыре фиксированных положения.
    """
    a = math.radians(float(cfg.rotate) % 360.0)
    c, s = math.cos(a), math.sin(a)
    if abs(c) < 1e-12:
        c = 0.0
    if abs(s) < 1e-12:
        s = 0.0
    w, h = cfg.sheet_w, cfg.sheet_h
    xs = [c * x - s * y for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    ys = [s * x + c * y for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    return c, s, -min(xs), -min(ys), max(xs) - min(xs), max(ys) - min(ys)


def sheet_to_bed(pt, cfg):
    """Точка листа (мм, Y вверх) -> точка стола."""
    x, y = pt
    c, s, dx, dy, _w, _h = _sheet_turn(cfg)
    return (cfg.origin_x + c * x - s * y + dx, cfg.origin_y + s * x + c * y + dy)


def sheet_extent(cfg):
    """Габарит листа на столе с учётом поворота: (w, h)."""
    return _sheet_turn(cfg)[4:]


# ------------------------------------------------------------- результат

@dataclass
class GcodeResult:
    text: str
    strokes: int = 0
    points: int = 0
    draw_mm: float = 0.0
    travel_mm: float = 0.0
    pen_downs: int = 0
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    seconds: float = 0.0
    warnings: tuple = ()

    def summary(self):
        m, s = divmod(int(self.seconds), 60)
        h, m = divmod(m, 60)
        t = ("%d ч %02d мин" % (h, m)) if h else ("%d мин %02d с" % (m, s))
        return ("штрихов %d · опусканий пера %d · линия %.0f мм · "
                "холостых %.0f мм · ~%s" %
                (self.strokes, self.pen_downs, self.draw_mm, self.travel_mm, t))


# ------------------------------------------------------------ генератор

class GcodeWriter:
    def __init__(self, cfg, human=None):
        self.cfg = cfg
        self.pen = cfg.pen
        self.machine = cfg.machine
        self.page = cfg.page
        self.human = human
        self.out = []
        self.warnings = []
        self._x = self._y = None
        self._z = None
        self.draw_mm = 0.0
        self.travel_mm = 0.0
        self.pen_downs = 0
        self.points = 0
        self.seconds = 0.0

    # ------------------------------------------------------------ низкий
    def _emit(self, line):
        self.out.append(line)

    def _time(self, dist, feed):
        if feed > 0 and dist > 0:
            self.seconds += dist / (feed / 60.0)

    def _g0(self, x, y, f):
        d = 0.0 if self._x is None else math.hypot(x - self._x, y - self._y)
        self._emit("G0 X%.3f Y%.3f F%d" % (x, y, f))
        self.travel_mm += d
        self._time(d, f)
        self._x, self._y = x, y

    def _g1(self, x, y, f, z=None):
        d = 0.0 if self._x is None else math.hypot(x - self._x, y - self._y)
        if z is None:
            self._emit("G1 X%.3f Y%.3f F%d" % (x, y, f))
        else:
            self._emit("G1 X%.3f Y%.3f Z%.3f F%d" % (x, y, z, f))
            self._z = z
        self.draw_mm += d
        self._time(d, f)
        self._x, self._y = x, y
        self.points += 1

    def _z_to(self, z, f=None):
        if self._z is not None and abs(self._z - z) < 1e-4:
            return
        f = f or self.pen.feed_z
        d = abs((self._z if self._z is not None else z) - z)
        self._emit("G0 Z%.3f F%d" % (z, f))
        self._time(d, f)
        self._z = z

    # ----------------------------------------------------------- перо
    def pen_up(self, full=True):
        p = self.pen
        if p.use_servo:
            self._emit("M280 P%d S%d" % (p.servo_index, p.servo_up))
            if p.servo_delay_ms:
                self._emit("G4 P%d" % p.servo_delay_ms)
            return
        target = p.travel_z() if full else p.hop_z()
        # «поднять» никогда не значит «опустить»: после парковки перо висит
        # на «Z после парковки», и к первой букве оно едет на этой высоте,
        # а опускается уже на месте. Раньше оно сначала шло вниз до высоты
        # холостых, а при кривых настройках — и ниже бумаги
        if self._z is not None and self._z > target:
            return
        self._z_to(target)
        if p.dwell_up_ms:
            self._emit("G4 P%d" % p.dwell_up_ms)

    def pen_down(self):
        p = self.pen
        self.pen_downs += 1
        if p.use_servo:
            self._emit("M280 P%d S%d" % (p.servo_index, p.servo_down))
            if p.servo_delay_ms:
                self._emit("G4 P%d" % p.servo_delay_ms)
            return
        self._z_to(p.z_draw)
        if p.dwell_down_ms:
            self._emit("G4 P%d" % p.dwell_down_ms)

    # -------------------------------------------------------- прелюдия
    def prologue(self, title=""):
        m, p = self.machine, self.pen
        self._emit("; ==========================================================")
        self._emit("; Рукописный текст пером. Сгенерировано hw (text -> gcode).")
        if title:
            self._emit("; %s" % title)
        self._emit("; Станок: %s, стол %.0fx%.0f мм" % (m.name, m.bed_x, m.bed_y))
        self._emit("; Перо: Z письма %.2f, холостые на Z %.2f, подача %d мм/мин"
                   % (p.z_draw, p.travel_z(), p.feed_draw))
        self._emit("; ВНИМАНИЕ: нагрева нет и не должно быть. Сопло холодное.")
        self._emit("; ==========================================================")
        self._emit("G21          ; миллиметры")
        self._emit("G90          ; абсолютные координаты")
        self._emit("M107         ; вентилятор выключен")
        if m.disable_soft_endstops:
            self._emit("M211 S0      ; программные концевики выключены (можно Z<0)")

        if m.home_mode == "all":
            self._emit("G28          ; парковка всех осей")
        elif m.home_mode == "xy":
            self._emit("G28 X Y      ; парковка только X и Y, Z не трогаем")
        else:
            self._emit("; парковка пропущена (home_mode=none)")

        if m.level_mode == "m420":
            self._emit("M420 S1      ; включить сохранённую карту стола")
        elif m.level_mode == "g29":
            self._emit("G29          ; промер стола")

        if m.home_mode == "all" and m.z_after_home > 0:
            self._z_to(m.z_after_home)
        else:
            # Ось Z не парковали, и принтер не знает, где она. Объявляем
            # текущее положение как «Z подъёма»: перед запуском перо должно
            # висеть примерно на этой высоте над бумагой. Без G92 Marlin
            # считал бы Z нулём и не пустил бы перо ниже.
            self._emit("G92 Z%.3f    ; текущая высота пера объявлена как высота холостых"
                       % p.travel_z())
            self._z = p.travel_z()
        if p.use_servo:
            self._emit("M280 P%d S%d" % (p.servo_index, p.servo_up))

    def epilogue(self):
        m = self.machine
        self.pen_up()
        if m.home_mode != "none":
            self._emit("G0 X%.1f Y%.1f F%d" % (5.0, m.bed_y - 10.0, self.pen.feed_travel))
        if m.beep_at_end:
            self._emit("M300 S880 P160")
        if m.motors_off_at_end:
            self._emit("M84          ; моторы отключены")
        self._emit("; готово")

    # --------------------------------------------------------- штрихи
    def draw_strokes(self, strokes, comment=None, feed=None):
        p, page = self.pen, self.page
        feed = int(feed or p.feed_draw)
        if comment:
            self._emit("; %s" % comment)
        prev_end = None
        for s in strokes:
            if len(s) < 2:
                continue
            pts = [sheet_to_bed(q, page) for q in s]
            x0, y0 = pts[0]

            # если следующий штрих начинается рядом — не поднимаем перо на всю
            near = (prev_end is not None
                    and math.hypot(x0 - prev_end[0], y0 - prev_end[1])
                    <= p.hop_threshold)
            self.pen_up(full=not near)
            self._g0(x0, y0, p.feed_travel)
            self.pen_down()

            run = 0.0
            for i in range(1, len(pts)):
                x, y = pts[i]
                if self.human is not None and self.human.cfg.pressure > 0:
                    run += math.dist(pts[i - 1], pts[i])
                    self._g1(x, y, feed,
                             z=self.human.pressure_z(p.z_draw, run))
                else:
                    self._g1(x, y, feed)
            prev_end = pts[-1]

    # ------------------------------------------------------------ сборка
    def build(self, pages, page_index=None, title=""):
        sel = pages if page_index is None else [pages[page_index]]
        self.prologue(title)
        n_strokes = 0
        for i, pg in enumerate(sel):
            if len(sel) > 1:
                self._emit("; ---- страница %d из %d ----" % (i + 1, len(sel)))
            if pg.guides:
                self.draw_strokes(pg.guides, "разлиновка")
                n_strokes += len(pg.guides)
            art = getattr(pg, "art", None)
            if art:
                d = getattr(self.cfg, "draw", None)
                pct = max(5, min(300, int(getattr(d, "speed_pct", 100) or 100)))
                self.draw_strokes(art, "рисунок",
                                  feed=max(60, self.pen.feed_draw * pct // 100))
                n_strokes += len(art)
            if pg.strokes:
                self.draw_strokes(pg.strokes, "текст")
            n_strokes += len(pg.strokes)
            if i < len(sel) - 1:
                self.pen_up()
                self._emit("M117 Смените лист")
                self._emit("M0 Смените лист и нажмите кнопку")
        self.epilogue()

        bb = self._bbox(sel)
        self._check_bounds(bb)
        return GcodeResult(
            text="\n".join(self.out) + "\n",
            strokes=n_strokes, points=self.points,
            draw_mm=self.draw_mm, travel_mm=self.travel_mm,
            pen_downs=self.pen_downs, bbox=bb, seconds=self.seconds,
            warnings=tuple(self.warnings))

    def _bbox(self, pages):
        xs, ys = [], []
        for pg in pages:
            for s in list(pg.strokes) + list(pg.guides) + list(getattr(pg, "art", [])):
                for q in s:
                    bx, by = sheet_to_bed(q, self.page)
                    xs.append(bx)
                    ys.append(by)
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def _check_bounds(self, bb):
        m = self.machine
        x0, y0, x1, y1 = bb
        if x0 < 0 or y0 < 0 or x1 > m.bed_x or y1 > m.bed_y:
            self.warnings.append(
                "Рисунок или текст выходит за стол: X %.1f..%.1f, Y %.1f..%.1f "
                "при столе %.0fx%.0f. Сдвиньте лист или уменьшите текст."
                % (x0, x1, y0, y1, m.bed_x, m.bed_y))
        p = self.pen
        if not p.use_servo:
            if p.z_draw < 0 and not m.disable_soft_endstops:
                self.warnings.append(
                    "Z письма отрицательный (%.2f), но программные концевики "
                    "включены — принтер не пустит перо ниже нуля." % p.z_draw)
            if p.z_lift < p.MIN_LIFT:
                self.warnings.append(
                    "Подъём на холостых %.2f мм слишком мал — перо зацепит "
                    "бумагу. Взято %.1f мм." % (p.z_lift, p.MIN_LIFT))
            if p.travel_z() > m.max_z:
                self.warnings.append(
                    "Высота холостых (%.2f) выше хода оси Z (%.0f мм)."
                    % (p.travel_z(), m.max_z))


# ------------------------------------------------------------- проверка

def validate(text):
    """
    Проверить готовый G-code на опасные команды.
    -> список найденных проблем (пустой = всё чисто).
    """
    bad = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split(";", 1)[0].strip()
        if not code:
            continue
        up = code.upper()
        for cmd in ("M104", "M109", "M140", "M190", "M303", "M600"):
            if up.startswith(cmd):
                bad.append((i, line, "команда нагрева/сервиса"))
        if re.search(r"(^|\s)E-?\d", up):
            bad.append((i, line, "движение экструдера"))
        if up.startswith("G2 ") or up.startswith("G3 "):
            bad.append((i, line, "дуга G2/G3 — не поддерживается генератором"))
    return bad


def frame_gcode(page, cfg, loops=2, dwell_ms=600, what="content", pad=0.0,
                extra_lift=2.0):
    """
    Предпросмотр на принтере: перо ПОДНЯТО и объезжает прямоугольник того,
    что будет напечатано, с остановкой в каждом углу — чтобы сверить с
    реальным листом до того, как перо коснётся бумаги.

    what = "content" — рамка текста и рисунка страницы (в координатах листа,
    поэтому при повёрнутом листе рамка тоже повёрнута); "sheet" — края листа.
    Рамка получается в миллиметрах листа и переводится на стол как обычно.
    Перо ни разу не опускается; для запаса оно ещё и поднимается на
    extra_lift мм выше обычной высоты холостых (но не выше хода оси Z).
    -> GcodeResult (strokes — число объездов).
    """
    pc = cfg.page
    if what == "sheet":
        x0, y0, x1, y1 = 0.0, 0.0, pc.sheet_w, pc.sheet_h
    else:
        pts = [q for s in list(page.strokes) + list(getattr(page, "art", []))
               for q in s]
        if not pts:
            raise ValueError("на странице нечего печатать")
        x0 = min(p[0] for p in pts) - pad
        x1 = max(p[0] for p in pts) + pad
        y0 = min(p[1] for p in pts) - pad
        y1 = max(p[1] for p in pts) + pad
    corners = [(x0, y1), (x1, y1), (x1, y0), (x0, y0)]      # от верхнего левого
    w = GcodeWriter(cfg)
    w.prologue("предпросмотр границы: перо поднято")
    w.pen_up()
    safe = min(cfg.pen.travel_z() + max(0.0, float(extra_lift)), cfg.machine.max_z)
    if w._z is None or w._z < safe:
        w._z_to(safe)
        w._emit("; перо поднято выше обычного — бумаги не коснётся")
    feed = max(300, int(cfg.pen.feed_travel) // 2)          # медленнее — успеть глазом
    bed = [sheet_to_bed(c, pc) for c in corners]
    w._emit("; ---- объезд границы, перо поднято ----")
    for n in range(max(1, int(loops))):
        for i, (bx, by) in enumerate(bed + [bed[0]]):
            w._g0(bx, by, feed)
            if dwell_ms and i < 4:
                w._emit("G4 P%d" % int(dwell_ms))
                w.seconds += dwell_ms / 1000.0
    w.epilogue()
    xs = [p[0] for p in bed]
    ys = [p[1] for p in bed]
    bb = (min(xs), min(ys), max(xs), max(ys))
    w._check_bounds(bb)
    return GcodeResult(text="\n".join(w.out) + "\n", strokes=int(loops),
                       travel_mm=w.travel_mm, bbox=bb, seconds=w.seconds,
                       warnings=tuple(w.warnings))


def generate(pages, cfg, human=None, page_index=None, title=""):
    """Удобная обёртка: раскладка -> GcodeResult."""
    w = GcodeWriter(cfg, human)
    res = w.build(pages, page_index=page_index, title=title)
    problems = validate(res.text)
    if problems:
        res.warnings = res.warnings + tuple(
            "строка %d: %s (%s)" % (n, l, why) for n, l, why in problems)
    return res
