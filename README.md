# TermostatoWebserver

Este repositorio contiene **dos códigos principales** que trabajan juntos para monitorear temperatura y humedad con un sensor DHT21 (AM2301):

1. **`humedad_y_temp_por_webserver.ino` (Arduino/ESP32)**  
   Levanta un servidor web en el ESP32 y expone:
   - `GET /` → Página HTML con temperatura y humedad en vivo.
   - `GET /data` → Respuesta JSON para consumo por otras aplicaciones.

2. **`humedad y temp por webserver.py` (Python)**  
   Consume periódicamente `http://<ESP32_IP>/data`, guarda histórico en JSON y muestra una interfaz HMI con tendencias.

---

## Flujo general

```text
DHT21/AM2301 -> ESP32 (/data JSON) -> App Python -> Histórico + Tendencias
```

---

## Requisitos

### ESP32
- Librerías:
  - `WiFi.h`
  - `WebServer.h`
  - `DHT.h`
- Configurar en el `.ino`:
  - `ssid`
  - `password`
  - pin del sensor (`DHTPIN`) y tipo (`DHTTYPE`)

### Python
- Python 3.9+
- Dependencias:
  - `requests`
  - `pandas`
  - `matplotlib`
  - `tkinter` (normalmente viene con Python en instalaciones de escritorio)

Instalación sugerida:

```bash
pip install requests pandas matplotlib
```

---

## Uso rápido

1. Cargar `humedad_y_temp_por_webserver.ino` al ESP32.
2. Verificar por serial la IP asignada al ESP32.
3. Actualizar `ESP32_IP` en `humedad y temp por webserver.py`.
4. Ejecutar la app Python:

```bash
python3 "humedad y temp por webserver.py"
```

---

## Archivos de datos

La aplicación Python crea y usa:

- Carpeta `data/`
- Archivo `data/historico.json`

Allí se guarda el historial de muestras para graficar tendencias.
