from django.contrib import messages
from django.db.models import Q
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from .models import (
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
from datetime import datetime
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required


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
    if request.method == 'POST':
        Propuestas_Citas.objects.create(
            Nombre=request.POST.get('nombre'),
            Email=request.POST.get('email'),
            Numero_telefonico=request.POST.get('numero_telefonico'),
            Propuesta_De_Dia=request.POST.get('propuesta_de_dia'),
            Fecha=timezone.now()
        )
        return render(request, 'web/index.html', {
            'success': True
        })

    messages.success(request, "Solicitud enviada correctamente")
    return redirect('landing')


def inbox(request):
    solicitudes = Propuestas_Citas.objects.filter(
        Atendida=False
    ).order_by('-Fecha')

    return render(request, 'administrativo/inbox.html', {
        'solicitudes': solicitudes
    })

def rechazar_propuesta(request, id):
    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    propuesta.Atendida = True
    propuesta.save()

    return redirect('inbox')


def crear_cita_desde_propuesta(request, id):
    propuesta = get_object_or_404(Propuestas_Citas, ID_Propuesta=id)

    request.session['cliente_temp'] = {
        'nombre': propuesta.Nombre,
        'apellido': '',
        'telefono': propuesta.Numero_telefonico,
        'email': propuesta.Email,
    }

    request.session['propuesta_id'] = propuesta.ID_Propuesta
    request.session['flujo_origen'] = 'inbox'

    return redirect('seleccionar_servicios')



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
            servicios_ids = [s.id_servicio for s in form.cleaned_data['servicios']]
            request.session['servicios_seleccionados'] = servicios_ids
            request.session.modified = True

            origen = request.session.get('flujo_origen')

            if origen == 'inbox':
                return redirect('seleccionar_operativo')
            else:
                return redirect('crear_cita_cliente')
    else:
        form = ServicioSelectForm()

    return render(request, 'citas/seleccionar_servicios.html', {'form': form})


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
            return redirect('seleccionar_fecha_hora')
    else:
        form = OperativoSelectForm()
    return render(request, 'citas/seleccionar_operativo.html', {'form': form})


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

        # 🔒 VALIDAR SI EL HORARIO YA ESTÁ OCUPADO
        conflicto = Cita.objects.filter(
            operativo=operativo,
            fechahora=fechahora
        ).exists()

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
            operativo=operativo
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
        'detalle_cita',
        id_cita=cita.id_cita
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
def pagar_factura(request, id_factura):

    factura = get_object_or_404(
        Factura.objects.select_related('estado_pago'),
        id_factura=id_factura
    )

    if factura.estado_pago and factura.estado_pago.nombre == 'Pagada':
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
