from apscheduler.schedulers.background import BackgroundScheduler

from base_datos import cancel_expired_pending_payments, get_pending_reminders, mark_reminder_sent
from servicio_correo import send_reservation_reminder

scheduler = BackgroundScheduler()


def process_reminders():
    citas_pendientes = get_pending_reminders()

    for appointment in citas_pendientes:
        enviado = send_reservation_reminder(
            client_email=appointment["client_email"],
            client_name=appointment["client_name"],
            service_name=appointment["service_name"],
            employee_name=appointment["employee_name"],
            appointment_datetime=appointment["appointment_datetime"],
        )

        if enviado:
            mark_reminder_sent(appointment["id"])


def process_expired_pending_payments():
    cancel_expired_pending_payments()


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            process_expired_pending_payments,
            "interval",
            seconds=30,
            id="expired_pending_payments_job",
            replace_existing=True,
        )
        scheduler.add_job(process_reminders, "interval", minutes=1, id="reminders_job", replace_existing=True)
        scheduler.start()
