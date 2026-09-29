# -*- coding: utf-8 -*-
"""
Экспериментальные окна вкладки «Почерк»:

    FreehandWindow — почерк со свободного листа: фото любого листа
                     с текстом + что на нём написано -> буквы шрифта;
    JoinEditor     — соединения букв: где у начертания вход и выход
                     и соединяется ли оно с соседями вообще.
"""

import copy
import os
import threading

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from ..core import freehand as FH
from ..core import joins as JN
from ..core import layout as L
from ..core import preview as PV
from ..core.glyphset import CAP, XH
from ..core.humanize import Human

GRAB = 7            # px: насколько близко к линии надо щёлкнуть, чтобы её взять


def _theme(app, win):
    C = app.C
    win.configure(background=C["bg"])
    return C


def _text_colors(app, w):
    C = app.C
    w.configure(background=C["field"], foreground=C["field_fg"],
                selectbackground=C["select"],
                selectforeground=C["select_fg"], relief="flat",
                highlightthickness=1, highlightbackground=C["border"],
                highlightcolor=C["accent"])
    if isinstance(w, tk.Text):                  # у списка курсора ввода нет
        w.configure(insertbackground=C["insert"])


# ===================================================== свободный лист

class FreehandWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Почерк со свободного листа — эксперимент")
        self.geometry("1260x800")
        self.minsize(980, 620)
        C = _theme(app, self)
        self.sheet = None
        self.path = None
        self.li = 0                   # текущая строка
        self.off = set()              # исключённые буквы (строка, слово, буква)
        self._drag = None
        self._img = None
        self._thumbs = []
        self._busy = False

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Button(top, text="Открыть фото…", command=self.open_photo).pack(side="left")
        ttk.Label(top, text="порог чернил:").pack(side="left", padx=(14, 4))
        self.v_ink = tk.DoubleVar(value=float(app.v_ink.get()))
        ttk.Scale(top, from_=0.35, to=0.92, variable=self.v_ink,
                  orient="horizontal", length=120).pack(side="left")
        self.lbl_ink = ttk.Label(top, text="%.2f" % self.v_ink.get(), width=5)
        self.lbl_ink.pack(side="left", padx=4)
        self.v_ink.trace_add("write", lambda *a: self.lbl_ink.configure(
            text="%.2f" % self.v_ink.get()))
        self.v_ruled = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="убрать клетку / линейку",
                        variable=self.v_ruled).pack(side="left", padx=10)
        ttk.Button(top, text="Разобрать заново", command=self.analyse).pack(side="left")
        self.lbl = ttk.Label(top, text="Откройте фото листа, где что-то написано от руки.",
                             style="Hint.TLabel")
        self.lbl.pack(side="left", padx=14)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=4)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, width=300)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        ttk.Label(left, text="Что написано на листе —\nстрока в строку, как на фото:",
                  justify="left").pack(anchor="w")
        self.txt = tk.Text(left, width=34, height=11, wrap="none", undo=True,
                           font=("Segoe UI", 11))
        _text_colors(app, self.txt)
        self.txt.pack(fill="x", pady=(4, 4))
        ttk.Button(left, text="Разложить текст по строкам фото",
                   command=self.apply_text).pack(fill="x")
        ttk.Label(left, style="Hint.TLabel", justify="left", wraplength=290, text=(
            "Пустые строки не считаются. Переносы строк — ровно как на "
            "листе: программа сверяет число строк. Можно переписать "
            "не весь лист, а первые строки.")).pack(anchor="w", pady=(4, 10))
        ttk.Label(left, text="Строки на фото:").pack(anchor="w")
        self.lst = tk.Listbox(left, height=10, activestyle="none", exportselection=False,
                              font=("Segoe UI", 10))
        _text_colors(app, self.lst)
        self.lst.pack(fill="both", expand=True, pady=(4, 0))
        self.lst.bind("<<ListboxSelect>>", lambda e: self._pick_line())

        mid = ttk.Frame(body)
        mid.grid(row=0, column=1, sticky="nsew")
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(1, weight=1)
        nav = ttk.Frame(mid)
        nav.grid(row=0, column=0, sticky="ew")
        ttk.Button(nav, text="◀", width=3, command=lambda: self.go(-1)).pack(side="left")
        self.lbl_line = ttk.Label(nav, text="строка — из —", width=16, anchor="center")
        self.lbl_line.pack(side="left", padx=4)
        ttk.Button(nav, text="▶", width=3, command=lambda: self.go(1)).pack(side="left")
        ttk.Label(nav, text="наклон письма, °:").pack(side="left", padx=(16, 4))
        self.v_slant = tk.StringVar(value="0")
        sp = ttk.Spinbox(nav, from_=-30, to=45, increment=1, width=6,
                         textvariable=self.v_slant, command=self._slant_typed)
        sp.pack(side="left")
        sp.bind("<Return>", lambda e: self._slant_typed())
        ttk.Button(nav, text="Разрезать заново", command=self.recut).pack(side="left", padx=10)
        self.lbl_note = ttk.Label(nav, text="", style="Hint.TLabel")
        self.lbl_note.pack(side="left", padx=6)

        self.cv = tk.Canvas(mid, highlightthickness=0, background=C["canvas"],
                            cursor="crosshair")
        self.cv.grid(row=1, column=0, sticky="nsew", pady=6)
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Motion>", self._hover)
        ttk.Label(mid, style="Hint.TLabel", text=(
            "Тяните мышью: красные линии — границы букв, зелёные — края слов, "
            "синяя — базовая линия, голубая — высота строчных. "
            "Щелчок по букве внизу — не брать её в шрифт.")).grid(row=2, column=0, sticky="w")

        strip = ttk.Frame(mid)
        strip.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.sc = tk.Canvas(strip, height=96, highlightthickness=0, background=C["tiles"])
        self.sc.pack(fill="x", side="top")
        hs = ttk.Scrollbar(strip, orient="horizontal", command=self.sc.xview)
        hs.pack(fill="x")
        self.sc.configure(xscrollcommand=hs.set)
        self.sf = tk.Frame(self.sc, background=C["tiles"])
        self.sc.create_window((0, 0), window=self.sf, anchor="nw")
        self.sf.bind("<Configure>", lambda e: self.sc.configure(
            scrollregion=self.sc.bbox("all")))

        bot = ttk.Frame(self)
        bot.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bot, text="Передать буквы в «Распознанное»",
                   command=self.send, style="Accent.TButton").pack(side="right")
        self.lbl_total = ttk.Label(bot, text="", style="Hint.TLabel")
        self.lbl_total.pack(side="right", padx=12)
        ttk.Label(bot, style="Hint.TLabel", text=(
            "Дальше — как с прописью: на вкладке «Почерк» просмотреть "
            "и нажать «Принять распознанное».")).pack(side="left")

        self.bind("<Left>", lambda e: self.go(-1) if e.widget is not self.txt else None)
        self.bind("<Right>", lambda e: self.go(1) if e.widget is not self.txt else None)
        app._titlebar_for(self)

    # --------------------------------------------------------- фото
    def open_photo(self):
        p = filedialog.askopenfilename(
            parent=self, title="Фото листа с текстом",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                       ("Все файлы", "*.*")])
        if p:
            self.path = p
            self.analyse()

    def analyse(self):
        if not self.path or self._busy:
            return
        self._busy = True
        self.lbl.configure(text="разбираю фото…")
        ink, ruled = float(self.v_ink.get()), bool(self.v_ruled.get())

        box = []

        def work():
            try:
                box.append((FH.FreeSheet(self.path, ink_level=ink, ruled=ruled), None))
            except Exception as e:                  # noqa: BLE001
                box.append((None, str(e)))

        def poll():
            # Tk трогаем только из главного потока: поток лишь кладёт результат
            if box:
                self._analysed(*box[0])
            elif self.winfo_exists():
                self.after(80, poll)
        threading.Thread(target=work, daemon=True).start()
        self.after(80, poll)

    def _analysed(self, sh, err):
        self._busy = False
        if err:
            self.lbl.configure(text="не получилось: " + err)
            return
        self.sheet = sh
        self.lbl.configure(text="%s: строк с текстом на фото — %d"
                                % (os.path.basename(self.path), len(sh.peaks)))
        if self.txt.get("1.0", "end").strip():
            self.apply_text()
        else:
            self.lbl.configure(text=self.lbl.cget("text") +
                               ". Впишите слева, что там написано.")

    def apply_text(self):
        if self.sheet is None:
            messagebox.showinfo("Сначала фото", "Откройте фото листа.", parent=self)
            return
        text = self.txt.get("1.0", "end")
        if not text.strip():
            messagebox.showinfo("Нет текста", "Впишите, что написано на листе.", parent=self)
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.sheet.set_text(L.clean_text(text))
        finally:
            self.config(cursor="")
        self.off = set()
        self.li = 0
        msg = "; ".join(self.sheet.warnings) or "строки совпали с текстом"
        self.lbl.configure(text=msg)
        self._fill_list()
        self.show_line()

    def _fill_list(self):
        self.lst.delete(0, "end")
        for i, ln in enumerate(self.sheet.lines):
            mark = "  ⚠" if ln.note else ""
            self.lst.insert("end", "%d. %s%s" % (i + 1, ln.text, mark))
        self._count()

    def _pick_line(self):
        sel = self.lst.curselection()
        if sel:
            self.li = sel[0]
            self.show_line()

    def go(self, d):
        if self.sheet and self.sheet.lines:
            self.li = (self.li + d) % len(self.sheet.lines)
            self.show_line()

    def line(self):
        if self.sheet is None or not self.sheet.lines:
            return None
        return self.sheet.lines[min(self.li, len(self.sheet.lines) - 1)]

    def show_line(self):
        ln = self.line()
        if ln is None:
            self.cv.delete("all")
            return
        n = len(self.sheet.lines)
        self.lbl_line.configure(text="строка %d из %d" % (self.li + 1, n))
        self.lst.selection_clear(0, "end")
        self.lst.selection_set(self.li)
        self.lst.see(self.li)
        import math
        self.v_slant.set("%g" % round(math.degrees(math.atan(ln.slant)), 1))
        self.lbl_note.configure(text=ln.note)
        self._crop = None
        self.draw()
        self.strip()

    def _slant_typed(self):
        ln = self.line()
        if ln is None:
            return
        import math
        try:
            deg = float(self.v_slant.get().replace(",", "."))
        except ValueError:
            return
        ln.slant = math.tan(math.radians(max(-40.0, min(50.0, deg))))
        self.draw()
        self.strip()

    def recut(self):
        ln = self.line()
        if ln is None:
            return
        self.sheet.auto_cut(ln)
        self.off = {k for k in self.off if k[0] != self.li}
        self.draw()
        self.strip()

    # --------------------------------------------------------- холст
    def _geom(self):
        """Кусок фото вокруг строки и масштаб: -> (x0, y0, x1, y1, s)."""
        ln = self.line()
        x0, y0, x1, y1 = ln.box
        h = self.sheet.h_med
        x0, x1 = max(0, x0 - int(0.6 * h)), min(self.sheet.image.width, x1 + int(0.6 * h))
        y0, y1 = max(0, y0 - int(0.5 * h)), min(self.sheet.image.height, y1 + int(0.5 * h))
        cw = max(50, self.cv.winfo_width() - 20)
        ch = max(50, self.cv.winfo_height() - 20)
        s = min(cw / max(1, x1 - x0), ch / max(1, y1 - y0))
        return x0, y0, x1, y1, s

    def _to_cv(self, x, y):
        x0, y0, _x1, _y1, s = self._g
        return 10 + (x - x0) * s, 10 + (y - y0) * s

    def _to_img(self, cx, cy):
        x0, y0, _x1, _y1, s = self._g
        return x0 + (cx - 10) / s, y0 + (cy - 10) / s

    def draw(self):
        c = self.cv
        c.delete("all")
        ln = self.line()
        if ln is None:
            c.create_text(20, 20, anchor="nw", fill=self.app.C["muted"],
                          text="Здесь появится строка с фото и границы букв.",
                          font=("Segoe UI", 11))
            return
        self._g = self._geom()
        x0, y0, x1, y1, s = self._g
        crop = self.sheet.image.crop((x0, y0, x1, y1))
        crop = crop.resize((max(1, int((x1 - x0) * s)), max(1, int((y1 - y0) * s))),
                           Image.LANCZOS if s < 1 else Image.BICUBIC)
        self._img = ImageTk.PhotoImage(crop, master=self)
        c.create_image(10, 10, anchor="nw", image=self._img)
        self.overlay()

    def overlay(self):
        c = self.cv
        c.delete("ov")
        ln = self.line()
        x0, y0, x1, y1, s = self._g
        a, _ = self._to_cv(x0, 0)
        b, _ = self._to_cv(x1, 0)
        for y, col, dash in ((ln.xh, "#5fb4ff", (4, 3)), (ln.base, "#1f6fff", None)):
            _, cy = self._to_cv(0, y)
            c.create_line(a, cy, b, cy, fill=col, width=2, dash=dash, tags="ov")
        # осевые линии, как их увидела программа
        for p in self.sheet.paths(ln):
            pts = [v for q in p for v in self._to_cv(*q)]
            if len(pts) >= 4:
                c.create_line(*pts, fill="#ff9f1a", width=1, tags="ov")
        words = ln.text_words
        for wi, bnd in enumerate(ln.words):
            for k, u in enumerate(bnd):
                edge = k in (0, len(bnd) - 1)
                xa, ya = self._to_cv(ln.x_at(u, y0), y0)
                xb, yb = self._to_cv(ln.x_at(u, y1), y1)
                c.create_line(xa, ya, xb, yb, width=2 if edge else 2,
                              fill="#18a058" if edge else "#e23b3b", tags="ov")
            word = words[wi] if wi < len(words) else ""
            for k, ch in enumerate(word[:len(bnd) - 1]):
                u = 0.5 * (bnd[k] + bnd[k + 1])
                off = (self.li, wi, k) in self.off
                tx, ty = self._to_cv(ln.x_at(u, ln.xh - 0.9 * (ln.base - ln.xh)),
                                     ln.xh - 0.9 * (ln.base - ln.xh))
                c.create_text(tx, max(12, ty), text=ch, tags="ov",
                              fill=self.app.C["muted"] if off else "#e23b3b",
                              font=("Segoe UI", 13, "bold"))

    def _handles(self):
        """Все линии, которые можно тянуть: (вид, данные, расстояние-функция)."""
        ln = self.line()
        out = []
        for wi, bnd in enumerate(ln.words):
            for k in range(len(bnd)):
                out.append(("cut", (wi, k)))
        out.append(("base", None))
        out.append(("xh", None))
        return out

    def _nearest(self, cx, cy):
        ln = self.line()
        if ln is None:
            return None
        x, y = self._to_img(cx, cy)
        s = self._g[4]
        best, bd = None, GRAB
        for kind, data in self._handles():
            if kind == "cut":
                wi, k = data
                d = abs(ln.x_at(ln.words[wi][k], y) - x) * s
            else:
                d = abs((ln.base if kind == "base" else ln.xh) - y) * s
            if d < bd:
                best, bd = (kind, data), d
        return best

    def _hover(self, e):
        h = self._nearest(e.x, e.y) if self.line() is not None else None
        cur = "crosshair"
        if h:
            cur = "sb_h_double_arrow" if h[0] == "cut" else "sb_v_double_arrow"
        self.cv.configure(cursor=cur)

    def _press(self, e):
        self._drag = self._nearest(e.x, e.y)

    def _move(self, e):
        if not self._drag:
            return
        ln = self.line()
        x, y = self._to_img(e.x, e.y)
        kind, data = self._drag
        if kind == "cut":
            wi, k = data
            self.sheet.move_boundary(ln, wi, k, ln.u(x, y))
        elif kind == "base":
            ln.base = max(y, ln.xh + 4)
        else:
            ln.xh = min(y, ln.base - 4)
        self.overlay()

    def _release(self, _e):
        if self._drag:
            self._drag = None
            self.strip()

    # --------------------------------------------------------- буквы
    def strip(self):
        for w in self.sf.winfo_children():
            w.destroy()
        self._thumbs = []
        if self.line() is None:
            return
        C = self.app.C
        items = self.sheet.glyphs(lines=[self.li])
        for ch, g, key in items:
            ph = ImageTk.PhotoImage(PV.glyph_image(g, size=70), master=self)
            self._thumbs.append(ph)
            off = key in self.off
            cell = tk.Frame(self.sf, background=C["tiles"], padx=2, pady=2)
            cell.pack(side="left", padx=2, pady=2)
            lb = tk.Label(cell, image=ph, borderwidth=2, relief="solid",
                          background="#ffffff")
            lb.pack()
            cap = tk.Label(cell, text=ch, background=C["tiles"],
                           foreground=C["muted"] if off else C["text"])
            cap.pack()
            if off:
                lb.configure(background="#d9d9de", relief="flat")
            for w in (lb, cap):
                w.bind("<Button-1>", lambda e, k=key: self._toggle(k))
        self._count()

    def _toggle(self, key):
        if key in self.off:
            self.off.discard(key)
        else:
            self.off.add(key)
        self.overlay()
        self.strip()

    def _count(self):
        if self.sheet is None:
            return
        n = sum(len(w) for ln in self.sheet.lines for w in ln.text_words)
        self.lbl_total.configure(text="букв в тексте: %d, исключено: %d" % (n, len(self.off)))

    def send(self):
        if self.sheet is None or not self.sheet.lines:
            messagebox.showinfo("Нечего передавать",
                                "Откройте фото и впишите, что на нём написано.", parent=self)
            return
        items = [(ch, g) for ch, g, key in self.sheet.glyphs() if key not in self.off]
        if not items:
            messagebox.showwarning("Пусто", "Не получилось вырезать ни одной буквы.",
                                   parent=self)
            return
        count = {}
        rec = []
        for ch, g in items:
            v = count.get(ch, 0)
            count[ch] = v + 1
            rec.append([ch, v, g, None, True, None])
        self.app.receive_freehand(rec, os.path.basename(self.path or ""))
        self.lbl.configure(text="передано букв: %d — теперь «Принять распознанное» "
                                "на вкладке «Почерк»" % len(rec))


# ===================================================== соединения

LR = {"авто": None, "соединять": True, "не соединять": False}


class JoinEditor(tk.Toplevel):
    SIZE = 380

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Соединения букв — эксперимент")
        self.geometry("980x700")
        self.minsize(860, 600)
        C = _theme(app, self)
        self.items = []
        self.cur = None
        self._map = None
        self._pimg = None

        head = ttk.Frame(self)
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.v_on = tk.BooleanVar(value=bool(app.cfg.human.joins))
        ttk.Checkbutton(head, text="соединять буквы в словах", variable=self.v_on,
                        command=self._toggle_on).pack(side="left")
        ttk.Label(head, style="Hint.TLabel", text=(
            "   Зелёная точка — откуда соединение входит в букву, красная — "
            "откуда уходит к следующей. Пустые кружки — как решила программа.")
        ).pack(side="left")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.lst = tk.Listbox(body, width=14, activestyle="none", exportselection=False,
                              font=("Segoe UI", 12))
        _text_colors(app, self.lst)
        self.lst.grid(row=0, column=0, sticky="nsw", pady=4)
        self.lst.bind("<<ListboxSelect>>", lambda e: self._pick())

        mid = ttk.Frame(body)
        mid.grid(row=0, column=1, sticky="nsew", padx=10)
        self.cv = tk.Canvas(mid, width=self.SIZE, height=self.SIZE, highlightthickness=0,
                            background="#ffffff", cursor="crosshair")
        self.cv.grid(row=0, column=0, rowspan=8, sticky="nw", pady=4)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Button-3>", self._click)

        side = ttk.Frame(mid)
        side.grid(row=0, column=1, sticky="nw", padx=12, pady=4)
        ttk.Label(side, text="щелчок по линии буквы ставит:").pack(anchor="w")
        self.v_mode = tk.StringVar(value="exit")
        ttk.Radiobutton(side, text="вход (зелёная)", value="entry",
                        variable=self.v_mode).pack(anchor="w")
        ttk.Radiobutton(side, text="выход (красная)", value="exit",
                        variable=self.v_mode).pack(anchor="w")
        ttk.Label(side, style="Hint.TLabel", justify="left", text=(
            "правая кнопка — поставить\nдругую точку")).pack(anchor="w", pady=(0, 10))

        self.v_l = tk.StringVar(value="авто")
        self.v_r = tk.StringVar(value="авто")
        for text, var in (("с буквой слева:", self.v_l), ("с буквой справа:", self.v_r)):
            ttk.Label(side, text=text).pack(anchor="w", pady=(6, 0))
            cb = ttk.Combobox(side, textvariable=var, values=list(LR), state="readonly",
                              width=14)
            cb.pack(anchor="w")
            cb.bind("<<ComboboxSelected>>", lambda e: self._flags())
        ttk.Button(side, text="Точки — как решит программа",
                   command=self.reset_points).pack(fill="x", pady=(14, 2))
        ttk.Button(side, text="Настройки «слева/справа» —\nвсем начертаниям буквы",
                   command=self.flags_to_all).pack(fill="x", pady=2)
        ttk.Label(side, style="Hint.TLabel", justify="left", wraplength=250, text=(
            "Изменения сразу попадают в ваш шрифт. Чтобы они остались "
            "насовсем — «Сохранить шрифт…» на вкладке «Почерк».")
        ).pack(anchor="w", pady=(12, 0))

        low = ttk.Frame(self)
        low.pack(fill="x", padx=10, pady=(4, 10))
        row = ttk.Frame(low)
        row.pack(fill="x")
        ttk.Label(row, text="проба:").pack(side="left")
        self.v_sample = tk.StringVar(value="")
        e = ttk.Entry(row, textvariable=self.v_sample, width=40)
        e.pack(side="left", padx=6)
        e.bind("<Return>", lambda ev: self.preview())
        ttk.Button(row, text="Показать", command=self.preview).pack(side="left")
        self.pv = tk.Canvas(low, height=130, highlightthickness=0, background="#ffffff")
        self.pv.pack(fill="x", pady=(6, 0))
        self.pv.bind("<Configure>", lambda e: self.preview())

        self.fill()
        app._titlebar_for(self)

    # --------------------------------------------------------- список
    def fill(self):
        fs = self.app.fontset
        self.items = [(ch, v) for ch in sorted(fs.variants)
                      for v in range(len(fs.variants[ch]))]
        self.lst.delete(0, "end")
        for ch, v in self.items:
            self.lst.insert("end", "  %s  · %d" % (ch, v + 1))
        if not self.items:
            self.cv.delete("all")
            self.cv.create_text(self.SIZE / 2, self.SIZE / 2, width=self.SIZE - 40,
                                fill="#6b7280", font=("Segoe UI", 11), text=(
                                    "В почерке пока нет своих букв. Соединения "
                                    "встроенного шрифта ставятся сами — включите "
                                    "галочку сверху и посмотрите пробу внизу."))
            self.v_sample.set("мама мыла раму")
            return
        first = next((i for i, (ch, _v) in enumerate(self.items) if ch.isalpha()), 0)
        self.lst.selection_set(first)
        self.lst.see(first)
        self._pick()

    def glyph(self):
        if self.cur is None:
            return None
        ch, v = self.cur
        lst = self.app.fontset.variants.get(ch) or []
        return lst[v] if v < len(lst) else None

    def _pick(self):
        sel = self.lst.curselection()
        if not sel:
            return
        self.cur = self.items[sel[0]]
        g = self.glyph()
        inv = {v: k for k, v in LR.items()}
        self.v_l.set(inv[g.join_l])
        self.v_r.set(inv[g.join_r])
        ch = g.char
        self.v_sample.set(("а%sа %s%s" % (ch, ch, ch)) if ch.isalpha() and ch.islower()
                          else ("%sаша" % ch if ch.isalpha() else "мама"))
        self.draw()
        self.preview()

    # --------------------------------------------------------- буква
    def draw(self):
        c = self.cv
        c.delete("all")
        g = self.glyph()
        if g is None:
            return
        S = self.SIZE
        x0, _y0, x1, _y1 = g.bbox()
        span = max(CAP * 1.9, (max(x1, g.advance) - min(0.0, x0)) * 1.15)
        s = (S - 40) / span
        ox = (S - (max(x1, g.advance) + min(0.0, x0)) * s) / 2
        base = S * 0.68
        self._map = (ox, base, s)
        tx = lambda x: ox + x * s
        ty = lambda y: base - y * s
        for y, col in ((0, "#9db4d6"), (XH, "#d6e2f2"), (CAP, "#e8edf5")):
            c.create_line(0, ty(y), S, ty(y), fill=col)
        c.create_line(tx(0), 0, tx(0), S, fill="#f0e3e3", dash=(3, 3))
        c.create_line(tx(g.advance), 0, tx(g.advance), S, fill="#f0e3e3", dash=(3, 3))
        for st in g.strokes:
            if len(st) > 1:
                c.create_line(*[v for p in st for v in (tx(p[0]), ty(p[1]))],
                              fill="#1f2430", width=3, capstyle="round", joinstyle="round")
        ae, ax = JN.auto_points(g.strokes)
        for pt, col, fixed in ((g.entry or ae, "#18a058", g.entry is not None),
                               (g.exit or ax, "#e23b3b", g.exit is not None)):
            if pt is None:
                continue
            r = 7
            c.create_oval(tx(pt[0]) - r, ty(pt[1]) - r, tx(pt[0]) + r, ty(pt[1]) + r,
                          outline=col, width=2, fill=col if fixed else "")
        c.create_text(8, 8, anchor="nw", fill="#6b7280", font=("Segoe UI", 10),
                      text="«%s», начертание %d" % (g.char, self.cur[1] + 1))

    def _click(self, e):
        g = self.glyph()
        if g is None or not self._map:
            return
        ox, base, s = self._map
        pt = ((e.x - ox) / s, (base - e.y) / s)
        hit = JN.nearest_on(g.strokes, pt)
        if hit is None:
            return
        q = (round(hit[2][0], 2), round(hit[2][1], 2))
        mode = self.v_mode.get()
        if e.num == 3:
            mode = "entry" if mode == "exit" else "exit"
        if mode == "entry":
            g.entry = q
        else:
            g.exit = q
        self._changed()

    def _flags(self):
        g = self.glyph()
        if g is None:
            return
        g.join_l = LR[self.v_l.get()]
        g.join_r = LR[self.v_r.get()]
        self._changed()

    def reset_points(self):
        g = self.glyph()
        if g is not None:
            g.entry = g.exit = None
            self._changed()

    def flags_to_all(self):
        g = self.glyph()
        if g is None:
            return
        for o in self.app.fontset.variants.get(g.char, []):
            o.join_l, o.join_r = g.join_l, g.join_r
        self._changed()

    def _toggle_on(self):
        self.app.set_joins(bool(self.v_on.get()))
        self.preview()

    def _changed(self):
        self.draw()
        self.preview()
        self.app.rebuild()

    # --------------------------------------------------------- проба
    def preview(self):
        c = self.pv
        c.delete("all")
        text = self.v_sample.get().strip()
        W = max(200, c.winfo_width())
        H = max(60, c.winfo_height())
        if not text:
            return
        cfg = copy.deepcopy(self.app.cfg)
        pg = cfg.page
        pg.grid = False
        pg.text_angle = 0.0
        pg.sheet_w, pg.sheet_h = 400.0, 40.0
        pg.margin_left = pg.margin_right = 4.0
        pg.margin_top, pg.margin_bottom = 6.0, 2.0
        pg.size_mm, pg.autofit, pg.overflow = 12.0, False, "clip"
        pg.align, pg.first_line_indent, pg.ruling = "left", 0.0, False
        h = cfg.human
        h.joins = True
        h.enabled = False           # без разнобоя: видно именно соединения
        h.calli_style = "off"
        pages = L.build_pages(text.split("\n")[0], self.app.fontset, pg, Human(h))
        strokes = pages[0].strokes if pages else []
        pts = [p for s in strokes for p in s]
        if not pts:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        s = min((W - 30) / max(bw, 1e-6), (H - 24) / max(bh, 1e-6))
        ox = 15 - min(xs) * s
        oy = 12 + max(ys) * s
        for st in strokes:
            if len(st) > 1:
                c.create_line(*[v for p in st for v in (ox + p[0] * s, oy - p[1] * s)],
                              fill="#1f2430", width=2, capstyle="round", joinstyle="round")
        if not self.v_on.get():
            c.create_text(W - 8, 8, anchor="ne", fill="#c0392b", font=("Segoe UI", 9),
                          text="в тексте соединения выключены — здесь показаны для пробы")
