# -*- coding: utf-8 -*-
"""
Точка входа.

    hw.py                       — окно программы
    hw.py text -i a.txt -o a.gcode   — командная строка

В собранном виде (PyInstaller) окно запускается без консоли, поэтому
необработанная ошибка иначе осталась бы незамеченной: пишем её в
hw-ошибка.log рядом с exe и показываем окном.
"""

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _app_dir():
    """Папка, рядом с которой лежит программа (для логов и профилей)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _report(exc_text, gui=True):
    path = os.path.join(_app_dir(), "hw-ошибка.log")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(exc_text)
    except OSError:
        path = "(не удалось записать лог)"
    if not gui:
        # в командной строке ждать нажатия кнопки некому — процесс завис бы
        sys.stderr.write(exc_text)
        sys.stderr.write("\nподробности: %s\n" % path)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Программа остановилась",
            "%s\n\nПодробности записаны в:\n%s"
            % (exc_text.strip().splitlines()[-1], path))
        root.destroy()
    except Exception:
        sys.stderr.write(exc_text)


def _ensure_console():
    """
    Оконная сборка запускается без консоли, и sys.stdout там равен None —
    любой print в режиме командной строки уронил бы программу. Поэтому
    цепляемся к консоли, из которой нас позвали, а если её нет — заводим
    свою. Совсем не вышло — пишем вывод в файл рядом с exe.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    if os.environ.get("HW_NO_CONSOLE"):
        raise_to_file = True
    else:
        raise_to_file = False
    try:
        if raise_to_file:
            raise RuntimeError("вывод принудительно в файл")
        import ctypes
        k32 = ctypes.windll.kernel32
        if not k32.AttachConsole(-1):          # -1 = консоль родителя
            k32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8",
                          errors="replace", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8",
                          errors="replace", buffering=1)
    except Exception:                                    # noqa: BLE001
        try:
            log = open(os.path.join(_app_dir(), "hw-вывод.log"), "w",
                       encoding="utf-8", errors="replace", buffering=1)
            sys.stdout = sys.stderr = log
        except OSError:
            class _Null:
                def write(self, _s):
                    pass

                def flush(self):
                    pass
            sys.stdout = sys.stderr = _Null()


def main():
    if len(sys.argv) > 1:
        _ensure_console()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass          # уже настроено выше или потоков нет вовсе

    try:
        if len(sys.argv) > 1:
            from hw.cli import main as cli_main
            return cli_main()
        from hw.gui.app import main as gui_main
        gui_main()
        return 0
    except SystemExit:
        raise
    except BaseException:                                # noqa: BLE001
        import traceback
        _report(traceback.format_exc(), gui=(len(sys.argv) <= 1))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
