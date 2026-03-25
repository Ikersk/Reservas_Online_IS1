from contextlib import contextmanager
from collections import Counter
from datetime import datetime, date, time, timedelta
import re
from uuid import uuid4

from sqlalchemy import String, Integer, Float, ForeignKey, UniqueConstraint, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, mapped_column, Mapped, relationship, sessionmaker

from configuracion import (
    DB_PATH,
    BUSINESS_OPEN_HOUR,
    BUSINESS_CLOSE_HOUR,
    SLOT_DURATION_MINUTES,
    PAYMENT_PENDING_MINUTES,
    PAYMENT_PROOF_WINDOW_MINUTES,
    PATRON_EMAIL,
    PATRON_NOMBRE_EMPLEADO,
    PATRON_NOMBRE_SERVICIO,
    PATRON_CEDULA_RIF_VE,
    MAX_LONGITUD_NOMBRE_CLIENTE,
    MAX_LONGITUD_EMAIL,
    MAX_LONGITUD_NOMBRE_EMPLEADO,
    MAX_LONGITUD_EMAIL_EMPLEADO,
    MAX_LONGITUD_BANCO_PAGO,
    MAX_LONGITUD_NOMBRE_SERVICIO,
    MAX_LONGITUD_COMENTARIO_CALIFICACION,
    MAX_PRECIO_SERVICIO_USD,
    BANCOS_VENEZOLANOS,
    get_local_now,
)

SERVICE_CATALOG = {
    "Corte de cabello": 45,
    "Corte de barba": 30,
    "Manicura": 60,
}
SERVICE_PRICE_USD = {
    "Corte de cabello": 8.0,
    "Corte de barba": 6.0,
    "Manicura": 10.0,
}
SERVICES = list(SERVICE_CATALOG.keys())
ALLOWED_APPOINTMENT_STATUS = {"pending_payment", "scheduled", "completed", "canceled"}

DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M"
CREATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"

Base = declarative_base()

sqlite_url = f"sqlite:///{DB_PATH.resolve().as_posix()}"
engine = create_engine(sqlite_url, future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Qué hace: representa al personal que atiende citas.
# Qué valida: las restricciones de columnas (id PK, nombre obligatorio).
# Qué retorna: instancias ORM de empleados al consultar BD.
class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=True)
    work_area: Mapped[str] = mapped_column(String, nullable=False, default="General")
    

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="employee")


# Qué hace: representa clientes que reservan servicios.
# Qué valida: email único y campos obligatorios del cliente.
# Qué retorna: instancias ORM de clientes al consultar BD.
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String,nullable=False)

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="client")


# Qué hace: representa el catálogo de servicios del negocio.
# Qué valida: nombre único y duración/precio obligatorios.
# Qué retorna: instancias ORM de servicios al consultar BD.
class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="service")


# Qué hace: representa citas con estado, pago y calificación.
# Qué valida: unicidad empleado+fecha/hora y claves foráneas.
# Qué retorna: instancias ORM de citas al consultar BD.
class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("employee_id", "appointment_datetime", name="uq_employee_datetime"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False)
    appointment_datetime: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="scheduled")
    reminder_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payment_last4: Mapped[str] = mapped_column(String, nullable=True)
    payment_bank: Mapped[str] = mapped_column(String, nullable=True)
    payment_phone: Mapped[str] = mapped_column(String, nullable=True)
    payment_payer_id: Mapped[str] = mapped_column(String, nullable=True)
    payment_datetime: Mapped[str] = mapped_column(String, nullable=True)
    payment_submitted_at: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=True)
    rating_comment: Mapped[str] = mapped_column(String, nullable=True)
    rating_token: Mapped[str] = mapped_column(String, unique=True, nullable=True)

    client: Mapped[Client] = relationship(back_populates="appointments")
    service: Mapped[Service] = relationship(back_populates="appointments")
    employee: Mapped[Employee] = relationship(back_populates="appointments")


# Qué hace: abre una sesión SQLAlchemy y la cierra automáticamente.
# Qué valida: N/A (solo gestión de recurso).
# Qué retorna: un contexto (`session`) para ejecutar operaciones DB.
@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# Qué hace: revisa si `appointments` ya posee FK a `services`.
# Qué valida: estructura real de FK en SQLite (PRAGMA).
# Qué retorna: `True` si existe FK correcta, `False` en caso contrario.
def _appointments_has_service_fk(connection) -> bool:
    fk_rows = connection.execute(text("PRAGMA foreign_key_list(appointments)")).fetchall()
    for row in fk_rows:
        table_name = row[2]
        from_column = row[3]
        to_column = row[4]
        if table_name == "services" and from_column == "service_id" and to_column == "id":
            return True
    return False


# Qué hace: migra `appointments` al esquema nuevo con `service_id` obligatorio.
# Qué valida: presencia de columnas antiguas para mapear datos existentes.
# Qué retorna: N/A (aplica cambios de esquema sobre la conexión).
def _migrate_appointments_add_service_fk(connection):
    existing_columns = [
        row[1]
        for row in connection.execute(text("PRAGMA table_info(appointments)")).fetchall()
    ]
    has_service_id = "service_id" in existing_columns
    has_service_name = "service_name" in existing_columns

    if has_service_name:
        connection.execute(
            text(
                """
                INSERT INTO services (name, duration_minutes, price_usd)
                SELECT DISTINCT a.service_name, :slot_duration, :fallback_price
                FROM appointments a
                LEFT JOIN services s ON s.name = a.service_name
                WHERE a.service_name IS NOT NULL AND TRIM(a.service_name) != '' AND s.id IS NULL
                """
            ),
            {
                "slot_duration": SLOT_DURATION_MINUTES,
                "fallback_price": 1.0,
            },
        )

    connection.execute(text("PRAGMA foreign_keys=OFF"))

    connection.execute(
        text(
            """
            CREATE TABLE appointments_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                appointment_datetime TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                reminder_sent INTEGER NOT NULL DEFAULT 0,
                payment_last4 TEXT,
                payment_bank TEXT,
                payment_phone TEXT,
                payment_payer_id TEXT,
                payment_datetime TEXT,
                payment_submitted_at TEXT,
                created_at TEXT NOT NULL,
                rating INTEGER,
                rating_comment TEXT,
                rating_token TEXT UNIQUE,
                CONSTRAINT uq_employee_datetime UNIQUE (employee_id, appointment_datetime),
                FOREIGN KEY(client_id) REFERENCES clients (id),
                FOREIGN KEY(service_id) REFERENCES services (id),
                FOREIGN KEY(employee_id) REFERENCES employees (id)
            )
            """
        )
    )

    select_service_id = "a.service_id"
    join_clause = ""
    if has_service_name:
        join_clause = " LEFT JOIN services s ON s.name = a.service_name "
        if has_service_id:
            select_service_id = "COALESCE(a.service_id, s.id)"
        else:
            select_service_id = "s.id"

    def _col_or_default(column_name: str, default_sql: str = "NULL"):
        if column_name in existing_columns:
            return f"a.{column_name}"
        return default_sql

    copy_sql = f"""
        INSERT INTO appointments_new (
            id,
            client_id,
            service_id,
            employee_id,
            appointment_datetime,
            status,
            reminder_sent,
            payment_last4,
            payment_bank,
            payment_phone,
            payment_payer_id,
            payment_datetime,
            payment_submitted_at,
            created_at,
            rating,
            rating_comment,
            rating_token
        )
        SELECT
            a.id,
            a.client_id,
            {select_service_id},
            a.employee_id,
            a.appointment_datetime,
            {_col_or_default('status', "'scheduled'")},
            {_col_or_default('reminder_sent', '0')},
            {_col_or_default('payment_last4')},
            {_col_or_default('payment_bank')},
            {_col_or_default('payment_phone')},
            {_col_or_default('payment_payer_id')},
            {_col_or_default('payment_datetime')},
            {_col_or_default('payment_submitted_at')},
            {_col_or_default('created_at', "strftime('%Y-%m-%d %H:%M:%S', 'now')")},
            {_col_or_default('rating')},
            {_col_or_default('rating_comment')},
            {_col_or_default('rating_token')}
        FROM appointments a
        {join_clause}
        WHERE {select_service_id} IS NOT NULL
    """
    connection.execute(text(copy_sql))

    connection.execute(text("DROP TABLE appointments"))
    connection.execute(text("ALTER TABLE appointments_new RENAME TO appointments"))
    connection.execute(text("PRAGMA foreign_keys=ON"))


# Qué hace: garantiza que `appointments` cumpla el esquema vigente.
# Qué valida: existencia de tabla, columnas y FK esperadas.
# Qué retorna: N/A (migra solo si hace falta).
def _ensure_appointments_service_fk(connection):
    table_exists = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='appointments'")
    ).fetchone()
    if not table_exists:
        return

    columns = {
        row[1]
        for row in connection.execute(text("PRAGMA table_info(appointments)")).fetchall()
    }
    has_service_fk = _appointments_has_service_fk(connection)

    if "service_id" in columns and has_service_fk:
        return

    _migrate_appointments_add_service_fk(connection)


# Qué hace: crea tablas, corrige esquema legacy y siembra datos base.
# Qué valida: columnas faltantes y existencia mínima de empleados/servicios.
# Qué retorna: N/A (deja base lista para operar).
def init_db():
    Base.metadata.create_all(bind=engine)

    with engine.begin() as connection:
        _ensure_appointments_service_fk(connection)

        existing_emp_columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(employees)")).fetchall()
        }
        if "work_area" not in existing_emp_columns:
            connection.execute(text("ALTER TABLE employees ADD COLUMN work_area TEXT NOT NULL DEFAULT 'General'"))
        

        existing_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(appointments)")).fetchall()
        }

        extra_columns = {
            "payment_last4": "TEXT",
            "payment_bank": "TEXT",
            "payment_phone": "TEXT",
            "payment_payer_id": "TEXT",
            "payment_datetime": "TEXT",
            "payment_submitted_at": "TEXT",
            "rating": "INTEGER",
            "rating_comment": "TEXT",
            "rating_token": "TEXT UNIQUE",
        }

        for column_name, column_type in extra_columns.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE appointments ADD COLUMN {column_name} {column_type}"))

    with get_session() as session:
        existing_employees = session.query(Employee).count()
        if existing_employees == 0:
            session.add_all(
                [
                    Employee(name="Luis", email="luis@barberia.local"),
                    Employee(name="Carlos", email="carlos@barberia.local"),
                    Employee(name="Ana", email="ana@barberia.local"),
                ]
            )
            session.commit()

        existing_services = session.query(Service).count()
        if existing_services == 0:
            session.add_all(
                [
                    Service(name="Corte de cabello", duration_minutes=45, price_usd=8.0),
                    Service(name="Corte de barba", duration_minutes=30, price_usd=6.0),
                    Service(name="Manicura", duration_minutes=60, price_usd=10.0),
                ]
            )
            session.commit()

    _refresh_services_cache()


# Qué hace: sincroniza caché en memoria de servicios y precios.
# Qué valida: N/A (usa datos persistidos como fuente de verdad).
# Qué retorna: N/A (actualiza variables globales de caché).
def _refresh_services_cache():
    with get_session() as session:
        rows = session.execute(select(Service).order_by(Service.name.asc())).scalars().all()

    SERVICE_CATALOG.clear()
    SERVICE_PRICE_USD.clear()

    for item in rows:
        SERVICE_CATALOG[item.name] = int(item.duration_minutes)
        SERVICE_PRICE_USD[item.name] = float(item.price_usd)

    SERVICES.clear()
    SERVICES.extend(SERVICE_CATALOG.keys())


# Qué hace: consulta todos los servicios ordenados por nombre.
# Qué valida: N/A (consulta directa).
# Qué retorna: lista de objetos `Service`.
def get_services():
    with get_session() as session:
        return session.execute(select(Service).order_by(Service.name.asc())).scalars().all()


# Qué hace: crea un nuevo servicio en catálogo.
# Qué valida: nombre único, longitud, duración positiva y precio > 0.
# Qué retorna: `(success, mensaje)`.
def create_service(name: str, duration_minutes: int, price_usd: float):
    normalized_name = re.sub(r"\s+", " ", (name or "").strip())

    if not normalized_name:
        return False, "El nombre del servicio es obligatorio."

    if len(normalized_name) > MAX_LONGITUD_NOMBRE_SERVICIO:
        return False, "El nombre del servicio es demasiado largo."

    if PATRON_NOMBRE_SERVICIO.match(normalized_name) is None or not any(ch.isalnum() for ch in normalized_name):
        return False, "El nombre del servicio es inválido."

    if not isinstance(duration_minutes, int) or duration_minutes <= 0:
        return False, "La duración del servicio debe ser un número entero positivo."

    if duration_minutes > 480:
        return False, "La duración del servicio no puede ser mayor a 480 minutos."

    if price_usd <= 0:
        return False, "El precio del servicio debe ser mayor a 0."

    if price_usd > MAX_PRECIO_SERVICIO_USD:
        return False, f"El precio del servicio no puede ser mayor a {MAX_PRECIO_SERVICIO_USD} USD."

    with get_session() as session:
        existing = session.execute(
            select(Service).where(Service.name == normalized_name)
        ).scalar_one_or_none()

        if existing:
            return False, "Ya existe un servicio con ese nombre."

        session.add(
            Service(
                name=normalized_name,
                duration_minutes=duration_minutes,
                price_usd=round(float(price_usd), 2),
            )
        )
        session.commit()

    _refresh_services_cache()
    return True, "Servicio agregado correctamente."


# Qué hace: actualiza datos de un servicio existente.
# Qué valida: existencia del servicio, duplicados y rangos válidos.
# Qué retorna: `(success, mensaje)`.
def update_service(service_id: int, name: str, duration_minutes: int, price_usd: float):
    normalized_name = re.sub(r"\s+", " ", (name or "").strip())

    if not normalized_name:
        return False, "El nombre del servicio es obligatorio."

    if len(normalized_name) > MAX_LONGITUD_NOMBRE_SERVICIO:
        return False, "El nombre del servicio es demasiado largo."

    if PATRON_NOMBRE_SERVICIO.match(normalized_name) is None or not any(ch.isalnum() for ch in normalized_name):
        return False, "El nombre del servicio es inválido."

    if not isinstance(duration_minutes, int) or duration_minutes <= 0:
        return False, "La duración del servicio debe ser un número entero positivo."

    if duration_minutes > 480:
        return False, "La duración del servicio no puede ser mayor a 480 minutos."

    if price_usd <= 0:
        return False, "El precio del servicio debe ser mayor a 0."

    if price_usd > MAX_PRECIO_SERVICIO_USD:
        return False, f"El precio del servicio no puede ser mayor a {MAX_PRECIO_SERVICIO_USD} USD."

    with get_session() as session:
        service = session.get(Service, service_id)
        if not service:
            return False, "Servicio no encontrado."

        duplicate = session.execute(
            select(Service).where(
                Service.name == normalized_name,
                Service.id != service_id,
            )
        ).scalar_one_or_none()

        if duplicate:
            return False, "Ya existe otro servicio con ese nombre."

        service.name = normalized_name
        service.duration_minutes = duration_minutes
        service.price_usd = round(float(price_usd), 2)

        session.commit()

    _refresh_services_cache()
    return True, "Servicio actualizado correctamente."


# Qué hace: elimina un servicio del catálogo.
# Qué valida: que exista y no tenga citas asociadas.
# Qué retorna: `(success, mensaje)`.
def delete_service(service_id: int):
    with get_session() as session:
        service = session.get(Service, service_id)
        if not service:
            return False, "Servicio no encontrado."

        has_appointments = session.query(Appointment).filter(Appointment.service_id == service.id).count() > 0
        if has_appointments:
            return False, "No se puede eliminar porque tiene citas asociadas."

        session.delete(service)
        session.commit()

    _refresh_services_cache()
    return True, "Servicio eliminado correctamente."


# Qué hace: consulta empleados ordenados alfabéticamente.
# Qué valida: N/A (consulta directa).
# Qué retorna: lista de objetos `Employee`.
def get_employees():
    with get_session() as session:
        employees = session.execute(select(Employee).order_by(Employee.name)).scalars().all()
        return employees


# Qué hace: crea un empleado nuevo.
# Qué valida: nombre obligatorio/no duplicado y email válido opcional.
# Qué retorna: `(success, mensaje)`.
def create_employee(name: str, email: str | None = None, work_area: str = "General"):
    normalized_name = re.sub(r"\s+", " ", (name or "").strip())
    normalized_email = (email or "").strip().lower()
    normalized_work_area = (work_area or "General").strip()

    if not normalized_name:
        return False, "El nombre del empleado es obligatorio."

    if len(normalized_name) > MAX_LONGITUD_NOMBRE_EMPLEADO:
        return False, "El nombre del empleado es demasiado largo."

    if PATRON_NOMBRE_EMPLEADO.match(normalized_name) is None or not any(ch.isalpha() for ch in normalized_name):
        return False, "El nombre del empleado es inválido."

    if normalized_email and (
        len(normalized_email) > MAX_LONGITUD_EMAIL_EMPLEADO
        or PATRON_EMAIL.match(normalized_email) is None
    ):
        return False, "El correo del empleado es inválido."

    with get_session() as session:
        existing = session.execute(
            select(Employee).where(Employee.name == normalized_name)
        ).scalar_one_or_none()

        if existing:
            return False, "Ya existe un empleado con ese nombre."

        session.add(
            Employee(
                name=normalized_name,
                email=normalized_email or None,
                work_area=normalized_work_area,
            )
        )
        session.commit()

    return True, "Empleado agregado correctamente."


# Qué hace: actualiza nombre y/o email de un empleado.
# Qué valida: existencia, duplicados de nombre y formato de email.
# Qué retorna: `(success, mensaje)`.
def update_employee(employee_id: int, name: str, email: str | None = None, work_area: str = "General"):
    normalized_name = re.sub(r"\s+", " ", (name or "").strip())
    normalized_email = (email or "").strip().lower()
    normalized_work_area = (work_area or "General").strip()

    if not normalized_name:
        return False, "El nombre del empleado es obligatorio."

    if len(normalized_name) > MAX_LONGITUD_NOMBRE_EMPLEADO:
        return False, "El nombre del empleado es demasiado largo."

    if PATRON_NOMBRE_EMPLEADO.match(normalized_name) is None or not any(ch.isalpha() for ch in normalized_name):
        return False, "El nombre del empleado es inválido."

    if normalized_email and (
        len(normalized_email) > MAX_LONGITUD_EMAIL_EMPLEADO
        or PATRON_EMAIL.match(normalized_email) is None
    ):
        return False, "El correo del empleado es inválido."

    with get_session() as session:
        employee = session.get(Employee, employee_id)
        if not employee:
            return False, "Empleado no encontrado."

        duplicate = session.execute(
            select(Employee).where(
                Employee.name == normalized_name,
                Employee.id != employee_id,
            )
        ).scalar_one_or_none()

        if duplicate:
            return False, "Ya existe otro empleado con ese nombre."

        employee.name = normalized_name
        employee.email = normalized_email or None
        if normalized_work_area:
            employee.work_area = normalized_work_area
        session.commit()

    return True, "Empleado actualizado correctamente."


# Qué hace: elimina un empleado.
# Qué valida: que exista y no tenga citas asociadas.
# Qué retorna: `(success, mensaje)`.
def delete_employee(employee_id: int):
    with get_session() as session:
        employee = session.get(Employee, employee_id)
        if not employee:
            return False, "Empleado no encontrado."

        has_appointments = session.query(Appointment).filter(Appointment.employee_id == employee_id).count() > 0
        if has_appointments:
            return False, "No se puede eliminar porque tiene citas asociadas."

        session.delete(employee)
        session.commit()

    return True, "Empleado eliminado correctamente."


# Qué hace: obtiene duración de un servicio desde caché.
# Qué valida: refresca caché si el servicio no está cargado.
# Qué retorna: minutos de duración o `None`.
def get_service_duration(service_name: str):
    if service_name not in SERVICE_CATALOG:
        _refresh_services_cache()

    return SERVICE_CATALOG.get(service_name)


# Qué hace: genera bloques base del día según horario laboral.
# Qué valida: N/A (usa límites configurados).
# Qué retorna: lista de `datetime` con slots del día.
def _build_daily_slots(target_date: date):
    start_dt = datetime.combine(target_date, time(BUSINESS_OPEN_HOUR, 0))
    end_dt = datetime.combine(target_date, time(BUSINESS_CLOSE_HOUR, 0))

    slots = []
    current = start_dt
    while current < end_dt:
        slots.append(current)
        current += timedelta(minutes=SLOT_DURATION_MINUTES)

    return slots


# Qué hace: calcula horas disponibles para un empleado y servicio.
# Qué valida: evita horas pasadas, solapes y cierre de jornada.
# Qué retorna: lista de horas (`HH:MM`) disponibles.
def get_available_slots(date_str: str, employee_id: int, service_name: str):
    service_duration = get_service_duration(service_name)
    if not service_duration:
        return []

    target_date = datetime.strptime(date_str, DATE_FORMAT).date()
    all_slots = _build_daily_slots(target_date)
    business_end = datetime.combine(target_date, time(BUSINESS_CLOSE_HOUR, 0))

    day_start = f"{date_str} 00:00"
    day_end = f"{date_str} 23:59"

    with get_session() as session:
        busy_rows = session.execute(
            select(Appointment.appointment_datetime, Service.duration_minutes).join(
                Service, Service.id == Appointment.service_id
            ).where(
                Appointment.employee_id == employee_id,
                Appointment.status.in_(["scheduled", "pending_payment"]),
                Appointment.appointment_datetime >= day_start,
                Appointment.appointment_datetime <= day_end,
            )
        ).all()

    busy_intervals = []
    for row in busy_rows:
        busy_start = datetime.strptime(row.appointment_datetime, DATETIME_FORMAT)
        busy_duration = int(row.duration_minutes or SLOT_DURATION_MINUTES)
        busy_end = busy_start + timedelta(minutes=busy_duration)
        busy_intervals.append((busy_start, busy_end))

    now = get_local_now()
    available = []
    for slot in all_slots:
        if slot <= now:
            continue

        slot_end = slot + timedelta(minutes=service_duration)
        if slot_end > business_end:
            continue

        overlaps = any(slot < busy_end and slot_end > busy_start for busy_start, busy_end in busy_intervals)
        if not overlaps:
            available.append(slot.strftime("%H:%M"))

    return available


# Qué hace: crea una cita nueva en estado `pending_payment`.
# Qué valida: fecha, servicio, disponibilidad, datos del cliente y concurrencia.
# Qué retorna: `(success, mensaje, appointment_id|None)`.
def create_appointment(client_name: str, client_email: str, service_name: str, employee_id: int, date_str: str, time_str: str):
    try:
        selected_date = datetime.strptime(date_str, DATE_FORMAT).date()
    except ValueError:
        return False, "Fecha de cita inválida.", None

    current_date = get_local_now().date()
    if selected_date < current_date:
        return False, "No puedes agendar citas en fechas pasadas.", None

    if selected_date.year != current_date.year:
        return False, "Solo puedes agendar citas dentro del año actual.", None

    if get_service_duration(service_name) is None:
        return False, "Servicio no válido.", None

    if time_str not in get_available_slots(date_str, employee_id, service_name):
        return False, "El horario ya no está disponible.", None

    appointment_datetime = datetime.strptime(f"{date_str} {time_str}", DATETIME_FORMAT)
    normalized_email = client_email.strip().lower()
    normalized_name = client_name.strip()

    if not normalized_name:
        return False, "El nombre del cliente es obligatorio.", None

    if len(normalized_name) > MAX_LONGITUD_NOMBRE_CLIENTE:
        return False, "El nombre del cliente es demasiado largo.", None

    if PATRON_NOMBRE_EMPLEADO.match(normalized_name) is None or not any(ch.isalpha() for ch in normalized_name):
        return False, "El nombre del cliente es inválido.", None

    if len(normalized_email) > MAX_LONGITUD_EMAIL or PATRON_EMAIL.match(normalized_email) is None:
        return False, "El correo del cliente es inválido.", None

    with get_session() as session:
        try:
            service = session.execute(
                select(Service).where(Service.name == service_name)
            ).scalar_one_or_none()
            if not service:
                return False, "Servicio no válido.", None

            # Buscar cliente por nombre y email juntos
            client = session.execute(
                select(Client).where(
                    Client.email == normalized_email,
                    Client.name == normalized_name
                )
            ).scalar_one_or_none()

            if not client:
                # Permitir múltiples clientes con el mismo email pero distinto nombre
                client = Client(name=normalized_name, email=normalized_email)
                session.add(client)
                session.flush()
            # Si ya existe cliente con ese nombre y email, usarlo tal cual
            # No actualizar el nombre si el email ya existe con otro nombre

            slot_datetime = appointment_datetime.strftime(DATETIME_FORMAT)
            existing_slot = session.execute(
                select(Appointment).where(
                    Appointment.employee_id == employee_id,
                    Appointment.appointment_datetime == slot_datetime,
                )
            ).scalar_one_or_none()

            if existing_slot:
                if existing_slot.status == "canceled":
                    session.delete(existing_slot)
                    session.flush()
                else:
                    return False, "El horario ya fue reservado por otro cliente.", None

            appointment = Appointment(
                client_id=client.id,
                service_id=service.id,
                employee_id=employee_id,
                appointment_datetime=slot_datetime,
                status="pending_payment",
                reminder_sent=0,
                created_at=get_local_now().strftime(CREATED_AT_FORMAT),
                rating_token=uuid4().hex,
            )
            session.add(appointment)
            session.commit()
        except IntegrityError:
            session.rollback()
            return False, "El horario ya fue reservado por otro cliente.", None

    return True, "Reserva creada. Completa el pago para confirmarla.", appointment.id


# Qué hace: busca una cita por ID con joins de cliente/servicio/empleado.
# Qué valida: N/A (consulta directa).
# Qué retorna: diccionario con detalle de cita o `None`.
def get_appointment_with_details(appointment_id: int):
    with get_session() as session:
        row = session.execute(
            select(
                Appointment.id,
                Client.name.label("client_name"),
                Client.email.label("client_email"),
                Service.name.label("service_name"),
                Service.duration_minutes.label("service_duration"),
                Appointment.appointment_datetime,
                Appointment.status,
                Appointment.payment_last4,
                Appointment.payment_bank,
                Appointment.payment_phone,
                Appointment.payment_payer_id,
                Appointment.payment_datetime,
                Appointment.payment_submitted_at,
                Appointment.created_at,
                Appointment.employee_id,
                Employee.name.label("employee_name"),
            )
            .join(Client, Client.id == Appointment.client_id)
            .join(Service, Service.id == Appointment.service_id)
            .join(Employee, Employee.id == Appointment.employee_id)
            .where(Appointment.id == appointment_id)
        ).first()

    if not row:
        return None

    item = dict(row._mapping)
    item["service_duration"] = int(item.get("service_duration") or SLOT_DURATION_MINUTES)
    return item


# Qué hace: calcula tiempo restante para pagar una cita pendiente.
# Qué valida: formato de `created_at`.
# Qué retorna: segundos restantes (mínimo 0).
def get_payment_remaining_seconds(appointment: dict, pending_minutes: int = PAYMENT_PENDING_MINUTES):
    try:
        created_at = datetime.strptime(appointment["created_at"], CREATED_AT_FORMAT)
    except (TypeError, ValueError):
        return 0

    expires_at = created_at + timedelta(minutes=pending_minutes)
    remaining = int((expires_at - get_local_now()).total_seconds())
    return max(remaining, 0)


# Qué hace: lista citas para panel admin con filtros opcionales.
# Qué valida: estado dentro del conjunto permitido.
# Qué retorna: lista de diccionarios con detalle de citas.
def get_appointments_for_admin(date_str: str | None = None, employee_id: int | None = None, status: str | None = None):
    with get_session() as session:
        query = (
            select(
                Appointment.id,
                Client.name.label("client_name"),
                Client.email.label("client_email"),
                Service.name.label("service_name"),
                Service.duration_minutes.label("service_duration"),
                Appointment.appointment_datetime,
                Appointment.status,
                Appointment.payment_last4,
                Appointment.payment_bank,
                Appointment.payment_phone,
                Appointment.payment_payer_id,
                Appointment.payment_datetime,
                Appointment.payment_submitted_at,
                Appointment.created_at,
                Appointment.employee_id,
                Employee.name.label("employee_name"),
            )
            .join(Client, Client.id == Appointment.client_id)
            .join(Service, Service.id == Appointment.service_id)
            .join(Employee, Employee.id == Appointment.employee_id)
            .order_by(Appointment.appointment_datetime.asc())
        )

        if date_str:
            query = query.where(Appointment.appointment_datetime >= f"{date_str} 00:00")
            query = query.where(Appointment.appointment_datetime <= f"{date_str} 23:59")

        if employee_id:
            query = query.where(Appointment.employee_id == employee_id)

        if status in ALLOWED_APPOINTMENT_STATUS:
            query = query.where(Appointment.status == status)

        rows = session.execute(query).all()

    output = []
    for row in rows:
        item = dict(row._mapping)
        item["service_duration"] = int(item.get("service_duration") or SLOT_DURATION_MINUTES)
        output.append(item)

    return output


# Qué hace: obtiene citas próximas a 24h sin recordatorio enviado.
# Qué valida: estado `scheduled` y bandera `reminder_sent == 0`.
# Qué retorna: lista de citas pendientes de recordatorio.
def get_pending_reminders():
    now = get_local_now()
    next_24h = now + timedelta(hours=24)
    now_str = now.strftime(DATETIME_FORMAT)
    next_24h_str = next_24h.strftime(DATETIME_FORMAT)

    with get_session() as session:
        rows = session.execute(
            select(
                Appointment.id,
                Client.name.label("client_name"),
                Client.email.label("client_email"),
                Service.name.label("service_name"),
                Service.duration_minutes.label("service_duration"),
                Appointment.appointment_datetime,
                Employee.name.label("employee_name"),
            )
            .join(Client, Client.id == Appointment.client_id)
            .join(Service, Service.id == Appointment.service_id)
            .join(Employee, Employee.id == Appointment.employee_id)
            .where(
                Appointment.status == "scheduled",
                Appointment.reminder_sent == 0,
                Appointment.appointment_datetime > now_str,
                Appointment.appointment_datetime <= next_24h_str,
            )
            .order_by(Appointment.appointment_datetime.asc())
        ).all()

    output = []
    for row in rows:
        item = dict(row._mapping)
        item["service_duration"] = int(item.get("service_duration") or SLOT_DURATION_MINUTES)
        output.append(item)

    return output


# Qué hace: marca una cita como recordatorio enviado.
# Qué valida: existencia de la cita.
# Qué retorna: N/A.
def mark_reminder_sent(appointment_id: int):
    with get_session() as session:
        appointment = session.get(Appointment, appointment_id)
        if appointment:
            appointment.reminder_sent = 1
            session.commit()


# Qué hace: cancela citas pendientes de pago cuyo tiempo expiró.
# Qué valida: estado `pending_payment` y fecha `created_at` parseable.
# Qué retorna: cantidad de citas canceladas.
def cancel_expired_pending_payments():
    now = get_local_now()
    canceled_count = 0

    with get_session() as session:
        pending_rows = session.execute(
            select(Appointment).where(
                Appointment.status == "pending_payment",
                Appointment.payment_submitted_at == None
            )
        ).scalars().all()

        for appointment in pending_rows:
            try:
                created_at = datetime.strptime(appointment.created_at, CREATED_AT_FORMAT)
            except (TypeError, ValueError):
                appointment.status = "canceled"
                canceled_count += 1
                continue

            expires_at = created_at + timedelta(minutes=PAYMENT_PENDING_MINUTES)
            if now > expires_at:
                appointment.status = "canceled"
                canceled_count += 1

        if canceled_count > 0:
            session.commit()

    return canceled_count


# Qué hace: actualiza el estado de una cita.
# Qué valida: estado permitido y existencia de cita.
# Qué retorna: `(success, mensaje)`.
def update_appointment_status(appointment_id: int, new_status: str):
    if new_status not in ALLOWED_APPOINTMENT_STATUS:
        return False, "Estado inválido."

    with get_session() as session:
        appointment = session.get(Appointment, appointment_id)
        if not appointment:
            return False, "La cita no existe."

        appointment.status = new_status
        session.commit()

    return True, "Estado de cita actualizado correctamente."


# Qué hace: elimina todas las citas con estado `canceled`.
# Qué valida: N/A (opera por estado fijo).
# Qué retorna: cantidad de citas eliminadas.
def delete_canceled_appointments():
    with get_session() as session:
        deleted_count = session.query(Appointment).filter(Appointment.status == "canceled").delete(
            synchronize_session=False
        )
        session.commit()

    return int(deleted_count or 0)


# Qué hace: elimina todas las citas con estado `completed`.
# Qué valida: N/A (opera por estado fijo).
# Qué retorna: cantidad de citas eliminadas.
def delete_completed_appointments():
    with get_session() as session:
        deleted_count = session.query(Appointment).filter(Appointment.status == "completed").delete(
            synchronize_session=False
        )
        session.commit()

    return int(deleted_count or 0)


# Qué hace: normaliza teléfonos venezolanos a formato local.
# Qué valida: prefijo, longitud y estructura móvil válida.
# Qué retorna: teléfono normalizado o `None`.
def _normalizar_telefono_ve(phone: str):
    digits = "".join(ch for ch in phone if ch.isdigit())

    if digits.startswith("58") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("58") and len(digits) == 13 and digits[2] == "0":
        digits = digits[2:]

    if len(digits) != 11 or not digits.startswith("04"):
        return None

    return digits


# Qué hace: normaliza cédula/RIF del emisor.
# Qué valida: patrón venezolano permitido.
# Qué retorna: identificador normalizado o `None`.
def _normalizar_cedula_rif_ve(payer_id: str):
    compact = re.sub(r"\s+", "", payer_id.strip().upper())

    if not PATRON_CEDULA_RIF_VE.match(compact):
        return None

    if compact[0].isdigit():
        return compact

    letter = compact[0]
    digits = "".join(ch for ch in compact[1:] if ch.isdigit())
    return f"{letter}-{digits}"


# Qué hace: registra comprobante de pago de una cita pendiente.
# Qué valida: campos, formato, banco, ventana temporal y estado de cita.
# Qué retorna: `(success, mensaje)`.
def submit_payment_proof(
    appointment_id: int,
    payment_last4: str,
    payment_bank: str,
    payment_phone: str,
    payment_payer_id: str,
    payment_datetime: str,
):
    normalized_last4 = payment_last4.strip()
    normalized_bank = payment_bank.strip()
    normalized_phone = _normalizar_telefono_ve(payment_phone)
    normalized_payer_id = _normalizar_cedula_rif_ve(payment_payer_id)
    normalized_payment_datetime = payment_datetime.strip()

    if not normalized_last4.isdigit() or len(normalized_last4) != 4:
        return False, "Los últimos 4 dígitos deben ser numéricos."

    if not all([normalized_bank, payment_phone.strip(), payment_payer_id.strip(), normalized_payment_datetime]):
        return False, "Debes completar todos los datos del pago."

    if len(normalized_bank) > MAX_LONGITUD_BANCO_PAGO:
        return False, "El banco emisor es demasiado largo."

    if normalized_bank not in BANCOS_VENEZOLANOS:
        return False, "Debes seleccionar un banco emisor válido de la lista."

    if not normalized_phone:
        return False, "El teléfono emisor debe ser venezolano válido (ej: 04121234567)."

    if not normalized_payer_id:
        return False, "La cédula/RIF emisor es inválida (ej: V-12345678)."

    try:
        parsed_payment_datetime = datetime.strptime(normalized_payment_datetime, "%Y-%m-%dT%H:%M")
    except ValueError:
        return False, "Fecha y hora de pago inválida."

    now = get_local_now()
    if parsed_payment_datetime.year != now.year:
        return False, "La fecha y hora del pago debe estar dentro del año actual."

    with get_session() as session:
        appointment = session.get(Appointment, appointment_id)
        if not appointment:
            return False, "La cita no existe."

        if appointment.status != "pending_payment":
            return False, "Esta cita ya no está esperando pago."

        created_at = datetime.strptime(appointment.created_at, CREATED_AT_FORMAT)
        expires_at = created_at + timedelta(minutes=PAYMENT_PENDING_MINUTES)
        payment_proof_deadline = created_at + timedelta(minutes=PAYMENT_PROOF_WINDOW_MINUTES)

        if parsed_payment_datetime > payment_proof_deadline:
            return False, f"La fecha/hora del pago no puede ser mayor a {PAYMENT_PROOF_WINDOW_MINUTES} minutos después de crear la cita."

        if parsed_payment_datetime < (created_at - timedelta(minutes=5)):
            return False, "La fecha/hora del pago no puede ser previa a la creación de la cita."

        if get_local_now() > expires_at:
            appointment.status = "canceled"
            session.commit()
            return False, "El tiempo para pagar expiró. Debes crear una nueva reserva."

        appointment.payment_last4 = normalized_last4
        appointment.payment_bank = normalized_bank
        appointment.payment_phone = normalized_phone
        appointment.payment_payer_id = normalized_payer_id
        appointment.payment_datetime = normalized_payment_datetime.replace("T", " ")
        appointment.payment_submitted_at = get_local_now().strftime(CREATED_AT_FORMAT)
        session.commit()

    return True, "Comprobante recibido. Será validado por administración."


# Qué hace: procesa decisión admin sobre un pago (`approve/reject`).
# Qué valida: decisión válida, existencia de cita y estado pendiente.
# Qué retorna: `(success, mensaje)`.
def review_payment_submission(appointment_id: int, decision: str):
    normalized_decision = decision.strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        return False, "Acción de pago inválida."

    with get_session() as session:
        appointment = session.get(Appointment, appointment_id)
        if not appointment:
            return False, "La cita no existe."

        if appointment.status != "pending_payment":
            return False, "La cita no está pendiente de pago."

        if normalized_decision == "approve":
            appointment.status = "scheduled"
            appointment.reminder_sent = 0
            session.commit()
            return True, "Pago aprobado y cita confirmada."

        appointment.status = "canceled"
        session.commit()
        return True, "Pago rechazado y cita cancelada."


# Qué hace: calcula KPIs de citas para el dashboard admin.
# Qué valida: filtros opcionales (fecha, empleado, estado).
# Qué retorna: diccionario de métricas agregadas.
def get_admin_metrics(date_str: str | None = None, employee_id: int | None = None, status: str | None = None):
    with get_session() as session:
        query = (
            select(
                Appointment.appointment_datetime,
                Service.name.label("service_name"),
                Appointment.status,
                Employee.name.label("employee_name"),
            )
            .join(Service, Service.id == Appointment.service_id)
            .join(Employee, Employee.id == Appointment.employee_id)
            .order_by(Appointment.appointment_datetime.asc())
        )

        if date_str:
            query = query.where(Appointment.appointment_datetime >= f"{date_str} 00:00")
            query = query.where(Appointment.appointment_datetime <= f"{date_str} 23:59")

        if employee_id:
            query = query.where(Appointment.employee_id == employee_id)

        if status in ALLOWED_APPOINTMENT_STATUS:
            query = query.where(Appointment.status == status)

        rows = session.execute(query).all()

    total = len(rows)
    if total == 0:
        return {
            "total_appointments": 0,
            "most_requested_service": "-",
            "most_requested_count": 0,
            "top_employee": "-",
            "top_employee_count": 0,
            "status_counts": {"pending_payment": 0, "scheduled": 0, "completed": 0, "canceled": 0},
            "appointments_by_day": [],
        }

    service_counter = Counter(row.service_name for row in rows)
    employee_counter = Counter(row.employee_name for row in rows)
    status_counter = Counter(row.status for row in rows)
    day_counter = Counter(row.appointment_datetime.split(" ")[0] for row in rows)

    most_requested_service, most_requested_count = service_counter.most_common(1)[0]
    top_employee, top_employee_count = employee_counter.most_common(1)[0]

    appointments_by_day = [
        {"date": day, "count": count}
        for day, count in sorted(day_counter.items())
    ]

    return {
        "total_appointments": total,
        "most_requested_service": most_requested_service,
        "most_requested_count": most_requested_count,
        "top_employee": top_employee,
        "top_employee_count": top_employee_count,
        "status_counts": {
            "pending_payment": status_counter.get("pending_payment", 0),
            "scheduled": status_counter.get("scheduled", 0),
            "completed": status_counter.get("completed", 0),
            "canceled": status_counter.get("canceled", 0),
        },
        "appointments_by_day": appointments_by_day,
    }


# Qué hace: busca cita asociada a un token de calificación.
# Qué valida: token no vacío.
# Qué retorna: diccionario de cita/rating o `None`.
def get_appointment_by_rating_token(token: str):
    if not token:
        return None
    with get_session() as session:
        row = session.execute(
            select(
                Appointment.id,
                Client.name.label("client_name"),
                Service.name.label("service_name"),
                Employee.name.label("employee_name"),
                Appointment.rating
            )
            .join(Client, Client.id == Appointment.client_id)
            .join(Service, Service.id == Appointment.service_id)
            .join(Employee, Employee.id == Appointment.employee_id)
            .where(Appointment.rating_token == token)
        ).first()

    if not row:
        return None
    return dict(row._mapping)


# Qué hace: sanea comentario de calificación (espacios/control chars).
# Qué valida: longitud máxima permitida.
# Qué retorna: `(comentario_normalizado|None, mensaje_error)`.
def _sanitizar_comentario_calificacion(comment: str):
    normalized_comment = (comment or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized_comment = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", normalized_comment)
    normalized_comment = "\n".join(line.strip() for line in normalized_comment.split("\n"))
    normalized_comment = re.sub(r"[ \t]{2,}", " ", normalized_comment)
    normalized_comment = re.sub(r"\n{3,}", "\n\n", normalized_comment)
    normalized_comment = normalized_comment.strip()

    if len(normalized_comment) > MAX_LONGITUD_COMENTARIO_CALIFICACION:
        return None, f"El comentario no puede exceder {MAX_LONGITUD_COMENTARIO_CALIFICACION} caracteres."

    return normalized_comment, ""


# Qué hace: guarda rating y comentario de una cita.
# Qué valida: token válido, no calificada previamente y rango 1..5.
# Qué retorna: `(success, mensaje)`.
def submit_appointment_rating(token: str, rating: int, comment: str):
    normalized_comment, comment_error = _sanitizar_comentario_calificacion(comment)
    if comment_error:
        return False, comment_error

    with get_session() as session:
        appointment = session.execute(select(Appointment).where(Appointment.rating_token == token)).scalar_one_or_none()
        if not appointment:
            return False, "Token inválido o cita no encontrada."
        
        if appointment.rating is not None:
            return False, "Esta cita ya fue calificada."

        if not (1 <= rating <= 5):
            return False, "La calificación debe estar entre 1 y 5 estrellas."

        appointment.rating = rating
        appointment.rating_comment = normalized_comment or None
        session.commit()
    return True, "Calificación enviada con éxito. ¡Gracias por tu opinión!"


# Qué hace: genera ranking mensual por promedio de estrellas.
# Qué valida: rango mensual por año/mes y citas completadas con rating.
# Qué retorna: lista ordenada de estadísticas por empleado.
def get_employee_ratings_summary(year: int, month: int):
    # Devuelve el promedio de estrellas y cantidad de citas para el mes dado
    start_date = f"{year}-{month:02d}-01 00:00"
    if month == 12:
        end_date = f"{year+1}-01-01 00:00"
    else:
        end_date = f"{year}-{month+1:02d}-01 00:00"

    with get_session() as session:
        rows = session.execute(
            select(
                Employee.id,
                Employee.name,
                Employee.work_area,
                Appointment.rating,
                Appointment.rating_comment
            )
            .join(Appointment, Employee.id == Appointment.employee_id)
            .where(
                Appointment.status == "completed",
                Appointment.appointment_datetime >= start_date,
                Appointment.appointment_datetime < end_date,
                Appointment.rating != None
            )
        ).all()

    stats = {}
    for row in rows:
        emp_id = row.id
        if emp_id not in stats:
            stats[emp_id] = {
                "id": emp_id,
                "name": row.name,
                "work_area": row.work_area,
                "total_ratings": 0,
                "sum_ratings": 0,
                "average": 0.0,
                "comments": []
            }
        
        stats[emp_id]["total_ratings"] += 1
        stats[emp_id]["sum_ratings"] += row.rating
        if row.rating_comment:
            stats[emp_id]["comments"].append({"rating": row.rating, "comment": row.rating_comment})

    for emp_id, data in stats.items():
        if data["total_ratings"] > 0:
            raw_average = data["sum_ratings"] / data["total_ratings"]
            data["average"] = round(raw_average, 1)
        else:
            data["average"] = 0.0

    result_list = list(stats.values())
    result_list.sort(key=lambda x: (x["average"], x["total_ratings"]), reverse=True)
    return result_list


# Qué hace: calcula ranking público global de empleados.
# Qué valida: considera solo calificaciones existentes.
# Qué retorna: lista de empleados con promedio y total de reseñas.
def get_public_employee_ratings():
    with get_session() as session:
        # Trae todos los empleados (incluso los sin calificaciones)
        employees = session.execute(select(Employee)).scalars().all()
        
        # Trae todas las calificaciones válidas
        ratings = session.execute(
            select(Appointment.employee_id, Appointment.rating)
            .where(Appointment.rating != None)
        ).all()

    stats = {emp.id: {"id": emp.id, "name": emp.name, "work_area": emp.work_area, "total_ratings": 0, "sum_ratings": 0, "average": 0.0} for emp in employees}
    
    for row in ratings:
        eid = row.employee_id
        if eid in stats:
            stats[eid]["total_ratings"] += 1
            stats[eid]["sum_ratings"] += row.rating

    for eid in stats:
        if stats[eid]["total_ratings"] > 0:
            raw_average = stats[eid]["sum_ratings"] / stats[eid]["total_ratings"]
            stats[eid]["average"] = round(raw_average, 1)
        else:
            stats[eid]["average"] = 0.0

    result_list = list(stats.values())
    # Ordenar por el que tenga mejor promedio, luego el que tenga más calificaciones
    result_list.sort(key=lambda x: (x["average"], x["total_ratings"]), reverse=True)
    return result_list


# Qué hace: valida elegibilidad de cliente para calificar a un empleado.
# Qué valida: email, cliente existente y cita completada sin calificar.
# Qué retorna: `(success, mensaje, rating_token|None)`.
def validate_client_for_rating(employee_id: int, client_email: str):
    normalized_email = client_email.strip().lower()
    if not normalized_email:
        return False, "Por favor indica tu correo electrónico.", None

    with get_session() as session:
        client = session.execute(
            select(Client).where(Client.email == normalized_email)
        ).scalar_one_or_none()

        if not client:
            return False, "No encontramos ningún cliente registrado con este correo.", None

        # Busca una cita de este cliente con este empleado, que este completada y no este calificada aun
        appointment = session.execute(
            select(Appointment)
            .where(
                Appointment.client_id == client.id,
                Appointment.employee_id == employee_id,
                Appointment.status == "completed",
                Appointment.rating == None,
                Appointment.rating_token != None
            )
            .order_by(Appointment.appointment_datetime.desc())
        ).scalars().first()

        if not appointment:
            return False, "Actualmente no tienes servicios sin calificar correspondientes a este empleado (asegúrate de que tu cita ya haya finalizado).", None

        return True, "Validación exitosa.", appointment.rating_token
