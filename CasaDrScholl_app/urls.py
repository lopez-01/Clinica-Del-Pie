# CasaDrScholl_app/urls.py

from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.home, name='home'),
    path('web/', views.landing, name='landing'),
    path('citas/iniciar/', views.iniciar_cita, name='iniciar_cita'),
    path('solicitud-web/', views.crear_solicitud_web, name='crear_solicitud_web'),
    path('inbox/', views.inbox, name='inbox'),
    path('propuesta/rechazar/<int:id>/', views.rechazar_propuesta, name='rechazar_propuesta'),
    path('crear-cita-desde-propuesta/<int:id>/', views.crear_cita_desde_propuesta, name='crear_cita_desde_propuesta'),

    path('administrativo/', views.administrativo_view, name='administrativo'),
    path('operativo/', views.resumen_citas, name='operativo'),
    path('empleados/', views.gestionar_empleados, name='gestionar_empleados'),
    path('empleados/<int:id_personal>/', views.detalle_empleado, name='detalle_empleado'),
    path('servicios/', views.servicios, name='servicios'),

    # Clientes
    path('clientes/', views.listar_clientes, name='listar_clientes'),
    path('clientes/crear/', views.crear_cliente, name='crear_cliente'),
    path('clientes/editar/<int:id_cliente>/', views.editar_cliente, name='editar_cliente'),
    path('clientes/eliminar/<int:id_cliente>/', views.eliminar_cliente, name='eliminar_cliente'),

    # Citas
    path('citas/seleccionar_servicios/', views.seleccionar_servicios, name='seleccionar_servicios'),
    path('citas/crear_cita_cliente/', views.crear_cita_cliente, name='crear_cita_cliente'),
    path('citas/seleccionar_operativo/', views.seleccionar_operativo, name='seleccionar_operativo'),
    path('citas/agenda_operativo/', views.agenda_operativo, name='agenda_operativo'),
    path('citas/bloques-hora/', views.bloques_hora_ajax, name='bloques_hora_ajax'),
    path('citas/seleccionar_fecha_hora/', views.seleccionar_fecha_hora, name='seleccionar_fecha_hora'),
    path('citas/seleccionar-bloque/', views.seleccionar_bloque, name='seleccionar_bloque'),
    path('citas/confirmar_cita/', views.confirmar_cita, name='confirmar_cita'),
    path('citas/cerrar/<int:id_cita>/', views.cerrar_cita, name='cerrar_cita'),
    path('citas/<int:id_cita>/', views.detalle_cita, name='detalle_cita'),
    path('citas/generar_factura/<int:id_cita>/', views.generar_factura, name='generar_factura'),
    path('citas/ver_factura/<int:id_factura>/', views.ver_factura, name='ver_factura'),
    path('citas/ver_factura/<int:id_factura>/pdf/', views.descargar_factura_pdf, name='descargar_factura_pdf'),
    path('citas/listar_facturas/', views.listar_facturas, name='listar_facturas'),
    path('citas/pagar_factura/<int:id_factura>/', views.pagar_factura, name='pagar_factura'),
    path('citas/anular_factura/<int:id_factura>/', views.anular_factura, name='anular_factura'),
    path('calendario/', views.calendar_view, name='calendar'),
    path('citas/crear-ajax/', views.crear_cita_ajax, name='crear_cita_ajax'),
    path("clientes/buscar/", views.buscar_clientes, name="buscar_clientes"),
    path("citas/<int:id_cita>/json/", views.obtener_cita_ajax, name="obtener_cita_ajax"),
    path("citas/editar-ajax/", views.editar_cita_ajax, name="editar_cita_ajax"),
    

    #Login
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

]

