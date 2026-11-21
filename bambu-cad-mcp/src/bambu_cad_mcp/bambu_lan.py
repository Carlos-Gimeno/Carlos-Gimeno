"""Local-network (LAN) control of a Bambu Lab printer — EXPERIMENTAL.

Uses the same local protocol as Bambu Studio in LAN mode:
  * FTPS (implicit TLS, port 990) to upload the sliced ``.gcode.3mf``
  * MQTT over TLS (port 8883, user ``bblp``, password = LAN access code)

Requirements on the printer (screen → Settings → Network/LAN):
  * "LAN Only" mode, and on recent firmware "Developer Mode" enabled so that
    third-party tools are allowed to control it.
  * Note the IP address, the serial number and the 8-digit access code.

Configuration via environment variables (never hard-code them):
  BAMBU_PRINTER_IP, BAMBU_PRINTER_SERIAL, BAMBU_ACCESS_CODE
  BAMBU_CA_CERT (optional): path to the printer CA certificate to verify TLS.
"""

from __future__ import annotations

import ftplib
import hashlib
import json
import os
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrinterConn:
    ip: str
    serial: str
    access_code: str

    @classmethod
    def from_env(cls) -> "PrinterConn":
        ip = os.environ.get("BAMBU_PRINTER_IP")
        serial = os.environ.get("BAMBU_PRINTER_SERIAL")
        code = os.environ.get("BAMBU_ACCESS_CODE")
        missing = [n for n, v in [("BAMBU_PRINTER_IP", ip), ("BAMBU_PRINTER_SERIAL", serial),
                                  ("BAMBU_ACCESS_CODE", code)] if not v]
        if missing:
            raise RuntimeError(
                "Faltan variables de entorno para conectar con la P2S: " + ", ".join(missing)
                + ". Añádelas en la config del MCP (ver README)."
            )
        return cls(ip, serial, code)  # type: ignore[arg-type]


def _tls_context() -> ssl.SSLContext:
    ca = os.environ.get("BAMBU_CA_CERT")
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
        ctx.check_hostname = False  # the cert CN is the serial number, not the IP
        return ctx
    # The printer uses a self-signed certificate. Only reachable on your LAN.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS with implicit TLS (port 990) and TLS session reuse on the data channel."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, session=self.sock.session)
        return conn, size


def upload_file(conn: PrinterConn, local: Path, remote_name: str | None = None) -> str:
    remote_name = remote_name or local.name
    ftp = _ImplicitFTPS(context=_tls_context(), timeout=30)
    ftp.connect(conn.ip, 990)
    ftp.login("bblp", conn.access_code)
    ftp.prot_p()
    with open(local, "rb") as fh:
        ftp.storbinary(f"STOR {remote_name}", fh)
    try:
        ftp.quit()
    except Exception:
        ftp.close()
    return remote_name


def _mqtt_client(conn: PrinterConn):
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Instala paho-mqtt: pip install 'bambu-cad-mcp[printer]'") from exc
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bambu-cad-mcp-{int(time.time())}")
    except AttributeError:  # paho < 2
        client = mqtt.Client(client_id=f"bambu-cad-mcp-{int(time.time())}")
    client.username_pw_set("bblp", conn.access_code)
    client.tls_set_context(_tls_context())
    client.tls_insecure_set(True)
    return client


def mqtt_request(conn: PrinterConn, payload: dict | None, wait_report: bool = True, timeout: float = 10.0) -> dict:
    """Publish ``payload`` (if any) and return the first full status report."""
    client = _mqtt_client(conn)
    report: dict = {}
    got = threading.Event()
    connected = threading.Event()
    req_topic = f"device/{conn.serial}/request"

    def on_connect(c, userdata, flags, rc, properties=None):
        c.subscribe(f"device/{conn.serial}/report")
        connected.set()

    def on_message(c, userdata, msg):
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        p = data.get("print", {})
        if "gcode_state" in p or "nozzle_temper" in p:
            report.update(p)
            if "gcode_state" in p:
                got.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(conn.ip, 8883, keepalive=30)
    client.loop_start()
    try:
        if not connected.wait(timeout):
            raise TimeoutError("No se pudo conectar por MQTT con la impresora (¿IP, modo LAN, código de acceso?)")
        if payload is not None:
            client.publish(req_topic, json.dumps(payload))
        if wait_report:
            client.publish(req_topic, json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
            got.wait(timeout)
    finally:
        client.loop_stop()
        client.disconnect()
    return report


def summarize_status(r: dict) -> str:
    if not r:
        return "No llegó ningún informe de estado de la impresora."
    rem = r.get("mc_remaining_time")
    lines = [
        f"Estado: {r.get('gcode_state', '?')}",
        f"Progreso: {r.get('mc_percent', '?')} %  ·  capa {r.get('layer_num', '?')}/{r.get('total_layer_num', '?')}",
        f"Tiempo restante: {rem} min" if rem is not None else "Tiempo restante: ?",
        f"Boquilla: {r.get('nozzle_temper', '?')} / {r.get('nozzle_target_temper', '?')} °C",
        f"Cama: {r.get('bed_temper', '?')} / {r.get('bed_target_temper', '?')} °C",
        f"Cámara: {r.get('chamber_temper', '?')} °C" if "chamber_temper" in r else "",
        f"Trabajo: {r.get('subtask_name', '-')}",
    ]
    if r.get("print_error"):
        lines.append(f"⚠️ Código de error: {r['print_error']}")
    return "\n".join(line for line in lines if line)


def start_print(conn: PrinterConn, gcode_3mf: Path, plate: int = 1, use_ams: bool = True,
                timelapse: bool = False, bed_leveling: bool = True) -> dict:
    remote = upload_file(conn, gcode_3mf)
    md5 = hashlib.md5(gcode_3mf.read_bytes()).hexdigest()
    url_tpl = os.environ.get("BAMBU_PRINT_URL_TEMPLATE", "file:///sdcard/{file}")
    payload = {
        "print": {
            "sequence_id": "0",
            "command": "project_file",
            "param": f"Metadata/plate_{plate}.gcode",
            "project_id": "0",
            "profile_id": "0",
            "task_id": "0",
            "subtask_id": "0",
            "subtask_name": gcode_3mf.name.replace(".gcode.3mf", ""),
            "url": url_tpl.format(file=remote),
            "md5": md5,
            "timelapse": timelapse,
            "bed_type": "auto",
            "bed_levelling": bed_leveling,
            "bed_leveling": bed_leveling,
            "flow_cali": True,
            "vibration_cali": True,
            "layer_inspect": False,
            "use_ams": use_ams,
            "ams_mapping": [0] if use_ams else [],
        }
    }
    return mqtt_request(conn, payload, wait_report=True, timeout=15)


CONTROL_COMMANDS = {
    "pause": {"print": {"sequence_id": "0", "command": "pause"}},
    "resume": {"print": {"sequence_id": "0", "command": "resume"}},
    "stop": {"print": {"sequence_id": "0", "command": "stop"}},
    "light_on": {"system": {"sequence_id": "0", "command": "ledctrl", "led_node": "chamber_light",
                            "led_mode": "on", "led_on_time": 500, "led_off_time": 500,
                            "loop_times": 0, "interval_time": 0}},
    "light_off": {"system": {"sequence_id": "0", "command": "ledctrl", "led_node": "chamber_light",
                             "led_mode": "off", "led_on_time": 500, "led_off_time": 500,
                             "loop_times": 0, "interval_time": 0}},
}
