import os
import json
import time
import threading
from datetime import datetime, timedelta

import requests
import pandas as pd

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ================== CONFIG ==================
STATION_NAMES = [
    "Vespucio Norte",
    "Zapadores",
    "Dorsal",
    "Einstein",
    "Cementerios",
    "Cerro Blanco",
    "Patronato",
    "Puente Cal y Canto",
    "Santa Ana",
    "Los Héroes",
    "Toesca",
    "Parque O'Higgins",
    "Rondizzoni",
    "Franklin",
    "El Llano",
    "San Miguel",
    "Lo Vial",
    "Departamental",
    "Ciudad del Niño",
    "Lo Ovalle",
    "El Parrón",
    "La Cisterna",
    "El Bosque",
    "Observatorio",
    "Copa Lo Martínez",
    "Hospital El Pino",
]

DATA_DIR = "data"
HIST_FILE = os.path.join(DATA_DIR, "historico.json")
STATIONS_FILE = os.path.join(DATA_DIR, "estaciones.json")

POLL_SECONDS = 1
SAVE_EVERY_N_SAMPLES = 2
HTTP_TIMEOUT = 3
# ============================================


def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(HIST_FILE):
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)

    if not os.path.exists(STATIONS_FILE):
        initial = {name: "" for name in STATION_NAMES}
        with open(STATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(initial, f, indent=2, ensure_ascii=False)


def load_station_ips():
    ensure_storage()
    try:
        with open(STATIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}

    merged = {name: str(data.get(name, "")).strip() for name in STATION_NAMES}
    return merged


def save_station_ips(station_ips: dict):
    cleaned = {name: str(station_ips.get(name, "")).strip() for name in STATION_NAMES}
    with open(STATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)


def load_history():
    try:
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_history(record, max_records=None):
    hist = load_history()
    hist.append(record)
    if max_records is not None and len(hist) > max_records:
        hist = hist[-max_records:]

    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)


def history_to_df(station_name=None):
    hist = load_history()
    if not hist:
        return pd.DataFrame(columns=["fecha", "estacion", "temperatura", "humedad"]).set_index("fecha")

    df = pd.DataFrame(hist)
    if "estacion" not in df.columns:
        df["estacion"] = "(sin estación)"

    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])

    if station_name:
        df = df[df["estacion"] == station_name]

    if df.empty:
        return pd.DataFrame(columns=["estacion", "temperatura", "humedad"], index=pd.DatetimeIndex([], name="fecha"))

    return df.set_index("fecha").sort_index()


class CollectorThread(threading.Thread):
    """
    Hilo en segundo plano:
    - consulta /data del ESP32 de la estación seleccionada
    - actualiza lectura actual
    - guarda histórico incluyendo estación
    """

    def __init__(self, latest_ref, latest_lock, status_ref, stop_event, station_state_ref, station_state_lock):
        super().__init__(daemon=True)
        self.latest_ref = latest_ref
        self.latest_lock = latest_lock
        self.status_ref = status_ref
        self.stop_event = stop_event
        self.station_state_ref = station_state_ref
        self.station_state_lock = station_state_lock
        self.session = requests.Session()
        self._counter = 0

    def run(self):
        ensure_storage()

        while not self.stop_event.is_set():
            start = time.time()

            with self.station_state_lock:
                station_name = self.station_state_ref["current_station"]
                station_ip = self.station_state_ref["station_ips"].get(station_name, "").strip()

            if not station_ip:
                self.status_ref["state"] = "no_ip"
                self.status_ref["last_error"] = f"La estación '{station_name}' no tiene IP configurada"
                self.stop_event.wait(0.5)
                continue

            url = f"http://{station_ip}/data"
            try:
                self.status_ref["state"] = "leyendo"
                r = self.session.get(url, timeout=HTTP_TIMEOUT)
                r.raise_for_status()

                data = r.json()
                temp = float(data.get("temp"))
                hum = float(data.get("hum"))

                now = datetime.now().isoformat(timespec="seconds")
                record = {
                    "fecha": now,
                    "estacion": station_name,
                    "ip": station_ip,
                    "temperatura": temp,
                    "humedad": hum,
                }

                with self.latest_lock:
                    self.latest_ref["temp"] = temp
                    self.latest_ref["hum"] = hum
                    self.latest_ref["ts"] = now
                    self.latest_ref["station"] = station_name
                    self.latest_ref["ip"] = station_ip

                self._counter += 1
                if self._counter % SAVE_EVERY_N_SAMPLES == 0:
                    append_history(record, max_records=200000)

                self.status_ref["state"] = "ok"
                self.status_ref["last_error"] = ""
            except Exception as e:
                self.status_ref["state"] = "error"
                self.status_ref["last_error"] = f"{station_name} ({station_ip}) -> {e}"

            elapsed = time.time() - start
            sleep_for = max(0.2, POLL_SECONDS - elapsed)
            self.stop_event.wait(sleep_for)


class TrendsWindow(tk.Toplevel):
    def __init__(self, master, station_name_getter):
        super().__init__(master)
        self.station_name_getter = station_name_getter
        self.title("Tendencias (Histórico)")
        self.geometry("920x560")

        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        self.station_lbl = ttk.Label(top, text="Estación: -")
        self.station_lbl.pack(side="left", padx=(0, 16))

        ttk.Label(top, text="Periodo:").pack(side="left")

        self.period_var = tk.StringVar(value="24h")
        period = ttk.Combobox(
            top,
            textvariable=self.period_var,
            values=["1h", "6h", "24h", "7d", "30d", "Todo"],
            width=10,
            state="readonly",
        )
        period.pack(side="left", padx=8)

        ttk.Button(top, text="Actualizar", command=self.refresh_plot).pack(side="left", padx=8)
        ttk.Button(top, text="Cerrar", command=self.destroy).pack(side="right")

        self.fig = Figure(figsize=(9, 5.2), dpi=100)
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        self.refresh_plot()
        self.after(1000, self.auto_refresh)

    def _filter_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        p = self.period_var.get()
        now = df.index.max()

        if p == "Todo":
            return df

        if p.endswith("h"):
            hours = int(p[:-1])
            return df[df.index >= (now - timedelta(hours=hours))]

        if p.endswith("d"):
            days = int(p[:-1])
            return df[df.index >= (now - timedelta(days=days))]

        return df

    def auto_refresh(self):
        if not self.winfo_exists():
            return
        self.refresh_plot()
        self.after(1000, self.auto_refresh)

    def refresh_plot(self):
        station_name = self.station_name_getter()
        self.station_lbl.configure(text=f"Estación: {station_name}")

        df = history_to_df(station_name=station_name)
        df = self._filter_df(df)

        self.ax1.clear()
        self.ax2.clear()

        if df.empty:
            self.ax1.set_title(f"Sin datos para {station_name} (aún)")
            self.ax2.set_title("")
            self.canvas.draw()
            return

        self.ax1.plot(df.index, df["temperatura"])
        self.ax1.set_title(f"Temperatura (°C) - {station_name}")
        self.ax1.set_ylabel("°C")
        self.ax1.grid(True, alpha=0.3)

        self.ax2.plot(df.index, df["humedad"])
        self.ax2.set_title(f"Humedad (%) - {station_name}")
        self.ax2.set_ylabel("%")
        self.ax2.set_xlabel("Tiempo")
        self.ax2.grid(True, alpha=0.3)

        self.fig.tight_layout()
        self.canvas.draw()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HMI Multi-Estación ESP32 - Temperatura y Humedad")
        self.geometry("900x600")

        ensure_storage()

        self.station_state_lock = threading.Lock()
        self.station_state = {
            "station_ips": load_station_ips(),
            "current_station": STATION_NAMES[0],
        }

        self.latest = {"temp": None, "hum": None, "ts": None, "station": None, "ip": None}
        self.latest_lock = threading.Lock()
        self.status = {"state": "init", "last_error": ""}

        self.stop_event = threading.Event()
        self.collector = CollectorThread(
            self.latest,
            self.latest_lock,
            self.status,
            self.stop_event,
            self.station_state,
            self.station_state_lock,
        )
        self.collector.start()

        self.current_station_var = tk.StringVar(value=STATION_NAMES[0])
        self.current_ip_var = tk.StringVar()

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="Monitoreo de 26 estaciones ESP32", font=("Segoe UI", 15, "bold"))
        title.pack(anchor="w", pady=(0, 10))

        top = ttk.Frame(root)
        top.pack(fill="x", pady=(0, 10))

        ttk.Label(top, text="Estación actual:").pack(side="left")
        self.station_combo = ttk.Combobox(
            top,
            textvariable=self.current_station_var,
            values=STATION_NAMES,
            state="readonly",
            width=28,
        )
        self.station_combo.pack(side="left", padx=8)
        self.station_combo.bind("<<ComboboxSelected>>", self.on_station_changed)

        ttk.Button(top, text="◀ Anterior", command=self.select_prev_station).pack(side="left", padx=4)
        ttk.Button(top, text="Siguiente ▶", command=self.select_next_station).pack(side="left", padx=4)

        self.lbl_current_ip = ttk.Label(top, text="IP: -")
        self.lbl_current_ip.pack(side="left", padx=14)

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)

        left_col = ttk.Frame(body)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right_col = ttk.LabelFrame(body, text="Configurar IP por estación", padding=10)
        right_col.pack(side="left", fill="both", expand=True)

        cards = ttk.Frame(left_col)
        cards.pack(fill="x")

        self.temp_var = tk.StringVar(value="--.- °C")
        self.hum_var = tk.StringVar(value="--.- %")
        self.ts_var = tk.StringVar(value="Última lectura: -")
        self.st_var = tk.StringVar(value="Estado: iniciando...")

        temp_card = ttk.LabelFrame(cards, text="Temperatura", padding=12)
        hum_card = ttk.LabelFrame(cards, text="Humedad", padding=12)
        temp_card.pack(side="left", expand=True, fill="both", padx=(0, 8))
        hum_card.pack(side="left", expand=True, fill="both")

        ttk.Label(temp_card, textvariable=self.temp_var, font=("Segoe UI", 24, "bold")).pack()
        ttk.Label(hum_card, textvariable=self.hum_var, font=("Segoe UI", 24, "bold")).pack()

        ttk.Label(left_col, textvariable=self.ts_var).pack(anchor="w", pady=(12, 2))
        ttk.Label(left_col, textvariable=self.st_var, wraplength=420).pack(anchor="w", pady=(0, 12))

        btns = ttk.Frame(left_col)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(btns, text="Tendencias estación actual", command=self.open_trends).pack(side="left")
        ttk.Button(btns, text="Abrir /data", command=self.open_data).pack(side="left", padx=8)
        ttk.Button(btns, text="Salir", command=self.on_close).pack(side="right")

        filter_frame = ttk.Frame(right_col)
        filter_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_frame, text="Buscar estación:").pack(side="left")
        self.filter_var = tk.StringVar()
        entry_filter = ttk.Entry(filter_frame, textvariable=self.filter_var)
        entry_filter.pack(side="left", fill="x", expand=True, padx=8)
        entry_filter.bind("<KeyRelease>", self.apply_station_filter)

        list_frame = ttk.Frame(right_col)
        list_frame.pack(fill="both", expand=True)

        self.station_listbox = tk.Listbox(list_frame, height=18)
        self.station_listbox.pack(side="left", fill="both", expand=True)
        self.station_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)

        yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.station_listbox.yview)
        yscroll.pack(side="left", fill="y")
        self.station_listbox.configure(yscrollcommand=yscroll.set)

        edit_frame = ttk.Frame(right_col)
        edit_frame.pack(fill="x", pady=(10, 0))

        ttk.Label(edit_frame, text="Estación seleccionada:").grid(row=0, column=0, sticky="w", pady=3)
        self.selected_station_var = tk.StringVar(value=STATION_NAMES[0])
        ttk.Label(edit_frame, textvariable=self.selected_station_var).grid(row=0, column=1, sticky="w", pady=3)

        ttk.Label(edit_frame, text="IP: ").grid(row=1, column=0, sticky="w", pady=3)
        self.ip_entry_var = tk.StringVar()
        ttk.Entry(edit_frame, textvariable=self.ip_entry_var, width=20).grid(row=1, column=1, sticky="w", pady=3)

        action_frame = ttk.Frame(right_col)
        action_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(action_frame, text="Guardar IP", command=self.save_selected_station_ip).pack(side="left")
        ttk.Button(action_frame, text="Limpiar IP", command=self.clear_selected_station_ip).pack(side="left", padx=8)
        ttk.Button(action_frame, text="Usar esta estación ahora", command=self.set_current_from_selected).pack(side="left")

        self.filtered_stations = STATION_NAMES[:]
        self.refresh_station_listbox()
        self.set_current_station(STATION_NAMES[0])
        self.select_station_in_editor(STATION_NAMES[0])

        self.trends_win = None
        self.after(500, self.refresh_ui)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def apply_station_filter(self, *_):
        term = self.filter_var.get().strip().lower()
        if not term:
            self.filtered_stations = STATION_NAMES[:]
        else:
            self.filtered_stations = [s for s in STATION_NAMES if term in s.lower()]
        self.refresh_station_listbox()

    def refresh_station_listbox(self):
        self.station_listbox.delete(0, tk.END)
        with self.station_state_lock:
            station_ips = dict(self.station_state["station_ips"])

        for name in self.filtered_stations:
            ip = station_ips.get(name, "").strip()
            status = ip if ip else "(sin IP)"
            self.station_listbox.insert(tk.END, f"{name}   |   {status}")

    def on_listbox_select(self, _event=None):
        if not self.station_listbox.curselection():
            return
        idx = self.station_listbox.curselection()[0]
        if idx < 0 or idx >= len(self.filtered_stations):
            return
        station = self.filtered_stations[idx]
        self.select_station_in_editor(station)

    def select_station_in_editor(self, station_name):
        with self.station_state_lock:
            ip = self.station_state["station_ips"].get(station_name, "")
        self.selected_station_var.set(station_name)
        self.ip_entry_var.set(ip)

    def set_current_from_selected(self):
        self.set_current_station(self.selected_station_var.get())

    def set_current_station(self, station_name):
        with self.station_state_lock:
            if station_name not in self.station_state["station_ips"]:
                return
            self.station_state["current_station"] = station_name
            ip = self.station_state["station_ips"].get(station_name, "")

        self.current_station_var.set(station_name)
        self.current_ip_var.set(ip)
        self.lbl_current_ip.configure(text=f"IP: {ip if ip else '(sin configurar)'}")

    def on_station_changed(self, _event=None):
        self.set_current_station(self.current_station_var.get())

    def select_prev_station(self):
        current = self.current_station_var.get()
        i = STATION_NAMES.index(current)
        self.set_current_station(STATION_NAMES[(i - 1) % len(STATION_NAMES)])

    def select_next_station(self):
        current = self.current_station_var.get()
        i = STATION_NAMES.index(current)
        self.set_current_station(STATION_NAMES[(i + 1) % len(STATION_NAMES)])

    def save_selected_station_ip(self):
        station = self.selected_station_var.get().strip()
        ip = self.ip_entry_var.get().strip()

        if not station:
            return

        with self.station_state_lock:
            self.station_state["station_ips"][station] = ip
            save_station_ips(self.station_state["station_ips"])

        if self.current_station_var.get() == station:
            self.set_current_station(station)

        self.refresh_station_listbox()
        messagebox.showinfo("Guardado", f"IP de '{station}' guardada correctamente.")

    def clear_selected_station_ip(self):
        station = self.selected_station_var.get().strip()
        if not station:
            return

        with self.station_state_lock:
            self.station_state["station_ips"][station] = ""
            save_station_ips(self.station_state["station_ips"])

        if self.current_station_var.get() == station:
            self.set_current_station(station)

        self.ip_entry_var.set("")
        self.refresh_station_listbox()

    def current_station_name(self):
        return self.current_station_var.get()

    def current_station_ip(self):
        with self.station_state_lock:
            return self.station_state["station_ips"].get(self.current_station_var.get(), "").strip()

    def open_data(self):
        import webbrowser

        ip = self.current_station_ip()
        if not ip:
            messagebox.showwarning("Sin IP", "La estación actual no tiene IP configurada.")
            return
        webbrowser.open(f"http://{ip}/data")

    def open_trends(self):
        if self.trends_win is None or not self.trends_win.winfo_exists():
            self.trends_win = TrendsWindow(self, station_name_getter=self.current_station_name)
        else:
            self.trends_win.lift()

    def refresh_ui(self):
        with self.latest_lock:
            temp = self.latest["temp"]
            hum = self.latest["hum"]
            ts = self.latest["ts"]
            station = self.latest["station"]
            ip = self.latest["ip"]

        if temp is not None:
            self.temp_var.set(f"{temp:.1f} °C")
        else:
            self.temp_var.set("--.- °C")

        if hum is not None:
            self.hum_var.set(f"{hum:.1f} %")
        else:
            self.hum_var.set("--.- %")

        if ts:
            self.ts_var.set(f"Última lectura: {ts} | {station} ({ip})")
        else:
            self.ts_var.set("Última lectura: -")

        st = self.status.get("state", "init")
        err = self.status.get("last_error", "")

        if st == "ok":
            self.st_var.set("Estado: OK (guardando histórico por estación)")
        elif st == "leyendo":
            self.st_var.set(f"Estado: leyendo {self.current_station_var.get()}...")
        elif st == "no_ip":
            self.st_var.set(f"Estado: sin IP -> {err}")
        elif st == "error":
            self.st_var.set(f"Estado: ERROR -> {err}")
        else:
            self.st_var.set("Estado: iniciando...")

        self.after(500, self.refresh_ui)

    def on_close(self):
        self.stop_event.set()
        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    app = App()
    app.mainloop()
