#include <WiFi.h>
#include <WebServer.h>
#include <DHT.h>

// ================= CONFIG =================
#define DHTPIN 27
#define DHTTYPE DHT21   // AM2301 = DHT21

const char* ssid = "ESP32";
const char* password = "Totoladrillo123";

// IP fija solicitada
IPAddress local_IP(10, 129, 197, 172);
IPAddress gateway(10, 129, 197, 1);
IPAddress subnet(255, 255, 255, 0);
IPAddress primaryDNS(8, 8, 8, 8);
IPAddress secondaryDNS(8, 8, 4, 4);
// ==========================================

DHT dht(DHTPIN, DHTTYPE);
WebServer server(80);

// --------- HTML PRINCIPAL ----------
void handleRoot() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  String html = "<!DOCTYPE html><html><head>";
  html += "<meta charset='UTF-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<meta http-equiv='refresh' content='5'>";
  html += "<title>ESP32 Sensor</title>";
  html += "<style>";
  html += "body{font-family:Arial;text-align:center;background:#111;color:#0f0;}";
  html += ".box{margin-top:40px;font-size:28px;}";
  html += "</style></head><body>";

  html += "<h1>ESP32 + DHT21</h1>";

  if (isnan(t) || isnan(h)) {
    html += "<div class='box'>Error leyendo sensor</div>";
  } else {
    html += "<div class='box'>🌡 Temperatura: " + String(t,1) + " °C</div>";
    html += "<div class='box'>💧 Humedad: " + String(h,1) + " %</div>";
  }

  html += "<p>Actualiza cada 5 segundos</p>";
  html += "</body></html>";

  server.send(200, "text/html", html);
}

// --------- JSON PARA PYTHON ----------
void handleData() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  if (isnan(t) || isnan(h)) {
    server.send(500, "application/json", "{\"error\":true}");
    return;
  }

  String json = "{";
  json += "\"temp\":" + String(t,1) + ",";
  json += "\"hum\":" + String(h,1);
  json += "}";

  server.send(200, "application/json", json);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\nIniciando ESP32...");

  // --------- WIFI ----------
  WiFi.mode(WIFI_STA);

  if (!WiFi.config(local_IP, gateway, subnet, primaryDNS, secondaryDNS)) {
    Serial.println("Error configurando IP fija");
  }

  WiFi.begin(ssid, password);

  Serial.print("Conectando a WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi conectado");
  Serial.print("IP ESP32: ");
  Serial.println(WiFi.localIP());

  // --------- SENSOR ----------
  dht.begin();

  // --------- SERVIDOR ----------
  server.on("/", handleRoot);
  server.on("/data", handleData);
  server.begin();

  Serial.println("Servidor HTTP iniciado");
}

void loop() {
  server.handleClient();
}

