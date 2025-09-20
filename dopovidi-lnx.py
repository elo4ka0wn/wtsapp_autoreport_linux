# -*- coding: utf-8 -*-
# АвтоДоповідь WhatsApp — Linux Wayland (Hyprland + WasIstLos)
# Повна версія: таймер (показ завжди), антифлуд (15 хв), 1 доповідь/год (:45 ±2),
# "Відправити зараз", "Тест вставлення", "Діагностика", збереження у report.ini,
# лог-панель. Відправка: просто фокус на WhatsApp → друк через wtype → Enter.
#
# Залежності:
#   sudo pacman -S python tk wl-clipboard        # (wl-clipboard не обов'язково)
#   yay -S wtype
#
# Запуск у venv (рекомендовано):
#   python -m venv venv && source venv/bin/activate && python dopovidi-lnx.py

import tkinter as tk
from tkinter import ttk
import time
import random
from datetime import datetime, timedelta
import threading
import configparser
import os
import queue
import subprocess
import json
import traceback
import shutil

APP_TITLE = "АвтоДоповідь WhatsApp — Linux Wayland (Hyprland)"
CONFIG_FILE = "report.ini"
CONFIG_SECTION = "Report"
CONFIG_KEY = "text"

# -------- Параметри логіки --------
ANTIFLOOD_SECONDS = 15 * 60          # антифлуд: 15 хв між відправками
VERIFY_RETRIES = 2                   # скільки разів пробувати просту відправку
PRE_TYPE_DELAY_MS_DEFAULT = 200      # пауза перед друком, мс
SEND_DELAY_S_DEFAULT = 0.20          # пауза після Enter, с

# -------- Ідентифікація вікна WasIstLos/WhatsApp --------
WASISTLOS_CLASS = "wasistlos"
TITLE_HINTS = ["whatsapp", "wasistlos"]

# -------- Перевірка утиліт --------
HAS_WTYPE   = shutil.which("wtype") is not None
HAS_HYPRCTL = shutil.which("hyprctl") is not None

# -------- Логи (черга → текстове поле) --------
log_q = queue.Queue()
def log_message(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    log_q.put(f"[{ts}] {msg}\n")

def log_exception(prefix: str, e: Exception):
    tb = traceback.format_exc(limit=2)
    log_message(f"{prefix}: {e.__class__.__name__}: {e}")
    log_message(f"↳ Trace: {tb.strip()}")

# -------- Хелпери команд --------
def run_cmd(cmd: list[str], timeout: float = 5.0):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return -1, "", str(e)

# -------- Hyprland: пошук/фокус вікна --------
def hypr_clients_json():
    if not HAS_HYPRCTL:
        return []
    rc, out, err = run_cmd(["hyprctl", "-j", "clients"], timeout=3)
    if rc != 0 or not out:
        return []
    try:
        return json.loads(out)
    except Exception as e:
        log_exception("hyprctl -j clients parse", e)
        return []

def find_wasistlos_client():
    """Знайти клієнт WasIstLos/WhatsApp по class або title."""
    clients = hypr_clients_json()
    if not clients:
        return None
    for c in clients:
        if (c.get("class") or "").lower() == WASISTLOS_CLASS:
            return c
    for c in clients:
        title = (c.get("title") or "").lower()
        if any(h in title for h in TITLE_HINTS):
            return c
    return None

def focus_client(client: dict) -> bool:
    """Перемикаємося на workspace клієнта та фокусимо його вікно."""
    if not HAS_HYPRCTL:
        log_message("❌ Немає hyprctl — фокус недоступний.")
        return False
    ws_id = client.get("workspace", {}).get("id")
    if ws_id is not None:
        run_cmd(["hyprctl", "dispatch", "workspace", str(ws_id)])
        time.sleep(0.08)
    addr = client.get("address")
    if addr:
        run_cmd(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"])
        time.sleep(0.12)
        return True
    # фолбек: за class
    clazz = client.get("class")
    if clazz:
        run_cmd(["hyprctl", "dispatch", "focuswindow", clazz])
        time.sleep(0.12)
        return True
    return False

def ensure_whatsapp_focused(prefix="") -> bool:
    c = find_wasistlos_client()
    if not c:
        log_message(prefix + "❌ Вікно WasIstLos/WhatsApp не знайдено.")
        return False
    ok = focus_client(c)
    title = c.get("title","")
    clazz = c.get("class","")
    if ok:
        log_message(prefix + f"✅ Активовано: {clazz} — '{title}'")
        return True
    log_message(prefix + "❌ Не вдалося сфокусувати вікно через hyprctl.")
    return False

# -------- Емуляція вводу (wtype) --------
def wtype_key(key: str) -> bool:
    if not HAS_WTYPE:
        log_message("❌ Немає wtype (емуляція клавіш недоступна).")
        return False
    rc, out, err = run_cmd(["wtype", "-k", key])
    return rc == 0

def wtype_text(text: str) -> bool:
    if not HAS_WTYPE:
        log_message("❌ Немає wtype (емуляція клавіш недоступна).")
        return False
    rc, out, err = run_cmd(["wtype", text])
    return rc == 0

# -------- Відправка: просто друк → Enter --------
def paste_and_send(
    text: str,
    do_send: bool = True,
    pre_ms: int = PRE_TYPE_DELAY_MS_DEFAULT,
    send_delay_s: float = SEND_DELAY_S_DEFAULT
) -> bool:
    """Фокус на WhatsApp вже зроблено. Просто: пауза → друк тексту → (опц.) Enter."""
    time.sleep(max(0.0, pre_ms/1000.0))

    # ДРУК ТЕКСТУ
    if not wtype_text(text):
        log_message("❌ Не вдалося надрукувати текст.")
        return False

    # Відправка (Enter)
    if do_send:
        if not wtype_key("Return"):
            log_message("❌ Не вдалося натиснути Enter.")
            return False
        time.sleep(max(0.05, send_delay_s))
    return True

def send_whatsapp_message(
    text: str,
    do_send: bool = True,
    pre_ms: int = PRE_TYPE_DELAY_MS_DEFAULT,
    send_s: float = SEND_DELAY_S_DEFAULT,
    focus_first: bool = True
) -> bool:
    """Комплекс: (опц.) фокус → друк → Enter."""
    if focus_first:
        if not ensure_whatsapp_focused():
            return False
    for attempt in range(1, VERIFY_RETRIES + 1):
        log_message(f"→ Надрукувати/відправити (спроба {attempt})…")
        ok = paste_and_send(text, do_send, pre_ms, send_s)
        if ok:
            log_message("✅ Відправлено успішно.")
            return True
        time.sleep(0.15 * attempt)
    return False

# -------- Планування часу --------
def get_next_slot(base=None):
    """Найближчий слот: :45 поточної/наступної години з офсетом [-2..+2] хв."""
    now = base or datetime.now()
    if now.minute < 45:
        t = now.replace(minute=45, second=0, microsecond=0)
    else:
        t = (now + timedelta(hours=1)).replace(minute=45, second=0, microsecond=0)
    return t + timedelta(minutes=random.randint(-2, 2))

def get_next_hour_slot_from_target(prev_target):
    """Наступний слот рівно через годину від попереднього target: HH:45 ±2 хв."""
    base = (prev_target + timedelta(hours=1)).replace(minute=45, second=0, microsecond=0)
    return base + timedelta(minutes=random.randint(-2, 2))

# -------- Глобальний стан таймера --------
state_lock = threading.Lock()
timer_active = False
next_report_time = None
last_fired_target = None
last_send_ts = 0.0
timer_thread = None
fire_lock = threading.Lock()

# -------- Відправка / Таймер --------
def do_send_report(
    text: str,
    pre_ms: int,
    send_s: float,
    via_timer: bool = False,
    focus_first: bool = True
):
    """Антифлуд + відправка; префікси логів для таймера."""
    global last_send_ts
    now_ts = time.time()
    with state_lock:
        if now_ts - last_send_ts < ANTIFLOOD_SECONDS:
            left = int(ANTIFLOOD_SECONDS - (now_ts - last_send_ts))
            log_message(f"⛔ Скасовано дубль: антифлуд {ANTIFLOOD_SECONDS//60} хв. Залишилось ~{left}с.")
            return
        last_send_ts = now_ts

    prefix = "⏰ [Таймер] " if via_timer else ""
    text = (text or "").strip()
    if not text:
        log_message(prefix + "⚠️ Текст порожній.")
        return
    log_message(prefix + "📤 Відправляю…")
    ok = send_whatsapp_message(
        text, True, pre_ms, send_s,
        focus_first=focus_first
    )
    if ok:
        log_message(prefix + "🎉 Готово.")
    else:
        log_message(prefix + "❌ Не вдалося відправити.")

def schedule_thread():
    """Єдиний таймер-тред: 1 запуск/год (:45 ±2), без дублювань, одразу переносить на наступну годину."""
    global next_report_time, timer_active, last_fired_target
    with state_lock:
        timer_active = True
        if next_report_time is None:
            next_report_time = get_next_slot()
    log_message("✅ Таймер запущено.")

    while True:
        with state_lock:
            active = timer_active
            target = next_report_time
            fired_for_target = (last_fired_target == target)
        if not active:
            break

        now = datetime.now()
        if now >= target and not fired_for_target:
            if not fire_lock.acquire(blocking=False):
                time.sleep(0.1)
                continue
            try:
                log_message(f"⏰ ТАЙМЕР: {target.strftime('%H:%M:%S')} — відправляю автоматично.")
                with state_lock:
                    last_fired_target = target
                # прочитати значення з GUI в головному треді
                def read_and_dispatch():
                    t  = entry.get()
                    pre = pre_paste_delay.get()
                    sd = send_delay.get()
                    focus = focus_before_send.get()
                    threading.Thread(
                        target=do_send_report,
                        args=(t, pre, sd, True, focus),
                        daemon=True
                    ).start()
                root.after(0, read_and_dispatch)

                with state_lock:
                    next_report_time = get_next_hour_slot_from_target(target)
                    log_message(f"📅 Наступна доповідь запланована на {next_report_time.strftime('%H:%M:%S')}")
            finally:
                fire_lock.release()
        time.sleep(0.2)

# -------- Збереження тексту --------
def load_saved_text():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE, encoding="utf-8-sig")
        return cfg.get(CONFIG_SECTION, CONFIG_KEY, fallback="")
    return ""

def save_text(text):
    cfg = configparser.ConfigParser()
    cfg[CONFIG_SECTION] = {CONFIG_KEY: text}
    with open(CONFIG_FILE, "w", encoding="utf-8-sig") as f:
        cfg.write(f)

# -------- GUI --------
root = tk.Tk()
root.title(APP_TITLE)
root.geometry("980x800")

# GUI-змінні
send_delay = tk.DoubleVar(value=SEND_DELAY_S_DEFAULT)   # затримка після Enter
pre_paste_delay = tk.IntVar(value=PRE_TYPE_DELAY_MS_DEFAULT)  # затримка перед друком (мс)
focus_before_send = tk.BooleanVar(value=True)           # фокусувати WasIstLos перед відправкою

notebook = ttk.Notebook(root); notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

# --- Вкладка Основні ---
main_frame = ttk.Frame(notebook); notebook.add(main_frame, text="Основні")

tk.Label(main_frame, text="Введіть текст доповіді:", font=("Arial", 12, "bold")).pack(pady=10)
entry = tk.Entry(main_frame, width=70, font=("Arial", 11))
entry.insert(0, load_saved_text()); entry.pack(pady=5)
entry.bind("<KeyRelease>", lambda e: save_text(entry.get()))

# Опції
opts = tk.LabelFrame(main_frame, text="Опції", font=("Arial", 10, "bold"))
opts.pack(pady=10, padx=20, fill=tk.X)

tk.Checkbutton(
    opts,
    text="Перед відправкою переключатися на вікно WasIstLos/WhatsApp",
    variable=focus_before_send
).pack(anchor="w", padx=10, pady=5)

# Налаштування затримок
delay_frame = tk.LabelFrame(main_frame, text="Налаштування затримок", font=("Arial", 10, "bold"))
delay_frame.pack(pady=10, padx=20, fill=tk.X)

r1 = tk.Frame(delay_frame); r1.pack(fill=tk.X, padx=10, pady=5)
tk.Label(r1, text="Затримка перед друком:", font=("Arial", 10)).pack(side=tk.LEFT)
tk.Spinbox(r1, from_=0, to=5000, increment=50, textvariable=pre_paste_delay, width=10, font=("Arial", 10)).pack(side=tk.RIGHT)
tk.Label(r1, text="мс", font=("Arial", 10)).pack(side=tk.RIGHT, padx=(0,5))

r3 = tk.Frame(delay_frame); r3.pack(fill=tk.X, padx=10, pady=5)
tk.Label(r3, text="Затримка після відправки:", font=("Arial", 10)).pack(side=tk.LEFT)
tk.Spinbox(r3, from_=0.05, to=5.0, increment=0.05, textvariable=send_delay, width=10, font=("Arial", 10)).pack(side=tk.RIGHT)
tk.Label(r3, text="секунд", font=("Arial", 10)).pack(side=tk.RIGHT, padx=(0,5))

# Таймер
timer_frame = tk.LabelFrame(main_frame, text="Автоматичні доповіді", font=("Arial", 10, "bold"))
timer_frame.pack(pady=10, padx=20, fill=tk.X)

btns = tk.Frame(timer_frame); btns.pack(pady=10)
def start_timer():
    global timer_active, timer_thread
    with state_lock:
        if timer_active and timer_thread and timer_thread.is_alive():
            log_message("⚠️ Таймер уже працює (активний тред).")
            return
        timer_active = True
        if next_report_time is None:
            globals()['next_report_time'] = get_next_slot()
    timer_thread = threading.Thread(target=schedule_thread, daemon=True)
    timer_thread.start()
    log_message("▶️ Запуск таймера…")

def stop_timer():
    global timer_active
    with state_lock:
        timer_active = False
    log_message("🛑 Таймер зупинено.")

tk.Button(btns, text="Запустити таймер", command=start_timer, font=("Arial", 10), bg="#4CAF50", fg="white", width=15).pack(side=tk.LEFT, padx=5)
tk.Button(btns, text="Зупинити таймер", command=stop_timer, font=("Arial", 10), bg="#f44336", fg="white", width=15).pack(side=tk.LEFT, padx=5)

timer_label = tk.Label(timer_frame, text="", font=("Arial", 12), fg="#333")
timer_label.pack(pady=10)

# Дії
actions = tk.LabelFrame(main_frame, text="Дії", font=("Arial", 10, "bold"))
actions.pack(pady=10, padx=20, fill=tk.X)

def send_now():
    t = entry.get()
    pre = pre_paste_delay.get()
    sd = send_delay.get()
    focus = focus_before_send.get()
    threading.Thread(
        target=do_send_report,
        args=(t, pre, sd, False, focus),
        daemon=True
    ).start()

def test_insert():
    t = entry.get().strip()
    if not t:
        log_message("⚠️ Текст для тесту порожній.")
        return
    def worker():
        log_message("🧪 Тест: друк (без Enter)…")
        focus = focus_before_send.get()
        if focus and not ensure_whatsapp_focused("[Тест] "):
            log_message("❌ Тест: не вдалося сфокусувати WhatsApp.")
            return
        ok = paste_and_send(
            t, do_send=False,
            pre_ms=pre_paste_delay.get(),
            send_delay_s=send_delay.get()
        )
        if ok: log_message("🎉 Друк пройшов (без відправки).")
        else:  log_message("❌ Не вдалося надрукувати у тесті.")
    threading.Thread(target=worker, daemon=True).start()

def diagnose():
    log_message("🔬 Діагностика середовища:")
    log_message(f"  hyprctl: {HAS_HYPRCTL}")
    log_message(f"  wtype:   {HAS_WTYPE}")
    c = find_wasistlos_client()
    if c:
        log_message(f"  Знайдено клієнт: class='{c.get('class')}', title='{c.get('title')}', addr={c.get('address')}, ws={c.get('workspace', {}).get('id')}")
    else:
        log_message("  Клієнт WasIstLos/WhatsApp не знайдений. Відкрий клієнт.")

row = tk.Frame(actions); row.pack(pady=10)
tk.Button(row, text="Відправити зараз", command=send_now, font=("Arial", 9), bg="#2196F3", fg="white", width=17).pack(side=tk.LEFT, padx=3)
tk.Button(row, text="Тест вставлення", command=test_insert, font=("Arial", 9), bg="#FF9800", fg="white", width=17).pack(side=tk.LEFT, padx=3)
tk.Button(row, text="Діагностика", command=diagnose, font=("Arial", 9), bg="#9C27B0", fg="white", width=17).pack(side=tk.LEFT, padx=3)

# --- Вкладка Логи ---
log_tab = ttk.Frame(notebook); notebook.add(log_tab, text="Логи")
log_header = tk.Frame(log_tab); log_header.pack(fill=tk.X, padx=10, pady=5)
tk.Label(log_header, text="Логи:", font=("Arial", 12, "bold")).pack(side=tk.LEFT)
def clear_log():
    log_text.delete(1.0, tk.END)
tk.Button(log_header, text="Очистити", command=clear_log, font=("Arial", 10), bg="#607D8B", fg="white").pack(side=tk.RIGHT)

log_text = tk.Text(log_tab, wrap=tk.WORD, font=("Consolas", 10), bg="#f5f5f5", fg="#333")
log_scroll = tk.Scrollbar(log_tab, command=log_text.yview)
log_text.config(yscrollcommand=log_scroll.set)
log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10,0), pady=10)
log_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0,10), pady=10)

# --- Лог-помпа ---
def pump_logs():
    try:
        while True:
            line = log_q.get_nowait()
            log_text.insert(tk.END, line)
            log_text.see(tk.END)
    except queue.Empty:
        pass
    root.after(50, pump_logs)

# --- Таймерний лейбл (показ завжди) ---
def compute_display_target():
    with state_lock:
        target = next_report_time
    return target if target is not None else get_next_slot()

def update_timer_label():
    target = compute_display_target()
    now = datetime.now()
    remaining = target - now
    if remaining.total_seconds() < 0:
        target = get_next_slot(now + timedelta(seconds=1))
        remaining = target - now
    mins, secs = divmod(int(remaining.total_seconds()), 60)
    hours, mins = divmod(mins, 60)
    with state_lock:
        active = timer_active
    status = "🟢 Таймер активний" if active else "⚪ Таймер вимкнений"
    timer_label.config(
        text=f"{status}\nНаступна доповідь: {target.strftime('%H:%M:%S')}\nЗалишилось: {hours:02d}:{mins:02d}:{secs:02d}"
    )
    root.after(200, update_timer_label)

# --- Старт ---
root.title(APP_TITLE)
log_message("🚀 Запуск (Wayland/Hyprland). Потрібні: hyprctl, wtype.")

root.after(0, pump_logs)
root.after(0, update_timer_label)
root.mainloop()

