import ctypes
import datetime as dt
import os
import queue
import re
import socket
import subprocess
import threading
import time
import traceback
import winsound
from pathlib import Path

import serial
from serial.tools import list_ports
import tkinter as tk
from tkinter import messagebox, ttk
import updater

try:
    import ui_theme
except Exception:
    ui_theme = None

APP_VERSION = "1.0.2"  # FN_MASTER_V102


class PlaceholderEntry(tk.Entry):
    def __init__(self, master, textvariable, placeholder, **kwargs):
        super().__init__(
            master,
            textvariable=textvariable,
            fg="#94A3B8",
            insertbackground="white",
            bg="white",
            relief="flat",
            **kwargs
        )
        self.variable = textvariable
        self.placeholder = placeholder
        self.showing_placeholder = True
        self.variable.set(placeholder)
        self.bind("<FocusIn>", self.focus_in)
        self.bind("<FocusOut>", self.focus_out)

    def focus_in(self, event=None):
        if self.showing_placeholder:
            self.variable.set("")
            self.config(fg="#0F172A")
            self.showing_placeholder = False

    def focus_out(self, event=None):
        if not self.variable.get().strip():
            self.showing_placeholder = True
            self.variable.set(self.placeholder)
            self.config(fg="#94A3B8")

try:
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)
        ctypes.windll.kernel32.FreeConsole()
except Exception:
    pass


# =====================================================================
# ЭТАЛОННЫЕ КОНСТАНТЫ
# =====================================================================
VID = 0x0483
PID = 0x5740
BAUD = 57600

INN = "9729777779"
OFD_NAME = "Платформа ОФД"
OFD_INN = "9715260691"
MAIL = "check@detmir.ru"
FNS = "www.nalog.gov.ru"

SNO = 1          
MODE = 32832     
DOPMODE = 2      
FFD = 4          

MIN_FW = (665, 4, 44)

NETWORK = {
    77:  ("URL ОФД",   "ofdp.platformaofd.ru", None),
    78:  ("Порт ОФД",  "21101",                None),
    201: ("IP ОИСМ",   "185.170.204.91",       "ofdp.platformaofd.ru"),
    202: ("Порт ОИСМ", "21102",                None),
    203: ("URL ОИСМ",  "ofdp.platformaofd.ru", None),
    91:  ("IP ОКП",    "31.44.83.174",         "prod01.okp-fn.ru"),
    92:  ("Порт ОКП",  "26101",                None),
}

ERRORS = {
    "00": "Успешно",
    "01": "Функция невыполнима при данном статусе ККТ (проверьте смену / начало работы)",
    "02": "Неверный номер функции в команде",
    "03": "Некорректный формат или параметр команды",
    "06": "Неверный пароль связи",
    "07": "Ошибка контрольной суммы (CRC)",
    "08": "Конец бумаги в принтере (заправьте чековую ленту!)",
    "09": "Принтер не готов (закройте крышку принтера!)",
    "0A": "Текущая смена открыта более 24 часов",
    "0B": "Разница во времени ККТ и ПК больше 8 минут! Исправьте время в Windows вручную.",
    "0C": "Вводимая дата/время раньше последней фискальной операции в ФН!",
    "0F": "Для выполнения операции необходимо закрыть смену!",
    "12": "Ошибка посылки данных в ОФД",
    "20": "Фатальная ошибка ККТ!",
    "41": "Код 41 ФН (при закрытом архиве ФН = норма, отчёт ушёл в ОФД)",
    "42": "Некорректное состояние ФН",
    "43": "Аппаратная ошибка ФН",
    "44": "Ошибка криптографического сопроцессора (КС) ФН",
    "45": "Исчерпан срок действия ФН",
    "46": "ФН переполнен",
    "47": "Неверные дата или время в команде для ФН!",
    "48": "Нет запрошенных данных в ФН",
    "54": "Очередь документов ОФД > 30 дней или архив заполнен на 90%",
    "56": "Продолжительность смены ФН более 24 часов",
    "61": "Нет связи с ФН (проверьте шлейф и разъём ФН!)",
    "62": "Ошибка обмена с ФН (проверьте контакт разъёма ФН)",
}

FN_PHASES = {
    0:  "Фаза 0 (Настройка)",
    1:  "Фаза 1 (Новый ФН — готов к рег.)",
    3:  "Фаза 3 (Фискальный режим)",
    7:  "Фаза 7 (Постфискальный режим)",
    15: "Фаза 15 (Архив ФН закрыт)",
}


# =====================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================================
def timestamp():
    return dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def format_kkt_date(d_str, fn_closed=False):
    if not d_str or d_str == "000000":
        return "Архив закрыт" if fn_closed else "Не читается (ФН закрыт)"
    if len(d_str) == 6 and d_str.isdigit():
        return f"{d_str[0:2]}.{d_str[2:4]}.20{d_str[4:6]}"
    return d_str


# FN_MASTER_V101_FIXES
def format_fn_expiry(raw_value, phase, fn_closed=False):
    """Преобразует срок действия ФН в понятную дату."""
    raw = str(raw_value or "").strip()

    if phase == 1:
        return "Будет установлен при фискализации"

    if fn_closed or phase == 15:
        return "Архив закрыт"

    if not raw or raw == "000000":
        return "Не определён"

    try:
        # Резервный формат: ДДММГГ.
        if len(raw) == 6 and raw.isdigit():
            parsed = dt.datetime.strptime(raw, "%d%m%y")
            return parsed.strftime("%d.%m.%Y")

        # Основной формат: количество дней с 01.01.2000.
        if raw.isdigit():
            days = int(raw)
            parsed = (
                dt.datetime(2000, 1, 1)
                + dt.timedelta(days=days)
            )

            # Отсекаем явно ошибочные значения вроде 1.
            if parsed < dt.datetime(2016, 1, 1):
                return "Не определён"

            return parsed.strftime("%d.%m.%Y")

    except (ValueError, OverflowError):
        pass

    return "Не определён"


def format_kkt_time(t_str):
    if len(t_str) == 6 and t_str.isdigit():
        return f"{t_str[0:2]}:{t_str[2:4]}:{t_str[4:6]}"
    return t_str or "—"


def dns_addresses(host):
    try:
        return sorted({
            item[4][0]
            for item in socket.getaddrinfo(host, None, socket.AF_INET)
        })
    except Exception as exc:
        return [f"Ошибка DNS: {exc}"]


def valid_fio(text):
    text = text.strip()
    forbidden = (
        "ФН", "ЗАКРЫТ", "РЕГИСТР", "ОТЧЕТ", "ОТЧЁТ",
        "КАССИР", "АДМИН", "ИНЖЕНЕР", "ТЕСТ", "ФАМИЛИЯ"
    )
    if any(w in text.upper() for w in forbidden):
        return False
    return bool(
        2 <= len(text) <= 60
        and re.fullmatch(
            r"[A-Za-zА-Яа-яЁё.\-]+(?: [A-Za-zА-Яа-яЁё.\-]+){0,2}",
            text
        )
    )


class KKTError(Exception):
    pass


# =====================================================================
# КЛАСС ОБМЕНА С ККТ ПО ПРОТОКОЛУ ПИРИТ / ВИКИ ПРИНТ
# =====================================================================
class KKT:
    def __init__(self, log_func):
        self.log = log_func
        self.ser = None
        self.port = None
        self.last_diag = {}

    @staticmethod
    def ports():
        rows = []
        for p in list_ports.comports():
            desc_low = (p.description or "").lower()
            is_bt = "bluetooth" in desc_low or "bth" in (p.hwid or "").lower()
            is_viki = (p.vid == VID and p.pid == PID)
            preferred = is_viki or ("COM50" in p.device.upper())
            rows.append((p.device, p.description, is_viki, preferred, is_bt))
        return sorted(rows, key=lambda x: (not x[2], not x[3], x[4], x[0]))

    def connect(self, port, silent=False):
        """
        Подключение с автоматическим переподключением (до 3 попыток),
        если Windows выдаёт PermissionError(13).
        """
        self.close()
        last_exc = None

        for attempt in range(1, 4):
            try:
                if attempt > 1:
                    if not silent:
                        self.log(f"Повторная попытка подключения к {port} ({attempt}/3) через 1.5 сек...", "WARN")
                    time.sleep(1.5)

                self.ser = serial.Serial(port, BAUD, timeout=20)
                self.port = port

                self.ser.timeout = 5
                self.ser.write(b"\x05")
                ack = self.ser.read(1)
                if ack == b"\x06" and not silent:
                    self.log(f"Порт {port}: получен сигнал 0x06 (ККТ на связи)", "SUCCESS")

                err, fields = self.ask("00", timeout=10, quiet=True)
                if err is not None and fields:
                    if not silent:
                        self.log(f"Успешно подключено к ККТ на {port} (скорость {BAUD})", "SUCCESS")
                    return

                self.close()
                last_exc = f"Порт {port} открыт, но ККТ не ответила на команду 0x00."
            except Exception as exc:
                self.close()
                last_exc = exc
                if not silent:
                    self.log(f"Попытка {attempt}/3 не удалась: {exc}", "WARN")

        raise KKTError(
            f"Не удалось подключиться к {port} после 3 попыток.\n"
            f"Причина: {last_exc}\n\n"
            "Проверьте: закрыты ли Fito, ComProxy и кассовая программа."
        )

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def ask(self, cmd, f=(), timeout=20, quiet=False):
        if not self.ser or not self.ser.is_open:
            raise KKTError("Нет подключения к ККТ")

        data = b"".join(str(x).encode("cp866", "replace") + b"\x1c" for x in f)
        body = b"PIRI!" + cmd.encode("ascii") + data + b"\x03"
        c = 0
        for b in body:
            c ^= b

        try:
            self.ser.timeout = timeout
            self.ser.reset_input_buffer()
            self.ser.write(b"\x02" + body + b"%02X" % c)
            raw = self.ser.read_until(b"\x03")
            if not raw.endswith(b"\x03"):
                if not quiet:
                    self.log(f"Команда 0x{cmd}: ТАЙМАУТ ответа от ККТ", "ERROR")
                return None, []
            raw += self.ser.read(2)
        except Exception as exc:
            raise KKTError(f"Обрыв связи по порту {self.port}: {exc}") from exc

        try:
            b2 = raw[raw.find(b"\x02") + 1:raw.rfind(b"\x03")]
            err = b2[3:5].decode("ascii", "replace")
            ff = [x.decode("cp866", "replace") for x in b2[5:].split(b"\x1c")][:-1]
        except Exception as exc:
            if not quiet:
                self.log(f"Команда 0x{cmd}: ошибка разбора ответа ({exc})", "ERROR")
            return None, []

        if err != "00" and not quiet:
            desc = ERRORS.get(err, "см. документацию протокола")
            self.log(f"Команда 0x{cmd}: ОШИБКА {err} — {desc}", "WARN")
            if err in ("01", "03"):
                ext_e, ext_f = self.ask("06", [1], quiet=True)
                if ext_e == "00" and len(ext_f) > 1:
                    self.log(f"  -> Расширенная причина ККТ (0x06): код {ext_f[1]}", "WARN")
        return err, ff

    def info(self, n):
        e, f = self.ask("02", [n], quiet=True)
        return f[1:] if f else []

    def service_info(self, n):
        e, f = self.ask("05", [n], quiet=True)
        return f[1:] if f else []

    def table(self, n, i=0):
        e, f = self.ask("11", [n, i], quiet=True)
        return f[0] if f else ""

    def set_table(self, n, i, val):
        e, _ = self.ask("12", [n, i, val])
        return e

    def fn(self, n, quiet=False):
        e, f = self.ask("78", [n], quiet=quiet)
        if e != "00":
            return e, []
        vals = f[1:] if f and f[0] == str(n) else (f if f else [])
        return e, vals

    def status(self):
        e, f = self.ask("00", quiet=True)
        if e != "00" or len(f) < 3:
            raise KKTError("Не удалось прочитать статус ККТ (0x00)")
        return tuple(map(int, f[:3]))

    def unsent(self):
        e, f = self.fn(7, quiet=True)
        if e != "00":
            return None, e
        return (f[1] if len(f) > 1 else None), e

    def begin_work(self):
        now = dt.datetime.now()
        e, _ = self.ask("10", [now.strftime("%d%m%y"), now.strftime("%H%M%S")])
        if e != "00":
            raise KKTError(
                f"Команда «Начало работы» (0x10) вернула ошибку {e}.\n"
                "Проверьте совпадение даты и времени на кассе!"
            )

    def full_diagnostics(self):
        d = {}
        fatal, current, doc = self.status()
        d["fatal"] = fatal
        d["current"] = current
        d["doc"] = doc
        d["shift_open_kkt"] = bool(current & 4)
        d["fn_closed_flag"] = bool(current & 16)
        d["need_begin_work"] = bool(current & 1)

        v = self.info(1); d["serial"] = v[0] if v else "?"
        v = self.info(3); d["inn"] = v[0].strip() if v else "?"
        v = self.info(4); d["rn"] = v[0].strip() if v else "?"
        v = self.info(2)
        d["fw_tuple"] = tuple(int(x) for x in v[:3] if x.isdigit()) if len(v) >= 3 else (0, 0, 0)
        d["fw_str"] = ".".join(v[:3]) if v else "?"

        # Срок действия ФН: сначала пробуем 0x78/3 (напрямую из ФН), затем 0x02/14
        e_exp, v_exp = self.fn(3, quiet=True)
        raw_exp = v_exp[0] if (e_exp == "00" and v_exp and v_exp[0] != "000000") else ""
        if not raw_exp:
            v14 = self.info(14)
            raw_exp = v14[0] if v14 else "000000"
        d["fn_expire_raw"] = raw_exp

        e, clk = self.ask("13", quiet=True)
        now = dt.datetime.now()
        d["pc_time"] = now
        if e == "00" and len(clk) >= 2:
            d["kkt_time_str"] = f"{format_kkt_date(clk[0])} {format_kkt_time(clk[1])}"
            try:
                kkt_dt = dt.datetime.strptime(clk[0] + clk[1], "%d%m%y%H%M%S")
                d["time_diff_sec"] = abs(int((now - kkt_dt).total_seconds()))
            except Exception:
                d["time_diff_sec"] = 9999
        else:
            d["kkt_time_str"] = "?"
            d["time_diff_sec"] = 9999

        v = self.service_info(7)
        d["battery_v"] = int(v[0]) / 1000.0 if v and v[0].isdigit() else 0.0

        e, prn = self.ask("04", quiet=True)
        d["printer_status"] = int(prn[0]) if e == "00" and prn and prn[0].isdigit() else -1

        e, v = self.fn(1, quiet=True); d["fn_num"] = v[0] if e == "00" and v else f"Ошибка ({e})"
        e, v = self.fn(2, quiet=True)
        if e == "00" and len(v) >= 3:
            d["fn_phase"] = int(v[0]) & 15
            d["fn_warn"] = int(v[2])
        else:
            d["fn_phase"] = 15 if d["fn_closed_flag"] else -1
            d["fn_warn"] = -1

        is_closed = d["fn_closed_flag"] or (d["fn_phase"] == 15)
        d["fn_expire_str"] = format_fn_expiry(raw_exp, d["fn_phase"], is_closed)

        e, v = self.fn(6, quiet=True)
        if e == "00" and len(v) >= 2:
            d["fn_shift_num"] = v[0]
            d["shift_open_fn"] = (v[1] == "1")
        else:
            d["fn_shift_num"] = "—"
            d["shift_open_fn"] = False

        u_val, u_err = self.unsent()
        d["unsent"] = u_val
        d["unsent_err"] = u_err

        e, v = self.fn(14, quiet=True)
        d["fn_ver"] = v[0].strip() if e == "00" and v else "?"
        e, v = self.fn(22, quiet=True)
        d["ffd_cur"] = v[0] if e == "00" and v else "?"
        d["ffd_max"] = v[1] if e == "00" and len(v) > 1 else "?"

        net_bad = []
        net_details = {}
        for num, (name, want, _) in NETWORK.items():
            cur_val = self.table(num)
            ok = (cur_val == want)
            if not ok:
                net_bad.append(num)
            net_details[num] = (name, cur_val, want, ok)
        d["net_bad"] = net_bad
        d["net_details"] = net_details

        d["name1"] = self.table(30, 0)
        d["name2"] = self.table(30, 1)
        d["addr1"] = self.table(30, 2)
        d["addr2"] = self.table(30, 3)
        d["place"] = self.table(85)
        d["ofd_name"] = self.table(81)
        d["ofd_inn"] = self.table(71).strip()
        d["email"] = self.table(82)
        d["fns"] = self.table(83)

        self.last_diag = d
        return d


# =====================================================================
# ВИЗУАЛЬНЫЙ РЕДАКТОР РЕКВИЗИТОВ В ВИДЕ КАССОВОГО ЧЕКА
# =====================================================================
class ReceiptRequisitesEditor(tk.Toplevel):
    def __init__(self, parent, kkt, diag_data, on_saved_callback):
        super().__init__(parent)
        self.title("Просмотр и редактирование реквизитов ККТ (Вид чека)")
        self.geometry("620x680")
        self.minsize(540, 560)
        self.configure(bg="#CBD5E1")
        self.transient(parent)
        self.grab_set()

        self.kkt = kkt
        self.d = diag_data
        self.on_saved = on_saved_callback

        # Нижняя панель кнопок (прикреплена первой снизу)
        btn_bar = tk.Frame(self, bg="#1E293B", padx=16, pady=10)
        btn_bar.pack(side="bottom", fill="x")

        tk.Button(
            btn_bar, text="💾 Сохранить изменения в ККТ", font=("Segoe UI", 10, "bold"),
            bg="#16A34A", fg="white", activebackground="#15803D", activeforeground="white",
            relief="flat", padx=16, pady=6, cursor="hand2", command=self.save_to_kkt
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        tk.Button(
            btn_bar, text="Закрыть без сохранения", font=("Segoe UI", 10),
            bg="#475569", fg="white", relief="flat", padx=14, pady=6, cursor="hand2",
            command=self.destroy
        ).pack(side="right")

        # "Бумажная лента чека" по центру
        paper_outer = tk.Frame(self, bg="#CBD5E1", padx=28, pady=14)
        paper_outer.pack(side="top", fill="both", expand=True)

        paper = tk.Frame(paper_outer, bg="#FFFDF9", highlightbackground="#94A3B8", highlightthickness=1, padx=22, pady=16)
        paper.pack(fill="both", expand=True)

        tk.Label(
            paper, text="*** КАССОВЫЙ ЧЕК / РЕКВИЗИТЫ ККТ ***",
            font=("Consolas", 11, "bold"), bg="#FFFDF9", fg="#0F172A"
        ).pack(fill="x", pady=(0, 2))

        tk.Label(
            paper, text="================================================",
            font=("Consolas", 9), bg="#FFFDF9", fg="#64748B"
        ).pack(fill="x", pady=(0, 8))

        # Неизменяемые заводские/регистрационные поля (для вида чека)
        ro_frame = tk.Frame(paper, bg="#FFFDF9")
        ro_frame.pack(fill="x", pady=(0, 8))
        tk.Label(
            ro_frame,
            text=f"ЗН ККТ: {self.d.get('serial', '?')}      РН ККТ: {self.d.get('rn', '?')}\n"
                 f"ИНН ПОЛЬЗОВАТЕЛЯ: {self.d.get('inn', '?')}   ФН №: {self.d.get('fn_num', '?')}",
            font=("Consolas", 9, "bold"), bg="#FFFDF9", fg="#334155", justify="center"
        ).pack(fill="x")

        tk.Label(
            paper, text="------------------------------------------------",
            font=("Consolas", 9), bg="#FFFDF9", fg="#64748B"
        ).pack(fill="x", pady=(0, 8))

        # Редактируемые поля в стиле строк чека с отступами
        self.vars = {}
        fields_spec = [
            ("name1",    "Наименование (стр. 1) [Табл.30/0]:", self.d.get("name1", "")),
            ("name2",    "Наименование (стр. 2) [Табл.30/1]:", self.d.get("name2", "")),
            ("addr1",    "Адрес расчётов (стр. 1) [Табл.30/2]:", self.d.get("addr1", "")),
            ("addr2",    "Адрес расчётов (стр. 2) [Табл.30/3]:", self.d.get("addr2", "")),
            ("place",    "Место расчётов [Табл.85]:",           self.d.get("place", "")),
            ("ofd_name", "Оператор ФД (ОФД) [Табл.81]:",        self.d.get("ofd_name", OFD_NAME)),
            ("ofd_inn",  "ИНН ОФД [Табл.71]:",                  self.d.get("ofd_inn", OFD_INN)),
            ("email",    "Эл. почта отправителя [Табл.82]:",    self.d.get("email", MAIL)),
            ("fns",      "Сайт ФНС [Табл.83]:",                 self.d.get("fns", FNS)),
        ]

        form = tk.Frame(paper, bg="#FFFDF9")
        form.pack(fill="both", expand=True)

        for key, caption, init_val in fields_spec:
            row = tk.Frame(form, bg="#FFFDF9")
            row.pack(fill="x", pady=3)
            tk.Label(
                row, text=caption, font=("Consolas", 9, "bold"),
                bg="#FFFDF9", fg="#475569", anchor="w"
            ).pack(fill="x")
            var = tk.StringVar(value=init_val)
            ent = tk.Entry(
                row, textvariable=var, font=("Consolas", 10, "bold"),
                bg="#F8FAFC", fg="#0F172A", relief="solid", bd=1, justify="center"
            )
            ent.pack(fill="x", ipady=2, padx=12)
            self.vars[key] = var

        tk.Label(
            paper, text="================================================",
            font=("Consolas", 9), bg="#FFFDF9", fg="#64748B"
        ).pack(fill="x", pady=(8, 0))

    def save_to_kkt(self):
        if self.d.get("shift_open_kkt") or self.d.get("shift_open_fn"):
            messagebox.showerror("Смена открыта", "Нельзя менять таблицы реквизитов при открытой смене!", parent=self)
            return

        mapping = [
            ("name1",    30, 0, "Наименование (стр. 1)"),
            ("name2",    30, 1, "Наименование (стр. 2)"),
            ("addr1",    30, 2, "Адрес (стр. 1)"),
            ("addr2",    30, 3, "Адрес (стр. 2)"),
            ("place",    85, 0, "Место расчётов"),
            ("ofd_name", 81, 0, "Название ОФД"),
            ("ofd_inn",  71, 0, "ИНН ОФД"),
            ("email",    82, 0, "Email отправителя"),
            ("fns",      83, 0, "Сайт ФНС"),
        ]

        changed = 0
        for key, tbl, row_idx, label in mapping:
            new_val = self.vars[key].get().strip()
            old_val = (self.d.get(key) or "").strip()
            if new_val != old_val:
                err = self.kkt.set_table(tbl, row_idx, new_val)
                if err != "00":
                    messagebox.showerror(
                        "Ошибка записи",
                        f"Не удалось записать поле «{label}» (таблица {tbl}, ряд {row_idx}).\nКод ошибки ККТ: {err}",
                        parent=self
                    )
                    return
                changed += 1

        messagebox.showinfo(
            "Реквизиты сохранены",
            f"Запись в память ККТ успешно завершена!\nОбновлено полей: {changed}.",
            parent=self
        )
        self.destroy()
        if self.on_saved:
            self.on_saved()


# =====================================================================
# ОКНО ПОДТВЕРЖДЕНИЯ КРИТИЧЕСКИХ ОПЕРАЦИЙ
# =====================================================================
class ConfirmMasterDialog(tk.Toplevel):
    def __init__(self, parent, title, header_text, header_bg, details_list, confirm_word, rn_from_kkt=None):
        super().__init__(parent)
        self.title(title)
        available_height = max(400, self.winfo_screenheight() - 120)
        dialog_height = min(540, max(420, 300 + len(details_list) * 22))
        dialog_height = min(dialog_height, available_height)
        self.geometry(f"700x{dialog_height}")
        self.minsize(600, 400)
        self.resizable(True, True)
        self.configure(bg="#F8FAFC")
        self.transient(parent)
        self.grab_set()

        self.result = None
        self.confirm_word = confirm_word
        self.rn_from_kkt = rn_from_kkt

        hdr = tk.Frame(self, bg=header_bg, padx=14, pady=8)
        hdr.pack(side="top", fill="x")
        tk.Label(
            hdr, text=header_text, font=("Segoe UI", 11, "bold"),
            fg="white", bg=header_bg, anchor="w"
        ).pack(fill="x")

        bottom_panel = tk.Frame(self, bg="#E2E8F0", highlightbackground="#94A3B8", highlightthickness=1, padx=16, pady=10)
        bottom_panel.pack(side="bottom", fill="x")

        self.rn_var = tk.StringVar()
        if rn_from_kkt is not None:
            rn_row = tk.Frame(bottom_panel, bg="#E2E8F0")
            rn_row.pack(fill="x", pady=3)
            tk.Label(
                rn_row, text="1. РН ККТ (16 цифр с чека):", width=28, anchor="w",
                font=("Segoe UI", 9, "bold"), bg="#E2E8F0", fg="#0F172A"
            ).pack(side="left")
            self.rn_entry = ttk.Entry(rn_row, textvariable=self.rn_var, font=("Consolas", 11, "bold"), width=20)
            self.rn_entry.pack(side="left", padx=(0, 8))
            tk.Button(
                rn_row, text=f"📋 Вставить {rn_from_kkt}", font=("Segoe UI", 9, "bold"),
                bg="#DBEAFE", fg="#1E40AF", relief="groove", padx=8, pady=2,
                command=lambda: self.rn_var.set(rn_from_kkt)
            ).pack(side="left")

        word_row = tk.Frame(bottom_panel, bg="#E2E8F0")
        word_row.pack(fill="x", pady=4)
        step_num = "2." if rn_from_kkt is not None else "1."
        tk.Label(
            word_row, text=f"{step_num} Введите фразу «{confirm_word}»:", width=28, anchor="w",
            font=("Segoe UI", 9, "bold"), bg="#E2E8F0", fg="#DC2626"
        ).pack(side="left")
        self.word_var = tk.StringVar()
        self.word_entry = ttk.Entry(word_row, textvariable=self.word_var, font=("Consolas", 11, "bold"), width=20)
        self.word_entry.pack(side="left", padx=(0, 8))
        tk.Label(
            word_row, text="(и нажмите Enter или кнопку ниже)",
            font=("Segoe UI", 8), bg="#E2E8F0", fg="#475569"
        ).pack(side="left")

        btn_row = tk.Frame(bottom_panel, bg="#E2E8F0")
        btn_row.pack(fill="x", pady=(8, 0))

        self.ok_btn = tk.Button(
            btn_row, text=f"✅ ПОДТВЕРДИТЬ И ВЫПОЛНИТЬ: {confirm_word}",
            font=("Segoe UI", 11, "bold"),
            bg=header_bg, fg="white", activebackground="#0F172A", activeforeground="white",
            padx=16, pady=7, relief="flat", cursor="hand2", command=self.on_ok
        )
        self.ok_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        tk.Button(
            btn_row, text="❌ Отмена", font=("Segoe UI", 10, "bold"),
            bg="white", fg="#334155", padx=18, pady=7, relief="groove", cursor="hand2",
            command=self.on_cancel
        ).pack(side="right")

        center = tk.Frame(self, bg="#F8FAFC", padx=16, pady=8)
        center.pack(side="top", fill="both", expand=False)

        tk.Label(
            center, text="Внимательно проверьте параметры ККТ перед выполнением:",
            font=("Segoe UI", 9, "bold"), bg="#F8FAFC", fg="#334155"
        ).pack(anchor="w", pady=(0, 4))

        summary_box = tk.Text(
            center, wrap="word", font=("Consolas", 10),
            bg="white", fg="#0F172A", height=max(6, min(10, len(details_list))), padx=10, pady=8,
            relief="solid", borderwidth=1
        )
        summary_box.pack(fill="both", expand=False)
        summary_box.tag_config("KEY", font=("Consolas", 10, "bold"), foreground="#475569")
        summary_box.tag_config("VAL", font=("Consolas", 10), foreground="#0F172A")
        summary_box.tag_config("IMP", font=("Consolas", 10, "bold"), foreground="#1D4ED8")

        for k, v, imp in details_list:
            summary_box.insert("end", f"{k:<24}: ", "KEY")
            summary_box.insert("end", f"{v}\n", "IMP" if imp else "VAL")
        summary_box.config(state="disabled")

        self.bind("<Return>", lambda event: self.on_ok())
        self.bind("<Escape>", lambda event: self.on_cancel())

        if rn_from_kkt is not None:
            self.rn_entry.focus_set()
        else:
            self.word_entry.focus_set()

        self.wait_window(self)

    def on_ok(self):
        if self.rn_from_kkt is not None:
            rn_entered = self.rn_var.get().strip()
            if not re.fullmatch(r"\d{16}", rn_entered):
                messagebox.showerror(
                    "Некорректный РН ККТ",
                    "В поле «1. РН ККТ» должно быть ровно 16 цифр!\n"
                    "Вы можете нажать кнопку «📋 Вставить...» рядом с полем.",
                    parent=self
                )
                self.rn_entry.focus_set()
                return
            if rn_entered != self.rn_from_kkt:
                messagebox.showerror(
                    "Несовпадение РН!",
                    f"Введённый РН ({rn_entered}) НЕ СОВПАДАЕТ с РН в памяти кассы ({self.rn_from_kkt})!",
                    parent=self
                )
                return

        entered_word = self.word_var.get().strip().upper()
        if entered_word != self.confirm_word.upper():
            messagebox.showwarning(
                "Введите кодовое слово",
                f"Для защиты от случайного нажатия введите точную фразу:\n\n{self.confirm_word}",
                parent=self
            )
            self.word_entry.focus_set()
            return

        self.result = {
            "confirmed": True,
            "rn": self.rn_var.get().strip() if self.rn_from_kkt is not None else ""
        }
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()


# =====================================================================
# ГЛАВНОЕ ОКНО ПРОГРАММЫ
# =====================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.app_version = APP_VERSION
        self.title("FN Master — Замена ФН (ФФД 1.2) | Вики Принт / Пирит 2Ф | ООО «Детский мир»")
        self.geometry("1160x760")
        self.minsize(940, 600)
        self.configure(bg="#F1F5F9")

        try:
            log_dir = Path(__file__).resolve().parent.parent / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log_dir = Path.home() / "FN_Master_Logs"
            log_dir.mkdir(parents=True, exist_ok=True)

        self.log_dir = log_dir
        self.log_path = log_dir / f"fn_session_{dt.datetime.now():%Y%m%d_%H%M%S}.txt"

        self.events = queue.Queue()
        self.log_started = False
        self._log_buffer = []
        self._log_lock = threading.Lock()
        self.kkt = KKT(self.log)
        self.busy = False
        self.low_battery_alerted = False

        self._build_ui()
        if ui_theme is not None:
            try:
                ui_theme.apply(self)
            except Exception:
                pass
        self.refresh_ports()

        self.log("Введите вашу фамилию в поле «ФИО инженера» сверху перед началом работ.", "INFO")

        self.after(100, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.after(400, self.auto_connect_on_start)
        self.after(150, lambda: updater.start_update_check(self))

    def auto_connect_on_start(self):
        rows = self.kkt.ports()
        viki_port = next((r[0] for r in rows if r[2]), None)
        if viki_port:
            self.connect()

    def _build_ui(self):
        # 1. НИЖНИЙ СТАТУС-БАР (BOTTOM)
        bottom = tk.Frame(self, bg="white", highlightbackground="#CBD5E1", highlightthickness=1, padx=12, pady=5)
        bottom.pack(side="bottom", fill="x")

        self.status_var = tk.StringVar(value="Готов к работе.")
        tk.Label(bottom, textvariable=self.status_var, font=("Segoe UI", 9, "bold"), bg="white", fg="#334155").pack(side="left")

        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=180)
        # Полоса появляется только во время выполнения операции.

        # 2. ВЕРХНЯЯ ПАНЕЛЬ ПОДКЛЮЧЕНИЯ И ФИО ИНЖЕНЕРА (TOP)
        top_bar = tk.Frame(self, bg="#0F172A", padx=12, pady=8)
        top_bar.pack(side="top", fill="x")

        tk.Label(
            top_bar, text="FN MASTER", font=("Segoe UI", 12, "bold"),
            fg="#38BDF8", bg="#0F172A"
        ).pack(side="left", padx=(0, 10))

        tk.Label(top_bar, text="Порт:", font=("Segoe UI", 9, "bold"), fg="white", bg="#0F172A").pack(side="left")
        self.port_box = ttk.Combobox(top_bar, width=30, state="readonly", font=("Segoe UI", 9))
        self.port_box.pack(side="left", padx=4)

        self.refresh_btn = tk.Button(
            top_bar, text="🔄", font=("Segoe UI", 9), bg="#334155", fg="white",
            relief="flat", padx=6, command=self.refresh_ports
        )
        self.refresh_btn.pack(side="left", padx=2)

        self.connect_btn = tk.Button(
            top_bar, text="🔌 Подключить", font=("Segoe UI", 9, "bold"),
            bg="#2563EB", fg="white", relief="flat", padx=10, pady=2, command=self.connect
        )
        self.connect_btn.pack(side="left", padx=4)

        self.autosearch_btn = tk.Button(
            top_bar, text="🔍 Автопоиск", font=("Segoe UI", 9, "bold"),
            bg="#0D9488", fg="white", relief="flat", padx=10, pady=2, command=self.auto_find_kkt
        )
        self.autosearch_btn.pack(side="left", padx=2)

        self.fio_var = tk.StringVar(value="")
        self.fio_entry = PlaceholderEntry(
            top_bar,
            textvariable=self.fio_var,
            placeholder="ФИО инженера",
            width=24,
            font=("Segoe UI", 9, "bold")
        )
        self.fio_entry.pack(side="left", padx=8, ipady=3)

        self.conn_badge = tk.Label(
            top_bar, text=" НЕ ПОДКЛЮЧЕНО ", font=("Segoe UI", 9, "bold"),
            bg="#475569", fg="white", padx=8, pady=2
        )
        self.conn_badge.pack(side="right")

        tk.Button(
            top_bar, text="📂 Папка логов", font=("Segoe UI", 9), bg="#334155", fg="white",
            relief="flat", padx=8, pady=2, command=self.open_log_folder
        ).pack(side="right", padx=4)

        # Кнопка-подсказка по настройке времени (появляется, если расхождение >= 2 минут, но доступна и из сервиса)
        self.time_guide_btn = tk.Button(
            top_bar, text="⚠️ КАК ИСПРАВИТЬ ВРЕМЯ", font=("Segoe UI", 9, "bold"),
            bg="#EA580C", fg="white", relief="flat", padx=8, pady=2,
            command=self.show_time_manual_guide
        )

        # 3. ДАШБОРД СОСТОЯНИЯ ККТ (8 КАРТОЧЕК)
        dash_wrap = tk.Frame(self, bg="white", highlightbackground="#CBD5E1", highlightthickness=1, padx=10, pady=6)
        dash_wrap.pack(side="top", fill="x", padx=10, pady=(8, 4))

        self.cards = {}
        card_specs = [
            ("shift",   "Смена ККТ / ФН"),
            ("unsent",  "Очередь в ОФД"),
            ("battery", "Батарея ККТ"),
            ("fw",      "Прошивка ККТ"),
            ("fn_stat", "Фаза и № ФН"),
            ("clock",   "Время ККТ и компьютера"),
            ("net",     "Сеть ОФД / ОИСМ"),
            ("req",     "Реквизиты и РНМ"),
        ]

        for idx, (key, title) in enumerate(card_specs):
            r, c = divmod(idx, 4)
            dash_wrap.grid_columnconfigure(c, weight=1, uniform="c")
            cell = tk.Frame(dash_wrap, bg="#F8FAFC", highlightbackground="#E2E8F0", highlightthickness=1, padx=8, pady=4)
            cell.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)

            hdr_row = tk.Frame(cell, bg="#F8FAFC")
            hdr_row.pack(fill="x")

            lbl_t = tk.Label(hdr_row, text=title, font=("Segoe UI", 8, "bold"), bg="#F8FAFC", fg="#64748B", anchor="w")
            lbl_t.pack(side="left")

            # В карточке «Реквизиты и РНМ» добавляем кнопку «✏️ Изменить»
            if key == "req":
                self.edit_req_btn = tk.Button(
                    hdr_row, text="✏️ Изменить", font=("Segoe UI", 7, "bold"),
                    bg="#DBEAFE", fg="#1E40AF", relief="groove", padx=5, pady=0,
                    cursor="hand2", command=self.open_requisites_editor
                )
                self.edit_req_btn.pack(side="right")

            lbl_v = tk.Label(cell, text="—", font=("Segoe UI", 9, "bold"), bg="#F8FAFC", fg="#1E293B", anchor="w")
            lbl_v.pack(fill="x", pady=(2, 0))

            self.cards[key] = (cell, hdr_row, lbl_t, lbl_v)

        self.action_buttons = []

        # 4. ВЕРХНИЙ СЕРВИСНЫЙ РЯД (ДИАГНОСТИКА И СЕТЬ)
        service_frame = tk.Frame(self, bg="#E2E8F0", highlightbackground="#CBD5E1", highlightthickness=1, padx=8, pady=5)
        service_frame.pack(side="top", fill="x", padx=10, pady=(2, 4))

        tk.Label(
            service_frame, text="🛠 ДИАГНОСТИКА И СЕТЬ:",
            font=("Segoe UI", 8, "bold"), bg="#E2E8F0", fg="#334155"
        ).pack(side="left", padx=(4, 10))

        srv_buttons = [
            ("🔄 Полная диагностика ККТ",     "#0284C7", self.diagnose),
            ("🌐 Проверить сеть и DNS",       "#4F46E5", self.check_network),
            ("✏️ Исправить сеть",    "#475569", self.fix_network),
            ("📖 Памятка по настройке часов", "#D97706", self.show_time_manual_guide),
        ]

        for text, color, cmd in srv_buttons:
            b = tk.Button(
                service_frame, text=text, font=("Segoe UI", 8, "bold"),
                bg=color, fg="white", activebackground="#0F172A", activeforeground="white",
                relief="flat", padx=10, pady=4, cursor="hand2", command=cmd
            )
            b.pack(side="left", padx=3, fill="x", expand=True)
            self.action_buttons.append(b)

        # 5. ОТДЕЛЬНЫЙ ВЫДЕЛЕННЫЙ БЛОК: 3 ГЛАВНЫЕ ОПЕРАЦИИ ЗАМЕНЫ ФН
        main_ops_frame = tk.Frame(self, bg="white", highlightbackground="#0F172A", highlightthickness=2, padx=10, pady=8)
        main_ops_frame.pack(side="top", fill="x", padx=10, pady=(2, 6))

        tk.Label(
            main_ops_frame,
            text="🔥 ОСНОВНЫЕ ЭТАПЫ ЗАМЕНЫ ФН (Автоматическая проверка отправки в ОФД встроена в каждый шаг):",
            font=("Segoe UI", 9, "bold"), bg="white", fg="#0F172A", anchor="w"
        ).pack(fill="x", pady=(0, 6))

        main_grid = tk.Frame(main_ops_frame, bg="white")
        main_grid.pack(fill="x")

        main_steps = [
            ("1. ПЕЧАТЬ ОТЧЁТА О РАСЧЁТАХ",   "#0D9488", self.print_report),
            ("2. ЗАКРЫТЬ АРХИВ СТАРОГО ФН",   "#EA580C", self.close_fn),
            ("3. РЕГИСТРАЦИЯ НОВОГО ФН",      "#16A34A", self.register_fn),
        ]

        for idx, (text, color, cmd) in enumerate(main_steps):
            main_grid.grid_columnconfigure(idx, weight=1, uniform="main_ops")
            b = tk.Button(
                main_grid, text=text, font=("Segoe UI", 10, "bold"),
                bg=color, fg="white", activebackground="#0F172A", activeforeground="white",
                relief="flat", pady=9, cursor="hand2", command=cmd
            )
            b.grid(row=0, column=idx, sticky="ew", padx=5)
            self.action_buttons.append(b)

        # 6. ТЕРМИНАЛ ЖУРНАЛА (ТОЛЬКО ЧТЕНИЕ + ПОДДЕРЖКА ВЫДЕЛЕНИЯ И КОПИРОВАНИЯ CTRL+C / ПКМ)
        term_frame = tk.Frame(self, bg="#0F172A", highlightbackground="#CBD5E1", highlightthickness=1)
        term_frame.pack(side="top", fill="both", expand=True, padx=10, pady=(2, 8))

        term_hdr = tk.Frame(term_frame, bg="#1E293B", padx=10, pady=4)
        term_hdr.pack(fill="x")
        tk.Label(
            term_hdr, text="ЖУРНАЛ ОПЕРАЦИЙ (Выделите текст мышкой для копирования Ctrl+C или ПКМ)",
            font=("Consolas", 9, "bold"), bg="#1E293B", fg="#94A3B8"
        ).pack(side="left")

        tk.Button(
            term_hdr, text="Очистить", font=("Segoe UI", 8),
            bg="#334155", fg="white", relief="flat", padx=8,
            command=self.clear_log_screen
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            term_hdr, text="📑 Скопировать весь лог", font=("Segoe UI", 8, "bold"),
            bg="#0284C7", fg="white", relief="flat", padx=8,
            command=self.copy_all_log
        ).pack(side="right")

        scrollbar = ttk.Scrollbar(term_frame)
        scrollbar.pack(side="right", fill="y")

        self.text = tk.Text(
            term_frame, wrap="word", font=("Consolas", 10),
            bg="#0F172A", fg="#E2E8F0", insertbackground="#0F172A",
            selectbackground="#2563EB", selectforeground="white",
            relief="flat", padx=12, pady=8, yscrollcommand=scrollbar.set
        )
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

        # Делаем поле журнала строго защищённым от печати с клавиатуры, но разрешаем выделение и Ctrl+C!
        self._make_text_readonly_copyable(self.text)

        self.text.tag_config("INFO",    foreground="#E2E8F0")
        self.text.tag_config("SUCCESS", foreground="#4ADE80", font=("Consolas", 10, "bold"))
        self.text.tag_config("WARN",    foreground="#FACC15", font=("Consolas", 10, "bold"))
        self.text.tag_config("ERROR",   foreground="#F87171", font=("Consolas", 10, "bold"))
        self.text.tag_config("HEADER",  foreground="#38BDF8", font=("Consolas", 10, "bold"))

    def _make_text_readonly_copyable(self, txt_widget):
        """
        Запрещает любой ввод текста с клавиатуры в журнал,
        но оставляет выделение мышью, копирование по Ctrl+C (в любой раскладке) и меню по ПКМ.
        """
        ctx_menu = tk.Menu(txt_widget, tearoff=0)
        ctx_menu.add_command(label="📋 Копировать выделенное", command=lambda: self.copy_selected_log())
        ctx_menu.add_command(label="📑 Скопировать весь журнал", command=lambda: self.copy_all_log())

        def on_key(event):
            # Разрешаем стрелки, PageUp/PageDown, Home/End
            if event.keysym in ("Up", "Down", "Left", "Right", "Prior", "Next", "Home", "End"):
                return None
            # Ctrl+C и Ctrl+A на английской и русской раскладках (keycodes 67='C', 65='A', 45='Insert')
            if (event.state & 0x4):
                if event.keycode in (67, 45) or event.keysym.lower() in ("c", "insert"):
                    self.copy_selected_log()
                    return "break"
                if event.keycode == 65 or event.keysym.lower() == "a":
                    txt_widget.tag_add("sel", "1.0", "end")
                    return "break"
            return "break"  # Блокируем любые другие нажатия клавиш!

        txt_widget.bind("<Key>", on_key)
        txt_widget.bind("<Button-3>", lambda e: ctx_menu.tk_popup(e.x_root, e.y_root))

    def copy_selected_log(self):
        try:
            sel = self.text.get("sel.first", "sel.last")
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except tk.TclError:
            pass

    def copy_all_log(self):
        all_txt = self.text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(all_txt)
        messagebox.showinfo("Скопировано", "Весь текст журнала скопирован в буфер обмена!", parent=self)

    def clear_log_screen(self):
        self.text.delete("1.0", "end")

    def set_card(self, key, text_val, status="neutral"):
        if key not in self.cards:
            return
        cell, hdr_row, lbl_t, lbl_v = self.cards[key]
        colors = {
            "ok":      ("#DCFCE7", "#22C55E", "#166534"),
            "warn":    ("#FEF9C3", "#EAB308", "#854D0E"),
            "err":     ("#FEE2E2", "#EF4444", "#991B1B"),
            "neutral": ("#F8FAFC", "#E2E8F0", "#1E293B"),
        }
        bg, border, fg = colors.get(status, colors["neutral"])
        cell.config(bg=bg, highlightbackground=border)
        hdr_row.config(bg=bg)
        lbl_t.config(bg=bg)
        lbl_v.config(bg=bg, fg=fg, text=text_val)

    def update_dashboard_from_diag(self, d):
        if d["shift_open_kkt"] or d["shift_open_fn"]:
            self.set_card(
                "shift",
                f"ОТКРЫТА! (№{d['fn_shift_num']})",
                "err"
            )
        else:
            self.set_card(
                "shift",
                f"Закрыта (№{d['fn_shift_num']}) ✅",
                "ok"
            )

        if d["unsent"] == "0":
            self.set_card(
                "unsent",
                "0 док. (Все в ОФД) ✅",
                "ok"
            )
        elif d["unsent"] is None:
            if d["fn_closed_flag"] or d["unsent_err"] == "41":
                self.set_card(
                    "unsent",
                    f"ФН закрыт ({d['unsent_err']}) ✅",
                    "ok"
                )
            else:
                self.set_card(
                    "unsent",
                    f"Ошибка ФН: {d['unsent_err']}",
                    "warn"
                )
        else:
            self.set_card(
                "unsent",
                f"В очереди: {d['unsent']} шт!",
                "err"
            )

        # При напряжении 2,84 В и ниже показываем жёлтое предупреждение.
        bv = d["battery_v"]

        if bv > 2.84:
            self.low_battery_alerted = False
            self.set_card(
                "battery",
                f"{bv:.2f} В (Норма) ✅",
                "ok"
            )
        elif bv > 0:
            self.set_card(
                "battery",
                f"{bv:.2f} В (Низкая)",
                "warn"
            )

            if not self.low_battery_alerted:
                self.low_battery_alerted = True

                self.after(
                    0,
                    lambda voltage=bv: messagebox.showwarning(
                        "Низкое напряжение батареи ККТ",
                        f"Напряжение резервной батареи: {voltage:.2f} В.\n\n"
                        "Необходимо заменить батарею в ККТ по регламенту "
                        "обслуживания. При дальнейшем разряде возможен сбой "
                        "внутренних часов кассы.\n\n"
                        "Предупредите директора магазина о необходимости "
                        "создать заявку на замену батареи.",
                        parent=self
                    )
                )
        else:
            self.set_card("battery", "Н/Д", "warn")

        fw_ok = d["fw_tuple"] >= MIN_FW
        self.set_card(
            "fw",
            f"{d['fw_str']} {'✅' if fw_ok else 'СТАРАЯ!'}",
            "ok" if fw_ok else "err"
        )

        ph = d["fn_phase"]
        expire = d.get("fn_expire_str", "Не определён")

        if ph == 3:
            self.set_card(
                "fn_stat",
                f"Фаза 3 | {d['fn_num']}\n"
                f"Действует до {expire}",
                "ok"
            )
        elif ph == 1:
            self.set_card(
                "fn_stat",
                f"Фаза 1 | {d['fn_num']}\n"
                "Срок — при фискализации",
                "ok"
            )
        elif ph == 15 or d["fn_closed_flag"]:
            self.set_card(
                "fn_stat",
                f"ЗАКРЫТ | {d['fn_num']}",
                "warn"
            )
        else:
            self.set_card(
                "fn_stat",
                f"Фаза {ph} | {d['fn_num']}",
                "err"
            )

        diff = d["time_diff_sec"]

        if diff < 120:
            self.set_card(
                "clock",
                f"{d['kkt_time_str']} (ОК) ✅",
                "ok"
            )
            self.time_guide_btn.pack_forget()
        else:
            self.set_card(
                "clock",
                f"Расхождение {diff // 60} мин! Нажмите подсказку",
                "err"
            )
            self.time_guide_btn.pack(side="right", padx=4)

        if not d["net_bad"]:
            self.set_card(
                "net",
                "Все 7 параметров ОК ✅",
                "ok"
            )
        else:
            self.set_card(
                "net",
                f"Отличаются: {len(d['net_bad'])} шт.",
                "warn"
            )

        self.set_card(
            "req",
            f"{d['rn']} | {d['place'][:14]}",
            "ok" if d["inn"] == INN else "err"
        )

    def open_requisites_editor(self):
        if not self.require_connection():
            return
        if not self.kkt.last_diag:
            messagebox.showinfo("Диагностика", "Сначала выполните диагностику ККТ!", parent=self)
            return
        ReceiptRequisitesEditor(self, self.kkt, self.kkt.last_diag, on_saved_callback=self.diagnose)

    def show_time_manual_guide(self):
        win = tk.Toplevel(self)
        win.title("Инструкция: Как исправить дату и время на кассе (Windows 10)")
        win.geometry("660x420")
        win.configure(bg="#F8FAFC")
        win.transient(self)
        win.grab_set()

        hdr = tk.Frame(win, bg="#EA580C", padx=14, pady=10)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="⏰ КАК ВРУЧНУЮ НАСТРОИТЬ ВРЕМЯ В WINDOWS 10 НА КАССЕ",
            font=("Segoe UI", 11, "bold"), fg="white", bg="#EA580C"
        ).pack(anchor="w")

        body = tk.Frame(win, bg="white", highlightbackground="#CBD5E1", highlightthickness=1, padx=18, pady=14)
        body.pack(fill="both", expand=True, padx=14, pady=12)

        guide_text = (
            "Если часы на кассе отстают или спешат на 2 минуты и более (либо кассовая программа\n"
            "выдаёт ошибку времени при запуске), выполните настройку часов вручную в самой ОС Windows 10:\n\n"
            "ШАГ 1. Закройте кассовую программу и выйдите на Рабочий стол ОС Windows 10 моноблока кассы.\n\n"
            "ШАГ 2. В правом нижнем углу экрана (в панели задач) наведите курсор на Часы и Дату\n"
            "       и нажмите ПРАВУЮ кнопку мыши (ПКМ).\n\n"
            "ШАГ 3. В появившемся меню выберите пункт «Настройка даты и времени».\n\n"
            "ШАГ 4. Проверьте часовой пояс. Затем:\n"
            "       • Либо нажмите кнопку «Синхронизировать сейчас» (в блоке Синхронизация часов).\n"
            "       • Либо отключите тумблер «Устанавливать время автоматически», нажмите кнопку\n"
            "         «Изменить» (в блоке Изменение даты и времени вручную) и выставьте точное текущее\n"
            "         время (сверьте по телефону до минуты) и нажмите «Изменить».\n\n"
            "ШАГ 5. После исправления времени в Windows перезапустите кассовое ПО или нажмите\n"
            "       «🔄 Полная диагностика ККТ» в этой программе."
        )
        tk.Label(body, text=guide_text, font=("Segoe UI", 9, "bold"), bg="white", fg="#1E293B", justify="left", anchor="w").pack(fill="both", expand=True)

        tk.Button(
            win, text="Понятно, закрыть подсказку", font=("Segoe UI", 10, "bold"),
            bg="#0284C7", fg="white", relief="flat", padx=20, pady=6, command=win.destroy
        ).pack(pady=(0, 12))

    def log(self, message, level="INFO"):
        line = f"[{timestamp()}] {message}"
        with self._log_lock:
            if self.log_started:
                self._write_log_lines([line])
            else:
                self._log_buffer.append(line)
        self.events.put(("log", (line, level)))

    def _write_log_lines(self, lines):
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def start_file_log(self):
        """Файл журнала создаётся только после подключения к ККТ."""
        with self._log_lock:
            if self.log_started:
                return
            self.log_started = True
            if self._log_buffer:
                self._write_log_lines(self._log_buffer)
                self._log_buffer = []
        self.log(f"Журнал сессии: {self.log_path}", "SUCCESS")

    def process_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    line, level = payload
                    self.text.insert("end", line + "\n", level)
                    self.text.see("end")
                elif kind == "diag_update":
                    self.update_dashboard_from_diag(payload)
                elif kind == "card_update":
                    k, val, st = payload
                    self.set_card(k, val, st)
                elif kind == "conn_state":
                    connected, port_name = payload
                    if connected:
                        self.conn_badge.config(text=f" ● {port_name} ", bg="#16A34A")
                    else:
                        self.conn_badge.config(text=" НЕ ПОДКЛЮЧЕНО ", bg="#475569")
                elif kind == "call":
                    try:
                        payload()
                    except Exception:
                        pass
                elif kind == "select_port":
                    self.port_box.set(payload)
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.progress.pack_forget()
                    self.set_controls(True)
                    self.status_var.set(f"Готово (Порт: {self.kkt.port})" if self.kkt.port else "Не подключено")
        except queue.Empty:
            pass
        self.after(80, self.process_events)

    def set_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        for w in [self.refresh_btn, self.connect_btn, self.autosearch_btn, self.fio_entry, self.edit_req_btn, *self.action_buttons]:
            w.config(state=state)
        self.port_box.config(state="readonly" if enabled else "disabled")

    def open_log_folder(self):
        try:
            os.startfile(str(self.log_dir))
        except Exception:
            subprocess.Popen(["explorer", str(self.log_dir)])

    def refresh_ports(self):
        rows = self.kkt.ports()
        values = [
            f"{port} | {desc}{' | ★ Вики Принт' if is_viki else ''}"
            for port, desc, is_viki, _, _ in rows
        ]
        self.port_box["values"] = values
        if values:
            selected = next((i for i, r in enumerate(rows) if r[2]), 0)
            self.port_box.current(selected)
        else:
            self.port_box.set("")

    def selected_port(self):
        val = self.port_box.get().split(" | ")[0].strip()
        if not val:
            raise KKTError("COM-порт не выбран!")
        return val

    def get_validated_fio(self):
        fio = self.fio_var.get().strip()
        if not valid_fio(fio):
            empty = (not fio) or getattr(self.fio_entry, "showing_placeholder", False)
            messagebox.showwarning(
                "ФИО инженера",
                "Пожалуйста, введите вашу фамилию и инициалы."
                if empty else
                "ФИО указано некорректно.\n\n"
                "Введите фамилию и инициалы, например: Иванов И.И.",
                parent=self
            )
            self.fio_entry.focus_set()
            return None
        return fio

    def require_connection(self):
        if not self.kkt.ser or not self.kkt.ser.is_open:
            messagebox.showwarning("Нет подключения", "Сначала нажмите «🔌 Подключить» или «🔍 Автопоиск»!", parent=self)
            return False
        return True

    def run_async(self, operation, title):
        if self.busy:
            return

        self.busy = True
        self.set_controls(False)
        self.progress.pack(side="right")
        self.progress.start(12)
        self.status_var.set(f"Выполняется: {title}...")
        self.log(f"=== СТАРТ: {title.upper()} ===", "HEADER")

        def worker():
            succeeded = False

            try:
                operation()
                succeeded = True
            except KKTError as exc:
                self.log(f"ОШИБКА: {exc}", "ERROR")
            except Exception:
                self.log(
                    "СБОЙ:\n" + traceback.format_exc(),
                    "ERROR"
                )
            finally:
                if succeeded:
                    self.log(
                        f"=== ЗАВЕРШЕНО: {title.upper()} ===\n",
                        "HEADER"
                    )
                else:
                    self.log(
                        f"=== ОСТАНОВЛЕНО С ОШИБКОЙ: "
                        f"{title.upper()} ===\n",
                        "ERROR"
                    )

                self.events.put(("done", None))

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # =====================================================================
    # УЛУЧШЕННАЯ АВТО-ПРОВЕРКА ОФД (С ТИХИМ ПЕРЕПОДКЛЮЧЕНИЕМ И ДИАГНОСТИКОЙ КАЖДЫЕ 15 СЕК)
    # =====================================================================
    def _wait_ofd_auto_with_reconnect(
        self,
        context_name,
        max_seconds=300,
        allow_err_41=False
    ):
        """
        Проверяет очередь ОФД без плановых переподключений.

        Полная диагностика здесь не выполняется. Переподключение
        происходит только после ошибки связи. Нулевая очередь
        подтверждается двумя последовательными чтениями.
        """
        port_to_use = self.kkt.port

        if not port_to_use:
            raise KKTError("COM-порт ККТ не определён.")

        max_seconds = min(300, max(30, int(max_seconds)))
        start_ts = time.monotonic()
        zero_confirmations = 0
        closed_confirmations = 0

        self.log(
            f"Ожидание отправки «{context_name}» в ОФД: "
            f"до {max_seconds} секунд. Проверка очереди каждые "
            "10 секунд без планового переподключения.",
            "INFO"
        )

        while True:
            elapsed = int(time.monotonic() - start_ts)

            if elapsed >= max_seconds:
                self.log(
                    f"⚠️ Отчёт «{context_name}» создан, но отправка "
                    f"в ОФД не подтверждена за {max_seconds} секунд. "
                    "Проверьте ComProxy, сеть и доступность ОФД. "
                    "Повторно выполнять фискальную операцию не требуется.",
                    "WARN"
                )
                return False

            u_val = None
            u_err = "ERR"

            try:
                if not self.kkt.ser or not self.kkt.ser.is_open:
                    self.kkt.connect(port_to_use, silent=True)
                    self.log(
                        "Связь с ККТ восстановлена после ошибки.",
                        "SUCCESS"
                    )

                u_val, u_err = self.kkt.unsent()

                if u_val == "0" and u_err == "00":
                    zero_confirmations += 1
                    closed_confirmations = 0

                    self.events.put((
                        "card_update",
                        ("unsent", "0 док. (Все в ОФД) ✅", "ok")
                    ))

                    if zero_confirmations >= 2:
                        self.log(
                            f"✅ Отправка в ОФД подтверждена за "
                            f"{elapsed} секунд: очередь пуста при двух "
                            "последовательных проверках.",
                            "SUCCESS"
                        )

                        try:
                            winsound.MessageBeep(winsound.MB_OK)
                        except Exception:
                            pass

                        return True

                    self.log(
                        "Очередь ОФД пуста. Контрольное повторное "
                        "чтение через 3 секунды.",
                        "INFO"
                    )
                    time.sleep(3)
                    continue

                zero_confirmations = 0

                if allow_err_41 and u_err == "41":
                    # Код 41 принимаем только при фактически закрытом архиве.
                    _, current, _ = self.kkt.status()

                    if current & 16:
                        closed_confirmations += 1

                        self.events.put((
                            "card_update",
                            ("unsent", "ФН закрыт (41) ✅", "ok")
                        ))

                        if closed_confirmations >= 2:
                            self.log(
                                f"✅ Архив ФН закрыт. Ответ 41 и закрытый "
                                f"архив подтверждены повторно за "
                                f"{elapsed} секунд.",
                                "SUCCESS"
                            )

                            try:
                                winsound.MessageBeep(winsound.MB_OK)
                            except Exception:
                                pass

                            return True

                        time.sleep(3)
                        continue

                closed_confirmations = 0

            except Exception as exc:
                zero_confirmations = 0
                closed_confirmations = 0

                self.log(
                    f"Ошибка чтения состояния ОФД: {exc}. "
                    "Следующая проверка будет выполнена с восстановлением "
                    "соединения.",
                    "WARN"
                )
                self.kkt.close()
                u_val = None
                u_err = "СВЯЗЬ"

            elapsed = int(time.monotonic() - start_ts)

            self.events.put((
                "card_update",
                (
                    "unsent",
                    f"Очередь: {u_val if u_val is not None else 'Н/Д'} "
                    f"({elapsed} с)",
                    "warn"
                )
            ))

            self.log(
                f"  [{elapsed:>3} сек] Неотправленных: "
                f"{u_val if u_val is not None else 'Н/Д'} | "
                f"Ответ ФН: {u_err}. Следующая проверка через 10 секунд.",
                "INFO"
            )

            remaining = max_seconds - (
                time.monotonic() - start_ts
            )

            if remaining > 0:
                time.sleep(min(10, remaining))

    def auto_find_kkt(self):
        def op():
            rows = self.kkt.ports()
            for port, desc, is_viki, _, is_bt in rows:
                if is_bt:
                    continue
                try:
                    self.log(f"Проверка {port} ({desc})...", "INFO")
                    self.kkt.connect(port)
                    self.start_file_log(); self.events.put(("conn_state", (True, port)))
                    self.events.put(("select_port", f"{port} | {desc}{' | ★ Вики Принт' if is_viki else ''}"))
                    self._perform_diagnostics_log()
                    return
                except Exception as exc:
                    self.log(f"  {port}: {exc}", "WARN")
            self.events.put(("conn_state", (False, None)))
            raise KKTError("ККТ не найдена ни на одном порту.")

        self.run_async(op, "Автопоиск ККТ")

    def connect(self):
        try:
            port = self.selected_port()
        except KKTError as exc:
            messagebox.showerror("Порт", str(exc), parent=self)
            return

        def op():
            try:
                self.kkt.connect(port)
                self.start_file_log(); self.events.put(("conn_state", (True, port)))
                self._perform_diagnostics_log()
            except KKTError as exc:
                self.events.put(("conn_state", (False, None)))
                raise exc

        self.run_async(op, f"Подключение к {port}")

    def _perform_diagnostics_log(self):
        d = self.kkt.full_diagnostics()
        self.events.put(("diag_update", d))

        self.log(
            f"Заводской №: {d['serial']} | РН ККТ: {d['rn']} | "
            f"ИНН: {d['inn']}",
            "INFO"
        )

        self.log(
            f"Флаги: fatal={d['fatal']}, cur={d['current']} | "
            f"Смена: "
            f"{'ОТКРЫТА (!)' if d['shift_open_kkt'] else 'закрыта'} | "
            f"Архив ФН: "
            f"{'ЗАКРЫТ' if d['fn_closed_flag'] else 'не закрыт'}",
            "ERROR"
            if d["fatal"] or d["shift_open_kkt"]
            else "SUCCESS"
        )

        bv = d["battery_v"]

        if bv > 2.84:
            bat_msg = f"{bv:.2f} В (Норма)"
            bat_lvl = "SUCCESS"
        elif bv > 0:
            bat_msg = (
                f"{bv:.2f} В (Низкая — необходимо создать заявку "
                "на замену батареи ККТ)"
            )
            bat_lvl = "WARN"
        else:
            bat_msg = "Н/Д"
            bat_lvl = "WARN"

        self.log(
            f"Прошивка: {d['fw_str']} | Батарея: {bat_msg} | "
            f"Принтер: "
            f"{'OK' if d['printer_status'] == 0 else d['printer_status']}",
            bat_lvl
        )

        if d["time_diff_sec"] >= 120:
            self.log(
                f"Время ККТ: {d['kkt_time_str']} | "
                f"Время компьютера: {d['pc_time']:%d.%m.%Y %H:%M:%S} "
                f"(РАСХОЖДЕНИЕ {d['time_diff_sec'] // 60} мин! "
                "Нажмите «⚠️ КАК ИСПРАВИТЬ ВРЕМЯ» сверху!)",
                "ERROR"
            )
        else:
            self.log(
                f"Время ККТ: {d['kkt_time_str']} | "
                f"Время компьютера: {d['pc_time']:%d.%m.%Y %H:%M:%S} "
                f"(разница {d['time_diff_sec']} с) ✅",
                "SUCCESS"
            )

        self.log(
            f"ФН №: {d['fn_num']} | "
            f"{FN_PHASES.get(d['fn_phase'], d['fn_phase'])} | "
            f"Срок действия ФН: {d['fn_expire_str']} | "
            f"Смена ФН: №{d['fn_shift_num']} | "
            f"Неотправленных в ОФД: {d['unsent']}",
            "SUCCESS" if d["unsent"] == "0" else "WARN"
        )

        self.log(
            f"Организация: {d['name1']} {d['name2']} | "
            f"Адрес: {d['addr1']} {d['addr2']}",
            "INFO"
        )

        self.log(
            f"Место расчётов: {d['place']} "
            "(Для редактирования нажмите «✏️ Изменить» "
            "в карточке Реквизиты)",
            "INFO"
        )
        self._log_network_state(d)

    def _log_network_state(self, d):
        bad = d.get("net_bad") or []
        if not bad:
            self.log("Сеть ОФД / ОИСМ / ОКП: все 7 параметров в норме ✅", "SUCCESS")
            return
        self.log(f"Сеть ОФД / ОИСМ / ОКП: неверных параметров — {len(bad)}:", "WARN")
        for num in bad:
            name, cur, want, _ = d["net_details"][num]
            self.log(f"   [{num}] {name}: сейчас '{cur}', должно быть '{want}'", "WARN")
        self.log("   Чтобы исправить, нажмите кнопку «Исправить сеть».", "WARN")

    def diagnose(self):
        if not self.require_connection():
            return
        self.run_async(self._perform_diagnostics_log, "Диагностика ККТ")

    def check_network(self):
        if not self.require_connection():
            return

        def op():
            d = self.kkt.full_diagnostics()
            self.events.put(("diag_update", d))
            for num, (name, expected, host) in NETWORK.items():
                cur_val = self.kkt.table(num)
                ok = (cur_val == expected)
                self.log(
                    f"[{num}] {name}: '{cur_val}' {'== OK' if ok else f'!= ДОЛЖНО БЫТЬ {expected}'}",
                    "SUCCESS" if ok else "WARN"
                )
                if host:
                    self.log(f"    DNS {host}: {', '.join(dns_addresses(host))}", "INFO")

        self.run_async(op, "Проверка сети и DNS")

    def fix_network(self):
        if not self.require_connection():
            return

        def op():
            changed = 0
            for num, (name, expected, _) in NETWORK.items():
                if self.kkt.table(num) == expected:
                    continue
                err = self.kkt.set_table(num, 0, expected)
                if err != "00":
                    raise KKTError(f"Не удалось записать {name} (ошибка {err})")
                changed += 1
                self.log(f"{name}: записано '{expected}'", "SUCCESS")
            self.log(f"Готово. Изменено параметров: {changed}", "SUCCESS")
            self._perform_diagnostics_log()

        self.run_async(op, "Запись настроек сети")

    def print_report(self):
        if not self.require_connection():
            return
        fio = self.get_validated_fio()
        if not fio:
            return

        def op():
            _, cur, _ = self.kkt.status()
            if cur & 1:
                self.kkt.begin_work()
            err, _ = self.kkt.ask("59", [fio], timeout=60)
            if err != "00":
                raise KKTError(f"Ошибка печати отчёта: {err}")
            self.log("✅ Отчёт о состоянии расчётов распечатан! Сохраните чек.", "SUCCESS")
            delivered = self._wait_ofd_auto_with_reconnect("Отчёт о расчётах", max_seconds=300, allow_err_41=False)
            if not delivered:
                raise KKTError("Отчёт распечатан, но отправка в ОФД не подтверждена. Проверьте ComProxy и сеть; повторно печатать отчёт не требуется.")

        self.run_async(op, "Печать отчёта о расчётах")

    def close_fn(self):
        if not self.require_connection():
            return
        fio = self.get_validated_fio()
        if not fio:
            return

        d = self.kkt.last_diag
        if not d:
            self.diagnose()
            return

        if d.get("shift_open_kkt") or d.get("shift_open_fn"):
            messagebox.showerror("СТОП: Смена открыта", "Сначала закройте смену через кассира!", parent=self)
            return

        if d.get("fn_closed_flag") or d.get("fn_phase") == 15:
            messagebox.showwarning("Уже закрыт", "Архив этого ФН уже закрыт!", parent=self)
            return

        if d.get("unsent") != "0":
            messagebox.showerror(
                "СТОП: Очередь ОФД не пуста!",
                f"Неотправленных документов: {d.get('unsent')} шт.\nДождитесь отправки всех чеков в ОФД!",
                parent=self
            )
            return

        details = [
            ("Заводской № ККТ", d.get("serial", "?"), False),
            ("РН ККТ", d.get("rn", "?"), True),
            ("Номер старого ФН", d.get("fn_num", "?"), True),
            ("Статус смены", f"Закрыта (№{d.get('fn_shift_num', '?')})", False),
            ("Неотправленных в ОФД", "0 (Проверено)", True),
            ("Часы ККТ", d.get("kkt_time_str", "?"), True),
            ("Инженер", fio, False),
        ]

        dlg = ConfirmMasterDialog(
            self,
            title="Закрытие архива старого ФН",
            header_text="⚠️ ПОДТВЕРЖДЕНИЕ ЗАКРЫТИЯ АРХИВА СТАРОГО ФН",
            header_bg="#EA580C",
            details_list=details,
            confirm_word="ЗАКРЫТЬ ФН"
        )
        if not dlg.result:
            return

        def op():
            fatal, cur, doc = self.kkt.status()
            if fatal != 0 or (cur & 4) or (doc & 15):
                raise KKTError("Статус ККТ не позволяет закрыть ФН (проверьте смену/документ).")
            u_val, _ = self.kkt.unsent()
            if u_val != "0":
                raise KKTError(f"Есть неотправленные документы ({u_val})!")

            if cur & 1:
                self.kkt.begin_work()

            self.log("Отправка команды закрытия ФН (0x71)... Ждите печати чека!", "WARN")
            err, fields = self.kkt.ask("71", [fio], timeout=120)
            if err != "00":
                raise KKTError(f"Закрытие ФН не выполнено (ошибка {err})")

            fd = fields[0] if fields else "?"
            fp = fields[1] if len(fields) > 1 else "?"
            self.log(f"✅ ФН ЗАКРЫТ! ФД={fd} | ФП={fp}. НЕ ВЫКЛЮЧАЙТЕ КАССУ!", "SUCCESS")

            delivered = self._wait_ofd_auto_with_reconnect("Отчёт о закрытии ФН", max_seconds=300, allow_err_41=True)
            if not delivered:
                raise KKTError("Архив ФН закрыт, но ответ 41 не подтверждён за 5 минут. Не повторяйте команду закрытия архива; проверьте ComProxy и сеть.")
            if delivered:
                self.log("🔧 ГОТОВО К ЗАМЕНЕ ФН: подождите 30-60 сек, выключите ККТ и установите новый ФН.", "SUCCESS")

        self.run_async(op, "Закрытие архива ФН")

    def register_fn(self):
        if not self.require_connection():
            return
        fio = self.get_validated_fio()
        if not fio:
            return

        d = self.kkt.last_diag
        if not d:
            self.diagnose()
            return

        if d.get("shift_open_kkt") or d.get("shift_open_fn"):
            messagebox.showerror("Смена открыта", "Закройте смену!", parent=self)
            return

        if d.get("fn_phase") == 3:
            messagebox.showwarning("Уже зарегистрирован", f"ФН {d.get('fn_num')} уже в Фазе 3 (зарегистрирован)!", parent=self)
            return

        if d.get("fn_phase") != 1:
            messagebox.showerror(
                "Неверная фаза ФН",
                f"Ожидается новый ФН (Фаза 1). Сейчас: {FN_PHASES.get(d.get('fn_phase'), d.get('fn_phase'))}.\n"
                "Нажмите «🔄 Полная диагностика ККТ» после физической замены ФН.",
                parent=self
            )
            return

        details = [
            ("Номер НОВОГО ФН", d.get("fn_num", "?"), True),
            ("РН ККТ в памяти кассы", d.get("rn", "?"), True),
            ("ИНН / Организация", f"{INN} / {d.get('name1', '')}", False),
            ("Адрес расчётов", f"{d.get('addr1', '')} {d.get('addr2', '')}", False),
            ("Место расчётов", d.get("place", "?"), True),
            ("ОФД", f"{OFD_NAME} (ИНН {OFD_INN})", False),
            ("Email / Сайт ФНС", f"{MAIL} / {FNS}", False),
            ("Параметры регистрации", f"СНО={SNO} | Режим={MODE} | Доп={DOPMODE} | ФФД=1.2", True),
            ("Инженер", fio, False),
        ]

        dlg = ConfirmMasterDialog(
            self,
            title="Регистрация нового ФН (ФФД 1.2)",
            header_text="🛡️ ПЕРЕРЕГИСТРАЦИЯ ККТ В СВЯЗИ С ЗАМЕНОЙ ФН (ФФД 1.2)",
            header_bg="#16A34A",
            details_list=details,
            confirm_word="РЕГИСТРАЦИЯ",
            rn_from_kkt=d.get("rn", "")
        )
        if not dlg.result:
            return

        rn = dlg.result["rn"]

        def op():
            fatal, cur, doc = self.kkt.status()
            if fatal != 0 or (cur & 4) or (doc & 15):
                raise KKTError("Недопустимый статус ККТ перед регистрацией!")

            stored_rn = (self.kkt.info(4) or [""])[0].strip()
            if rn != stored_rn:
                raise KKTError(f"РН не совпадает с ККТ ({rn} != {stored_rn})!")

            err, fn_f = self.kkt.fn(2)
            if err != "00" or not fn_f or (int(fn_f[0]) & 15) != 1:
                raise KKTError("ФН не в фазе 1!")

            name1 = self.kkt.table(30, 0)
            name2 = self.kkt.table(30, 1)
            addr1 = self.kkt.table(30, 2)
            addr2 = self.kkt.table(30, 3)
            place = self.kkt.table(85)
            ofd_name = self.kkt.table(81) or OFD_NAME
            ofd_inn = self.kkt.table(71).strip() or OFD_INN
            email = self.kkt.table(82) or MAIL
            fns = self.kkt.table(83) or FNS

            if cur & 1:
                self.kkt.begin_work()

            now = dt.datetime.now()
            d_str, t_str = now.strftime("%d%m%y"), now.strftime("%H%M%S")

            params = [
                1, rn, INN, SNO, MODE, fio, d_str, t_str,
                name1, name2, addr1, addr2, place, "",
                ofd_name, ofd_inn, email, fns, DOPMODE, FFD,
            ]

            self.log(f"Отправка команды регистрации 0x60 ({d_str} {t_str})...", "WARN")
            err, ans = self.kkt.ask("60", params, timeout=120)
            if err != "00":
                raise KKTError(f"Ошибка регистрации: код {err}")

            fd = ans[0] if ans else "?"
            fp = ans[1] if len(ans) > 1 else "?"
            self.log(f"🎉 ЗАРЕГИСТРИРОВАНО! ФД={fd} | ФП={fp}", "SUCCESS")

            delivered = self._wait_ofd_auto_with_reconnect("Отчёт о регистрации ФН", max_seconds=300, allow_err_41=False)
            if not delivered:
                raise KKTError("ФН зарегистрирован, но отправка отчёта в ОФД не подтверждена. Не повторяйте регистрацию; проверьте ComProxy и сеть.")

        self.run_async(op, "Регистрация нового ФН")

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno("Выход", "Операция выполняется. Выйти?", parent=self):
                return
        self.kkt.close()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()

