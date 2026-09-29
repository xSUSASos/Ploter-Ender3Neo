# -*- coding: utf-8 -*-
"""
Все настройки приложения в одном месте.

Сохраняются/загружаются в JSON — это «профиль». Профиль можно завести
отдельно под каждую ручку, бумагу и почерк.
"""

import json
import os
from dataclasses import dataclass, field, asdict, fields


# --------------------------------------------------------------------- лист

@dataclass
class PageCfg:
    """Геометрия листа и текстового блока. Всё в миллиметрах."""
    # Физический лист. По умолчанию A5: это самый большой стандартный
    # формат, который целиком помещается на стол Ender 3 (220x220 мм).
    # A4 на такой стол не влезает ни вдоль, ни поперёк — 297 > 220.
    sheet_w: float = 148.0
    sheet_h: float = 210.0
    # где левый нижний угол листа лежит на столе принтера
    origin_x: float = 5.0
    origin_y: float = 5.0
    # поля внутри листа
    margin_left: float = 15.0
    margin_right: float = 12.0
    margin_top: float = 15.0
    margin_bottom: float = 15.0
    # поворот содержимого, град (0 или 90 — альбомная ориентация)
    rotate: float = 0.0

    # ---- типографика
    size_mm: float = 6.0          # высота ЗАГЛАВНОЙ буквы
    line_spacing: float = 1.85    # межстрочный, в долях от size_mm
    word_spacing: float = 1.0     # множитель ширины пробела
    tracking_mm: float = 0.0      # доп. межбуквенный интервал
    align: str = "left"           # left | center | right | justify
    first_line_indent: float = 0.0  # красная строка, мм
    paragraph_gap: float = 0.0    # доп. отступ между абзацами, мм

    # ---- тетрадь в клетку: строки ложатся точно на линии клетки.
    # Межстрочный = клетка × grid_every, высота букв — доля этого шага,
    # верхнее поле считается от grid_first. Размер шрифта, межстрочный и
    # автоподбор в этом режиме не действуют.
    grid: bool = False
    grid_cell: float = 5.0        # размер клетки, мм
    grid_first: float = 20.0      # от верхнего края листа до линии первой строки, мм
    grid_every: int = 1           # строка через столько клеток (1 — в каждой)
    grid_fill: float = 0.8        # высота заглавной в долях шага строк

    # ---- поворот текста на листе (не путать с rotate — это поворот листа
    # на столе). Любой угол, градусы против часовой, вокруг центра полей.
    text_angle: float = 0.0
    text_angle_fit: bool = True   # уменьшить, если после поворота не влезает в поля

    # ---- автоподбор «чтобы поместилось»
    autofit: bool = False
    autofit_min_size: float = 2.5
    autofit_max_size: float = 20.0
    # что делать, если текст не влезает: shrink | pages | clip
    overflow: str = "pages"

    # ---- линовка (нарисовать линии тетради вместе с текстом)
    ruling: bool = False
    ruling_step: float = 0.0      # 0 = равен высоте строки

    @property
    def text_w(self):
        return self.sheet_w - self.margin_left - self.margin_right

    @property
    def text_h(self):
        return self.sheet_h - self.margin_top - self.margin_bottom


# ------------------------------------------------------------------ реализм

@dataclass
class HumanCfg:
    """Насколько «живым» получится письмо. 0 = идеально ровно, как принтер."""
    enabled: bool = True
    seed: int = 0                 # 0 = каждый раз новый; иначе воспроизводимо

    # выбор варианта начертания буквы из имеющихся
    variant_mix: float = 1.0      # 0 = всегда первый вариант, 1 = случайный
    avoid_repeat: bool = True     # не ставить один вариант дважды подряд

    # геометрические искажения каждой буквы
    jitter_pos: float = 0.14      # смещение буквы, в долях от size_mm
    jitter_size: float = 0.05     # разброс размера, доля
    jitter_rot: float = 2.0       # разброс поворота, градусы
    slant: float = 6.0            # общий наклон вправо, градусы
    slant_jitter: float = 1.8     # разброс наклона по буквам, градусы

    # Дрожание пера вдоль штриха. Рабочий диапазон 0.005..0.05: выше 0.06
    # буквы начинают разваливаться. Длина волны задаётся в долях size_mm,
    # около 1.0 — волна примерно в одну букву, что и выглядит как рука.
    tremor: float = 0.018         # амплитуда, в долях от size_mm
    tremor_scale: float = 1.0     # длина волны, в долях от size_mm

    # «плавающая» строка
    baseline_wave: float = 0.07   # амплитуда волны базовой линии, доли size_mm
    baseline_drift: float = 0.06  # наклон строки целиком, доли size_mm на строку

    # интервалы
    space_jitter: float = 0.12    # разброс ширины пробела, доля
    word_slope: float = 0.0       # «съезжание» слов вниз к концу строки

    # ---- ошибки письма
    typo_rate: float = 0.0        # доля слов с опиской (0..1)
    strike_rate: float = 0.0      # доля слов, зачёркнутых и переписанных (0..1)
    strike_style: str = "line"    # line | zigzag | scribble
    blot_rate: float = 0.0        # доля букв с «кляксой» (обводка на месте)
    skip_rate: float = 0.0        # доля пропущенных штрихов (перо не пишет)

    # ---- нажим (эмулируется высотой пера Z)
    pressure: float = 0.0         # 0 = выключено; иначе амплитуда в мм
    pressure_scale: float = 3.0   # длина волны нажима, мм

    # ---- каллиграфия: толщина линии из нескольких проходов пера рядом
    #   off     — обычная линия
    #   broad   — широкое перо: толщина зависит от направления штриха
    #   pointed — острое перо: утолщение (нажим) на нисходящих штрихах
    calli_style: str = "off"
    calli_width: float = 1.0      # ширина пера / наибольший нажим, мм
    calli_angle: float = 40.0     # угол широкого пера к строке, градусы
    calli_pen: float = 0.35       # толщина линии настоящего стержня, мм

    # ---- соединения букв в слове, как в слитном письме (эксперимент).
    # Работают и при выключенном реализме: это свойство почерка, а не помарка
    joins: bool = False
    join_reach: float = 0.9       # не соединять дальше, в долях высоты заглавной


# -------------------------------------------------------------------- перо

@dataclass
class PenCfg:
    """Перо и движение. Всё, что касается Z и подач."""
    z_draw: float = 0.0           # Z, при котором перо пишет
    # Оба подъёма — НАД Z письма, а не абсолютные высоты. Раньше «Z подъёма»
    # был абсолютным, а «малый подъём» — относительным; «Z подъёма = 0» при
    # «Z письма = 1» значило «на холостых опустить перо ниже бумаги», и
    # перо волоклось по листу, разрывая его.
    z_lift: float = 2.0           # подъём над бумагой на холостых, мм
    z_travel_min: float = 0.4     # малый подъём между близкими штрихами, мм
    hop_threshold: float = 2.0    # мм: короче — не поднимать перо на полную

    feed_draw: int = 1800         # мм/мин при письме
    feed_travel: int = 4500       # мм/мин на холостых
    feed_z: int = 900             # мм/мин по Z

    # сервопривод вместо Z (порт BLTouch); на Neo занят CR Touch
    use_servo: bool = False
    servo_index: int = 0
    servo_up: int = 60
    servo_down: int = 20
    servo_delay_ms: int = 180

    dwell_down_ms: int = 0        # пауза после опускания пера
    dwell_up_ms: int = 0

    MIN_LIFT = 0.5                # ниже — перо цепляет бумагу на холостых

    def travel_z(self):
        """Высота пера на холостых переходах (абсолютная)."""
        return self.z_draw + max(float(self.z_lift), self.MIN_LIFT)

    def hop_z(self):
        """Высота короткого перескока между близкими штрихами."""
        lift = max(float(self.z_travel_min), 0.2)
        return self.z_draw + min(lift, max(float(self.z_lift), self.MIN_LIFT))


# ------------------------------------------------------------------ принтер

@dataclass
class MachineCfg:
    """Параметры станка. Значения по умолчанию — Creality Ender 3 Neo."""
    name: str = "Ender 3 Neo"
    bed_x: float = 220.0
    bed_y: float = 220.0
    max_z: float = 250.0

    home_mode: str = "xy"         # all | xy | none
    level_mode: str = "none"      # none | m420 | g29
    z_after_home: float = 5.0     # на какую высоту поднять Z после home

    disable_soft_endstops: bool = False   # M211 S0 — разрешить отрицательный Z
    motors_off_at_end: bool = True
    beep_at_end: bool = True

    # связь по USB
    port: str = ""
    baud: int = 115200


# ------------------------------------------------------------------ рисунок

@dataclass
class DrawCfg:
    """
    Рисунок на листе: картинка/SVG из файла и эскиз, нарисованный мышью.
    Размеры в миллиметрах листа, если не сказано иное.
    """
    enabled: bool = True          # печатать рисунок (выключить, не удаляя)
    # как рисунок уживается с текстом:
    #   over  — поверх, текст пишется как обычно
    #   above — рисунок вверху первого листа, текст начинается под ним
    #   alone — только рисунок, текст не пишется
    layout: str = "alone"
    page_no: int = 1              # на каком листе рисовать

    # ---- размещение картинки из файла (эскиз лежит там, где нарисован)
    width_mm: float = 0.0         # 0 и высота 0 — вписать в поля листа
    height_mm: float = 0.0
    anchor: str = "center"        # center | top | bottom | left | right | top-left ...
    offset_x: float = 0.0         # сдвиг вправо, мм
    offset_y: float = 0.0         # сдвиг вниз, мм
    rotate: float = 0.0           # поворот, градусы против часовой
    mirror: bool = False          # отразить слева направо
    gap_mm: float = 5.0           # отступ от рисунка до текста (layout=above)

    # ---- как превращать картинку в линии
    #   outline    — контуры тёмных областей
    #   centerline — по осевой линии (для линейных рисунков, раскрасок)
    #   edges      — края (для фотографий)
    #   hatch      — штриховка по тону
    #   outline+hatch — контур и штриховка вместе
    mode: str = "outline"
    threshold: float = 0.55       # светлее этого — бумага (0..1)
    invert: bool = False          # светлый рисунок на тёмном фоне
    autocontrast: bool = True
    blur: float = 1.0             # сглаживание картинки, px
    detail: int = 700             # размер обработки по большей стороне, px
    edge_sens: float = 0.5        # чувствительность к краям (режим edges)
    hatch_step: float = 1.2       # расстояние между линиями штриховки, мм
    hatch_angle: float = 45.0     # наклон штриховки на бумаге, град
    hatch_levels: int = 3         # тонов: 1 — одна штриховка, 2..4 — перекрёстная
    min_len: float = 1.0          # штрихи короче, мм, отбрасываются
    simplify: float = 0.05        # допуск упрощения линий, мм

    # ---- перо
    passes: int = 1               # сколько раз обводить каждую линию
    speed_pct: int = 100          # скорость рисования в % от подачи письма
    pen_width: float = 0.5        # толщина линии пера, мм (для превью)
    tremor: float = 0.0           # дрожание руки, мм (0 — ровно)
    optimize: bool = True         # упорядочить штрихи, чтобы меньше ездить
    join_gap: float = 0.3         # мм: ближе — не поднимать перо
    sketch_smooth: int = 5        # сглаживание линий, нарисованных мышью


# --------------------------------------------------------------------- всё

@dataclass
class Config:
    page: PageCfg = field(default_factory=PageCfg)
    human: HumanCfg = field(default_factory=HumanCfg)
    pen: PenCfg = field(default_factory=PenCfg)
    machine: MachineCfg = field(default_factory=MachineCfg)
    draw: DrawCfg = field(default_factory=DrawCfg)
    font_path: str = ""           # путь к JSON пользовательского почерка
    curve_flatness: float = 0.35  # точность тесселяции кривых, ед. шрифта

    # ------------------------------------------------------------- json
    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        cfg = cls()
        for sect in ("page", "human", "pen", "machine", "draw"):
            sub = d.get(sect) or {}
            obj = getattr(cfg, sect)
            known = {f.name for f in fields(obj)}
            for k, v in sub.items():
                if k in known:
                    setattr(obj, k, v)
        # старые профили хранили абсолютный «Z подъёма»: переводим в подъём
        # над бумагой; бессмысленное значение (не выше Z письма) — в 2 мм
        pen = d.get("pen") or {}
        if "z_up" in pen and "z_lift" not in pen:
            lift = float(pen["z_up"]) - float(cfg.pen.z_draw)
            cfg.pen.z_lift = lift if lift >= PenCfg.MIN_LIFT else PenCfg.z_lift
        for k in ("font_path", "curve_flatness"):
            if k in d:
                setattr(cfg, k, d[k])
        return cfg

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ------------------------------------------------------------- пресеты

SHEETS = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    "Letter": (215.9, 279.4),
    "Половина A4 (альбом)": (297.0, 105.0),
}


def preset_neat():
    """Аккуратный почерк — почти без искажений."""
    h = HumanCfg()
    h.jitter_pos, h.jitter_rot = 0.06, 1.0
    h.tremor, h.tremor_scale = 0.008, 1.2
    h.baseline_wave, h.slant_jitter = 0.035, 0.8
    return h


def preset_normal():
    return HumanCfg()


def preset_sloppy():
    """Небрежный почерк — заметное дрожание и разнобой."""
    h = HumanCfg()
    h.jitter_pos, h.jitter_size, h.jitter_rot = 0.20, 0.09, 3.5
    h.tremor, h.tremor_scale = 0.035, 0.85
    h.baseline_wave, h.baseline_drift = 0.14, 0.09
    h.slant, h.slant_jitter, h.space_jitter = 8.0, 3.0, 0.20
    return h


def preset_hurried():
    """Второпях: сильный наклон, съезжающая строка, редкие помарки."""
    h = preset_sloppy()
    h.tremor = 0.045
    h.slant, h.word_slope = 12.0, 0.05
    h.strike_rate, h.blot_rate = 0.03, 0.012
    return h


def preset_calligraphy():
    """Каллиграфия широким пером: ровно, с наклоном, контраст толщин."""
    h = preset_neat()
    h.jitter_pos, h.jitter_rot, h.jitter_size = 0.03, 0.5, 0.02
    h.tremor, h.baseline_wave, h.baseline_drift = 0.004, 0.015, 0.0
    h.slant, h.slant_jitter, h.space_jitter = 8.0, 0.4, 0.05
    h.calli_style, h.calli_width, h.calli_angle = "broad", 1.0, 40.0
    return h


def preset_copperplate():
    """Острое перо, как в прописях: сильный наклон, нажим на нисходящих."""
    h = preset_calligraphy()
    h.slant = 22.0
    h.calli_style, h.calli_width = "pointed", 0.9
    return h


HUMAN_PRESETS = {
    "Аккуратно": preset_neat,
    "Обычно": preset_normal,
    "Небрежно": preset_sloppy,
    "Второпях": preset_hurried,
    "Каллиграфия": preset_calligraphy,
    "Каллиграфия, острое перо": preset_copperplate,
}
