from urllib import request
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from .models import Servicio, Cliente, Cita, Operativo,  Personal, Administrativo, Estado
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
    
    error = None  # 👈 importante

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
            error = "Usuario o contraseña incorrectos"  # 👈 aquí

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

@login_required
def administrativo_view(request):
    return render(request, 'administrativo.html')


@login_required
def operativo_view(request):
    return render(request, 'operativo.html')


def lista_clientes(request):
    return render(request, 'lista_clientes.html')


def listar_clientes(request):
    clientes = Cliente.objects.all()
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
    # Para confirmar eliminación, puedes crear un template o hacer directamente el POST con JS
    return render(request, 'clientes/eliminar_confirmar.html', {'cliente': cliente})



def servicios(request):
    return render(request, 'servicios.html')


def seleccionar_servicios(request):
    print(Servicio.objects.all())
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
            # Guardar la fecha y hora en sesión para confirmar
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
        # Crear la cita
        cita = Cita.objects.create(
            fechahora=fechahora,
            cliente=cliente,
            operativo=operativo
        )
        # Asignar los servicios seleccionados
        cita.servicios.set(servicios)

        # Crear el estado inicial para la cita
        Estado.objects.create(
            nombre_estado='Abierta',  # Estado inicial
            cita=cita
        )

        # Limpiar sesión
        for key in ['servicios_seleccionados', 'cliente_id', 'operativo_id', 'fecha', 'hora']:
            if key in request.session:
                del request.session[key]

        return redirect('home')
    


    return render(request, 'citas/confirmar_cita.html', {
        'cliente': cliente,
        'operativo': operativo,
        'servicios': servicios,
        'fechahora': fechahora,
    })

def resumen_citas(request):
    estado_filtro = request.GET.get('estado')
    citas = Cita.objects.select_related('cliente').all().order_by('-fechahora')

    if estado_filtro:  # Filtrar por estado si se pasa en URL
        citas = citas.filter(estado__nombre_estado=estado_filtro)

    # Obtener fechas de hoy
    hoy = timezone.now().date()
    citas_hoy = citas.filter(fechahora__date=hoy)

    # Contadores dinámicos
    total_citas_hoy = citas_hoy.count()
    citas_confirmadas = citas_hoy.filter(estado__nombre_estado='Abierta').count()
    citas_pendientes = citas_hoy.filter(estado__nombre_estado='Pendiente').count()

    return render(request, 'operativo.html', {
        'citas': citas,
        'total_citas_hoy': total_citas_hoy,
        'citas_confirmadas': citas_confirmadas,
        'citas_pendientes': citas_pendientes,
        'estado_filtro': estado_filtro
    })

def cerrar_cita(request, id_cita):
    cita = get_object_or_404(Cita, id_cita=id_cita)
    
    estado = cita.estado_set.first()
    if estado:
        estado.nombre_estado = 'Cerrada'
        estado.save()
    
    return redirect('operativo')  

