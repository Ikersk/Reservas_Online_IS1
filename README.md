# 💈 Sistema de Reservas y Gestión Operativa

> Una aplicación web integral diseñada para optimizar la gestión de citas, control de pagos manuales y la administración general de una barbería o negocio de servicios.

Este proyecto se centra en ofrecer una experiencia de usuario fluida para los clientes y un panel de control robusto para los administradores, asegurando la integridad de los datos y un flujo operativo eficiente.

---

## ✨ Funcionalidades Principales

### 👤 Para el Cliente
* **Reservas Inteligentes:** Agendamiento de citas por servicio, profesional, fecha y hora.
* **Disponibilidad en Tiempo Real:** Validación instantánea de horarios ocupados para evitar choques.
* **Pasarela de Pago Manual:** Flujo de carga de comprobantes con asignación de estado `pending_payment`.
* **Notificaciones Automatizadas:** Envío de correos electrónicos para confirmación y recordatorios de citas.
* **Interfaz Adaptable:** Soporte nativo para tema Claro/Oscuro.

### 🛡️ Para la Administración (Panel Admin)
* **Gestión de Personal:** Alta, baja y modificación de empleados.
* **Catálogo de Servicios:** Administración de nombres, duraciones y precios.
* **Control Financiero:** Aprobación o rechazo de pagos reportados manualmente.
* **Gestión de Citas:** Actualización manual de estados de reservaciones.
* **Métricas y Visualización:** Calendario interactivo y gráficos de rendimiento.
* **Sistema de Calificación:** Emisión de tokens únicos para evaluar el servicio de los empleados con rankings mensuales y vista pública de "Empleado del Mes".
* **Tasa de Cambio Dinámica:** Consulta en tiempo real a la API oficial (`ve.dolarapi.com`) con memoria caché local para cálculo de precios.
* **Envío de Correos Cifrado:** Integración nativa con la API v3.1 de **Mailjet**, abandonando servicios inseguros en texto plano.

---

## 🛠️ Stack Tecnológico

**Backend & Lógica**
* **Framework:** Python + Flask (v3.1.0)
* **Base de Datos & ORM:** SQLite + SQLAlchemy (v2.0.38)
* **Tareas en Segundo Plano:** APScheduler (v3.10.4)
* **Variables de Entorno:** python-dotenv (v1.0.1)
* **Seguridad y Cifrado:** Flask-Limiter (v3.8.0) para endpoints y `werkzeug.security` (Hashes PBKDF2) para contraseñas de configuración.

**Frontend & UI**
* **Motor de Plantillas:** Jinja2
* **Estructura y Estilos:** HTML5, CSS3, JavaScript (Vanilla)
* **Librerías de Visualización:** FullCalendar (Agendas) + Chart.js (Métricas)

---

## 📂 Arquitectura del Proyecto

```text
📦 Proyecto_Ing_Soft
 ┣ 📂 static/               # Recursos estáticos
 ┃ ┣ 📂 css/styles.css      # Hojas de estilo globales
 ┃ ┗ 📂 js/main.js          # Lógica de interfaz en el cliente
 ┣ 📂 templates/            # Vistas renderizadas por Jinja2
 ┃ ┣ 📜 admin_ingreso.html  # Login de seguridad del administrador
 ┃ ┣ 📜 admin_panel.html    # Dashboard de métricas, ranking y control
 ┃ ┣ 📜 base.html           # Layout principal y navegación
 ┃ ┣ 📜 calificar.html      # Interfaz de calificación (5 Estrellas) para el cliente
 ┃ ┣ 📜 inicio.html         # Portal público de reservas y tarjetas de empleados
 ┃ ┗ 📜 pagina_pago.html    # Interfaz de carga de comprobantes
 ┣ 📜 .env.example          # Plantilla segura de variables de entorno
 ┣ 📜 app.py                # Enrutamiento principal, validaciones y núcleo de Auth
 ┣ 📜 base_datos.py         # Modelos SQLAlchemy y queries
 ┣ 📜 changelog_17-mar_said.md # Registro detallado de parches y versiones
 ┣ 📜 configuracion.py      # Adaptador de credenciales, límites y entorno
 ┣ 📜 generate_hash.py      # Utilidad criptográfica generar claves (PBKDF2)
 ┣ 📜 requirements.txt      # Dependencias y versiones del Proyecto
 ┣ 📜 run_windows.bat       # Launcher rápido para Windows
 ┣ 📜 servicio_correo.py    # Integración con API Mailjet V3.1 para Notificaciones
 ┣ 📜 servicio_recordatorios.py # Tareas en cola automáticas (APScheduler)
 ┗ 📜 setup_windows.bat     # Creador automático del entorno virtual para Windows
```