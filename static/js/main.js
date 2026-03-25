(() => {
    const THEME_KEY = 'barberia_theme';

    // Qué hace: inicializa el botón de cambio de tema claro/oscuro.
    // Qué valida: existencia del botón y preferencia guardada/sistema.
    // Qué retorna: N/A.
    function initThemeToggle() {
        const root = document.documentElement;
        const toggle = document.getElementById('theme-toggle');

        if (!toggle) {
            return;
        }

        const applyTheme = (theme) => {
            root.setAttribute('data-theme', theme);
            toggle.textContent = theme === 'dark' ? '☀️' : '🌙';
        };

        const savedTheme = localStorage.getItem(THEME_KEY);
        const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        applyTheme(savedTheme || (systemPrefersDark ? 'dark' : 'light'));

        toggle.addEventListener('click', (e) => {
            const current = root.getAttribute('data-theme') || 'light';
            const next = current === 'dark' ? 'light' : 'dark';

            if (!document.startViewTransition) {
                applyTheme(next);
                localStorage.setItem(THEME_KEY, next);
                return;
            }

            const x = e.clientX || innerWidth / 2;
            const y = e.clientY || innerHeight / 2;
            const endRadius = Math.hypot(
                Math.max(x, innerWidth - x),
                Math.max(y, innerHeight - y)
            );

            const transition = document.startViewTransition(() => {
                applyTheme(next);
                localStorage.setItem(THEME_KEY, next);
            });

            transition.ready.then(() => {
                const clipPath = [
                    `circle(0px at ${x}px ${y}px)`,
                    `circle(${endRadius}px at ${x}px ${y}px)`
                ];

                document.documentElement.animate(
                    {
                        clipPath: next === 'dark' ? clipPath : [...clipPath].reverse(),
                    },
                    {
                        duration: 500,
                        easing: 'ease-in-out',
                        pseudoElement: next === 'dark' ? '::view-transition-new(root)' : '::view-transition-old(root)',
                    }
                );
            });
        });
    }

    // Qué hace: consulta y actualiza los horarios disponibles de reserva.
    // Qué valida: selección de servicio, empleado y fecha, además de respuesta API.
    // Qué retorna: N/A.
    function initAvailability() {
        const serviceInput = document.getElementById('service_name');
        const employeeInput = document.getElementById('employee_id');
        const dateInput = document.getElementById('date');
        const timeSelect = document.getElementById('time');

        if (!serviceInput || !employeeInput || !dateInput || !timeSelect) {
            return;
        }

        const refreshAvailability = async () => {
            const serviceName = serviceInput.value;
            const employeeId = employeeInput.value;
            const date = dateInput.value;

            if (!serviceName || !employeeId || !date) {
                timeSelect.innerHTML = '<option value="">Primero selecciona servicio, empleado y fecha</option>';
                return;
            }

            timeSelect.innerHTML = '<option value="">Cargando horarios...</option>';
            const timeLabel = timeSelect.closest('label');
            if (timeLabel) timeLabel.classList.add('select-loading');

            try {
                const query = new URLSearchParams({
                    service_name: serviceName,
                    employee_id: employeeId,
                    date,
                });
                const response = await fetch(`/api/availability?${query.toString()}`);
                const data = await response.json();

                if (!response.ok) {
                    timeSelect.innerHTML = `<option value="">${data.error || 'Error al cargar horarios'}</option>`;
                    return;
                }

                if (!data.slots.length) {
                    timeSelect.innerHTML = '<option value="">No hay horarios disponibles</option>';
                    return;
                }

                timeSelect.innerHTML = '<option value="">Selecciona horario...</option>';
                data.slots.forEach((slot) => {
                    const option = document.createElement('option');
                    option.value = slot;
                    option.textContent = slot;
                    timeSelect.appendChild(option);
                });
            } catch {
                timeSelect.innerHTML = '<option value="">No se pudo actualizar</option>';
            } finally {
                const timeLabel = timeSelect.closest('label');
                if (timeLabel) timeLabel.classList.remove('select-loading');
            }
        };

        serviceInput.addEventListener('change', refreshAvailability);
        employeeInput.addEventListener('change', refreshAvailability);
        dateInput.addEventListener('change', refreshAvailability);
        setInterval(refreshAvailability, 20000);
    }

    // Qué hace: inicializa Flatpickr para un selector de fechas de reserva avanzado.
    // Qué valida: domingos deshabilitados, fechas pasadas omitidas.
    // Qué retorna: N/A.
    function initBookingDateLimits() {
        const dateInput = document.getElementById('date');
        if (!dateInput) return;

        const now = new Date();
        const maxDate = new Date(now.getFullYear(), 11, 31);

        if (typeof flatpickr !== 'undefined') {
            flatpickr(dateInput, {
                locale: "es",
                minDate: "today",
                maxDate: maxDate,
                disable: [
                    function(date) { return date.getDay() === 0; }
                ],
                onChange: function(selectedDates, dateStr, instance) {
                    dateInput.dispatchEvent(new Event('change'));
                }
            });
        }
    }

    // Qué hace: inicializa Flatpickr para selección de fecha y hora del pago móvil.
    // Qué valida: límites configurados en los inputs de HTML.
    // Qué retorna: N/A.
    function initPaymentDatetimeLimits() {
        const dateInput = document.getElementById('payment_date');
        const timeInput = document.getElementById('payment_time');
        
        if (!dateInput && !timeInput) return;

        if (typeof flatpickr !== 'undefined') {
            if (dateInput) {
                const minRaw = dateInput.getAttribute('min');
                const maxRaw = dateInput.getAttribute('max');
                flatpickr(dateInput, {
                    locale: "es",
                    minDate: minRaw || null,
                    maxDate: maxRaw || null,
                    defaultDate: maxRaw || "today"
                });
            }
            if (timeInput) {
                flatpickr(timeInput, {
                    enableTime: true,
                    noCalendar: true,
                    dateFormat: "H:i",
                    time_24hr: true,
                    locale: "es",
                    defaultDate: new Date()
                });
            }
        }
    }

    // Qué hace: inicializa FullCalendar en el panel admin y carga eventos.
    // Qué valida: existencia del contenedor y disponibilidad de la librería FullCalendar.
    // Qué retorna: N/A.
    function initAdminCalendar() {
        const calendarElement = document.getElementById('admin-calendar');

        if (!calendarElement || typeof FullCalendar === 'undefined') {
            return;
        }

        const eventsUrl = calendarElement.dataset.eventsUrl;

        const calendar = new FullCalendar.Calendar(calendarElement, {
            initialView: 'dayGridMonth',
            locale: 'es',
            height: 'auto',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,timeGridWeek,timeGridDay',
            },
            buttonText: {
                today: 'Hoy',
                month: 'Mes',
                week: 'Semana',
                day: 'Día',
            },
            events: async (_, successCallback, failureCallback) => {
                try {
                    const response = await fetch(eventsUrl);
                    if (!response.ok) {
                        failureCallback(new Error('No se pudieron cargar las citas'));
                        return;
                    }
                    successCallback(await response.json());
                } catch (error) {
                    failureCallback(error);
                }
            },
            eventTimeFormat: {
                hour: '2-digit',
                minute: '2-digit',
                meridiem: false,
            },
            eventClick: ({ event }) => {
                const { client_name, client_email, status } = event.extendedProps;
                alert(`Cliente: ${client_name}\nEmail: ${client_email}\nEstado: ${status}`);
            },
        });

        calendar.render();
    }

    // Qué hace: renderiza el gráfico de barras de citas por día en admin.
    // Qué valida: existencia de canvas, datos embebidos y librería Chart.
    // Qué retorna: N/A.
    function initAdminMetricsChart() {
        const chartCanvas = document.getElementById('appointmentsByDayChart');
        const dataScript = document.getElementById('appointments-by-day-data');

        if (!chartCanvas || !dataScript || typeof Chart === 'undefined') {
            return;
        }

        let points = [];
        try {
            points = JSON.parse(dataScript.textContent || '[]');
        } catch {
            points = [];
        }

        const labels = points.map((item) => item.date);
        const values = points.map((item) => item.count);
        const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';

        // Override default text color for all charts
        Chart.defaults.color = isDarkMode ? '#f8fafc' : '#475569';

        new Chart(chartCanvas, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Citas',
                        data: values,
                        backgroundColor: isDarkMode ? 'rgba(96, 165, 250, 0.75)' : 'rgba(37, 99, 235, 0.75)',
                        borderColor: isDarkMode ? 'rgba(147, 197, 253, 1)' : 'rgba(29, 78, 216, 1)',
                        borderWidth: 1,
                        borderRadius: 6,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                },
                scales: {
                    x: {
                        ticks: { color: isDarkMode ? '#f1f5f9' : '#475569' }
                    },
                    y: {
                        beginAtZero: true,
                        ticks: { precision: 0, color: isDarkMode ? '#f1f5f9' : '#475569' },
                    },
                },
            },
        });
    }

    // Qué hace: renderiza el gráfico de estados de citas (doughnut) en admin.
    // Qué valida: existencia de canvas, datos embebidos y librería Chart.
    // Qué retorna: N/A.
    function initAdminStatusChart() {
        const chartCanvas = document.getElementById('appointmentsStatusChart');
        const dataScript = document.getElementById('appointments-status-data');

        if (!chartCanvas || !dataScript || typeof Chart === 'undefined') {
            return;
        }

        let status = { pending_payment: 0, scheduled: 0, completed: 0, canceled: 0 };
        try {
            status = JSON.parse(dataScript.textContent || '{}');
        } catch {
            status = { pending_payment: 0, scheduled: 0, completed: 0, canceled: 0 };
        }

        const isDarkMode = document.documentElement.getAttribute('data-theme') === 'dark';

        new Chart(chartCanvas, {
            type: 'doughnut',
            data: {
                labels: ['Pendiente pago', 'Agendadas', 'Atendidas', 'Canceladas'],
                datasets: [
                    {
                        data: [status.pending_payment || 0, status.scheduled || 0, status.completed || 0, status.canceled || 0],
                        backgroundColor: ['#7c3aed', '#2563eb', '#059669', '#dc2626'],
                        borderColor: isDarkMode ? '#0f172a' : '#ffffff',
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            color: isDarkMode ? '#f1f5f9' : '#334155',
                        },
                    },
                },
            },
        });
    }

    // Qué hace: inicia la cuenta regresiva para vencimiento del pago.
    // Qué valida: elementos del temporizador, segundos restantes y URL de redirección al expirar.
    // Qué retorna: N/A.
    function initPaymentCountdown() {
        const timerElement = document.getElementById('payment-timer');
        const countdownElement = document.getElementById('payment-countdown');
        const paymentForm = document.getElementById('payment-proof-form');
        const submitButton = document.getElementById('payment-submit-button');
        const expiredRedirectUrl = timerElement?.dataset.expiredRedirectUrl;

        if (!timerElement || !countdownElement) {
            return;
        }

        let remainingSeconds = Number(timerElement.dataset.remainingSeconds || '0');

        const disablePaymentForm = () => {
            if (!paymentForm) {
                return;
            }

            const fields = paymentForm.querySelectorAll('input, button');
            fields.forEach((field) => {
                field.disabled = true;
            });

            if (submitButton) {
                submitButton.textContent = 'Tiempo expirado';
            }
        };

        const formatSeconds = (seconds) => {
            const mins = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
        };

        const render = () => {
            countdownElement.textContent = formatSeconds(Math.max(remainingSeconds, 0));
        };

        render();

        if (remainingSeconds <= 0) {
            disablePaymentForm();
            if (expiredRedirectUrl) {
                setTimeout(() => {
                    window.location.href = expiredRedirectUrl;
                }, 800);
            }
            return;
        }

        const intervalId = setInterval(() => {
            remainingSeconds -= 1;
            render();

            if (remainingSeconds <= 0) {
                clearInterval(intervalId);
                disablePaymentForm();
                if (expiredRedirectUrl) {
                    setTimeout(() => {
                        window.location.href = expiredRedirectUrl;
                    }, 800);
                }
            }
        }, 1000);
    }

    // Qué hace: previene doble envío en todos los formularios y da feedback visual.
    function initDoubleSubmitPrevention() {
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('submit', (e) => {
                if (form.dataset.submitting) {
                    e.preventDefault();
                    return;
                }
                form.dataset.submitting = 'true';
                const btn = form.querySelector('button[type="submit"]');
                if (btn) {
                    btn.dataset.originalHtml = btn.innerHTML;
                    btn.innerHTML = '<span class="select-loading" style="display:inline-block; width:16px; height:16px; margin-right:8px; vertical-align:middle; position:relative; top:-2px; right: 0;"></span> Procesando...';
                    btn.style.pointerEvents = 'none';
                    btn.style.opacity = '0.8';
                }
                
                // Allow re-submit after 5 seconds just in case navigation fails
                setTimeout(() => {
                    delete form.dataset.submitting;
                    if (btn && btn.dataset.originalHtml) {
                        btn.innerHTML = btn.dataset.originalHtml;
                        btn.style.pointerEvents = 'auto';
                        btn.style.opacity = '1';
                    }
                }, 5000);
            });
        });
    }

    // Helper for clipboard
    window.copyToClipboard = async function(btn, text) {
        try {
            await navigator.clipboard.writeText(text);
            const original = btn.textContent;
            btn.textContent = '✅';
            setTimeout(() => btn.textContent = original, 1500);
        } catch (err) {
            console.error('Failed to copy', err);
        }
    };

    initThemeToggle();
    initBookingDateLimits();
    initPaymentDatetimeLimits();
    initAvailability();
    initAdminCalendar();
    initAdminMetricsChart();
    initAdminStatusChart();
    initPaymentCountdown();
    initDoubleSubmitPrevention();
})();
