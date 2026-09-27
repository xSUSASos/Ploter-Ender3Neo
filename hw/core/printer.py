# -*- coding: utf-8 -*-
"""
Отправка G-code на принтер по USB.

Marlin общается построчно: приняв команду, он отвечает "ok". Поэтому
шлём строку, ждём "ok", шлём следующую. Это медленнее, чем очередь с
контрольными суммами, зато не переполняет буфер принтера и легко
ставится на паузу.

Работа идёт в отдельном потоке, чтобы окно программы не подвисало.
"""

import threading
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:                                   # pragma: no cover
    serial = None
    list_ports = None


def available_ports():
    """Список (устройство, описание) доступных портов."""
    if list_ports is None:
        return []
    out = []
    for p in list_ports.comports():
        out.append((p.device, "%s — %s" % (p.device, p.description or "?")))
    return out


class PrinterError(RuntimeError):
    pass


class PrinterLink:
    """
    Печать задания в фоновом потоке.

    Обратные вызовы (все необязательные, вызываются из фонового потока —
    в Tk обновляйте интерфейс через after()):
        on_progress(sent, total, line)
        on_log(text)
        on_done(ok, message)
    """

    def __init__(self, port, baud=115200, on_progress=None, on_log=None,
                 on_done=None, timeout=8.0):
        if serial is None:
            raise PrinterError("не установлен pyserial (pip install pyserial)")
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.on_progress = on_progress
        self.on_log = on_log
        self.on_done = on_done

        self._ser = None
        self._thread = None
        self._pause = threading.Event()
        self._abort = threading.Event()
        self._running = threading.Event()

    # --------------------------------------------------------- состояние
    @property
    def is_running(self):
        return self._running.is_set()

    @property
    def is_paused(self):
        return self._pause.is_set()

    def _log(self, msg):
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    # ------------------------------------------------------------ связь
    def connect(self):
        if self._ser is not None and self._ser.is_open:
            return
        self._ser = serial.Serial(self.port, self.baud, timeout=1.0)
        # плата перезагружается при открытии порта — надо переждать
        time.sleep(2.0)
        self._ser.reset_input_buffer()
        self._log("порт %s открыт на %d бод" % (self.port, self.baud))

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def _readline(self):
        raw = self._ser.readline()
        if not raw:
            return ""
        return raw.decode("utf-8", "replace").strip()

    def _send_line(self, line):
        """Отправить одну команду и дождаться ok. -> True, если приняли."""
        self._ser.write((line + "\n").encode("ascii", "replace"))
        self._ser.flush()
        t0 = time.time()
        while True:
            if self._abort.is_set():
                return False
            r = self._readline()
            if not r:
                if time.time() - t0 > self.timeout:
                    self._log("нет ответа на: %s" % line)
                    return False
                continue
            low = r.lower()
            if low.startswith("ok"):
                return True
            if low.startswith("error") or "unknown command" in low:
                self._log("принтер: %s  (на команду %s)" % (r, line))
                return False
            if low.startswith("resend") or low.startswith("rs"):
                self._ser.write((line + "\n").encode("ascii", "replace"))
                t0 = time.time()
                continue
            if low.startswith("echo:busy") or low == "wait":
                t0 = time.time()               # принтер занят — просто ждём
                continue
            if low.startswith("//") or low.startswith("echo:"):
                continue
            self._log(r)

    # ------------------------------------------------------------ печать
    def start(self, gcode_text, warm_reset=True):
        """Запустить печать в фоне."""
        if self.is_running:
            raise PrinterError("печать уже идёт")
        lines = []
        for raw in gcode_text.splitlines():
            code = raw.split(";", 1)[0].strip()
            if code:
                lines.append(code)
        if not lines:
            raise PrinterError("в задании нет ни одной команды")

        self._abort.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, args=(lines, warm_reset),
                                        daemon=True)
        self._running.set()
        self._thread.start()

    def _run(self, lines, warm_reset):
        ok, msg = False, ""
        try:
            self.connect()
            if warm_reset:
                self._ser.reset_input_buffer()
                self._send_line("M110 N0")
            total = len(lines)
            for i, ln in enumerate(lines, 1):
                while self._pause.is_set() and not self._abort.is_set():
                    time.sleep(0.1)
                if self._abort.is_set():
                    msg = "печать остановлена"
                    break
                if not self._send_line(ln):
                    if self._abort.is_set():
                        msg = "печать остановлена"
                    else:
                        msg = "принтер не принял команду: %s" % ln
                    break
                if self.on_progress:
                    try:
                        self.on_progress(i, total, ln)
                    except Exception:
                        pass
            else:
                ok, msg = True, "готово"
        except Exception as e:                          # noqa: BLE001
            msg = "%s: %s" % (type(e).__name__, e)
        finally:
            if self._abort.is_set():
                self._safe_stop()
            self._running.clear()
            self.close()
            if self.on_done:
                try:
                    self.on_done(ok, msg)
                except Exception:
                    pass

    def _safe_stop(self):
        """Поднять перо и отключить моторы после аварийной остановки."""
        try:
            for cmd in ("G91", "G0 Z5 F900", "G90", "M84"):
                self._ser.write((cmd + "\n").encode())
                self._ser.flush()
                time.sleep(0.15)
        except Exception:
            pass

    # ----------------------------------------------------------- команды
    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def abort(self):
        self._abort.set()
        self._pause.clear()

    def send_now(self, cmd):
        """
        Отправить одиночную команду вне задания — для наладки:
        подвигать оси, выставить высоту пера. Во время печати не работает.
        """
        if self.is_running:
            raise PrinterError("идёт печать — сначала пауза или стоп")
        self.connect()
        okk = self._send_line(cmd)
        self.close()
        return okk


# ------------------------------------------------------- наладочный код

def jog_gcode(axis, delta, feed=600):
    """Относительное перемещение одной оси."""
    return ["G91", "G0 %s%.3f F%d" % (axis.upper(), delta, feed), "G90"]


def calibration_gcode(cfg, z_from=3.0, z_to=-0.4, steps=8, run=25.0):
    """
    Лесенка для подбора высоты пера: несколько горизонтальных штрихов,
    каждый на своей Z. Первый различимый штрих и даёт нужный «Z письма».
    """
    m, p, pg = cfg.machine, cfg.pen, cfg.page
    x0 = pg.origin_x + 10.0
    y0 = pg.origin_y + 10.0
    out = ["G21", "G90", "M107",
           "; лесенка Z: каждый штрих на 0.1-0.5 мм ниже предыдущего",
           "; возьмите тот Z, где линия впервые стала ровной и сплошной"]
    if m.home_mode == "all":
        out.append("G28")
    elif m.home_mode == "xy":
        out.append("G28 X Y")
    if m.home_mode != "all":
        out.append("G92 Z%.3f    ; перо сейчас примерно на этой высоте" % (z_from + 2.0))
    if m.disable_soft_endstops:
        out.append("M211 S0")
    out.append("G0 Z%.2f F%d" % (max(z_from, 2.0) + 2.0, p.feed_z))
    for i in range(steps):
        z = z_from + (z_to - z_from) * i / max(1, steps - 1)
        y = y0 + i * 6.0
        out += ["G0 X%.2f Y%.2f F%d" % (x0, y, p.feed_travel),
                "G0 Z%.3f F%d" % (z, p.feed_z),
                "G1 X%.2f Y%.2f F%d" % (x0 + run, y, p.feed_draw),
                "G0 Z%.3f F%d" % (z + 3.0, p.feed_z),
                "M117 Z=%.2f" % z]
    out += ["G0 Z%.2f F%d" % (max(z_from, 2.0) + 5.0, p.feed_z),
            "M300 S880 P160", "M84"]
    return "\n".join(out) + "\n"
