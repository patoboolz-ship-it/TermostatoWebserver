import os
import json
import time
import threading
from datetime import datetime, timedelta

import requests
import pandas as pd

import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ================== CONFIG ==================
ESP32_IP = "10.129.197.172"
URL = f"http://{ESP32_IP}/data"

DATA_DIR = "data"
HIST_FILE = os.path.join(DATA_DIR, "historico.json")

POLL_SECONDS = 1           # cada cuántos segundos leo el ESP32
SAVE_EVERY_N_SAMPLES = 2   # guarda al archivo cada N lecturas (1 = siempre)
HTTP_TIMEOUT = 3
# ============================================


def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HIST_FILE):
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def load_history():
    try:
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def append_history(record, max_records=None):
    """
    Agrega un registro al JSON.
    Si max_records está definido, recorta historial al final (evita que crezca infinito).
    """
    hist = load_history()
    hist.append(record)
    if max_records is not None and len(hist) > max_records:
        hist = hist[-max_records:]
    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)


def history_to_df():
    hist = load_history()
    if not hist:
        return pd.DataFrame(columns=["fecha", "temperatura", "humedad"]).set_index("fecha")
    df = pd.DataFrame(hist)
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["fecha"])
    df = df.set_index("fecha").sort_index()
    return df


class CollectorThread(threading.Thread):
    """
    Hilo en segundo plano:
    - consulta /data del ESP32
    - actualiza 'latest' (lectura actual)
    - guarda histórico en JSON
    """
    def __init__(self, latest_ref, latest_lock, status_ref, stop_event):
        super().__init__(daemon=True)
        self.latest_ref = latest_ref
        self.latest_lock = latest_lock
        self.status_ref = status_ref
        self.stop_event = stop_event
        self.session = requests.Session()
        self._counter = 0

    def run(self):
        ensure_storage()

        while not self.stop_event.is_set():
            start = time.time()
            try:
                self.status_ref["state"] = "leyendo"
                r = self.session.get(URL, timeout=HTTP_TIMEOUT)
                r.raise_for_status()

                data = r.json()
                temp = float(data.get("temp"))
                hum = float(data.get("hum"))

                now = datetime.now().isoformat(timespec="seconds")
                record = {"fecha": now, "temperatura": temp, "humedad": hum}

                with self.latest_lock:
                    self.latest_ref["temp"] = temp
                    self.latest_ref["hum"] = hum
                    self.latest_ref["ts"] = now

                self._counter += 1
                if self._counter % SAVE_EVERY_N_SAMPLES == 0:
                    append_history(record, max_records=200000)  # ajusta si quieres

                self.status_ref["state"] = "ok"
                self.status_ref["last_error"] = ""

            except Exception as e:
                self.status_ref["state"] = "error"
                self.status_ref["last_error"] = str(e)

            # dormir respetando el periodo
            elapsed = time.time() - start
            sleep_for = max(0.2, POLL_SECONDS - elapsed)
            self.stop_event.wait(sleep_for)


class TrendsWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Tendencias (Histórico)")
        self.geometry("900x520")

        # Top bar
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(top, text="Periodo:").pack(side="left")

        self.period_var = tk.StringVar(value="24h")
        period = ttk.Combobox(
            top,
            textvariable=self.period_var,
            values=["1h", "6h", "24h", "7d", "30d", "Todo"],
            width=10,
            state="readonly"
        )
        period.pack(side="left", padx=8)

        ttk.Button(top, text="Actualizar", command=self.refresh_plot).pack(side="left", padx=8)
        ttk.Button(top, text="Cerrar", command=self.destroy).pack(side="right")

        # Figure
        self.fig = Figure(figsize=(9, 4.8), dpi=100)
        self.ax1 = self.fig.add_subplot(211)
        self.ax2 = self.fig.add_subplot(212)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)

        self.refresh_plot()
                # Auto-actualizar tendencias cada 1 segundo
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
            return  # si la ventana se cerró, no seguir

        self.refresh_plot()
        self.after(1000, self.auto_refresh)


    def refresh_plot(self):

        df = history_to_df()
        df = self._filter_df(df)

        self.ax1.clear()
        self.ax2.clear()

        if df.empty:
            self.ax1.set_title("Sin datos aún (espera que se guarde historial)")
            self.canvas.draw()
            return

        # Plot temp
        self.ax1.plot(df.index, df["temperatura"])
        self.ax1.set_title("Temperatura (°C)")
        self.ax1.set_ylabel("°C")
        self.ax1.grid(True, alpha=0.3)

        # Plot hum
        self.ax2.plot(df.index, df["humedad"])
        self.ax2.set_title("Humedad (%)")
        self.ax2.set_ylabel("%")
        self.ax2.set_xlabel("Tiempo")
        self.ax2.grid(True, alpha=0.3)

        self.fig.tight_layout()
        self.canvas.draw()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HMI ESP32 - Temperatura y Humedad")
        self.geometry("520x320")

        self.latest = {"temp": None, "hum": None, "ts": None}
        self.latest_lock = threading.Lock()
        self.status = {"state": "init", "last_error": ""}

        self.stop_event = threading.Event()
        self.collector = CollectorThread(self.latest, self.latest_lock, self.status, self.stop_event)
        self.collector.start()

        ensure_storage()

        # UI
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="ESP32 Sensor (DHT21/AM2301)", font=("Segoe UI", 14, "bold"))
        title.pack(anchor="w", pady=(0, 10))

        self.lbl_ip = ttk.Label(root, text=f"ESP32: {ESP32_IP}   Endpoint: /data")
        self.lbl_ip.pack(anchor="w", pady=(0, 12))

        cards = ttk.Frame(root)
        cards.pack(fill="x")

        self.temp_var = tk.StringVar(value="--.- °C")
        self.hum_var = tk.StringVar(value="--.- %")
        self.ts_var = tk.StringVar(value="Última lectura: -")
        self.st_var = tk.StringVar(value="Estado: iniciando...")

        # “Tarjetas”
        left = ttk.LabelFrame(cards, text="Temperatura", padding=12)
        right = ttk.LabelFrame(cards, text="Humedad", padding=12)
        left.pack(side="left", expand=True, fill="both", padx=(0, 8))
        right.pack(side="left", expand=True, fill="both")

        ttk.Label(left, textvariable=self.temp_var, font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(right, textvariable=self.hum_var, font=("Segoe UI", 22, "bold")).pack()

        ttk.Label(root, textvariable=self.ts_var).pack(anchor="w", pady=(12, 2))
        ttk.Label(root, textvariable=self.st_var).pack(anchor="w", pady=(0, 12))

        btns = ttk.Frame(root)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(btns, text="Tendencias", command=self.open_trends).pack(side="left")
        ttk.Button(btns, text="Abrir /data en navegador", command=self.open_data).pack(side="left", padx=8)
        ttk.Button(btns, text="Salir", command=self.on_close).pack(side="right")

        self.trends_win = None

        # refresh UI loop
        self.after(500, self.refresh_ui)

        # close handler
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def open_data(self):
        import webbrowser
        webbrowser.open(f"http://{ESP32_IP}/data")

    def open_trends(self):
        # “otra pestaña” = otra ventana que puedes cerrar
        if self.trends_win is None or not self.trends_win.winfo_exists():
            self.trends_win = TrendsWindow(self)
        else:
            self.trends_win.lift()

    def refresh_ui(self):
        with self.latest_lock:
            temp = self.latest["temp"]
            hum = self.latest["hum"]
            ts = self.latest["ts"]

        if temp is not None:
            self.temp_var.set(f"{temp:.1f} °C")
        else:
            self.temp_var.set("--.- °C")

        if hum is not None:
            self.hum_var.set(f"{hum:.1f} %")
        else:
            self.hum_var.set("--.- %")

        self.ts_var.set(f"Última lectura: {ts if ts else '-'}")

        st = self.status.get("state", "init")
        err = self.status.get("last_error", "")

        if st == "ok":
            self.st_var.set("Estado: OK (guardando histórico en segundo plano)")
        elif st == "leyendo":
            self.st_var.set("Estado: leyendo...")
        elif st == "error":
            # mostramos el error, pero no explotamos
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
