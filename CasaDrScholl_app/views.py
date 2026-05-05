from urllib import request
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from .models import Servicio, Cliente, Cita, Operativo, Personal, Administrativo, Estado, Propuestas_Citas
from .forms import ClienteForm, ServicioSelectForm, OperativoSelectForm, FechaHoraForm, PropuestaCitaForm
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


def lista_clientes(request):
    return render(request, 'lista_clientes.html')


@login_required
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
            return redirect('crear_cita_cliente')
    else:
        form = ServicioSelectForm()
    return render(request, 'citas/seleccionar_servicios.html', {'form': form})


def crear_cita_cliente(request):
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


def confirmar_cita(request):
    servicios_ids = request.session.get('servicios_seleccionados')
    cliente_id = request.session.get('cliente_id')
    operativo_id = request.session.get('operativo_id')
    fecha_str = request.session.get('fecha')
    hora_str = request.session.get('hora')

    if not all([servicios_ids, cliente_id, operativo_id, fecha_str, hora_str]):
        return redirect('seleccionar_servicios')

    cliente = get_object_or_404(Cliente, id_cliente=cliente_id)
    operativo = get_object_or_404(Operativo, id_operativo=operativo_id)
    servicios = Servicio.objects.filter(id_servicio__in=servicios_ids)

    fechahora = datetime.strptime(f"{fecha_str} {hora_str}", '%Y-%m-%d %H:%M')

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

        # ✅ Crear la cita (solo si NO hay conflicto)
        cita = Cita.objects.create(
            fechahora=fechahora,
            cliente=cliente,
            operativo=operativo
        )

        # Asignar los servicios seleccionados
        cita.servicios.set(servicios)

        # 🔹 ESTADO INICIAL CORRECTO
        try:
            estado_programada = Estado.objects.get(nombre_estado='Programada')
            cita.estado = estado_programada
            cita.save()
        except Estado.DoesNotExist:
            pass
        # Limpiar sesión
        for key in ['servicios_seleccionados', 'cliente_id', 'operativo_id', 'fecha', 'hora']:
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
            operativo=operativo
        ).exclude(
            estado__nombre_estado='Atendida'
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
    citas_pendientes = citas.filter(estado__nombre_estado='Pendiente').count()

    return render(request, 'operativo.html', {
        'citas': citas,
        'total_citas_hoy': total_citas_hoy,
        'citas_confirmadas': citas_confirmadas,
        'citas_pendientes': citas_pendientes,
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
    """
    Muestra todos los empleados en una tabla con:
    - Nombre
    - Apellido
    - Teléfono (N_Telefonico)
    - Fecha de Ingreso (Fecha_Ingreso)
    - Email (puede estar vacío)
    """
    empleados = Personal.objects.all()
    return render(request, 'gestionar_empleados.html', {'empleados': empleados})


def Crear_Propuestas_Citas(request):
    if request.method == 'POST':
        form = PropuestaCitaForm(request.POST)
        if form.is_valid():
            Propuestas_Citas.objects.create(
                Nombre=form.cleaned_data['nombre'],
                Email=form.cleaned_data['email'],
                Numero_telefonico=form.cleaned_data['numero_telefonico'],
                Propuesta_De_Dia=form.cleaned_data['propuesta_de_dia'],

            )
            return render(request, 'web/index.html', {
            'exito': True
        })
    else:
        form = PropuestaCitaForm()

    return render(request, 'web/index.html', {'form': form})