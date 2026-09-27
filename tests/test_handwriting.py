# -*- coding: utf-8 -*-
"""
Полный цикл сбора почерка без принтера и фотоаппарата.

Печатаем пропись, «вписываем» в неё буквы встроенным шрифтом, портим
результат так, как портит его телефон — перспектива, поворот, косой свет,
шум, потеря разрешения, — и проверяем, что программа находит лист,
режет его на ячейки и восстанавливает каждую букву.

Заодно ловим главную ловушку: разлиновка шаблона не должна попадать
в векторизацию вместе с буквой.
"""

import io
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import cv2
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from hw.core import sheet as S, vectorize as V, glyphset as G, font_vector as FV

fails = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  СБОЙ ") + name + (("  — " + extra) if extra else ""))
    if not cond:
        fails.append(name)


def write_letters(page, spec, page_index, dpi=200):
    """Изобразить «руку»: вписать буквы в ячейки встроенным шрифтом."""
    ppm = dpi / S.MM
    d = ImageDraw.Draw(page)
    for idx, ch in enumerate(spec.pages[page_index]):
        row, col = divmod(idx, spec.cols)
        x0, y0, x1, y1 = spec.cell_rect(row, col)
        cw, chh = x1 - x0, y1 - y0
        scale = (S.F_BASE - S.F_CAP) * chh / FV.CAP * ppm
        _adv, polys = FV.get_glyph(ch)
        gx = (x0 + 0.34 * cw + (idx % 3 - 1) * 0.7) * ppm
        gy = (y0 + S.F_BASE * chh + (idx % 2 - 0.5) * 0.6) * ppm
        for p in polys:
            pts = [(gx + x * scale, gy - y * scale) for (x, y) in p]
            if len(pts) > 1:
                d.line(pts, fill=(18, 18, 26), width=4, joint="curve")
    return page


def fake_photo(page, rotate=cv2.ROTATE_180, seed=1):
    """Испортить скан так, как это делает съёмка с рук."""
    a = np.array(page.convert("L"))
    h, w = a.shape
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[38, 60], [w - 70, 15], [w - 20, h - 45], [70, h - 18]])
    a = cv2.warpPerspective(a, cv2.getPerspectiveTransform(src, dst), (w, h),
                            borderValue=250)
    if rotate is not None:
        a = cv2.rotate(a, rotate)
    yy, xx = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    light = 0.70 + 0.30 * xx / a.shape[1] + 0.16 * yy / a.shape[0]
    noise = np.random.RandomState(seed).normal(0, 4.5, a.shape)
    a = np.clip(a.astype(np.float32) * light + noise, 0, 255).astype(np.uint8)
    return cv2.resize(a, (a.shape[1] // 2, a.shape[0] // 2))


def bent_photo(page, seed=1, bend=1.0):
    """Цветное «фото» изогнутого листа: бумага выгнута на ~3 мм посередине."""
    a = np.array(page.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    A = 0.012 * h * bend
    mx = xx + A * 0.6 * np.sin(np.pi * yy / h) * np.sin(np.pi * xx / w - np.pi / 2)
    my = yy + A * np.sin(np.pi * xx / w) * np.sin(np.pi * yy / h)
    a = cv2.remap(a, mx, my, cv2.INTER_LINEAR, borderValue=(250, 250, 250))
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[38, 60], [w - 70, 15], [w - 20, h - 45], [70, h - 18]])
    a = cv2.warpPerspective(a, cv2.getPerspectiveTransform(src, dst), (w, h),
                            borderValue=(120, 110, 100))
    a = cv2.rotate(a, cv2.ROTATE_90_CLOCKWISE)
    H, W = a.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    light = (0.62 + 0.30 * xx / W + 0.16 * yy / H)[..., None] * np.array([1.0, 0.96, 0.85])
    a = a * light + np.random.RandomState(seed).normal(0, 5, a.shape)
    a = cv2.GaussianBlur(np.clip(a, 0, 255).astype(np.uint8), (3, 3), 0.9)
    return Image.fromarray(cv2.resize(a, (W * 3 // 5, H * 3 // 5),
                                      interpolation=cv2.INTER_AREA))


def _pts(strokes):
    out = []
    for st in strokes:
        out += G.resample(st, 0.25)
    return np.array(out) if out else np.zeros((0, 2))


def hard_case(tmp, dark_print=False, bend=1.0):
    """
    Вся азбука со знаками, тонкая синяя ручка, изогнутый лист.
    Раньше здесь терялись точки (. : ; ! ё i j), дуги и наклонные штрихи
    крошились, а изгиб сдвигал буквы по высоте на 2 мм.

    dark_print — шаблон пропечатан вдвое темнее задуманного, как на
    настоящем принтере с настоящей камерой: базовая линия выходит темнее
    порога чернил, и без сверки с шаблоном в буквы лезли линии и образцы.
    """
    chars = FV.alphabet(include_latin=True)
    spec, _f = S.save_template(chars, tmp, variants=1, dpi=200, pdf=False)
    ppm = 200 / S.MM
    broken, shifts = [], []
    for pi in range(len(spec.pages)):
        page = Image.open(os.path.join(tmp, "propis_%02d.png" % (pi + 1))).convert("RGB")
        if dark_print:
            a = np.asarray(page).astype(np.float32)
            dark = np.clip(255 - (255 - a) * 2.0, 0, 255)
            dark[a.min(axis=2) < 40] = a[a.min(axis=2) < 40]     # реперы как есть
            # принтер растекается: линии в 1 px выходят толще, иначе после
            # «съёмки» они размываются обратно светлее порога
            page = Image.fromarray(dark.astype(np.uint8)).filter(ImageFilter.MinFilter(3))
        # чернила ложатся поверх печати и темнят её (умножение), а не
        # заменяют: штрих, идущий прямо по линии разлиновки, темнее обоих
        ink = Image.new("RGB", page.size, "white")
        d = ImageDraw.Draw(ink)
        for idx, ch in enumerate(spec.pages[pi]):
            row, col = divmod(idx, spec.cols)
            x0, y0, x1, y1 = spec.cell_rect(row, col)
            sc = (S.F_BASE - S.F_CAP) * (y1 - y0) / FV.CAP * ppm
            gx, gy = (x0 + 0.34 * (x1 - x0)) * ppm, (y0 + S.F_BASE * (y1 - y0)) * ppm
            for p in FV.get_glyph(ch)[1]:
                if len(p) > 1:
                    d.line([(gx + x * sc, gy - y * sc) for (x, y) in p],
                           fill=(45, 70, 165), width=3, joint="curve")
        page = ImageChops.multiply(page, ink)
        rect, info = S.detect_sheet(bent_photo(page, seed=pi + 1, bend=bend), spec,
                                    work_dpi=200)
        for c in S.slice_cells(rect, spec, pi, info["px_per_mm"]):
            st, _ = V.vectorize_image(c["image"], ink_level=0.68,
                                      template=c["template"])
            if not st:
                broken.append(c["char"])
                continue
            _adv, ns = G.normalize_strokes(st, c["baseline"], c["cap"])
            P, R = _pts(ns), _pts(FV.get_glyph(c["char"])[1])
            P = P + [R[:, 0].min() - P[:, 0].min(), 0]
            shifts.append(abs(np.median(R[:, 1]) - np.median(P[:, 1])))
            dist = np.sqrt(((R[:, None] - P[None]) ** 2).sum(-1))
            if (dist.min(1) < 1.1).mean() < 0.7 or (dist.min(0) < 1.1).mean() < 0.7:
                broken.append(c["char"])
    return len(chars), broken, shifts


def main():
    tmp = tempfile.mkdtemp(prefix="hw_hand_")
    try:
        chars = list("АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЫЬЭЮЯ")
        spec, _files = S.save_template(chars, tmp, variants=3, dpi=200, pdf=False)
        print("шаблон: символов %d, листов %d, ячейка %.1f×%.1f мм"
              % (len(chars), len(spec.pages), spec.cell_w, spec.cell_h))

        page = Image.open(os.path.join(tmp, "propis_01.png")).convert("RGB")
        page = write_letters(page, spec, 0)
        photo = fake_photo(page)
        print("«фото»: %dx%d, перспектива + поворот 180° + косой свет + шум"
              % (photo.shape[1], photo.shape[0]))

        print("\nраспознавание:")
        rect, info = S.detect_sheet(Image.fromarray(photo), spec, work_dpi=200)
        check("лист найден и выпрямлен", rect is not None)
        check("ориентация определена", info["rotations"] == 2,
              "доворотов %d" % info["rotations"])

        cells = S.slice_cells(rect, spec, 0, info["px_per_mm"])
        check("все ячейки нарезаны", len(cells) == len(spec.pages[0]),
              "%d из %d" % (len(cells), len(spec.pages[0])))

        good, empty, ruled, wrong = 0, 0, 0, 0
        for c in cells:
            strokes, _dbg = V.vectorize_image(c["image"], ink_level=0.68)
            if not strokes:
                empty += 1
                continue
            adv, ns = G.normalize_strokes(strokes, c["baseline"], c["cap"])
            ref_adv, ref = FV.get_glyph(c["char"])
            ref_horiz = any(
                (max(p[0] for p in s) - min(p[0] for p in s)) > 0.7 * ref_adv
                and (max(p[1] for p in s) - min(p[1] for p in s)) < 1.2
                for s in ref)
            leaked = False
            if not ref_horiz:
                for s in ns:
                    xs = [p[0] for p in s]
                    ys = [p[1] for p in s]
                    if (max(xs) - min(xs)) > 0.75 * adv and (max(ys) - min(ys)) < 1.2:
                        leaked = True
                        break
            if leaked:
                ruled += 1
                continue
            if not (0.55 < adv / ref_adv < 1.75):
                wrong += 1
                continue
            good += 1

        check("буквы восстановлены", good == len(cells),
              "чисто %d · пусто %d · с разлиновкой %d · плохая ширина %d"
              % (good, empty, ruled, wrong))
        check("разлиновка не просочилась ни разу", ruled == 0)

        print("\nточки над буквами:")
        for ch in "ЁЙ":
            _a, polys = FV.get_glyph(ch)
            im = Image.new("RGB", (200, 240), "white")
            d = ImageDraw.Draw(im)
            for p in polys:
                pts = [(30 + x * 9, 200 - y * 9) for (x, y) in p]
                if len(pts) > 1:
                    d.line(pts, fill="black", width=7, joint="curve")
            st, _ = V.vectorize_image(im)
            check("%s: диакритика не потерялась" % ch, len(st) >= 2,
                  "штрихов %d" % len(st))

        print("\nдиагонали при утоньшении:")
        # классический Zhang–Suen целиком стирал чистую диагональ «/»:
        # у «»» и «и» пропадали нижние плечи
        # ровная «лесенка»: в каждой строке пробег из w пикселей, сдвиг на 1 —
        # ровно такой узор давали нижние плечи «»» на фото
        for w in (3, 4, 5, 6, 7):
            m = np.zeros((60, 80), bool)
            for r in range(40):
                m[10 + r, 55 - r:55 - r + w] = True
            sk = V.thin(m)
            ys = np.nonzero(sk)[0]
            span = (ys.max() - ys.min()) if len(ys) else 0
            check("диагональ «/» с пробегом %d px пережила утоньшение" % w,
                  span >= 30, "длина скелета: %d из 40" % span)

        print("\nпетли с самопересечением:")
        # сглаживание уводило точку крепления петли внутрь неё, и нижняя
        # петля «в», «д», «з», «8» отрывалась от буквы
        # «леденец»: штрих подходит к петле, которая начинается и кончается
        # в той же точке, — как низ у рукописных «в», «д», «з»
        im = Image.new("L", (120, 170), 255)
        d8 = ImageDraw.Draw(im)
        d8.line([(60, 15), (60, 95)], fill=0, width=5)
        d8.ellipse([38, 95, 82, 139], outline=0, width=5)
        st, _ = V.vectorize_image(im)
        groups = list(range(len(st)))
        arr = [np.asarray(s, float) for s in st]
        for i in range(len(st)):
            for j in range(i + 1, len(st)):
                if min(float(np.min(np.hypot(*(arr[j] - q).T))) for q in arr[i]) < 2.5:
                    gi, gj = groups[i], groups[j]
                    groups = [gi if g == gj else g for g in groups]
        check("петля не оторвалась от штриха", len(set(groups)) == 1,
              "штрихов %d, отдельных кусков %d" % (len(st), len(set(groups))))

        print("\nизогнутый лист, синяя ручка, вся азбука со знаками:")
        n, broken, shifts = hard_case(os.path.join(tmp, "hard"))
        check("все символы восстановлены целиком", not broken,
              "%d из %d, сбой: %s" % (n - len(broken), n, "".join(broken) or "—"))
        check("изгиб не сдвигает буквы по высоте", float(np.percentile(shifts, 90)) < 1.0,
              "90%% сдвигов < %.2f ед. (заглавная = 14)" % np.percentile(shifts, 90))

        print("\nшаблон пропечатан вдвое темнее (на реальном фото было 1.85):")
        n, broken, shifts = hard_case(os.path.join(tmp, "dark"), dark_print=True,
                                      bend=0.5)
        check("разлиновка и образцы не попали в буквы", not broken,
              "%d из %d, сбой: %s" % (n - len(broken), n, "".join(broken) or "—"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nитог: %s" % ("всё в порядке" if not fails else "провалено: %s" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
