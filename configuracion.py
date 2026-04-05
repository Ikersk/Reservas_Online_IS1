import os
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
import re
from typing import Any
from datetime import datetime
try:
	from zoneinfo import ZoneInfo
except ImportError:
	from pytz import timezone as ZoneInfo

from dotenv import load_dotenv, dotenv_values

TIMEZONE = os.getenv("TIMEZONE", "America/Caracas")

def get_local_now() -> datetime:
	try:
		return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
	except Exception as e:
		print(f"[ERROR ZONA HORARIA] Falló ZoneInfo con {TIMEZONE}: {e}. Retornando hora del servidor UTC.")
		return datetime.now()

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

DB_PATH = BASE_DIR / "barberia.db"

SECRET_KEY = os.getenv("SECRET_KEY", "cambia-esta-clave-secreta")
# Hash PBKDF2 predeterminado para la contraseña "barberia123"
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "scrypt:32768:8:1$P0e4V1ZfRjT07O52$af05d3b6f8f7c11f7c2dc289751e3381e4b862922115f532bfa322d7cbaecbc599a0ed41ed0a8fbdd3b2ffeb7b79a099a1be34125b0351f0ee34c4f9bc2f6b83")

MAILJET_API_KEY = os.getenv("MAILJET_API_KEY", "")
MAILJET_SECRET_KEY = os.getenv("MAILJET_SECRET_KEY", "")
MAILJET_SENDER_EMAIL = os.getenv("MAILJET_SENDER_EMAIL", "")

PAYMENT_RECEIVER_BANK = os.getenv("PAYMENT_RECEIVER_BANK", "Banco de Venezuela")
PAYMENT_RECEIVER_PHONE = os.getenv("PAYMENT_RECEIVER_PHONE", "0412-0000000")
PAYMENT_RECEIVER_ID = os.getenv("PAYMENT_RECEIVER_ID", "V-00000000")
PAYMENT_RECEIVER_NAME = os.getenv("PAYMENT_RECEIVER_NAME", "Barbería")
PAYMENT_REFERENCE_PREFIX = os.getenv("PAYMENT_REFERENCE_PREFIX", "CITA")
PAYMENT_PENDING_MINUTES = int(os.getenv("PAYMENT_PENDING_MINUTES", "5"))
PAYMENT_PROOF_WINDOW_MINUTES = int(os.getenv("PAYMENT_PROOF_WINDOW_MINUTES", "5"))
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "usd").strip().lower() or "usd"
TASA_BCV_USD = float(os.getenv("BCV_USD_RATE", "36.50"))

BANCOS_VENEZOLANOS_CODIGOS = {
	"Banco de Venezuela": "0102",
	"Banco Venezolano de Crédito": "0104",
	"Banco Mercantil": "0105",
	"BBVA Provincial": "0108",
	"Bancaribe": "0114",
	"Banco Exterior": "0115",
	"Banco Caroní": "0128",
	"Banesco": "0134",
	"Banco Sofitasa": "0137",
	"Banco Plaza": "0138",
	"Bangente": "0146",
	"Banco Fondo Común (BFC)": "0151",
	"100% Banco": "0156",
	"Banco del Sur": "0157",
	"Banco del Tesoro": "0163",
	"Banco Agrícola de Venezuela": "0166",
	"Bancrecer": "0168",
	"Mi Banco": "0169",
	"Banco Activo": "0171",
	"Banfanb": "0177",
	"Banco Internacional de Desarrollo": "0173",
	"Banplus": "0174",
	"Banco Bicentenario": "0175",
	"Banco Digital de los Trabajadores": "0175",
	"Banco Nacional de Crédito (BNC)": "0191",
}

BANCOS_VENEZOLANOS = sorted(
	BANCOS_VENEZOLANOS_CODIGOS.keys(),
	key=lambda banco: (BANCOS_VENEZOLANOS_CODIGOS[banco], banco),
)

_CACHE_TASA_BCV: dict[str, Any] = {
	"timestamp": 0,
	"value": TASA_BCV_USD,
}


def _parsear_tasa(valor_crudo: Any, valor_por_defecto: float) -> float:
	if valor_crudo is None:
		return valor_por_defecto

	normalized = str(valor_crudo).strip().replace(",", ".")
	try:
		parsed = float(normalized)
	except (TypeError, ValueError):
		return valor_por_defecto

	if parsed <= 0:
		return valor_por_defecto

	return parsed


def obtener_tasa_bcv_usd() -> float:
	current_time = time.time()
	CACHE_TTL = 600  # 10 minutos de cache

	if current_time - _CACHE_TASA_BCV["timestamp"] < CACHE_TTL:
		return _CACHE_TASA_BCV["value"]

	try:
		req = urllib.request.Request("https://ve.dolarapi.com/v1/dolares/oficial", headers={'User-Agent': 'Mozilla/5.0'})
		with urllib.request.urlopen(req, timeout=5) as response:
			data = json.loads(response.read().decode('utf-8'))
			rate = float(data.get("promedio", TASA_BCV_USD))
			if rate > 0:
				_CACHE_TASA_BCV["timestamp"] = current_time
				_CACHE_TASA_BCV["value"] = rate
				return rate
	except Exception:
		pass

	env_path = BASE_DIR / ".env"
	try:
		env_values = dotenv_values(env_path)
		parsed_rate = _parsear_tasa(env_values.get("BCV_USD_RATE"), TASA_BCV_USD)
	except Exception:
		parsed_rate = TASA_BCV_USD

	_CACHE_TASA_BCV["timestamp"] = current_time
	_CACHE_TASA_BCV["value"] = parsed_rate
	return parsed_rate


PATRON_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PATRON_CEDULA_RIF_VE = re.compile(r"^(?:[VEJGP]-?\d{6,10}|\d{6,10})$")
PATRON_NOMBRE_EMPLEADO = re.compile(
	r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:['’-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)*(?: [A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:['’-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)*)?$"
)
PATRON_NOMBRE_SERVICIO = re.compile(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9][A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9()&+.,' -]*$")

ESTADOS_FILTRO_PERMITIDOS = {"pending_payment", "scheduled", "completed", "canceled"}
DECISIONES_PAGO_PERMITIDAS = {"approve", "reject"}

MAX_LONGITUD_NOMBRE_CLIENTE = 16
MAX_LONGITUD_EMAIL = 28
MAX_LONGITUD_NOMBRE_EMPLEADO = 16
MAX_LONGITUD_EMAIL_EMPLEADO = 30
MAX_LONGITUD_CLAVE = 16
MAX_LONGITUD_BANCO_PAGO = 80
MAX_LONGITUD_TELEFONO_PAGO = 13
MAX_LONGITUD_CEDULA_RIF_PAGO = 12
MAX_LONGITUD_NOMBRE_SERVICIO = 30
MAX_LONGITUD_COMENTARIO_CALIFICACION = 60
MAX_PRECIO_SERVICIO_USD = 1000

BUSINESS_OPEN_HOUR = 9
BUSINESS_CLOSE_HOUR = 19
SLOT_DURATION_MINUTES = 30
