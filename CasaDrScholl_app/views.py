from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
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
)
from .forms import ClienteForm, ServicioSelectForm, OperativoSelectForm, FechaHoraForm
from django.urls import reverse
from datetime import date, datetime, timedelta, time
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
import json
from django.db.models import Q

MAX_SOLICITUDES_WEB_POR_DIA = 5


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

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            try:
                personal = Personal.objects.get(user=user)

                if Administrativo.objects.filter(personal=personal).exists():
                    return redirect('administrativo')
                elif Operativo.objects.filter(personal=personal).exists():
                    return redirect('operativo')
                else:
                    return redirect('home')

            except Personal.DoesNotExist:
                return redirect('home')
        else:
            error = "Usuario o contraseña incorrectos"

    return render(request, 'login.html', {'error': error})


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

@login_required
def administrativo_view(request):
    return render(request, 'administrativo.html')


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
        return render(request, 'web/index.html', status=400)

    if len(telefono) != 8:
        messages.error(
            request,
            "El telefono debe tener exactamente 8 numeros."
        )
        return render(request, 'web/index.html', status=400)

    if email:
        try:
            validate_email(email)
        except ValidationError:
            messages.error(
                request,
                "Ingrese un correo valido o deje el campo vacio."
            )
            return render(request, 'web/index.html', status=400)

    if Propuestas_Citas.objects.filter(
        Numero_telefonico=telefono,
        Atendida=False
    ).exists():
        messages.warning(
            request,
            "Ya tienes una solicitud pendiente. La clinica se pondra en contacto contigo para confirmarla."
        )
        return render(request, 'web/index.html', status=400)

    hoy = timezone.localdate()
    inicio_dia = timezone.make_aware(
        datetime.combine(hoy, time.min)
    )
    fin_dia = inicio_dia + timedelta(days=1)

    solicitudes_hoy = Propuestas_Citas.objects.filter(
        Numero_telefonico=telefono,
        Fecha__gte=inicio_dia,
        Fecha__lt=fin_dia
    ).count()

    if solicitudes_hoy >= MAX_SOLICITUDES_WEB_POR_DIA:
        messages.error(
            request,
            "Llegaste al limite de solicitudes por hoy. Intenta de nuevo manana o contacta directamente a la clinica."
        )
        return render(request, 'web/index.html', status=429)

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
    if request.method == 'POST':
        form = OperativoSelectForm(request.POST)
        if form.is_valid():
            operativo_id = form.cleaned_data['operativo'].id_operativo
            request.session['operativo_id'] = operativo_id
            request.session['flujo_origen'] = 'calendar'
            return redirect('calendar')
    else:
        form = OperativoSelectForm()
    return render(request, 'citas/seleccionar_operativo.html', {'form': form})



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

    fecha_str = request.GET.get('fecha')
    base_date = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else date.today()

    offset = int(request.GET.get("offset", 0))

    # 🔥 aplicar desplazamiento de semanas
    base_date = base_date + timedelta(weeks=offset)

    # 🔥 construir semana (lunes a domingo)
    inicio_semana = base_date - timedelta(days=base_date.weekday())

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
        })

    return render(request, "citas/calendar.html", {
        "operativo": operativo,
        "dias": dias,
        "horas": horas,
        "eventos": eventos,
        "offset": offset,
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

    nombre = data.get("nombre", "").strip()
    apellido = data.get("apellido", "").strip()
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

    if not cliente_id:
        if not nombre or not apellido or not telefono or not sexo:
            return JsonResponse({
                "error": True,
                "mensaje": "Nombre, apellido, telefono y sexo son obligatorios."
            })

        if not telefono.isdigit() or len(telefono) != 8:
            return JsonResponse({
                "error": True,
                "mensaje": "El telefono debe tener exactamente 8 numeros."
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

    fecha = data["fecha"]
    hora = data["hora"]
    duracion = int(data["duracion"])

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

    if duracion <= 0:

        return JsonResponse({
            "error": True,
            "mensaje": "La duración debe ser mayor que 0."
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

    cita.save()

    return JsonResponse({
        "ok": True
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
        'total_citas_hoy': total_citas_hoy,
        'citas_confirmadas': citas_confirmadas,
        'citas_canceladas': citas_canceladas,
        'estado_filtro': estado_filtro
    })


def cerrar_cita(request, id_cita):
    
    cita = get_object_or_404(Cita, id_cita=id_cita)

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
        Cita,
        id_cita=id_cita
    )

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
def anular_factura(request, id_factura):

    factura = get_object_or_404(
        Factura,
        id_factura=id_factura
    )

    estado_anulada = EstadoPago.objects.get(
        nombre='Anulada'
    )

    factura.estado_pago = estado_anulada
    factura.save()

    return redirect(
        'ver_factura',
        id_factura=factura.id_factura
    )


@login_required
def pagar_factura(request, id_factura):

    factura = get_object_or_404(Factura, id_factura=id_factura)

    # evitar pagar dos veces
    if factura.estado_pago.nombre == "Pagada":  
        return redirect('ver_factura', id_factura=id_factura)

    metodos = MetodoPago.objects.all()

    if request.method == "POST":

        metodo_id = request.POST.get("metodo_pago")

        metodo = get_object_or_404(MetodoPago, id_metodopago=metodo_id)

        estado_pagada = get_object_or_404(EstadoPago, nombre='Pagada')

        factura.estado_pago = estado_pagada
        factura.metodo_pago = metodo
        factura.save()

        return redirect('ver_factura', id_factura=id_factura)

    return render(request, "facturas/pagar_factura.html", {
        "factura": factura,
        "metodos": metodos
    })



@login_required
def anular_factura(request, id_factura):

    factura = get_object_or_404(
        Factura,
        id_factura=id_factura
    )

    estado_anulada = get_object_or_404(
        EstadoPago,
        nombre='Anulada'
    )

    factura.estado_pago = estado_anulada
    factura.save()

    return redirect(
        'ver_factura',
        id_factura=factura.id_factura
    )
