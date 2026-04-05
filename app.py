import os
from datetime import datetime, timedelta
from hmac import compare_digest
import re
import csv
import unicodedata
from collections import Counter
from io import StringIO
from urllib.parse import urlencode
from werkzeug.security import check_password_hash, generate_password_hash

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from configuracion import (
    SECRET_KEY,
    PAYMENT_RECEIVER_BANK,
    PAYMENT_RECEIVER_PHONE,
    PAYMENT_RECEIVER_ID,
    PAYMENT_RECEIVER_NAME,
    PAYMENT_REFERENCE_PREFIX,
    PAYMENT_PENDING_MINUTES,
    PAYMENT_PROOF_WINDOW_MINUTES,
    obtener_tasa_bcv_usd,
    get_local_now,
    PATRON_EMAIL,
    PATRON_NOMBRE_EMPLEADO,
    PATRON_NOMBRE_SERVICIO,
    ESTADOS_FILTRO_PERMITIDOS,
    DECISIONES_PAGO_PERMITIDAS,
    MAX_LONGITUD_NOMBRE_CLIENTE,
    MAX_LONGITUD_EMAIL,
    MAX_LONGITUD_NOMBRE_EMPLEADO,
    MAX_LONGITUD_EMAIL_EMPLEADO,
    MAX_LONGITUD_CLAVE,
    MAX_LONGITUD_BANCO_PAGO,
    MAX_LONGITUD_TELEFONO_PAGO,
    MAX_LONGITUD_CEDULA_RIF_PAGO,
    MAX_LONGITUD_NOMBRE_SERVICIO,
    MAX_LONGITUD_COMENTARIO_CALIFICACION,
    MAX_PRECIO_SERVICIO_USD,
    BANCOS_VENEZOLANOS,
    BANCOS_VENEZOLANOS_CODIGOS,
)
from base_datos import (
    init_db,
    get_employees,
    get_services,
    create_employee,
    create_service,
    update_service,
    delete_service,
    update_employee,
    delete_employee,
    get_available_slots,
    create_appointment,
    submit_payment_proof,
    review_payment_submission,
    get_appointment_with_details,
    get_payment_remaining_seconds,
    get_appointments_for_admin,
    get_admin_metrics,
    update_appointment_status,
    get_appointment_by_rating_token,
    submit_appointment_rating,
    get_employee_ratings_summary,
    get_public_employee_ratings,
    validate_client_for_rating,
    delete_canceled_appointments,
    delete_completed_appointments,
    get_admin_password_hash,
    update_admin_password_hash,
)
from servicio_correo import send_reservation_confirmation, send_rating_request
from servicio_recordatorios import start_scheduler

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "60 per hour"],
    storage_uri="memory://",
)


@app.context_processor
def inject_globals():
    now_local = get_local_now()
    return {"current_year": now_local.year}


COLORES_EMPLEADOS = {
    "Ana": "#8b5cf6",
    "Carlos": "#0ea5e9",
    "Luis": "#f59e0b",
}
COLORES_RESPALDO = ["#22c55e", "#ef4444", "#ec4899", "#14b8a6", "#6366f1"]


# Qué hace: verifica si hay sesión administrativa activa.
# Qué valida: existencia de `is_admin` en sesión.
# Qué retorna: `True`/`False`.
def _admin_autenticado():
    return bool(session.get("is_admin"))


# Qué hace: define clave de rate-limit para login admin con menor colisión.
# Qué valida: N/A.
# Qué retorna: clave basada en IP + User-Agent.
def _admin_login_rate_limit_key():
    ip_address = get_remote_address() or "unknown-ip"
    user_agent = (request.headers.get("User-Agent") or "unknown-agent")[:120]
    return f"{ip_address}:{user_agent}"


# Qué hace: parsea una fecha en formato `YYYY-MM-DD`.
# Qué valida: formato correcto de fecha.
# Qué retorna: N/A (lanza `ValueError` si falla).
def _parsear_fecha(date_str: str):
    datetime.strptime(date_str, "%Y-%m-%d")


# Qué hace: parsea una hora en formato `HH:MM`.
# Qué valida: formato correcto de hora.
# Qué retorna: N/A (lanza `ValueError` si falla).
def _parsear_hora(time_str: str):
    datetime.strptime(time_str, "%H:%M")


# Qué hace: valida que la fecha de cita sea agendable.
# Qué valida: no pasado y dentro del año actual.
# Qué retorna: `(is_valid, mensaje)`.
def _validar_fecha_agendamiento(date_str: str):
    try:
        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False, "Fecha inválida."

    now_local = get_local_now()
    current_date = now_local.date()

    if selected_date < current_date:
        return False, "No puedes agendar citas en fechas pasadas."

    if selected_date.year != current_date.year:
        return False, "Solo puedes agendar citas dentro del año actual."

    return True, ""


# Qué hace: valida fecha/hora de pago móvil recibida del formulario.
# Qué valida: formato y año actual.
# Qué retorna: `(is_valid, mensaje)`.
def _validar_fecha_pago_movil(payment_datetime: str):
    try:
        selected_dt = datetime.strptime(payment_datetime.strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return False, "Fecha y hora de pago inválida."

    now_local = get_local_now()

    if selected_dt.year != now_local.year:
        return False, "La fecha y hora del pago debe estar dentro del año actual."

    return True, ""


# Qué hace: valida correo con regex y longitud máxima.
# Qué valida: no vacío, longitud y patrón email.
# Qué retorna: `True`/`False`.
def _email_valido(value: str):
    return bool(value) and len(value) <= MAX_LONGITUD_EMAIL and PATRON_EMAIL.match(value) is not None


# Qué hace: intenta convertir un valor a entero positivo.
# Qué valida: conversión y que sea mayor a cero.
# Qué retorna: entero válido o `None`.
def _entero_positivo(value: str):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _normalizar_texto(value: str):
    return re.sub(r"\s+", " ", (value or "").strip())


def _normalizar_email(value: str):
    normalized = _normalizar_texto(value).lower()
    return normalized.replace(" ", "")


def _normalizar_telefono_entrada(value: str):
    raw = _normalizar_texto(value)
    if raw.startswith("+"):
        return "+" + re.sub(r"\D", "", raw[1:])
    return re.sub(r"\D", "", raw)


def _normalizar_cedula_rif_entrada(value: str):
    compact = re.sub(r"\s+", "", (value or "").strip().upper())
    if not compact:
        return ""

    if compact[0].isalpha():
        letter = compact[0]
        digits = "".join(ch for ch in compact[1:] if ch.isdigit())
        return f"{letter}-{digits}" if digits else letter

    return "".join(ch for ch in compact if ch.isdigit())


def _normalizar_numero_decimal_entrada(value: str):
    normalized = _normalizar_texto(value).replace(",", ".")
    return normalized


def _normalizar_ascii_basico(value: str):
    normalized = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").lower()


def _nombre_con_repeticiones_excesivas(value: str):
    # Evalua solo letras para evitar falsos positivos por espacios o guiones.
    normalized_letters = "".join(ch for ch in _normalizar_ascii_basico(value) if "a" <= ch <= "z")
    if not normalized_letters:
        return True

    # Ejemplo a bloquear: "aaaaaaaa".
    if len(set(normalized_letters)) == 1 and len(normalized_letters) >= 3:
        return True

    # Bloquea secuencias largas del mismo caracter.
    if re.search(r"([a-z])\1{3,}", normalized_letters):
        return True

    # Bloquea nombres con una sola letra dominando casi todo el valor.
    if len(normalized_letters) >= 6:
        most_common_count = Counter(normalized_letters).most_common(1)[0][1]
        if most_common_count / len(normalized_letters) >= 0.75:
            return True

    return False


def _nombre_persona_valido(value: str, max_longitud: int = MAX_LONGITUD_NOMBRE_EMPLEADO):
    normalized = _normalizar_texto(value)
    if not normalized or len(normalized) > max_longitud:
        return False
    if PATRON_NOMBRE_EMPLEADO.match(normalized) is None:
        return False
    if not any(ch.isalpha() for ch in normalized):
        return False
    if _nombre_con_repeticiones_excesivas(normalized):
        return False
    return True


# Alias para retrocompatibilidad
_nombre_empleado_valido = _nombre_persona_valido


def _nombre_servicio_valido(value: str):
    normalized = _normalizar_texto(value)
    if not normalized or len(normalized) > MAX_LONGITUD_NOMBRE_SERVICIO:
        return False
    if PATRON_NOMBRE_SERVICIO.match(normalized) is None:
        return False
    return any(ch.isalnum() for ch in normalized)


# Qué hace: construye leyenda de colores para empleados en calendario.
# Qué valida: asigna color fijo o de respaldo por nombre.
# Qué retorna: lista de `{nombre, color}`.
def _leyenda_colores_empleados(empleados):
    leyenda = []
    colores_asignados = {}

    for empleado in empleados:
        color = COLORES_EMPLEADOS.get(empleado.name)
        if not color:
            if empleado.name not in colores_asignados:
                colores_asignados[empleado.name] = COLORES_RESPALDO[len(colores_asignados) % len(COLORES_RESPALDO)]
            color = colores_asignados[empleado.name]

        leyenda.append(
            {
                "nombre": empleado.name,
                "color": color,
            }
        )

    return leyenda


# Qué hace: renderiza la página pública de reservas.
# Qué valida: N/A (consulta datos y construye contexto de vista).
# Qué retorna: HTML de `inicio.html`.
@app.route("/")
def home():
    employees = get_employees()
    services = get_services()
    bcv_usd_rate = obtener_tasa_bcv_usd()
    price_board = [
        {
            "service_name": service.name,
            "duration": service.duration_minutes,
            "price_usd": float(service.price_usd),
            "price_bcv": round(float(service.price_usd) * bcv_usd_rate, 2),
        }
        for service in services
    ]
    
    employee_ratings = get_public_employee_ratings()

    return render_template(
        "inicio.html",
        employees=employees,
        services=[service.name for service in services],
        service_catalog={service.name: service.duration_minutes for service in services},
        bcv_usd_rate=bcv_usd_rate,
        price_board=price_board,
        employee_ratings=employee_ratings,
    )


# Qué hace: valida elegibilidad para calificar a un empleado.
# Qué valida: `employee_id` válido y reglas de elegibilidad del cliente.
# Qué retorna: JSON con `success` y `url` o `error`.
@app.post("/api/verify-rating-eligibility")
@limiter.limit("5 per minute")
def verify_rating_eligibility():
    data = request.get_json(silent=True) or {}
    employee_id_raw = data.get("employee_id")
    client_email = _normalizar_email(data.get("client_email", ""))

    try:
        employee_id = int(employee_id_raw)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "ID de empleado inválido."}), 400

    success, msg, token = validate_client_for_rating(employee_id, client_email)
    
    if success and token:
        redirect_url = url_for("rate_appointment", token=token)
        return jsonify({"success": True, "url": redirect_url})
    
    return jsonify({"success": False, "error": msg}), 400


# Qué hace: consulta horarios disponibles para reserva.
# Qué valida: parámetros, fecha agendable y servicio existente.
# Qué retorna: JSON con lista `slots` o mensaje de error.
@app.get("/api/availability")
@limiter.limit("40 per minute")
def availability():
    date_str = _normalizar_texto(request.args.get("date", ""))
    employee_id = _normalizar_texto(request.args.get("employee_id", ""))
    service_name = _normalizar_texto(request.args.get("service_name", ""))

    if not date_str or not employee_id or not service_name:
        return jsonify({"slots": [], "error": "Debes seleccionar servicio, fecha y empleado."}), 400

    if service_name not in {service.name for service in get_services()}:
        return jsonify({"slots": [], "error": "Servicio inválido."}), 400

    try:
        _parsear_fecha(date_str)
        employee_id_int = int(employee_id)
        if employee_id_int <= 0:
            raise ValueError
    except ValueError:
        return jsonify({"slots": [], "error": "Parámetros inválidos."}), 400

    is_valid_booking_date, booking_date_msg = _validar_fecha_agendamiento(date_str)
    if not is_valid_booking_date:
        return jsonify({"slots": [], "error": booking_date_msg}), 400

    slots = get_available_slots(date_str, employee_id_int, service_name)
    return jsonify({"slots": slots})


# Qué hace: procesa el formulario y crea una reserva.
# Qué valida: campos, longitudes, email, fecha/hora y servicio.
# Qué retorna: redirección a pago (éxito) o a inicio (error) con `flash`.
@app.post("/book")
@limiter.limit("10 per minute")
def book():
    client_name = _normalizar_texto(request.form.get("client_name", ""))
    client_email = _normalizar_email(request.form.get("client_email", ""))
    service_name = _normalizar_texto(request.form.get("service_name", ""))
    employee_id = _normalizar_texto(request.form.get("employee_id", ""))
    date_str = _normalizar_texto(request.form.get("date", ""))
    time_str = _normalizar_texto(request.form.get("time", ""))

    if not all([client_name, client_email, service_name, employee_id, date_str, time_str]):
        flash("Completa todos los campos para reservar.", "error")
        return redirect(url_for("home"))

    if len(client_name) > MAX_LONGITUD_NOMBRE_CLIENTE:
        flash("El nombre del cliente es demasiado largo.", "error")
        return redirect(url_for("home"))

    if not _nombre_persona_valido(client_name):
        flash("El nombre del cliente es inválido. Usa letras reales y evita repeticiones excesivas.", "error")
        return redirect(url_for("home"))

    if not _email_valido(client_email):
        flash("El correo del cliente es inválido.", "error")
        return redirect(url_for("home"))

    services = get_services()
    if service_name not in [s.name for s in services]:
        flash("Servicio inválido.", "error")
        return redirect(url_for("home"))

    try:
        employee_id_int = int(employee_id)
        if employee_id_int <= 0:
            raise ValueError
        _parsear_fecha(date_str)
        _parsear_hora(time_str)
    except ValueError:
        flash("Fecha u hora inválida.", "error")
        return redirect(url_for("home"))

    is_valid_booking_date, booking_date_msg = _validar_fecha_agendamiento(date_str)
    if not is_valid_booking_date:
        flash(booking_date_msg, "error")
        return redirect(url_for("home"))

    success, message, appointment_id = create_appointment(
        client_name=client_name,
        client_email=client_email,
        service_name=service_name,
        employee_id=employee_id_int,
        date_str=date_str,
        time_str=time_str,
    )

    if success:
        flash(message, "success")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    flash(message, "success" if success else "error")
    return redirect(url_for("home"))


# Qué hace: muestra la página de pago de una cita.
# Qué valida: existencia de cita, estado pendiente y tiempo no expirado.
# Qué retorna: HTML de `pagina_pago.html` o redirección con error.
@app.get("/payment/<int:appointment_id>")
def payment_page(appointment_id: int):
    appointment = get_appointment_with_details(appointment_id)

    if not appointment:
        flash("No se encontró la cita para registrar pago.", "error")
        return redirect(url_for("home"))

    if appointment["status"] == "pending_payment":
        remaining_seconds = get_payment_remaining_seconds(appointment, PAYMENT_PENDING_MINUTES)
        if remaining_seconds <= 0:
            update_appointment_status(appointment_id, "canceled")
            flash("El tiempo para completar el pago expiró. Reserva nuevamente.", "error")
            return redirect(url_for("home"))
    else:
        remaining_seconds = 0

    if appointment["status"] != "pending_payment":
        flash("Esta cita ya no está pendiente de pago.", "error")
        return redirect(url_for("home"))

    try:
        created_at = datetime.strptime(appointment["created_at"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        update_appointment_status(appointment_id, "canceled")
        flash("La cita tiene datos inválidos y fue cancelada por seguridad.", "error")
        return redirect(url_for("home"))

    expires_at = created_at + timedelta(minutes=PAYMENT_PENDING_MINUTES)
    bcv_usd_rate = obtener_tasa_bcv_usd()
    service_price_lookup = {service.name: float(service.price_usd) for service in get_services()}
    amount_usd = service_price_lookup.get(appointment["service_name"], 0)
    amount_bcv = round(amount_usd * bcv_usd_rate, 2)

    return render_template(
        "pagina_pago.html",
        appointment=appointment,
        bancos_venezolanos=BANCOS_VENEZOLANOS,
        bancos_venezolanos_codigos=BANCOS_VENEZOLANOS_CODIGOS,
        payment_datetime_min=created_at.strftime("%Y-%m-%dT%H:%M"),
        payment_datetime_max=(created_at + timedelta(minutes=PAYMENT_PROOF_WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M"),
        payment_pending_minutes=PAYMENT_PENDING_MINUTES,
        payment_remaining_seconds=remaining_seconds,
        payment_expires_at=expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        payment_amount_usd=amount_usd,
        payment_amount_bcv=amount_bcv,
        bcv_usd_rate=bcv_usd_rate,
        payment_receiver={
            "bank": PAYMENT_RECEIVER_BANK,
            "phone": PAYMENT_RECEIVER_PHONE,
            "id": PAYMENT_RECEIVER_ID,
            "name": PAYMENT_RECEIVER_NAME,
            "reference": f"{PAYMENT_REFERENCE_PREFIX}-{appointment_id}",
        },
    )


# Qué hace: registra comprobante de pago móvil.
# Qué valida: fecha/hora de pago y longitudes de datos de emisor.
# Qué retorna: redirección con `flash` de éxito/error.
@app.post("/payment/<int:appointment_id>/submit")
@limiter.limit("10 per minute")
def payment_submit(appointment_id: int):
    payment_last4 = re.sub(r"\D", "", request.form.get("payment_last4", ""))
    payment_bank = _normalizar_texto(request.form.get("payment_bank", ""))
    
    payment_phone_prefix = _normalizar_texto(request.form.get("payment_phone_prefix", ""))
    payment_phone_digits = re.sub(r"\D", "", request.form.get("payment_phone_digits", ""))
    payment_phone_val = f"{payment_phone_prefix}{payment_phone_digits}"
    payment_phone = _normalizar_telefono_entrada(payment_phone_val)
    
    payment_payer_id_prefix = _normalizar_texto(request.form.get("payment_payer_id_prefix", "")).upper()
    payment_payer_id_digits = re.sub(r"\D", "", request.form.get("payment_payer_id_digits", ""))
    payment_payer_id_val = f"{payment_payer_id_prefix}-{payment_payer_id_digits}" if payment_payer_id_prefix and payment_payer_id_digits else f"{payment_payer_id_prefix}{payment_payer_id_digits}"
    payment_payer_id = _normalizar_cedula_rif_entrada(payment_payer_id_val)
    
    payment_date = _normalizar_texto(request.form.get("payment_date", ""))
    payment_time = _normalizar_texto(request.form.get("payment_time", ""))
    payment_datetime = f"{payment_date}T{payment_time}" if payment_date and payment_time else ""

    is_valid_payment_date, payment_date_msg = _validar_fecha_pago_movil(payment_datetime)
    if not is_valid_payment_date:
        flash(payment_date_msg, "error")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    if len(payment_bank) > MAX_LONGITUD_BANCO_PAGO:
        flash("El banco emisor es demasiado largo.", "error")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    if payment_bank not in BANCOS_VENEZOLANOS:
        flash("Debes seleccionar un banco emisor válido de la lista.", "error")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    if len(payment_phone) > MAX_LONGITUD_TELEFONO_PAGO:
        flash("El teléfono emisor es demasiado largo.", "error")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    if len(payment_payer_id) > MAX_LONGITUD_CEDULA_RIF_PAGO:
        flash("La cédula/RIF emisor es demasiado larga.", "error")
        return redirect(url_for("payment_page", appointment_id=appointment_id))

    success, message = submit_payment_proof(
        appointment_id=appointment_id,
        payment_last4=payment_last4,
        payment_bank=payment_bank,
        payment_phone=payment_phone,
        payment_payer_id=payment_payer_id,
        payment_datetime=payment_datetime,
    )

    flash(message, "success" if success else "error")

    if success:
        return redirect(url_for("booking_success", appointment_id=appointment_id))

    return redirect(url_for("payment_page", appointment_id=appointment_id))


# Qué hace: muestra la página de confirmación final
# Qué valida: existencia de cita
# Qué retorna: HTML de `confirmacion.html`
@app.get("/confirmacion/<int:appointment_id>")
def booking_success(appointment_id: int):
    appointment = get_appointment_with_details(appointment_id)
    if not appointment:
        flash("No se encontró la cita solicitada.", "error")
        return redirect(url_for("home"))
    return render_template("confirmacion.html", appointment=appointment)


# Qué hace: maneja acceso al panel administrativo.
# Qué valida: longitud de clave y hash de contraseña.
# Qué retorna: HTML de login o redirección al dashboard.
@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=_admin_login_rate_limit_key)
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) > MAX_LONGITUD_CLAVE:
            flash("Clave incorrecta.", "error")
            return render_template("admin_ingreso.html", max_password_length=MAX_LONGITUD_CLAVE)

        stored_admin_hash = get_admin_password_hash()
        try:
            password_is_valid = check_password_hash(stored_admin_hash, password)
        except ValueError:
            password_is_valid = False

        if password_is_valid:
            session.permanent = True
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))

        flash("Clave incorrecta.", "error")

    return render_template("admin_ingreso.html", max_password_length=MAX_LONGITUD_CLAVE)


# Qué hace: cierra sesión de administrador.
# Qué valida: N/A.
# Qué retorna: redirección al login admin.
@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# Qué hace: renderiza dashboard administrativo completo.
# Qué valida: sesión admin activa y filtros de consulta.
# Qué retorna: HTML de `admin_panel.html`.
@app.get("/admin")
def admin_dashboard():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    filter_date = _normalizar_texto(request.args.get("date", ""))
    filter_employee = _normalizar_texto(request.args.get("employee_id", ""))
    filter_status = _normalizar_texto(request.args.get("status", "")).lower()

    if filter_date:
        try:
            _parsear_fecha(filter_date)
        except ValueError:
            flash("La fecha de filtro es inválida.", "error")
            filter_date = ""

    if filter_status and filter_status not in ESTADOS_FILTRO_PERMITIDOS:
        flash("El estado de filtro no es válido.", "error")
        filter_status = ""

    employee_id = None
    if filter_employee:
        employee_id = _entero_positivo(filter_employee)
        if employee_id is None:
            flash("El empleado de filtro no es válido.", "error")
            filter_employee = ""

    employees = get_employees()
    services = get_services()
    leyenda_colores = _leyenda_colores_empleados(employees)

    appointments = get_appointments_for_admin(
        date_str=filter_date or None,
        employee_id=employee_id,
        status=filter_status or None,
    )
    metrics = get_admin_metrics(
        date_str=filter_date or None,
        employee_id=employee_id,
        status=filter_status or None,
    )
    
    today = get_local_now()
    employee_ratings = get_employee_ratings_summary(today.year, today.month)

    return render_template(
        "admin_panel.html",
        appointments=appointments,
        employees=employees,
        services=services,
        leyenda_colores=leyenda_colores,
        metrics=metrics,
        employee_ratings=employee_ratings,
        max_password_length=MAX_LONGITUD_CLAVE,
        filters={
            "date": filter_date,
            "employee_id": filter_employee,
            "status": filter_status,
        },
    )


# Qué hace: permite cambiar la contraseña del administrador autenticado.
# Qué valida: contraseña actual, confirmación y reglas básicas de longitud.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/change-password")
@limiter.limit("5 per minute")
def admin_change_password():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not current_password or not new_password or not confirm_password:
        flash("Debes completar todos los campos para cambiar la contraseña.", "error")
        return redirect(url_for("admin_dashboard"))

    if any(len(value) > MAX_LONGITUD_CLAVE for value in (current_password, new_password, confirm_password)):
        flash("La contraseña no cumple con la longitud permitida.", "error")
        return redirect(url_for("admin_dashboard"))

    if len(new_password) < 6:
        flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        return redirect(url_for("admin_dashboard"))

    if new_password != confirm_password:
        flash("La confirmación de la nueva contraseña no coincide.", "error")
        return redirect(url_for("admin_dashboard"))

    stored_admin_hash = get_admin_password_hash()
    try:
        current_password_is_valid = check_password_hash(stored_admin_hash, current_password)
    except ValueError:
        current_password_is_valid = False

    if not current_password_is_valid:
        flash("La contraseña actual es incorrecta.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        is_same_password = check_password_hash(stored_admin_hash, new_password)
    except ValueError:
        is_same_password = False

    if is_same_password:
        flash("La nueva contraseña debe ser distinta a la actual.", "error")
        return redirect(url_for("admin_dashboard"))

    new_password_hash = generate_password_hash(new_password)
    success, message = update_admin_password_hash(new_password_hash)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))

# Qué hace: exporta citas a CSV.
# Qué valida: sesión de admin y usa los mismos filtros.
# Qué retorna: archivo CSV.
@app.get("/admin/export-csv")
@limiter.limit("5 per minute")
def admin_export_csv():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    filter_date = _normalizar_texto(request.args.get("date", ""))
    filter_employee = _normalizar_texto(request.args.get("employee_id", ""))
    filter_status = _normalizar_texto(request.args.get("status", "")).lower()

    if filter_date:
        try:
            _parsear_fecha(filter_date)
        except ValueError:
            filter_date = ""

    if filter_status and filter_status not in ESTADOS_FILTRO_PERMITIDOS:
        filter_status = ""

    employee_id = _entero_positivo(filter_employee) if filter_employee else None

    appointments = get_appointments_for_admin(
        date_str=filter_date or None,
        employee_id=employee_id,
        status=filter_status or None,
    )

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Fecha Cita', 'Cliente', 'Email', 'Servicio', 'Empleado', 'Estado', 'Banco Pago', 'Ref Pago'])

    for apt in appointments:
        writer.writerow([
            apt["id"], apt["appointment_datetime"], apt["client_name"], 
            apt["client_email"], apt["service_name"], apt["employee_name"], 
            apt["status"], apt.get("payment_bank", ""), apt.get("payment_last4", "")
        ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=citas_export.csv"
    return response


# Qué hace: crea un empleado desde panel admin.
# Qué valida: autenticación admin, nombre y email válidos.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/employees")
@limiter.limit("20 per minute")
def admin_add_employee():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    employee_name = _normalizar_texto(request.form.get("employee_name", ""))
    employee_email = _normalizar_email(request.form.get("employee_email", ""))
    work_area = _normalizar_texto(request.form.get("work_area", "General"))

    if not _nombre_empleado_valido(employee_name):
        flash("El nombre del empleado es inválido. Usa letras reales y evita repeticiones excesivas.", "error")
        return redirect(url_for("admin_dashboard"))

    normalized_email = employee_email
    if normalized_email and (
        len(normalized_email) > MAX_LONGITUD_EMAIL_EMPLEADO
        or not _email_valido(normalized_email)
    ):
        flash("El correo del empleado es inválido.", "error")
        return redirect(url_for("admin_dashboard"))

    success, message = create_employee(employee_name, normalized_email, work_area)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: crea un servicio desde panel admin.
# Qué valida: autenticación admin y formato de duración/precio.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/services")
@limiter.limit("20 per minute")
def admin_add_service():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    service_name = _normalizar_texto(request.form.get("service_name", ""))
    service_duration = _normalizar_texto(request.form.get("service_duration", ""))
    service_price = _normalizar_numero_decimal_entrada(request.form.get("service_price", ""))

    if not _nombre_servicio_valido(service_name):
        flash("El nombre del servicio es inválido. Usa letras/números y signos básicos permitidos.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        duration_minutes = int(service_duration)
    except (TypeError, ValueError):
        flash("La duración del servicio debe ser un número entero.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        price_usd = float(service_price)
    except (TypeError, ValueError):
        flash("El precio del servicio debe ser un número válido.", "error")
        return redirect(url_for("admin_dashboard"))

    if price_usd > MAX_PRECIO_SERVICIO_USD:
        flash(f"El precio del servicio no puede ser mayor a {MAX_PRECIO_SERVICIO_USD} USD.", "error")
        return redirect(url_for("admin_dashboard"))

    success, message = create_service(service_name, duration_minutes, price_usd)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: edita un servicio existente desde admin.
# Qué valida: autenticación admin y formato de campos.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/services/<int:service_id>/edit")
@limiter.limit("30 per minute")
def admin_edit_service(service_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    service_name = _normalizar_texto(request.form.get("service_name", ""))
    service_duration = _normalizar_texto(request.form.get("service_duration", ""))
    service_price = _normalizar_numero_decimal_entrada(request.form.get("service_price", ""))

    if not _nombre_servicio_valido(service_name):
        flash("El nombre del servicio es inválido. Usa letras/números y signos básicos permitidos.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        duration_minutes = int(service_duration)
    except (TypeError, ValueError):
        flash("La duración del servicio debe ser un número entero.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        price_usd = float(service_price)
    except (TypeError, ValueError):
        flash("El precio del servicio debe ser un número válido.", "error")
        return redirect(url_for("admin_dashboard"))

    if price_usd > MAX_PRECIO_SERVICIO_USD:
        flash(f"El precio del servicio no puede ser mayor a {MAX_PRECIO_SERVICIO_USD} USD.", "error")
        return redirect(url_for("admin_dashboard"))

    success, message = update_service(service_id, service_name, duration_minutes, price_usd)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: elimina un servicio desde admin.
# Qué valida: autenticación admin y reglas de eliminación.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/services/<int:service_id>/delete")
@limiter.limit("20 per minute")
def admin_delete_service(service_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    success, message = delete_service(service_id)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: edita datos de empleado desde admin.
# Qué valida: autenticación admin y formato de campos.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/employees/<int:employee_id>/edit")
@limiter.limit("30 per minute")
def admin_edit_employee(employee_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    employee_name = _normalizar_texto(request.form.get("employee_name", ""))
    employee_email = _normalizar_email(request.form.get("employee_email", ""))
    work_area = _normalizar_texto(request.form.get("work_area", "General"))

    if not _nombre_empleado_valido(employee_name):
        flash("El nombre del empleado es inválido. Usa letras reales y evita repeticiones excesivas.", "error")
        return redirect(url_for("admin_dashboard"))

    normalized_email = employee_email
    if normalized_email and (
        len(normalized_email) > MAX_LONGITUD_EMAIL_EMPLEADO
        or not _email_valido(normalized_email)
    ):
        flash("El correo del empleado es inválido.", "error")
        return redirect(url_for("admin_dashboard"))

    success, message = update_employee(employee_id, employee_name, normalized_email, work_area)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: edita datos de todos los empleados a la vez desde admin.
# Qué valida: autenticación admin y formato de campos.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/employees/batch_edit")
@limiter.limit("30 per minute")
def admin_batch_edit_employees():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    emp_ids = request.form.getlist("emp_id")
    for eid_str in emp_ids:
        try:
            eid = int(eid_str)
        except ValueError:
            continue

        employee_name = _normalizar_texto(request.form.get(f"employee_name_{eid}", ""))
        employee_email = _normalizar_email(request.form.get(f"employee_email_{eid}", ""))
        work_area = _normalizar_texto(request.form.get(f"work_area_{eid}", "General"))

        if not _nombre_empleado_valido(employee_name):
            flash(f"El nombre '{employee_name}' es inválido. Evita repeticiones excesivas.", "error")
            return redirect(url_for("admin_dashboard"))

        normalized_email = employee_email
        if normalized_email and (
            len(normalized_email) > MAX_LONGITUD_EMAIL_EMPLEADO
            or not _email_valido(normalized_email)
        ):
            flash(f"El correo para '{employee_name}' es inválido.", "error")
            return redirect(url_for("admin_dashboard"))

        update_employee(eid, employee_name, normalized_email, work_area)

    flash("Empleados actualizados correctamente.", "success")
    return redirect(url_for("admin_dashboard"))


# Qué hace: elimina un empleado desde admin.
# Qué valida: autenticación admin y reglas de eliminación.
# Qué retorna: redirección al dashboard con mensaje.
@app.post("/admin/employees/<int:employee_id>/delete")
@limiter.limit("20 per minute")
def admin_delete_employee(employee_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    success, message = delete_employee(employee_id)
    flash(message, "success" if success else "error")

    return redirect(url_for("admin_dashboard"))


# Qué hace: actualiza estado de una cita desde admin.
# Qué valida: autenticación admin y estado objetivo.
# Qué retorna: redirección al dashboard preservando filtros.
@app.post("/admin/appointments/<int:appointment_id>/status")
@limiter.limit("30 per minute")
def admin_update_appointment_status(appointment_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    new_status = _normalizar_texto(request.form.get("status", "")).lower()
    
    old_appointment = get_appointment_with_details(appointment_id)
    old_status = old_appointment["status"] if old_appointment else None

    success, message = update_appointment_status(appointment_id, new_status)
    
    # Ya no se penaliza automáticamente al empleado cuando una cita agendada es cancelada manualmente

    if success and old_status != "completed" and new_status == "completed":
        updated_appointment = get_appointment_with_details(appointment_id)
        # Use a separate query to fetch the token, because get_appointment_with_details doesn't include it currently
        from base_datos import get_session, Appointment
        with get_session() as session:
            db_apt = session.get(Appointment, appointment_id)
            if db_apt and db_apt.rating_token:
                send_rating_request(
                    client_email=updated_appointment["client_email"],
                    client_name=updated_appointment["client_name"],
                    employee_name=updated_appointment["employee_name"],
                    rating_token=db_apt.rating_token
                )

    flash(message, "success" if success else "error")

    query_params = {
        "date": _normalizar_texto(request.form.get("filter_date", "")),
        "employee_id": _normalizar_texto(request.form.get("filter_employee_id", "")),
        "status": _normalizar_texto(request.form.get("filter_status", "")),
    }
    filtered_params = {key: value for key, value in query_params.items() if value}
    redirect_url = url_for("admin_dashboard")

    if filtered_params:
        redirect_url = f"{redirect_url}?{urlencode(filtered_params)}"

    return redirect(redirect_url)


# Qué hace: procesa decisión admin sobre comprobante de pago.
# Qué valida: autenticación admin y decisión permitida.
# Qué retorna: redirección al dashboard preservando filtros.
@app.post("/admin/appointments/<int:appointment_id>/payment")
@limiter.limit("30 per minute")
def admin_review_payment(appointment_id: int):
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    decision = _normalizar_texto(request.form.get("decision", "")).lower()
    if decision not in DECISIONES_PAGO_PERMITIDAS:
        flash("Acción de pago inválida.", "error")
        return redirect(url_for("admin_dashboard"))

    success, message = review_payment_submission(appointment_id, decision)

    if success and decision == "approve":
        appointment = get_appointment_with_details(appointment_id)
        if appointment:
            send_reservation_confirmation(
                client_email=appointment["client_email"],
                client_name=appointment["client_name"],
                service_name=appointment["service_name"],
                employee_name=appointment["employee_name"],
                appointment_datetime=appointment["appointment_datetime"],
            )

    flash(message, "success" if success else "error")

    query_params = {
        "date": _normalizar_texto(request.form.get("filter_date", "")),
        "employee_id": _normalizar_texto(request.form.get("filter_employee_id", "")),
        "status": _normalizar_texto(request.form.get("filter_status", "")),
    }
    filtered_params = {key: value for key, value in query_params.items() if value}
    redirect_url = url_for("admin_dashboard")

    if filtered_params:
        redirect_url = f"{redirect_url}?{urlencode(filtered_params)}"

    return redirect(redirect_url)


# Qué hace: elimina citas canceladas desde panel admin.
# Qué valida: sesión admin activa.
# Qué retorna: redirección al dashboard preservando filtros.
@app.post("/admin/appointments/cleanup-canceled")
@limiter.limit("10 per minute")
def admin_cleanup_canceled_appointments():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    deleted_count = delete_canceled_appointments()

    if deleted_count > 0:
        flash(f"Se eliminaron {deleted_count} citas canceladas.", "success")
    else:
        flash("No hay citas canceladas para eliminar.", "error")

    query_params = {
        "date": _normalizar_texto(request.form.get("filter_date", "")),
        "employee_id": _normalizar_texto(request.form.get("filter_employee_id", "")),
        "status": _normalizar_texto(request.form.get("filter_status", "")),
    }
    filtered_params = {key: value for key, value in query_params.items() if value}
    redirect_url = url_for("admin_dashboard")

    if filtered_params:
        redirect_url = f"{redirect_url}?{urlencode(filtered_params)}"

    return redirect(redirect_url)


# Qué hace: elimina citas atendidas desde panel admin.
# Qué valida: sesión admin activa.
# Qué retorna: redirección al dashboard preservando filtros.
@app.post("/admin/appointments/cleanup-completed")
@limiter.limit("10 per minute")
def admin_cleanup_completed_appointments():
    if not _admin_autenticado():
        return redirect(url_for("admin_login"))

    deleted_count = delete_completed_appointments()

    if deleted_count > 0:
        flash(f"Se eliminaron {deleted_count} citas atendidas.", "success")
    else:
        flash("No hay citas atendidas para eliminar.", "error")

    query_params = {
        "date": _normalizar_texto(request.form.get("filter_date", "")),
        "employee_id": _normalizar_texto(request.form.get("filter_employee_id", "")),
        "status": _normalizar_texto(request.form.get("filter_status", "")),
    }
    filtered_params = {key: value for key, value in query_params.items() if value}
    redirect_url = url_for("admin_dashboard")

    if filtered_params:
        redirect_url = f"{redirect_url}?{urlencode(filtered_params)}"

    return redirect(redirect_url)


# Qué hace: expone citas en formato eventos para FullCalendar.
# Qué valida: sesión admin activa.
# Qué retorna: JSON con eventos del calendario.
@app.get("/api/admin/appointments")
@limiter.limit("30 per minute")
def admin_appointments_api():
    if not _admin_autenticado():
        return jsonify({"error": "No autorizado"}), 401

    appointments = get_appointments_for_admin()
    events = []
    assigned_fallback = {}

    for item in appointments:
        appointment_datetime = item["appointment_datetime"]
        start_iso = appointment_datetime.replace(" ", "T", 1)
        employee_name = item["employee_name"]
        event_color = COLORES_EMPLEADOS.get(employee_name)

        if not event_color:
            if employee_name not in assigned_fallback:
                assigned_fallback[employee_name] = COLORES_RESPALDO[len(assigned_fallback) % len(COLORES_RESPALDO)]
            event_color = assigned_fallback[employee_name]

        events.append(
            {
                "id": item["id"],
                "title": f"{item['service_name']} - {employee_name}",
                "start": start_iso,
                "backgroundColor": event_color,
                "borderColor": event_color,
                "textColor": "#ffffff",
                "extendedProps": {
                    "client_name": item["client_name"],
                    "client_email": item["client_email"],
                    "status": item["status"],
                    "employee_name": employee_name,
                },
            }
        )

    return jsonify(events)


# Qué hace: muestra y procesa calificación de cita por token.
# Qué valida: token existente y cita no calificada previamente.
# Qué retorna: HTML de calificación o redirección con mensaje.
@app.route("/rate/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def rate_appointment(token: str):
    appointment = get_appointment_by_rating_token(token)
    if not appointment:
        flash("El enlace de calificación es inválido o la cita no existe.", "error")
        return redirect(url_for("home"))
    
    if appointment["rating"] is not None:
        flash("Ya has calificado este servicio. ¡Gracias por tu opinión!", "success")
        return redirect(url_for("home"))

    if request.method == "POST":
        rating_val = _normalizar_texto(request.form.get("rating", ""))
        comment_val = _normalizar_texto(request.form.get("comment", ""))
        try:
            rating_int = int(rating_val)
        except ValueError:
            flash("Calificación inválida.", "error")
            return redirect(request.url)

        success, msg = submit_appointment_rating(token, rating_int, comment_val)
        flash(msg, "success" if success else "error")
        return redirect(url_for("home"))

    return render_template(
        "calificar.html",
        appointment=appointment,
        token=token,
        max_comment_length=MAX_LONGITUD_COMENTARIO_CALIFICACION,
    )


# Qué hace: maneja respuestas cuando se excede rate limit.
# Qué valida: tipo de ruta (`/api/` o vista HTML).
# Qué retorna: JSON 429 o redirección con `flash`.
@app.errorhandler(404)
def page_not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(429)
def rate_limit_exceeded(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Demasiadas solicitudes. Intenta de nuevo en unos segundos."}), 429

    flash("Demasiados intentos. Espera un momento e inténtalo nuevamente.", "error")
    if request.path.startswith("/admin/login"):
        return redirect(url_for("admin_login"))
    return redirect(request.referrer or url_for("home"))


if __name__ == "__main__":
    init_db()
    start_scheduler()
    app.run(debug=True)
