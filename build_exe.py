# -*- coding: utf-8 -*-
"""
Сборка отдельного exe.

    python build_exe.py            # один файл, портативный
    python build_exe.py --dir      # папка: стартует в разы быстрее
    python build_exe.py --both

Собираем не на месте, а во временной папке с латинским путём: PyInstaller
спотыкается о кириллицу и пробелы в пути к проекту. Готовый exe кладётся
в dist/ рядом с исходниками.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "handwriter"
SRC = ["hw", "hw.py"]

# Тяжёлые пакеты, которые тянутся по цепочке, но программе не нужны.
#
# ВАЖНО: ни подмодули scipy, ни numpy.f2py отсюда исключать нельзя.
# scipy.ndimage через scipy._lib._array_api -> array_api_compat.clone_module
# подтягивает scipy.fft, scipy.linalg, numpy.f2py и прочее, и вырезание
# любого из них роняет собранный exe прямо на импорте. Проверено на живой
# сборке: с ними файл тяжелее, но запускается.
EXCLUDE = [
    "matplotlib", "pandas", "IPython", "pytest", "notebook", "jupyter",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "sphinx", "docutils",
]


def make_icon(path):
    """Нарисовать иконку своим же векторным шрифтом — внешних файлов не нужно."""
    from PIL import Image, ImageDraw
    sys.path.insert(0, ROOT)
    from hw.core import font_vector as FV

    sizes = [256, 128, 64, 48, 32, 16]
    base = 256
    im = Image.new("RGBA", (base, base), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([6, 6, base - 6, base - 6], radius=46, fill=(31, 42, 68, 255))

    # рукописная «А» белым, плюс подчёркивающий росчерк
    adv, polys = FV.get_glyph("А")
    scale = 150.0 / FV.CAP
    ox = (base - adv * scale) / 2.0 + 4
    oy = base * 0.72
    for p in polys:
        if len(p) > 1:
            d.line([(ox + x * scale, oy - y * scale) for (x, y) in p],
                   fill=(255, 255, 255, 255), width=13, joint="curve")
    d.line([(52, 202), (150, 196), (204, 208)], fill=(120, 190, 255, 255),
           width=9, joint="curve")

    im.save(path, sizes=[(s, s) for s in sizes])
    return path


def build(onefile, work, icon):
    dist = os.path.join(work, "dist")
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", NAME,
        "--windowed",                 # окно без чёрной консоли за спиной
        "--icon", icon,
        "--distpath", dist,
        "--workpath", os.path.join(work, "build"),
        "--specpath", work,
        "--onefile" if onefile else "--onedir",
    ]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    args.append(os.path.join(work, "hw.py"))

    t0 = time.time()
    r = subprocess.run(args, cwd=work)
    if r.returncode:
        raise SystemExit("PyInstaller завершился с ошибкой %d" % r.returncode)
    print("собрано за %.0f с" % (time.time() - t0))
    return dist


def stage(work):
    for item in SRC:
        s = os.path.join(ROOT, item)
        d = os.path.join(work, item)
        if os.path.isdir(s):
            shutil.copytree(s, d,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, d)


def main():
    ap = argparse.ArgumentParser(description="Сборка exe")
    ap.add_argument("--dir", action="store_true", help="собрать папкой (быстрый запуск)")
    ap.add_argument("--both", action="store_true", help="и файл, и папку")
    a = ap.parse_args()

    modes = []
    if a.both:
        modes = [True, False]
    elif a.dir:
        modes = [False]
    else:
        modes = [True]

    out_root = os.path.join(ROOT, "dist")
    os.makedirs(out_root, exist_ok=True)
    icon = os.path.join(out_root, "icon.ico")
    make_icon(icon)
    print("иконка:", icon)

    work = tempfile.mkdtemp(prefix="hwbuild_")
    try:
        stage(work)
        for onefile in modes:
            print("\n=== сборка: %s ===" % ("один файл" if onefile else "папка"))
            dist = build(onefile, work, icon)
            src = os.path.join(dist, NAME + ".exe") if onefile \
                else os.path.join(dist, NAME)
            dst = os.path.join(out_root, NAME + ".exe") if onefile \
                else os.path.join(out_root, NAME)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            if onefile:
                shutil.copy2(src, dst)
                print("готово: %s  (%.0f МБ)" % (dst, os.path.getsize(dst) / 1e6))
            else:
                shutil.copytree(src, dst)
                tot = sum(os.path.getsize(os.path.join(r, f))
                          for r, _, fs in os.walk(dst) for f in fs)
                print("готово: %s\\%s.exe  (папка %.0f МБ)" % (dst, NAME, tot / 1e6))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
