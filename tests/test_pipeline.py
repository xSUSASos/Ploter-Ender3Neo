# -*- coding: utf-8 -*-
"""Приёмочный прогон: все части конвейера от текста до G-code."""
import sys, io, os, subprocess, tempfile, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  — " + extra) if extra else ""))
    if not cond:
        fails.append(name)


def run(*args):
    r = subprocess.run([sys.executable, "hw.py"] + list(args),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")

tmp = tempfile.mkdtemp(prefix="hwtest_")
try:
    from hw.core.config import Config, HUMAN_PRESETS
    from hw.core.glyphset import FontSet, Glyph
    from hw.core.humanize import Human
    from hw.core import font_vector as FV, layout as L, gcode as GC

    print("шрифт:")
    check("глифов в наборе", len(FV.GLYPHS) >= 160, "%d" % len(FV.GLYPHS))
    odd = "„×·±≈≠≤≥€₽£‰§′″←→½²³éüñç«»—№ωφ√∞αβγδελμπστΔΣΩ−₁₂∫∂"
    check("частые знаки есть в шрифте", all(FV.has_glyph(c) for c in odd),
          "нет: " + ("".join(c for c in odd if not FV.has_glyph(c)) or "—"))
    t = L.clean_text("е" + chr(0x308) + "ж, и" + chr(0x306) + "од, ру" + chr(0x301)
                     + "ка, пере" + chr(0xAD) + "нос" + chr(0x200B))
    check("ё/й из двух кодов, ударения и мягкий перенос", t == "ёж, йод, рука, перенос",
          repr(t))
    _fs = FontSet()
    _fs.builtin_fallback = False
    try:
        _pg = L.build_pages("Привет", _fs, Config().page, Human(Config().human))
        ok = True
    except Exception:                             # noqa: BLE001
        ok, _pg = False, []
    check("без запасного шрифта раскладка не падает", ok)
    check("без запасного шрифта недостающие буквы пропущены",
          ok and not any(pg.strokes for pg in _pg))
    bad = [c for c in FV.alphabet() if not FV.has_glyph(c)]
    check("весь алфавит покрыт", not bad, str(bad[:8]))
    check("кратка над Й — чашей вниз",
          min(p[1] for s in FV.get_glyph("Й")[1] for p in s) >= 0
          and max(p[1] for s in FV.get_glyph("Й")[1] for p in s) <= 18.1)

    print("\nG-code:")
    cfg = Config()
    cfg.human = HUMAN_PRESETS["Второпях"]()
    cfg.human.seed = 3
    cfg.human.strike_rate = 0.15
    cfg.human.typo_rate = 0.15
    cfg.human.pressure = 0.15
    hu = Human(cfg.human)
    txt = "Проверка. Съешь ещё этих мягких булок, да выпей чаю. ABC xyz 0123!"
    pages = L.build_pages(txt, FontSet(), cfg.page, hu)
    res = GC.generate(pages, cfg, hu, page_index=0)
    check("нет опасных команд", not GC.validate(res.text))
    # проверяем ТОЛЬКО команды: в комментариях "Ender 3 Neo" есть " E",
    # и это, разумеется, не движение экструдера
    code_only = "\n".join(l.split(";", 1)[0].strip()
                          for l in res.text.splitlines()).upper()
    for c in ("M104", "M109", "M140", "M190", "M303"):
        check("нет %s" % c, c not in code_only)
    import re as _re
    check("нет движений экструдера", not _re.search(r"(^|\s)E-?\d", code_only))
    check("комментарии с текстом сохранены", "Ender 3 Neo" in res.text)
    check("есть парковка XY", "G28 X Y" in res.text)
    check("объявлен Z при непаркованной оси", "G92 Z" in res.text)
    check("нажим модулирует Z", res.text.count(" Z") > res.pen_downs)
    check("нет предупреждений на A5", not res.warnings, str(res.warnings))
    x0, y0, x1, y1 = res.bbox
    check("всё в пределах стола",
          0 <= x0 and 0 <= y0 and x1 <= cfg.machine.bed_x and y1 <= cfg.machine.bed_y,
          "X %.1f..%.1f  Y %.1f..%.1f" % (x0, x1, y0, y1))

    print("\nвысота пера на холостых:")
    # профиль пользователя: старый абсолютный «Z подъёма» = 0 при «Z письма» = 1.
    # Раньше перо после парковки опускалось на Z 0 и волоклось по листу
    uc = Config.from_dict({"pen": {"z_draw": 1, "z_up": 0, "z_travel_min": 1},
                           "machine": {"home_mode": "all", "z_after_home": 10,
                                       "disable_soft_endstops": True}})
    check("старый «Z подъёма» ниже письма переведён в безопасный подъём",
          uc.pen.travel_z() > uc.pen.z_draw + 0.4, "Z %.1f" % uc.pen.travel_z())
    ur = GC.generate(L.build_pages("Тест пера.", FontSet(), uc.page, Human(uc.human)),
                     uc, Human(uc.human))
    zc, low, moves = None, 0, []
    for ln in ur.text.splitlines():
        code = ln.split(";", 1)[0].strip()
        mz = _re.search(r"Z(-?[\d.]+)", code)
        if mz and code.startswith(("G0", "G1")):
            zc = float(mz.group(1))
        if code.startswith("G0") and "X" in code:
            moves.append(zc)
            if zc is None or zc <= uc.pen.z_draw + 0.1:
                low += 1
    check("ни одного холостого переезда на высоте письма", low == 0, "%d" % low)
    check("к первой букве перо едет на «Z после парковки»", moves[:1] == [10.0],
          str(moves[:1]))
    uc.pen.z_lift = 0.0
    ur = GC.generate(L.build_pages("Тест.", FontSet(), uc.page, Human(uc.human)),
                     uc, Human(uc.human))
    check("нулевой подъём не пропускается", uc.pen.travel_z() >= uc.pen.z_draw + 0.5
          and any("слишком мал" in w for w in ur.warnings))

    print("\nповорот листа:")
    for rot in (0, 90, 180, 270):
        cfg.page.rotate = rot
        r2 = GC.generate(pages, cfg, hu, page_index=0)
        w, h = GC.sheet_extent(cfg.page)
        bx0, by0, bx1, by1 = r2.bbox
        check("поворот %3d° — в габарите листа" % rot,
              bx0 >= cfg.page.origin_x - 0.01 and by0 >= cfg.page.origin_y - 0.01
              and bx1 <= cfg.page.origin_x + w + 0.01
              and by1 <= cfg.page.origin_y + h + 0.01,
              "лист %.0fx%.0f" % (w, h))
    cfg.page.rotate = 0

    print("\nсохранение и загрузка:")
    fs = FontSet("тест")
    fs.add(Glyph("Ы", 12.0, [[(0.0, 0.0), (0.0, 14.0)], [(6.0, 0.0), (6.0, 14.0)]],
                 source="user"))
    fs.add(Glyph("Ы", 12.5, [[(1.0, 0.0), (1.0, 13.0)]], source="user"))
    fp = os.path.join(tmp, "f.json")
    fs.save(fp)
    fs2 = FontSet.load(fp)
    check("шрифт пережил запись и чтение",
          fs2.count("Ы") == 2 and abs(fs2.get_variants("Ы")[1].advance - 12.5) < 1e-6)
    check("встроенный подставляется вместо отсутствующего",
          fs2.get_variants("А")[0].source == "builtin")

    cp = os.path.join(tmp, "p.json")
    cfg.save(cp)
    c2 = Config.load(cp)
    check("профиль пережил запись и чтение",
          abs(c2.human.tremor - cfg.human.tremor) < 1e-9
          and c2.page.sheet_w == cfg.page.sheet_w)

    print("\nкомандная строка:")
    code, out = run("text", "--text", "Привет, мир! Проверка 123.",
                    "-o", os.path.join(tmp, "a.gcode"),
                    "--sheet", "A5", "--size-mm", "6", "--preset", "Аккуратно",
                    "--svg", os.path.join(tmp, "a.svg"),
                    "--png", os.path.join(tmp, "a.png"))
    check("hw.py text", code == 0 and os.path.getsize(os.path.join(tmp, "a.gcode")) > 500,
          out.strip().splitlines()[-1] if out.strip() else "")
    check("превью SVG создано", os.path.getsize(os.path.join(tmp, "a.svg")) > 500)
    check("превью PNG создано", os.path.getsize(os.path.join(tmp, "a.png")) > 500)

    code, out = run("text", "--text", "x " * 400, "-o", os.path.join(tmp, "b.gcode"),
                    "--autofit")
    check("автоподбор сообщает о неудаче", "ВНИМАНИЕ" in out or code == 0,
          [l for l in out.splitlines() if "ВНИМАНИ" in l][:1])

    code, out = run("template", "-o", os.path.join(tmp, "tpl"), "--variants", "2")
    check("hw.py template", code == 0
          and os.path.exists(os.path.join(tmp, "tpl", "propis_spec.json")))

    code, out = run("calib", "-o", os.path.join(tmp, "c.gcode"))
    g = open(os.path.join(tmp, "c.gcode"), encoding="utf-8").read()
    check("hw.py calib", code == 0 and not GC.validate(g) and "M117 Z=" in g)

    code, out = run("ports")
    check("hw.py ports", code == 0)

    print("\nитог: %s" % ("всё в порядке" if not fails else "провалено: %s" % fails))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(1 if fails else 0)
