# -*- coding: utf-8 -*-
"""
Формулы в записи LaTeX: $...$ внутри строки, $$...$$ — отдельной строкой.

Формула раскладывается «как в тетради»: дробь в два этажа с чертой,
корень с крышкой над подкоренным, степени и индексы мельче и выше/ниже,
скобки \\left( \\right) растягиваются по высоте. Буквы и цифры берутся из
того же почерка, что и текст, — формула пишется той же рукой.

Поддерживается ходовое подмножество: ^ _ {} \\frac \\dfrac \\tfrac \\sqrt[n]
\\left \\right \\text \\mathrm \\operatorname \\overline \\bar \\vec \\hat
\\dot \\ddot, греческие буквы, стрелки, отношения, функции (\\sin, \\lg,
\\arctg …), \\sum \\prod \\int \\lim с пределами, пробелы \\, \\; \\quad.
Незнакомая команда пишется своим именем — формула не пропадает.

Единицы — единицы шрифта (высота заглавной CAP = 14), базовая линия 0.
"""

import math
import re

from .glyphset import CAP
from . import humanize as HM

# ------------------------------------------------------------- словари

SYMBOLS = {
    # греческие
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "varrho": "ρ", "sigma": "σ", "varsigma": "ς",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω",
    "Gamma": "Г", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "П", "Sigma": "Σ", "Upsilon": "Y", "Phi": "Ф", "Psi": "Ψ",
    "Omega": "Ω",
    # знаки
    "infty": "∞", "cdot": "·", "times": "×", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "leqslant": "≤", "ge": "≥", "geq": "≥",
    "geqslant": "≥", "ne": "≠", "neq": "≠", "approx": "≈", "equiv": "≡",
    "sim": "~", "simeq": "≈", "to": "→", "rightarrow": "→", "gets": "←",
    "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒",
    "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "uparrow": "↑",
    "downarrow": "↓", "partial": "∂", "nabla": "∇", "in": "∈",
    "forall": "∀", "exists": "∃", "emptyset": "∅", "varnothing": "∅",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "circ": "°", "degree": "°",
    "ldots": "…", "dots": "…", "cdots": "…", "prime": "′", "mid": "|",
    "vert": "|", "lvert": "|", "rvert": "|", "colon": ":", "div": ":",
    "lbrace": "{", "rbrace": "}", "langle": "<", "rangle": ">",
    "%": "%", "$": "$", "{": "{", "}": "}", "_": "_", "#": "#", "&": "&",
    "|": "∥", "ast": "*", "star": "*", "cdotp": "·",
}

# функции пишутся прямо, словом
FUNCS = {"sin", "cos", "tan", "tg", "ctg", "cot", "sec", "csc", "arcsin",
         "arccos", "arctan", "arctg", "arcctg", "sinh", "cosh", "tanh", "sh",
         "ch", "th", "cth", "lg", "ln", "log", "exp", "lim", "max", "min",
         "det", "sup", "inf", "arg", "deg", "mod", "Re", "Im", "sgn", "const"}

BIG_OPS = {"sum": "Σ", "prod": "П", "int": "∫", "oint": "∫", "lim": None}

SPACES = {",": 2.0, ":": 3.0, ">": 3.0, ";": 3.6, "!": -1.5, " ": 4.2,
          "quad": 14.0, "qquad": 28.0, "enspace": 7.0}

BIN = set("+−±∓×·*/")
REL = set("=<>≤≥≈≠≡~→←↔⇒⇔∈:")
PUNCT = set(",;")
OPEN = set("([{")
CLOSE = set(")]}")

SCRIPT = 0.68           # степени и индексы — мельче основы
FRAC_INLINE = 0.74      # дробь внутри строки
FRAC_DISPLAY = 0.9      # дробь в формуле отдельной строкой
AXIS = 6.0              # ось дробей и знаков: середина «+» и «−»


# ---------------------------------------------------------------- коробка

class Box:
    """
    Прямоугольник формулы: ширина, высота над базовой линией, глубина под
    ней и что в нём нарисовано:
      ("g", символ, x, y, sx, sy, gy0) — глиф: точка (gx, gy) шрифта
          ложится в (x + gx*sx, y + (gy - gy0)*sy);
      ("l", [(x, y), ...]) — черта (дробь, корень, надчёркивание).
    """
    __slots__ = ("w", "h", "d", "items", "kind", "limits")

    def __init__(self, w=0.0, h=0.0, d=0.0, items=None, kind="ord"):
        self.w, self.h, self.d = w, h, d
        self.items = items or []
        self.kind = kind            # ord | bin | rel | punct | open | close | op
        self.limits = False         # пределы над и под знаком (Σ в формуле-строке)

    def moved(self, dx, dy):
        out = []
        for it in self.items:
            if it[0] == "g":
                _, ch, x, y, sx, sy, gy0 = it
                out.append(("g", ch, x + dx, y + dy, sx, sy, gy0))
            else:
                out.append(("l", [(x + dx, y + dy) for (x, y) in it[1]]))
        return out


def hbox(boxes, kind="ord"):
    out = Box(kind=kind)
    x = 0.0
    for b in boxes:
        out.items.extend(b.moved(x, 0.0))
        x += b.w
        out.h = max(out.h, b.h)
        out.d = max(out.d, b.d)
    out.w = x
    return out


# ---------------------------------------------------------------- разбор

_TOK = re.compile(r"\\[A-Za-z]+\*?|\\.|\s+|.", re.S)


class _Parser:
    def __init__(self, src, metrics, display):
        self.t = _TOK.findall(src)
        self.i = 0
        self.metrics = metrics      # символ -> (advance, ymin, ymax)
        self.display = display

    # ---- токены
    def peek(self, skip_ws=True):
        while skip_ws and self.i < len(self.t) and self.t[self.i].isspace():
            self.i += 1
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        if tok is not None:
            self.i += 1
        return tok

    def raw_group(self):
        """Содержимое {…} как есть, с пробелами — для \\text."""
        if self.peek() != "{":
            tok = self.next()
            return tok or ""
        self.i += 1
        depth, buf = 1, []
        while self.i < len(self.t):
            tok = self.t[self.i]
            self.i += 1
            if tok == "{":
                depth += 1
            elif tok == "}":
                depth -= 1
                if depth == 0:
                    break
            buf.append(tok[1:] if tok.startswith("\\") and len(tok) == 2 else tok)
        return "".join(buf)

    # ---- коробки из символов
    def glyph(self, ch, s, kind=None):
        adv, ymin, ymax = self.metrics(ch)
        if kind is None:
            kind = ("bin" if ch in BIN else "rel" if ch in REL else
                    "punct" if ch in PUNCT else "open" if ch in OPEN else
                    "close" if ch in CLOSE else "ord")
        return Box(adv * s, max(ymax, 0.0) * s, max(-ymin, 0.0) * s,
                   [("g", ch, 0.0, 0.0, s, s, 0.0)], kind)

    def word(self, text, s):
        return hbox([self.glyph(c, s, "ord") if not c.isspace()
                     else Box(4.2 * s) for c in text])

    def stretched(self, ch, s, top, bottom):
        """Скобка на высоту от bottom до top."""
        if ch in (".", ""):
            return Box(0.6 * s)
        adv, ymin, ymax = self.metrics(ch)
        span = max(ymax - ymin, 1e-6)
        need = top - bottom
        if need <= span * s * 1.05:
            b = self.glyph(ch, s)
            b.kind = "open" if ch in OPEN else "close" if ch in CLOSE else "ord"
            return b
        sy = need / span
        sx = s * min(1.5, math.sqrt(sy / s))
        return Box(adv * sx, top, -bottom, [("g", ch, 0.0, bottom, sx, sy, ymin)],
                   "open" if ch in OPEN else "close" if ch in CLOSE else "ord")

    # ---- выражение
    def expr(self, s, stop=("}",)):
        atoms = []
        while True:
            tok = self.peek()
            if tok is None or tok in stop:
                break
            if tok in ("^", "_"):
                self.next()
                base = atoms.pop() if atoms else Box(0.0, 0.0, 0.0)
                atoms.append(self.scripts(base, tok, s))
                continue
            a = self.atom(s)
            if a is not None:
                atoms.append(a)
        return self.join(atoms, s)

    def join(self, atoms, s):
        """Атомы в строку с пробелами вокруг знаков, как принято в формулах."""
        k = 1.0 if s > 0.8 else 0.5
        out = []
        prev = None
        for a in atoms:
            kind = a.kind
            # минус в начале или после знака — унарный, без пробелов
            if kind == "bin" and (prev is None or prev in ("bin", "rel", "open", "punct")):
                kind = "ord"
            if prev is not None:
                gap = 0.0
                if kind == "rel" or prev == "rel":
                    gap = 3.0
                elif kind == "bin" or prev == "bin":
                    gap = 2.4
                elif prev == "punct":
                    gap = 2.2
                elif prev == "op" and kind not in ("open", "punct", "close"):
                    gap = 1.8
                if gap:
                    out.append(Box(gap * s * k))
            out.append(a)
            prev = kind
        return hbox(out)

    def group(self, s):
        """{…} или один атом."""
        if self.peek() == "{":
            self.next()
            b = self.expr(s)
            if self.peek() == "}":
                self.next()
            return b
        tok = self.peek()
        if tok is None:
            return Box()
        return self.atom(s) or Box()

    def scripts(self, base, first, s):
        sup = sub = None
        tok = first
        while True:
            b = self.group(s * SCRIPT)
            if tok == "^":
                sup = b
            else:
                sub = b
            nxt = self.peek()
            if nxt in ("^", "_") and ((nxt == "^" and sup is None) or
                                     (nxt == "_" and sub is None)):
                tok = self.next()
                continue
            break
        if base.kind == "op" and base.limits:
            return self.limits(base, sup, sub, s)
        out = Box(kind=base.kind if base.kind in ("close", "ord") else "ord")
        out.items = base.moved(0.0, 0.0)
        out.h, out.d = base.h, base.d
        x = base.w + 0.4 * s
        w = 0.0
        if sup is not None:
            up = max(6.6 * s, base.h - 4.0 * s)
            out.items += sup.moved(x, up)
            out.h = max(out.h, up + sup.h)
            w = max(w, sup.w)
        if sub is not None:
            down = max(3.4 * s, base.d + 1.2 * s)
            if sup is not None:
                down = max(down, sub.h - 3.0 * s)
            out.items += sub.moved(x, -down)
            out.d = max(out.d, down + sub.d)
            w = max(w, sub.w)
        out.w = x + w + 0.4 * s
        return out

    def limits(self, op, sup, sub, s):
        w = max(op.w, sup.w if sup else 0.0, sub.w if sub else 0.0)
        out = Box(kind="op")
        out.items = op.moved((w - op.w) / 2.0, 0.0)
        out.h, out.d, out.w = op.h, op.d, w
        if sup is not None:
            y = op.h + 1.4 * s + sup.d
            out.items += sup.moved((w - sup.w) / 2.0, y)
            out.h = y + sup.h
        if sub is not None:
            y = -(op.d + 1.4 * s + sub.h)
            out.items += sub.moved((w - sub.w) / 2.0, y)
            out.d = -y + sub.d
        return out

    # ---- конструкции
    def frac(self, s, k):
        fs = max(0.5, s * k) if s > 0.8 else max(0.45, s * 0.8)
        num = self.group(fs)
        den = self.group(fs)
        pad = 1.2 * s
        w = max(num.w, den.w) + 2 * pad
        axis = AXIS * s
        gap = 2.0 * s
        yn = axis + gap + num.d
        yd = axis - gap - den.h
        out = Box(w, yn + num.h, -(yd - den.d))
        out.items = (num.moved((w - num.w) / 2.0, yn) +
                     den.moved((w - den.w) / 2.0, yd) +
                     [("l", [(0.3 * s, axis), (w - 0.3 * s, axis)])])
        return out

    def sqrt(self, s):
        idx = None
        if self.peek() == "[":
            self.next()
            idx = self.expr(s * 0.55, stop=("]",))
            if self.peek() == "]":
                self.next()
        body = self.group(s)
        top = max(body.h, 10.0 * s) + 1.8 * s
        bottom = -max(body.d, 0.0) - 0.8 * s
        hgt = top - bottom
        lead = 0.0
        if idx is not None:
            lead = max(0.0, idx.w - 2.0 * s)
        x0 = lead
        tick = (x0, bottom + 0.42 * hgt)
        pts = [tick, (x0 + 1.6 * s, bottom + 0.5 * hgt), (x0 + 3.6 * s, bottom),
               (x0 + 6.0 * s, top), (x0 + 6.0 * s + body.w + 1.6 * s, top)]
        out = Box(x0 + 6.0 * s + body.w + 2.2 * s, top + 0.8 * s, -bottom)
        out.items = [("l", pts)] + body.moved(x0 + 6.8 * s, 0.0)
        if idx is not None:
            yi = bottom + 0.5 * hgt + 1.0 * s + idx.d
            out.items += idx.moved(0.0, yi)
            out.h = max(out.h, yi + idx.h)
        return out

    def accent(self, name, s):
        body = self.group(s)
        y = max(body.h, 9.0 * s) + 1.6 * s
        x0, x1 = 0.3 * s, max(body.w - 0.3 * s, 0.3 * s + 3.0 * s)
        out = Box(max(body.w, x1 + 0.3 * s), y + 1.2 * s, body.d)
        out.items = body.moved(0.0, 0.0)
        if name in ("overline", "bar", "widebar"):
            out.items.append(("l", [(x0, y), (x1, y)]))
        elif name in ("vec", "overrightarrow"):
            out.items.append(("l", [(x0, y), (x1, y)]))
            out.items.append(("l", [(x1 - 2.2 * s, y + 1.3 * s), (x1, y),
                                    (x1 - 2.2 * s, y - 1.3 * s)]))
            out.h = y + 1.6 * s
        elif name in ("hat", "widehat"):
            xm = (x0 + x1) / 2.0
            out.items.append(("l", [(xm - 1.8 * s, y - 0.2 * s), (xm, y + 1.4 * s),
                                    (xm + 1.8 * s, y - 0.2 * s)]))
            out.h = y + 1.8 * s
        elif name in ("tilde", "widetilde"):
            xm = (x0 + x1) / 2.0
            out.items.append(("l", [(xm - 2.0 * s, y), (xm - 1.0 * s, y + 0.9 * s),
                                    (xm + 1.0 * s, y - 0.2 * s), (xm + 2.0 * s, y + 0.7 * s)]))
        elif name in ("dot", "ddot"):
            xm = (x0 + x1) / 2.0
            xs = [xm] if name == "dot" else [xm - 1.2 * s, xm + 1.2 * s]
            r = 0.35 * s
            for xc in xs:
                out.items.append(("l", [(xc + r * math.cos(a * math.pi / 4),
                                          y + r * math.sin(a * math.pi / 4))
                                         for a in range(9)]))
        elif name == "underline":
            yb = -max(body.d, 0.0) - 1.2 * s
            out.items.append(("l", [(x0, yb), (x1, yb)]))
            out.d = -yb + 0.4 * s
            out.h = body.h
        return out

    def delim_token(self):
        tok = self.next() or "."
        if tok.startswith("\\"):
            name = tok[1:]
            return SYMBOLS.get(name, {"lbrace": "{", "rbrace": "}"}.get(name, "|"))
        return tok

    def left_right(self, s):
        l = self.delim_token()
        body = self.expr(s, stop=("}", "\\right"))
        r = "."
        if self.peek() == "\\right":
            self.next()
            r = self.delim_token()
        top = max(body.h, 11.0 * s) + 1.0 * s
        bottom = -max(body.d, 2.4 * s) - 1.0 * s
        return hbox([self.stretched(l, s, top, bottom), Box(0.4 * s), body,
                     Box(0.4 * s), self.stretched(r, s, top, bottom)])

    def atom(self, s):
        tok = self.next()
        if tok is None:
            return None
        if tok == "{":
            b = self.expr(s)
            if self.peek() == "}":
                self.next()
            return b
        if tok in ("}", "&"):
            return None
        if tok == "~":
            return Box(4.2 * s)
        if tok == "-":
            return self.glyph("−", s, "bin")
        if tok == "'":
            return self.glyph("′", s, "ord")
        if tok == "," and self.i < len(self.t) and self.t[self.i].isdigit():
            return self.glyph(",", s, "ord")     # десятичная запятая: 0,16
        if not tok.startswith("\\"):
            return self.glyph(tok, s)

        name = tok[1:]
        if name in SPACES:
            return Box(SPACES[name] * s)
        if name == "\\":                     # перенос строки в формуле
            return Box(4.2 * s)
        if name in ("frac", "dfrac", "cfrac"):
            return self.frac(s, FRAC_DISPLAY if (self.display or name != "frac")
                             else FRAC_INLINE)
        if name == "tfrac":
            return self.frac(s, FRAC_INLINE)
        if name == "sqrt":
            return self.sqrt(s)
        if name == "left":
            return self.left_right(s)
        if name in ("right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
                    "Bigl", "Bigr", "displaystyle", "textstyle", "limits",
                    "nolimits", "mathbf", "mathit", "mathbb", "mathcal",
                    "boldsymbol", "bm", "mathsf", "mathtt"):
            return Box()                     # оформление: пишем содержимое как есть
        if name in ("text", "textrm", "mathrm", "operatorname", "textit",
                    "textbf", "mbox", "rm"):
            return self.word(self.raw_group(), s)
        if name in ("overline", "bar", "vec", "hat", "tilde", "dot", "ddot",
                    "widehat", "widetilde", "overrightarrow", "underline",
                    "widebar"):
            return self.accent(name, s)
        if name in BIG_OPS:
            if name == "lim":
                b = self.word("lim", s)
            else:
                k = 1.35 if self.display else 1.1
                b = self.glyph(BIG_OPS[name], s * k, "op")
                # знак на оси формулы
                dy = AXIS * s - (b.h - b.d) / 2.0
                b = Box(b.w + 0.6 * s, b.h + dy, max(b.d - dy, 0.0),
                        b.moved(0.3 * s, dy), "op")
            b.kind = "op"
            b.limits = self.display and name in ("sum", "prod", "lim")
            if name == "lim":
                b.limits = True
            return b
        if name in FUNCS:
            b = self.word(name, s)
            b.kind = "op"
            return b
        if name in SYMBOLS:
            return self.glyph(SYMBOLS[name], s)
        return self.word(name, s)            # незнакомая команда — её имя


def layout(src, metrics, display=False):
    """Строка LaTeX (без $) -> Box в единицах шрифта."""
    p = _Parser(src, metrics, display)
    box = Box()
    parts = []
    while p.peek() is not None:
        parts.append(p.expr(1.0, stop=()))
        if p.peek() == "}":
            p.next()                          # лишняя скобка — пропускаем
    if parts:
        box = hbox(parts)
    # поля по краям, чтобы формула не липла к соседним словам
    return hbox([Box(0.6), box, Box(0.6)])


def metrics_for(fontset):
    cache = {}

    def m(ch):
        hit = cache.get(ch)
        if hit is None:
            g = fontset.pick(ch)[0]
            x0, y0, x1, y1 = g.bbox()
            if not g.strokes:
                y0, y1 = 0.0, 0.0
            hit = cache[ch] = (g.advance, y0, y1)
        return hit
    return m


# ------------------------------------------------------------ текст с $…$

_MATH = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)


def has_math(text):
    return bool(_MATH.search(text))


def split_math(text):
    """'a $x^2$ b' -> [("t", "a "), ("m", "x^2", False), ("t", " b")]."""
    out, pos = [], 0
    for m in _MATH.finditer(text):
        if m.start() > pos:
            out.append(("t", text[pos:m.start()]))
        disp = m.group(1) is not None or m.group(4) is not None
        src = next(g for g in m.groups() if g is not None)
        out.append(("m", src, disp))
        pos = m.end()
    if pos < len(text):
        out.append(("t", text[pos:]))
    return out


def tokens(line):
    """
    Строка абзаца -> слова, где формула — одно неделимое слово даже с
    пробелами внутри. Каждое слово — список частей [("t", str) | ("m", src, disp)].
    """
    words, cur = [], []
    for part in split_math(line):
        if part[0] == "m":
            cur.append(part)
            continue
        chunks = re.split(r"(\s+)", part[1])
        for ch in chunks:
            if not ch:
                continue
            if ch.isspace():
                if cur:
                    words.append(cur)
                    cur = []
            else:
                cur.append(("t", ch))
    if cur:
        words.append(cur)
    return words


# ----------------------------------------------------------------- рисование

def _line(pts, human, size_mm):
    hc = human.cfg
    if not hc.enabled or len(pts) < 2:
        return list(pts)
    amp = max(0.0, float(hc.tremor)) * size_mm * 0.5
    return HM.tremor([list(pts)], amp, size_mm * 4.0, human.rng)[0]


def render(box, fontset, human, size_mm, x_mm, base_mm, line_index):
    """
    Нарисовать формулу. -> (штрихи в мм, ширина мм, (ylo, yhi)).
    Координаты листа: X вправо, Y вверх.
    """
    k = size_mm / CAP
    hc = human.cfg
    dy = human.baseline_offset(x_mm, line_index, size_mm)
    strokes = []
    for it in box.items:
        if it[0] == "g":
            _, ch, x, y, sx, sy, gy0 = it
            g = human.pick_variant(fontset, ch)
            gs = g.strokes
            if hc.enabled and gs:
                gs = HM.humanize_glyph(gs, hc, human.rng, size_units=CAP)
            for st in gs:
                if len(st) < 2:
                    continue
                strokes.append([(x_mm + (x + gx * sx) * k,
                                 base_mm + dy + (y + (gy - gy0) * sy) * k)
                                for (gx, gy) in st])
        else:
            pts = [(x_mm + x * k, base_mm + dy + y * k) for (x, y) in it[1]]
            strokes.append(_line(pts, human, size_mm))
    return strokes, box.w * k, (base_mm - box.d * k, base_mm + box.h * k)
