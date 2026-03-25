import urllib.request
import urllib.error
import json
import base64

from configuracion import MAILJET_API_KEY, MAILJET_SECRET_KEY, MAILJET_SENDER_EMAIL


def can_send_emails():
    return bool(MAILJET_API_KEY and MAILJET_SECRET_KEY and MAILJET_SENDER_EMAIL)


def _send_email(to_email: str, subject: str, body: str):
    if not can_send_emails():
        print("[AVISO] Faltan configuraciones de Mailjet en el .env (API_KEY, SECRET_KEY o SENDER_EMAIL)")
        return False

    url = "https://api.mailjet.com/v3.1/send"
    
    payload_dict = {
        "Messages": [
            {
                "From": {
                    "Email": MAILJET_SENDER_EMAIL,
                    "Name": "Barbería"
                },
                "To": [
                    {
                        "Email": to_email,
                        "Name": "Cliente"
                    }
                ],
                "Subject": subject,
                "TextPart": body
            }
        ]
    }
    
    payload = json.dumps(payload_dict).encode("utf-8")
    auth_str = f"{MAILJET_API_KEY}:{MAILJET_SECRET_KEY}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {auth_b64}")

    try:
        with urllib.request.urlopen(req) as response:
            res_data = response.read().decode("utf-8")
            print(f"[CORREO] Enviado correctamente mediante Mailjet a {to_email}")
            return True
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        print(f"\n[ERROR DE RECHAZO MAILJET] Status {e.code}: {error_msg}")
        print("  -> Verifica que 'MAILJET_SENDER_EMAIL' sea un correo verificado en tu cuenta de Mailjet.\n")
        return False
    except Exception as e:
        print(f"\n[ERROR DE CONEXION MAILJET] Ocurrió un fallo en la red: {e}\n")
        return False


def send_reservation_confirmation(client_email: str, client_name: str, service_name: str, employee_name: str, appointment_datetime: str):
    body = (
        f"Hola {client_name},\n\n"
        "Tu reserva fue confirmada con éxito.\n"
        f"Servicio: {service_name}\n"
        f"Empleado: {employee_name}\n"
        f"Fecha y hora: {appointment_datetime}\n\n"
        "Gracias por reservar con nosotros."
    )

    return _send_email(
        to_email=client_email,
        subject="Confirmación de tu cita en la barbería",
        body=body,
    )


def send_reservation_reminder(client_email: str, client_name: str, service_name: str, employee_name: str, appointment_datetime: str):

    body = (
        f"Hola {client_name},\n\n"
        f"Te recordamos tu cita:\n"
        f"Servicio: {service_name}\n"
        f"Empleado: {employee_name}\n"
        f"Fecha y hora: {appointment_datetime}\n\n"
        "¡Te esperamos!"
    )

    return _send_email(
        to_email=client_email,
        subject="Recordatorio de tu cita en la barbería",
        body=body,
    )


def send_rating_request(client_email: str, client_name: str, employee_name: str, rating_token: str):
    from flask import request
    base_url = request.host_url.rstrip('/')
    rating_link = f"{base_url}/rate/{rating_token}"

    body = (
        f"Hola {client_name},\n\n"
        f"Esperamos que hayas disfrutado tu servicio con {employee_name}.\n"
        "Para ayudarnos a mejorar y reconocer el trabajo de nuestro equipo, "
        "te invitamos a calificar tu experiencia ingresando al siguiente enlace:\n\n"
        f"{rating_link}\n\n"
        "¡Tu opinión es muy importante para nosotros!"
    )

    return _send_email(
        to_email=client_email,
        subject="¡Califica tu experiencia en la barbería!",
        body=body,
    )
