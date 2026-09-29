# -*- coding: utf-8 -*-
"""Прогнать все проверки: python tests/run_all.py"""
import io, os, subprocess, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUITES = [("конвейер и G-code", "test_pipeline.py"),
          ("сбор почерка с фото", "test_handwriting.py"),
          ("окно программы", "test_gui.py"),
          ("рисунки и поворот текста", "test_drawing.py"),
          ("свободный лист и соединения букв", "test_freehand.py"),
          ("таблицы и формулы LaTeX", "test_tables_math.py")]

bad = []
for title, fn in SUITES:
    print("\n" + "=" * 62)
    print("  " + title)
    print("=" * 62)
    r = subprocess.run([sys.executable, os.path.join(HERE, fn)], cwd=ROOT)
    if r.returncode:
        bad.append(title)

print("\n" + "=" * 62)
print("ИТОГ: " + ("все проверки пройдены" if not bad else "провалено: " + ", ".join(bad)))
sys.exit(1 if bad else 0)
