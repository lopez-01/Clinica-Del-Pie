from django.db import models
from django.contrib.auth.models import User



# ----------------------
#   TABLA: PERSONAL
# ----------------------
class Personal(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, db_column='user_id')

    id_personal = models.AutoField(primary_key=True, db_column='ID_Personal')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    n_telefonico = models.CharField(max_length=15, blank=True, null=True)
    fecha_ingreso = models.DateField()
    email = models.EmailField(max_length=150, blank=True, null=True)

    class Meta:
        db_table = 'Personal'
        managed = False

    def __str__(self):
        return f"{self.nombre} {self.apellido}"


# ----------------------
#   TABLA: CLIENTE
# ----------------------
class Cliente(models.Model):
    id_cliente = models.AutoField(primary_key=True, db_column='ID_Cliente')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    sexo = models.CharField(max_length=10, blank=True, null=True)
    n_telefono = models.CharField(max_length=15, blank=True, null=True)
    email = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        db_table = 'Cliente'
        managed = False

    @property
    def sexo_display(self):
        # Para registros anteriores con el valor retirado NB, no mostrar esa opción en la interfaz.
        return {
            'M': 'Masculino',
            'F': 'Femenino',
            'O': 'Otros',
        }.get(self.sexo, 'Sin dato')

    def __str__(self):
        return f"{self.nombre} {self.apellido}"


# ----------------------
#   TABLA: SERVICIO
# ----------------------
class Servicio(models.Model):
    id_servicio = models.AutoField(primary_key=True, db_column='ID_Servicio')  # PK explícito
    nombre = models.CharField(max_length=100)
    duracion = models.IntegerField(blank=True, null=True)
    descripcion = models.CharField(max_length=200, blank=True, null=True)
    costo = models.DecimalField(max_digits=10, decimal_places=2, db_column='Costo')
    comision_operativo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        db_column='Comision_Operativo'
    )

    class Meta:
        db_table = 'Servicio'
        managed = False

    def __str__(self):
        return self.nombre


# ----------------------
#   TABLA: FACTURA
# ----------------------
class Factura(models.Model):
    
    id_factura = models.AutoField(primary_key=True, db_column='ID_Factura')

    cita = models.ForeignKey('Cita', on_delete=models.CASCADE, db_column='ID_Cita', to_field='id_cita', null=True,blank=True)

    fecha_factura = models.DateTimeField(db_column='Fecha_Factura', null=True, blank=True)

    estado_pago = models.ForeignKey(
        'EstadoPago',
        on_delete=models.SET_NULL,
        db_column='ID_EstadoPago',
        to_field='id_estadopago',
        null=True,
        blank=True
    )

    total = models.DecimalField(max_digits=10, decimal_places=2)

    metodo_pago = models.ForeignKey(
        'MetodoPago',
        on_delete=models.SET_NULL,
        db_column='ID_MetodoPago',
        to_field='id_metodopago',
        null=True,
        blank=True
    )

    class Meta:
        db_table = 'Factura'
        managed = False

    def __str__(self):
        return f"Factura #{self.id_factura}"


class PagoFactura(models.Model):
    """Registro individual de un pago aplicado a una factura."""
    id_pago = models.AutoField(primary_key=True, db_column='ID_PagoFactura')
    factura = models.ForeignKey(
        Factura,
        on_delete=models.PROTECT,
        db_column='ID_Factura',
        to_field='id_factura',
        related_name='pagos'
    )
    fecha_pago = models.DateTimeField(db_column='Fecha_Pago')
    monto = models.DecimalField(max_digits=10, decimal_places=2, db_column='Monto')
    metodo_pago = models.ForeignKey(
        'MetodoPago',
        on_delete=models.PROTECT,
        db_column='ID_MetodoPago',
        to_field='id_metodopago'
    )

    class Meta:
        db_table = 'PagoFactura'
        managed = False
        ordering = ['-fecha_pago', '-id_pago']

    def __str__(self):
        return f"Pago #{self.id_pago} - Factura #{self.factura_id}"
    

# ----------------------
#   TABLA: ESTADO
# ----------------------
class Estado(models.Model):
    id_estado = models.AutoField(primary_key=True, db_column='ID_Estado')
    nombre_estado = models.CharField(max_length=20, db_column='Nombre_Estado')

    class Meta:
        db_table = 'Estado'
        managed = False

    def __str__(self):
        return self.nombre_estado


# ----------------------
#   TABLA: METODO PAGO
# ----------------------
class MetodoPago(models.Model):
    id_metodopago = models.AutoField(
        primary_key=True,
        db_column='ID_MetodoPago'
    )

    nombre = models.CharField(
        max_length=50,
        db_column='Nombre'
    )

    class Meta:
        db_table = 'MetodoPago'
        managed = False

    def __str__(self):
        return self.nombre
    

# ----------------------
#   TABLA: ESTADO PAGO
# ----------------------
class EstadoPago(models.Model):
    id_estadopago = models.AutoField(
        primary_key=True,
        db_column='ID_EstadoPago'
    )

    nombre = models.CharField(
        max_length=50,
        db_column='Nombre'
    )

    class Meta:
        db_table = 'EstadoPago'
        managed = False

    def __str__(self):
        return self.nombre

# ----------------------
#   TABLA: CITAS
# ----------------------
class Cita(models.Model):
    id_cita = models.AutoField(primary_key=True, db_column='ID_Cita')
    fechahora = models.DateTimeField(db_column='FechaHora')

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        db_column='ID_Cliente',
        to_field='id_cliente'
    )

    operativo = models.ForeignKey(
        'Operativo',
        on_delete=models.CASCADE,
        db_column='ID_Operativo',
        to_field='id_operativo',
        null=True,
        blank=True
    )

    estado = models.ForeignKey(
        Estado,
        on_delete=models.CASCADE,
        db_column='ID_Estado',
        to_field='id_estado',
        null=True,
        blank=True
    )

    cita_anterior = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        db_column='ID_Cita_Anterior',
        to_field='id_cita',
        related_name='seguimientos',
        null=True,
        blank=True
    )

    servicios = models.ManyToManyField(Servicio, through="CitaServicio")

    duracion_minutos = models.IntegerField(default=30)

    class Meta:
        db_table = 'Citas'
        managed = False

    def __str__(self):
        return f"Cita {self.id_cita} - {self.cliente}"

# ----------------------
#   TABLA: ADMINISTRATIVO
# ----------------------
class Administrativo(models.Model):
    id_administrativo = models.AutoField(primary_key=True, db_column='ID_Administrativo')  # PK explícito
    personal = models.ForeignKey(
        Personal,
        on_delete=models.CASCADE,
        db_column='ID_Personal',
        to_field='id_personal'
    )

    class Meta:
        db_table = 'Administrativo'
        managed = False

    def __str__(self):
        return f"Administrativo {self.personal}"


# ----------------------
#   TABLA: REGISTRO
# ----------------------
class Registro(models.Model):
    id_registro = models.AutoField(primary_key=True, db_column='ID_Registro')  # PK explícito
    ingreso = models.DecimalField(max_digits=10, decimal_places=2)
    sueldos = models.DecimalField(max_digits=10, decimal_places=2)
    administrativo = models.ForeignKey(
        Administrativo,
        on_delete=models.CASCADE,
        db_column='ID_Administrativo',
        to_field='id_administrativo'
    )

    class Meta:
        db_table = 'Registro'
        managed = False

    def __str__(self):
        return f"Registro {self.id_registro}"


# ----------------------
#   TABLA: OPERATIVO
# ----------------------
class Operativo(models.Model):
    id_operativo = models.AutoField(primary_key=True, db_column='ID_Operativo')  # PK explícito
    personal = models.ForeignKey(
        Personal,
        on_delete=models.CASCADE,
        db_column='ID_Personal',
        to_field='id_personal'
    )

    class Meta:
        db_table = 'Operativo'
        managed = False

    def __str__(self):
        return f"Operativo {self.personal}"


# -------------------------------
#   TABLA: PAGO DE SERVICIOS
# -------------------------------
class PagoDeServicios(models.Model):
    id_pago = models.AutoField(primary_key=True, db_column='ID_Pago')  # PK explícito
    agua = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    luz = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    internet = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    registro = models.ForeignKey(
        Registro,
        on_delete=models.CASCADE,
        db_column='ID_Registro',
        to_field='id_registro'
    )

    class Meta:
        db_table = 'PagoDeServicios'
        managed = False

    def __str__(self):
        return f"Pago Servicio {self.id_pago}"


# -------------------------------
#   TABLA: GASTO MENSUAL
# -------------------------------
class GastoMensual(models.Model):
    id_gasto = models.AutoField(primary_key=True, db_column='ID_Gasto')
    mes = models.IntegerField(db_column='Mes')
    anio = models.IntegerField(db_column='Anio')
    luz = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Luz')
    agua = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Agua')
    internet = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Internet')
    materiales = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Materiales')
    salarios = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Salarios')
    otros = models.DecimalField(max_digits=10, decimal_places=2, default=0, db_column='Otros')
    fecha_registro = models.DateTimeField(db_column='Fecha_Registro', blank=True, null=True)
    cerrado = models.BooleanField(default=False, db_column='Cerrado')
    ingresos_cierre = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, db_column='Ingresos_Cierre')
    salarios_cierre = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True, db_column='Salarios_Cierre')
    facturas_pagadas_cierre = models.IntegerField(blank=True, null=True, db_column='Facturas_Pagadas_Cierre')
    facturas_pendientes_cierre = models.IntegerField(blank=True, null=True, db_column='Facturas_Pendientes_Cierre')
    detalle_comisiones_cierre = models.TextField(blank=True, null=True, db_column='Detalle_Comisiones_Cierre')
    ingresos_por_metodo_cierre = models.TextField(blank=True, null=True, db_column='Ingresos_Por_Metodo_Cierre')
    administrativo = models.ForeignKey(
        Administrativo,
        on_delete=models.SET_NULL,
        db_column='ID_Administrativo',
        to_field='id_administrativo',
        null=True,
        blank=True
    )

    class Meta:
        db_table = 'GastoMensual'
        managed = False

    def __str__(self):
        return f"Gastos {self.mes}/{self.anio}"


class Comision(models.Model):
    ESTADOS = [
        ('Calculada', 'Calculada'),
        ('Pendiente', 'Pendiente'),
        ('Pagada', 'Pagada'),
    ]

    id_comision = models.AutoField(primary_key=True, db_column='ID_Comision')
    periodo = models.CharField(max_length=7, db_column='Periodo')
    monto = models.DecimalField(max_digits=10, decimal_places=2, db_column='Monto')
    estado = models.CharField(max_length=20, db_column='Estado', choices=ESTADOS, default='Calculada')
    personal = models.ForeignKey(
        Personal,
        on_delete=models.PROTECT,
        db_column='ID_Personal',
        to_field='id_personal',
        related_name='comisiones'
    )

    class Meta:
        db_table = 'Comision'
        managed = False
        constraints = [
            models.UniqueConstraint(fields=['periodo', 'personal'], name='UQ_Comision_Periodo_Personal')
        ]
        ordering = ['-periodo', 'personal__apellido', 'personal__nombre']

    def __str__(self):
        return f"Comisión {self.periodo} - {self.personal}"


# -------------------------------
#   TABLA: HISTORIAL DE ACCIONES
# -------------------------------
class HistorialAccion(models.Model):
    id_accion = models.AutoField(primary_key=True, db_column='ID_Accion')
    usuario = models.CharField(max_length=150, db_column='Usuario')
    accion = models.CharField(max_length=100, db_column='Accion')
    detalle = models.CharField(max_length=500, blank=True, null=True, db_column='Detalle')
    fecha = models.DateTimeField(db_column='Fecha')
    id_cita = models.IntegerField(blank=True, null=True, db_column='ID_Cita')
    id_factura = models.IntegerField(blank=True, null=True, db_column='ID_Factura')
    mes = models.IntegerField(blank=True, null=True, db_column='Mes')
    anio = models.IntegerField(blank=True, null=True, db_column='Anio')

    class Meta:
        db_table = 'HistorialAccion'
        managed = False

    def __str__(self):
        return f"{self.accion} - {self.usuario}"


# -------------------------------
#   TABLA INTERMEDIA: CITA - SERVICIO
# -------------------------------
class CitaServicio(models.Model):
    id_citaservicio = models.AutoField(primary_key=True, db_column='ID_CitaServicio')  # PK explícito
    cita = models.ForeignKey(
        Cita,
        on_delete=models.CASCADE,
        db_column='ID_Cita',
        to_field='id_cita'
    )
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.CASCADE,
        db_column='ID_Servicio',
        to_field='id_servicio'
    )

    class Meta:
        unique_together = ('cita', 'servicio')
        db_table = 'CitaServicio'
        managed = False

    def __str__(self):
        return f"Cita {self.cita.id_cita} - Servicio {self.servicio.nombre}"


# -------------------------------
#   TABLA INTERMEDIA: SERVICIO - FACTURA
# -------------------------------
class ServicioFactura(models.Model):
    id_serviciofactura = models.AutoField(primary_key=True, db_column='ID_ServicioFactura')  # PK explícito
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.CASCADE,
        db_column='ID_Servicio',
        to_field='id_servicio'
    )
    factura = models.ForeignKey(
        Factura,
        on_delete=models.CASCADE,
        db_column='ID_Factura',
        to_field='id_factura'
    )
    
    class Meta:
        db_table = 'Serviciofactura'
        managed = False

    def __str__(self):
        return f"{self.servicio.nombre} -> Factura {self.factura.id_factura}"


# -------------------------------
#   TABLA INTERMEDIA: SERVICIO - OPERATIVO
# -------------------------------
class ServicioOperativo(models.Model):
    id_serviciooperativo = models.AutoField(primary_key=True, db_column='ID_ServicioOperativo')  # PK explícito
    operativo = models.ForeignKey(
        Operativo,
        on_delete=models.CASCADE,
        db_column='ID_Operativo',
        to_field='id_operativo'
    )
    servicio = models.ForeignKey(
        Servicio,
        on_delete=models.CASCADE,
        db_column='ID_Servicio',
        to_field='id_servicio'
    )

    class Meta:
        db_table = 'ServicioOperativo'
        managed = False

    def __str__(self):
        return f"{self.operativo} -> {self.servicio.nombre}"


# -------------------------------
#   TABLA: EXPEDIENTE CLINICO
# -------------------------------
class ExpedienteClinico(models.Model):
    id_expediente = models.AutoField(primary_key=True, db_column='ID_RegistroClinico')  # PK existente en SQL Server
    cita = models.ForeignKey(
        Cita,
        on_delete=models.SET_NULL,
        db_column='ID_Cita',
        to_field='id_cita',
        related_name='expedientes',
        null=True,
        blank=True
    )
    nombre_medico = models.CharField(max_length=100, blank=True, null=True)
    progreso = models.CharField(max_length=200, blank=True, null=True)
    fecha_de_atencion = models.DateField(blank=True, null=True)
    tratamiento = models.CharField(max_length=200, blank=True, null=True)
    diagnostico = models.CharField(max_length=200, blank=True, null=True)
    observaciones = models.CharField(max_length=500, blank=True, null=True, db_column='Observaciones')
    operativo = models.ForeignKey(
        Operativo,
        on_delete=models.CASCADE,
        db_column='ID_Operativo',
        to_field='id_operativo'
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        db_column='ID_Cliente',
        to_field='id_cliente'
    )

    class Meta:
        db_table = 'ExpedienteClinico'
        managed = False

    def __str__(self):
        return f"Expediente {self.id_expediente}"


class Propuestas_Citas(models.Model):
    ID_Propuesta = models.AutoField(primary_key=True)
    Nombre = models.CharField(max_length=100)
    Email = models.CharField(max_length=150)
    Numero_telefonico = models.CharField(max_length=20, null=True, blank=True)
    Propuesta_De_Dia = models.TextField()

    Estado = models.CharField(max_length=20, default='Pendiente')
    Fecha = models.DateTimeField()
    Atendida = models.BooleanField(default=False)

    class Meta:
        db_table = 'Propuestas_Citas'
        managed = False

    def __str__(self):
        return f"{self.Nombre} {self.Email}"
