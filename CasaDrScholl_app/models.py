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

    def __str__(self):
        return f"{self.nombre} {self.apellido}"


# ----------------------
#   TABLA: SERVICIO
# ----------------------
class Servicio(models.Model):
    id_servicio = models.AutoField(primary_key=True, db_column='ID_Servicio')  # PK explícito
    nombre = models.CharField(max_length=100)
    duracion = models.CharField(max_length=50, blank=True, null=True)
    descripcion = models.CharField(max_length=200, blank=True, null=True)

    class Meta:
        db_table = 'Servicio'
        managed = False

    def __str__(self):
        return self.nombre


# ----------------------
#   TABLA: FACTURA
# ----------------------
class Factura(models.Model):
    id_factura = models.AutoField(primary_key=True, db_column='ID_Factura')  # PK explícito
    total = models.DecimalField(max_digits=10, decimal_places=2)
    metodo_pago = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'Factura'
        managed = False

    def __str__(self):
        return f"Factura #{self.id_factura}"


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
    servicios = models.ManyToManyField(Servicio, through="CitaServicio")

    class Meta:
        db_table = 'Citas'
        managed = False

    def str(self):
        return f"Cita {self.id_cita} - {self.cliente}"

# ----------------------
#   TABLA: ESTADO
# ----------------------
class Estado(models.Model):
    id_estado = models.AutoField(primary_key=True, db_column='ID_Estado')  # PK explícito
    nombre_estado = models.CharField(max_length=50, db_column='Nombre_Estado')
    cita = models.ForeignKey(
        Cita,
        on_delete=models.CASCADE,
        db_column='ID_Cita',
        to_field='id_cita'
    )

    class Meta:
        db_table = 'Estado'
        managed = False

    def __str__(self):
        return f"{self.nombre_estado} (Cita {self.cita.id_cita})"

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
    costos = models.DecimalField(max_digits=10, decimal_places=2)

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
    id_expediente = models.AutoField(primary_key=True, db_column='ID_Expediente')  # PK explícito
    nombre_medico = models.CharField(max_length=100, blank=True, null=True)
    progreso = models.CharField(max_length=200, blank=True, null=True)
    fecha_de_atencion = models.DateField(blank=True, null=True)
    tratamiento = models.CharField(max_length=200, blank=True, null=True)
    diagnostico = models.CharField(max_length=200, blank=True, null=True)
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
