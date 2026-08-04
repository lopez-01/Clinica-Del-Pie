from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection
from django.db.models import Q, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
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
    ServicioFactura,
    EstadoPago,
    MetodoPago,
    GastoMensual,
    HistorialAccion,
)
from .forms import ClienteForm, ServicioSelectForm, OperativoSelectForm, FechaHoraForm
from django.urls import reverse
from datetime import date, datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
import json
import re

MAX_SOLICITUDES_WEB_POR_DIA = 5
HORA_APERTURA_CLINICA = time(8, 0)
HORA_CIERRE_CLINICA = time(17, 0)
DIAS_NO_LABORALES = {0}
PATRON_NOMBRE = re.compile(
    r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:[ '-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)*$"
)


def obtener_rango_agenda():
    fecha_minima = timezone.localdate()
    fecha_maxima = fecha_minima + timedelta(days=365)
    return fecha_minima, fecha_maxima


def fecha_en_rango_agenda(fecha):
    fecha_minima, fecha_maxima = obtener_rango_agenda()
    return fecha_minima <= fecha <= fecha_maxima


def fecha_laboral(fecha):
    return fecha.weekday() not in DIAS_NO_LABORALES


def cita_en_horario_laboral(inicio, duracion):
    inicio_local = timezone.localtime(inicio)
    fin_local = inicio_local + timedelta(minutes=duracion)

    if inicio_local.date() != fin_local.date():
        return False

    return (
        inicio_local.time() >= HORA_APERTURA_CLINICA
        and fin_local.time() <= HORA_CIERRE_CLINICA
    )


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


def actualizar_citas_vencidas(operativo=None):
    try:
        estado_atendida = Estado.objects.get(nombre_estado='Atendida')
    except Estado.DoesNotExist:
        return 0

    citas = Cita.objects.filter(
        fechahora__lt=timezone.now(),
        estado__nombre_estado='Programada'
    )

    if operativo:
        citas = citas.filter(operativo=operativo)

    return citas.update(estado=estado_atendida)


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
    inicio, fin = rango_mes(anio, mes)
    return Factura.objects.filter(
        fecha_factura__gte=inicio,
        fecha_factura__lt=fin,
        estado_pago__nombre='Pagada'
    ).aggregate(
        total=Sum('total')
    )['total'] or Decimal('0.00')


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
    inicio, fin = rango_mes(anio, mes)
    comisiones = {}

    servicios_facturados = ServicioFactura.objects.filter(
        factura__fecha_factura__gte=inicio,
        factura__fecha_factura__lt=fin,
        factura__estado_pago__nombre='Pagada'
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
            key = operativo.id_operativo
            nombre_operativo = f"{operativo.personal.nombre} {operativo.personal.apellido}"
        else:
            key = 0
            nombre_operativo = "Sin operativo"

        item = comisiones.setdefault(key, {
            'operativo': nombre_operativo,
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
        username = request.POST.get('username')
        selected_username = username
        password = request.POST.get('password')

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
    return render(request, 'web/index.html')


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
    asegurar_tablas_administrativas()
    actualizar_citas_vencidas()

    hoy = timezone.localdate()
    mes = hoy.month
    anio = hoy.year
    ingresos_mes = ingresos_facturados_pagados(anio, mes)
    gasto_mes = GastoMensual.objects.filter(mes=mes, anio=anio).first()
    _, salarios_operativos = comisiones_operativos_mes(anio, mes)

    if gasto_mes:
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
        'total_empleados': Personal.objects.count(),
        'total_servicios': Servicio.objects.count(),
        'ingresos_mes': ingresos_mes,
        'gastos_mes': gastos_mes,
        'salarios_operativos': salarios_operativos,
        'balance_mes': balance_mes,
        'alertas_admin': alertas_admin,
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

    gasto = GastoMensual.objects.filter(
        mes=mes,
        anio=anio
    ).first()

    if request.method == 'POST':
        accion_formulario = request.POST.get('accion', 'guardar')

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
                    cerrado=True
                )
            else:
                gasto.salarios = salarios_operativos
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

        if gasto and gasto.cerrado:
            messages.error(request, "Este mes ya esta cerrado y no se puede editar.")
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

    inicio_mes, fin_mes = rango_mes(anio, mes)
    ingresos_mes = ingresos_facturados_pagados(anio, mes)
    gastos_mes = total_gasto_mensual(gasto, salarios_operativos)
    balance_mes = ingresos_mes - gastos_mes

    facturas_pagadas = Factura.objects.filter(
        fecha_factura__gte=inicio_mes,
        fecha_factura__lt=fin_mes,
        estado_pago__nombre='Pagada'
    ).count()
    facturas_pendientes = Factura.objects.filter(
        fecha_factura__gte=inicio_mes,
        fecha_factura__lt=fin_mes,
        estado_pago__nombre='Pendiente'
    ).count()

    historial = list(
        GastoMensual.objects.all().order_by('-anio', '-mes')[:12]
    )

    nombres_meses = dict(MESES)
    for item in historial:
        item.nombre_mes = nombres_meses.get(item.mes, item.mes)
        item.ingresos = ingresos_facturados_pagados(item.anio, item.mes)
        item.comisiones_operativos, item.salarios_operativos = comisiones_operativos_mes(
            item.anio,
            item.mes
        )
        item.total_gastos = total_gasto_mensual(item, item.salarios_operativos)
        item.balance = item.ingresos - item.total_gastos

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
            'salarios_operativos': salarios_operativos,
            'comisiones_operativos': comisiones_operativos,
            'facturas_pagadas': facturas_pagadas,
            'facturas_pendientes': facturas_pendientes,
            'historial': historial,
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
    actualizar_citas_vencidas()

    busqueda = request.GET.get('buscar', '').strip()
    operativo_id = request.GET.get('operativo', '').strip()
    estado_factura = request.GET.get('estado_factura', '').strip()
    orden_fecha = request.GET.get('orden_fecha', 'recientes').strip()

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

        item['total_facturas'] += 1

        if estado_pago == 'Pagada':
            item['facturas_pagadas'] += 1
            item['total_pagado'] += total
            item['total_facturado'] += total
        elif estado_pago == 'Pendiente':
            item['facturas_pendientes'] += 1
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

    clientes_resumen.sort(
        key=lambda item: item['ultima_cita'].fechahora,
        reverse=orden_fecha != 'antiguas'
    )

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
            'clientes_resumen': clientes_resumen,
            'operativos': operativos,
            'busqueda': busqueda,
            'operativo_id': operativo_id,
            'estado_factura': estado_factura,
            'orden_fecha': orden_fecha,
            'total_clientes': total_clientes,
            'total_citas': total_citas,
            'total_facturas': total_facturas,
            'total_pagado': total_pagado,
            'total_pendiente': total_pendiente,
            'acciones_recientes': HistorialAccion.objects.order_by('-fecha')[:20],
        }
    )


@login_required
def operativo_view(request):
    return render(request, 'operativo.html')

@login_required
def lista_clientes(request):
    return render(request, 'lista_clientes.html')


def crear_solicitud_web(request):
    if request.method != 'POST':
        return redirect('landing')

    nombre = request.POST.get('nombre', '').strip()
    email = request.POST.get('email', '').strip()
    telefono = ''.join(
        digito
        for digito in request.POST.get('numero_telefonico', '')
        if digito.isdigit()
    )
    propuesta = request.POST.get('propuesta_de_dia', '').strip()

    if not nombre or not telefono or not propuesta:
        messages.error(
            request,
            "Nombre, telefono y mensaje son obligatorios."
        )
        return redirect(f"{reverse('landing')}#contacts")

    if len(propuesta) < 10 or len(propuesta) > 250:
        messages.error(
            request,
            "El mensaje debe tener entre 10 y 250 caracteres."
        )
        return redirect(f"{reverse('landing')}#contacts")

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


@login_required
def inbox(request):
    solicitudes = Propuestas_Citas.objects.filter(
        Atendida=False
    ).order_by('-Fecha')

    return render(request, 'administrativo/inbox.html', {
        'solicitudes': solicitudes
    })


@login_required
def rechazar_propuesta(request, id):
    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    propuesta.Atendida = True
    propuesta.save()

    return redirect('inbox')


@login_required
def crear_cita_desde_propuesta(request, id):
    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    if propuesta.Atendida:
        messages.warning(
            request,
            "Esta solicitud ya fue atendida."
        )
        return redirect('inbox')

    request.session['cliente_temp'] = {
        'nombre': propuesta.Nombre,
        'apellido': '',
        'telefono': propuesta.Numero_telefonico,
        'email': propuesta.Email,
    }

    request.session['propuesta_id'] = propuesta.ID_Propuesta
    request.session['flujo_origen'] = 'inbox'

    return redirect('seleccionar_operativo')


def listar_clientes(request):
    try:
        personal = Personal.objects.get(user=request.user)
        operativo = Operativo.objects.get(personal=personal)

        clientes = Cliente.objects.filter(
            cita__operativo=operativo
        ).distinct()

    except (Personal.DoesNotExist, Operativo.DoesNotExist):
        clientes = Cliente.objects.none()

    return render(request, 'clientes/listar.html', {'clientes': clientes})


def crear_cliente(request):
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('listar_clientes')
    else:
        form = ClienteForm()
    return render(request, 'clientes/crear.html', {'form': form})


def editar_cliente(request, id_cliente):
    cliente = get_object_or_404(Cliente, id_cliente=id_cliente)
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            return redirect('listar_clientes')
    else:
        form = ClienteForm(instance=cliente)
    return render(request, 'clientes/editar.html', {'form': form})


def eliminar_cliente(request, id_cliente):
    cliente = get_object_or_404(Cliente, id_cliente=id_cliente)
    if request.method == 'POST':
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

            duracion_sugerida = sum(s.duracion for s in servicios)

            duracion_final = request.POST.get('duracion_final')

            # 🔥 guardar selección en sesión
            request.session['servicios_seleccionados'] = servicios_ids
            request.session['duracion_sugerida'] = duracion_sugerida
            request.session['duracion_final'] = (
                int(duracion_final) if duracion_final else duracion_sugerida
            )

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


def crear_cita_cliente(request):

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

    return render(request, 'citas/seleccionar_operativo.html', {
        'form': form,
        'operativo_actual': operativo_actual
    })



def agenda_operativo(request):

    operativo_id = request.session.get('operativo_id')

    if not operativo_id:
        return redirect('seleccionar_operativo')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)

    fecha_str = request.GET.get('fecha')

    if fecha_str:
        fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    else:
        fecha = date.today()

    # ❌ no atención lunes
    if fecha.weekday() == 0:
        return render(request, 'citas/agenda_operativo.html', {
            'operativo': operativo,
            'fecha': fecha,
            'horas': [],
            'error': 'La clínica no atiende los lunes.'
        })

    horas = []
    now = timezone.localtime(timezone.now())

    for hora in range(8, 18):

        bloques = []

        for minuto in range(0, 60):

            inicio = datetime.combine(fecha, time(hora, minuto))
            inicio = timezone.make_aware(inicio)

            # 🔥 solo bloquear pasado (visual)
            es_pasado = (fecha == now.date() and inicio < now)

            libre = not hay_conflicto_intervalo(
                operativo,
                fecha,
                inicio,
                30
            )

            bloques.append({
                "hora": f"{hora:02d}:{minuto:02d}",
                "inicio": inicio,
                "pasado": es_pasado,
                "libre": libre and not es_pasado,
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
        'horas': horas
    })


@require_GET
def bloques_hora_ajax(request):

    operativo_id = request.session.get('operativo_id')
    hora = int(request.GET.get('hora'))
    fecha_str = request.GET.get('fecha')

    operativo = Operativo.objects.get(id_operativo=operativo_id)
    fecha = datetime.strptime(fecha_str, '%Y-%m-%d').date()

    bloques = []

    now = timezone.localtime(timezone.now())

    for minuto in range(0, 60):

        inicio = datetime.combine(fecha, time(hora, minuto))
        inicio = timezone.make_aware(inicio)

        es_pasado = (fecha == now.date() and inicio < now)

        libre = not hay_conflicto_intervalo(
            operativo,
            fecha,
            inicio,
            30
        )

        bloques.append({
            "hora": f"{hora:02d}:{minuto:02d}",
            "libre": libre and not es_pasado,
            "pasado": es_pasado,
        })

    return JsonResponse({"bloques": bloques})


def hay_conflicto_intervalo(operativo, fecha, inicio, duracion, excluir=None):

    citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__date=fecha,
        estado__nombre_estado='Programada'
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


def obtener_rangos_ocupados(operativo, fecha):

    citas = Cita.objects.filter(
        operativo=operativo,
        fechahora__date=fecha,
        estado__nombre_estado='Programada'
    )

    rangos = []

    for c in citas:
        inicio = timezone.localtime(c.fechahora)
        fin = inicio + timedelta(minutes=c.duracion_minutos or 30)

        rangos.append({
            "inicio": inicio,
            "fin": fin
        })

    return rangos


def calendar_view(request):

    operativo_id = request.session.get('operativo_id')
    if not operativo_id:
        return redirect('seleccionar_operativo')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    actualizar_citas_vencidas(operativo=operativo)
    fecha_minima, fecha_maxima = obtener_rango_agenda()

    fecha_str = request.GET.get('fecha')
    try:
        base_date = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else timezone.localdate()
    except (TypeError, ValueError):
        base_date = timezone.localdate()

    offset = int(request.GET.get("offset", 0))

    # 🔥 aplicar desplazamiento de semanas
    base_date = base_date + timedelta(weeks=offset)

    if base_date < fecha_minima:
        base_date = fecha_minima

    if base_date > fecha_maxima:
        base_date = fecha_maxima

    # 🔥 construir semana (lunes a domingo)
    inicio_semana = base_date - timedelta(days=base_date.weekday())
    semana_minima = fecha_minima - timedelta(days=fecha_minima.weekday())
    semana_maxima = fecha_maxima - timedelta(days=fecha_maxima.weekday())

    dias = []
    for i in range(7):
        dia = inicio_semana + timedelta(days=i)
        dias.append(dia)

    # 🔥 horas visibles
    horas = list(range(8, 18))

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
            "Atendida": "event-atendida",
            "Cancelada": "event-cancelada",
        }.get(estado_nombre, "event-programada")

        eventos.append({
            "id": c.id_cita,
            "inicio": inicio,
            "fin": fin,
            "cliente": f"{c.cliente.nombre} {c.cliente.apellido}",
            "dia": inicio.date(),

            "duracion": c.duracion_minutos,

            "top": inicio_minutos * 2,  # 1px por minuto
            "height": (c.duracion_minutos or 30) * 2,

            "hora_texto": inicio.strftime("%H:%M"),
            "hora_fin": fin.strftime("%H:%M"),
            "estado": estado_nombre,
            "estado_clase": estado_clase,
        })

    return render(request, "citas/calendar.html", {
        "operativo": operativo,
        "dias": dias,
        "horas": horas,
        "eventos": eventos,
        "offset": offset,
        "fecha_actual": base_date,
        "fecha_minima": fecha_minima,
        "fecha_maxima": fecha_maxima,
        "fecha_anterior": base_date - timedelta(weeks=1),
        "fecha_siguiente": base_date + timedelta(weeks=1),
        "puede_semana_anterior": inicio_semana > semana_minima,
        "puede_semana_siguiente": inicio_semana < semana_maxima,
        "Clientes": Cliente.objects.all(),
        "duracion": 30,
        "hora_texto": "",
        "hora_fin": "",
        "servicios": servicios,
    })



@require_POST
def mover_cita(request):

    data = json.loads(request.body)

    cita = Cita.objects.get(id=data["id"])

    nueva_fecha = timezone.localtime(cita.fechahora).date()

    nueva_hora_min = data["top"]

    nueva_duracion = data["height"]

    nueva_inicio = datetime.combine(
        nueva_fecha,
        time(0, 0)
    ) + timedelta(minutes=nueva_hora_min)

    nueva_inicio = timezone.make_aware(nueva_inicio)

    # VALIDACIÓN
    if hay_conflicto_intervalo(
        cita.operativo,
        nueva_fecha,
        nueva_inicio,
        nueva_duracion
    ):
        return JsonResponse({
            "error": True,
            "mensaje": "Conflicto con otra cita."
        })

    cita.fechahora = nueva_inicio
    cita.duracion_minutos = nueva_duracion
    cita.save()

    return JsonResponse({"ok": True})


def seleccionar_fecha_hora(request):
    if request.method == 'POST':
        form = FechaHoraForm(request.POST)
        if form.is_valid():
            fecha = form.cleaned_data['fecha']
            hora = form.cleaned_data['hora']
            request.session['fecha'] = str(fecha)
            request.session['hora'] = str(hora)
            return redirect('confirmar_cita')
    else:
        form = FechaHoraForm()
    return render(request, 'citas/seleccionar_fecha_hora.html', {'form': form})


def seleccionar_bloque(request):

    operativo_id = request.session.get('operativo_id')

    if not operativo_id:
        return redirect('seleccionar_operativo')

    fecha = request.GET.get('fecha')
    hora = request.GET.get('hora')
    bloque = request.GET.get('bloque')

    if not all([fecha, hora, bloque]):
        return redirect('agenda_operativo')

    # guardar en sesión (ESTO ES LO IMPORTANTE)
    request.session['fecha'] = fecha
    request.session['hora'] = bloque
    request.session['operativo_id'] = operativo_id

    return redirect('confirmar_cita')



@require_POST
def crear_cita_ajax(request):

    data = json.loads(request.body)

    fecha = data.get("fecha")
    hora = data.get("hora")
    duracion = int(data.get("duracion") or 0)
    cliente_id = data.get("cliente_id")

    nombre = normalizar_nombre_persona(data.get("nombre", ""))
    apellido = normalizar_nombre_persona(data.get("apellido", ""))
    telefono = data.get("telefono", "").strip()
    email = data.get("email", "").strip()
    sexo = data.get("sexo", "").strip()
    servicios_ids = data.get("servicios", [])

    operativo_id = request.session.get("operativo_id")

    operativo = get_object_or_404(
        Operativo,
        id_operativo=operativo_id
    )

    if not fecha or not hora:
        return JsonResponse({
            "error": True,
            "mensaje": "Debe seleccionar fecha y hora."
        })

    inicio = datetime.strptime(
        f"{fecha} {hora}",
        "%Y-%m-%d %H:%M"
    )

    inicio = timezone.make_aware(inicio)

    ahora = timezone.localtime()

    # VALIDACION PRIMERO
    if not cliente_id and not telefono:
        return JsonResponse({
            "error": True,
            "mensaje": "El telefono es obligatorio y debe tener 8 numeros."
        })

    if inicio < ahora:
        return JsonResponse({
            "error": True,
            "mensaje": "No se pueden crear citas en fechas pasadas."
        })

    if not fecha_en_rango_agenda(inicio.date()):
        return JsonResponse({
            "error": True,
            "mensaje": "Solo se pueden agendar citas dentro de un limite de un año."
        })

    if not fecha_laboral(inicio.date()):
        return JsonResponse({
            "error": True,
            "mensaje": "La clinica no atiende los lunes."
        })

    if duracion <= 0:
        return JsonResponse({
            "error": True,
            "mensaje": "La duracion debe ser mayor que 0."
        })

    if not servicios_ids:
        return JsonResponse({
            "error": True,
            "mensaje": "Debe seleccionar al menos un servicio."
        })

    if not cita_en_horario_laboral(inicio, duracion):
        return JsonResponse({
            "error": True,
            "mensaje": "La cita debe terminar antes del cierre de la clinica."
        })

    if not cliente_id:
        if not nombre or not apellido or not telefono or not sexo:
            return JsonResponse({
                "error": True,
                "mensaje": "Nombre, apellido, telefono y sexo son obligatorios."
            })

        if not nombre_persona_valido(nombre) or not nombre_persona_valido(apellido):
            return JsonResponse({
                "error": True,
                "mensaje": "Nombre y apellido deben contener solo letras y tener un formato valido."
            })

        if not telefono.isdigit() or len(telefono) != 8:
            return JsonResponse({
                "error": True,
                "mensaje": "El telefono debe tener exactamente 8 numeros."
            })

        if email:
            try:
                validate_email(email)
            except ValidationError:
                return JsonResponse({
                    "error": True,
                    "mensaje": "Ingrese un correo valido o deje el campo vacio."
                })

    cliente = (
        get_object_or_404(Cliente, id_cliente=cliente_id)
        if cliente_id
        else None
    )

    # BUSCAR CLIENTE EXISTENTE
    if not cliente and telefono:
        cliente = Cliente.objects.filter(n_telefono=telefono).first()

    if not cliente and email:
        cliente = Cliente.objects.filter(email=email).first()

    # CREAR SI NO EXISTE
    if not cliente:
        cliente = Cliente.objects.create(
            nombre=nombre,
            apellido=apellido,
            sexo=sexo,
            n_telefono=telefono,
            email=email
        )

    # VALIDAR SOLAPAMIENTO
    if hay_conflicto_intervalo(
        operativo,
        inicio.date(),
        inicio,
        duracion
    ):
        return JsonResponse({
            "error": True,
            "mensaje": "La duración de la cita se solapa con otra."
        })

    cita = Cita.objects.create(
        fechahora=inicio,
        cliente=cliente,
        operativo=operativo,
        duracion_minutos=duracion,
        estado=Estado.objects.get(nombre_estado="Programada")
    )

    for servicio_id in servicios_ids:

        CitaServicio.objects.create(
            cita=cita,
            servicio_id=servicio_id
        )

    registrar_cita_creada(request, cita)

    return JsonResponse({
        "ok": True,
        "id_cita": cita.id_cita,
        "cliente_id": cliente.id_cliente
    })


def detalle_cita_ajax(request, id):

    cita = get_object_or_404(
        Cita,
        id_cita=id
    )

    return JsonResponse({

        "id": cita.id_cita,

        "cliente":
            f"{cita.cliente.nombre} "
            f"{cita.cliente.apellido}",

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

    })


@require_POST
def editar_cita_ajax(request):

    data = json.loads(request.body)

    cita = get_object_or_404(
        Cita,
        id_cita=data["id"]
    )

    estado_actual = cita.estado.nombre_estado if cita.estado else "Programada"

    if estado_actual != "Programada":
        return JsonResponse({
            "error": True,
            "mensaje": "Solo se pueden modificar citas programadas."
        })

    fecha = data["fecha"]
    hora = data["hora"]
    duracion = int(data["duracion"])
    servicios_ids = data.get("servicios", [])

    if not servicios_ids:
        return JsonResponse({
            "error": True,
            "mensaje": "Debe seleccionar al menos un servicio."
        })

    inicio = datetime.strptime(
        f"{fecha} {hora}",
        "%Y-%m-%d %H:%M"
    )

    inicio = timezone.make_aware(inicio)

    # NO PERMITIR FECHAS PASADAS

    if inicio < timezone.localtime():

        return JsonResponse({
            "error": True,
            "mensaje": "No se puede mover una cita a una fecha pasada."
        })

    if not fecha_en_rango_agenda(inicio.date()):
        return JsonResponse({
            "error": True,
            "mensaje": "Solo se pueden mover citas dentro de un limite de un año."
        })

    if not fecha_laboral(inicio.date()):
        return JsonResponse({
            "error": True,
            "mensaje": "La clinica no atiende los lunes."
        })

    if duracion <= 0:

        return JsonResponse({
            "error": True,
            "mensaje": "La duración debe ser mayor que 0."
        })

    if not cita_en_horario_laboral(inicio, duracion):
        return JsonResponse({
            "error": True,
            "mensaje": "La cita debe terminar antes del cierre de la clinica."
        })

    # VALIDAR SOLAPAMIENTO

    if hay_conflicto_intervalo(
        cita.operativo,
        inicio.date(),
        inicio,
        duracion,
        excluir=cita.id_cita
    ):

        return JsonResponse({
            "error": True,
            "mensaje": "La cita se solapa con otra cita existente."
        })

    cita.fechahora = inicio
    cita.duracion_minutos = duracion
    servicios = Servicio.objects.filter(id_servicio__in=servicios_ids)

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


def obtener_cita_ajax(request, id_cita):

    cita = get_object_or_404(
        Cita,
        id_cita=id_cita
    )

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


@require_POST
def mover_cita_ajax(request):

    data = json.loads(request.body)

    cita = get_object_or_404(
        Cita,
        id_cita=data["id"]
    )

    top = int(data["top"])
    height = int(data["height"])

    minutos_inicio = top
    duracion = height

    hora = 8 + (minutos_inicio // 60)
    minuto = minutos_inicio % 60

    inicio_nuevo = timezone.localtime(
        cita.fechahora
    ).replace(
        hour=hora,
        minute=minuto
    )

    conflicto = hay_conflicto_intervalo(
        cita.operativo,
        inicio_nuevo.date(),
        inicio_nuevo,
        duracion,
        excluir=cita.id_cita
    )

    if conflicto:

        sugerencias = buscar_horarios_libres(
            cita.operativo,
            inicio_nuevo.date(),
            duracion
        )

        return JsonResponse({
            "error": True,
            "mensaje": "La cita se solapa con otra.",
            "sugerencias": sugerencias
        })

    cita.fechahora = inicio_nuevo
    cita.duracion_minutos = duracion
    cita.save()

    return JsonResponse({"ok": True})



def buscar_horarios_libres(
    operativo,
    fecha,
    duracion
):

    opciones = []

    for hora in range(8, 18):

        for minuto in range(60):

            inicio = datetime.combine(
                fecha,
                time(hora, minuto)
            )

            inicio = timezone.make_aware(
                inicio
            )

            conflicto = hay_conflicto_intervalo(
                operativo,
                fecha,
                inicio,
                duracion
            )

            if not conflicto:

                opciones.append({
                    "inicio": inicio.strftime("%H:%M"),
                    "duracion": duracion
                })

            if len(opciones) >= 5:
                return opciones

    return opciones


def buscar_clientes(request):

    q = request.GET.get("q", "")

    clientes = Cliente.objects.filter(
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


def confirmar_cita(request):
    servicios_ids = request.session.get('servicios_seleccionados')
    cliente_id = request.session.get('cliente_id')
    operativo_id = request.session.get('operativo_id')
    fecha_str = request.session.get('fecha')
    hora_str = request.session.get('hora')

    if not all([servicios_ids, operativo_id, fecha_str, hora_str]):
        return redirect('seleccionar_servicios')

    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    servicios = Servicio.objects.filter(id_servicio__in=servicios_ids)

    fechahora = datetime.strptime(f"{fecha_str} {hora_str}", '%Y-%m-%d %H:%M')

    # 🔥 CLIENTE DESDE INBOX O NORMAL
    cliente_temp = request.session.get('cliente_temp')

    if cliente_temp:
        telefono = cliente_temp.get('telefono')
        email = cliente_temp.get('email')

        cliente = None

        # 🔍 Buscar por teléfono
        if telefono:
            cliente = Cliente.objects.filter(n_telefono=telefono).first()

        # 🔍 Buscar por email si no encontró
        if not cliente and email:
            cliente = Cliente.objects.filter(email=email).first()

        # 🆕 Crear si no existe
        if not cliente:
            nombre_completo = cliente_temp.get('nombre', '').strip().split()

            nombre = nombre_completo[0] if nombre_completo else ''
            apellido = " ".join(nombre_completo[1:]) if len(nombre_completo) > 1 else ''

            cliente = Cliente.objects.create(
                nombre=nombre,
                apellido=apellido,
                n_telefono=telefono,
                email=email
            )
    else:
        cliente = get_object_or_404(Cliente, id_cliente=cliente_id)


    if request.method == 'POST':

        duracion_sugerida = sum(s.duracion for s in servicios)
        duracion_final = duracion_sugerida  # luego será editable


        # 🔒 VALIDAR SI EL HORARIO YA ESTÁ OCUPADO
        conflicto = hay_conflicto_intervalo(
            operativo=operativo,
            fecha=fechahora.date(),
            inicio=fechahora,
            duracion=duracion_final
        )

        if conflicto:
            return render(request, 'citas/confirmar_cita.html', {
                'cliente': cliente,
                'operativo': operativo,
                'servicios': servicios,
                'fechahora': fechahora,
                'error': 'Este horario ya está ocupado para el operativo seleccionado.'
            })

        # ✅ Crear la cita
        cita = Cita.objects.create(
            fechahora=fechahora,
            cliente=cliente,
            operativo=operativo,
            duracion_minutos=duracion_final
        )

        cita.servicios.set(servicios)

        # 🔥 MARCAR PROPUESTA COMO ATENDIDA (INBOX)
        propuesta_id = request.session.get('propuesta_id')

        if propuesta_id:
            try:
                propuesta = Propuestas_Citas.objects.get(ID_Propuesta=propuesta_id)
                propuesta.Atendida = True
                propuesta.save()
            except Propuestas_Citas.DoesNotExist:
                pass


        try:
            estado_programada = Estado.objects.get(nombre_estado='Programada')
            cita.estado = estado_programada
            cita.save()
        except Estado.DoesNotExist:
            pass

        registrar_cita_creada(request, cita)

        # 🧹 Limpiar sesión
        for key in [
            'servicios_seleccionados',
            'cliente_id',
            'operativo_id',
            'fecha',
            'hora',
            'cliente_temp',
            'propuesta_id',
            'flujo_origen'
        ]:
            request.session.pop(key, None)

        return redirect('home')

    return render(request, 'citas/confirmar_cita.html', {
        'cliente': cliente,
        'operativo': operativo,
        'servicios': servicios,
        'fechahora': fechahora,
    })


@login_required    
def resumen_citas(request):
    ahora = timezone.now()

    # 🔒 Obtener operativo del usuario
    try:
        personal = Personal.objects.get(user=request.user)
        operativo = Operativo.objects.get(personal=personal)
    except (Personal.DoesNotExist, Operativo.DoesNotExist):
        return redirect('home')

    # ✅ ACTUALIZAR AUTOMÁTICAMENTE A "ATENDIDA" (solo sus citas)
    try:
        estado_atendida = Estado.objects.get(nombre_estado='Atendida')
        Cita.objects.filter(
        fechahora__lt=ahora,
        operativo=operativo,
        estado__nombre_estado='Programada'
    ).update(estado=estado_atendida)
    except Estado.DoesNotExist:
        pass

    # 🔍 FILTRO POR ESTADO
    estado_filtro = request.GET.get('estado')

    # 🔒 SOLO CITAS DEL OPERATIVO
    citas = Cita.objects.filter(
        operativo=operativo
    ).select_related('cliente').order_by('-fechahora')

    if estado_filtro:
        citas = citas.filter(estado__nombre_estado=estado_filtro)

    # 📊 MÉTRICAS
    hoy = timezone.now().date()
    citas_hoy = citas.filter(fechahora__date=hoy)

    total_citas_hoy = citas_hoy.count()
    citas_confirmadas = citas.filter(estado__nombre_estado='Programada').count()
    citas_canceladas = citas.filter(estado__nombre_estado='Cancelada').count()

    return render(request, 'operativo.html', {
        'citas': citas,
        'operativo': operativo,
        'total_citas_hoy': total_citas_hoy,
        'citas_confirmadas': citas_confirmadas,
        'citas_canceladas': citas_canceladas,
        'estado_filtro': estado_filtro
    })


@login_required
def cerrar_cita(request, id_cita):
    cita = get_object_or_404(Cita, id_cita=id_cita)

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
    empleados = Personal.objects.all()

    busqueda = request.GET.get('buscar')
    cargo_filtro = request.GET.get('cargo')

    if busqueda:
        empleados = empleados.filter(
            Q(nombre__icontains=busqueda) |
            Q(apellido__icontains=busqueda)
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
            'cargo': cargo
        })

    total_empleados = empleados.count()
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
def detalle_empleado(request, id_personal):

    empleado = get_object_or_404(
        Personal,
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
            'servicios': servicios,
            'citas_programadas': citas_programadas,
            'citas_atendidas': citas_atendidas,
            'citas_canceladas': citas_canceladas,
            'ultimas_citas': ultimas_citas,
        }
    )


@login_required
def detalle_cita(request, id_cita):
    actualizar_citas_vencidas()

    cita = get_object_or_404(
        Cita.objects.select_related(
            'cliente',
            'operativo',
            'estado'
        ),
        id_cita=id_cita
    )

    servicios = cita.servicios.all()

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
            'factura': factura
        }
    )


@login_required
def generar_factura(request, id_cita):

    cita = get_object_or_404(
        Cita.objects.select_related('estado'),
        id_cita=id_cita
    )

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

    factura = get_object_or_404(
        Factura.objects.select_related(
            'cita',
            'cita__cliente',
            'cita__estado',
            'estado_pago',
            'metodo_pago'
        ),
        id_factura=id_factura
    )

    servicios = Servicio.objects.filter(
        serviciofactura__factura=factura
    )

    return render(
        request,
        'facturas/ver_factura.html',
        {
            'factura': factura,
            'servicios': servicios,
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
            'cita__cliente',
            'estado_pago',
            'metodo_pago'
        ),
        id_factura=id_factura
    )

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
    actualizar_citas_vencidas()

    facturas = Factura.objects.select_related(
        'cita',
        'cita__cliente',
        'estado_pago'
    ).order_by('-fecha_factura')

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
def pagar_factura(request, id_factura):

    factura = get_object_or_404(
        Factura.objects.select_related(
            'estado_pago',
            'cita',
            'cita__estado'
        ),
        id_factura=id_factura
    )

    if cita_esta_cancelada(factura.cita):
        messages.error(
            request,
            "No se puede pagar una factura vinculada a una cita cancelada."
        )
        return redirect('ver_factura', id_factura=id_factura)

    if factura.estado_pago.nombre == "Anulada":
        messages.error(request, "No se puede pagar una factura anulada.")
        return redirect('ver_factura', id_factura=id_factura)

    # evitar pagar dos veces
    if factura.estado_pago.nombre == "Pagada":  
        messages.warning(request, "Esta factura ya estaba pagada.")
        return redirect('ver_factura', id_factura=id_factura)

    metodos = MetodoPago.objects.all()

    if request.method == "POST":

        metodo_id = request.POST.get("metodo_pago")

        metodo = get_object_or_404(MetodoPago, id_metodopago=metodo_id)

        estado_pagada = get_object_or_404(EstadoPago, nombre='Pagada')

        factura.estado_pago = estado_pagada
        factura.metodo_pago = metodo
        factura.save()
        registrar_accion(
            request,
            "Pagar factura",
            f"Factura pagada con metodo {metodo.nombre}.",
            factura=factura,
            cita=factura.cita
        )

        return redirect('ver_factura', id_factura=id_factura)

    return render(request, "facturas/pagar_factura.html", {
        "factura": factura,
        "metodos": metodos
    })



@login_required
def anular_factura(request, id_factura):

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
