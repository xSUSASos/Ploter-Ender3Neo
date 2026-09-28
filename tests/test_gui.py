# -*- coding: utf-8 -*-
"""Проверка окна без съёмки экрана: дерево виджетов + прогон обработчиков."""
import sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# уводим файл состояния в сторону: тест не должен трогать настоящие настройки
import tempfile as _tf
os.environ["HW_STATE"] = os.path.join(_tf.mkdtemp(prefix="hwstate_"), "state.json")

from hw.gui.app import App

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fails.append(name)


def count(w):
    n = 1
    for c in w.winfo_children():
        n += count(c)
    return n


app = App()
app.withdraw()                      # не показываем окно, тесты этого не требуют
for _ in range(30):
    app.update()
    time.sleep(0.02)

print("вкладки:")
for i, (tab, nm) in enumerate([(app.tab_text, "Текст"), (app.tab_hand, "Почерк"),
                               (app.tab_page, "Лист"), (app.tab_human, "Реализм"),
                               (app.tab_print, "Печать")]):
    check("вкладка %-8s собрана" % nm, count(tab) > 8, "виджетов: %d" % count(tab))

print("состояние:")
KEY_FIELDS = {("page", "size_mm"), ("page", "align"), ("page", "paragraph_gap"),
              ("human", "tremor"), ("human", "strike_rate"), ("pen", "z_draw"),
              ("machine", "home_mode")}
have = {(f.section, f.attr) for f in app.fields}
check("ключевые настройки привязаны", KEY_FIELDS <= have,
      "всего %d, нет: %s" % (len(app.fields), sorted(KEY_FIELDS - have) or "—"))
check("текст разложен на страницы", len(app.pages) >= 1)
check("превью отрисовано", app._photo is not None)
check("в статусе есть оценка времени", "~" in app.status.cget("text"))

print("обработчики:")
app.v_preset.set("Небрежно")
app.apply_preset()
check("пресет применился", abs(app.cfg.human.tremor - 0.035) < 1e-9,
      "tremor=%.3f" % app.cfg.human.tremor)

app.v_sheet.set("A4")
app.on_sheet_preset()
check("формат листа сменился", (app.cfg.page.sheet_w, app.cfg.page.sheet_h) == (210.0, 297.0))
check("предупреждение о столе показано", "не помещается" in app.lbl_fit.cget("text"),
      repr(app.lbl_fit.cget("text")[:60]))

app.v_sheet.set("A5")
app.on_sheet_preset()
check("A5 помещается", app.lbl_fit.cget("text").strip() == "")

# правка поля через виджет
f = next(x for x in app.fields if x.section == "page" and x.attr == "size_mm")
f.var.set("9")
app.rebuild()
check("размер шрифта применился", abs(app.cfg.page.size_mm - 9.0) < 1e-9)

before = len(app.pages)
app.txt.delete("1.0", "end")
app.txt.insert("1.0", ("Длинный текст. " * 200))
app.rebuild()
check("длинный текст разлился на страницы", len(app.pages) > before,
      "было %d, стало %d" % (before, len(app.pages)))

app.turn(1)
check("листание работает", app.page_index == 1)

app.cfg.page.autofit = True
next(x for x in app.fields if x.attr == "autofit").pull()
app.rebuild()
check("автоподбор честно сообщил, что не влезает",
      app._report.get("autofit_ok") is False
      and "не помещается" in app.lbl_fit.cget("text"),
      "размер %.2f мм, страниц %d" % (app.pages[0].size_mm, len(app.pages)))

app.txt.delete("1.0", "end")
app.txt.insert("1.0", "Короткий текст, который заведомо влезает.")
app.rebuild()
check("автоподбор уместил короткий текст", len(app.pages) == 1
      and app._report.get("autofit_ok") is True,
      "подобран размер %.2f мм" % app.pages[0].size_mm)

app.v_travel.set(True)
app.redraw()
check("режим холостых ходов рисуется", app._photo is not None)

print("правка в русской раскладке:")
from types import SimpleNamespace as NS
t = app.txt
t.delete("1.0", "end"); t.insert("1.0", "абв")
app.clipboard_clear(); app.clipboard_append("++")
t.mark_set("insert", "end-1c")
app._ctrl_key(NS(widget=t, keysym="Cyrillic_em", keycode=86)); app.update()
check("Ctrl+V (кириллица)", t.get("1.0", "end-1c") == "абв++", repr(t.get("1.0", "end-1c")))
app._ctrl_key(NS(widget=t, keysym="Cyrillic_ef", keycode=65)); app.update()
check("Ctrl+A выделяет всё", t.get("sel.first", "sel.last") == "абв++")
app._ctrl_key(NS(widget=t, keysym="Cyrillic_che", keycode=88)); app.update()
check("Ctrl+X (кириллица)", t.get("1.0", "end-1c") == "" and app.clipboard_get() == "абв++")
r = app._ctrl_key(NS(widget=t, keysym="v", keycode=86))
check("латиница не задваивает вставку", r is None and t.get("1.0", "end-1c") == "")

print("тема:")
app.v_dark.set(True); app.apply_theme(); app.update()
check("тёмная тема включилась", app.tk.call("ttk::style", "theme", "use") == "clam")
from hw.gui.app import THEMES
check("поле текста потемнело", app.txt.cget("background") == THEMES["dark"]["field"])
app.v_dark.set(False); app.apply_theme(); app.update()
check("светлая тема вернула светлые цвета",
      app.txt.cget("background") == THEMES["light"]["field"]
      and app.side.cget("background") == THEMES["light"]["side"])
check("у каждой настройки есть пояснение у значка «i»",
      all(f.info.text for f in app.fields) and len(app._infos) >= len(app.fields))
app.nb.select(app.tab_page); app.update()
check("боковое меню и шапка следуют за разделом",
      app.h_title.cget("text") == "Лист")

print("управление своим шрифтом:")
from hw.core.glyphset import Glyph
bar = [[(0.0, 0.0), (0.0, 14.0)]]
for i in range(3):
    app.fontset.add(Glyph("Ы", 12.0 + i, [list(s2) for s2 in bar], source="user"))
app.fontset.add(Glyph("Ф", 12.0, [list(s2) for s2 in bar], source="user"))
app._update_coverage()
app.v_view.set("font")
app._render_glyph_grid()
app.update()
check("свой шрифт показывается сеткой", len(app._thumbs) == 4,
      "плиток %d" % len(app._thumbs))
check("в подписи видно число начертаний", "4" in app.lbl_grid.cget("text"))

app._font_marks = {("Ы", 1)}
import tkinter.messagebox as mb
mb.askyesno = lambda *a, **k: True
app.delete_marked()
check("одно начертание удалено, остальные целы",
      app.fontset.count("Ы") == 2 and app.fontset.count("Ф") == 1,
      "Ы=%d Ф=%d" % (app.fontset.count("Ы"), app.fontset.count("Ф")))
check("удалили именно отмеченное",
      [round(g.advance, 1) for g in app.fontset.get_variants("Ы")] == [12.0, 14.0])
check("отметки сброшены", not app._font_marks)

app.v_view.set("new")
app._render_glyph_grid()
check("переключение на распознанное работает",
      "распознано" in app.lbl_grid.cget("text"))

print("память между запусками:")
import tempfile, os as _os
fp = _os.path.join(tempfile.gettempdir(), "hw_test_font.json")
app.fontset.save(fp)
app.cfg.font_path = fp
app._save_state()
check("файл состояния записан", _os.path.exists(app._state_file()))
app2 = App()
app2.withdraw()
for _ in range(60):
    app2.update()
    if app2._photo is not None:
        break
    time.sleep(0.02)
check("почерк подхватился при следующем запуске",
      app2.fontset.count("Ы") == 2 and app2.cfg.font_path == fp,
      "начертаний Ы: %d" % app2.fontset.count("Ы"))
app2.destroy()

# настройки из окна, текст и переключатели — без всякого профиля
app._profile_path = ""
app.cfg.page.size_mm = 8.25
app.cfg.pen.z_draw = -0.35
for f in app.fields:
    f.pull()
app.txt.delete("1.0", "end")
app.txt.insert("1.0", "Текст с прошлого раза")
app.v_travel.set(True)
app.v_preset.set("Небрежно")
app.nb.select(app.tab_page)
app._save_state()
check("нет недописанного временного файла", not _os.path.exists(app._state_file() + ".tmp"))
app3 = App()
app3.withdraw()
for _ in range(60):
    app3.update()
    if app3._photo is not None:
        break
    time.sleep(0.02)
check("настройки окна восстановились без профиля",
      app3.cfg.page.size_mm == 8.25 and app3.cfg.pen.z_draw == -0.35,
      "size %.2f, z %.2f" % (app3.cfg.page.size_mm, app3.cfg.pen.z_draw))
check("поля ввода показывают восстановленное",
      any(f.attr == "size_mm" and abs(float(f.var.get()) - 8.25) < 1e-6
          for f in app3.fields))
check("текст восстановился", app3.txt.get("1.0", "end-1c") == "Текст с прошлого раза")
check("переключатели и вкладка восстановились",
      app3.v_travel.get() and app3.v_preset.get() == "Небрежно"
      and app3.nb.select() == str(app3.tab_page))

# автосохранение: правка без закрытия окна попадает в файл сама
app3.cfg.page.size_mm = 9.5
for f in app3.fields:
    f.pull()
app3.rebuild()
for _ in range(150):
    app3.update()
    if getattr(app3, "_autosave_id", 1) is None:
        break
    time.sleep(0.02)
import json as _json
saved = _json.load(open(app3._state_file(), encoding="utf-8"))
check("правка сохраняется сама, без закрытия окна",
      saved["config"]["page"]["size_mm"] == 9.5)

# сброс настроек: всё по умолчанию, почерк остаётся
import tkinter.messagebox as _mb
_ask = _mb.askyesno
_mb.askyesno = lambda *a, **k: True
try:
    app3.reset_settings()
finally:
    _mb.askyesno = _ask
from hw.core.config import Config as _Cfg
check("сброс возвращает настройки по умолчанию, почерк на месте",
      app3.cfg.page.size_mm == _Cfg().page.size_mm and app3.cfg.font_path == fp)
app3.destroy()
try:
    _os.remove(app._state_file()); _os.remove(fp)
except OSError:
    pass

print("\nитог: %s" % ("всё в порядке" if not fails else "провалено: %s" % fails))
app.destroy()
sys.exit(1 if fails else 0)
