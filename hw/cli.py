# -*- coding: utf-8 -*-
"""
Командная строка — для скриптов и пакетной обработки.

    python -m hw.cli text -i письмо.txt -o письмо.gcode --size 6 --preset Обычно
    python -m hw.cli template -o propis/ --variants 3
    python -m hw.cli import -s propis/propis_spec.json -p foto1.jpg -o pochevk.json
    python -m hw.cli calib -o calib.gcode
    python -m hw.cli draw -i кот.png -o кот.gcode --mode hatch --width 100
"""

import argparse
import os
import sys

from .core.config import Config, HUMAN_PRESETS, SHEETS
from .core.glyphset import FontSet, Glyph, normalize_strokes, order_strokes
from .core.humanize import Human
from .core import font_vector as FV
from .core import layout as L
from .core import preview as PV
from .core import gcode as GC
from .core import sheet as SH
from .core import vectorize as VZ
from .core import printer as PR
from .core import drawing as DR


def _out(s):
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("utf-8", "replace").decode("ascii", "replace"))


def _read_text(path):
    for enc in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue
    raise SystemExit("не удалось определить кодировку файла %s" % path)


def _save_profile(a, cfg):
    """--save-profile: записать итоговые настройки (профиль + ключи)."""
    path = getattr(a, "save_profile", None)
    if path:
        cfg.save(path)
        _out("профиль сохранён: %s" % path)


# --------------------------------------------------------------- команды

def cmd_text(a):
    cfg = Config.load(a.profile) if a.profile else Config()
    if a.preset:
        keep = cfg.human.seed
        cfg.human = HUMAN_PRESETS[a.preset]()
        cfg.human.seed = keep
    if a.sheet:
        if a.sheet not in SHEETS:
            raise SystemExit("формат %s неизвестен; есть: %s"
                             % (a.sheet, ", ".join(SHEETS)))
        cfg.page.sheet_w, cfg.page.sheet_h = SHEETS[a.sheet]
    for name in ("size_mm", "line_spacing", "align", "rotate", "origin_x",
                 "origin_y", "margin_left", "margin_right", "margin_top",
                 "margin_bottom", "paragraph_gap", "text_angle",
                 "grid_cell", "grid_first", "grid_every"):
        v = getattr(a, name, None)
        if v is not None:
            setattr(cfg.page, name, v)
    if a.autofit:
        cfg.page.autofit = True
    if a.grid:
        cfg.page.grid = True
    if a.seed is not None:
        cfg.human.seed = a.seed
    if a.no_human:
        cfg.human.enabled = False
    if a.z_draw is not None:
        cfg.pen.z_draw = a.z_draw
    if a.z_lift is not None:
        cfg.pen.z_lift = a.z_lift
    if a.z_up is not None:                  # старый ключ: абсолютная высота
        cfg.pen.z_lift = a.z_up - cfg.pen.z_draw

    text = _read_text(a.input) if a.input else (a.text or "")
    if not text.strip():
        raise SystemExit("нечего писать: задайте -i файл или --text")

    if a.font and a.font.lower() in ("callig", "каллиграфия", "пропись"):
        a.font = FontSet.CALLIG
    fs = FontSet.load(a.font) if a.font else FontSet()
    fs.flat = cfg.curve_flatness
    if a.font:
        cfg.font_path = a.font
    _save_profile(a, cfg)
    hu = Human(cfg.human)
    report = {}
    pages = L.build_pages(text, fs, cfg.page, hu, report=report)
    if report.get("autofit_ok") is False:
        _out("ВНИМАНИЕ: автоподбор не уместил текст на страницу даже при "
             "минимальном размере %.1f мм — вышло %d страниц."
             % (report.get("autofit_min", 0.0), report.get("pages", 0)))
    st = L.stats(pages)
    _out("страниц %d · строк %d · штрихов %d · длина линии %.0f мм"
         % (st["pages"], st["lines"], st["strokes"], st["ink_mm"]))

    idx = None if a.all_pages else (a.page - 1)
    res = GC.generate(pages, cfg, hu, page_index=idx,
                      title=os.path.basename(a.output))
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(res.text)
    _out(res.summary())
    _out("сохранено: %s" % a.output)
    for w in res.warnings:
        _out("ВНИМАНИЕ: " + w)
    if a.svg:
        PV.save_svg(a.svg, pages, cfg.page)
        _out("превью: %s" % a.svg)
    if a.png:
        PV.save_png(a.png, pages[0], cfg.page, px_per_mm=12.0)
        _out("превью: %s" % a.png)
    return 0


def cmd_draw(a):
    cfg = Config.load(a.profile) if a.profile else Config()
    if a.sheet:
        if a.sheet not in SHEETS:
            raise SystemExit("формат %s неизвестен; есть: %s"
                             % (a.sheet, ", ".join(SHEETS)))
        cfg.page.sheet_w, cfg.page.sheet_h = SHEETS[a.sheet]
    d = cfg.draw
    for name in ("mode", "threshold", "width_mm", "height_mm", "anchor",
                 "rotate", "hatch_step", "hatch_angle", "hatch_levels",
                 "passes", "speed_pct", "tremor", "detail", "blur"):
        v = getattr(a, name, None)
        if v is not None:
            setattr(d, name, v)
    if a.invert:
        d.invert = True
    if a.mirror:
        d.mirror = True
    _save_profile(a, cfg)
    src = DR.ArtSource(a.input)
    _out(src.describe())
    art, _box = DR.build_art(src, [], d, cfg.page)
    if not art:
        raise SystemExit("из картинки не получилось ни одной линии: "
                         "поменяйте --threshold или --mode")
    pages = [L.Page(size_mm=cfg.page.size_mm, art=art)]
    res = GC.generate(pages, cfg, None, title=os.path.basename(a.output))
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(res.text)
    _out(res.summary())
    _out("сохранено: %s" % a.output)
    for w in res.warnings:
        _out("ВНИМАНИЕ: " + w)
    if a.png:
        PV.save_png(a.png, pages[0], cfg.page, px_per_mm=12.0, art_width=d.pen_width)
        _out("превью: %s" % a.png)
    if a.svg:
        PV.save_svg(a.svg, pages, cfg.page, art_width=d.pen_width)
        _out("превью: %s" % a.svg)
    return 0


def cmd_template(a):
    chars = FV.alphabet(include_latin=a.latin, include_cyrillic=not a.no_cyrillic,
                        include_digits=not a.no_digits, include_punct=not a.no_punct)
    spec = SH.TemplateSpec()
    spec.title = a.title
    spec, files = SH.save_template(chars, a.output, spec=spec, dpi=a.dpi,
                                   variants=a.variants)
    _out("символов %d · вариантов %d · листов %d" %
         (len(chars), a.variants, len(spec.pages)))
    for f in files:
        _out("  " + f)
    _out("Печатайте без масштабирования (100%), пишите тёмной ручкой.")
    return 0


def cmd_import(a):
    from PIL import Image
    spec = SH.TemplateSpec.load(a.spec)
    fs = FontSet.load(a.font) if (a.font and os.path.exists(a.font)) else FontSet()
    total = 0
    for pi, path in enumerate(a.photo):
        img = Image.open(path)
        rect, info = SH.detect_sheet(img, spec, work_dpi=a.dpi)
        page_idx = a.page - 1 if a.page else min(pi, len(spec.pages) - 1)
        cells = SH.slice_cells(rect, spec, page_idx, info["px_per_mm"])
        got = 0
        for c in cells:
            strokes, _ = VZ.vectorize_image(
                c["image"], ink_level=a.ink, template=c.get("template"),
                remove_lines=c["guides"] if a.remove_lines else None)
            if not strokes:
                continue
            try:
                adv, ns = normalize_strokes(strokes, c["baseline"], c["cap"])
            except ValueError:
                continue
            fs.add(Glyph(c["char"], adv, order_strokes(ns), source="user"))
            got += 1
        total += got
        _out("%s: лист %d, доворотов %d, начертаний %d"
             % (os.path.basename(path), page_idx + 1, info["rotations"], got))
    fs.save(a.output)
    done, tot, missing = fs.coverage(FV.alphabet())
    _out("всего добавлено %d · оцифровано %d из %d букв" % (total, done, tot))
    if missing:
        _out("нет ещё: " + "".join(missing[:40]))
    _out("сохранено: %s" % a.output)
    return 0


def cmd_calib(a):
    cfg = Config.load(a.profile) if a.profile else Config()
    g = PR.calibration_gcode(cfg, z_from=a.z_from, z_to=a.z_to, steps=a.steps)
    with open(a.output, "w", encoding="utf-8") as f:
        f.write(g)
    _out("лесенка Z: %.2f -> %.2f, шагов %d" % (a.z_from, a.z_to, a.steps))
    _out("сохранено: %s" % a.output)
    return 0


def cmd_send(a):
    link = PR.PrinterLink(a.port, a.baud, on_log=_out,
                          on_progress=lambda i, n, l: None)
    import time
    done = {}
    link.on_done = lambda ok, m: done.update(ok=ok, msg=m)
    link.start(_read_text(a.input))
    while link.is_running:
        time.sleep(0.3)
    _out(done.get("msg", "?"))
    return 0 if done.get("ok") else 1


def cmd_selftest(_a):
    """
    Проверка собранного exe изнутри: поднимаются ли numpy/scipy/opencv,
    строится ли окно, работает ли раскладка и генератор G-code.
    Нужна потому, что в сборке ломаются именно импорты, а не логика.
    """
    import importlib
    bad = []
    for m in ("numpy", "scipy.ndimage", "cv2", "PIL.Image", "PIL.ImageTk",
              "serial.tools.list_ports", "tkinter"):
        try:
            importlib.import_module(m)
            _out("  OK   %s" % m)
        except Exception as e:                          # noqa: BLE001
            _out("  СБОЙ %s -> %s" % (m, e))
            bad.append(m)

    cfg = Config()
    hu = Human(cfg.human)
    pages = L.build_pages("Проверка сборки. Тест 123.", FontSet(), cfg.page, hu)
    res = GC.generate(pages, cfg, hu, page_index=0)
    ok = bool(res.text) and not GC.validate(res.text)
    _out("  %s раскладка и G-code (%d штрихов)"
         % ("OK  " if ok else "СБОЙ", res.strokes))
    if not ok:
        bad.append("gcode")

    # шаблон прописи и векторизация — самые тяжёлые по зависимостям части
    import tempfile, shutil
    from PIL import Image, ImageDraw
    tmp = tempfile.mkdtemp(prefix="hwself_")
    try:
        spec, _files = SH.save_template(list("АБВ"), tmp, variants=1,
                                        dpi=150, pdf=False)
        _out("  OK   пропись (%d лист)" % len(spec.pages))
        im = Image.new("RGB", (160, 160), "white")
        d = ImageDraw.Draw(im)
        d.ellipse([30, 30, 120, 120], outline="black", width=7)
        st, _dbg = VZ.vectorize_image(im)
        _out("  %s векторизация фото (%d штрихов)"
             % ("OK  " if st else "СБОЙ", len(st)))
        if not st:
            bad.append("vectorize")
    except Exception as e:                              # noqa: BLE001
        _out("  СБОЙ пропись/векторизация -> %s" % e)
        bad.append("sheet")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        import tkinter as tk
        from hw.gui.app import App
        import time as _t
        app = App()
        app.withdraw()
        # первая раскладка запускается через after(120), поэтому ждём её,
        # а не крутим update() вхолостую
        deadline = _t.time() + 15.0
        while _t.time() < deadline:
            app.update()
            if app._photo is not None:
                break
            _t.sleep(0.02)
        need = {("page", "size_mm"), ("human", "tremor"), ("pen", "z_draw")}
        have = {(f.section, f.attr) for f in app.fields}
        good = need <= have and app._photo is not None
        _out("  %s окно программы (%d настроек, превью %s)"
             % ("OK  " if good else "СБОЙ", len(app.fields),
                "есть" if app._photo is not None else "нет"))
        if not good:
            bad.append("gui")
        app.destroy()
    except Exception as e:                              # noqa: BLE001
        _out("  СБОЙ окно программы -> %s" % e)
        bad.append("gui")

    _out("итог: " + ("всё работает" if not bad else "проблемы: " + ", ".join(bad)))
    return 1 if bad else 0


def cmd_ports(_a):
    ports = PR.available_ports()
    if not ports:
        _out("портов не найдено")
    for _p, d in ports:
        _out("  " + d)
    return 0


# ------------------------------------------------------------------ разбор

def build_parser():
    p = argparse.ArgumentParser(
        prog="hw", description="Текст -> рукописный G-code для Ender 3 / Neo")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("text", help="текст -> gcode")
    t.add_argument("-i", "--input", help="файл с текстом")
    t.add_argument("--text", help="текст прямо в командной строке")
    t.add_argument("-o", "--output", required=True, help="куда писать .gcode")
    t.add_argument("--font", help="JSON своего почерка или callig — "
                                   "встроенная каллиграфическая пропись")
    t.add_argument("--profile", help="JSON профиля настроек")
    t.add_argument("--save-profile", dest="save_profile",
                   help="сохранить итоговые настройки в JSON профиля")
    t.add_argument("--preset", choices=list(HUMAN_PRESETS), help="набор реализма")
    t.add_argument("--sheet", help="формат листа: " + ", ".join(SHEETS))
    t.add_argument("--size-mm", dest="size_mm", type=float,
                   help="высота заглавной, мм")
    t.add_argument("--line-spacing", dest="line_spacing", type=float)
    t.add_argument("--align", choices=["left", "center", "right", "justify"])
    t.add_argument("--paragraph-gap", dest="paragraph_gap", type=float,
                   help="отбивка между абзацами, мм")
    t.add_argument("--rotate", type=float,
                   help="поворот листа на столе, любой угол, градусы")
    t.add_argument("--text-angle", dest="text_angle", type=float,
                   help="поворот текста на листе, любой угол, градусы")
    t.add_argument("--origin-x", dest="origin_x", type=float)
    t.add_argument("--origin-y", dest="origin_y", type=float)
    t.add_argument("--margin-left", dest="margin_left", type=float)
    t.add_argument("--margin-right", dest="margin_right", type=float)
    t.add_argument("--margin-top", dest="margin_top", type=float)
    t.add_argument("--margin-bottom", dest="margin_bottom", type=float)
    t.add_argument("--autofit", action="store_true", help="подобрать размер")
    t.add_argument("--grid", action="store_true",
                   help="тетрадь в клетку: строки точно на линиях клетки")
    t.add_argument("--grid-cell", dest="grid_cell", type=float, help="клетка, мм")
    t.add_argument("--grid-first", dest="grid_first", type=float,
                   help="от верха листа до линии первой строки, мм")
    t.add_argument("--grid-every", dest="grid_every", type=int,
                   help="строка через столько клеток")
    t.add_argument("--seed", type=int)
    t.add_argument("--no-human", action="store_true", help="без искажений")
    t.add_argument("--z-draw", dest="z_draw", type=float)
    t.add_argument("--z-lift", dest="z_lift", type=float,
                   help="подъём пера над бумагой на холостых, мм")
    t.add_argument("--z-up", dest="z_up", type=float,
                   help="устарело: абсолютная высота холостых")
    t.add_argument("--page", type=int, default=1, help="какую страницу печатать")
    t.add_argument("--all-pages", action="store_true", help="все страницы подряд")
    t.add_argument("--svg", help="сохранить превью SVG")
    t.add_argument("--png", help="сохранить превью PNG")
    t.set_defaults(func=cmd_text)

    dr = sub.add_parser("draw", help="картинка или SVG -> gcode рисунка")
    dr.add_argument("-i", "--input", required=True, help="PNG/JPG/... или SVG")
    dr.add_argument("-o", "--output", required=True, help="куда писать .gcode")
    dr.add_argument("--profile", help="JSON профиля настроек")
    dr.add_argument("--save-profile", dest="save_profile",
                    help="сохранить итоговые настройки в JSON профиля")
    dr.add_argument("--sheet", help="формат листа: " + ", ".join(SHEETS))
    dr.add_argument("--mode", choices=list(DR.MODES), help="как превращать в линии")
    dr.add_argument("--threshold", type=float, help="порог тёмного 0..1")
    dr.add_argument("--invert", action="store_true")
    dr.add_argument("--width", dest="width_mm", type=float, help="ширина, мм")
    dr.add_argument("--height", dest="height_mm", type=float, help="высота, мм")
    dr.add_argument("--anchor", choices=list(DR.ANCHORS))
    dr.add_argument("--rotate", type=float, help="поворот, градусы")
    dr.add_argument("--mirror", action="store_true")
    dr.add_argument("--hatch-step", dest="hatch_step", type=float)
    dr.add_argument("--hatch-angle", dest="hatch_angle", type=float)
    dr.add_argument("--hatch-levels", dest="hatch_levels", type=int)
    dr.add_argument("--detail", type=int)
    dr.add_argument("--blur", type=float)
    dr.add_argument("--passes", type=int, help="обводить N раз")
    dr.add_argument("--speed", dest="speed_pct", type=int, help="скорость, %% подачи")
    dr.add_argument("--tremor", type=float, help="дрожание руки, мм")
    dr.add_argument("--svg", help="сохранить превью SVG")
    dr.add_argument("--png", help="сохранить превью PNG")
    dr.set_defaults(func=cmd_draw)

    m = sub.add_parser("template", help="напечатать пропись для сбора почерка")
    m.add_argument("-o", "--output", required=True, help="папка для файлов")
    m.add_argument("--variants", type=int, default=3, help="вариантов на букву")
    m.add_argument("--dpi", type=int, default=200)
    m.add_argument("--title", default="ОБРАЗЕЦ ПОЧЕРКА")
    m.add_argument("--latin", action="store_true", help="добавить латиницу")
    m.add_argument("--no-cyrillic", action="store_true")
    m.add_argument("--no-digits", action="store_true")
    m.add_argument("--no-punct", action="store_true")
    m.set_defaults(func=cmd_template)

    im = sub.add_parser("import", help="фото прописи -> шрифт")
    im.add_argument("-s", "--spec", required=True, help="propis_spec.json")
    im.add_argument("-p", "--photo", nargs="+", required=True, help="фотографии")
    im.add_argument("-o", "--output", required=True, help="куда писать шрифт .json")
    im.add_argument("--font", help="дополнить существующий шрифт")
    im.add_argument("--ink", type=float, default=0.68, help="порог чернил 0.35..0.92")
    im.add_argument("--remove-lines", dest="remove_lines", action="store_true",
                    help="вычитать разлиновку: нужно, только если она "
                         "пропечаталась слишком тёмной или вы писали "
                         "на обычной тетрадной бумаге")
    im.add_argument("--dpi", type=int, default=200)
    im.add_argument("--page", type=int, help="номер листа прописи")
    im.set_defaults(func=cmd_import)

    c = sub.add_parser("calib", help="лесенка Z для подбора высоты пера")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("--profile")
    c.add_argument("--z-from", dest="z_from", type=float, default=3.0)
    c.add_argument("--z-to", dest="z_to", type=float, default=-0.4)
    c.add_argument("--steps", type=int, default=8)
    c.set_defaults(func=cmd_calib)

    s = sub.add_parser("send", help="отправить готовый gcode на принтер")
    s.add_argument("-i", "--input", required=True)
    s.add_argument("--port", required=True)
    s.add_argument("--baud", type=int, default=115200)
    s.set_defaults(func=cmd_send)

    sub.add_parser("ports", help="список USB-портов").set_defaults(func=cmd_ports)
    sub.add_parser("selftest",
                   help="самопроверка сборки: импорты, окно, G-code"
                   ).set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = build_parser().parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
