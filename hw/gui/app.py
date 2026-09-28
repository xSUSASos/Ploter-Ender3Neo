# -*- coding: utf-8 -*-
"""
Окно программы: текст -> рукописный G-code -> принтер.

Шесть вкладок:
    Текст     — что писать и как это будет выглядеть
    Рисунок   — картинка, SVG или эскиз мышью, и как их рисовать
    Почерк    — пропись, загрузка фото, свои начертания букв
    Лист      — геометрия листа, размер, поля, выравнивание
    Реализм   — насколько «живым» будет письмо
    Печать    — перо, станок, связь по USB, калибровка Z
"""

import json
import math
import os
import sys
import threading
import traceback
from dataclasses import astuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from ..core.config import Config, SHEETS, HUMAN_PRESETS
from ..core.glyphset import FontSet, Glyph, normalize_strokes, order_strokes
from ..core.humanize import Human
from ..core import humanize as HM
from ..core import font_vector as FV
from ..core import layout as L
from ..core import preview as PV
from ..core import gcode as GC
from ..core import sheet as SH
from ..core import vectorize as VZ
from ..core import printer as PR
from ..core import drawing as DR
from .help_texts import HELP

PAD = 6

# Оформление. Обе темы строятся на «clam» — только его ttk позволяет
# перекрасить целиком, а родная тема Windows в тёмном виде не бывает.
#   side    — боковое меню и строка состояния
#   bg      — рабочая область; карточки-группы того же цвета, с рамкой
#   canvas  — подложка под превью листа
THEMES = {
    "light": dict(
        side="#eef0f4", side_fg="#3b4252", side_hover="#e2e6ec",
        side_active="#ffffff", bg="#ffffff", panel="#ffffff",
        text="#1f2430", heading="#111827", muted="#6b7280", error="#c0392b",
        border="#e1e4ea", field="#ffffff", field_fg="#111827",
        btn="#f3f4f7", btn_hover="#e8ebf0", btn_press="#dde2e9",
        accent="#2f6fed", accent_hover="#2560d6", accent_fg="#ffffff",
        track="#dfe3ea", select="#d6e4ff", select_fg="#111827",
        insert="#111827", canvas="#eceef2", shadow="#d3d7de", tiles="#f5f6f8",
        tip_bg="#1f2430", tip_fg="#f3f4f6"),
    "dark": dict(
        side="#16171a", side_fg="#c5cad3", side_hover="#212327",
        side_active="#26282d", bg="#1d1f23", panel="#26282d",
        text="#e3e5e8", heading="#f3f4f6", muted="#9aa1ab", error="#ff8f86",
        border="#33363c", field="#26282d", field_fg="#e8eaed",
        btn="#2a2d32", btn_hover="#33363c", btn_press="#3b3f46",
        accent="#5b8def", accent_hover="#739ef2", accent_fg="#ffffff",
        track="#393c43", select="#34507f", select_fg="#ffffff",
        insert="#e8eaed", canvas="#131417", shadow="#0b0c0e", tiles="#191a1d",
        tip_bg="#3a3e46", tip_fg="#f3f4f6"),
}
UI_FONT = "Segoe UI"

# разделы бокового меню: (атрибут вкладки, название, пояснение в шапке)
SECTIONS = [
    ("tab_text", "Текст", "что написать и как это будет выглядеть"),
    ("tab_draw", "Рисунок", "картинка, SVG или эскиз мышью прямо на листе"),
    ("tab_hand", "Почерк", "свой почерк с фото прописи или готовая пропись"),
    ("tab_page", "Лист", "бумага, поля, строки, клетка и поворот"),
    ("tab_human", "Реализм", "живость письма, помарки и каллиграфия"),
    ("tab_print", "Печать", "перо, принтер, связь по USB и калибровка Z"),
]

# Виртуальные коды клавиш Windows: не зависят от раскладки. Tk привязывает
# копирование к символу «c», а в русской раскладке это «с» (Cyrillic_es) —
# поэтому Ctrl+C/V/X/A/Z в русской раскладке молча не работали.
_VK_EVENTS = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>",
              90: "<<Undo>>", 89: "<<Redo>>"}
_LATIN_KEYS = {"c", "v", "x", "z", "y"}
DEMO = ("Привет! Это текст, который принтер напишет пером как от руки.\n\n"
        "Замените его на свой — превью справа обновится само (или по F5), "
        "а когда всё устроит, нажмите «Сохранить G-code» или «Печать».")
NO_ART = "файл не выбран — можно просто рисовать мышью на листе справа"


# ------------------------------------------------------------ мелочи UI

class InfoIcon:
    """
    Значок «i» в кружке; при наведении всплывает пояснение к настройке.
    Рисуется на холсте, а не символом шрифта: ⓘ есть не во всех шрифтах.
    """

    def __init__(self, app, parent, text):
        self.app = app
        self.text = text
        self.tip = None
        self._after = None
        self.hover = False
        self.cv = tk.Canvas(parent, width=18, height=18, highlightthickness=0,
                            borderwidth=0, cursor="question_arrow")
        self.cv.bind("<Enter>", self._enter)
        self.cv.bind("<Leave>", self._leave)
        self.cv.bind("<Button-1>", lambda e: self._show())
        app._infos.append(self)
        self.paint()

    def grid(self, **kw):
        self.cv.grid(**kw)
        return self

    def pack(self, **kw):
        self.cv.pack(**kw)
        return self

    def paint(self):
        C = self.app.C
        col = C["accent"] if self.hover else C["muted"]
        c = self.cv
        c.delete("all")
        c.configure(background=C["bg"])
        c.create_oval(2, 2, 16, 16, outline=col, width=1.4)
        c.create_text(9, 9.5, text="i", fill=col, font=(UI_FONT, 8, "bold"))

    def _enter(self, _e):
        self.hover = True
        self.paint()
        self._after = self.cv.after(350, self._show)

    def _leave(self, _e):
        self.hover = False
        self.paint()
        if self._after:
            self.cv.after_cancel(self._after)
            self._after = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None

    def _show(self):
        if self.tip is not None or not self.text:
            return
        C = self.app.C
        tw = tk.Toplevel(self.cv)
        tw.wm_overrideredirect(True)
        try:
            tw.attributes("-topmost", True)
        except tk.TclError:
            pass
        tw.configure(background=C["tip_bg"])
        tk.Label(tw, text=self.text, justify="left", wraplength=340,
                 background=C["tip_bg"], foreground=C["tip_fg"],
                 font=(UI_FONT, 9), padx=10, pady=7).pack()
        tw.update_idletasks()
        x = self.cv.winfo_rootx() + 22
        y = self.cv.winfo_rooty() - 4
        top = self.cv.winfo_toplevel()
        right = min(self.cv.winfo_screenwidth(), top.winfo_rootx() + top.winfo_width())
        if x + tw.winfo_width() > right - 8:              # не за край окна
            x = self.cv.winfo_rootx() - tw.winfo_width() - 6
        tw.wm_geometry("+%d+%d" % (x, y))
        self.tip = tw


class Field:
    """Одна настройка: подпись + поле ввода/ползунок, привязанная к конфигу."""

    def __init__(self, app, parent, row, label, section, attr, kind="float",
                 lo=0.0, hi=1.0, step=None, values=None, width=8, tip=""):
        self.app = app
        self.section = section
        self.attr = attr
        self.kind = kind
        self.cast = {"float": float, "int": int, "bool": bool,
                     "str": str}[kind if kind != "scale" else "float"]

        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                           padx=(6, 10), pady=3)
        if kind == "bool":
            self.var = tk.BooleanVar()
            w = ttk.Checkbutton(parent, variable=self.var,
                                command=app.on_change)
        elif kind == "str" and values:
            self.var = tk.StringVar()
            # словарь {значение: подпись}: в конфиге значение, в списке подпись
            self.labels = dict(values) if isinstance(values, dict) else None
            shown = list(self.labels.values()) if self.labels else list(values)
            w = ttk.Combobox(parent, textvariable=self.var, values=shown,
                             state="readonly", width=width + 6)
            w.bind("<<ComboboxSelected>>", lambda e: app.on_change())
        elif kind == "scale":
            self.var = tk.DoubleVar()
            box = ttk.Frame(parent)
            self.scale = ttk.Scale(box, from_=lo, to=hi, variable=self.var,
                                   orient="horizontal", length=150,
                                   command=lambda e: self._scale_moved())
            self.scale.pack(side="left")
            # рядом с ползунком — поле: точное значение можно вписать руками
            self.echo_var = tk.StringVar()
            self.echo = ttk.Entry(box, width=7, textvariable=self.echo_var)
            self.echo.pack(side="left", padx=(6, 0))
            self.echo.bind("<Return>", lambda e: self._echo_typed())
            self.echo.bind("<FocusOut>", lambda e: self._echo_typed())
            w = box
        else:
            self.var = tk.StringVar()
            w = ttk.Entry(parent, textvariable=self.var, width=width)
            w.bind("<FocusOut>", lambda e: app.on_change())
            w.bind("<Return>", lambda e: app.on_change())
        w.grid(row=row, column=1, sticky="w", padx=(0, 6), pady=3)
        self.widget = w
        # пояснение — во всплывающей подсказке у значка «i», а не строкой рядом
        self.help = HELP.get((section, attr)) or tip or label
        self.info = InfoIcon(app, parent, self.help).grid(
            row=row, column=2, sticky="w", padx=(0, 6))
        app.fields.append(self)

    def _scale_moved(self):
        self.echo_var.set(self._fmt(self.var.get()))
        self.app.on_change(defer=True)

    def _echo_typed(self):
        try:
            v = float(self.echo_var.get().replace(",", "."))
        except ValueError:
            self.echo_var.set(self._fmt(self.var.get()))
            return
        if abs(v - self.var.get()) > 1e-9:
            self.var.set(v)
            self.app.on_change()

    @staticmethod
    def _fmt(v):
        return ("%.3f" % v).rstrip("0").rstrip(".") if isinstance(v, float) else str(v)

    def obj(self):
        return getattr(self.app.cfg, self.section)

    def pull(self):
        """конфиг -> виджет"""
        v = getattr(self.obj(), self.attr)
        if self.kind == "scale":
            self.var.set(float(v))
            self.echo_var.set(self._fmt(float(v)))
        elif self.kind == "bool":
            self.var.set(bool(v))
        elif self.kind == "str":
            # поворот хранится числом, а в списке значений — "0", "90", ...
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            if getattr(self, "labels", None):
                v = self.labels.get(v, v)
            self.var.set(str(v))
        else:
            self.var.set(self._fmt(v))

    def push(self):
        """виджет -> конфиг. -> True, если значение изменилось"""
        try:
            raw = self.var.get()
            if getattr(self, "labels", None):
                raw = {lb: k for k, lb in self.labels.items()}.get(raw, raw)
            val = raw if self.kind in ("bool", "str") else self.cast(float(raw))
            if self.kind == "int":
                val = int(round(float(raw)))
        except (ValueError, tk.TclError):
            return False
        old = getattr(self.obj(), self.attr)
        if old != val:
            setattr(self.obj(), self.attr, val)
            return True
        return False


# ------------------------------------------------------------------ окно

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Плоттер · Ender 3 Neo — текст и рисунки пером")
        self.geometry("1320x840")
        self.minsize(1100, 700)
        self._setup_fonts()

        self.cfg = Config()
        self.fontset = FontSet(flat=self.cfg.curve_flatness)
        self.human = Human(self.cfg.human)
        self.pages = []
        self.page_index = 0
        self.fields = []
        self._infos = []           # значки «i» — перекрашиваются с темой
        self.link = None
        self._defer = None
        self._photo = None
        self._recognized = []      # [char, вариант, Glyph, картинка, брать?, виджет]
        self._font_marks = set()   # отмеченные на удаление (символ, вариант)
        self._thumbs = []
        self._spec = None
        self._art = None           # картинка или SVG (DR.ArtSource)
        self._sketch = []          # эскиз мышью, мм листа
        self._sketch_undo = []
        self._art_page = 0         # на каком листе рисунок
        self._art_note = ""
        self._dphoto = None
        self._dmap = None          # холст рисунка: (ox, oy, px на мм, высота листа)
        self._drag = None
        self._sel = set()          # выделенные штрихи эскиза (номера)
        self._sel_art = False      # выделена картинка из файла

        self._profile_path = ""
        self.v_dark = tk.BooleanVar(value=self._initial_dark())
        self._native_theme = ttk.Style(self).theme_use()
        self.C = THEMES["light"]
        self._build()
        self._install_edit_keys()
        self.apply_theme()
        for f in self.fields:
            f.pull()
        self.txt.insert("1.0", DEMO)
        restored = self._restore_state()
        self.after(120, lambda: self._first_draw(restored))

    def _first_draw(self, restored):
        self.rebuild()
        if restored:
            self.status.configure(
                text=self.status.cget("text") + "   ·   восстановлено: " + restored)

    def _say(self, text, error=False):
        """Строка состояния; цвет берётся из текущей темы."""
        self.status.configure(text=text,
                              style="StatusErr.TLabel" if error else "Status.TLabel")

    def _setup_fonts(self):
        """Segoe UI вместо мелкого системного шрифта Tk — всем виджетам сразу."""
        from tkinter import font as tkfont
        for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10),
                           ("TkMenuFont", 10), ("TkHeadingFont", 10),
                           ("TkCaptionFont", 10), ("TkTooltipFont", 9)):
            try:
                tkfont.nametofont(name).configure(family=UI_FONT, size=size)
            except tk.TclError:
                pass

    # -------------------------------------------------- память между запусками
    @staticmethod
    def _state_file():
        # тесты уводят файл состояния в сторону, чтобы не затереть настоящий
        if os.environ.get("HW_STATE"):
            return os.environ["HW_STATE"]
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
            else os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
        return os.path.join(base, "hw-настройки.json")

    def _restore_state(self):
        """Подтянуть профиль и почерк с прошлого запуска. -> что восстановили."""
        try:
            with open(self._state_file(), encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            return ""
        got = []
        prof = st.get("profile")
        conf = st.get("config")
        if isinstance(conf, dict):
            # все настройки окна с прошлого раза — даже не сохранённые в профиль
            try:
                self.cfg = Config.from_dict(conf)
                for fl in self.fields:
                    fl.pull()
                self._profile_path = prof or ""
                got.append("настройки")
            except (TypeError, ValueError, KeyError, AttributeError):
                self.cfg = Config()
                for fl in self.fields:
                    fl.pull()
        elif prof and os.path.exists(prof):
            try:
                self.cfg = Config.load(prof)
                for fl in self.fields:
                    fl.pull()
                self._profile_path = prof
                got.append("профиль %s" % os.path.basename(prof))
            except (OSError, ValueError, KeyError):
                pass
        font = st.get("font") or self.cfg.font_path
        if font and (font == FontSet.CALLIG or os.path.exists(font))                 and self._load_font_file(font, quiet=True):
            got.append("почерк %s" % os.path.basename(font))
        art = st.get("art")
        if art and os.path.exists(art) and self._load_art(art, quiet=True):
            got.append("рисунок %s" % os.path.basename(art))
        try:
            self._sketch = [[(float(x), float(y)) for x, y in q]
                            for q in st.get("sketch") or [] if len(q) > 1]
        except (TypeError, ValueError):
            self._sketch = []
        if self._sketch:
            got.append("эскиз")
        text = st.get("text")
        if isinstance(text, str):
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", text)
            got.append("текст")
        self._restore_ui(st.get("ui") or {})
        geom = st.get("geometry")
        if geom:
            try:
                self.geometry(geom)
            except tk.TclError:
                pass
        return ", ".join(got)

    # переключатели окна, которых нет в Config: тоже переживают перезапуск
    _UI_VARS = ("v_travel", "v_margins", "v_fallback", "v_sheet", "v_preset",
                "v_port", "v_baud", "v_ink", "v_delines", "v_cyr", "v_lat",
                "v_dig", "v_pun", "v_variants")

    def _ui_state(self):
        ui = {}
        for name in self._UI_VARS:
            var = getattr(self, name, None)
            if var is not None:
                try:
                    ui[name] = var.get()
                except tk.TclError:
                    pass
        try:
            tabs = self.nb.tabs()
            ui["tab"] = tabs.index(self.nb.select())
        except (tk.TclError, ValueError, AttributeError):
            pass
        ui["page_index"] = self.page_index
        return ui

    def _restore_ui(self, ui):
        for name in self._UI_VARS:
            var = getattr(self, name, None)
            if var is not None and name in ui:
                try:
                    var.set(ui[name])
                except (tk.TclError, TypeError, ValueError):
                    pass
        self.fontset.builtin_fallback = bool(self.v_fallback.get())
        try:
            tabs = self.nb.tabs()
            i = int(ui.get("tab", -1))
            if 0 <= i < len(tabs):
                self.nb.select(tabs[i])
                self._paint_nav()
        except (tk.TclError, TypeError, ValueError, AttributeError):
            pass
        try:
            self.page_index = max(0, int(ui.get("page_index", 0)))
        except (TypeError, ValueError):
            pass

    def _save_state(self):
        """
        Записать всё, что нужно для следующего запуска: настройки, текст,
        почерк, рисунок, эскиз, переключатели окна. Пишем во временный
        файл и подменяем им старый — оборванная запись не испортит
        настройки.
        """
        self._autosave_id = None
        try:
            self._collect()
        except Exception:                               # noqa: BLE001
            pass                                        # недописанное поле
        path = self._state_file()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"profile": self._profile_path,
                           "font": self.cfg.font_path,
                           "theme": "dark" if self.v_dark.get() else "light",
                           "art": self._art.path if self._art else "",
                           "sketch": [[[round(x, 3), round(y, 3)] for x, y in q]
                                      for q in self._sketch],
                           "geometry": self.winfo_geometry(),
                           "config": self.cfg.to_dict(),
                           "text": self.txt.get("1.0", "end-1c"),
                           "ui": self._ui_state()},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except (OSError, tk.TclError):
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _autosave(self):
        """Сохранить состояние через пару секунд тишины после правки."""
        if getattr(self, "_autosave_id", None) is not None:
            self.after_cancel(self._autosave_id)
        self._autosave_id = self.after(2000, self._save_state)

    def reset_settings(self):
        """Все настройки — по умолчанию. Текст, почерк и рисунок остаются."""
        if not messagebox.askyesno(
                "Сбросить настройки",
                "Вернуть все настройки к значениям по умолчанию?\n"
                "Текст, почерк и рисунок останутся."):
            return
        font = self.cfg.font_path
        self.cfg = Config()
        self.cfg.font_path = font
        for fl in self.fields:
            fl.pull()
        self._profile_path = ""
        self.v_sheet.set("A5")
        self.v_preset.set("Обычно")
        self.rebuild()
        self._save_state()
        self._say(self.status.cget("text") + "   ·   настройки сброшены")

    # ------------------------------------------------------------ каркас
    def _build(self):
        # строка состояния — внизу во всю ширину
        self.statusbar = tk.Frame(self, height=30)
        self.statusbar.pack(side="bottom", fill="x")
        self.status = ttk.Label(self.statusbar, text="", anchor="w",
                                style="Status.TLabel")
        self.status.pack(side="left", fill="x", expand=True, padx=14, pady=5)

        # боковое меню
        self.side = tk.Frame(self, width=200)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.side_title = tk.Label(self.side, text="Плоттер", anchor="w",
                                   font=(UI_FONT, 16, "bold"))
        self.side_title.pack(fill="x", padx=20, pady=(20, 0))
        self.side_sub = tk.Label(self.side, text="Ender 3 Neo · перо", anchor="w",
                                 font=(UI_FONT, 9))
        self.side_sub.pack(fill="x", padx=20, pady=(0, 18))
        self.nav = []
        self.side_theme = ttk.Checkbutton(self.side, text="Тёмная тема",
                                          variable=self.v_dark,
                                          command=self.apply_theme,
                                          style="Side.TCheckbutton")
        self.side_theme.pack(side="bottom", anchor="w", padx=18, pady=16)

        # рабочая область: шапка раздела + страницы без ярлыков вкладок
        main = ttk.Frame(self)
        main.pack(side="left", fill="both", expand=True)
        head = ttk.Frame(main)
        head.pack(fill="x", padx=22, pady=(16, 6))
        titles = ttk.Frame(head)
        titles.pack(side="left")
        self.h_title = ttk.Label(titles, text="", style="Title.TLabel")
        self.h_title.pack(anchor="w")
        self.h_sub = ttk.Label(titles, text="", style="Hint.TLabel")
        self.h_sub.pack(anchor="w")
        acts = ttk.Frame(head)
        acts.pack(side="right")
        ttk.Button(acts, text="Обновить  F5", command=self.rebuild).pack(side="left")
        ttk.Button(acts, text="Граница", command=self.do_frame).pack(side="left", padx=(6, 0))
        self.btn_export = ttk.Button(acts, text="Экспорт  ▾", command=self.export_menu)
        self.btn_export.pack(side="left", padx=(6, 0))
        ttk.Button(acts, text="Сохранить G-code",
                   command=self.save_gcode).pack(side="left", padx=(6, 0))
        ttk.Button(acts, text="Печать  ▶", style="Accent.TButton",
                   command=self.do_print).pack(side="left", padx=(6, 0))
        self.h_line = tk.Frame(main, height=1)
        self.h_line.pack(fill="x", padx=22, pady=(6, 0))

        nb = ttk.Notebook(main, style="Pages.TNotebook")
        nb.pack(fill="both", expand=True, padx=14, pady=(6, 8))
        self.nb = nb
        self.tab_text = ttk.Frame(nb)
        self.tab_draw = ttk.Frame(nb)
        self.tab_hand = ttk.Frame(nb)
        self.tab_page = ttk.Frame(nb)
        self.tab_human = ttk.Frame(nb)
        self.tab_print = ttk.Frame(nb)
        for attr, name, _sub in SECTIONS:
            nb.add(getattr(self, attr), text=name)
            self._nav_item(attr, name)

        self._build_text()
        self._build_draw()
        self._build_hand()
        self._build_page()
        self._build_human()
        self._build_print()

        nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.bind("<F5>", lambda e: self.rebuild())
        self.bind("<Control-s>", lambda e: self.save_gcode())
        self.bind("<Control-S>", lambda e: self.save_gcode())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _nav_item(self, attr, name):
        """Пункт бокового меню: цветная полоска слева у текущего раздела."""
        row = tk.Frame(self.side, cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        bar = tk.Frame(row, width=3)
        bar.pack(side="left", fill="y")
        lbl = tk.Label(row, text=name, anchor="w", font=(UI_FONT, 11),
                       padx=14, pady=8, cursor="hand2")
        lbl.pack(side="left", fill="x", expand=True)
        item = {"attr": attr, "row": row, "bar": bar, "lbl": lbl, "hover": False}

        def go(_e=None):
            self.nb.select(getattr(self, attr))

        def hover(on):
            item["hover"] = on
            self._paint_nav()
        for w in (row, lbl, bar):
            w.bind("<Button-1>", go)
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))
        self.nav.append(item)

    def _paint_nav(self):
        C = self.C
        try:
            cur = self.nb.select()
        except tk.TclError:
            cur = ""
        for it in self.nav:
            active = cur == str(getattr(self, it["attr"]))
            bg = C["side_active"] if active else (
                C["side_hover"] if it["hover"] else C["side"])
            it["row"].configure(background=bg)
            it["lbl"].configure(background=bg,
                                foreground=C["heading"] if active else C["side_fg"],
                                font=(UI_FONT, 11, "bold" if active else "normal"))
            it["bar"].configure(background=C["accent"] if active else bg)
        for attr, name, sub in SECTIONS:
            if cur == str(getattr(self, attr)):
                self.h_title.configure(text=name)
                self.h_sub.configure(text=sub)

    def export_menu(self):
        m = self._menu()
        m.add_command(label="Превью в SVG…", command=self.save_svg)
        m.add_command(label="Превью в PNG…", command=self.save_png)
        m.add_separator()
        m.add_command(label="Сохранить профиль настроек…", command=self.save_profile)
        m.add_command(label="Загрузить профиль настроек…", command=self.load_profile)
        m.add_command(label="Сбросить настройки…", command=self.reset_settings)
        b = self.btn_export
        try:
            m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
        finally:
            m.grab_release()

    # -------------------------------------------------------- вкладка Текст
    def _build_text(self):
        f = self.tab_text
        f.columnconfigure(0, weight=4, minsize=380)
        f.columnconfigure(1, weight=5, minsize=440)
        f.rowconfigure(1, weight=1)

        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, sticky="ew", pady=(8, 6), padx=8)
        ttk.Button(bar, text="Открыть файл…", command=self.open_text).pack(side="left")
        ttk.Button(bar, text="Очистить",
                   command=lambda: self.txt.delete("1.0", "end")).pack(side="left", padx=6)
        self.lbl_chars = ttk.Label(bar, text="", style="Hint.TLabel")
        self.lbl_chars.pack(side="right")

        # поле текста — в рамке, которая подсвечивается при вводе
        self.txt_box = tk.Frame(f, highlightthickness=1, borderwidth=0)
        self.txt_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.txt_box.rowconfigure(0, weight=1)
        self.txt_box.columnconfigure(0, weight=1)
        self.txt = tk.Text(self.txt_box, wrap="word", undo=True,
                           font=(UI_FONT, 12), borderwidth=0, relief="flat",
                           padx=16, pady=12, spacing1=2, spacing3=4,
                           highlightthickness=0)
        self.txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.txt_box, command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.bind("<FocusIn>", lambda e: self._txt_focus(True))
        self.txt.bind("<FocusOut>", lambda e: self._txt_focus(False))
        self.txt.bind("<<Modified>>", self._txt_modified)

        right = ttk.Frame(f)
        right.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(4, 8), pady=(8, 8))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(top, text="‹", width=2, style="Small.TButton",
                   command=lambda: self.turn(-1)).pack(side="left")
        self.lbl_page = ttk.Label(top, text="—", width=12, anchor="center")
        self.lbl_page.pack(side="left", padx=2)
        ttk.Button(top, text="›", width=2, style="Small.TButton",
                   command=lambda: self.turn(1)).pack(side="left")

        self.v_travel = tk.BooleanVar(value=False)
        self.v_margins = tk.BooleanVar(value=True)
        InfoIcon(self, top, HELP["margins"]).pack(side="right", padx=(2, 0))
        ttk.Checkbutton(top, text="поля", variable=self.v_margins,
                        command=self.redraw).pack(side="right")
        InfoIcon(self, top, HELP["travel"]).pack(side="right", padx=(2, 12))
        ttk.Checkbutton(top, text="холостые ходы", variable=self.v_travel,
                        command=self.redraw).pack(side="right")

        self.canvas = tk.Canvas(right, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda e: self.redraw())

    def _txt_focus(self, on):
        C = self.C
        self.txt_box.configure(highlightbackground=C["accent"] if on else C["border"],
                               highlightcolor=C["accent"] if on else C["border"])

    def _txt_modified(self, _e=None):
        try:
            n = len(self.txt.get("1.0", "end-1c"))
            self.txt.edit_modified(False)
        except tk.TclError:
            return
        self.lbl_chars.configure(text="символов: %d" % n)

    # ------------------------------------------------------ вкладка Рисунок
    def _scroll_panel(self, parent, width=440):
        """Столбец настроек с прокруткой колесом — их больше, чем влезает."""
        box = ttk.Frame(parent)
        cv = tk.Canvas(box, highlightthickness=0, width=width, borderwidth=0)
        sb = ttk.Scrollbar(box, command=cv.yview)
        inner = ttk.Frame(cv)
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(
            scrollregion=cv.bbox("all"), width=max(width, e.width)))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="y")
        sb.pack(side="left", fill="y")

        def wheel(e):
            cv.yview_scroll(-1 if e.delta > 0 else 1, "units")
        box.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", wheel))
        box.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))
        return box, cv, inner

    def _build_draw(self):
        f = self.tab_draw
        f.columnconfigure(1, weight=1)
        f.rowconfigure(0, weight=1)
        box, self.dpanel, inner = self._scroll_panel(f)
        box.grid(row=0, column=0, sticky="nsw", padx=(4, 2), pady=6)

        g0 = ttk.LabelFrame(inner, text="Что рисовать")
        g0.pack(fill="x", padx=4, pady=(0, 6))
        row = ttk.Frame(g0)
        row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Button(row, text="Открыть картинку или SVG…",
                   command=self.open_art).pack(side="left")
        ttk.Button(row, text="Убрать", command=self.clear_art).pack(side="left", padx=4)
        self.lbl_art = ttk.Label(g0, text=NO_ART, style="Hint.TLabel",
                                 wraplength=400, justify="left")
        self.lbl_art.pack(anchor="w", padx=8, pady=(0, 4))
        gg = ttk.Frame(g0)
        gg.pack(fill="x", padx=2, pady=(0, 4))
        r = 0
        Field(self, gg, r, "печатать рисунок", "draw", "enabled", "bool"); r += 1
        Field(self, gg, r, "с текстом", "draw", "layout", "str",
              values=DR.LAYOUTS, width=16); r += 1
        Field(self, gg, r, "на листе №", "draw", "page_no", "int", width=5,
              tip="для «поверх текста»"); r += 1

        g1 = ttk.LabelFrame(inner, text="Эскиз мышью (прямо на листе)")
        g1.pack(fill="x", padx=4, pady=(0, 6))
        self.v_tool = tk.StringVar(value="pen")
        tr = ttk.Frame(g1)
        tr.pack(fill="x", padx=6, pady=(6, 2))
        for val, lb in (("select", "двигать"), ("pen", "перо"), ("line", "линия"),
                        ("rect", "прямоуг."), ("ellipse", "эллипс"),
                        ("eraser", "ластик")):
            ttk.Radiobutton(tr, text=lb, value=val, variable=self.v_tool,
                            command=self._tool_cursor).pack(side="left", padx=(0, 6))
        sr = ttk.Frame(g1)
        sr.pack(fill="x", padx=6, pady=2)
        ttk.Button(sr, text="Выделить всё", command=self.sel_all).pack(side="left")
        ttk.Button(sr, text="Удалить", command=self.sel_delete).pack(side="left", padx=4)
        ttk.Button(sr, text="Отразить ↔",
                   command=lambda: self.sel_apply("mirror")).pack(side="left")
        sr2 = ttk.Frame(g1)
        sr2.pack(fill="x", padx=6, pady=2)
        ttk.Label(sr2, text="масштаб, %").pack(side="left")
        self.v_sel_scale = tk.StringVar(value="120")
        ttk.Entry(sr2, textvariable=self.v_sel_scale, width=5).pack(side="left", padx=3)
        ttk.Button(sr2, text="ОК", width=4,
                   command=lambda: self.sel_apply("scale")).pack(side="left")
        ttk.Label(sr2, text="   повернуть, °").pack(side="left")
        self.v_sel_rot = tk.StringVar(value="15")
        ttk.Entry(sr2, textvariable=self.v_sel_rot, width=5).pack(side="left", padx=3)
        ttk.Button(sr2, text="ОК", width=4,
                   command=lambda: self.sel_apply("rot")).pack(side="left")
        ttk.Label(g1, text="«Двигать»: щёлкните по линии или картинке, или обведите "
                           "рамкой. Тяните середину — сдвиг, угол — размер, кружок "
                           "сверху — поворот. Колесо — размер, стрелки — сдвиг на 1 мм "
                           "(Shift — 5), Delete — удалить, Ctrl+A/C/V.",
                  style="Hint.TLabel", wraplength=400, justify="left").pack(
            anchor="w", padx=8, pady=(0, 2))
        br = ttk.Frame(g1)
        br.pack(fill="x", padx=6, pady=2)
        ttk.Button(br, text="Отменить (Ctrl+Z)", command=self.sketch_undo).pack(side="left")
        ttk.Button(br, text="Очистить", command=self.sketch_clear).pack(side="left", padx=4)
        br2 = ttk.Frame(g1)
        br2.pack(fill="x", padx=6, pady=2)
        ttk.Button(br2, text="Сохранить эскиз…", command=self.sketch_save).pack(side="left")
        ttk.Button(br2, text="Загрузить эскиз…",
                   command=self.sketch_load).pack(side="left", padx=4)
        ttk.Label(g1, text="Shift — ровно: линия через 15°, квадрат, круг.",
                  style="Hint.TLabel").pack(anchor="w", padx=8)
        g1f = ttk.Frame(g1)
        g1f.pack(fill="x", padx=2, pady=(0, 4))
        Field(self, g1f, 0, "сглаживание руки", "draw", "sketch_smooth", "int",
              width=5, tip="1 — как нарисовано")

        g2 = ttk.LabelFrame(inner, text="Размещение картинки")
        g2.pack(fill="x", padx=4, pady=(0, 6))
        r = 0
        Field(self, g2, r, "ширина, мм", "draw", "width_mm", width=6,
              tip="0 и 0 — вписать в поля"); r += 1
        Field(self, g2, r, "высота, мм", "draw", "height_mm", width=6); r += 1
        Field(self, g2, r, "привязка", "draw", "anchor", "str",
              values=DR.ANCHORS, width=12); r += 1
        Field(self, g2, r, "сдвиг вправо, мм", "draw", "offset_x", width=6); r += 1
        Field(self, g2, r, "сдвиг вниз, мм", "draw", "offset_y", width=6); r += 1
        Field(self, g2, r, "поворот, °", "draw", "rotate", "scale", -180, 180); r += 1
        Field(self, g2, r, "отразить", "draw", "mirror", "bool"); r += 1
        Field(self, g2, r, "отступ до текста, мм", "draw", "gap_mm", width=6,
              tip="для «сверху»"); r += 1

        g3 = ttk.LabelFrame(inner, text="Картинка → линии")
        g3.pack(fill="x", padx=4, pady=(0, 6))
        r = 0
        Field(self, g3, r, "способ", "draw", "mode", "str",
              values=DR.MODES, width=14); r += 1
        Field(self, g3, r, "порог тёмного", "draw", "threshold",
              "scale", 0.05, 0.95); r += 1
        Field(self, g3, r, "инвертировать", "draw", "invert", "bool",
              tip="светлое на тёмном"); r += 1
        Field(self, g3, r, "автоконтраст", "draw", "autocontrast", "bool"); r += 1
        Field(self, g3, r, "размытие, px", "draw", "blur", "scale", 0, 4); r += 1
        Field(self, g3, r, "детальность, px", "draw", "detail", "int", width=6,
              tip="больше — точнее и дольше"); r += 1
        Field(self, g3, r, "чувствительность краёв", "draw", "edge_sens",
              "scale", 0, 1); r += 1
        Field(self, g3, r, "шаг штриховки, мм", "draw", "hatch_step", width=6); r += 1
        Field(self, g3, r, "угол штриховки, °", "draw", "hatch_angle",
              "scale", -90, 90); r += 1
        Field(self, g3, r, "тонов штриховки", "draw", "hatch_levels", "int",
              width=5, tip="1…4"); r += 1
        Field(self, g3, r, "короче — выкинуть, мм", "draw", "min_len", width=6); r += 1
        Field(self, g3, r, "упрощение, мм", "draw", "simplify", width=6); r += 1

        g4 = ttk.LabelFrame(inner, text="Перо для рисунка")
        g4.pack(fill="x", padx=4, pady=(0, 6))
        r = 0
        Field(self, g4, r, "обводить раз", "draw", "passes", "int", width=5,
              tip="2–3 — линия гуще"); r += 1
        Field(self, g4, r, "скорость, %", "draw", "speed_pct", "int", width=5,
              tip="от подачи письма"); r += 1
        Field(self, g4, r, "толщина пера, мм", "draw", "pen_width", width=6,
              tip="для превью"); r += 1
        Field(self, g4, r, "дрожание руки, мм", "draw", "tremor",
              "scale", 0, 0.5); r += 1
        Field(self, g4, r, "оптимизировать порядок", "draw", "optimize", "bool"); r += 1
        Field(self, g4, r, "не поднимать перо ближе, мм", "draw", "join_gap",
              width=6); r += 1

        right = ttk.Frame(f)
        right.grid(row=0, column=1, sticky="nsew", padx=4, pady=6)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        top = ttk.Frame(right)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.lbl_dpage = ttk.Label(top, text="—", style="Hint.TLabel")
        self.lbl_dpage.pack(side="left")
        self.dcanvas = tk.Canvas(right, highlightthickness=0, cursor="pencil")
        self.dcanvas.grid(row=1, column=0, sticky="nsew")
        self.dcanvas.bind("<Configure>", lambda e: self.redraw_draw())
        self.dcanvas.bind("<ButtonPress-1>", self._d_press)
        self.dcanvas.bind("<B1-Motion>", self._d_move)
        self.dcanvas.bind("<ButtonRelease-1>", self._d_release)
        self.dcanvas.bind("<MouseWheel>", self._d_wheel)
        self.dcanvas.bind("<Delete>", lambda e: self.sel_delete())
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, 1), ("Down", 0, -1)):
            self.dcanvas.bind("<%s>" % key, lambda e, a=dx, b=dy: self._sel_nudge(e, a, b))

    # ------------------------------------------------------- вкладка Почерк
    def _build_hand(self):
        f = self.tab_hand
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)

        box, self.handpanel, left = self._scroll_panel(f, width=300)
        box.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=6, pady=6)

        g1 = ttk.LabelFrame(left, text="1. Напечатать пропись")
        g1.pack(fill="x", pady=(0, 8))
        self.v_cyr = tk.BooleanVar(value=True)
        self.v_lat = tk.BooleanVar(value=False)
        self.v_dig = tk.BooleanVar(value=True)
        self.v_pun = tk.BooleanVar(value=True)
        for t, v in (("кириллица", self.v_cyr), ("латиница", self.v_lat),
                     ("цифры", self.v_dig), ("знаки", self.v_pun)):
            ttk.Checkbutton(g1, text=t, variable=v).pack(anchor="w", padx=8)
        row = ttk.Frame(g1)
        row.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Label(row, text="вариантов на букву:").pack(side="left")
        self.v_variants = tk.IntVar(value=3)
        ttk.Spinbox(row, from_=1, to=6, width=4,
                    textvariable=self.v_variants).pack(side="left", padx=4)
        InfoIcon(self, row, HELP["variants"]).pack(side="left")
        ttk.Button(g1, text="Создать шаблон…",
                   command=self.make_template).pack(fill="x", padx=8, pady=6)
        ttk.Label(g1, text="Распечатайте, впишите буквы\nтёмной ручкой, сфотографируйте.",
                  style="Hint.TLabel", justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        g2 = ttk.LabelFrame(left, text="2. Загрузить фото")
        g2.pack(fill="x", pady=(0, 8))
        ttk.Button(g2, text="Выбрать фото…",
                   command=self.load_photos).pack(fill="x", padx=8, pady=(6, 2))
        rr = ttk.Frame(g2)
        rr.pack(fill="x", padx=8, pady=2)
        ttk.Label(rr, text="порог чернил:").pack(side="left")
        self.v_ink = tk.DoubleVar(value=0.68)
        ttk.Scale(rr, from_=0.35, to=0.92, variable=self.v_ink,
                  orient="horizontal", length=110).pack(side="left", padx=4)
        self.lbl_ink = ttk.Label(rr, text="0.68", width=5)
        self.lbl_ink.pack(side="left")
        InfoIcon(self, rr, HELP["ink"]).pack(side="left")
        self.v_ink.trace_add("write", lambda *a: self.lbl_ink.configure(
            text="%.2f" % self.v_ink.get()))
        self.v_delines = tk.BooleanVar(value=False)
        dl = ttk.Frame(g2)
        dl.pack(anchor="w", padx=8)
        ttk.Checkbutton(dl, text="вычитать разлиновку",
                        variable=self.v_delines).pack(side="left")
        InfoIcon(self, dl, HELP["delines"]).pack(side="left", padx=4)
        ttk.Label(g2, text="нужно, только если линии шаблона\nпропечатались слишком тёмными",
                  style="Hint.TLabel", justify="left").pack(anchor="w", padx=8)
        ttk.Button(g2, text="Распознать заново",
                   command=self.rerecognize).pack(fill="x", padx=8, pady=(2, 6))

        g3 = ttk.LabelFrame(left, text="3. Шрифт")
        g3.pack(fill="x")
        self.lbl_cov = ttk.Label(g3, text="оцифровано: 0 букв")
        self.lbl_cov.pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Button(g3, text="Принять распознанное",
                   command=self.accept_recognized).pack(fill="x", padx=8, pady=2)
        ttk.Button(g3, text="Сохранить шрифт…",
                   command=self.save_font).pack(fill="x", padx=8, pady=2)
        ttk.Button(g3, text="Загрузить шрифт…",
                   command=self.load_font).pack(fill="x", padx=8, pady=2)
        ttk.Button(g3, text="Каллиграфический (пропись)",
                   command=self.use_calligraphy).pack(fill="x", padx=8, pady=2)
        ttk.Button(g3, text="Очистить почерк",
                   command=self.clear_font).pack(fill="x", padx=8, pady=(2, 6))
        self.v_fallback = tk.BooleanVar(value=True)
        fb = ttk.Frame(g3)
        fb.pack(anchor="w", padx=8, pady=(0, 6))
        ttk.Checkbutton(fb, text="встроенный шрифт\nдля недостающих букв",
                        variable=self.v_fallback,
                        command=self.on_change).pack(side="left")
        InfoIcon(self, fb, HELP["fallback"]).pack(side="left", padx=4)

        head = ttk.Frame(f)
        head.grid(row=0, column=1, sticky="ew", padx=8, pady=(8, 0))
        self.v_view = tk.StringVar(value="new")
        ttk.Radiobutton(head, text="Распознанное", value="new",
                        variable=self.v_view,
                        command=self._render_glyph_grid).pack(side="left")
        ttk.Radiobutton(head, text="Мой шрифт", value="font",
                        variable=self.v_view,
                        command=self._render_glyph_grid).pack(side="left", padx=(10, 0))
        self.lbl_grid = ttk.Label(head, text="", style="Hint.TLabel")
        self.lbl_grid.pack(side="left", padx=14)
        self.btn_del = ttk.Button(head, text="Удалить отмеченные",
                                  command=self.delete_marked, state="disabled")
        self.btn_del.pack(side="right")

        holder = ttk.Frame(f)
        holder.grid(row=1, column=1, sticky="nsew", padx=6, pady=6)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.gcanvas = tk.Canvas(holder, highlightthickness=0)
        self.gcanvas.grid(row=0, column=0, sticky="nsew")
        gsb = ttk.Scrollbar(holder, command=self.gcanvas.yview)
        gsb.grid(row=0, column=1, sticky="ns")
        self.gcanvas.configure(yscrollcommand=gsb.set)
        self.gframe = ttk.Frame(self.gcanvas)
        self.gcanvas.create_window((0, 0), window=self.gframe, anchor="nw")
        self.gframe.bind("<Configure>", lambda e: self.gcanvas.configure(
            scrollregion=self.gcanvas.bbox("all")))

    # --------------------------------------------------------- вкладка Лист
    def _build_page(self):
        f = self.tab_page
        box, self.ppanel, wrap = self._scroll_panel(f, width=1080)
        box.pack(fill="both", expand=True, padx=10, pady=10)
        left = ttk.Frame(wrap)
        left.grid(row=0, column=0, sticky="nw")
        right = ttk.Frame(wrap)
        right.grid(row=0, column=1, sticky="nw")

        g = ttk.LabelFrame(left, text="Лист")
        g.pack(fill="x", padx=6, pady=6)
        r = 0
        ttk.Label(g, text="формат").grid(row=r, column=0, sticky="w", padx=(4, 6))
        self.v_sheet = tk.StringVar(value="A5")
        cb = ttk.Combobox(g, textvariable=self.v_sheet, state="readonly",
                          values=list(SHEETS) + ["свой"], width=20)
        cb.grid(row=r, column=1, sticky="w", pady=2)
        cb.bind("<<ComboboxSelected>>", self.on_sheet_preset)
        r += 1
        Field(self, g, r, "ширина, мм", "page", "sheet_w"); r += 1
        Field(self, g, r, "высота, мм", "page", "sheet_h"); r += 1
        Field(self, g, r, "поле слева", "page", "margin_left"); r += 1
        Field(self, g, r, "поле справа", "page", "margin_right"); r += 1
        Field(self, g, r, "поле сверху", "page", "margin_top"); r += 1
        Field(self, g, r, "поле снизу", "page", "margin_bottom"); r += 1
        Field(self, g, r, "угол листа на столе X", "page", "origin_x"); r += 1
        Field(self, g, r, "угол листа на столе Y", "page", "origin_y"); r += 1
        Field(self, g, r, "поворот листа на столе, °", "page", "rotate",
              "scale", 0, 360, tip="90 — альбомная"); r += 1

        gb = ttk.LabelFrame(left, text="Стол принтера сверху")
        gb.pack(fill="x", padx=6, pady=6)
        self.lbl_bed = ttk.Label(gb)
        self.lbl_bed.pack(padx=6, pady=(6, 2))
        ttk.Label(gb, text="красная черта — верх листа, кружок — начало стола",
                  style="Hint.TLabel").pack(padx=6, pady=(0, 6))
        self._bed_photo = None

        g3 = ttk.LabelFrame(left, text="Чтобы поместилось")
        g3.pack(fill="x", padx=6, pady=6)
        r = 0
        Field(self, g3, r, "подбирать размер автоматически", "page", "autofit",
              "bool"); r += 1
        Field(self, g3, r, "минимальный размер, мм", "page", "autofit_min_size"); r += 1
        Field(self, g3, r, "максимальный размер, мм", "page", "autofit_max_size"); r += 1
        Field(self, g3, r, "если не влезает", "page", "overflow", "str",
              values={"pages": "на несколько листов", "shrink": "уменьшить",
                      "clip": "обрезать"}, width=14); r += 1

        g2 = ttk.LabelFrame(right, text="Текст на листе")
        g2.pack(fill="x", padx=6, pady=6)
        r = 0
        Field(self, g2, r, "высота заглавной, мм", "page", "size_mm",
              tip="обычно 5…7 мм"); r += 1
        Field(self, g2, r, "межстрочный", "page", "line_spacing",
              tip="доли высоты буквы"); r += 1
        Field(self, g2, r, "ширина пробела", "page", "word_spacing"); r += 1
        Field(self, g2, r, "межбуквенный, мм", "page", "tracking_mm"); r += 1
        Field(self, g2, r, "выравнивание", "page", "align", "str",
              values={"left": "влево", "center": "по центру", "right": "вправо",
                      "justify": "по ширине"}, width=10); r += 1
        Field(self, g2, r, "красная строка, мм", "page", "first_line_indent",
              tip="влево и по ширине"); r += 1
        Field(self, g2, r, "отбивка между абзацами, мм", "page",
              "paragraph_gap"); r += 1
        Field(self, g2, r, "разлиновка", "page", "ruling", "bool"); r += 1
        Field(self, g2, r, "шаг разлиновки, мм", "page", "ruling_step",
              tip="0 — как межстрочный"); r += 1
        Field(self, g2, r, "поворот текста, °", "page", "text_angle",
              "scale", 0, 360, tip="против часовой"); r += 1
        Field(self, g2, r, "ужать повёрнутый текст в поля", "page",
              "text_angle_fit", "bool"); r += 1

        gq = ttk.LabelFrame(right, text="Тетрадь в клетку")
        gq.pack(fill="x", padx=6, pady=6)
        r = 0
        Field(self, gq, r, "писать по клеткам", "page", "grid", "bool"); r += 1
        Field(self, gq, r, "размер клетки, мм", "page", "grid_cell",
              width=6, tip="обычно 5"); r += 1
        Field(self, gq, r, "первая строка от верха, мм", "page", "grid_first",
              width=6, tip="до линии букв"); r += 1
        Field(self, gq, r, "строка через клеток", "page", "grid_every", "int",
              width=5, tip="1 — в каждой"); r += 1
        Field(self, gq, r, "высота заглавной, доля строки", "page", "grid_fill",
              "scale", 0.4, 1.6, tip="0.8 — 4 мм"); r += 1
        self.lbl_grid_info = ttk.Label(gq, text="", style="Hint.TLabel",
                                       justify="left")
        self.lbl_grid_info.grid(row=r, column=0, columnspan=3, sticky="w",
                                padx=4, pady=(4, 2)); r += 1
        ttk.Button(gq, text="Метки строк — проверить совпадение…",
                   command=self.grid_marks).grid(row=r, column=0, columnspan=3,
                                                 sticky="w", padx=4, pady=(2, 6))

        self.lbl_fit = ttk.Label(right, text="", style="Error.TLabel",
                                 justify="left", wraplength=540)
        self.lbl_fit.pack(anchor="w", padx=10, pady=6)

    # ------------------------------------------------------ вкладка Реализм
    def _build_human(self):
        f = self.tab_human
        box, self.hpanel, wrap = self._scroll_panel(f, width=1060)
        box.pack(fill="both", expand=True, padx=10, pady=6)

        top = ttk.Frame(wrap)
        top.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(top, text="готовый набор:").pack(side="left", padx=(4, 6))
        self.v_preset = tk.StringVar(value="Обычно")
        ttk.Combobox(top, textvariable=self.v_preset, state="readonly",
                     values=list(HUMAN_PRESETS), width=26).pack(side="left")
        ttk.Button(top, text="Применить",
                   command=self.apply_preset).pack(side="left", padx=6)
        InfoIcon(self, top, HELP["preset"]).pack(side="left", padx=2)

        g = ttk.LabelFrame(wrap, text="Разнобой букв")
        g.grid(row=1, column=0, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g, r, "включить реализм", "human", "enabled", "bool"); r += 1
        Field(self, g, r, "зерно случайности", "human", "seed", "int",
              tip="0 — каждый раз по-новому"); r += 1
        Field(self, g, r, "смешивать варианты", "human", "variant_mix",
              "scale", 0, 1); r += 1
        Field(self, g, r, "смещение буквы", "human", "jitter_pos", "scale", 0, 0.5); r += 1
        Field(self, g, r, "разброс размера", "human", "jitter_size", "scale", 0, 0.3); r += 1
        Field(self, g, r, "разброс поворота, °", "human", "jitter_rot",
              "scale", 0, 10); r += 1
        Field(self, g, r, "наклон, °", "human", "slant", "scale", -15, 25); r += 1
        Field(self, g, r, "разброс наклона, °", "human", "slant_jitter",
              "scale", 0, 8); r += 1

        g2 = ttk.LabelFrame(wrap, text="Дрожание и строка")
        g2.grid(row=1, column=1, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g2, r, "дрожание пера", "human", "tremor", "scale", 0, 0.06,
              tip="норма 0.005…0.045"); r += 1
        Field(self, g2, r, "длина волны дрожания", "human", "tremor_scale",
              "scale", 0.3, 2.0); r += 1
        Field(self, g2, r, "волна базовой линии", "human", "baseline_wave",
              "scale", 0, 0.35); r += 1
        Field(self, g2, r, "снос строки", "human", "baseline_drift",
              "scale", 0, 0.3); r += 1
        Field(self, g2, r, "разброс пробелов", "human", "space_jitter",
              "scale", 0, 0.4); r += 1
        Field(self, g2, r, "съезжание к концу строки", "human", "word_slope",
              "scale", 0, 0.2); r += 1

        g3 = ttk.LabelFrame(wrap, text="Ошибки и помарки")
        g3.grid(row=2, column=0, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g3, r, "доля зачёркнутых слов", "human", "strike_rate",
              "scale", 0, 0.4, tip="слово пишется, черкается и пишется заново"); r += 1
        Field(self, g3, r, "доля описок", "human", "typo_rate", "scale", 0, 0.4,
              tip="зачёркнутая копия написана с ошибкой"); r += 1
        Field(self, g3, r, "как зачёркивать", "human", "strike_style", "str",
              values={"line": "чертой", "zigzag": "зигзагом", "scribble": "каракулями"},
              width=10); r += 1
        Field(self, g3, r, "доля клякс", "human", "blot_rate", "scale", 0, 0.15); r += 1
        Field(self, g3, r, "доля непрописанных штрихов", "human", "skip_rate",
              "scale", 0, 0.15); r += 1

        g4 = ttk.LabelFrame(wrap, text="Нажим (через высоту пера)")
        g4.grid(row=2, column=1, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g4, r, "амплитуда, мм", "human", "pressure", "scale", 0, 0.4,
              tip="0 — выключено"); r += 1
        Field(self, g4, r, "длина волны, мм", "human", "pressure_scale",
              "scale", 0.5, 20); r += 1

        g5 = ttk.LabelFrame(wrap, text="Каллиграфия (толстые и тонкие линии)")
        g5.grid(row=3, column=0, columnspan=2, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g5, r, "перо", "human", "calli_style", "str",
              values=HM.CALLI_STYLES, width=12,
              tip="широкое — как плакатное; острое — нажим на нисходящих"); r += 1
        Field(self, g5, r, "ширина пера / нажим, мм", "human", "calli_width",
              "scale", 0.3, 3.0, tip="для букв 6 мм хорошо 0.8…1.2"); r += 1
        Field(self, g5, r, "угол широкого пера, °", "human", "calli_angle",
              "scale", 0, 90, tip="классика — 30…45"); r += 1
        Field(self, g5, r, "толщина стержня, мм", "human", "calli_pen",
              "scale", 0.1, 1.0, tip="меньше — больше проходов, линия плотнее"); r += 1
        ttk.Button(g5, text="Каллиграфический почерк (пропись) — включить всё",
                   command=self.use_calligraphy).grid(row=r, column=0, columnspan=3,
                                                      sticky="w", padx=4, pady=(4, 6))

    # ------------------------------------------------------- вкладка Печать
    def _build_print(self):
        f = self.tab_print
        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        wrap.columnconfigure(2, weight=1)

        g = ttk.LabelFrame(wrap, text="Перо")
        g.grid(row=0, column=0, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g, r, "Z письма, мм", "pen", "z_draw",
              tip="высота, на которой перо касается бумаги"); r += 1
        Field(self, g, r, "подъём на холостых, мм", "pen", "z_lift",
              tip="над бумагой: перо едет на «Z письма» + это"); r += 1
        Field(self, g, r, "малый подъём, мм", "pen", "z_travel_min",
              tip="тоже над бумагой, между близкими штрихами"); r += 1
        Field(self, g, r, "порог малого подъёма, мм", "pen", "hop_threshold"); r += 1
        Field(self, g, r, "подача письма, мм/мин", "pen", "feed_draw", "int"); r += 1
        Field(self, g, r, "подача холостых", "pen", "feed_travel", "int"); r += 1
        Field(self, g, r, "подача по Z", "pen", "feed_z", "int"); r += 1
        Field(self, g, r, "перо на сервоприводе", "pen", "use_servo", "bool",
              tip="на Neo порт занят CR Touch"); r += 1

        g2 = ttk.LabelFrame(wrap, text="Станок")
        g2.grid(row=0, column=1, sticky="nw", padx=6, pady=6)
        r = 0
        Field(self, g2, r, "стол X, мм", "machine", "bed_x"); r += 1
        Field(self, g2, r, "стол Y, мм", "machine", "bed_y"); r += 1
        Field(self, g2, r, "парковка", "machine", "home_mode", "str",
              values=["xy", "all", "none"], width=8,
              tip="xy — безопасно: Z не трогаем"); r += 1
        Field(self, g2, r, "карта стола", "machine", "level_mode", "str",
              values=["none", "m420", "g29"], width=8); r += 1
        Field(self, g2, r, "Z после парковки", "machine", "z_after_home",
              tip="на этой высоте перо едет к первой букве"); r += 1
        Field(self, g2, r, "разрешить Z ниже нуля", "machine",
              "disable_soft_endstops", "bool", tip="M211 S0"); r += 1
        Field(self, g2, r, "выключать моторы в конце", "machine",
              "motors_off_at_end", "bool"); r += 1
        Field(self, g2, r, "пищать в конце", "machine", "beep_at_end", "bool"); r += 1

        g3 = ttk.LabelFrame(wrap, text="Связь по USB")
        g3.grid(row=1, column=0, columnspan=3, sticky="nwe", padx=6, pady=6)
        row = ttk.Frame(g3)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Label(row, text="порт:").pack(side="left")
        self.v_port = tk.StringVar()
        self.cb_port = ttk.Combobox(row, textvariable=self.v_port, width=34,
                                    state="readonly")
        self.cb_port.pack(side="left", padx=4)
        InfoIcon(self, row, HELP["port"]).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Обновить", command=self.refresh_ports).pack(side="left")
        ttk.Label(row, text="скорость:").pack(side="left", padx=(12, 2))
        self.v_baud = tk.StringVar(value="115200")
        ttk.Combobox(row, textvariable=self.v_baud, width=8, state="readonly",
                     values=["115200", "250000", "57600", "9600"]).pack(side="left")
        InfoIcon(self, row, HELP["baud"]).pack(side="left", padx=4)

        row2 = ttk.Frame(g3)
        row2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(row2, text="Проверить связь",
                   command=self.test_link).pack(side="left")
        ttk.Button(row2, text="Лесенка Z (калибровка)",
                   command=self.run_calibration).pack(side="left", padx=6)
        ttk.Button(row2, text="Граница (перо поднято)",
                   command=self.do_frame).pack(side="left", padx=(16, 4))
        ttk.Button(row2, text="Отправить на печать",
                   command=self.do_print).pack(side="left")
        self.btn_pause = ttk.Button(row2, text="Пауза", command=self.do_pause,
                                    state="disabled")
        self.btn_pause.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(row2, text="Стоп", command=self.do_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left")

        jog = ttk.LabelFrame(wrap, text="Подвинуть перо (подбор Z)")
        jog.grid(row=0, column=2, sticky="nw", padx=6, pady=6)
        jr = ttk.Frame(jog)
        jr.pack(padx=6, pady=6)
        for i, d in enumerate((1.0, 0.1, -0.1, -1.0)):
            ttk.Button(jr, text="Z %+.1f" % d, width=6, style="Small.TButton",
                       command=lambda dd=d: self.jog_z(dd)).grid(row=i // 2, column=i % 2,
                                                                  padx=2, pady=2)
        ttk.Button(jog, text="Записать текущий Z как «Z письма»",
                   command=self.grab_z).pack(fill="x", padx=6, pady=(0, 6))

        self.prog = ttk.Progressbar(wrap, mode="determinate")
        self.prog.grid(row=2, column=0, columnspan=3, sticky="new", padx=6, pady=(8, 4))
        self.log = tk.Text(wrap, height=10, font=("Consolas", 9), wrap="none")
        self.log.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=6)
        wrap.rowconfigure(3, weight=1)
        self.refresh_ports()

    # ================================================ правка и буфер обмена
    def _install_edit_keys(self):
        self.bind_all("<Control-KeyPress>", self._ctrl_key, add="+")
        for cls in ("Text", "TEntry", "TSpinbox", "TCombobox"):
            self.bind_class(cls, "<Button-3>", self._edit_menu, add="+")

    @staticmethod
    def _editable(w):
        return isinstance(w, (tk.Text, tk.Entry, ttk.Entry))

    def _ctrl_key(self, e):
        w = e.widget
        key = (e.keysym or "").lower()
        if e.keycode == 83 and key != "s":          # Ctrl+S в русской раскладке
            self.save_gcode()
            return "break"
        if not self._editable(w):
            if self._on_draw_tab():
                act = {90: self.sketch_undo, 65: self.sel_all,     # Z, A
                       67: self.sel_copy, 86: self.sel_paste}.get(e.keycode)
                if act:                                           # C, V
                    act()
                    return "break"
            return None
        if e.keycode == 65:                         # Ctrl+A — выделить всё
            self._select_all(w)
            return "break"
        ev = _VK_EVENTS.get(e.keycode)
        # в латинской раскладке стандартные привязки Tk уже сработали —
        # повторять нельзя, иначе вставка произойдёт дважды
        if ev and key not in _LATIN_KEYS:
            w.event_generate(ev)
            return "break"
        return None

    @staticmethod
    def _select_all(w):
        if isinstance(w, tk.Text):
            w.tag_add("sel", "1.0", "end-1c")
            w.mark_set("insert", "end-1c")
            w.see("insert")
        else:
            w.select_range(0, "end")
            w.icursor("end")

    def _menu(self):
        C = self.C
        m = tk.Menu(self, tearoff=0)
        if C.get("bg"):
            m.configure(background=C["panel"], foreground=C["text"],
                        activebackground=C["select"],
                        activeforeground=C["select_fg"],
                        disabledforeground=C["muted"], borderwidth=1)
        return m

    def _edit_menu(self, e):
        w = e.widget
        if not self._editable(w):
            return None
        try:
            w.focus_set()
        except tk.TclError:
            pass
        ro = str(w.cget("state")) in ("readonly", "disabled")
        st = "disabled" if ro else "normal"
        m = self._menu()
        m.add_command(label="Вырезать", accelerator="Ctrl+X", state=st,
                      command=lambda: w.event_generate("<<Cut>>"))
        m.add_command(label="Копировать", accelerator="Ctrl+C",
                      command=lambda: w.event_generate("<<Copy>>"))
        m.add_command(label="Вставить", accelerator="Ctrl+V", state=st,
                      command=lambda: w.event_generate("<<Paste>>"))
        m.add_separator()
        m.add_command(label="Выделить всё", accelerator="Ctrl+A",
                      command=lambda: self._select_all(w))
        if isinstance(w, tk.Text) and not ro:
            m.add_separator()
            m.add_command(label="Отменить", accelerator="Ctrl+Z",
                          command=lambda: w.event_generate("<<Undo>>"))
            m.add_command(label="Повторить", accelerator="Ctrl+Y",
                          command=lambda: w.event_generate("<<Redo>>"))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()
        return "break"

    # ================================================================ тема
    def _initial_dark(self):
        """Тема с прошлого запуска, а в первый раз — как в Windows."""
        try:
            with open(self._state_file(), encoding="utf-8") as f:
                t = json.load(f).get("theme")
            if t in ("dark", "light"):
                return t == "dark"
        except (OSError, ValueError, AttributeError):
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion"
                                r"\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except (OSError, ImportError):
            return False

    def apply_theme(self):
        dark = bool(self.v_dark.get())
        C = self.C = THEMES["dark" if dark else "light"]
        st = ttk.Style(self)
        st.theme_use("clam")
        f_ui = (UI_FONT, 10)
        st.configure(".", background=C["bg"], foreground=C["text"], font=f_ui,
                     fieldbackground=C["field"], bordercolor=C["border"],
                     lightcolor=C["bg"], darkcolor=C["bg"],
                     troughcolor=C["track"], selectbackground=C["select"],
                     selectforeground=C["select_fg"], insertcolor=C["insert"],
                     arrowcolor=C["muted"], focuscolor=C["accent"])
        st.map(".", foreground=[("disabled", C["muted"])],
               background=[("disabled", C["bg"])])
        st.configure("TFrame", background=C["bg"])
        st.configure("TLabel", background=C["bg"], foreground=C["text"])
        st.configure("Hint.TLabel", foreground=C["muted"], font=(UI_FONT, 9))
        st.configure("Error.TLabel", foreground=C["error"])
        st.configure("Title.TLabel", foreground=C["heading"],
                     font=(UI_FONT, 18, "bold"))
        st.configure("Status.TLabel", background=C["side"], foreground=C["muted"],
                     font=(UI_FONT, 9))
        st.configure("StatusErr.TLabel", background=C["side"],
                     foreground=C["error"], font=(UI_FONT, 9))

        # кнопки: спокойные серые и одна акцентная — «Печать»
        st.configure("TButton", background=C["btn"], foreground=C["text"],
                     bordercolor=C["border"], lightcolor=C["btn"],
                     darkcolor=C["btn"], padding=(12, 5), relief="flat",
                     focuscolor=C["btn"])
        st.map("TButton",
               background=[("disabled", C["bg"]), ("pressed", C["btn_press"]),
                           ("active", C["btn_hover"])],
               lightcolor=[("pressed", C["btn_press"]), ("active", C["btn_hover"])],
               darkcolor=[("pressed", C["btn_press"]), ("active", C["btn_hover"])],
               bordercolor=[("focus", C["accent"])])
        st.configure("Small.TButton", padding=(6, 3))
        st.configure("Accent.TButton", background=C["accent"],
                     foreground=C["accent_fg"], bordercolor=C["accent"],
                     lightcolor=C["accent"], darkcolor=C["accent"],
                     font=(UI_FONT, 10, "bold"), focuscolor=C["accent"])
        st.map("Accent.TButton",
               background=[("pressed", C["accent_hover"]), ("active", C["accent_hover"])],
               lightcolor=[("active", C["accent_hover"])],
               darkcolor=[("active", C["accent_hover"])],
               bordercolor=[("active", C["accent_hover"])],
               foreground=[("disabled", C["muted"])])

        # группы настроек — «карточки»: тонкая рамка и жирный заголовок
        st.configure("TLabelframe", background=C["bg"], bordercolor=C["border"],
                     lightcolor=C["border"], darkcolor=C["border"],
                     relief="solid", borderwidth=1, padding=(10, 6, 10, 10))
        st.configure("TLabelframe.Label", background=C["bg"],
                     foreground=C["heading"], font=(UI_FONT, 10, "bold"))

        for w in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(w, fieldbackground=C["field"], foreground=C["field_fg"],
                         background=C["btn"], bordercolor=C["border"],
                         lightcolor=C["field"], darkcolor=C["field"],
                         padding=(6, 3), arrowcolor=C["muted"])
            st.map(w, bordercolor=[("focus", C["accent"]), ("hover", C["muted"])],
                   lightcolor=[("focus", C["field"])])
        st.map("TCombobox",
               fieldbackground=[("readonly", C["field"])],
               foreground=[("readonly", C["field_fg"])],
               selectbackground=[("readonly", C["field"])],
               selectforeground=[("readonly", C["field_fg"])],
               bordercolor=[("focus", C["accent"]), ("hover", C["muted"])])
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=C["bg"], foreground=C["text"],
                         indicatorbackground=C["field"],
                         indicatorforeground=C["accent_fg"],
                         bordercolor=C["muted"], indicatormargin=(0, 0, 6, 0),
                         focuscolor=C["bg"])
            st.map(w, background=[("active", C["bg"])],
                   indicatorbackground=[("selected", C["accent"]),
                                        ("pressed", C["btn_press"])],
                   bordercolor=[("selected", C["accent"])])
        st.configure("Side.TCheckbutton", background=C["side"],
                     foreground=C["side_fg"], focuscolor=C["side"])
        st.map("Side.TCheckbutton", background=[("active", C["side"])])

        st.configure("Horizontal.TScale", background=C["bg"],
                     troughcolor=C["bg"], bordercolor=C["bg"],
                     lightcolor=C["bg"], darkcolor=C["bg"], borderwidth=0)
        st.map("Horizontal.TScale", background=[("active", C["bg"])])
        st.configure("Horizontal.TProgressbar", background=C["accent"],
                     troughcolor=C["track"], bordercolor=C["track"],
                     lightcolor=C["accent"], darkcolor=C["accent"], thickness=8)
        for o in ("Vertical", "Horizontal"):
            st.configure("%s.TScrollbar" % o, background=C["track"],
                         troughcolor=C["bg"], bordercolor=C["bg"],
                         lightcolor=C["track"], darkcolor=C["track"],
                         arrowcolor=C["muted"], gripcount=0, relief="flat",
                         arrowsize=12)
            st.map("%s.TScrollbar" % o,
                   background=[("active", C["muted"]), ("pressed", C["muted"])])

        self._make_elements(st, C, "dark" if dark else "light")

        # страницы без ярлыков: разделы переключаются боковым меню
        st.layout("Pages.TNotebook.Tab", [])
        st.configure("Pages.TNotebook", background=C["bg"], borderwidth=0,
                     tabmargins=0, bordercolor=C["bg"], lightcolor=C["bg"],
                     darkcolor=C["bg"])

        bg = C["bg"]
        self.configure(background=bg)
        for w in (self.side, self.side_title, self.side_sub):
            w.configure(background=C["side"])
        self.side_title.configure(foreground=C["heading"])
        self.side_sub.configure(foreground=C["muted"])
        self.statusbar.configure(background=C["side"])
        self.h_line.configure(background=C["border"])
        self._paint_nav()

        for t in (self.txt, self.log):
            t.configure(background=C["field"], foreground=C["field_fg"],
                        insertbackground=C["insert"],
                        selectbackground=C["select"],
                        selectforeground=C["select_fg"])
        self.txt_box.configure(background=C["field"])
        try:
            focused = self.focus_get() is self.txt
        except (KeyError, tk.TclError):       # фокус во всплывающем списке
            focused = False
        self._txt_focus(focused)
        self.log.configure(highlightthickness=1, highlightbackground=C["border"],
                           highlightcolor=C["accent"], borderwidth=0,
                           padx=8, pady=6)
        self.canvas.configure(background=C["canvas"])
        self.dcanvas.configure(background=C["canvas"])
        self.gcanvas.configure(background=C["tiles"])
        for pnl in (self.dpanel, self.ppanel, self.hpanel, self.handpanel):
            pnl.configure(background=bg)
        for ic in getattr(self, "_infos", []):
            ic.paint()

        # выпадающие списки: и будущие, и уже открывавшиеся
        for k, v in (("background", C["field"]), ("foreground", C["field_fg"]),
                     ("selectBackground", C["select"]),
                     ("selectForeground", C["select_fg"]),
                     ("font", "{%s} 10" % UI_FONT)):
            self.option_add("*TCombobox*Listbox." + k, v)
        for cb in self._walk(self):
            if isinstance(cb, ttk.Combobox):
                try:
                    pd = self.tk.call("ttk::combobox::PopdownWindow", cb)
                    self.tk.call(str(pd) + ".f.l", "configure",
                                 "-background", C["field"],
                                 "-foreground", C["field_fg"],
                                 "-selectbackground", C["select"],
                                 "-selectforeground", C["select_fg"])
                except tk.TclError:
                    pass
        if self.pages:
            self.redraw()
        self._titlebar(dark)

    def _make_elements(self, st, C, key):
        """
        Галочки, переключатели и ползунки — картинками: у «clam» галочка
        рисуется крестиком, а ползунок толстой плашкой. Картинки рисуются
        с 4-кратным запасом и уменьшаются — края выходят гладкими.
        Элементы создаются один раз на тему; при смене темы меняется
        только раскладка стилей.
        """
        from PIL import Image as _Im, ImageDraw as _Dr
        self._elem_imgs = getattr(self, "_elem_imgs", {})
        pre = "hw%s" % key
        if key not in self._elem_imgs:
            def pic(w, h, fn):
                k = 4
                im = _Im.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
                fn(_Dr.Draw(im), k)
                return ImageTk.PhotoImage(im.resize((w, h), _Im.LANCZOS), master=self)

            def box(fill, edge):
                return lambda d, k: d.rounded_rectangle(
                    [1 * k, 1 * k, 17 * k, 17 * k], radius=4 * k, fill=fill,
                    outline=edge, width=int(1.5 * k))

            def check(d, k):
                box(C["accent"], C["accent"])(d, k)
                d.line([(5 * k, 9.5 * k), (8 * k, 12.5 * k), (13.5 * k, 6 * k)],
                       fill=C["accent_fg"], width=int(2.2 * k), joint="curve")

            def ring(fill, edge, dot=None):
                def f(d, k):
                    d.ellipse([1 * k, 1 * k, 17 * k, 17 * k], fill=fill, outline=edge,
                              width=int(1.5 * k))
                    if dot:
                        d.ellipse([6 * k, 6 * k, 12 * k, 12 * k], fill=dot)
                return f

            imgs = {
                "cb_off": pic(18, 18, box(C["field"], C["muted"])),
                "cb_hover": pic(18, 18, box(C["field"], C["accent"])),
                "cb_on": pic(18, 18, check),
                "cb_dis": pic(18, 18, box(C["bg"], C["border"])),
                "rb_off": pic(18, 18, ring(C["field"], C["muted"])),
                "rb_hover": pic(18, 18, ring(C["field"], C["accent"])),
                "rb_on": pic(18, 18, ring(C["accent"], C["accent"], C["accent_fg"])),
                "tr": pic(24, 18, lambda d, k: d.rounded_rectangle(
                    [2 * k, 7 * k, 22 * k, 11 * k], radius=2 * k, fill=C["track"])),
                "kn": pic(18, 18, lambda d, k: d.ellipse(
                    [1.5 * k, 1.5 * k, 16.5 * k, 16.5 * k], fill=C["field"],
                    outline=C["accent"], width=int(2.5 * k))),
                "kn_act": pic(18, 18, lambda d, k: d.ellipse(
                    [1.5 * k, 1.5 * k, 16.5 * k, 16.5 * k], fill=C["accent"],
                    outline=C["accent"], width=int(2.5 * k))),
            }
            self._elem_imgs[key] = imgs
            st.element_create(pre + ".Checkbutton.indicator", "image", imgs["cb_off"],
                              ("disabled", imgs["cb_dis"]), ("selected", imgs["cb_on"]),
                              ("active", imgs["cb_hover"]), sticky="w", width=26)
            st.element_create(pre + ".Radiobutton.indicator", "image", imgs["rb_off"],
                              ("disabled", imgs["cb_dis"]), ("selected", imgs["rb_on"]),
                              ("active", imgs["rb_hover"]), sticky="w", width=26)
            st.element_create(pre + ".Horizontal.Scale.trough", "image", imgs["tr"],
                              border=(8, 0, 8, 0), sticky="ew")
            st.element_create(pre + ".Horizontal.Scale.slider", "image", imgs["kn"],
                              ("pressed", imgs["kn_act"]), ("active", imgs["kn_act"]),
                              sticky="")
        for cls in ("Checkbutton", "Radiobutton"):
            layout = [("%s.padding" % cls, {"sticky": "nswe", "children": [
                ("%s.%s.indicator" % (pre, cls), {"side": "left", "sticky": "w"}),
                ("%s.focus" % cls, {"side": "left", "sticky": "w", "children": [
                    ("%s.label" % cls, {"sticky": "nswe"})]})]})]
            st.layout("T" + cls, layout)
            if cls == "Checkbutton":
                st.layout("Side.TCheckbutton", layout)
        st.layout("Horizontal.TScale", [
            (pre + ".Horizontal.Scale.trough", {"sticky": "ew", "children": [
                (pre + ".Horizontal.Scale.slider", {"side": "left", "sticky": ""})]})])

    def _walk(self, w):
        for c in w.winfo_children():
            yield c
            yield from self._walk(c)

    def _titlebar(self, dark):
        """Тёмная рамка окна Windows 10/11 — иначе заголовок остаётся белым."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):       # DWMWA_USE_IMMERSIVE_DARK_MODE (новый/старый)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
            # перерисовать рамку сразу: NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)
        except Exception:                               # noqa: BLE001
            pass

    # ================================================== реакция на правки
    def on_change(self, defer=False):
        if self._defer is not None:
            self.after_cancel(self._defer)
        self._defer = self.after(320 if defer else 60, self.rebuild)

    def _collect(self):
        for f in self.fields:
            f.push()
        self.cfg.page.rotate = float(self.cfg.page.rotate)
        self.fontset.builtin_fallback = bool(self.v_fallback.get())
        self.fontset.flat = self.cfg.curve_flatness

    def rebuild(self):
        self._defer = None
        try:
            self._collect()
            text = self.txt.get("1.0", "end-1c")
            self.human = Human(self.cfg.human)
            self._report = {}
            self.pages = self._layout(text)
            self.page_index = min(self.page_index, len(self.pages) - 1)
            st = L.stats(self.pages)
            res = GC.generate(self.pages, self.cfg, self.human,
                              page_index=self.page_index)
            self._say("страниц %d · строк %d · %s%s"
                      % (st["pages"], st["lines"], res.summary(),
                         self._art_note))
            self._fit_note(res)
            self.redraw()
        except Exception as e:                          # noqa: BLE001
            self._say("ошибка: %s" % e, error=True)
            traceback.print_exc()
        self._autosave()

    def _layout(self, text):
        """Текст и рисунок -> страницы. Рисунок кладётся в Page.art."""
        d = self.cfg.draw
        art = []
        self._art_note = ""
        box = None
        if d.enabled and (self._art is not None or self._sketch):
            art, box = self._build_art_cached(d)
        mode = d.layout if art else "over"
        if mode == "alone":
            pages = [L.Page(size_mm=self.cfg.page.size_mm)]
        else:
            # место резервируется под картинку; эскиз — свободное рисование
            # поверх листа, и лишь без картинки текст уходит под него
            keep = [[(box[0], box[1]), (box[2], box[3])]] if box else art
            skip = DR.text_skip(keep, self.cfg.page, d.gap_mm) if mode == "above" else 0.0
            pages = L.build_pages(text, self.fontset, self.cfg.page, self.human,
                                  report=self._report, first_skip=skip)
        self._art_page = 0
        if art:
            idx = max(0, int(d.page_no) - 1) if mode == "over" else 0
            while len(pages) <= idx:
                pages.append(L.Page(size_mm=pages[-1].size_mm))
            pages[idx].art = art
            self._art_page = idx
            self._art_note = "   ·   рисунок: линий %d%s" % (
                len(art), ", текст не пишется" if mode == "alone" else "")
        elif d.enabled is False and (self._art is not None or self._sketch):
            self._art_note = "   ·   рисунок выключен"
        return pages

    def _build_art_cached(self, d):
        """
        DR.build_art с памятью на последний вызов: правка текста не трогает
        рисунок, а упрощение, дрожание и порядок линий на большой картинке
        занимают заметное время на каждое нажатие клавиши.
        """
        seed = self.cfg.human.seed or 1
        key = (astuple(d), astuple(self.cfg.page), seed,
               tuple(tuple(map(tuple, st)) for st in self._sketch))
        hit = getattr(self, "_art_memo", None)
        if hit is not None and hit[0] is self._art and hit[1] == key:
            return hit[2]
        res = DR.build_art(self._art, self._sketch, d, self.cfg.page, seed=seed)
        self._art_memo = (self._art, key, res)
        return res

    def _fit_note(self, res):
        m, pg = self.cfg.machine, self.cfg.page
        w, h = GC.sheet_extent(pg)
        msgs = []
        if pg.origin_x + w > m.bed_x + 1e-6 or pg.origin_y + h > m.bed_y + 1e-6:
            msgs.append("Лист %.0f×%.0f мм при таком положении не помещается на "
                        "стол %.0f×%.0f. Уменьшите лист, поверните его или "
                        "сдвиньте начало." % (w, h, m.bed_x, m.bed_y))
        rep = getattr(self, "_report", {}) or {}
        if rep.get("autofit_ok") is False:
            msgs.append(
                "Автоподбор: текст не помещается на страницу даже при "
                "минимальном размере %.1f мм — получилось %d страниц. "
                "Уменьшите минимальный размер, увеличьте лист или "
                "сократите текст."
                % (rep.get("autofit_min", 0.0), rep.get("pages", 0)))
        if hasattr(self, "lbl_grid_info"):
            if pg.grid:
                step = L.grid_step(pg)
                g = L.grid_cfg(pg)
                self.lbl_grid_info.configure(
                    text="строки через %.1f мм, заглавная %.1f мм,\n"
                         "на листе строк: %d. Размер шрифта и межстрочный\n"
                         "в этом режиме подбираются сами."
                         % (step, g.size_mm, len(L.grid_baselines(pg))))
            else:
                self.lbl_grid_info.configure(text="")
        k = rep.get("angle_scale", 1.0)
        if k < 0.999:
            msgs.append("Повёрнутый текст ужат до %.0f%%, чтобы остаться в полях "
                        "(высота букв %.1f мм)." % (k * 100, pg.size_mm * k))
        msgs.extend(res.warnings)
        if hasattr(self, "lbl_fit"):
            self.lbl_fit.configure(text="\n".join(msgs))

    def turn(self, d):
        if not self.pages:
            return
        self.page_index = max(0, min(len(self.pages) - 1, self.page_index + d))
        self.rebuild()

    def redraw(self):
        if not self.pages:
            return
        c = self.canvas
        cw, ch = max(50, c.winfo_width()), max(50, c.winfo_height())
        pg = self.cfg.page
        scale = min((cw - 40) / max(pg.sheet_w, 1), (ch - 40) / max(pg.sheet_h, 1))
        scale = max(0.6, scale)
        img = PV.to_image(self.pages[self.page_index], pg, px_per_mm=scale,
                          show_travel=self.v_travel.get(),
                          show_margins=self.v_margins.get(),
                          art_width=self.cfg.draw.pen_width,
                          bg=self.C["canvas"])
        self._photo = ImageTk.PhotoImage(img, master=self)
        c.delete("all")
        self._sheet_shadow(c, cw, ch, img.size)
        c.create_image(cw // 2, ch // 2, image=self._photo)
        self.lbl_page.configure(text="лист %d из %d"
                                     % (self.page_index + 1, len(self.pages)))
        self.redraw_draw()
        self._bed_photo = ImageTk.PhotoImage(
            PV.bed_image(self.pages[self.page_index], self.cfg), master=self)
        self.lbl_bed.configure(image=self._bed_photo)

    def _sheet_shadow(self, c, cw, ch, size):
        """Мягкая тень под листом: лист «лежит» на подложке."""
        W, H = size
        x0, y0 = cw // 2 - W / 2.0, ch // 2 - H / 2.0
        C = self.C
        for d, col in ((6, C["canvas"]), (4, C["shadow"]), (2, C["shadow"])):
            c.create_rectangle(x0 + d - 1, y0 + d, x0 + W + d, y0 + H + d,
                               fill=col, outline="")

    # ============================================================ файлы
    def open_text(self):
        p = filedialog.askopenfilename(
            title="Открыть текст",
            filetypes=[("Текст", "*.txt *.md"), ("Все файлы", "*.*")])
        if not p:
            return
        data = None
        for enc in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                data = open(p, encoding=enc).read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if data is None:
            messagebox.showerror("Не открылось", "Не удалось определить кодировку.")
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", data)
        self.rebuild()

    def save_gcode(self):
        if not self.pages:
            return
        self._collect()
        p = filedialog.asksaveasfilename(defaultextension=".gcode",
                                         filetypes=[("G-code", "*.gcode")])
        if not p:
            return
        res = GC.generate(self.pages, self.cfg, self.human,
                          page_index=self.page_index,
                          title=os.path.basename(p))
        open(p, "w", encoding="utf-8").write(res.text)
        msg = res.summary()
        if res.warnings:
            msg += "\n\nВнимание:\n" + "\n".join(res.warnings)
        messagebox.showinfo("Сохранено", "%s\n\n%s" % (p, msg))

    def save_svg(self):
        if not self.pages:
            return
        p = filedialog.asksaveasfilename(defaultextension=".svg",
                                         filetypes=[("SVG", "*.svg")])
        if p:
            PV.save_svg(p, self.pages, self.cfg.page,
                        show_travel=self.v_travel.get(),
                        art_width=self.cfg.draw.pen_width)

    def save_png(self):
        if not self.pages:
            return
        p = filedialog.asksaveasfilename(defaultextension=".png",
                                         filetypes=[("PNG", "*.png")])
        if p:
            PV.save_png(p, self.pages[self.page_index], self.cfg.page,
                        px_per_mm=12.0, show_travel=self.v_travel.get(),
                        art_width=self.cfg.draw.pen_width)

    def profile_menu(self):
        m = self._menu()
        m.add_command(label="Сохранить профиль…", command=self.save_profile)
        m.add_command(label="Загрузить профиль…", command=self.load_profile)
        m.add_separator()
        m.add_command(label="Сбросить настройки…", command=self.reset_settings)
        try:
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            m.grab_release()

    def save_profile(self):
        self._collect()
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("Профиль", "*.json")])
        if p:
            self.cfg.save(p)
            self._profile_path = p
            messagebox.showinfo("Сохранено", p)

    def load_profile(self):
        p = filedialog.askopenfilename(filetypes=[("Профиль", "*.json")])
        if not p:
            return
        self.cfg = Config.load(p)
        for f in self.fields:
            f.pull()
        self._profile_path = p
        if self.cfg.font_path and (self.cfg.font_path == FontSet.CALLIG
                                   or os.path.exists(self.cfg.font_path)):
            self._load_font_file(self.cfg.font_path)
        self.rebuild()

    def on_sheet_preset(self, _e=None):
        name = self.v_sheet.get()
        if name in SHEETS:
            w, h = SHEETS[name]
            self.cfg.page.sheet_w, self.cfg.page.sheet_h = w, h
            for f in self.fields:
                if f.section == "page" and f.attr in ("sheet_w", "sheet_h"):
                    f.pull()
            self.rebuild()

    def apply_preset(self):
        name = self.v_preset.get()
        if name not in HUMAN_PRESETS:
            return
        keep_seed = self.cfg.human.seed
        self.cfg.human = HUMAN_PRESETS[name]()
        self.cfg.human.seed = keep_seed
        for f in self.fields:
            if f.section == "human":
                f.pull()
        self.rebuild()

    # ======================================================== рисунок
    def _on_draw_tab(self):
        try:
            return self.nb.select() == str(self.tab_draw)
        except tk.TclError:
            return False

    def _tab_changed(self, _e=None):
        self._paint_nav()
        if self._on_draw_tab() and self.pages:
            # сохранение и печать с этой вкладки — именно листа с рисунком
            if self.page_index != self._art_page:
                self.page_index = min(self._art_page, len(self.pages) - 1)
                self.rebuild()
                return
            self.redraw_draw()

    def open_art(self):
        p = filedialog.askopenfilename(
            title="Картинка или SVG",
            filetypes=[("Картинки и SVG", "*.png *.jpg *.jpeg *.bmp *.gif "
                                          "*.tif *.tiff *.webp *.svg"),
                       ("Все файлы", "*.*")])
        if p and self._load_art(p):
            self.rebuild()

    def _load_art(self, path, quiet=False):
        """-> True, если файл прочитался."""
        try:
            self._art = DR.ArtSource(path)
        except Exception as e:                          # noqa: BLE001
            if not quiet:
                messagebox.showerror("Не открылось", str(e))
            return False
        self.lbl_art.configure(text=self._art.describe())
        return True

    def clear_art(self):
        self._art = None
        self.lbl_art.configure(text=NO_ART)
        self.rebuild()

    # ---- холст
    def redraw_draw(self):
        c = getattr(self, "dcanvas", None)
        if c is None or not self.pages:
            return
        cw, ch = max(50, c.winfo_width()), max(50, c.winfo_height())
        pg = self.cfg.page
        idx = min(self._art_page, len(self.pages) - 1)
        scale = min((cw - 40) / max(pg.sheet_w, 1), (ch - 40) / max(pg.sheet_h, 1))
        scale = max(0.6, scale)
        img = PV.to_image(self.pages[idx], pg, px_per_mm=scale,
                          show_margins=True, art_width=self.cfg.draw.pen_width,
                          bg=self.C["canvas"])
        self._dphoto = ImageTk.PhotoImage(img, master=self)
        c.delete("all")
        self._sheet_shadow(c, cw, ch, img.size)
        c.create_image(cw // 2, ch // 2, image=self._dphoto)
        W, H = img.size
        self._dmap = (cw // 2 - W / 2.0, ch // 2 - H / 2.0, scale, pg.sheet_h)
        self.lbl_dpage.configure(text="лист %d из %d · рисуйте мышью прямо на листе"
                                      % (idx + 1, len(self.pages)))
        self._draw_selection()

    def _to_mm(self, cx, cy):
        ox, oy, s, sh = self._dmap
        return ((cx - ox) / s, sh - (cy - oy) / s)

    def _to_px(self, pt):
        ox, oy, s, sh = self._dmap
        return (ox + pt[0] * s, oy + (sh - pt[1]) * s)

    def _tool_cursor(self):
        tool = self.v_tool.get()
        self.dcanvas.configure(cursor={"eraser": "X_cursor", "pen": "pencil",
                                       "select": "fleur"}.get(tool, "crosshair"))
        if tool != "select":
            self._sel_clear()

    # настройки картинки, которые меняет мышь: их тоже откатывает Ctrl+Z
    _PLACE = ("width_mm", "height_mm", "anchor", "offset_x", "offset_y",
              "rotate", "mirror")

    def _push_undo(self):
        d = self.cfg.draw
        self._sketch_undo.append(([list(st) for st in self._sketch],
                                  {k: getattr(d, k) for k in self._PLACE}))
        del self._sketch_undo[:-100]

    def _pull_draw(self):
        """cfg.draw -> поля; иначе rebuild() вернул бы старые значения из полей."""
        for f in self.fields:
            if f.section == "draw":
                f.pull()

    # ---- выделение: номера штрихов эскиза и/или картинка из файла
    SEL = "#2f7cf6"

    def _sel_clear(self):
        self._sel = set()
        self._sel_art = False
        if getattr(self, "dcanvas", None) is not None:
            self.dcanvas.delete("sel")

    def _sel_any(self):
        return bool(getattr(self, "_sel", None)) or getattr(self, "_sel_art", False)

    def _sel_outline(self):
        """Выделенное как ломаные в мм: штрихи эскиза и рамка картинки."""
        out = [self._sketch[i] for i in sorted(getattr(self, "_sel", ()))
               if i < len(self._sketch)]
        if getattr(self, "_sel_art", False) and self._art is not None:
            out.append(DR.art_corners(self._art, self.cfg.draw, self.cfg.page))
        return out

    def _sel_handles(self):
        """-> (рамка в px, центр в мм, [углы px], кружок поворота px) или None."""
        bb = DR.bbox_of(self._sel_outline())
        if bb is None or self._dmap is None:
            return None
        x0, y1 = self._to_px((bb[0], bb[1]))
        x1, y0 = self._to_px((bb[2], bb[3]))
        c = ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        return (x0, y0, x1, y1), c, corners, ((x0 + x1) / 2.0, y0 - 22)

    def _draw_selection(self, op=None):
        c = getattr(self, "dcanvas", None)
        if c is None or self._dmap is None:
            return
        c.delete("sel")
        if not self._sel_any():
            return
        lines = self._sel_outline()
        if op is not None:
            lines = DR.transform_points(lines, op)
        for st in lines:
            if len(st) > 1:
                c.create_line(*[v for q in st for v in self._to_px(q)],
                              fill=self.SEL, width=2, tags="sel")
        if op is not None:
            return
        h = self._sel_handles()
        if h is None:
            return
        (x0, y0, x1, y1), _c, corners, (rx, ry) = h
        c.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline=self.SEL,
                           dash=(4, 3), tags="sel")
        for (hx, hy) in corners:
            c.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5, fill="white",
                               outline=self.SEL, width=2, tags="sel")
        c.create_line((x0 + x1) / 2.0, y0 - 3, rx, ry + 6, fill=self.SEL, tags="sel")
        c.create_oval(rx - 6, ry - 6, rx + 6, ry + 6, fill="white",
                      outline=self.SEL, width=2, tags="sel")

    def _handle_at(self, x, y):
        if not self._sel_any():
            return None
        h = self._sel_handles()
        if h is None:
            return None
        (x0, y0, x1, y1), _c, corners, (rx, ry) = h
        if math.hypot(x - rx, y - ry) <= 9:
            return "rot"
        if any(abs(x - hx) <= 8 and abs(y - hy) <= 8 for hx, hy in corners):
            return "scale"
        if x0 - 4 <= x <= x1 + 4 and y0 - 4 <= y <= y1 + 4:
            return "move"
        return None

    def _hit(self, pt):
        """Что под мышью: ("stroke", номер), ("art",) или None."""
        tol = max(0.8, 7.0 / self._dmap[2])
        best, bi = tol, None
        for i, st in enumerate(self._sketch):
            dd = DR.dist_to_path(pt, st)
            if dd <= best:
                best, bi = dd, i
        if bi is not None:
            return ("stroke", bi)
        if self._art is not None and self.cfg.draw.enabled:
            box = DR.art_box(self._art, self.cfg.draw, self.cfg.page)
            if box and box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]:
                return ("art",)
        return None

    def _sel_add(self, hit):
        if hit[0] == "stroke":
            self._sel.add(hit[1])
        else:
            self._sel_art = True

    def _sel_has(self, hit):
        return (hit[1] in self._sel) if hit[0] == "stroke" else self._sel_art

    def _apply_op(self, op):
        """Применить преобразование к выделенному и пересобрать лист."""
        if not self._sel_any():
            return
        self._push_undo()
        idx = sorted(i for i in self._sel if i < len(self._sketch))
        if idx:
            moved = DR.transform_points([self._sketch[i] for i in idx], op)
            for i, st in zip(idx, moved):
                self._sketch[i] = st
        if self._sel_art and self._art is not None:
            DR.transform_art(self.cfg.draw, self._art, self.cfg.page, op)
            self._pull_draw()
        self.rebuild()

    def _sel_center(self):
        bb = DR.bbox_of(self._sel_outline())
        return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0) if bb else None

    def sel_all(self):
        self.v_tool.set("select")
        self._tool_cursor()
        self._sel = set(range(len(self._sketch)))
        self._sel_art = self._art is not None
        self._draw_selection()

    def sel_delete(self):
        if not self._sel_any():
            return
        if self._sel_art and self._art is not None:
            if not messagebox.askyesno("Удалить", "Убрать и картинку из файла?"):
                self._sel_art = False
        self._push_undo()
        self._sketch = [st for i, st in enumerate(self._sketch) if i not in self._sel]
        if self._sel_art:
            self._art = None
            self.lbl_art.configure(text=NO_ART)
        self._sel_clear()
        self.rebuild()

    def sel_apply(self, kind):
        """Кнопки: отразить, масштаб в %, поворот на угол."""
        if not self._sel_any():
            messagebox.showinfo("Ничего не выделено",
                                "Выберите «двигать» и щёлкните по рисунку, "
                                "или нажмите «Выделить всё».")
            return
        c = self._sel_center()
        try:
            if kind == "mirror":
                op = ("mirror", c[0])
            elif kind == "scale":
                k = float(self.v_sel_scale.get().replace(",", ".")) / 100.0
                if k <= 0:
                    raise ValueError
                op = ("scale", k, c[0], c[1])
            else:
                op = ("rot", float(self.v_sel_rot.get().replace(",", ".")), c[0], c[1])
        except ValueError:
            messagebox.showwarning("Не число", "Впишите число.")
            return
        self._apply_op(op)

    def _d_wheel(self, e):
        if not self._sel_any():
            return
        c = self._sel_center()
        self._apply_op(("scale", 1.1 if e.delta > 0 else 1 / 1.1, c[0], c[1]))

    def _sel_nudge(self, e, dx, dy):
        if not self._sel_any():
            return
        step = 5.0 if (e.state & 0x0001) else 1.0
        self._apply_op(("move", dx * step, dy * step))
        return "break"

    def sel_copy(self):
        self._clip = [list(self._sketch[i]) for i in sorted(self._sel)
                      if i < len(self._sketch)]

    def sel_paste(self):
        clip = getattr(self, "_clip", None)
        if not clip:
            return
        self._push_undo()
        clip = DR.transform_points(clip, ("move", 5.0, -5.0))
        self._clip = clip
        n = len(self._sketch)
        self._sketch.extend(clip)
        self.v_tool.set("select")
        self._sel = set(range(n, n + len(clip)))
        self._sel_art = False
        self.rebuild()

    # ---- мышь в режиме «двигать»
    def _s_press(self, e, pt):
        shift = bool(e.state & 0x0001)
        mode = self._handle_at(e.x, e.y)
        if mode is None:
            hit = self._hit(pt)
            if hit is not None:
                if not shift and not self._sel_has(hit):
                    self._sel_clear()
                self._sel_add(hit)
                mode = "move"
            else:
                if not shift:
                    self._sel_clear()
                mode = "band"
        self._drag.update(mode=mode, center=self._sel_center(), op=None)
        self._draw_selection()

    def _s_move(self, e, pt):
        d = self._drag
        c, st = d.get("center"), d["start"]
        mode = d["mode"]
        if mode == "band":
            self.dcanvas.delete("live")
            x0, y0 = self._to_px(st)
            self.dcanvas.create_rectangle(x0, y0, e.x, e.y, outline=self.SEL,
                                          dash=(3, 3), tags="live")
            return
        if c is None:
            return
        if mode == "move":
            op = ("move", pt[0] - st[0], pt[1] - st[1])
        elif mode == "scale":
            k = math.dist(c, pt) / max(math.dist(c, st), 1e-6)
            op = ("scale", max(0.02, k), c[0], c[1])
        else:
            ang = math.degrees(math.atan2(pt[1] - c[1], pt[0] - c[0])
                               - math.atan2(st[1] - c[1], st[0] - c[0]))
            if e.state & 0x0001:
                ang = round(ang / 15.0) * 15.0
            op = ("rot", ang, c[0], c[1])
        d["op"] = op
        self._draw_selection(op)

    def _s_release(self, e, pt):
        d = self._drag
        if d["mode"] == "band":
            self.dcanvas.delete("live")
            a, b = d["start"], pt
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            if x1 - x0 > 0.5 or y1 - y0 > 0.5:
                inside = lambda bb: (bb and x0 <= bb[0] and bb[2] <= x1
                                     and y0 <= bb[1] and bb[3] <= y1)
                for i, st in enumerate(self._sketch):
                    if inside(DR.bbox_of([st])):
                        self._sel.add(i)
                if self._art is not None and self.cfg.draw.enabled \
                        and inside(DR.art_box(self._art, self.cfg.draw, self.cfg.page)):
                    self._sel_art = True
            self._draw_selection()
            return
        op = d.get("op")
        trivial = op is None or (op[0] == "move" and math.hypot(op[1], op[2]) < 0.05) \
            or (op[0] == "scale" and abs(op[1] - 1) < 1e-3) \
            or (op[0] == "rot" and abs(op[1]) < 0.05)
        if trivial:
            self._draw_selection()
            return
        self._apply_op(op)

    def _shape(self, a, b, shift):
        """Линия, прямоугольник или эллипс по двум углам (мм)."""
        tool = self.v_tool.get()
        if tool == "line":
            if shift:                                   # через каждые 15°
                step = math.pi / 12
                ang = round(math.atan2(b[1] - a[1], b[0] - a[0]) / step) * step
                ln = math.dist(a, b)
                b = (a[0] + ln * math.cos(ang), a[1] + ln * math.sin(ang))
            return [a, b]
        if shift:                                       # квадрат / круг
            side = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            b = (a[0] + math.copysign(side, (b[0] - a[0]) or 1),
                 a[1] + math.copysign(side, (b[1] - a[1]) or 1))
        return DR.rect_path(a, b) if tool == "rect" else DR.ellipse_path(a, b)

    def _erase_at(self, pt):
        tol = max(0.8, 7.0 / self._dmap[2])
        hit = [st for st in self._sketch if DR.dist_to_path(pt, st) <= tol]
        if not hit:
            return
        if not self._drag.get("erased"):
            self._push_undo()
            self._drag["erased"] = True
        for st in hit:                                  # сразу видно, что стёрто
            self.dcanvas.create_line(*[v for q in st for v in self._to_px(q)],
                                     fill="#ffffff", width=5, tags="live")
        self._sketch = [st for st in self._sketch if all(st is not h for h in hit)]
        self._sel_clear()

    def _d_press(self, e):
        if self._dmap is None:
            return
        self.dcanvas.focus_set()
        pt = self._to_mm(e.x, e.y)
        self._drag = {"start": pt, "pts": [pt], "last": (e.x, e.y)}
        if self.v_tool.get() == "select":
            self._s_press(e, pt)
        elif self.v_tool.get() == "eraser":
            self._erase_at(pt)

    def _d_move(self, e):
        if not self._drag:
            return
        tool = self.v_tool.get()
        pt = self._to_mm(e.x, e.y)
        c = self.dcanvas
        w = max(1, int(round(self.cfg.draw.pen_width * self._dmap[2])))
        if tool == "select":
            self._s_move(e, pt)
        elif tool == "eraser":
            self._erase_at(pt)
        elif tool == "pen":
            lx, ly = self._drag["last"]
            if math.hypot(e.x - lx, e.y - ly) < 1.5:
                return
            c.create_line(lx, ly, e.x, e.y, fill=PV.INK, width=w,
                          capstyle="round", tags="live")
            self._drag["pts"].append(pt)
            self._drag["last"] = (e.x, e.y)
        else:
            c.delete("live")
            shp = self._shape(self._drag["start"], pt, bool(e.state & 0x0001))
            c.create_line(*[v for q in shp for v in self._to_px(q)],
                          fill=PV.INK, width=w, tags="live")

    def _d_release(self, e):
        if not self._drag:
            return
        tool = self.v_tool.get()
        if tool == "select":
            try:
                self._s_release(e, self._to_mm(e.x, e.y))
            finally:
                self._drag = None
            return
        drag, self._drag = self._drag, None
        if tool == "eraser":
            if drag.get("erased"):
                self.rebuild()
            return
        pt = self._to_mm(e.x, e.y)
        if tool == "pen":
            pts = drag["pts"]
            if math.dist(pt, pts[-1]) > 0.05:
                pts.append(pt)
            if len(pts) == 1:                           # щелчок — точка
                x, y = pts[0]
                pts = [(x - 0.25, y), (x + 0.25, y)]
            stroke = DR.smooth_sketch(pts, self.cfg.draw.sketch_smooth)
        else:
            stroke = self._shape(drag["start"], pt, bool(e.state & 0x0001))
            if DR.path_length(stroke) < 0.3:
                self.dcanvas.delete("live")
                return
        self._push_undo()
        self._sketch.append(stroke)
        self.rebuild()

    def sketch_undo(self):
        if self._sketch_undo:
            sk, place = self._sketch_undo.pop()
            self._sketch = sk
            for k, v in place.items():
                setattr(self.cfg.draw, k, v)
            self._pull_draw()
            self._sel = {i for i in getattr(self, "_sel", set()) if i < len(sk)}
            self.rebuild()

    def sketch_clear(self):
        if self._sketch and messagebox.askyesno("Очистить", "Стереть весь эскиз?"):
            self._push_undo()
            self._sketch = []
            self._sel_clear()
            self.rebuild()

    def sketch_save(self):
        if not self._sketch:
            messagebox.showinfo("Пусто", "Эскиз пока пустой.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("Эскиз", "*.json")])
        if p:
            DR.save_sketch(p, self._sketch, self.cfg.page)

    def sketch_load(self):
        p = filedialog.askopenfilename(filetypes=[("Эскиз", "*.json")])
        if not p:
            return
        try:
            st = DR.load_sketch(p)
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror("Не открылось", str(e))
            return
        self._push_undo()
        self._sketch = st
        self._sel_clear()
        self.rebuild()

    # ======================================================== почерк
    def _alphabet(self):
        return FV.alphabet(include_latin=self.v_lat.get(),
                           include_cyrillic=self.v_cyr.get(),
                           include_digits=self.v_dig.get(),
                           include_punct=self.v_pun.get())

    def make_template(self):
        chars = self._alphabet()
        if not chars:
            messagebox.showwarning("Пусто", "Выберите хотя бы один набор символов.")
            return
        d = filedialog.askdirectory(title="Куда сохранить пропись")
        if not d:
            return
        spec = SH.TemplateSpec()
        spec.title = "ОБРАЗЕЦ ПОЧЕРКА"
        try:
            spec, files = SH.save_template(chars, d, spec=spec, dpi=200,
                                           variants=int(self.v_variants.get()))
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror("Не получилось", str(e))
            return
        self._spec = spec
        messagebox.showinfo(
            "Пропись готова",
            "Листов: %d\nФайлы в %s\n\nРаспечатайте БЕЗ масштабирования "
            "(100%%, «реальный размер»), впишите буквы тёмной ручкой между "
            "линиями и сфотографируйте сверху при ровном свете."
            % (len(spec.pages), d))

    def load_photos(self):
        paths = filedialog.askopenfilenames(
            title="Фотографии прописи",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff"),
                       ("Все файлы", "*.*")])
        if not paths:
            return
        spec = self._spec
        if spec is None:
            sp = filedialog.askopenfilename(
                title="Файл описания шаблона (propis_spec.json)",
                filetypes=[("Описание", "*.json")])
            if not sp:
                messagebox.showwarning(
                    "Нужно описание",
                    "Рядом с прописью лежит propis_spec.json — он говорит "
                    "программе, где какая буква на листе.")
                return
            spec = SH.TemplateSpec.load(sp)
            self._spec = spec
        # N-е фото соответствует N-му листу прописи, поэтому порядок
        # важен: сортируем по имени, чтобы propis_01.jpg не оказался вторым
        self._photo_paths = sorted(paths, key=lambda q: os.path.basename(q).lower())
        if len(self._photo_paths) > 1:
            self._logline("порядок листов: " + " -> ".join(
                os.path.basename(q) for q in self._photo_paths))
        self.rerecognize()

    def rerecognize(self):
        if not getattr(self, "_photo_paths", None) or self._spec is None:
            return
        self._say("распознаю…")
        self.update_idletasks()
        t = threading.Thread(target=self._recognize_worker, daemon=True)
        t.start()

    def _recognize_worker(self):
        found, errors, mapping = [], [], []
        ink = float(self.v_ink.get())
        delines = bool(self.v_delines.get())
        for pi, path in enumerate(self._photo_paths):
            try:
                img = Image.open(path)
                rect, info = SH.detect_sheet(img, self._spec, work_dpi=200)
                page_idx = min(pi, len(self._spec.pages) - 1)
                cells = SH.slice_cells(rect, self._spec, page_idx,
                                       info["px_per_mm"])
                mapping.append("%s = лист %d (%s…)"
                               % (os.path.basename(path), page_idx + 1,
                                  "".join(self._spec.pages[page_idx][:6])))
                for c in cells:
                    strokes, _dbg = VZ.vectorize_image(
                        c["image"], ink_level=ink, template=c.get("template"),
                        remove_lines=c["guides"] if delines else None)
                    if not strokes:
                        continue
                    try:
                        adv, ns = normalize_strokes(strokes, c["baseline"], c["cap"])
                    except ValueError:
                        continue
                    g = Glyph(c["char"], adv, order_strokes(ns), source="user")
                    found.append((c["char"], c["variant"], g))
            except Exception as e:                      # noqa: BLE001
                errors.append("%s: %s" % (os.path.basename(path), e))
        self.after(0, lambda: self._recognize_done(found, errors, mapping))

    def _recognize_done(self, found, errors, mapping=()):
        for m in mapping:
            self._logline(m)
        self._recognized = [[ch, v, g, None, True, None] for ch, v, g in found]
        self._render_glyph_grid()
        msg = "распознано начертаний: %d" % len(found)
        if errors:
            msg += " · ошибки: " + "; ".join(errors[:2])
        self._say(msg, error=bool(errors))
        if errors and not found:
            messagebox.showerror("Не удалось прочитать лист", "\n".join(errors))

    def _font_items(self):
        """Содержимое своего шрифта плоским списком: [char, вариант, Glyph]."""
        out = []
        for ch in sorted(self.fontset.variants):
            for v, g in enumerate(self.fontset.variants[ch]):
                out.append([ch, v, g])
        return out

    def _render_glyph_grid(self):
        """
        Сетка начертаний. Два режима: только что распознанное (щелчок
        исключает из приёмки) и содержимое своего шрифта (щелчок помечает
        на удаление). Без второго режима одно неудачное начертание нельзя
        было убрать иначе как стерев весь почерк.
        """
        for w in self.gframe.winfo_children():
            w.destroy()
        self._thumbs = []                      # держим ссылки, иначе Tk их соберёт

        mode = self.v_view.get()
        if mode == "font":
            items = self._font_items()
            self._font_marks = getattr(self, "_font_marks", set())
            self._font_marks &= {(c, v) for c, v, _g in items}
            self.lbl_grid.configure(
                text="начертаний: %d; щелчок отмечает на удаление" % len(items))
            self.btn_del.configure(state="normal" if items else "disabled")
        else:
            items = [[it[0], it[1], it[2]] for it in self._recognized]
            self.lbl_grid.configure(
                text="распознано: %d; щелчок исключает" % len(items))
            self.btn_del.configure(state="disabled")

        cols = 10
        for i, (ch, v, g) in enumerate(items):
            photo = ImageTk.PhotoImage(PV.glyph_image(g, size=86), master=self)
            self._thumbs.append(photo)
            cell = tk.Frame(self.gframe, relief="solid", borderwidth=1,
                            background="#ffffff")
            cell.grid(row=i // cols, column=i % cols, padx=3, pady=3)
            lbl = tk.Label(cell, image=photo, borderwidth=0, background="#ffffff")
            lbl.pack()
            cap = tk.Label(cell, text="%s · %d" % (ch, v + 1),
                           background="#ffffff")
            cap.pack(fill="x")
            if mode == "font":
                marked = (ch, v) in self._font_marks
                self._paint(cell, marked, "#f6d5d5")
                cb = lambda e, c=ch, vv=v, w=cell: self._toggle_font(c, vv, w)
            else:
                self._recognized[i][5] = cell
                self._paint(cell, not self._recognized[i][4], "#e6e6ea")
                cb = lambda e, idx=i: self._toggle_glyph(idx)
            for w in (lbl, cap, cell):
                w.bind("<Button-1>", cb)

        self.gframe.update_idletasks()
        self.gcanvas.configure(scrollregion=self.gcanvas.bbox("all"))

    @staticmethod
    def _paint(cell, marked, colour):
        bg = colour if marked else "#ffffff"
        cell.configure(background=bg, relief="flat" if marked else "solid")
        for w in cell.winfo_children():
            try:
                w.configure(background=bg)
            except tk.TclError:
                pass

    def _toggle_glyph(self, idx):
        item = self._recognized[idx]
        item[4] = not item[4]
        self._paint(item[5], not item[4], "#e6e6ea")

    def _toggle_font(self, ch, v, cell):
        key = (ch, v)
        if key in self._font_marks:
            self._font_marks.discard(key)
        else:
            self._font_marks.add(key)
        self._paint(cell, key in self._font_marks, "#f6d5d5")

    def delete_marked(self):
        marks = sorted(getattr(self, "_font_marks", set()),
                       key=lambda t: (t[0], -t[1]))   # с конца, чтобы не сбить номера
        if not marks:
            messagebox.showinfo("Ничего не отмечено",
                                "Щёлкните по начертаниям, которые нужно убрать.")
            return
        if not messagebox.askyesno("Удалить",
                                   "Убрать из шрифта начертаний: %d?" % len(marks)):
            return
        for ch, v in marks:
            self.fontset.remove(ch, v)
        self._font_marks = set()
        self._update_coverage()
        self._render_glyph_grid()
        self.rebuild()

    def accept_recognized(self):
        n = 0
        for item in self._recognized:
            if not item[4]:
                continue
            self.fontset.add(item[2])
            n += 1
        self._update_coverage()
        self.rebuild()
        if n:
            self.v_view.set("font")
            self._render_glyph_grid()
        messagebox.showinfo("Готово", "Добавлено начертаний: %d" % n)

    def _update_coverage(self):
        chars = self._alphabet() or FV.alphabet()
        done, total, _missing = self.fontset.coverage(chars)
        var = sum(len(v) for v in self.fontset.variants.values())
        self.lbl_cov.configure(
            text="оцифровано: %d из %d букв, всего начертаний %d"
                 % (done, total, var))

    def save_font(self):
        if not self.fontset.variants:
            messagebox.showwarning("Пусто", "Пока нет ни одного своего начертания.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("Шрифт", "*.json")])
        if p:
            self.fontset.save(p)
            self.cfg.font_path = p
            messagebox.showinfo("Сохранено", p)

    def _load_font_file(self, path, quiet=False):
        """-> True, если шрифт загрузился."""
        try:
            self.fontset = FontSet.load(path)
        except Exception as e:                          # noqa: BLE001
            if not quiet:
                messagebox.showerror("Не открылось", str(e))
            return False
        self.fontset.builtin_fallback = bool(self.v_fallback.get())
        self.fontset.flat = self.cfg.curve_flatness
        self.cfg.font_path = path
        self._font_marks = set()
        self._update_coverage()
        self.v_view.set("font")
        self._render_glyph_grid()
        return True

    def use_calligraphy(self):
        """
        Каллиграфический почерк одной кнопкой: встроенная пропись, острое
        перо с нажимом и размер, при котором строчные не мелкие — в прописи
        они в 2,4 раза ниже заглавных.
        """
        self._collect()
        if not self._load_font_file(FontSet.CALLIG):
            return
        keep = self.cfg.human.seed
        self.cfg.human = HUMAN_PRESETS["Каллиграфия, острое перо"]()
        self.cfg.human.seed = keep
        # буквы прописи уже наклонены — свой наклон почти не нужен
        self.cfg.human.slant, self.cfg.human.slant_jitter = 2.0, 0.6
        self.cfg.human.calli_width = 0.6
        self.v_preset.set("Каллиграфия, острое перо")
        pg = self.cfg.page
        if pg.grid:
            pg.grid_fill = max(pg.grid_fill, 1.3)
            note = "заглавные в 1,3 строки клетки"
        else:
            pg.size_mm = max(pg.size_mm, 8.0)
            pg.line_spacing = max(pg.line_spacing, 1.9)
            note = "высота заглавной %.0f мм" % pg.size_mm
        for f in self.fields:
            f.pull()
        self.rebuild()
        self._say(self.status.cget("text") + "   ·   каллиграфия: пропись, "
                  "острое перо, " + note)

    def load_font(self):
        p = filedialog.askopenfilename(filetypes=[("Шрифт", "*.json")])
        if p and self._load_font_file(p):
            self.rebuild()

    def clear_font(self):
        if messagebox.askyesno("Очистить", "Удалить все свои начертания?"):
            self.fontset.clear()
            self._font_marks = set()
            self.cfg.font_path = ""
            self._update_coverage()
            self._render_glyph_grid()
            self.rebuild()

    # ======================================================== печать
    def refresh_ports(self):
        ports = PR.available_ports()
        self.cb_port.configure(values=[d for _p, d in ports])
        self._port_map = {d: p for p, d in ports}
        if ports and not self.v_port.get():
            self.v_port.set(ports[0][1])

    def _port(self):
        d = self.v_port.get()
        p = getattr(self, "_port_map", {}).get(d, d)
        if not p:
            raise PR.PrinterError("не выбран порт")
        return p

    def _logline(self, s):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def test_link(self):
        try:
            link = PR.PrinterLink(self._port(), int(self.v_baud.get()),
                                  on_log=lambda s: self.after(0, self._logline, s))
            ok = link.send_now("M115")
            self._logline("связь: %s" % ("есть" if ok else "нет ответа"))
        except Exception as e:                          # noqa: BLE001
            self._logline("ошибка связи: %s" % e)
            messagebox.showerror("Связь", str(e))

    def jog_z(self, d):
        self._collect()
        try:
            link = PR.PrinterLink(self._port(), int(self.v_baud.get()),
                                  on_log=lambda s: self.after(0, self._logline, s))
            link.connect()
            for c in PR.jog_gcode("Z", d, self.cfg.pen.feed_z):
                link._send_line(c)
            link.close()
            self._logline("Z сдвинут на %+.2f мм" % d)
        except Exception as e:                          # noqa: BLE001
            self._logline("не вышло: %s" % e)

    def grab_z(self):
        messagebox.showinfo(
            "Как выставить Z письма",
            "1. Кнопками Z ±1 / ±0.1 опустите перо, пока оно не начнёт писать.\n"
            "2. Посмотрите текущий Z на экране принтера.\n"
            "3. Впишите это значение в поле «Z письма».\n\n"
            "Быстрее — «Лесенка Z»: принтер сам нарисует ряд штрихов на разной "
            "высоте, останется взять тот Z, где линия стала сплошной.")

    def run_calibration(self):
        self._collect()
        g = PR.calibration_gcode(self.cfg)
        if not messagebox.askyesno(
                "Лесенка Z",
                "Принтер нарисует ряд коротких штрихов, опускаясь по Z.\n"
                "Убедитесь, что перо закреплено, а на столе лежит бумага.\n\n"
                "Запускаем?"):
            return
        self._start_job(g)

    def grid_marks_gcode(self):
        """Чёрточки у полей на высоте каждой строки -> (G-code, число строк)."""
        pg = self.cfg.page
        ys = L.grid_baselines(pg)
        x0, x1 = pg.margin_left, pg.sheet_w - pg.margin_right
        dash = min(6.0, max(2.0, (x1 - x0) / 10.0))
        strokes = []
        for y in ys:
            strokes.append([(x0, y), (x0 + dash, y)])
            strokes.append([(x1 - dash, y), (x1, y)])
        res = GC.generate([L.Page(strokes=strokes, size_mm=pg.size_mm)], self.cfg,
                          None, title="метки строк")
        return res, len(ys)

    def grid_marks(self):
        self._collect()
        if not self.cfg.page.grid:
            messagebox.showinfo("Метки строк",
                                "Сначала включите «писать по клеткам».")
            return
        res, n = self.grid_marks_gcode()
        ans = messagebox.askyesnocancel(
            "Метки строк",
            "Принтер нарисует у левого и правого поля короткие чёрточки там, "
            "где будут строки (%d шт.). Если они легли точно на линии клетки — "
            "всё выставлено. Если все сдвинуты одинаково — поправьте «первую "
            "строку от верха»; если расходятся к низу — «размер клетки».\n\n"
            "Да — отправить на принтер, Нет — сохранить G-code." % n)
        if ans is None:
            return
        if ans:
            self._start_job(res.text)
            return
        p = filedialog.asksaveasfilename(defaultextension=".gcode",
                                         initialfile="метки_строк.gcode",
                                         filetypes=[("G-code", "*.gcode")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(res.text)

    def do_frame(self):
        """
        Предпросмотр на принтере: перо поднято и объезжает рамку того, что
        будет напечатано на текущем листе, — сверить положение с бумагой.
        """
        if not self.pages:
            return
        self._collect()
        ans = messagebox.askyesnocancel(
            "Граница на принтере",
            "Перо НЕ опустится — наоборот, поднимется на 2 мм выше обычного. "
            "Принтер медленно объедет рамку и постоит в "
            "каждом углу — сверьте её с бумагой.\n\n"
            "Да — рамка текста и рисунка (лист %d)\n"
            "Нет — края листа" % (self.page_index + 1))
        if ans is None:
            return
        try:
            res = GC.frame_gcode(self.pages[self.page_index], self.cfg,
                                 what="content" if ans else "sheet")
        except ValueError as e:
            messagebox.showinfo("Граница", "На листе пока нечего печатать (%s)." % e)
            return
        if res.warnings and not messagebox.askyesno(
                "Есть замечания", "\n".join(res.warnings) + "\n\nВсё равно ехать?"):
            return
        if not self.v_port.get():
            p = filedialog.asksaveasfilename(
                title="Порт не выбран — сохранить G-code рамки",
                defaultextension=".gcode", initialfile="граница.gcode",
                filetypes=[("G-code", "*.gcode")])
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(res.text)
            return
        self._start_job(res.text)

    def do_print(self):
        if not self.pages:
            return
        self._collect()
        res = GC.generate(self.pages, self.cfg, self.human,
                          page_index=self.page_index)
        if res.warnings:
            if not messagebox.askyesno("Есть замечания",
                                       "\n".join(res.warnings) + "\n\nВсё равно печатать?"):
                return
        if not messagebox.askyesno(
                "Печать",
                "%s\n\nПеро закреплено, бумага лежит, сопло холодное?"
                % res.summary()):
            return
        self._start_job(res.text)

    def _start_job(self, gtext):
        try:
            self.link = PR.PrinterLink(
                self._port(), int(self.v_baud.get()),
                on_progress=lambda i, n, l: self.after(0, self._progress, i, n),
                on_log=lambda s: self.after(0, self._logline, s),
                on_done=lambda ok, m: self.after(0, self._job_done, ok, m))
            self.link.start(gtext)
        except Exception as e:                          # noqa: BLE001
            messagebox.showerror("Не запустилось", str(e))
            return
        self.prog.configure(value=0, maximum=100)
        self.btn_pause.configure(state="normal", text="Пауза")
        self.btn_stop.configure(state="normal")
        self._logline("печать начата")

    def _progress(self, i, n):
        self.prog.configure(value=100.0 * i / max(1, n))
        self.status.configure(text="печать: %d из %d команд (%.0f%%)"
                                   % (i, n, 100.0 * i / max(1, n)))

    def _job_done(self, ok, msg):
        self.btn_pause.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self._logline(msg)
        self._say(msg, error=not ok)
        if ok:
            self.prog.configure(value=100)

    def do_pause(self):
        if not self.link:
            return
        if self.link.is_paused:
            self.link.resume()
            self.btn_pause.configure(text="Пауза")
            self._logline("продолжаем")
        else:
            self.link.pause()
            self.btn_pause.configure(text="Продолжить")
            self._logline("пауза")

    def do_stop(self):
        if self.link and messagebox.askyesno("Стоп", "Прервать печать?"):
            self.link.abort()

    def _on_close(self):
        if self.link and self.link.is_running:
            if not messagebox.askyesno("Идёт печать", "Прервать и выйти?"):
                return
            self.link.abort()
        if getattr(self, "_autosave_id", None) is not None:
            self.after_cancel(self._autosave_id)
        self._save_state()
        self.destroy()


def main():
    app = App()
    app.mainloop()
