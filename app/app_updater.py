import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from tkinter import messagebox


LATEST_RELEASE = (
    "https://api.github.com/repos/"
    "ZingiestFever/FN-Master/releases/latest"
)

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
UPDATES = ROOT / "updates"
USER_AGENT = "FN-Master-Updater/1.0"


def sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as source:
        for block in iter(lambda: source.read(262144), b""):
            digest.update(block)

    return digest.hexdigest()


def version_tuple(value):
    match = re.fullmatch(
        r"[vV]?(\d+(?:\.\d+){0,3})(?:[-+].*)?",
        str(value).strip()
    )

    if not match:
        raise ValueError(f"Некорректная версия: {value}")

    parts = [int(part) for part in match.group(1).split(".")]

    while len(parts) < 4:
        parts.append(0)

    return tuple(parts)


def app_log(app, text, category="INFO"):
    try:
        app.after(0, lambda: app.log(text, category))
    except Exception:
        pass


def request(url, api=False):
    headers = {"User-Agent": USER_AGENT}

    if api:
        headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })

    return urllib.request.Request(url, headers=headers)


def http_error(exc, name=""):
    suffix = f": {name}" if name else ""

    if exc.code in (403, 429):
        return RuntimeError(
            "GitHub временно ограничил запросы" + suffix
        )

    if exc.code == 404:
        return RuntimeError(
            "Файл или релиз не найден" + suffix
        )

    return RuntimeError(
        f"GitHub вернул HTTP {exc.code}{suffix}"
    )


def latest_release():
    try:
        with urllib.request.urlopen(
            request(LATEST_RELEASE, api=True),
            timeout=25
        ) as response:
            return json.load(response)

    except urllib.error.HTTPError as exc:
        raise http_error(exc) from exc


def asset(release, name):
    for item in release.get("assets", []):
        if item.get("name") == name:
            return item

    raise RuntimeError(
        f"В релизе {release.get('tag_name')} нет файла {name}"
    )


def download(release, name, destination):
    item = asset(release, name)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")

    try:
        with urllib.request.urlopen(
            request(item["browser_download_url"]),
            timeout=180
        ) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, 262144)

        os.replace(temporary, destination)

    except urllib.error.HTTPError as exc:
        temporary.unlink(missing_ok=True)
        raise http_error(exc, name) from exc

    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def safe_app_relative(value):
    if "\\" in value:
        raise RuntimeError(f"Недопустимый путь: {value}")

    relative = PurePosixPath(value)

    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "app"
        or ".." in relative.parts
        or len(relative.parts) < 2
    ):
        raise RuntimeError(f"Небезопасный путь: {value}")

    return relative


def asset_name(relative):
    inside_app = PurePosixPath(*relative.parts[1:])
    return "app_" + "_".join(inside_app.parts)


_status = {"text": "", "active": False, "n": 0}


def ui_call(app, func):
    try:
        app.events.put(("call", func))
    except Exception:
        pass


def _draw_status(app, text, level="INFO"):
    line = f"[{dt.datetime.now():%d.%m.%Y %H:%M:%S}] {text}\n"
    box = app.text
    ranges = box.tag_ranges("upd_line")
    if ranges:
        start = box.index(ranges[0])
        box.delete(ranges[0], ranges[-1])
        box.insert(start, line, ("upd_line", level))
    else:
        box.insert("end", line, ("upd_line", level))
        box.see("end")


def _tick(app):
    if not _status["active"]:
        return
    _status["n"] = (_status["n"] + 1) % 4
    _draw_status(
        app,
        "Проверка обновлений: " + _status["text"] + "." * _status["n"]
    )
    app.after(350, lambda: _tick(app))


def set_stage(app, text):
    def run():
        first = not _status["active"]
        _status["text"] = text
        _status["active"] = True
        if first:
            _tick(app)
    ui_call(app, run)


def finish_status(app, text, level="INFO"):
    def run():
        _status["active"] = False
        _draw_status(app, text, level)
    ui_call(app, run)


def ask_update_dialog(app, new_version, current_version):
    import tkinter as tk

    result = {"ok": False}
    win = tk.Toplevel(app)
    win.title("Обновление FN Master")
    win.configure(bg="#F8FAFC")
    win.resizable(False, False)
    win.transient(app)

    hdr = tk.Frame(win, bg="#0F172A", padx=20, pady=14)
    hdr.pack(fill="x")
    tk.Label(hdr, text="Доступно обновление", font=("Segoe UI", 14, "bold"),
             fg="#38BDF8", bg="#0F172A").pack(anchor="w")
    tk.Label(hdr, text="FN Master — замена фискального накопителя",
             font=("Segoe UI", 9), fg="#94A3B8", bg="#0F172A").pack(anchor="w")

    body = tk.Frame(win, bg="#F8FAFC", padx=20, pady=16)
    body.pack(fill="both")
    for caption, value, color in (
        ("Установлена:", str(current_version), "#475569"),
        ("Новая версия:", str(new_version), "#16A34A"),
    ):
        row = tk.Frame(body, bg="#F8FAFC")
        row.pack(fill="x", pady=2)
        tk.Label(row, text=caption, width=14, anchor="w", font=("Segoe UI", 10),
                 bg="#F8FAFC", fg="#475569").pack(side="left")
        tk.Label(row, text=value, font=("Consolas", 12, "bold"),
                 bg="#F8FAFC", fg=color).pack(side="left")

    tk.Label(
        body,
        text="Программа закроется, установит обновление\n"
             "и запустится снова. Это займёт несколько секунд.",
        font=("Segoe UI", 9), bg="#F8FAFC", fg="#334155", justify="left"
    ).pack(anchor="w", pady=(12, 0))

    btns = tk.Frame(win, bg="#E2E8F0", padx=20, pady=12)
    btns.pack(fill="x")

    def yes():
        result["ok"] = True
        win.destroy()

    tk.Button(btns, text="Обновить и перезапустить", font=("Segoe UI", 10, "bold"),
              bg="#16A34A", fg="white", activebackground="#15803D",
              activeforeground="white", relief="flat", padx=16, pady=6,
              cursor="hand2", command=yes).pack(side="left")
    tk.Button(btns, text="Позже", font=("Segoe UI", 10), bg="white", fg="#334155",
              relief="groove", padx=16, pady=6, cursor="hand2",
              command=win.destroy).pack(side="right")

    win.bind("<Return>", lambda e: yes())
    win.bind("<Escape>", lambda e: win.destroy())
    win.update_idletasks()
    x = app.winfo_rootx() + (app.winfo_width() - win.winfo_width()) // 2
    y = app.winfo_rooty() + (app.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.grab_set()
    win.wait_window(win)
    return result["ok"]


def run_with_window(args):
    """Окно с прогрессом установки (работает в отдельном процессе)."""
    state = {"text": "Ожидание закрытия программы", "percent": 5,
             "done": False, "error": None}

    def progress(text, percent):
        state["text"] = text
        state["percent"] = percent
        time.sleep(0.4)  # чтобы этап был виден

    def job():
        try:
            wait_parent(args.pid)
            apply_update(args.apply, args.python, args.main, progress)
            progress("Обновление установлено", 100)
        except Exception as exc:
            state["error"] = exc
        finally:
            state["done"] = True

    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
    except Exception:
        job()
        if state["error"]:
            raise state["error"]
        return

    root.title("Обновление FN Master")
    root.configure(bg="#0F172A")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    box = tk.Frame(root, bg="#0F172A", padx=24, pady=18)
    box.pack()
    tk.Label(box, text="Установка обновления", font=("Segoe UI", 13, "bold"),
             fg="#38BDF8", bg="#0F172A").pack(anchor="w")
    stage_lbl = tk.Label(box, text=state["text"], font=("Segoe UI", 10),
                         fg="#E2E8F0", bg="#0F172A", width=44, anchor="w")
    stage_lbl.pack(anchor="w", pady=(8, 6))
    bar = ttk.Progressbar(box, mode="determinate", maximum=100, length=380)
    bar.pack()
    pct_lbl = tk.Label(box, text="5 %", font=("Consolas", 9),
                       fg="#94A3B8", bg="#0F172A")
    pct_lbl.pack(anchor="e", pady=(4, 0))

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")

    threading.Thread(target=job, daemon=True).start()

    def poll():
        stage_lbl.config(text=state["text"])
        bar["value"] = state["percent"]
        pct_lbl.config(text=f"{state['percent']} %")
        if state["done"]:
            if state["error"]:
                messagebox.showerror(
                    "Обновление FN Master",
                    "Не удалось установить обновление.\n"
                    "Прежняя версия сохранена.\n\n" + str(state["error"]),
                    parent=root
                )
                root.destroy()
            else:
                root.after(700, root.destroy)
            return
        root.after(100, poll)

    poll()
    root.mainloop()

    if state["error"]:
        raise state["error"]


def start_update_check(app):
    def worker():
        try:
            set_stage(app, "подключение к GitHub")
            release = latest_release()
            tag = str(release.get("tag_name", "")).strip()

            if not tag:
                raise RuntimeError("GitHub не вернул тег релиза")

            set_stage(app, "сравнение версий")
            remote = version_tuple(tag)
            current_text = getattr(app, "app_version", "0.0.0")

            if remote <= version_tuple(current_text):
                finish_status(
                    app,
                    f"Обновлений нет — установлена актуальная версия {current_text}.",
                    "SUCCESS"
                )
                return

            set_stage(app, f"найдена версия {tag}, загрузка списка файлов")
            manifest_path = UPDATES / "latest_manifest.json"

            download(
                release,
                "manifest.json",
                manifest_path
            )

            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8-sig")
            )

            if version_tuple(manifest["version"]) != remote:
                raise RuntimeError(
                    "Версия manifest не совпадает с тегом релиза"
                )

            safe_tag = re.sub(r"[^0-9A-Za-z_.-]", "_", tag)
            stage = UPDATES / safe_tag

            if stage.exists():
                shutil.rmtree(stage)

            stage.mkdir(parents=True)
            downloaded = []

            # Скачиваются только объекты app/ из manifest.
            for relative_text, expected_hash in manifest["files"].items():
                if not relative_text.startswith("app/"):
                    continue

                relative = safe_app_relative(relative_text)
                github_name = asset_name(relative)
                set_stage(app, f"загрузка и проверка {relative.name}")
                destination = stage.joinpath(*relative.parts)

                download(
                    release,
                    github_name,
                    destination
                )

                if sha256(destination).lower() != expected_hash.lower():
                    raise RuntimeError(
                        "Ошибка контрольной суммы: " + github_name
                    )

                downloaded.append(relative)

            required = {
                PurePosixPath("app/fn_master.py"),
                PurePosixPath("app/updater.py")
            }

            if not required.issubset(set(downloaded)):
                raise RuntimeError(
                    "В обновлении нет обязательных Python-файлов"
                )

            def ask_user():
                try:
                    if not ask_update_dialog(app, tag, current_text):
                        return

                    if app.busy:
                        messagebox.showwarning(
                            "Обновление",
                            "Дождитесь завершения операции с ККТ.",
                            parent=app
                        )
                        return

                    subprocess.Popen(
                        [
                            str(Path(os.sys.executable)),
                            str(stage / "app" / "updater.py"),
                            "--apply", str(stage),
                            "--pid", str(os.getpid()),
                            "--python", os.sys.executable,
                            "--main", str(APP_DIR / "fn_master.py")
                        ],
                        cwd=str(APP_DIR),
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        close_fds=True
                    )

                    try:
                        app.kkt.close()
                    except Exception:
                        pass

                    app.destroy()

                except Exception as exc:
                    app_log(
                        app,
                        "Не удалось начать обновление: " + str(exc),
                        "WARN"
                    )

            finish_status(app, f"Доступна новая версия {tag}.", "WARN")
            ui_call(app, ask_user)

        except Exception as exc:
            # Ошибка обновления не мешает работе установленной версии.
            finish_status(app, "Проверка обновлений пропущена: " + str(exc), "WARN")

    threading.Thread(target=worker, daemon=True).start()


def wait_parent(pid):
    try:
        handle = ctypes.windll.kernel32.OpenProcess(
            0x00100000,
            False,
            int(pid)
        )

        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(
                handle,
                30000
            )
            ctypes.windll.kernel32.CloseHandle(handle)
        else:
            time.sleep(3)

    except Exception:
        time.sleep(3)


def apply_update(stage, python_exe, main_script, progress=None):
    progress = progress or (lambda text, percent: None)
    progress("Проверка пакета обновления", 20)
    stage = Path(stage).resolve()
    main_script = Path(main_script).resolve()
    target_app = main_script.parent
    root = target_app.parent

    nested_app = stage / "app"

    # Новый updater скачивает обновления в stage/app.
    if nested_app.is_dir():
        source_app = nested_app
    else:
        # Старый updater 1.0.0 скачивает fn_master.py и updater.py
        # непосредственно в stage. Этот формат тоже поддерживаем.
        source_app = stage

    if not source_app.is_dir():
        raise RuntimeError(
            "Папка пакета обновления не найдена: " + str(source_app)
        )

    files = sorted(
        item for item in source_app.rglob("*")
        if item.is_file() and not item.name.endswith(".part")
    )

    if not files:
        raise RuntimeError(
            "Пакет обновления пуст: " + str(source_app)
        )

    if not target_app.is_dir():
        raise RuntimeError(
            "Папка установленной программы не найдена: "
            + str(target_app)
        )

    backup = (
        root / "backups" /
        dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    progress("Создание резервной копии", 35)

    replaced = []
    existed = set()

    try:
        for source in files:
            relative = source.relative_to(source_app)
            progress(
                f"Установка файла {relative}",
                40 + 45 * files.index(source) // len(files)
            )

            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
            ):
                raise RuntimeError(
                    "Небезопасный путь в обновлении: "
                    + str(relative)
                )

            target = target_app / relative
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists():
                existed.add(relative)

                backup_target = backup / relative
                backup_target.parent.mkdir(
                    parents=True,
                    exist_ok=True
                )
                shutil.copy2(target, backup_target)

            temporary = target.with_name(
                target.name + ".update_tmp"
            )

            shutil.copy2(source, temporary)
            os.replace(temporary, target)
            replaced.append((relative, target))

    except Exception:
        # Восстанавливаем старую работоспособную версию.
        for relative, target in replaced:
            if relative in existed:
                shutil.copy2(backup / relative, target)
            elif target.exists():
                target.unlink()

        raise

    progress("Запуск новой версии", 95)
    subprocess.Popen(
        [str(python_exe), str(main_script)],
        cwd=str(target_app),
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", required=True)
    parser.add_argument("--pid", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--main", required=True)
    args = parser.parse_args()

    try:
        run_with_window(args)

    except Exception:
        try:
            log_dir = (
                Path(args.main).resolve().parent.parent / "logs"
            )
            log_dir.mkdir(parents=True, exist_ok=True)

            (log_dir / "updater_error.txt").write_text(
                traceback.format_exc(),
                encoding="utf-8"
            )
        except Exception:
            pass
