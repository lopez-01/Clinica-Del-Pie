from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import connection, transaction
from django.db.models import Count, Max, Prefetch, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import render, redirect, get_object_or_404
from .models import (
    CitaServicio,
    Servicio,
    Cliente,
    Cita,
    Operativo,
    Personal,
    Administrativo,
    Estado,
    Propuestas_Citas,
    ServicioOperativo,
    Factura,
    PagoFactura,
    Comision,
    ServicioFactura,
    EstadoPago,
    MetodoPago,
    GastoMensual,
    HistorialAccion,
    ExpedienteClinico,
)
from .forms import (
    ClienteForm, ServicioSelectForm, OperativoSelectForm, FechaHoraForm,
    SeguimientoCitaForm, AtencionForm, PersonalForm, PersonalEdicionForm,
    SuspensionCuentaForm, PagoFacturaForm, ComisionEstadoForm,
)
from .booking_rules import (
    HORA_APERTURA_CLINICA,
    HORA_CIERRE_CLINICA,
    cita_en_horario_laboral,
    fecha_en_rango_agenda,
    fecha_laboral,
    obtener_rango_agenda,
)
from django.urls import reverse
from datetime import date, datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from .auth_backends import login_attempts_blocked
import csv
import json
import re

MAX_SOLICITUDES_WEB_POR_DIA = 5
PATRON_NOMBRE = re.compile(
    r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:[ '-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)*$"
)


def url_retorno_segura(request, candidata, nombre_respaldo, **kwargs):
    """Devuelve el destino interno anterior o una ruta segura de respaldo."""
    if (
        candidata
        and candidata != request.get_full_path()
        and url_has_allowed_host_and_scheme(
            url=candidata,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidata
    return reverse(nombre_respaldo, kwargs=kwargs or None)


def normalizar_nombre_persona(valor):
    return " ".join(valor.strip().split()).title()


def nombre_persona_valido(valor):
    texto = normalizar_nombre_persona(valor)
    letras = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", texto).lower()

    if len(letras) < 2:
        return False

    if len(set(letras)) == 1:
        return False

    return bool(PATRON_NOMBRE.match(texto))


def obtener_servicios_validos(ids):
    if not isinstance(ids, (list, tuple)) or not ids:
        return None

    try:
        ids_normalizados = [int(servicio_id) for servicio_id in ids]
    except (TypeError, ValueError):
        return None

    if any(servicio_id <= 0 for servicio_id in ids_normalizados):
        return None
    if len(set(ids_normalizados)) != len(ids_normalizados):
        return None

    servicios = list(Servicio.objects.filter(id_servicio__in=ids_normalizados))
    if len(servicios) != len(ids_normalizados):
        return None
    return servicios


def duracion_servicios(servicios):
    return sum(max(servicio.duracion or 0, 0) for servicio in servicios) or 30


def parsear_inicio_cita(fecha, hora):
    try:
        fecha_valida = datetime.strptime(str(fecha), "%Y-%m-%d").date()
        hora_valida = datetime.strptime(str(hora), "%H:%M").time()
    except (TypeError, ValueError):
        return None
    return timezone.make_aware(datetime.combine(fecha_valida, hora_valida))


def validar_programacion_cita(operativo, inicio, duracion, excluir=None):
    if not inicio:
        return "La fecha o la hora seleccionada no es válida."

    inicio_local = timezone.localtime(inicio)
    fecha = inicio_local.date()

    if inicio_local < timezone.localtime():
        return "No se pueden crear citas en fechas u horas pasadas."
    if not fecha_en_rango_agenda(fecha):
        return "Solo se pueden agendar citas dentro del próximo año."
    if not fecha_laboral(fecha):
        return "La clínica no atiende los lunes."
    if not cita_en_horario_laboral(inicio, duracion):
        return "La cita debe iniciar después de la apertura y terminar antes del cierre."
    if hay_conflicto_intervalo(operativo, fecha, inicio, duracion, excluir=excluir):
        return "La duración de la cita se solapa con otra."
    return None


def duracion_para_sesion(request):
    servicios = obtener_servicios_validos(
        request.session.get('servicios_seleccionados')
    )
    if not servicios:
        return None

    try:
        duracion_sesion = int(request.session.get('duracion_final') or duracion_servicios(servicios))
    except (TypeError, ValueError):
        duracion_sesion = duracion_servicios(servicios)
    return duracion_sesion


def actualizar_citas_vencidas(operativo=None):
    try:
        estado_pendiente = Estado.objects.get(nombre_estado='Pendiente')
    except Estado.DoesNotExist:
        return 0

    citas = Cita.objects.filter(
        fechahora__lt=timezone.now(),
        estado__nombre_estado='Programada'
    )

    if operativo:
        citas = citas.filter(operativo=operativo)

    return citas.update(estado=estado_pendiente)


def limpiar_contexto_seguimiento(request):
    for clave in (
        'seguimiento_cita_id',
        'cliente_id',
        'servicios_seleccionados',
        'duracion_sugerida',
        'duracion_final',
    ):
        request.session.pop(clave, None)
    if request.session.get('flujo_origen') == 'seguimiento':
        request.session.pop('flujo_origen', None)
    request.session.modified = True


MESES = [
    (1, "Enero"),
    (2, "Febrero"),
    (3, "Marzo"),
    (4, "Abril"),
    (5, "Mayo"),
    (6, "Junio"),
    (7, "Julio"),
    (8, "Agosto"),
    (9, "Septiembre"),
    (10, "Octubre"),
    (11, "Noviembre"),
    (12, "Diciembre"),
]


def asegurar_tabla_gasto_mensual():
    with connection.cursor() as cursor:
        cursor.execute("""
IF OBJECT_ID(N'[dbo].[GastoMensual]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[GastoMensual] (
        [ID_Gasto] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [Mes] INT NOT NULL,
        [Anio] INT NOT NULL,
        [Luz] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Agua] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Internet] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Materiales] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Salarios] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Otros] DECIMAL(10,2) NOT NULL DEFAULT 0,
        [Fecha_Registro] DATETIME2 NULL,
        [Cerrado] BIT NOT NULL DEFAULT 0,
        [ID_Administrativo] INT NULL,
        CONSTRAINT [UQ_GastoMensual_Mes_Anio] UNIQUE ([Mes], [Anio])
    )
END
""")
        cursor.execute("""
IF COL_LENGTH('dbo.GastoMensual', 'Cerrado') IS NULL
BEGIN
    ALTER TABLE [dbo].[GastoMensual]
    ADD [Cerrado] BIT NOT NULL DEFAULT 0
END
""")
        cursor.execute("""
IF COL_LENGTH('dbo.GastoMensual', 'Ingresos_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Ingresos_Cierre] DECIMAL(12,2) NULL;
IF COL_LENGTH('dbo.GastoMensual', 'Salarios_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Salarios_Cierre] DECIMAL(12,2) NULL;
IF COL_LENGTH('dbo.GastoMensual', 'Facturas_Pagadas_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Facturas_Pagadas_Cierre] INT NULL;
IF COL_LENGTH('dbo.GastoMensual', 'Facturas_Pendientes_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Facturas_Pendientes_Cierre] INT NULL;
IF COL_LENGTH('dbo.GastoMensual', 'Detalle_Comisiones_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Detalle_Comisiones_Cierre] NVARCHAR(MAX) NULL;
IF COL_LENGTH('dbo.GastoMensual', 'Ingresos_Por_Metodo_Cierre') IS NULL
    ALTER TABLE [dbo].[GastoMensual] ADD [Ingresos_Por_Metodo_Cierre] NVARCHAR(MAX) NULL;
""")


def asegurar_tabla_historial_accion():
    with connection.cursor() as cursor:
        cursor.execute("""
IF OBJECT_ID(N'[dbo].[HistorialAccion]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[HistorialAccion] (
        [ID_Accion] INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [Usuario] NVARCHAR(150) NOT NULL,
        [Accion] NVARCHAR(100) NOT NULL,
        [Detalle] NVARCHAR(500) NULL,
        [Fecha] DATETIME2 NOT NULL,
        [ID_Cita] INT NULL,
        [ID_Factura] INT NULL,
        [Mes] INT NULL,
        [Anio] INT NULL
    )
END
""")


def asegurar_tabla_pago_factura():
    """Crea de forma aditiva la tabla que conserva cada pago de una factura."""
    with connection.cursor() as cursor:
        cursor.execute("""
IF OBJECT_ID(N'[dbo].[PagoFactura]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[PagoFactura] (
        [ID_PagoFactura] INT IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_PagoFactura] PRIMARY KEY,
        [ID_Factura] INT NOT NULL,
        [Fecha_Pago] DATETIME2(0) NOT NULL,
        [Monto] DECIMAL(10,2) NOT NULL,
        [ID_MetodoPago] INT NOT NULL,
        CONSTRAINT [CK_PagoFactura_Monto] CHECK ([Monto] > 0),
        CONSTRAINT [FK_PagoFactura_Factura]
            FOREIGN KEY ([ID_Factura]) REFERENCES [dbo].[Factura]([ID_Factura]),
        CONSTRAINT [FK_PagoFactura_MetodoPago]
            FOREIGN KEY ([ID_MetodoPago]) REFERENCES [dbo].[MetodoPago]([ID_MetodoPago])
    );
    CREATE INDEX [IX_PagoFactura_Factura_Fecha]
        ON [dbo].[PagoFactura] ([ID_Factura], [Fecha_Pago]);
END
""")


def asegurar_tabla_comision():
    """Crea de forma aditiva los registros mensuales de comisiones."""
    with connection.cursor() as cursor:
        cursor.execute("""
IF OBJECT_ID(N'[dbo].[Comision]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[Comision] (
        [ID_Comision] INT IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_Comision] PRIMARY KEY,
        [Periodo] NVARCHAR(7) NOT NULL,
        [Monto] DECIMAL(10,2) NOT NULL,
        [Estado] NVARCHAR(20) NOT NULL
            CONSTRAINT [DF_Comision_Estado] DEFAULT N'Calculada',
        [ID_Personal] INT NOT NULL,
        CONSTRAINT [CK_Comision_Monto] CHECK ([Monto] >= 0),
        CONSTRAINT [CK_Comision_Estado]
            CHECK ([Estado] IN (N'Calculada', N'Pendiente', N'Pagada')),
        CONSTRAINT [UQ_Comision_Periodo_Personal]
            UNIQUE ([Periodo], [ID_Personal]),
        CONSTRAINT [FK_Comision_Personal]
            FOREIGN KEY ([ID_Personal]) REFERENCES [dbo].[Personal]([ID_Personal])
    );
END
""")


def asegurar_tablas_administrativas():
    asegurar_tabla_gasto_mensual()
    asegurar_tabla_historial_accion()


def rango_mes(anio, mes):
    inicio = timezone.make_aware(datetime(anio, mes, 1))

    if mes == 12:
        fin = timezone.make_aware(datetime(anio + 1, 1, 1))
    else:
        fin = timezone.make_aware(datetime(anio, mes + 1, 1))

    return inicio, fin


def ingresos_facturados_pagados(anio, mes):
    asegurar_tabla_pago_factura()
    inicio, fin = rango_mes(anio, mes)
    pagos_registrados = PagoFactura.objects.filter(
        fecha_pago__gte=inicio,
        fecha_pago__lt=fin
    ).aggregate(total=Sum('monto'))['total'] or Decimal('0.00')

    # Las facturas pagadas antes de habilitar el detalle de pagos no tienen
    # filas en PagoFactura; se mantienen en el reporte histórico por emisión.
    facturas_legacy = Factura.objects.filter(
        fecha_factura__gte=inicio,
        fecha_factura__lt=fin,
        estado_pago__nombre='Pagada'
    ).filter(pagos__isnull=True).aggregate(
        total=Sum('total')
    )['total'] or Decimal('0.00')
    return pagos_registrados + facturas_legacy


def ingresos_por_metodo_pago(anio, mes):
    """Agrupa los cobros del período por método, incluyendo facturas históricas."""
    asegurar_tabla_pago_factura()
    inicio, fin = rango_mes(anio, mes)
    resumen = {}

    pagos = PagoFactura.objects.filter(
        fecha_pago__gte=inicio,
        fecha_pago__lt=fin,
    ).values('metodo_pago__nombre').annotate(
        total=Sum('monto'),
        operaciones=Count('id_pago'),
    )
    for pago in pagos:
        nombre = pago['metodo_pago__nombre'] or 'Sin método registrado'
        resumen[nombre] = {
            'metodo': nombre,
            'total': pago['total'] or Decimal('0.00'),
            'operaciones': pago['operaciones'],
        }

    facturas_legacy = Factura.objects.filter(
        fecha_factura__gte=inicio,
        fecha_factura__lt=fin,
        estado_pago__nombre='Pagada',
        pagos__isnull=True,
    ).values('metodo_pago__nombre').annotate(
        total=Sum('total'),
        operaciones=Count('id_factura'),
    )
    for factura in facturas_legacy:
        nombre = factura['metodo_pago__nombre'] or 'Sin método registrado'
        item = resumen.setdefault(nombre, {
            'metodo': nombre,
            'total': Decimal('0.00'),
            'operaciones': 0,
        })
        item['total'] += factura['total'] or Decimal('0.00')
        item['operaciones'] += factura['operaciones']

    return sorted(resumen.values(), key=lambda item: item['total'], reverse=True)


def facturas_cobradas_ids_mes(anio, mes):
    """Devuelve facturas liquidadas durante el mes según la fecha de cobro."""
    asegurar_tabla_pago_factura()
    inicio, fin = rango_mes(anio, mes)
    ids_con_pago = set(PagoFactura.objects.filter(
        fecha_pago__gte=inicio,
        fecha_pago__lt=fin,
        factura__estado_pago__nombre='Pagada'
    ).values_list('factura_id', flat=True).distinct())

    # Compatibilidad con facturas pagadas antes del registro detallado de pagos.
    ids_legacy = Factura.objects.filter(
        fecha_factura__gte=inicio,
        fecha_factura__lt=fin,
        estado_pago__nombre='Pagada',
        pagos__isnull=True
    ).values_list('id_factura', flat=True)
    ids_con_pago.update(ids_legacy)
    return ids_con_pago


def conteo_facturas_periodo(anio, mes):
    inicio, fin = rango_mes(anio, mes)
    pagadas = len(facturas_cobradas_ids_mes(anio, mes))
    pendientes = Factura.objects.filter(
        fecha_factura__gte=inicio,
        fecha_factura__lt=fin,
        estado_pago__nombre='Pendiente'
    ).count()
    return pagadas, pendientes


def total_gasto_mensual(gasto, salarios_operativos=Decimal('0.00')):
    return sum(
        (
            gasto.luz or Decimal('0.00'),
            gasto.agua or Decimal('0.00'),
            gasto.internet or Decimal('0.00'),
            gasto.materiales or Decimal('0.00'),
            gasto.otros or Decimal('0.00'),
            salarios_operativos or Decimal('0.00'),
        ),
        Decimal('0.00')
    )


def comisiones_operativos_mes(anio, mes):
    comisiones = {}
    ids_facturas_cobradas = facturas_cobradas_ids_mes(anio, mes)

    servicios_facturados = ServicioFactura.objects.filter(
        factura_id__in=ids_facturas_cobradas
    ).select_related(
        'servicio',
        'factura',
        'factura__cita',
        'factura__cita__operativo',
        'factura__cita__operativo__personal'
    )

    for servicio_factura in servicios_facturados:
        factura = servicio_factura.factura
        cita = factura.cita if factura else None
        operativo = cita.operativo if cita else None
        comision = servicio_factura.servicio.comision_operativo or Decimal('0.00')

        if operativo and operativo.personal:
            key = operativo.personal.id_personal
            nombre_operativo = f"{operativo.personal.nombre} {operativo.personal.apellido}"
        else:
            key = 0
            nombre_operativo = "Sin operativo"

        item = comisiones.setdefault(key, {
            'operativo': nombre_operativo,
            'personal_id': operativo.personal.id_personal if operativo and operativo.personal else None,
            'servicios': 0,
            'total_comision': Decimal('0.00'),
        })

        item['servicios'] += 1
        item['total_comision'] += comision

    detalle = sorted(
        comisiones.values(),
        key=lambda item: item['total_comision'],
        reverse=True
    )
    total = sum(
        (item['total_comision'] for item in detalle),
        Decimal('0.00')
    )

    return detalle, total


def decimal_post(request, campo):
    valor = request.POST.get(campo, '0').strip().replace(',', '.')

    try:
        monto = Decimal(valor or '0')
    except InvalidOperation as exc:
        raise ValueError("Monto invalido.") from exc

    if monto < 0:
        raise ValueError("Los gastos no pueden ser negativos.")

    return monto


def limpiar_motivo(valor):
    motivo = " ".join((valor or "").strip().split())

    if len(motivo) < 5:
        raise ValueError("Debe escribir un motivo de al menos 5 caracteres.")

    if len(motivo) > 250:
        raise ValueError("El motivo no puede superar 250 caracteres.")

    return motivo


def nombre_usuario_accion(request):
    if not request or not request.user.is_authenticated:
        return "Sistema"

    try:
        personal = Personal.objects.get(user=request.user)
        return f"{personal.nombre} {personal.apellido}".strip()
    except Personal.DoesNotExist:
        return request.user.get_username()


def registrar_accion(
    request,
    accion,
    detalle="",
    cita=None,
    factura=None,
    mes=None,
    anio=None
):
    asegurar_tabla_historial_accion()

    HistorialAccion.objects.create(
        usuario=nombre_usuario_accion(request),
        accion=accion,
        detalle=detalle[:500] if detalle else "",
        fecha=timezone.now(),
        id_cita=cita.id_cita if cita else None,
        id_factura=factura.id_factura if factura else None,
        mes=mes,
        anio=anio
    )


def registrar_cita_creada(request, cita):
    if not cita:
        return

    cliente = f"{cita.cliente.nombre} {cita.cliente.apellido}".strip()
    operativo = (
        f"{cita.operativo.personal.nombre} {cita.operativo.personal.apellido}".strip()
        if cita.operativo and cita.operativo.personal
        else "Sin operativo"
    )
    fechahora = timezone.localtime(cita.fechahora)

    registrar_accion(
        request,
        "Crear cita",
        (
            f"Cita para {cliente} asignada a {operativo} "
            f"el {fechahora.strftime('%d/%m/%Y')} a las {fechahora.strftime('%I:%M %p')}."
        ),
        cita=cita
    )


def notificar_citas_nuevas_operativo(request, operativo, desde=None):
    if not operativo:
        return

    asegurar_tabla_historial_accion()

    ahora = timezone.now()
    fecha_inicio = desde or (ahora - timedelta(days=7))
    nombre_operativo = f"{operativo.personal.nombre} {operativo.personal.apellido}".strip()

    citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__gte=ahora
    ).select_related(
        'cliente',
        'operativo',
        'operativo__personal'
    )
    citas_por_id = {cita.id_cita: cita for cita in citas}

    if not citas_por_id:
        return

    acciones = HistorialAccion.objects.filter(
        accion="Crear cita",
        id_cita__in=citas_por_id.keys(),
        fecha__gt=fecha_inicio
    ).exclude(
        usuario__iexact=nombre_operativo
    ).order_by('-fecha')[:5]

    total = 0
    for accion in acciones:
        cita = citas_por_id.get(accion.id_cita)
        if not cita:
            continue

        total += 1
        cliente = f"{cita.cliente.nombre} {cita.cliente.apellido}".strip()
        fechahora = timezone.localtime(cita.fechahora)

        messages.info(
            request,
            (
                f"{accion.usuario} le hizo una cita a {cliente} "
                f"para el {fechahora.strftime('%d/%m/%Y')} "
                f"a las {fechahora.strftime('%I:%M %p')}."
            )
        )

    if total == 5:
        messages.info(
            request,
            "Tienes mas citas nuevas asignadas. Revisa tu agenda para verlas todas."
        )


def cita_esta_cancelada(cita):
    return (
        cita
        and cita.estado
        and cita.estado.nombre_estado == "Cancelada"
    )


def obtener_operativo_usuario(request):
    if not request.user.is_authenticated:
        return None

    try:
        personal = Personal.objects.get(user=request.user)
        return Operativo.objects.select_related('personal').get(personal=personal)
    except (Personal.DoesNotExist, Operativo.DoesNotExist):
        return None


def obtener_usuarios_login():
    usuarios = []
    personales = Personal.objects.select_related('user').filter(
        user__isnull=False
    ).order_by('nombre', 'apellido')

    for personal in personales:
        if Operativo.objects.filter(personal=personal).exists():
            rol = "Operativo"
        elif Administrativo.objects.filter(personal=personal).exists():
            rol = "Administrativo"
        else:
            rol = "Usuario"

        usuarios.append({
            'username': personal.user.username,
            'nombre': f"{personal.nombre} {personal.apellido}",
            'rol': rol,
        })

    return usuarios


def login_view(request):
    if request.user.is_authenticated:
        try:
            personal = Personal.objects.get(user=request.user)
            if Administrativo.objects.filter(personal=personal).exists():
                return redirect('administrativo')
            elif Operativo.objects.filter(personal=personal).exists():
                return redirect('operativo')
        except Personal.DoesNotExist:
            pass
        return redirect('home')
    
    error = None
    selected_username = ""

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        selected_username = username
        password = request.POST.get('password') or ''

        if login_attempts_blocked(request, username):
            error = 'Demasiados intentos. Espera unos minutos antes de volver a intentarlo.'
            return render(request, 'login.html', {
                'error': error,
                'selected_username': selected_username,
            })

        user = authenticate(request, username=username, password=password)

        if user is not None:
            ultimo_login = user.last_login
            login(request, user)

            try:
                personal = Personal.objects.get(user=user)

                if Administrativo.objects.filter(personal=personal).exists():
                    return redirect('administrativo')
                elif Operativo.objects.filter(personal=personal).exists():
                    operativo = Operativo.objects.get(personal=personal)
                    notificar_citas_nuevas_operativo(
                        request,
                        operativo,
                        desde=ultimo_login
                    )
                    return redirect('operativo')
                else:
                    return redirect('home')

            except Personal.DoesNotExist:
                return redirect('home')
        else:
            error = "Usuario o contraseña incorrectos"

    return render(request, 'login.html', {
        'error': error,
        'selected_username': selected_username
    })


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def home(request):
    try:
        personal = Personal.objects.get(user=request.user)

        if Administrativo.objects.filter(personal=personal).exists():
            return redirect('administrativo')
        elif Operativo.objects.filter(personal=personal).exists():
            return redirect('operativo')

    except Personal.DoesNotExist:
        pass

    return render(request, 'home.html')

def landing(request):
    servicios_web = Servicio.objects.order_by('nombre')
    return render(request, 'web/index.html', {
        'servicios_web': servicios_web,
    })


def csrf_failure(request, reason=""):
    messages.error(
        request,
        "La pagina del formulario estaba desactualizada. Intenta enviar la solicitud nuevamente."
    )

    if request.path == reverse('crear_solicitud_web'):
        return redirect(f"{reverse('landing')}#contacts")

    return redirect('login')

@login_required
def administrativo_view(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    asegurar_tablas_administrativas()
    actualizar_citas_vencidas()

    hoy = timezone.localdate()
    inicio_hoy = timezone.make_aware(datetime.combine(hoy, time.min))
    inicio_manana = inicio_hoy + timedelta(days=1)
    citas_hoy = Cita.objects.filter(
        fechahora__gte=inicio_hoy,
        fechahora__lt=inicio_manana,
    ).select_related('cliente', 'operativo', 'estado').order_by('fechahora')
    citas_por_atender_hoy = citas_hoy.filter(
        Q(estado__nombre_estado='Programada')
        | Q(estado__nombre_estado='Pendiente')
        | Q(estado__isnull=True)
    ).count()
    solicitudes_pendientes = Propuestas_Citas.objects.filter(Atendida=False).count()
    mes = hoy.month
    anio = hoy.year
    ingresos_mes = ingresos_facturados_pagados(anio, mes)
    gasto_mes = GastoMensual.objects.filter(mes=mes, anio=anio).first()
    _, salarios_operativos = comisiones_operativos_mes(anio, mes)

    if gasto_mes:
        if gasto_mes.cerrado:
            if gasto_mes.ingresos_cierre is not None:
                ingresos_mes = gasto_mes.ingresos_cierre
            salarios_operativos = (
                gasto_mes.salarios_cierre
                if gasto_mes.salarios_cierre is not None
                else gasto_mes.salarios
            )
        gastos_mes = total_gasto_mensual(gasto_mes, salarios_operativos)
        mes_financiero_registrado = True
    else:
        gastos_mes = salarios_operativos
        mes_financiero_registrado = False

    balance_mes = ingresos_mes - gastos_mes
    facturas_pendientes = Factura.objects.filter(
        estado_pago__nombre='Pendiente'
    ).count()
    fecha_alerta = timezone.now() - timedelta(days=7)
    citas_canceladas_recientes = HistorialAccion.objects.filter(
        accion='Cancelar cita',
        fecha__gte=fecha_alerta
    ).count()

    alertas_admin = []

    if facturas_pendientes:
        alertas_admin.append({
            'tipo': 'warning',
            'titulo': 'Facturas pendientes',
            'mensaje': f'Hay {facturas_pendientes} facturas pendientes de pago.'
        })

    if citas_canceladas_recientes:
        alertas_admin.append({
            'tipo': 'danger',
            'titulo': 'Citas canceladas',
            'mensaje': f'Se cancelaron {citas_canceladas_recientes} citas en los ultimos 7 dias.'
        })

    if not mes_financiero_registrado:
        alertas_admin.append({
            'tipo': 'info',
            'titulo': 'Finanzas del mes',
            'mensaje': 'Aun no se han registrado gastos para el mes actual.'
        })

    return render(request, 'administrativo.html', {
        'hoy': hoy,
        'citas_hoy': citas_hoy,
        'total_citas_hoy': citas_hoy.count(),
        'citas_por_atender_hoy': citas_por_atender_hoy,
        'total_pacientes': Cliente.objects.count(),
        'solicitudes_pendientes': solicitudes_pendientes,
        'facturas_pendientes': facturas_pendientes,
        'total_empleados': Personal.objects.count(),
        'total_servicios': Servicio.objects.count(),
        'ingresos_mes': ingresos_mes,
        'gastos_mes': gastos_mes,
        'salarios_operativos': salarios_operativos,
        'balance_mes': balance_mes,
        'alertas_admin': alertas_admin,
        'citas_canceladas_recientes': citas_canceladas_recientes,
    })


@login_required
def finanzas_admin(request):
    try:
        personal = Personal.objects.get(user=request.user)
        administrativo = Administrativo.objects.get(personal=personal)
    except (Personal.DoesNotExist, Administrativo.DoesNotExist):
        return redirect('home')

    asegurar_tablas_administrativas()
    actualizar_citas_vencidas()

    hoy = timezone.localdate()
    anios = list(range(hoy.year - 3, hoy.year + 2))

    try:
        mes = int(request.POST.get('mes') or request.GET.get('mes') or hoy.month)
        anio = int(request.POST.get('anio') or request.GET.get('anio') or hoy.year)
    except (TypeError, ValueError):
        mes = hoy.month
        anio = hoy.year

    if mes < 1 or mes > 12:
        mes = hoy.month

    if anio < 2000 or anio > hoy.year + 5:
        anio = hoy.year

    comisiones_operativos, salarios_operativos = comisiones_operativos_mes(anio, mes)
    facturas_pagadas, facturas_pendientes = conteo_facturas_periodo(anio, mes)

    gasto = GastoMensual.objects.filter(
        mes=mes,
        anio=anio
    ).first()

    if request.method == 'POST':
        accion_formulario = request.POST.get('accion', 'guardar')

        if gasto and gasto.cerrado:
            messages.error(request, "Este mes ya esta cerrado y no se puede modificar.")
            return redirect(f"{reverse('finanzas_admin')}?mes={mes}&anio={anio}")

        if accion_formulario == 'cerrar_mes':
            if not gasto:
                gasto = GastoMensual.objects.create(
                    mes=mes,
                    anio=anio,
                    luz=Decimal('0.00'),
                    agua=Decimal('0.00'),
                    internet=Decimal('0.00'),
                    materiales=Decimal('0.00'),
                    salarios=salarios_operativos,
                    otros=Decimal('0.00'),
                    fecha_registro=timezone.now(),
                    administrativo=administrativo,
                    cerrado=True,
                    ingresos_cierre=ingresos_facturados_pagados(anio, mes),
                    salarios_cierre=salarios_operativos,
                    facturas_pagadas_cierre=facturas_pagadas,
                    facturas_pendientes_cierre=facturas_pendientes,
                    detalle_comisiones_cierre=json.dumps(
                        comisiones_operativos,
                        ensure_ascii=False,
                        default=str
                    ),
                    ingresos_por_metodo_cierre=json.dumps(
                        ingresos_por_metodo_pago(anio, mes),
                        ensure_ascii=False,
                        default=str
                    ),
                )
            else:
                gasto.salarios = salarios_operativos
                gasto.ingresos_cierre = ingresos_facturados_pagados(anio, mes)
                gasto.salarios_cierre = salarios_operativos
                gasto.facturas_pagadas_cierre = facturas_pagadas
                gasto.facturas_pendientes_cierre = facturas_pendientes
                gasto.detalle_comisiones_cierre = json.dumps(
                    comisiones_operativos,
                    ensure_ascii=False,
                    default=str
                )
                gasto.ingresos_por_metodo_cierre = json.dumps(
                    ingresos_por_metodo_pago(anio, mes),
                    ensure_ascii=False,
                    default=str
                )
                gasto.fecha_registro = timezone.now()
                gasto.administrativo = administrativo
                gasto.cerrado = True
                gasto.save()

            registrar_accion(
                request,
                "Cerrar mes financiero",
                f"Mes {mes}/{anio} cerrado.",
                mes=mes,
                anio=anio
            )
            messages.success(request, "Mes financiero cerrado correctamente.")
            return redirect(f"{reverse('finanzas_admin')}?mes={mes}&anio={anio}")

        try:
            datos_gasto = {
                'luz': decimal_post(request, 'luz'),
                'agua': decimal_post(request, 'agua'),
                'internet': decimal_post(request, 'internet'),
                'materiales': decimal_post(request, 'materiales'),
                'salarios': salarios_operativos,
                'otros': decimal_post(request, 'otros'),
                'fecha_registro': timezone.now(),
                'administrativo': administrativo,
                'cerrado': False,
            }
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            GastoMensual.objects.update_or_create(
                mes=mes,
                anio=anio,
                defaults=datos_gasto
            )
            registrar_accion(
                request,
                "Modificar gastos",
                f"Gastos registrados para {mes}/{anio}.",
                mes=mes,
                anio=anio
            )
            messages.success(request, "Gastos mensuales guardados correctamente.")
            return redirect(f"{reverse('finanzas_admin')}?mes={mes}&anio={anio}")

    if not gasto:
        gasto = GastoMensual(
            mes=mes,
            anio=anio,
            luz=Decimal('0.00'),
            agua=Decimal('0.00'),
            internet=Decimal('0.00'),
            materiales=Decimal('0.00'),
            salarios=salarios_operativos,
            otros=Decimal('0.00'),
            cerrado=False,
        )

    ingresos_mes_actual = ingresos_facturados_pagados(anio, mes)
    if gasto.cerrado and gasto.ingresos_cierre is not None:
        ingresos_mes = gasto.ingresos_cierre
    else:
        ingresos_mes = ingresos_mes_actual
    if gasto.cerrado:
        salarios_mes = (
            gasto.salarios_cierre
            if gasto.salarios_cierre is not None
            else gasto.salarios
        )
    else:
        salarios_mes = salarios_operativos
    gastos_mes = total_gasto_mensual(gasto, salarios_mes)
    balance_mes = ingresos_mes - gastos_mes

    if gasto.cerrado:
        if gasto.facturas_pagadas_cierre is not None:
            facturas_pagadas = gasto.facturas_pagadas_cierre
        if gasto.facturas_pendientes_cierre is not None:
            facturas_pendientes = gasto.facturas_pendientes_cierre
        if gasto.detalle_comisiones_cierre:
            try:
                comisiones_operativos = json.loads(gasto.detalle_comisiones_cierre)
                for item in comisiones_operativos:
                    item['total_comision'] = Decimal(item['total_comision'])
            except (TypeError, ValueError, KeyError, InvalidOperation):
                comisiones_operativos, _ = comisiones_operativos_mes(anio, mes)

    if gasto.cerrado and gasto.ingresos_por_metodo_cierre:
        try:
            ingresos_metodos = json.loads(gasto.ingresos_por_metodo_cierre)
            for item in ingresos_metodos:
                item['total'] = Decimal(item['total'])
                item['operaciones'] = int(item['operaciones'])
        except (TypeError, ValueError, KeyError, InvalidOperation):
            ingresos_metodos = ingresos_por_metodo_pago(anio, mes)
    else:
        ingresos_metodos = ingresos_por_metodo_pago(anio, mes)

    for item in ingresos_metodos:
        item['porcentaje'] = (
            float(item['total'] * Decimal('100') / ingresos_mes)
            if ingresos_mes > 0 else 0
        )

    detalle_gastos = [
        {'categoria': 'Luz', 'monto': gasto.luz or Decimal('0.00')},
        {'categoria': 'Agua', 'monto': gasto.agua or Decimal('0.00')},
        {'categoria': 'Internet', 'monto': gasto.internet or Decimal('0.00')},
        {'categoria': 'Materiales', 'monto': gasto.materiales or Decimal('0.00')},
        {'categoria': 'Comisiones', 'monto': salarios_mes or Decimal('0.00')},
        {'categoria': 'Otros', 'monto': gasto.otros or Decimal('0.00')},
    ]
    for item in detalle_gastos:
        item['porcentaje'] = (
            float(item['monto'] * Decimal('100') / gastos_mes)
            if gastos_mes > 0 else 0
        )

    gastos_por_periodo = {
        (item.anio, item.mes): item
        for item in GastoMensual.objects.all()
    }
    historial = []
    indice_mes_actual = hoy.year * 12 + hoy.month - 1
    for desplazamiento in range(12):
        indice = indice_mes_actual - desplazamiento
        anio_historial, mes_indice = divmod(indice, 12)
        mes_historial = mes_indice + 1
        item = gastos_por_periodo.get((anio_historial, mes_historial))
        if item is None:
            item = GastoMensual(
                mes=mes_historial,
                anio=anio_historial,
                luz=Decimal('0.00'),
                agua=Decimal('0.00'),
                internet=Decimal('0.00'),
                materiales=Decimal('0.00'),
                salarios=Decimal('0.00'),
                otros=Decimal('0.00'),
                cerrado=False,
            )
        historial.append(item)

    nombres_meses = dict(MESES)
    for item in historial:
        item.nombre_mes = nombres_meses.get(item.mes, item.mes)
        _, item.salarios_operativos = comisiones_operativos_mes(item.anio, item.mes)
        if item.cerrado and item.ingresos_cierre is not None:
            item.ingresos = item.ingresos_cierre
        else:
            item.ingresos = ingresos_facturados_pagados(item.anio, item.mes)
        if item.cerrado:
            item.salarios_operativos = (
                item.salarios_cierre
                if item.salarios_cierre is not None
                else item.salarios
            )
        item.total_gastos = total_gasto_mensual(item, item.salarios_operativos)
        item.balance = item.ingresos - item.total_gastos

    max_tendencia = max(
        [max(item.ingresos, item.total_gastos) for item in historial] or [Decimal('0.00')]
    ) or Decimal('1.00')
    for item in historial:
        item.ingresos_pct = max(2, int(item.ingresos * Decimal('100') / max_tendencia))
        item.gastos_pct = max(2, int(item.total_gastos * Decimal('100') / max_tendencia))

    if request.GET.get('descargar') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = (
            f'attachment; filename="finanzas_{anio}_{mes:02d}.csv"'
        )
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(['Control financiero', f'{mes:02d}/{anio}'])
        writer.writerow(['Estado del período', 'Cerrado' if gasto.cerrado else 'Abierto'])
        writer.writerow([])
        writer.writerow(['Resumen', 'Monto C$'])
        writer.writerow(['Ingresos brutos registrados', ingresos_mes])
        writer.writerow(['Gastos y comisiones', gastos_mes])
        writer.writerow(['Resultado neto operativo', balance_mes])
        writer.writerow(['Facturas cobradas', facturas_pagadas])
        writer.writerow(['Facturas pendientes emitidas en el período', facturas_pendientes])
        writer.writerow([])
        writer.writerow(['Desglose de gastos', 'Monto C$', 'Porcentaje'])
        for item in detalle_gastos:
            writer.writerow([item['categoria'], item['monto'], f"{item['porcentaje']:.2f}%"])
        writer.writerow([])
        writer.writerow(['Cobros por método', 'Operaciones', 'Monto C$', 'Porcentaje'])
        for item in ingresos_metodos:
            writer.writerow([
                item['metodo'], item['operaciones'], item['total'],
                f"{item['porcentaje']:.2f}%",
            ])
        writer.writerow([])
        writer.writerow(['Nota', 'Montos registrados; IVA no calculado ni incluido'])
        return response

    return render(
        request,
        'administrativo/finanzas.html',
        {
            'gasto': gasto,
            'mes': mes,
            'anio': anio,
            'meses': MESES,
            'anios': anios,
            'ingresos_mes': ingresos_mes,
            'gastos_mes': gastos_mes,
            'balance_mes': balance_mes,
            'salarios_operativos': salarios_mes,
            'comisiones_operativos': comisiones_operativos,
            'facturas_pagadas': facturas_pagadas,
            'facturas_pendientes': facturas_pendientes,
            'ingresos_metodos': ingresos_metodos,
            'detalle_gastos': detalle_gastos,
            'historial': historial,
            'max_tendencia': max_tendencia,
            'mes_cerrado': gasto.cerrado,
        }
    )


@login_required
def clientes_cuentas_admin(request):
    try:
        personal = Personal.objects.get(user=request.user)
        Administrativo.objects.get(personal=personal)
    except (Personal.DoesNotExist, Administrativo.DoesNotExist):
        return redirect('home')

    asegurar_tablas_administrativas()
    asegurar_tabla_pago_factura()
    actualizar_citas_vencidas()

    busqueda = request.GET.get('buscar', '').strip()
    operativo_id = request.GET.get('operativo', '').strip()
    estado_factura = request.GET.get('estado_factura', '').strip()
    orden = request.GET.get('orden', request.GET.get('orden_fecha', 'recientes')).strip()
    if orden not in ('recientes', 'antiguas', 'citas', 'saldo_pendiente'):
        orden = 'recientes'

    citas = Cita.objects.select_related(
        'cliente',
        'operativo',
        'operativo__personal',
        'estado'
    ).order_by('-fechahora')

    if operativo_id:
        citas = citas.filter(operativo_id=operativo_id)

    if busqueda:
        citas = citas.filter(
            Q(cliente__nombre__icontains=busqueda) |
            Q(cliente__apellido__icontains=busqueda) |
            Q(cliente__n_telefono__icontains=busqueda) |
            Q(cliente__email__icontains=busqueda) |
            Q(operativo__personal__nombre__icontains=busqueda) |
            Q(operativo__personal__apellido__icontains=busqueda)
        )

    resumen = {}
    citas_ids = []

    for cita in citas:
        if not cita.cliente:
            continue

        citas_ids.append(cita.id_cita)
        operativo = cita.operativo
        operativo_key = operativo.id_operativo if operativo else 0
        key = (operativo_key, cita.cliente.id_cliente)

        item = resumen.setdefault(key, {
            'cliente': cita.cliente,
            'operativo': operativo,
            'total_citas': 0,
            'programadas': 0,
            'atendidas': 0,
            'canceladas': 0,
            'total_facturas': 0,
            'facturas_pagadas': 0,
            'facturas_pendientes': 0,
            'facturas_anuladas': 0,
            'total_facturado': Decimal('0.00'),
            'total_pagado': Decimal('0.00'),
            'total_pendiente': Decimal('0.00'),
            'ultima_cita': cita,
            'ultima_factura': None,
        })

        item['total_citas'] += 1

        estado_cita = cita.estado.nombre_estado if cita.estado else ''
        if estado_cita == 'Programada':
            item['programadas'] += 1
        elif estado_cita == 'Atendida':
            item['atendidas'] += 1
        elif estado_cita == 'Cancelada':
            item['canceladas'] += 1

        if cita.fechahora > item['ultima_cita'].fechahora:
            item['ultima_cita'] = cita

    facturas = Factura.objects.filter(
        cita_id__in=citas_ids
    ).select_related(
        'cita',
        'cita__cliente',
        'cita__operativo',
        'cita__operativo__personal',
        'estado_pago'
    ).order_by('-fecha_factura')

    if estado_factura:
        facturas = facturas.filter(estado_pago__nombre=estado_factura)

    facturas = list(facturas)
    pagos_por_factura = dict(
        PagoFactura.objects.filter(
            factura_id__in=[factura.id_factura for factura in facturas]
        ).values('factura_id').annotate(
            total_pagado=Sum('monto')
        ).values_list('factura_id', 'total_pagado')
    )

    for factura in facturas:
        if not factura.cita or not factura.cita.cliente:
            continue

        operativo = factura.cita.operativo
        operativo_key = operativo.id_operativo if operativo else 0
        key = (operativo_key, factura.cita.cliente.id_cliente)
        item = resumen.get(key)

        if not item:
            continue

        estado_pago = factura.estado_pago.nombre if factura.estado_pago else ''
        total = factura.total or Decimal('0.00')
        monto_pagado = pagos_por_factura.get(factura.id_factura, Decimal('0.00')) or Decimal('0.00')

        item['total_facturas'] += 1

        if estado_pago == 'Pagada':
            item['facturas_pagadas'] += 1
            # Compatibilidad con facturas antiguas cuyo pago no tiene detalle.
            item['total_pagado'] += monto_pagado or total
            item['total_facturado'] += total
        elif estado_pago == 'Pendiente':
            item['facturas_pendientes'] += 1
            # Las citas se cobran completas: una factura pendiente cuenta como
            # el total por cobrar, sin descontar registros históricos parciales.
            item['total_pendiente'] += total
            item['total_facturado'] += total
        elif estado_pago == 'Anulada':
            item['facturas_anuladas'] += 1
        else:
            item['total_facturado'] += total

        if not item['ultima_factura']:
            item['ultima_factura'] = factura

    clientes_resumen = list(resumen.values())

    if estado_factura:
        clientes_resumen = [
            item for item in clientes_resumen
            if item['total_facturas'] > 0
        ]

    if orden == 'antiguas':
        clientes_resumen.sort(key=lambda item: item['ultima_cita'].fechahora)
    elif orden == 'citas':
        clientes_resumen.sort(
            key=lambda item: (item['total_citas'], item['ultima_cita'].fechahora),
            reverse=True,
        )
    elif orden == 'saldo_pendiente':
        clientes_resumen.sort(
            key=lambda item: (item['total_pendiente'], item['ultima_cita'].fechahora),
            reverse=True,
        )
    else:
        clientes_resumen.sort(key=lambda item: item['ultima_cita'].fechahora, reverse=True)

    total_clientes = len({
        item['cliente'].id_cliente
        for item in clientes_resumen
    })
    total_citas = sum(item['total_citas'] for item in clientes_resumen)
    total_facturas = sum(item['total_facturas'] for item in clientes_resumen)
    total_pagado = sum(
        (item['total_pagado'] for item in clientes_resumen),
        Decimal('0.00')
    )
    total_pendiente = sum(
        (item['total_pendiente'] for item in clientes_resumen),
        Decimal('0.00')
    )

    paginator = Paginator(clientes_resumen, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    operativos = Operativo.objects.select_related(
        'personal'
    ).order_by(
        'personal__nombre',
        'personal__apellido'
    )

    return render(
        request,
        'administrativo/clientes_cuentas.html',
        {
            'clientes_resumen': page_obj,
            'page_obj': page_obj,
            'total_resultados': paginator.count,
            'primer_resultado': page_obj.start_index() if paginator.count else 0,
            'ultimo_resultado': page_obj.end_index() if paginator.count else 0,
            'operativos': operativos,
            'busqueda': busqueda,
            'operativo_id': operativo_id,
            'estado_factura': estado_factura,
            'orden': orden,
            'query_string': urlencode(query_params),
            'total_clientes': total_clientes,
            'total_citas': total_citas,
            'total_facturas': total_facturas,
            'total_pagado': total_pagado,
            'total_pendiente': total_pendiente,
            'acciones_recientes': HistorialAccion.objects.order_by('-fecha')[:20],
        }
    )


@login_required
def pacientes_admin(request):
    """Consulta de todos los pacientes para administración y de los propios para operativo."""
    es_administrativo = usuario_es_administrativo(request.user)
    operativo = None if es_administrativo else obtener_operativo_usuario(request)
    if not es_administrativo and not operativo:
        return redirect('home')

    busqueda = request.GET.get('buscar', '').strip()
    citas_query = Cita.objects.select_related(
        'estado', 'operativo', 'operativo__personal'
    ).prefetch_related('factura_set').order_by('-fechahora')
    if operativo:
        citas_query = citas_query.filter(operativo=operativo)

    citas_prefetch = Prefetch(
        'cita_set',
        queryset=citas_query,
        to_attr='citas_consulta'
    )
    conteo_citas = Count(
        'cita',
        filter=Q(cita__operativo=operativo) if operativo else Q(),
        distinct=True,
    )
    pacientes = Cliente.objects.annotate(
        total_citas=conteo_citas,
        ultima_cita_fecha=Max('cita__fechahora'),
    ).prefetch_related(citas_prefetch)
    if operativo:
        pacientes = pacientes.filter(cita__operativo=operativo).distinct()

    if busqueda:
        pacientes = pacientes.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(n_telefono__icontains=busqueda) |
            Q(email__icontains=busqueda)
        )

    orden = request.GET.get('orden', 'nombre')
    ordenamientos = {
        'nombre': ('apellido', 'nombre'),
        'citas': ('-total_citas', 'apellido', 'nombre'),
        'reciente': ('-ultima_cita_fecha', 'apellido', 'nombre'),
    }
    if orden not in ordenamientos:
        orden = 'nombre'
    pacientes = pacientes.order_by(*ordenamientos[orden])

    paginator = Paginator(pacientes, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    query_params = request.GET.copy()
    query_params.pop('page', None)
    return render(request, 'administrativo/pacientes.html', {
        'pacientes': page_obj,
        'page_obj': page_obj,
        'total_pacientes': paginator.count,
        'primer_resultado': page_obj.start_index() if paginator.count else 0,
        'ultimo_resultado': page_obj.end_index() if paginator.count else 0,
        'busqueda': busqueda,
        'orden': orden,
        'query_string': urlencode(query_params),
        'es_administrativo': es_administrativo,
    })


def usuario_es_administrativo(user):
    if not user.is_authenticated:
        return False
    try:
        personal = Personal.objects.get(user=user)
        return Administrativo.objects.filter(personal=personal).exists()
    except Personal.DoesNotExist:
        return False


def usuario_puede_acceder_operativo(user, operativo):
    if not user.is_authenticated or operativo is None:
        return False
    if usuario_es_administrativo(user):
        return True
    return Operativo.objects.filter(
        id_operativo=operativo.id_operativo,
        personal__user=user,
    ).exists()


def usuario_puede_agendar_para_operativo(user, operativo):
    if not user.is_authenticated or operativo is None:
        return False
    return usuario_es_administrativo(user) or Operativo.objects.filter(
        personal__user=user
    ).exists()


def usuario_puede_acceder_cita(user, cita):
    if not user.is_authenticated or cita is None:
        return False
    if usuario_es_administrativo(user):
        return True
    if not cita.operativo_id:
        return False
    return Operativo.objects.filter(
        id_operativo=cita.operativo_id,
        personal__user=user,
    ).exists()


def usuario_puede_acceder_cliente(user, cliente):
    if not user.is_authenticated or cliente is None:
        return False
    if usuario_es_administrativo(user):
        return True
    return Operativo.objects.filter(
        personal__user=user,
        cita__cliente=cliente,
    ).exists()


def usuario_puede_gestionar_clientes(user):
    if not user.is_authenticated:
        return False
    return usuario_es_administrativo(user) or Operativo.objects.filter(
        personal__user=user
    ).exists()


def _periodo_comisiones_desde_request(request):
    hoy = timezone.localdate()
    try:
        mes = int(request.POST.get('mes') or request.GET.get('mes') or hoy.month)
        anio = int(request.POST.get('anio') or request.GET.get('anio') or hoy.year)
    except (TypeError, ValueError):
        mes, anio = hoy.month, hoy.year
    if mes < 1 or mes > 12:
        mes = hoy.month
    if anio < 2000 or anio > hoy.year + 5:
        anio = hoy.year
    return mes, anio, f'{anio:04d}-{mes:02d}'


@login_required
def gestionar_comisiones(request):
    es_administrativo = usuario_es_administrativo(request.user)
    personal_actual = None
    if not es_administrativo:
        try:
            personal_actual = Personal.objects.get(user=request.user)
        except Personal.DoesNotExist:
            return redirect('home')
        if not Operativo.objects.filter(personal=personal_actual).exists():
            return redirect('home')

    asegurar_tabla_comision()
    mes, anio, periodo = _periodo_comisiones_desde_request(request)

    if request.method == 'POST':
        if not es_administrativo or request.POST.get('accion') != 'calcular':
            return redirect('home')

        resultados, _ = comisiones_operativos_mes(anio, mes)
        if not resultados:
            messages.info(request, 'No hay servicios en facturas pagadas para calcular en este período.')
        else:
            nuevas = actualizadas = protegidas = 0
            with transaction.atomic():
                for item in resultados:
                    personal_id = item.get('personal_id')
                    if not personal_id:
                        continue
                    comision, creada = Comision.objects.get_or_create(
                        personal_id=personal_id,
                        periodo=periodo,
                        defaults={
                            'monto': item['total_comision'],
                            'estado': 'Calculada',
                        }
                    )
                    if creada:
                        nuevas += 1
                    elif comision.estado == 'Pagada':
                        protegidas += 1
                    else:
                        comision.monto = item['total_comision']
                        comision.save(update_fields=['monto'])
                        actualizadas += 1

            registrar_accion(
                request,
                'Calcular comisiones',
                f'Período {periodo}: {nuevas} nuevas, {actualizadas} actualizadas; '
                f'{protegidas} pagadas conservaron su monto.',
                mes=mes,
                anio=anio
            )
            messages.success(
                request,
                f'Período calculado: {nuevas} nuevas y {actualizadas} actualizadas.'
            )
            if protegidas:
                messages.info(
                    request,
                    f'{protegidas} comisiones marcadas como pagadas conservaron su monto registrado.'
                )
        return redirect(f"{reverse('gestionar_comisiones')}?{urlencode({'mes': mes, 'anio': anio})}")

    comisiones = Comision.objects.select_related('personal').filter(periodo=periodo)
    busqueda = request.GET.get('buscar', '').strip()
    personal_id = request.GET.get('personal', '').strip()
    estado = request.GET.get('estado', '').strip()

    if personal_actual:
        comisiones = comisiones.filter(personal=personal_actual)
        personal_id = str(personal_actual.id_personal)
    elif personal_id.isdigit():
        comisiones = comisiones.filter(personal_id=int(personal_id))
    if estado in dict(Comision.ESTADOS):
        comisiones = comisiones.filter(estado=estado)
    if busqueda:
        comisiones = comisiones.filter(
            Q(personal__nombre__icontains=busqueda) |
            Q(personal__apellido__icontains=busqueda)
        )

    operativos = Operativo.objects.select_related('personal').order_by(
        'personal__apellido', 'personal__nombre'
    ) if es_administrativo else []
    total_periodo = comisiones.aggregate(total=Sum('monto'))['total'] or Decimal('0.00')
    inicio, fin = rango_mes(anio, mes)
    conteos_servicios = dict(
        ServicioFactura.objects.filter(
            factura__fecha_factura__gte=inicio,
            factura__fecha_factura__lt=fin,
            factura__estado_pago__nombre='Pagada',
            factura__cita__operativo__isnull=False
        ).values(
            'factura__cita__operativo__personal_id'
        ).annotate(
            cantidad=Count('id_serviciofactura')
        ).values_list('factura__cita__operativo__personal_id', 'cantidad')
    )
    comisiones = list(comisiones)
    for comision in comisiones:
        comision.cantidad_servicios = conteos_servicios.get(comision.personal_id, 0)
    url_periodo = f"{reverse('gestionar_comisiones')}?{urlencode({'mes': mes, 'anio': anio})}"

    return render(request, 'comisiones/listar_comisiones.html', {
        'comisiones': comisiones,
        'mes': mes,
        'anio': anio,
        'meses': MESES,
        'anios': list(range(timezone.localdate().year - 3, timezone.localdate().year + 2)),
        'busqueda': busqueda,
        'personal_id': personal_id,
        'estado': estado,
        'estados': Comision.ESTADOS,
        'operativos': operativos,
        'es_administrativo': es_administrativo,
        'total_periodo': total_periodo,
        'url_periodo': url_periodo,
    })


@login_required
def detalle_comision(request, id_comision):
    es_administrativo = usuario_es_administrativo(request.user)
    asegurar_tabla_comision()
    comision = get_object_or_404(
        Comision.objects.select_related('personal'),
        id_comision=id_comision
    )
    if not es_administrativo:
        try:
            personal_actual = Personal.objects.get(user=request.user)
        except Personal.DoesNotExist:
            return redirect('home')
        if not Operativo.objects.filter(personal=personal_actual).exists() or personal_actual.id_personal != comision.personal_id:
            return redirect('home')

    try:
        anio_comision, mes_comision = (int(pieza) for pieza in comision.periodo.split('-', 1))
    except (TypeError, ValueError):
        anio_comision, mes_comision = timezone.localdate().year, timezone.localdate().month
    inicio, fin = rango_mes(anio_comision, mes_comision)
    servicios = ServicioFactura.objects.filter(
        factura__fecha_factura__gte=inicio,
        factura__fecha_factura__lt=fin,
        factura__estado_pago__nombre='Pagada',
        factura__cita__operativo__personal_id=comision.personal_id,
    ).select_related(
        'servicio', 'factura', 'factura__cita', 'factura__cita__cliente'
    ).order_by('factura__fecha_factura', 'factura_id')
    monto_fuente = sum(
        (detalle.servicio.comision_operativo or Decimal('0.00') for detalle in servicios),
        Decimal('0.00')
    )

    siguiente = request.POST.get('next') or request.GET.get('next')
    volver_url = url_retorno_segura(request, siguiente, 'gestionar_comisiones')
    form = ComisionEstadoForm(request.POST or None, initial={'estado': comision.estado})
    if request.method == 'POST' and es_administrativo and form.is_valid():
        estado_anterior = comision.estado
        comision.estado = form.cleaned_data['estado']
        comision.save(update_fields=['estado'])
        registrar_accion(
            request,
            'Actualizar estado de comisión',
            f'Comisión #{comision.id_comision}: {estado_anterior} → {comision.estado}.',
            mes=mes_comision,
            anio=anio_comision
        )
        messages.success(request, 'Estado de la comisión actualizado.')
        return redirect(f"{reverse('detalle_comision', kwargs={'id_comision': comision.id_comision})}?{urlencode({'next': volver_url})}")

    return render(request, 'comisiones/detalle_comision.html', {
        'comision': comision,
        'servicios': servicios,
        'monto_fuente': monto_fuente,
        'form': form,
        'es_administrativo': es_administrativo,
        'volver_url': volver_url,
    })


@login_required
def registrar_paciente_admin(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    cancel_url = url_retorno_segura(
        request,
        request.POST.get('cancel_to') or request.GET.get('cancel_to'),
        'pacientes_admin',
    )
    after_save = url_retorno_segura(
        request,
        request.POST.get('after_save') or request.GET.get('after_save'),
        'pacientes_admin',
    )
    form = ClienteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        paciente = form.save()
        messages.success(request, 'El paciente se registró correctamente.')
        detalle_url = reverse('detalle_paciente_admin', kwargs={'id_cliente': paciente.id_cliente})
        return redirect(f"{detalle_url}?{urlencode({'next': after_save})}")

    return render(request, 'administrativo/formulario_paciente.html', {
        'form': form,
        'titulo': 'Registrar paciente',
        'texto_boton': 'Registrar paciente',
        'cancel_url': cancel_url,
        'after_save': after_save,
    })


@login_required
def editar_paciente_admin(request, id_cliente):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    paciente = get_object_or_404(Cliente, id_cliente=id_cliente)
    cancel_url = url_retorno_segura(
        request,
        request.POST.get('cancel_to') or request.GET.get('cancel_to'),
        'detalle_paciente_admin',
        id_cliente=paciente.id_cliente,
    )
    after_save = url_retorno_segura(
        request,
        request.POST.get('after_save') or request.GET.get('after_save'),
        'pacientes_admin',
    )
    form = ClienteForm(request.POST or None, instance=paciente)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'La información del paciente se actualizó correctamente.')
        detalle_url = reverse('detalle_paciente_admin', kwargs={'id_cliente': paciente.id_cliente})
        return redirect(f"{detalle_url}?{urlencode({'next': after_save})}")

    return render(request, 'administrativo/formulario_paciente.html', {
        'form': form,
        'paciente': paciente,
        'titulo': 'Actualizar información del paciente',
        'texto_boton': 'Guardar cambios',
        'cancel_url': cancel_url,
        'after_save': after_save,
    })


@login_required
def detalle_paciente_admin(request, id_cliente):
    es_administrativo = usuario_es_administrativo(request.user)
    operativo = None if es_administrativo else obtener_operativo_usuario(request)
    if not es_administrativo and not operativo:
        return redirect('home')

    return_url = url_retorno_segura(
        request,
        request.GET.get('next'),
        'pacientes_admin',
    )
    paciente = get_object_or_404(Cliente, id_cliente=id_cliente)
    if operativo and not Cita.objects.filter(cliente=paciente, operativo=operativo).exists():
        return redirect('home')

    citas = Cita.objects.filter(cliente=paciente).select_related(
        'estado', 'operativo', 'operativo__personal'
    ).prefetch_related(
        'servicios', 'factura_set__estado_pago'
    ).order_by('-fechahora')
    if operativo:
        citas = citas.filter(operativo=operativo)
    expedientes = ExpedienteClinico.objects.filter(cliente=paciente).select_related(
        'operativo', 'operativo__personal'
    ).order_by('-fecha_de_atencion', '-id_expediente')
    if operativo:
        expedientes = expedientes.filter(operativo=operativo)

    return render(request, 'administrativo/detalle_paciente.html', {
        'paciente': paciente,
        'return_url': return_url,
        'citas': citas,
        'expedientes': expedientes,
        'es_administrativo': es_administrativo,
    })


def crear_solicitud_web(request):
    if request.method != 'POST':
        return redirect('landing')

    if request.POST.get('website_url', '').strip():
        messages.success(
            request,
            "Solicitud enviada correctamente. La clinica revisara tu solicitud y se pondra en contacto contigo para confirmar la cita."
        )
        return redirect(f"{reverse('landing')}#contacts")

    nombre = request.POST.get('nombre', '').strip()
    email = request.POST.get('email', '').strip()
    telefono = ''.join(
        digito
        for digito in request.POST.get('numero_telefonico', '')
        if digito.isdigit()
    )
    preferencia = request.POST.get('propuesta_de_dia', '').strip()
    servicio_solicitado_id = request.POST.get('servicio_solicitado', '').strip()

    if not nombre or not telefono or not servicio_solicitado_id:
        messages.error(
            request,
            "Nombre, telefono y servicio solicitado son obligatorios."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if len(nombre) > 100 or len(email) > 150:
        messages.error(
            request,
            "El nombre no puede superar 100 caracteres y el correo 150."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if len(nombre.split()) < 2:
        messages.error(
            request,
            "Ingresa tu nombre y al menos un apellido para poder registrar la solicitud."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if len(preferencia) > 80:
        messages.error(
            request,
            "La preferencia de fecha u horario no puede superar los 80 caracteres."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if servicio_solicitado_id == 'orientacion':
        servicio_solicitado = 'Necesito orientación'
    else:
        try:
            servicio_solicitado = Servicio.objects.get(
                id_servicio=int(servicio_solicitado_id)
            ).nombre
        except (ValueError, Servicio.DoesNotExist):
            messages.error(
                request,
                "Selecciona un servicio válido o indica que necesitas orientación."
            )
            return redirect(f"{reverse('landing')}#contacts")

    propuesta = f"Servicio solicitado: {servicio_solicitado}."
    if preferencia:
        propuesta += f" Preferencia de fecha u horario: {preferencia}"

    if len(telefono) != 8:
        messages.error(
            request,
            "El telefono debe tener exactamente 8 numeros."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if email:
        try:
            validate_email(email)
        except ValidationError:
            messages.error(
                request,
                "Ingrese un correo valido o deje el campo vacio."
            )
            return redirect(f"{reverse('landing')}#contacts")

    filtro_contacto = Q(Numero_telefonico=telefono)

    if email:
        filtro_contacto |= Q(Email__iexact=email)

    if Propuestas_Citas.objects.filter(
        filtro_contacto,
        Atendida=False
    ).exists():
        messages.warning(
            request,
            "Ya tienes una solicitud pendiente. La clinica se pondra en contacto contigo para confirmarla."
        )
        return redirect(f"{reverse('landing')}#contacts")

    hoy = timezone.localdate()
    inicio_dia = timezone.make_aware(
        datetime.combine(hoy, time.min)
    )
    fin_dia = inicio_dia + timedelta(days=1)

    solicitudes_hoy = Propuestas_Citas.objects.filter(
        filtro_contacto,
        Fecha__gte=inicio_dia,
        Fecha__lt=fin_dia
    ).count()

    if solicitudes_hoy >= MAX_SOLICITUDES_WEB_POR_DIA:
        messages.error(
            request,
            "Llegaste al limite de solicitudes por hoy. Intenta de nuevo manana o contacta directamente a la clinica."
        )
        return redirect(f"{reverse('landing')}#contacts")

    Propuestas_Citas.objects.create(
        Nombre=nombre,
        Email=email,
        Numero_telefonico=telefono,
        Propuesta_De_Dia=propuesta,
        Estado='Pendiente',
        Atendida=False,
        Fecha=timezone.now()
    )

    messages.success(
        request,
        "Solicitud enviada correctamente. La clinica revisara tu solicitud y se pondra en contacto contigo para confirmar la cita."
    )
    return redirect(f"{reverse('landing')}#contacts")


def separar_detalle_solicitud_web(texto):
    texto = (texto or '').strip()
    prefijo_servicio = 'Servicio solicitado: '
    separador_preferencia = ' Preferencia de fecha u horario: '
    if not texto.startswith(prefijo_servicio):
        return '', texto

    detalle = texto[len(prefijo_servicio):]
    if separador_preferencia in detalle:
        servicio, preferencia = detalle.split(separador_preferencia, 1)
        return servicio.rstrip('. '), preferencia.strip()
    return detalle.rstrip('. '), ''


def buscar_cliente_por_contacto(telefono, email):
    telefono = ''.join(digito for digito in str(telefono or '') if digito.isdigit())
    email = str(email or '').strip()
    if telefono:
        cliente = Cliente.objects.filter(n_telefono=telefono).first()
        if cliente:
            return cliente
    if email:
        return Cliente.objects.filter(email__iexact=email).first()
    return None


def buscar_servicio_solicitado_web(texto, servicios=None):
    nombre_servicio, _ = separar_detalle_solicitud_web(texto)
    if nombre_servicio:
        servicio = Servicio.objects.filter(nombre__iexact=nombre_servicio).first()
        if servicio:
            return servicio

    texto_normalizado = ' '.join(str(texto or '').casefold().split())
    catalogo = servicios if servicios is not None else Servicio.objects.all()
    for servicio in catalogo:
        nombre_normalizado = ' '.join(servicio.nombre.casefold().split())
        if nombre_normalizado and nombre_normalizado in texto_normalizado:
            return servicio
    return None


def preparar_detalle_solicitud_web(solicitud, servicios=None):
    nombre_servicio, preferencia = separar_detalle_solicitud_web(solicitud.Propuesta_De_Dia)
    servicio = buscar_servicio_solicitado_web(solicitud.Propuesta_De_Dia, servicios)
    if servicio:
        if not nombre_servicio:
            preferencia = re.sub(
                re.escape(servicio.nombre), '', solicitud.Propuesta_De_Dia or '', count=1, flags=re.IGNORECASE
            ).strip(' .,:;-')
        nombre_servicio = servicio.nombre
    solicitud.servicio_solicitado = nombre_servicio
    solicitud.preferencia_solicitada = preferencia
    return servicio


@login_required
def inbox(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    if request.GET.get('cancelar') == '1':
        for clave in (
            'cliente_temp', 'propuesta_id', 'flujo_origen', 'operativo_id',
            'servicios_seleccionados', 'duracion_sugerida', 'duracion_final',
            'cliente_id', 'fecha', 'hora',
        ):
            request.session.pop(clave, None)

    solicitudes = Propuestas_Citas.objects.filter(
        Atendida=False
    ).order_by('-Fecha', '-ID_Propuesta')
    catalogo_servicios = list(Servicio.objects.all())
    for solicitud in solicitudes:
        preparar_detalle_solicitud_web(solicitud, catalogo_servicios)

    return render(request, 'administrativo/inbox.html', {
        'solicitudes': solicitudes,
        'total_solicitudes_pendientes': solicitudes.count(),
    })


@login_required
@require_POST
def rechazar_propuesta(request, id):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    propuesta.Atendida = True
    propuesta.save()
    messages.success(request, 'La solicitud se marcó como rechazada y ya no aparecerá entre las pendientes.')

    return redirect('inbox')


@login_required
def crear_cita_desde_propuesta(request, id):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    if propuesta.Atendida:
        messages.warning(
            request,
            "Esta solicitud ya fue atendida."
        )
        return redirect('inbox')

    for clave in (
        'cliente_temp', 'propuesta_id', 'flujo_origen', 'operativo_id',
        'servicios_seleccionados', 'duracion_sugerida', 'duracion_final',
        'cliente_id', 'fecha', 'hora', 'seguimiento_cita_id',
    ):
        request.session.pop(clave, None)

    request.session['cliente_temp'] = {
        'nombre': propuesta.Nombre,
        'apellido': '',
        'telefono': propuesta.Numero_telefonico,
        'email': propuesta.Email,
    }

    request.session['propuesta_id'] = propuesta.ID_Propuesta
    request.session['flujo_origen'] = 'inbox'

    servicio_solicitado = buscar_servicio_solicitado_web(propuesta.Propuesta_De_Dia)
    if servicio_solicitado:
        servicios = [servicio_solicitado]
        request.session['servicios_seleccionados'] = [servicio_solicitado.id_servicio]
        duracion = duracion_servicios(servicios)
        request.session['duracion_sugerida'] = duracion
        request.session['duracion_final'] = duracion

    return redirect(f"{reverse('calendar')}?general=1")


@login_required
def listar_clientes(request):
    es_administrativo = usuario_es_administrativo(request.user)
    if es_administrativo:
        clientes = Cliente.objects.all()
    else:
        operativo = obtener_operativo_usuario(request)
        if not operativo:
            return redirect('home')
        clientes = Cliente.objects.filter(
            cita__operativo=operativo
        ).distinct()

    return render(request, 'clientes/listar.html', {
        'clientes': clientes,
        'puede_eliminar': es_administrativo,
        'puede_crear': es_administrativo,
    })


@login_required
def crear_cliente(request):
    if not usuario_es_administrativo(request.user):
        messages.error(request, 'Solo el personal administrativo puede registrar pacientes nuevos.')
        return redirect('listar_clientes')

    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('listar_clientes')
    else:
        form = ClienteForm()
    return render(request, 'clientes/crear.html', {'form': form})


@login_required
def editar_cliente(request, id_cliente):
    cliente = get_object_or_404(Cliente, id_cliente=id_cliente)
    if not usuario_puede_acceder_cliente(request.user, cliente):
        return redirect('home')

    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            return redirect('listar_clientes')
    else:
        form = ClienteForm(instance=cliente)
    return render(request, 'clientes/editar.html', {'form': form})


@login_required
def eliminar_cliente(request, id_cliente):
    # Eliminar un cliente puede borrar en cascada citas y expedientes clínicos.
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    cliente = get_object_or_404(Cliente, id_cliente=id_cliente)
    if request.method == 'POST':
        if Cita.objects.filter(cliente=cliente).exists() or ExpedienteClinico.objects.filter(cliente=cliente).exists():
            messages.error(
                request,
                'No se puede eliminar este paciente porque tiene citas o registros clínicos asociados.'
            )
            return redirect('listar_clientes')
        cliente.delete()
        return redirect('listar_clientes')
    return render(request, 'clientes/eliminar_confirmar.html', {'cliente': cliente})


def servicios(request):
    return render(request, 'servicios.html')


def seleccionar_servicios(request):

    if request.method == 'POST':

        form = ServicioSelectForm(request.POST)

        if form.is_valid():

            servicios = form.cleaned_data['servicios']

            servicios_ids = [s.id_servicio for s in servicios]

            duracion_sugerida = duracion_servicios(servicios)
            try:
                duracion_solicitada = int(request.POST.get('duracion_final') or duracion_sugerida)
            except (TypeError, ValueError):
                duracion_solicitada = duracion_sugerida
            duracion_final = duracion_solicitada if duracion_solicitada > 0 else duracion_sugerida

            # 🔥 guardar selección en sesión
            request.session['servicios_seleccionados'] = servicios_ids
            request.session['duracion_sugerida'] = duracion_sugerida
            request.session['duracion_final'] = duracion_final

            request.session.modified = True

            # 🔥 IMPORTANTE: flujo directo a operativo + calendario
            return redirect('seleccionar_operativo')

    else:
        form = ServicioSelectForm()

    servicios = list(
        Servicio.objects.all().values(
            'id_servicio',
            'nombre',
            'duracion'
        )
    )

    return render(request, 'citas/seleccionar_servicios.html', {
        'form': form,
        'servicios': servicios
    })


@login_required
def crear_cita_cliente(request):

    if not usuario_es_administrativo(request.user):
        messages.info(
            request,
            'Para agendar, selecciona un paciente existente asociado al personal elegido.'
        )
        return redirect(f"{reverse('calendar')}?propio=1")

    if request.session.get('flujo_origen') == 'inbox' and 'cliente_temp' in request.session:
        return redirect('seleccionar_fecha_hora')
    servicios_ids = request.session.get('servicios_seleccionados')

    if not servicios_ids:
        return redirect('seleccionar_servicios')

    servicios = Servicio.objects.filter(id_servicio__in=servicios_ids)

    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            cliente = form.save()
            request.session['cliente_id'] = cliente.id_cliente
            return redirect('seleccionar_operativo')
    else:
        form = ClienteForm()

    return render(request, 'citas/crear_cita_cliente.html', {'form': form, 'servicios': servicios})


def seleccionar_operativo(request):
    operativo_actual = obtener_operativo_usuario(request)

    if (
        usuario_es_administrativo(request.user)
        and not request.session.get('cliente_temp')
        and not request.session.get('servicios_seleccionados')
        and not request.session.get('seguimiento_cita_id')
    ):
        return redirect(f"{reverse('calendar')}?general=1")

    if request.method == 'POST':
        form = OperativoSelectForm(
            request.POST,
            operativo_inicial=operativo_actual
        )
        if form.is_valid():
            operativo_id = form.cleaned_data['operativo'].id_operativo
            request.session['operativo_id'] = operativo_id
            request.session['flujo_origen'] = 'calendar'
            return redirect('calendar')
    else:
        form = OperativoSelectForm(operativo_inicial=operativo_actual)

    if request.session.get('cliente_temp'):
        volver_url = reverse('inbox')
    elif request.session.get('servicios_seleccionados'):
        volver_url = reverse('seleccionar_servicios')
    elif usuario_es_administrativo(request.user):
        volver_url = reverse('administrativo')
    elif operativo_actual:
        volver_url = reverse('operativo')
    else:
        volver_url = reverse('home')

    return render(request, 'citas/seleccionar_operativo.html', {
        'form': form,
        'operativo_actual': operativo_actual,
        'volver_url': volver_url,
    })



def agenda_operativo(request):

    operativo_id = request.session.get('operativo_id')

    if not operativo_id:
        return redirect('seleccionar_operativo')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)

    fecha_str = request.GET.get('fecha')
    try:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date() if fecha_str else timezone.localdate()
    except (TypeError, ValueError):
        fecha = timezone.localdate()
        error = 'La fecha seleccionada no es válida.'
    else:
        error = None

    if not error and fecha < timezone.localdate():
        error = 'No se pueden agendar citas en fechas pasadas.'
    elif not error and not fecha_en_rango_agenda(fecha):
        error = 'Solo se pueden agendar citas dentro del próximo año.'
    elif not error and not fecha_laboral(fecha):
        error = 'La clínica no atiende los lunes.'

    if error:
        return render(request, 'citas/agenda_operativo.html', {
            'operativo': operativo,
            'fecha': fecha,
            'horas': [],
            'error': error,
            'fecha_minima': timezone.localdate(),
            'fecha_maxima': obtener_rango_agenda()[1],
        })

    duracion = duracion_para_sesion(request)
    if not duracion:
        return redirect('seleccionar_servicios')

    horas = []
    now = timezone.localtime()

    for hora in range(HORA_APERTURA_CLINICA.hour, HORA_CIERRE_CLINICA.hour):

        bloques = []

        for minuto in range(0, 60):

            inicio = datetime.combine(fecha, time(hora, minuto))
            inicio = timezone.make_aware(inicio)

            es_pasado = fecha < now.date() or (fecha == now.date() and inicio < now)
            dentro_horario = cita_en_horario_laboral(inicio, duracion)

            libre = not hay_conflicto_intervalo(
                operativo,
                fecha,
                inicio,
                duracion
            )

            bloques.append({
                "hora": f"{hora:02d}:{minuto:02d}",
                "inicio": inicio,
                "pasado": es_pasado,
                "libre": libre and not es_pasado and dentro_horario,
            })

        horas.append({
            "hora_24": f"{hora:02d}:00",
            "hora_12": datetime.strptime(
                f"{hora:02d}:00",
                "%H:%M"
            ).strftime("%I:%M %p"),
            "bloques": bloques
        })

    return render(request, 'citas/agenda_operativo.html', {
        'operativo': operativo,
        'fecha': fecha,
        'horas': horas,
        'fecha_minima': timezone.localdate(),
        'fecha_maxima': obtener_rango_agenda()[1],
    })


@require_GET
def bloques_hora_ajax(request):

    operativo_id = request.session.get('operativo_id')
    fecha_str = request.GET.get('fecha')

    try:
        hora = int(request.GET.get('hora'))
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return JsonResponse({'error': 'La fecha o la hora seleccionada no es válida.'}, status=400)

    if not operativo_id or not (HORA_APERTURA_CLINICA.hour <= hora < HORA_CIERRE_CLINICA.hour):
        return JsonResponse({'error': 'El horario seleccionado no está disponible.'}, status=400)

    if fecha < timezone.localdate() or not fecha_en_rango_agenda(fecha) or not fecha_laboral(fecha):
        return JsonResponse({'error': 'La fecha seleccionada no está disponible para citas.'}, status=400)

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    duracion = duracion_para_sesion(request)
    if not duracion:
        return JsonResponse({'error': 'Selecciona al menos un servicio.'}, status=400)

    bloques = []

    now = timezone.localtime()

    for minuto in range(0, 60):

        inicio = datetime.combine(fecha, time(hora, minuto))
        inicio = timezone.make_aware(inicio)

        es_pasado = fecha < now.date() or (fecha == now.date() and inicio < now)
        dentro_horario = cita_en_horario_laboral(inicio, duracion)

        libre = not hay_conflicto_intervalo(
            operativo,
            fecha,
            inicio,
            duracion
        )

        bloques.append({
            "hora": f"{hora:02d}:{minuto:02d}",
            "libre": libre and not es_pasado and dentro_horario,
            "pasado": es_pasado,
            "puede_iniciar": libre and not es_pasado and dentro_horario,
        })

    return JsonResponse({"bloques": bloques})


def hay_conflicto_intervalo(operativo, fecha, inicio, duracion, excluir=None):

    citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__date=fecha,
    ).filter(
        Q(estado__nombre_estado='Programada') | Q(estado__isnull=True)
    )

    if excluir:
        citas = citas.exclude(
            id_cita=excluir
        )

    fin_nuevo = inicio + timedelta(minutes=duracion)

    for c in citas:
        inicio_cita = timezone.localtime(c.fechahora)
        fin_cita = inicio_cita + timedelta(minutes=c.duracion_minutos or 30)

        if inicio < fin_cita and fin_nuevo > inicio_cita:
            return True

    return False


def carga_operativos_semana_actual():
    """Resume las citas asignadas en la semana actual para ordenar sugerencias."""
    hoy = timezone.localdate()
    inicio_semana_dia = hoy - timedelta(days=hoy.weekday())
    inicio_semana = timezone.make_aware(datetime.combine(inicio_semana_dia, time.min))
    fin_semana = inicio_semana + timedelta(days=7)
    operativos = list(Operativo.objects.select_related('personal').order_by(
        'personal__nombre', 'personal__apellido'
    ))
    cargas = {operativo.id_operativo: {'minutos': 0, 'citas': 0} for operativo in operativos}
    citas = Cita.objects.filter(
        operativo_id__in=list(cargas),
        fechahora__gte=inicio_semana,
        fechahora__lt=fin_semana,
    ).filter(
        Q(estado__isnull=True) | ~Q(estado__nombre_estado='Cancelada')
    )
    for cita in citas:
        carga = cargas[cita.operativo_id]
        carga['minutos'] += cita.duracion_minutos or 30
        carga['citas'] += 1

    resultados = [
        {
            'operativo': operativo,
            'minutos_ocupados': cargas[operativo.id_operativo]['minutos'],
            'cantidad_citas': cargas[operativo.id_operativo]['citas'],
        }
        for operativo in operativos
    ]
    rotacion = hoy.toordinal() % max(len(resultados), 1)
    resultados.sort(key=lambda item: (
        item['minutos_ocupados'],
        (item['operativo'].id_operativo + rotacion) % max(len(resultados), 1),
    ))
    return inicio_semana_dia, inicio_semana_dia + timedelta(days=6), resultados


def agenda_general_administrativa(request):
    actualizar_citas_vencidas()
    hoy = timezone.localdate()
    inicio_semana, fin_semana, resultados = carga_operativos_semana_actual()
    solicitud_web = None
    propuesta_id = request.session.get('propuesta_id')
    if request.session.get('flujo_origen') == 'inbox' and propuesta_id:
        solicitud_web = Propuestas_Citas.objects.filter(
            ID_Propuesta=propuesta_id,
            Atendida=False,
        ).first()
        if solicitud_web:
            preparar_detalle_solicitud_web(solicitud_web)
    return render(request, 'citas/agenda_general_admin.html', {
        'hoy': hoy,
        'inicio_semana': inicio_semana,
        'fin_semana': fin_semana,
        'resultados': resultados,
        'solicitud_web': solicitud_web,
    })


@login_required
def calendar_view(request):
    es_administrativo = usuario_es_administrativo(request.user)
    operativo_usuario = obtener_operativo_usuario(request) if not es_administrativo else None
    if not es_administrativo and not operativo_usuario:
        return redirect('home')

    if es_administrativo and request.method == 'POST':
        accion_agenda = request.POST.get('accion_agenda')
        if accion_agenda == 'abrir_agenda_operativo':
            try:
                operativo_id = int(request.POST.get('operativo_id'))
                operativo = Operativo.objects.get(id_operativo=operativo_id)
            except (TypeError, ValueError, Operativo.DoesNotExist):
                messages.error(request, 'El personal seleccionado ya no está disponible.')
                return redirect(f"{reverse('calendar')}?general=1")
            request.session['operativo_id'] = operativo.id_operativo
            if request.session.get('flujo_origen') != 'inbox':
                for clave in (
                    'servicios_seleccionados',
                    'duracion_sugerida',
                    'duracion_final',
                ):
                    request.session.pop(clave, None)
            fecha = timezone.localdate().isoformat()
            return redirect(f"{reverse('calendar')}?fecha={fecha}")

    if request.method == 'POST' and request.POST.get('cambiar_operativo'):
        try:
            nuevo_operativo_id = int(request.POST.get('operativo_id'))
        except (TypeError, ValueError):
            messages.error(request, 'Selecciona un operativo válido.')
            return redirect('calendar')
        if not Operativo.objects.filter(id_operativo=nuevo_operativo_id).exists():
            messages.error(request, 'El operativo seleccionado ya no está disponible.')
            return redirect('calendar')
        request.session['operativo_id'] = nuevo_operativo_id
        fecha_form = request.POST.get('fecha', '')
        try:
            fecha_form = datetime.strptime(fecha_form, '%Y-%m-%d').date().isoformat()
        except (TypeError, ValueError):
            fecha_form = ''
        destino = reverse('calendar')
        parametros = {}
        if fecha_form:
            parametros['fecha'] = fecha_form
        try:
            cita_id_origen = int(request.POST.get('cita_id') or 0)
        except (TypeError, ValueError):
            cita_id_origen = 0
        if cita_id_origen > 0:
            parametros['cita_id'] = cita_id_origen
        if parametros:
            destino = f'{destino}?{urlencode(parametros)}'
        return redirect(destino)

    if es_administrativo and (
        request.GET.get('general') == '1'
        or not request.session.get('operativo_id')
    ):
        return agenda_general_administrativa(request)

    if es_administrativo:
        operativo_id = request.session.get('operativo_id')
        if not operativo_id:
            return redirect('seleccionar_operativo')
    else:
        if request.GET.get('propio') == '1':
            request.session['operativo_id'] = operativo_usuario.id_operativo
        operativo_id = request.session.get('operativo_id') or operativo_usuario.id_operativo

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    if es_administrativo and not usuario_puede_acceder_operativo(request.user, operativo):
        return redirect('home')

    cita_seguimiento = None
    servicios_seguimiento_ids = []
    seguimiento_id = request.session.get('seguimiento_cita_id')
    if seguimiento_id:
        try:
            seguimiento_id = int(seguimiento_id)
        except (TypeError, ValueError):
            seguimiento_id = None

        cita_seguimiento = Cita.objects.select_related(
            'cliente', 'operativo', 'estado'
        ).prefetch_related('servicios').filter(id_cita=seguimiento_id).first() if seguimiento_id else None

        if (
            not cita_seguimiento
            or not cita_seguimiento.estado
            or cita_seguimiento.estado.nombre_estado != 'Atendida'
            or not usuario_puede_acceder_cita(request.user, cita_seguimiento)
        ):
            limpiar_contexto_seguimiento(request)
            messages.error(request, 'No se pudo iniciar el seguimiento. Revisa que la cita anterior esté atendida.')
            return redirect('home')

        servicios_seguimiento_ids = [
            servicio.id_servicio for servicio in cita_seguimiento.servicios.all()
        ]

    es_agenda_propia = es_administrativo or usuario_puede_acceder_operativo(request.user, operativo)
    if es_agenda_propia:
        actualizar_citas_vencidas(operativo=operativo)
    fecha_minima, fecha_maxima = obtener_rango_agenda()

    fecha_str = request.GET.get('fecha')
    try:
        base_date = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else timezone.localdate()
    except (TypeError, ValueError):
        base_date = timezone.localdate()

    try:
        cita_destacada_id = int(request.GET.get('cita_id') or 0) or None
    except (TypeError, ValueError):
        cita_destacada_id = None

    offset = int(request.GET.get("offset", 0))

    # 🔥 aplicar desplazamiento de semanas
    base_date = base_date + timedelta(weeks=offset)

    if base_date > fecha_maxima:
        base_date = fecha_maxima

    # 🔥 construir semana (lunes a domingo)
    inicio_semana = base_date - timedelta(days=base_date.weekday())
    semana_maxima = fecha_maxima - timedelta(days=fecha_maxima.weekday())

    dias = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        dias.append(dia)

    # 🔥 horas visibles
    horas = list(range(HORA_APERTURA_CLINICA.hour, HORA_CIERRE_CLINICA.hour))

    # 🔥 citas del operativo en la semana
    fin_semana = inicio_semana + timedelta(days=7)

    citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__date__gte=inicio_semana,
        fechahora__date__lt=fin_semana
    )

    servicios = Servicio.objects.all()

    eventos = []

    for c in citas:
        inicio = timezone.localtime(c.fechahora)
        fin = inicio + timedelta(minutes=c.duracion_minutos or 30)

        inicio_minutos = (inicio.hour - 8) * 60 + inicio.minute
        estado_nombre = c.estado.nombre_estado if c.estado else "Programada"
        estado_clase = {
            "Pendiente": "event-pendiente",
            "Atendida": "event-atendida",
            "Cancelada": "event-cancelada",
        }.get(estado_nombre, "event-programada")

        eventos.append({
            "id": c.id_cita if es_agenda_propia else "",
            "puede_editar": es_agenda_propia,
            "destacada": es_agenda_propia and c.id_cita == cita_destacada_id,
            "es_seguimiento": bool(c.cita_anterior_id),
            "inicio": inicio,
            "fin": fin,
            "cliente": f"{c.cliente.nombre} {c.cliente.apellido}" if es_agenda_propia else "Horario ocupado",
            "dia": inicio.date(),

            "duracion": c.duracion_minutos,

            "top": inicio_minutos * 2,  # 1px por minuto
            "height": (c.duracion_minutos or 30) * 2,

            "hora_texto": inicio.strftime("%H:%M"),
            "hora_fin": fin.strftime("%H:%M"),
            "estado": estado_nombre,
            "estado_clase": estado_clase,
        })

    propuesta_id = request.session.get('propuesta_id')
    solicitud_web = None
    if es_administrativo and request.session.get('flujo_origen') == 'inbox' and propuesta_id:
        solicitud_web = Propuestas_Citas.objects.filter(
            ID_Propuesta=propuesta_id,
            Atendida=False,
        ).first()
        if solicitud_web:
            preparar_detalle_solicitud_web(solicitud_web)

    cliente_solicitud = None
    cliente_solicitud_existente = None
    if solicitud_web and request.session.get('cliente_temp'):
        cliente_temp = request.session['cliente_temp']
        cliente_solicitud_existente = buscar_cliente_por_contacto(
            cliente_temp.get('telefono'), cliente_temp.get('email')
        )
        nombre_completo = str(cliente_temp.get('nombre') or '').strip().split()
        cliente_solicitud = {
            'nombre': nombre_completo[0] if nombre_completo else '',
            'apellido': ' '.join(nombre_completo[1:]) if len(nombre_completo) > 1 else '',
            'telefono': cliente_temp.get('telefono', ''),
            'email': cliente_temp.get('email', ''),
        }

    return render(request, "citas/calendar.html", {
        "operativo": operativo,
        "es_agenda_propia": es_agenda_propia,
        "puede_cambiar_operativo": True,
        "es_administrativo": es_administrativo,
        "cita_seguimiento": cita_seguimiento,
        "solicitud_web": solicitud_web,
        "cliente_solicitud": cliente_solicitud,
        "cliente_solicitud_existente": cliente_solicitud_existente,
        "flujo_inbox": bool(solicitud_web),
        "servicios_seguimiento_ids": servicios_seguimiento_ids,
        "operativos_disponibles": Operativo.objects.select_related('personal').order_by(
            'personal__nombre', 'personal__apellido'
        ),
        "dias": dias,
        "horas": horas,
        "eventos": eventos,
        "cita_destacada_id": cita_destacada_id,
        "offset": offset,
        "fecha_actual": base_date,
        "fecha_minima": fecha_minima,
        "fecha_maxima": fecha_maxima,
        "fecha_anterior": base_date - timedelta(weeks=1),
        "fecha_siguiente": base_date + timedelta(weeks=1),
        "puede_semana_anterior": True,
        "puede_semana_siguiente": inicio_semana < semana_maxima,
        "Clientes": (
            Cliente.objects.all()
            if es_administrativo
            else Cliente.objects.filter(cita__operativo=operativo_usuario).distinct()
        ),
        "duracion": 30,
        "hora_texto": "",
        "hora_fin": "",
        "servicios": servicios,
        "servicios_preseleccionados": request.session.get('servicios_seleccionados', []),
    })



def seleccionar_fecha_hora(request):
    servicios = obtener_servicios_validos(
        request.session.get('servicios_seleccionados')
    )
    operativo_id = request.session.get('operativo_id')
    if not servicios or not operativo_id:
        return redirect('seleccionar_servicios')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    duracion = duracion_para_sesion(request) or duracion_servicios(servicios)
    form = FechaHoraForm(
        request.POST or None,
        duracion=duracion,
    )

    if request.method == 'POST' and form.is_valid():
        fecha = form.cleaned_data['fecha']
        hora = form.cleaned_data['hora']
        inicio = parsear_inicio_cita(fecha.isoformat(), hora)
        error = validar_programacion_cita(operativo, inicio, duracion)
        if error:
            form.add_error('hora', error)
        else:
            request.session['fecha'] = str(fecha)
            request.session['hora'] = str(hora)
            return redirect('confirmar_cita')

    return render(request, 'citas/seleccionar_fecha_hora.html', {'form': form})


def seleccionar_bloque(request):

    operativo_id = request.session.get('operativo_id')

    if not operativo_id:
        return redirect('seleccionar_operativo')

    fecha = request.GET.get('fecha')
    bloque = request.GET.get('bloque')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    duracion = duracion_para_sesion(request)
    if not duracion:
        return redirect('seleccionar_servicios')

    inicio = parsear_inicio_cita(fecha, bloque)
    error = validar_programacion_cita(operativo, inicio, duracion)
    if error:
        messages.error(request, error)
        return redirect('agenda_operativo')

    # guardar en sesión (ESTO ES LO IMPORTANTE)
    request.session['fecha'] = fecha
    request.session['hora'] = bloque
    request.session['operativo_id'] = operativo_id

    return redirect('confirmar_cita')



@login_required
@require_POST
def crear_cita_ajax(request):

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": True, "mensaje": "La solicitud no contiene datos válidos."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": True, "mensaje": "La solicitud no contiene datos válidos."}, status=400)

    propuesta_id = request.session.get('propuesta_id') if request.session.get('flujo_origen') == 'inbox' else None
    es_solicitud_web = propuesta_id is not None
    if es_solicitud_web and not usuario_es_administrativo(request.user):
        return JsonResponse({"error": True, "mensaje": "Solo el personal administrativo puede programar solicitudes web."}, status=403)

    servicios = obtener_servicios_validos(data.get("servicios"))
    if not servicios:
        return JsonResponse({"error": True, "mensaje": "Selecciona servicios válidos para la cita."}, status=400)

    try:
        duracion = int(data.get("duracion") or 0)
        cliente_id = int(data["cliente_id"]) if data.get("cliente_id") else None
    except (TypeError, ValueError):
        return JsonResponse({"error": True, "mensaje": "La duración o el cliente no son válidos."}, status=400)

    inicio = parsear_inicio_cita(data.get("fecha"), data.get("hora"))
    nombre = normalizar_nombre_persona(str(data.get("nombre") or ""))
    apellido = normalizar_nombre_persona(str(data.get("apellido") or ""))
    telefono = str(data.get("telefono") or "").strip()
    email = str(data.get("email") or "").strip()
    sexo = str(data.get("sexo") or "").strip()

    operativo_id = request.session.get("operativo_id")
    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    if not usuario_puede_agendar_para_operativo(request.user, operativo):
        return JsonResponse({
            "error": True,
            "mensaje": "No tienes permiso para agendar con este personal."
        }, status=403)

    cita_anterior = None
    seguimiento_id = request.session.get('seguimiento_cita_id')
    if seguimiento_id:
        try:
            seguimiento_id = int(seguimiento_id)
        except (TypeError, ValueError):
            limpiar_contexto_seguimiento(request)
            return JsonResponse({
                "error": True,
                "mensaje": "El seguimiento ya no es válido. Inicia el proceso nuevamente desde la cita anterior."
            }, status=400)

        cita_anterior = Cita.objects.select_related('estado', 'cliente').filter(
            id_cita=seguimiento_id
        ).first()
        if (
            not cita_anterior
            or not cita_anterior.estado
            or cita_anterior.estado.nombre_estado != 'Atendida'
            or not usuario_puede_acceder_cita(request.user, cita_anterior)
        ):
            limpiar_contexto_seguimiento(request)
            return JsonResponse({
                "error": True,
                "mensaje": "La cita anterior ya no puede utilizarse para programar un seguimiento."
            }, status=400)

        if not cliente_id or cliente_id != cita_anterior.cliente_id:
            return JsonResponse({
                "error": True,
                "mensaje": "El seguimiento debe conservar el mismo paciente de la cita anterior."
            }, status=400)

        servicios_asignados = ServicioOperativo.objects.filter(
            operativo=operativo,
            servicio_id__in=[servicio.id_servicio for servicio in servicios],
        ).values('servicio_id').distinct().count()
        if servicios_asignados != len(servicios):
            return JsonResponse({
                "error": True,
                "mensaje": "El personal seleccionado no tiene asignados todos los servicios del seguimiento."
            }, status=400)

    es_administrativo = usuario_es_administrativo(request.user)
    if not cliente_id and not es_administrativo:
        return JsonResponse({
            "error": True,
            "mensaje": "El personal operativo solo puede agendar para pacientes existentes asociados al personal seleccionado."
        }, status=403)

    cliente = get_object_or_404(Cliente, id_cliente=cliente_id) if cliente_id else None
    if (
        cliente
        and not es_administrativo
        and not usuario_puede_acceder_cliente(request.user, cliente)
    ):
        return JsonResponse({
            "error": True,
            "mensaje": "Solo puedes seleccionar pacientes asociados a tus citas anteriores."
        }, status=403)

    error = validar_programacion_cita(operativo, inicio, duracion)
    if error:
        return JsonResponse({"error": True, "mensaje": error}, status=400)

    if not cliente_id:
        if not nombre or not apellido or not telefono or not sexo:
            return JsonResponse({
                "error": True,
                "mensaje": "Nombre, apellido, teléfono y sexo son obligatorios."
            }, status=400)

        if not nombre_persona_valido(nombre) or not nombre_persona_valido(apellido):
            return JsonResponse({
                "error": True,
                "mensaje": "Nombre y apellido deben contener solo letras y tener un formato válido."
            }, status=400)

        if not telefono.isdigit() or len(telefono) != 8:
            return JsonResponse({
                "error": True,
                "mensaje": "El teléfono debe tener exactamente 8 números."
            }, status=400)

        if email:
            try:
                validate_email(email)
            except ValidationError:
                return JsonResponse({
                    "error": True,
                    "mensaje": "Ingresa un correo válido o deja el campo vacío."
                }, status=400)

    if not cliente and telefono:
        cliente = Cliente.objects.filter(n_telefono=telefono).first()
    if not cliente and email:
        cliente = Cliente.objects.filter(email=email).first()

    estado_programada = Estado.objects.filter(nombre_estado__iexact="Programada").first()
    if not estado_programada:
        return JsonResponse({"error": True, "mensaje": "No está configurado el estado Programada."}, status=500)

    with transaction.atomic():
        propuesta_web = None
        if es_solicitud_web:
            propuesta_web = Propuestas_Citas.objects.select_for_update().filter(
                ID_Propuesta=propuesta_id,
                Atendida=False,
            ).first()
            if not propuesta_web:
                return JsonResponse({
                    "error": True,
                    "mensaje": "Esta solicitud ya fue atendida o ya no está disponible. Vuelve al inbox para actualizar la lista."
                }, status=409)

        # Revisa de nuevo antes de guardar y evita crear clientes si el horario dejó de estar libre.
        error = validar_programacion_cita(operativo, inicio, duracion)
        if error:
            return JsonResponse({"error": True, "mensaje": error}, status=400)

        if not cliente:
            cliente = Cliente.objects.create(
                nombre=nombre,
                apellido=apellido,
                sexo=sexo,
                n_telefono=telefono,
                email=email
            )

        cita = Cita.objects.create(
            fechahora=inicio,
            cliente=cliente,
            operativo=operativo,
            duracion_minutos=duracion,
            estado=estado_programada,
            cita_anterior=cita_anterior,
        )
        cita.servicios.set(servicios)
        if propuesta_web:
            propuesta_web.Atendida = True
            propuesta_web.save(update_fields=['Atendida'])
        registrar_cita_creada(request, cita)

    es_seguimiento = cita_anterior is not None
    if es_seguimiento:
        limpiar_contexto_seguimiento(request)
    if es_solicitud_web:
        for clave in (
            'cliente_temp', 'propuesta_id', 'flujo_origen',
            'servicios_seleccionados', 'duracion_sugerida', 'duracion_final',
            'cliente_id', 'fecha', 'hora',
        ):
            request.session.pop(clave, None)

    return JsonResponse({
        "ok": True,
        "id_cita": cita.id_cita,
        "cliente_id": cliente.id_cliente,
        "es_seguimiento": es_seguimiento,
        "es_solicitud_web": es_solicitud_web,
    })


@login_required
@require_POST
def editar_cita_ajax(request):

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": True, "mensaje": "La solicitud no contiene datos válidos."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": True, "mensaje": "La solicitud no contiene datos válidos."}, status=400)

    try:
        cita_id = int(data.get("id"))
        duracion = int(data.get("duracion") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"error": True, "mensaje": "La cita o duración no son válidas."}, status=400)

    cita = get_object_or_404(Cita, id_cita=cita_id)
    if not usuario_puede_acceder_cita(request.user, cita):
        return JsonResponse({
            "error": True,
            "mensaje": "No tienes permiso para modificar esta cita."
        }, status=403)

    estado_actual = cita.estado.nombre_estado if cita.estado else "Programada"
    if estado_actual != "Programada":
        return JsonResponse({
            "error": True,
            "mensaje": "Solo se pueden modificar citas programadas."
        }, status=400)

    servicios = obtener_servicios_validos(data.get("servicios"))
    if not servicios:
        return JsonResponse({"error": True, "mensaje": "Selecciona servicios válidos para la cita."}, status=400)
    inicio = parsear_inicio_cita(data.get("fecha"), data.get("hora"))
    error = validar_programacion_cita(cita.operativo, inicio, duracion, excluir=cita.id_cita)
    if error:
        return JsonResponse({"error": True, "mensaje": error}, status=400)

    with transaction.atomic():
        error = validar_programacion_cita(cita.operativo, inicio, duracion, excluir=cita.id_cita)
        if error:
            return JsonResponse({"error": True, "mensaje": error}, status=400)
        cita.fechahora = inicio
        cita.duracion_minutos = duracion
        cita.save()
        cita.servicios.set(servicios)

    return JsonResponse({
        "ok": True
    })


@login_required
@require_POST
def cancelar_cita_ajax(request):
    data = json.loads(request.body)
    try:
        motivo = limpiar_motivo(data.get("motivo", ""))
    except ValueError as exc:
        return JsonResponse({
            "error": True,
            "mensaje": str(exc)
        })

    cita = get_object_or_404(
        Cita,
        id_cita=data["id"]
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return JsonResponse({
            "error": True,
            "mensaje": "No tienes permiso para cancelar esta cita."
        }, status=403)

    estado_actual = cita.estado.nombre_estado if cita.estado else ""

    if estado_actual == "Atendida":
        return JsonResponse({
            "error": True,
            "mensaje": "No se puede cancelar una cita que ya fue atendida."
        })

    if estado_actual == "Cancelada":
        return JsonResponse({
            "error": True,
            "mensaje": "La cita ya estaba cancelada."
        })

    estado_cancelada = get_object_or_404(
        Estado,
        nombre_estado="Cancelada"
    )

    cita.estado = estado_cancelada
    cita.save()
    registrar_accion(
        request,
        "Cancelar cita",
        motivo,
        cita=cita
    )

    return JsonResponse({
        "ok": True
    })


@login_required
@require_POST
def cambiar_estado_cita_ajax(request):
    data = json.loads(request.body)

    cita = get_object_or_404(
        Cita.objects.select_related('estado', 'operativo'),
        id_cita=data["id"]
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return JsonResponse({
            "error": True,
            "mensaje": "No tienes permiso para cambiar el estado de esta cita."
        }, status=403)

    nuevo_estado = (data.get("estado") or "").strip()
    estados_validos = ["Programada", "Atendida", "Cancelada"]

    if nuevo_estado not in estados_validos:
        return JsonResponse({
            "error": True,
            "mensaje": "Estado no valido."
        })

    estado_actual = (
        cita.estado.nombre_estado.strip()
        if cita.estado and cita.estado.nombre_estado
        else "Programada"
    )

    if estado_actual == "Atendida":
        return JsonResponse({
            "error": True,
            "mensaje": "Una cita atendida ya no puede cambiar de estado."
        })

    if estado_actual == "Cancelada" and nuevo_estado != "Programada":
        return JsonResponse({
            "error": True,
            "mensaje": "Una cita cancelada solo puede volver a programada."
        })

    if estado_actual == "Programada" and nuevo_estado == "Programada":
        return JsonResponse({
            "ok": True
        })

    motivo = ""

    if nuevo_estado == "Cancelada":
        try:
            motivo = limpiar_motivo(data.get("motivo", ""))
        except ValueError as exc:
            return JsonResponse({
                "error": True,
                "mensaje": str(exc)
            })

    if nuevo_estado == "Atendida":
        inicio = timezone.localtime(cita.fechahora)

        if inicio > timezone.localtime():
            return JsonResponse({
                "error": True,
                "mensaje": "No se puede marcar como atendida una cita futura."
            })

    if estado_actual == "Cancelada" and nuevo_estado == "Programada":
        inicio = timezone.localtime(cita.fechahora)

        if inicio < timezone.localtime():
            return JsonResponse({
                "error": True,
                "mensaje": "No se puede volver a programar una cita en una fecha pasada."
            })

        if not fecha_en_rango_agenda(inicio.date()):
            return JsonResponse({
                "error": True,
                "mensaje": "No se puede volver a programar fuera del limite de un año."
            })

        if not fecha_laboral(inicio.date()):
            return JsonResponse({
                "error": True,
                "mensaje": "La clinica no atiende los lunes."
            })

        if not cita_en_horario_laboral(inicio, cita.duracion_minutos or 30):
            return JsonResponse({
                "error": True,
                "mensaje": "La cita no puede quedar fuera del horario de atencion."
            })

        if hay_conflicto_intervalo(
            cita.operativo,
            inicio.date(),
            inicio,
            cita.duracion_minutos or 30,
            excluir=cita.id_cita
        ):
            return JsonResponse({
                "error": True,
                "mensaje": "No se puede programar porque se solapa con otra cita."
            })

    estado = Estado.objects.filter(
        nombre_estado__iexact=nuevo_estado
    ).first()

    if not estado:
        return JsonResponse({
            "error": True,
            "mensaje": "No existe ese estado de cita en la base de datos."
        })

    cita.estado = estado
    cita.save()

    if nuevo_estado == "Cancelada":
        registrar_accion(
            request,
            "Cancelar cita",
            motivo,
            cita=cita
        )
    elif nuevo_estado == "Atendida":
        registrar_accion(
            request,
            "Marcar cita atendida",
            "Cita marcada como atendida.",
            cita=cita
        )
    elif nuevo_estado == "Programada":
        registrar_accion(
            request,
            "Reprogramar cita",
            "Cita cancelada devuelta a programada.",
            cita=cita
        )

    return JsonResponse({
        "ok": True,
        "estado": nuevo_estado
    })


@login_required
def obtener_cita_ajax(request, id_cita):

    cita = get_object_or_404(
        Cita,
        id_cita=id_cita
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return JsonResponse({
            "error": True,
            "mensaje": "No tienes permiso para consultar esta cita."
        }, status=403)

    servicios = []

    for s in cita.servicios.all():

        servicios.append({
            "id": s.id_servicio,
            "nombre": s.nombre,
            "duracion": s.duracion
        })
        

    todos_servicios = []

    for s in Servicio.objects.all():

        todos_servicios.append({
            "id": s.id_servicio,
            "nombre": s.nombre,
            "duracion": s.duracion
        })
         

    return JsonResponse({

        "id": cita.id_cita,

        "fecha":
            timezone.localtime(
                cita.fechahora
            ).strftime("%Y-%m-%d"),

        "hora":
            timezone.localtime(
                cita.fechahora
            ).strftime("%H:%M"),

        "duracion":
            cita.duracion_minutos,

        "cliente":
            f"{cita.cliente.nombre} {cita.cliente.apellido}",

        "telefono":
            cita.cliente.n_telefono,

        "email":
            cita.cliente.email,

        "estado":
            cita.estado.nombre_estado if cita.estado else "Programada",

        "servicios": servicios,

        "todos_servicios": todos_servicios,

    })


@login_required
def buscar_clientes(request):

    q = request.GET.get("q", "")

    if usuario_es_administrativo(request.user):
        clientes = Cliente.objects.all()
    else:
        operativo = obtener_operativo_usuario(request)
        if not operativo:
            return JsonResponse({
                "error": True,
                "mensaje": "No tienes permiso para consultar pacientes."
            }, status=403)
        clientes = Cliente.objects.filter(cita__operativo=operativo).distinct()

    clientes = clientes.filter(
        Q(nombre__icontains=q) |
        Q(apellido__icontains=q)
    )[:10]

    data = []

    for c in clientes:

        data.append({
            "id": c.id_cliente,
            "nombre": c.nombre,
            "apellido": c.apellido,
            "telefono": c.n_telefono,
            "email": c.email,
        })

    return JsonResponse({
        "clientes": data
    })


def iniciar_cita(request):
    request.session['flujo_origen'] = 'normal'
    return redirect('seleccionar_servicios')


@login_required
def confirmar_cita(request):
    servicios_ids = request.session.get('servicios_seleccionados')
    cliente_id = request.session.get('cliente_id')
    operativo_id = request.session.get('operativo_id')
    fecha_str = request.session.get('fecha')
    hora_str = request.session.get('hora')

    if not all([servicios_ids, operativo_id, fecha_str, hora_str]):
        return redirect('seleccionar_servicios')

    servicios = obtener_servicios_validos(servicios_ids)
    if not servicios:
        messages.error(request, 'La selección de servicios ya no es válida. Selecciónalos de nuevo.')
        return redirect('seleccionar_servicios')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    if not usuario_puede_agendar_para_operativo(request.user, operativo):
        messages.error(request, 'No tienes permiso para agendar con este personal.')
        return redirect('home')

    fechahora = parsear_inicio_cita(fecha_str, hora_str)
    if not fechahora:
        messages.error(request, 'La fecha u hora seleccionada ya no es válida. Elige otro horario.')
        return redirect('seleccionar_fecha_hora')

    duracion_final = duracion_para_sesion(request) or duracion_servicios(servicios)
    error = validar_programacion_cita(operativo, fechahora, duracion_final)
    cliente_temp = request.session.get('cliente_temp')

    if cliente_temp:
        if not usuario_es_administrativo(request.user):
            messages.error(request, 'Solo el personal administrativo puede registrar pacientes nuevos.')
            return redirect('home')
        nombre_completo = str(cliente_temp.get('nombre') or '').strip().split()
        cliente = {
            'nombre': nombre_completo[0] if nombre_completo else '',
            'apellido': ' '.join(nombre_completo[1:]) if len(nombre_completo) > 1 else '',
            'n_telefono': cliente_temp.get('telefono', ''),
            'email': cliente_temp.get('email', ''),
            'sexo': cliente_temp.get('sexo', ''),
        }
    else:
        if not cliente_id:
            return redirect('crear_cita_cliente')
        cliente = get_object_or_404(Cliente, id_cliente=cliente_id)
        if (
            not usuario_es_administrativo(request.user)
            and not usuario_puede_acceder_cliente(request.user, cliente)
        ):
            messages.error(request, 'Solo puedes agendar para pacientes asociados a tus citas anteriores.')
            return redirect(f"{reverse('calendar')}?propio=1")

    contexto = {
        'cliente': cliente,
        'operativo': operativo,
        'servicios': servicios,
        'fechahora': timezone.localtime(fechahora),
        'error': error,
    }

    if request.method != 'POST':
        return render(request, 'citas/confirmar_cita.html', contexto)

    if error:
        return render(request, 'citas/confirmar_cita.html', contexto)

    estado_programada = Estado.objects.filter(nombre_estado__iexact='Programada').first()
    if not estado_programada:
        contexto['error'] = 'No está configurado el estado Programada en el sistema.'
        return render(request, 'citas/confirmar_cita.html', contexto, status=500)

    with transaction.atomic():
        error = validar_programacion_cita(operativo, fechahora, duracion_final)
        if error:
            contexto['error'] = error
            return render(request, 'citas/confirmar_cita.html', contexto)

        if cliente_temp:
            telefono = str(cliente_temp.get('telefono') or '').strip()
            email = str(cliente_temp.get('email') or '').strip()
            cliente = Cliente.objects.filter(n_telefono=telefono).first() if telefono else None
            if not cliente and email:
                cliente = Cliente.objects.filter(email=email).first()
            if not cliente:
                nombre_completo = str(cliente_temp.get('nombre') or '').strip().split()
                nombre = nombre_completo[0] if nombre_completo else ''
                apellido = ' '.join(nombre_completo[1:]) if len(nombre_completo) > 1 else ''
                cliente = Cliente.objects.create(
                    nombre=nombre,
                    apellido=apellido,
                    sexo=cliente_temp.get('sexo') or '',
                    n_telefono=telefono,
                    email=email,
                )

        cita = Cita.objects.create(
            fechahora=fechahora,
            cliente=cliente,
            operativo=operativo,
            estado=estado_programada,
            duracion_minutos=duracion_final,
        )
        cita.servicios.set(servicios)

        propuesta_id = request.session.get('propuesta_id')
        if propuesta_id:
            Propuestas_Citas.objects.filter(ID_Propuesta=propuesta_id).update(Atendida=True)

        registrar_cita_creada(request, cita)

    for key in [
        'servicios_seleccionados', 'cliente_id', 'operativo_id', 'fecha', 'hora',
        'cliente_temp', 'propuesta_id', 'flujo_origen', 'duracion_sugerida', 'duracion_final'
    ]:
        request.session.pop(key, None)

    return redirect('home')


@login_required    
def resumen_citas(request):
    # 🔒 Obtener operativo del usuario
    try:
        personal = Personal.objects.get(user=request.user)
        operativo = Operativo.objects.get(personal=personal)
    except (Personal.DoesNotExist, Operativo.DoesNotExist):
        return redirect('home')

    # Las citas vencidas sin marcar manualmente como atendidas pasan a Pendiente.
    actualizar_citas_vencidas(operativo=operativo)

    hoy = timezone.localdate()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timedelta(days=7)
    inicio_semana_dt = timezone.make_aware(datetime.combine(inicio_semana, time.min))
    fin_semana_dt = timezone.make_aware(datetime.combine(fin_semana, time.min))

    # El panel muestra las citas de la semana actual, no todo el historial.
    todas_las_citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__gte=inicio_semana_dt,
        fechahora__lt=fin_semana_dt,
    ).select_related('cliente').order_by('-fechahora')

    citas_hoy = todas_las_citas.filter(fechahora__date=hoy).order_by('fechahora')
    citas_por_atender_hoy = citas_hoy.filter(
        Q(estado__nombre_estado='Programada') |
        Q(estado__nombre_estado='Pendiente') |
        Q(estado__isnull=True)
    ).count()
    citas_atendidas_hoy = citas_hoy.filter(estado__nombre_estado='Atendida').count()
    citas_canceladas_hoy = citas_hoy.filter(estado__nombre_estado='Cancelada').count()

    total_citas_hoy = citas_hoy.count()
    citas_por_atender_semana = todas_las_citas.filter(
        Q(estado__nombre_estado='Programada') |
        Q(estado__nombre_estado='Pendiente') |
        Q(estado__isnull=True)
    ).count()
    citas_atendidas_semana = todas_las_citas.filter(
        estado__nombre_estado='Atendida'
    ).count()
    citas_canceladas_semana = todas_las_citas.filter(
        estado__nombre_estado='Cancelada'
    ).count()

    return render(request, 'operativo.html', {
        'citas_hoy': citas_hoy,
        'operativo': operativo,
        'hoy': hoy,
        'total_citas_hoy': total_citas_hoy,
        'citas_por_atender_hoy': citas_por_atender_hoy,
        'citas_atendidas_hoy': citas_atendidas_hoy,
        'citas_canceladas_hoy': citas_canceladas_hoy,
        'total_citas_semana': todas_las_citas.count(),
        'citas_por_atender_semana': citas_por_atender_semana,
        'citas_atendidas_semana': citas_atendidas_semana,
        'citas_canceladas_semana': citas_canceladas_semana,
    })


@login_required
def cerrar_cita(request, id_cita):
    cita = get_object_or_404(Cita, id_cita=id_cita)
    if not usuario_puede_acceder_cita(request.user, cita):
        return redirect('home')

    estado_actual = cita.estado.nombre_estado if cita.estado else ""

    if estado_actual == "Atendida":
        messages.error(request, "No se puede cancelar una cita atendida.")
        return redirect('operativo')

    if estado_actual == "Cancelada":
        messages.warning(request, "La cita ya estaba cancelada.")
        return redirect('operativo')

    if request.method == "POST":
        try:
            motivo = limpiar_motivo(request.POST.get('motivo', ''))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            estado_cancelada = get_object_or_404(
                Estado,
                nombre_estado='Cancelada'
            )
            cita.estado = estado_cancelada
            cita.save()
            registrar_accion(
                request,
                "Cancelar cita",
                motivo,
                cita=cita
            )
            messages.success(request, "Cita cancelada correctamente.")
            return redirect('operativo')

    return render(request, 'citas/cancelar_cita.html', {
        'cita': cita
    })

    """
    try:
        # Obtener el estado 'Cancelada'
        estado_cancelada = Estado.objects.get(nombre_estado='Cancelada')
        cita.estado = estado_cancelada
        cita.save()
    except Estado.DoesNotExist:
        # ⚠️ Este caso no debería pasar si ya agregaste 'Cancelada'
        # Puedes lanzar una alerta o crear automáticamente el estado si quieres
        pass

    return redirect('operativo')
    """


@login_required
def gestionar_empleados(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    empleados = Personal.objects.select_related('user').all()

    busqueda = request.GET.get('buscar')
    cargo_filtro = request.GET.get('cargo')

    if busqueda:
        empleados = empleados.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda) |
            Q(n_telefonico__icontains=busqueda) |
            Q(email__icontains=busqueda) |
            Q(user__username__icontains=busqueda)
        )

    if cargo_filtro == 'Operativo':
        empleados = empleados.filter(
            operativo__isnull=False
        )

    elif cargo_filtro == 'Administrativo':
        empleados = empleados.filter(
            administrativo__isnull=False
        )

    empleados_info = []

    for empleado in empleados:

        if Administrativo.objects.filter(personal=empleado).exists():
            cargo = "Administrativo"

        elif Operativo.objects.filter(personal=empleado).exists():
            cargo = "Operativo"

        else:
            cargo = "Sin asignar"

        empleados_info.append({
            'empleado': empleado,
            'cargo': cargo,
            'cuenta_activa': empleado.user.is_active if empleado.user else None,
        })

    total_empleados = Personal.objects.count()
    total_operativos = Operativo.objects.count()
    total_administrativos = Administrativo.objects.count()

    return render(
        request,
        'gestionar_empleados.html',
        {
            'empleados_info': empleados_info,
            'total_empleados': total_empleados,
            'total_operativos': total_operativos,
            'total_administrativos': total_administrativos,
            'busqueda': busqueda,
            'cargo_filtro': cargo_filtro,
        }
    )


@login_required
def registrar_empleado(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    cancel_url = url_retorno_segura(
        request,
        request.POST.get('cancel_to') or request.GET.get('cancel_to'),
        'gestionar_empleados',
    )
    after_save = url_retorno_segura(
        request,
        request.POST.get('after_save') or request.GET.get('after_save'),
        'gestionar_empleados',
    )
    form = PersonalForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        empleado = form.save()
        messages.success(request, 'El personal se registró correctamente.')
        detalle_url = reverse('detalle_empleado', kwargs={'id_personal': empleado.id_personal})
        return redirect(f"{detalle_url}?{urlencode({'next': after_save})}")

    return render(request, 'formulario_empleado.html', {
        'form': form,
        'titulo': 'Registrar personal',
        'texto_boton': 'Registrar personal',
        'cancel_url': cancel_url,
        'after_save': after_save,
    })


@login_required
def editar_empleado(request, id_personal):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    empleado = get_object_or_404(Personal.objects.select_related('user'), id_personal=id_personal)
    operativo = Operativo.objects.filter(personal=empleado).first()
    cancel_url = url_retorno_segura(
        request,
        request.POST.get('cancel_to') or request.GET.get('cancel_to'),
        'detalle_empleado',
        id_personal=empleado.id_personal,
    )
    after_save = url_retorno_segura(
        request,
        request.POST.get('after_save') or request.GET.get('after_save'),
        'gestionar_empleados',
    )
    form = PersonalEdicionForm(
        request.POST or None,
        instance=empleado,
        cuenta_usuario=empleado.user,
        operativo=operativo,
    )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            if empleado.user:
                empleado.user.username = form.cleaned_data['username']
                empleado.user.email = form.cleaned_data['email'] or ''
                empleado.user.save(update_fields=['username', 'email'])
            if operativo:
                servicios_seleccionados = form.cleaned_data['servicios_asignados']
                ids_seleccionados = {servicio.pk for servicio in servicios_seleccionados}
                asignaciones_actuales = ServicioOperativo.objects.filter(operativo=operativo)
                asignaciones_actuales.exclude(
                    servicio_id__in=ids_seleccionados
                ).delete()
                ids_actuales = set(asignaciones_actuales.values_list('servicio_id', flat=True))
                for servicio_id in ids_seleccionados - ids_actuales:
                    ServicioOperativo.objects.create(
                        operativo=operativo,
                        servicio_id=servicio_id,
                    )
        messages.success(request, 'Los datos del personal se actualizaron correctamente.')
        detalle_url = reverse('detalle_empleado', kwargs={'id_personal': empleado.id_personal})
        return redirect(f"{detalle_url}?{urlencode({'next': after_save})}")

    return render(request, 'formulario_empleado.html', {
        'form': form,
        'empleado': empleado,
        'operativo': operativo,
        'titulo': 'Editar personal',
        'texto_boton': 'Guardar cambios',
        'cancel_url': cancel_url,
        'after_save': after_save,
    })


@login_required
def gestionar_acceso_empleado(request, id_personal):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    empleado = get_object_or_404(
        Personal.objects.select_related('user'),
        id_personal=id_personal,
    )
    if not empleado.user:
        messages.error(request, 'Este registro no tiene una cuenta de acceso vinculada.')
        return redirect('editar_empleado', id_personal=empleado.id_personal)

    volver_url = url_retorno_segura(
        request,
        request.POST.get('next') or request.GET.get('next'),
        'editar_empleado',
        id_personal=empleado.id_personal,
    )
    after_save_url = url_retorno_segura(
        request,
        request.POST.get('after_save') or request.GET.get('after_save'),
        'gestionar_empleados',
    )

    if request.method == 'POST':
        accion = request.POST.get('accion')
        if accion == 'desactivar':
            if not empleado.user.is_active:
                messages.info(request, 'La cuenta ya está desactivada.')
                return redirect('detalle_empleado', id_personal=empleado.id_personal)
            if empleado.user_id == request.user.id:
                messages.error(request, 'No puedes desactivar tu propia cuenta.')
                return redirect('detalle_empleado', id_personal=empleado.id_personal)

            form = SuspensionCuentaForm(
                request.POST,
                usuario_esperado=empleado.user.username,
            )
            if form.is_valid():
                detalle = (
                    f"ID personal {empleado.id_personal}; usuario {empleado.user.username}; "
                    f"motivo: {form.cleaned_data['motivo']}; "
                    f"plazo estimado: {form.cleaned_data['duracion_estimada']}."
                )
                with transaction.atomic():
                    empleado.user.is_active = False
                    empleado.user.save(update_fields=['is_active'])
                    registrar_accion(request, 'Suspender cuenta personal', detalle)
                messages.success(request, 'La cuenta se desactivó y el motivo quedó registrado.')
                detalle_url = reverse('detalle_empleado', kwargs={'id_personal': empleado.id_personal})
                return redirect(f"{detalle_url}?{urlencode({'next': after_save_url})}")
        elif accion == 'activar':
            if empleado.user.is_active:
                messages.info(request, 'La cuenta ya está activa.')
            else:
                detalle = f"ID personal {empleado.id_personal}; usuario {empleado.user.username}."
                with transaction.atomic():
                    empleado.user.is_active = True
                    empleado.user.save(update_fields=['is_active'])
                    registrar_accion(request, 'Reactivar cuenta personal', detalle)
                messages.success(request, 'La cuenta se reactivó correctamente.')
            detalle_url = reverse('detalle_empleado', kwargs={'id_personal': empleado.id_personal})
            return redirect(f"{detalle_url}?{urlencode({'next': after_save_url})}")
        else:
            messages.error(request, 'La acción solicitada no es válida.')
            return redirect('detalle_empleado', id_personal=empleado.id_personal)
    else:
        form = SuspensionCuentaForm(usuario_esperado=empleado.user.username)

    return render(request, 'confirmar_acceso_empleado.html', {
        'empleado': empleado,
        'form': form,
        'volver_url': volver_url,
        'after_save_url': after_save_url,
    })


@login_required
def detalle_empleado(request, id_personal):

    if not usuario_es_administrativo(request.user):
        return redirect('home')

    return_url = url_retorno_segura(
        request,
        request.GET.get('next'),
        'gestionar_empleados',
    )
    empleado = get_object_or_404(
        Personal.objects.select_related('user'),
        id_personal=id_personal
    )

    servicios = []
    citas_programadas = 0
    citas_atendidas = 0
    citas_canceladas = 0
    ultimas_citas = []

    if Administrativo.objects.filter(personal=empleado).exists():
        cargo = "Administrativo"

    elif Operativo.objects.filter(personal=empleado).exists():
        cargo = "Operativo"

        operativo = Operativo.objects.get(personal=empleado)

        citas_programadas = Cita.objects.filter(
            operativo=operativo,
            estado__nombre_estado='Programada'
        ).count()

        citas_atendidas = Cita.objects.filter(
            operativo=operativo,
            estado__nombre_estado='Atendida'
        ).count()

        citas_canceladas = Cita.objects.filter(
            operativo=operativo,
            estado__nombre_estado='Cancelada'
        ).count()

        servicios = ServicioOperativo.objects.filter(
            operativo=operativo
        ).select_related('servicio')


        ultimas_citas = Cita.objects.filter(
            operativo=operativo
        ).select_related(
            'cliente',
            'estado'
        ).order_by('-fechahora')[:5]

    else:
        cargo = "Sin asignar"

    return render(
        request,
        'detalle_empleado.html',
        {
            'empleado': empleado,
            'cargo': cargo,
            'return_url': return_url,
            'servicios': servicios,
            'citas_programadas': citas_programadas,
            'citas_atendidas': citas_atendidas,
            'citas_canceladas': citas_canceladas,
            'ultimas_citas': ultimas_citas,
        }
    )


@login_required
def detalle_cita(request, id_cita):
    cita = get_object_or_404(
        Cita.objects.select_related(
            'cliente',
            'operativo',
            'estado'
        ),
        id_cita=id_cita
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return redirect('home')

    actualizar_citas_vencidas(operativo=cita.operativo)

    servicios = cita.servicios.all()
    atencion = ExpedienteClinico.objects.filter(cita=cita).first()
    form_atencion = AtencionForm(
        instance=atencion,
        initial={'fecha_de_atencion': timezone.localtime(cita.fechahora).date()} if not atencion else None,
    )
    seguimientos = Cita.objects.filter(cita_anterior=cita).select_related(
        'estado', 'operativo', 'operativo__personal'
    ).order_by('-fechahora')

    factura = Factura.objects.filter(
        cita=cita
    ).first()

    total = sum(
        servicio.costo
        for servicio in servicios
    )

    return render(
        request,
        'citas/detalle_cita.html',
        {
            'cita': cita,
            'servicios': servicios,
            'total': total,
            'factura': factura,
            'atencion': atencion,
            'form_atencion': form_atencion,
            'seguimientos': seguimientos,
        }
    )


@login_required
def ver_cita_en_agenda(request, id_cita):
    cita = get_object_or_404(
        Cita.objects.select_related('operativo'),
        id_cita=id_cita,
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return redirect('home')
    if not cita.operativo_id:
        messages.error(request, 'Esta cita no tiene personal operativo asignado para mostrar su agenda.')
        return redirect('detalle_cita', id_cita=cita.id_cita)

    request.session['operativo_id'] = cita.operativo_id
    fecha_cita = timezone.localtime(cita.fechahora).date().isoformat()
    query = urlencode({'fecha': fecha_cita, 'cita_id': cita.id_cita})
    return redirect(f"{reverse('calendar')}?{query}")


@login_required
@require_POST
def guardar_atencion_cita(request, id_cita):
    cita = get_object_or_404(
        Cita.objects.select_related('cliente', 'operativo', 'estado'),
        id_cita=id_cita
    )
    if not usuario_es_administrativo(request.user):
        try:
            personal = Personal.objects.get(user=request.user)
        except Personal.DoesNotExist:
            return redirect('home')
        if not cita.operativo or cita.operativo.personal_id != personal.id_personal:
            return redirect('home')

    if not cita.estado or cita.estado.nombre_estado != 'Atendida':
        messages.error(request, 'La atención se puede registrar cuando la cita esté marcada como atendida.')
        return redirect('detalle_cita', id_cita=cita.id_cita)

    atencion = ExpedienteClinico.objects.filter(cita=cita).first()
    form = AtencionForm(request.POST, instance=atencion)
    if form.is_valid():
        registro = form.save(commit=False)
        registro.cita = cita
        registro.cliente = cita.cliente
        registro.operativo = cita.operativo
        registro.nombre_medico = str(cita.operativo.personal) if cita.operativo else None
        if not registro.fecha_de_atencion:
            registro.fecha_de_atencion = timezone.localtime(cita.fechahora).date()
        registro.save()
        messages.success(request, 'La información de atención se guardó correctamente.')
    else:
        errores = '; '.join(
            f'{form.fields[nombre].label}: {", ".join(errores)}'
            for nombre, errores in form.errors.items()
            if nombre in form.fields
        )
        messages.error(request, f'Revisa los campos del registro de atención. {errores}')
    return redirect('detalle_cita', id_cita=cita.id_cita)


@login_required
def programar_seguimiento(request, id_cita):
    cita_anterior = get_object_or_404(
        Cita.objects.select_related('cliente', 'operativo', 'estado').prefetch_related('servicios'),
        id_cita=id_cita
    )
    if not usuario_es_administrativo(request.user):
        try:
            personal = Personal.objects.get(user=request.user)
        except Personal.DoesNotExist:
            return redirect('home')
        if not cita_anterior.operativo or cita_anterior.operativo.personal_id != personal.id_personal:
            return redirect('home')

    if not cita_anterior.estado or cita_anterior.estado.nombre_estado != 'Atendida':
        messages.error(request, 'Solo se puede crear un seguimiento a partir de una cita atendida.')
        return redirect('detalle_cita', id_cita=cita_anterior.id_cita)

    servicios_previos = list(cita_anterior.servicios.all())
    duracion = sum(max(servicio.duracion or 0, 0) for servicio in servicios_previos) or 30
    request.session['seguimiento_cita_id'] = cita_anterior.id_cita
    request.session['cliente_id'] = cita_anterior.cliente_id
    request.session['operativo_id'] = cita_anterior.operativo_id
    request.session['servicios_seleccionados'] = [
        servicio.id_servicio for servicio in servicios_previos
    ]
    request.session['duracion_sugerida'] = duracion
    request.session['duracion_final'] = duracion
    request.session['flujo_origen'] = 'seguimiento'
    request.session.modified = True

    messages.info(request, 'Elige en el calendario el horario para la cita de seguimiento.')
    return redirect('calendar')


@login_required
def cancelar_seguimiento(request):
    cita_id = request.session.get('seguimiento_cita_id')
    cita_anterior = None
    try:
        cita_id = int(cita_id)
    except (TypeError, ValueError):
        cita_id = None

    if cita_id:
        cita_anterior = Cita.objects.select_related('operativo', 'estado').filter(
            id_cita=cita_id
        ).first()

    limpiar_contexto_seguimiento(request)
    if cita_anterior and usuario_puede_acceder_cita(request.user, cita_anterior):
        return redirect('detalle_cita', id_cita=cita_anterior.id_cita)
    return redirect('home')


@login_required
def generar_factura(request, id_cita):

    cita = get_object_or_404(
        Cita.objects.select_related('estado', 'operativo'),
        id_cita=id_cita
    )
    if not usuario_puede_acceder_cita(request.user, cita):
        return redirect('home')

    if cita_esta_cancelada(cita):
        messages.error(
            request,
            "No se puede generar factura para una cita cancelada."
        )
        return redirect('detalle_cita', id_cita=cita.id_cita)

    # Evitar duplicados
    factura_existente = Factura.objects.filter(
        cita=cita
    ).first()

    if factura_existente:
        return redirect(
            'ver_factura',
            id_factura=factura_existente.id_factura
        )

    servicios = cita.servicios.all()

    total = sum(
        servicio.costo
        for servicio in servicios
    )

    estado_pendiente = EstadoPago.objects.get(nombre="Pendiente")


    factura = Factura.objects.create(
        cita=cita,
        fecha_factura=timezone.now(),
        estado_pago=estado_pendiente,
        total=total,
        metodo_pago=None
    )

    for servicio in servicios:
        ServicioFactura.objects.create(
            factura=factura,
            servicio=servicio
        )

    return redirect(
        'ver_factura',
        id_factura=factura.id_factura
    )


@login_required
def ver_factura(request, id_factura):
    asegurar_tabla_pago_factura()

    factura = get_object_or_404(
        Factura.objects.select_related(
            'cita',
            'cita__operativo',
            'cita__cliente',
            'cita__estado',
            'estado_pago',
            'metodo_pago'
        ),
        id_factura=id_factura
    )
    if not usuario_puede_acceder_cita(request.user, factura.cita):
        return redirect('home')

    servicios = Servicio.objects.filter(
        serviciofactura__factura=factura
    )
    pagos = PagoFactura.objects.filter(factura=factura).select_related('metodo_pago')
    hay_pagos_registrados = pagos.exists()

    return render(
        request,
        'facturas/ver_factura.html',
        {
            'factura': factura,
            'servicios': servicios,
            'pagos': pagos,
            'hay_pagos_registrados': hay_pagos_registrados,
        }
    )


def _pdf_escape(texto):
    texto = str(texto or "")
    return (
        texto
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _pdf_text(comandos, texto, x, y, size=10, bold=False):
    fuente = "F2" if bold else "F1"
    comandos.append(
        f"BT /{fuente} {size} Tf {x} {y} Td ({_pdf_escape(texto)}) Tj ET"
    )


def _pdf_line(comandos, x1, y1, x2, y2):
    comandos.append(f"{x1} {y1} m {x2} {y2} l S")


def _construir_factura_pdf(factura, servicios):
    cliente = factura.cita.cliente
    fecha_factura = (
        timezone.localtime(factura.fecha_factura).strftime("%d/%m/%Y %I:%M %p")
        if factura.fecha_factura
        else "No definida"
    )
    fecha_cita = timezone.localtime(
        factura.cita.fechahora
    ).strftime("%d/%m/%Y")
    hora_cita = timezone.localtime(
        factura.cita.fechahora
    ).strftime("%I:%M %p")
    estado = (
        factura.estado_pago.nombre
        if factura.estado_pago
        else "No definido"
    )
    metodo = (
        factura.metodo_pago.nombre
        if factura.metodo_pago
        else "No definido"
    )

    comandos = [
        "0.05 0.45 0.48 rg 0 720 612 72 re f",
        "1 1 1 rg",
    ]
    _pdf_text(comandos, "Clinica Dr. Scholl", 56, 755, 20, True)
    _pdf_text(comandos, f"Factura #{factura.id_factura}", 430, 755, 18, True)

    comandos.append("0 0 0 rg")
    _pdf_text(comandos, "Datos de la factura", 56, 690, 14, True)
    _pdf_line(comandos, 56, 680, 556, 680)

    datos = [
        ("Cliente", f"{cliente.nombre} {cliente.apellido}"),
        ("Telefono", cliente.n_telefono or "No definido"),
        ("Correo", cliente.email or "No definido"),
        ("Fecha factura", fecha_factura),
        ("Fecha cita", fecha_cita),
        ("Hora cita", hora_cita),
        ("Estado", estado),
        ("Metodo de pago", metodo),
    ]

    y = 655
    for etiqueta, valor in datos:
        _pdf_text(comandos, etiqueta, 56, y, 10, True)
        _pdf_text(comandos, valor, 180, y, 10)
        y -= 22

    y -= 12
    _pdf_text(comandos, "Servicios facturados", 56, y, 14, True)
    y -= 18
    comandos.append(f"0.90 0.96 0.96 rg 56 {y - 5} 500 22 re f")
    comandos.append("0 0 0 rg")
    _pdf_text(comandos, "Servicio", 68, y, 10, True)
    _pdf_text(comandos, "Costo", 470, y, 10, True)
    y -= 26

    for servicio in servicios:
        if y < 130:
            _pdf_text(
                comandos,
                "Hay mas servicios; revise el sistema para el detalle completo.",
                68,
                y,
                9
            )
            y -= 20
            break

        nombre = servicio.nombre
        if len(nombre) > 55:
            nombre = nombre[:52] + "..."

        _pdf_text(comandos, nombre, 68, y, 10)
        _pdf_text(comandos, f"C$ {servicio.costo}", 470, y, 10)
        _pdf_line(comandos, 56, y - 8, 556, y - 8)
        y -= 24

    y -= 10
    _pdf_line(comandos, 360, y + 18, 556, y + 18)
    _pdf_text(comandos, "Total", 390, y, 16, True)
    _pdf_text(comandos, f"C$ {factura.total}", 470, y, 16, True)

    _pdf_text(
        comandos,
        "Gracias por preferir Clinica Dr. Scholl.",
        56,
        65,
        9
    )

    contenido = "\n".join(comandos).encode("cp1252", errors="replace")

    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
            b"/Contents 6 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        (
            f"<< /Length {len(contenido)} >>\nstream\n".encode("ascii")
            + contenido
            + b"\nendstream"
        ),
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []

    for numero, objeto in enumerate(objetos, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{numero} 0 obj\n".encode("ascii"))
        pdf.extend(objeto)
        pdf.extend(b"\nendobj\n")

    inicio_xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objetos) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")

    for offset in offsets:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer << /Size {len(objetos) + 1} /Root 1 0 R >>\n"
            f"startxref\n{inicio_xref}\n%%EOF"
        ).encode("ascii")
    )

    return bytes(pdf)


@login_required
def descargar_factura_pdf(request, id_factura):

    factura = get_object_or_404(
        Factura.objects.select_related(
            'cita',
            'cita__operativo',
            'cita__cliente',
            'estado_pago',
            'metodo_pago'
        ),
        id_factura=id_factura
    )
    if not usuario_puede_acceder_cita(request.user, factura.cita):
        return HttpResponseForbidden("No tienes permiso para consultar esta factura.")

    servicios = list(
        Servicio.objects.filter(
            serviciofactura__factura=factura
        )
    )

    respuesta = HttpResponse(
        _construir_factura_pdf(factura, servicios),
        content_type="application/pdf"
    )
    respuesta["Content-Disposition"] = (
        f'attachment; filename="factura_{factura.id_factura}.pdf"'
    )
    return respuesta


@login_required
def listar_facturas(request):
    es_administrativo = usuario_es_administrativo(request.user)
    if es_administrativo:
        actualizar_citas_vencidas()
    else:
        operativo = obtener_operativo_usuario(request)
        if not operativo:
            return redirect('home')
        actualizar_citas_vencidas(operativo=operativo)

    facturas = Factura.objects.select_related(
        'cita',
        'cita__operativo',
        'cita__cliente',
        'estado_pago'
    ).order_by('-fecha_factura')

    if not es_administrativo:
        facturas = facturas.filter(cita__operativo=operativo)

    estado = request.GET.get('estado')

    if estado:
        facturas = facturas.filter(
            estado_pago__nombre=estado
        )

    return render(
        request,
        'facturas/listar_facturas.html',
        {
            'facturas': facturas,
            'estado': estado
        }
    )
@login_required
def listar_pagos(request):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    asegurar_tabla_pago_factura()
    busqueda = request.GET.get('buscar', '').strip()
    estado = request.GET.get('estado', '').strip()
    metodo_id = request.GET.get('metodo', '').strip()
    pagos = PagoFactura.objects.select_related(
        'factura', 'factura__cita', 'factura__cita__cliente',
        'factura__estado_pago', 'metodo_pago'
    ).order_by('-fecha_pago', '-id_pago')

    if busqueda:
        filtros = (
            Q(factura__cita__cliente__nombre__icontains=busqueda) |
            Q(factura__cita__cliente__apellido__icontains=busqueda)
        )
        if busqueda.isdigit():
            filtros |= Q(factura__id_factura=int(busqueda)) | Q(id_pago=int(busqueda))
        pagos = pagos.filter(filtros)
    if estado:
        pagos = pagos.filter(factura__estado_pago__nombre=estado)
    if metodo_id.isdigit():
        pagos = pagos.filter(metodo_pago_id=int(metodo_id))

    return render(request, 'pagos/listar_pagos.html', {
        'pagos': pagos,
        'busqueda': busqueda,
        'estado': estado,
        'metodo_id': metodo_id,
        'metodos': MetodoPago.objects.order_by('nombre'),
    })


@login_required
def registrar_pago_factura(request, id_factura):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    asegurar_tabla_pago_factura()
    factura = get_object_or_404(
        Factura.objects.select_related(
            'estado_pago', 'cita', 'cita__cliente', 'cita__estado'
        ),
        id_factura=id_factura
    )
    estado = factura.estado_pago.nombre if factura.estado_pago else ''
    if cita_esta_cancelada(factura.cita) or estado == 'Anulada':
        messages.error(request, 'No se puede registrar un pago para esta factura.')
        return redirect('ver_factura', id_factura=id_factura)
    if estado == 'Pagada':
        messages.warning(request, 'Esta factura ya está pagada.')
        return redirect('ver_factura', id_factura=id_factura)

    if PagoFactura.objects.filter(factura=factura).exists():
        messages.error(
            request,
            'Esta factura ya tiene un registro de pago. No se permiten pagos parciales ni pagos adicionales; revisa el registro existente.'
        )
        return redirect('ver_factura', id_factura=id_factura)

    form = PagoFacturaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            factura_bloqueada = Factura.objects.select_for_update().select_related(
                'estado_pago', 'cita', 'cita__estado'
            ).get(id_factura=id_factura)
            estado_actual = (
                factura_bloqueada.estado_pago.nombre
                if factura_bloqueada.estado_pago else ''
            )
            pago_existente = PagoFactura.objects.filter(
                factura=factura_bloqueada
            ).exists()
            if (
                estado_actual in ('Pagada', 'Anulada')
                or cita_esta_cancelada(factura_bloqueada.cita)
                or pago_existente
            ):
                messages.error(
                    request,
                    'La factura cambió o ya tiene un pago registrado. Revisa su estado antes de continuar.'
                )
                return redirect('ver_factura', id_factura=id_factura)
            else:
                pago = PagoFactura.objects.create(
                    factura=factura_bloqueada,
                    fecha_pago=timezone.now(),
                    monto=factura_bloqueada.total,
                    metodo_pago=form.cleaned_data['metodo_pago']
                )
                factura_bloqueada.estado_pago = get_object_or_404(
                    EstadoPago, nombre='Pagada'
                )
                factura_bloqueada.metodo_pago = pago.metodo_pago
                factura_bloqueada.save(update_fields=['estado_pago', 'metodo_pago'])
                registrar_accion(
                    request,
                    'Registrar pago',
                    f'Pago #{pago.id_pago} por C$ {pago.monto:.2f}; '
                    f'factura #{id_factura}; método {pago.metodo_pago.nombre}.',
                    factura=factura_bloqueada,
                    cita=factura_bloqueada.cita
                )
                messages.success(request, 'Pago registrado. La factura quedó pagada.')
                return redirect('detalle_pago', id_pago=pago.id_pago)

    servicios = Servicio.objects.filter(serviciofactura__factura=factura)
    return render(request, 'facturas/pagar_factura.html', {
        'factura': factura,
        'form': form,
        'servicios': servicios,
    })


@login_required
def detalle_pago(request, id_pago):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    asegurar_tabla_pago_factura()
    pago = get_object_or_404(
        PagoFactura.objects.select_related(
            'factura', 'factura__cita', 'factura__cita__cliente',
            'factura__estado_pago', 'metodo_pago'
        ),
        id_pago=id_pago
    )
    return render(request, 'pagos/detalle_pago.html', {
        'pago': pago,
    })


@login_required
def pagar_factura(request, id_factura):
    # Mantiene compatible el enlace anterior mientras usa el nuevo registro.
    return redirect('registrar_pago_factura', id_factura=id_factura)



@login_required
def anular_factura(request, id_factura):
    if not usuario_es_administrativo(request.user):
        return redirect('home')

    factura = get_object_or_404(
        Factura.objects.select_related('estado_pago', 'cita'),
        id_factura=id_factura
    )

    if factura.estado_pago.nombre == "Anulada":
        messages.warning(request, "Esta factura ya estaba anulada.")
        return redirect(
            'ver_factura',
            id_factura=factura.id_factura
        )

    if request.method == "POST":
        try:
            motivo = limpiar_motivo(request.POST.get('motivo', ''))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            estado_anulada = get_object_or_404(
                EstadoPago,
                nombre='Anulada'
            )

            factura.estado_pago = estado_anulada
            factura.save()
            registrar_accion(
                request,
                "Anular factura",
                motivo,
                factura=factura,
                cita=factura.cita
            )
            messages.success(request, "Factura anulada correctamente.")
            return redirect(
                'ver_factura',
                id_factura=factura.id_factura
            )

    return render(request, 'facturas/anular_factura.html', {
        'factura': factura
    })
