from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from .models import (
    Cliente, Servicio, Operativo, ExpedienteClinico, Personal, MetodoPago,
    Comision, ServicioOperativo,
)
from datetime import datetime
from .booking_rules import (
    HORA_APERTURA_CLINICA,
    HORA_CIERRE_CLINICA,
    cita_en_horario_laboral,
    fecha_en_rango_agenda,
    fecha_laboral,
    obtener_rango_agenda,
)
import re




class ClienteForm(forms.ModelForm):
    PATRON_NOMBRE = re.compile(
        r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:[ '-][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)*$"
    )

    SEXO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('O', 'Otros'),
    ]

    sexo = forms.ChoiceField(
        choices=SEXO_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select', 'required': True}),
        label="Sexo"
    )

    n_telefono = forms.CharField(
        max_length=8,
        min_length=8,
        required=True,
        label="Número de Teléfono",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'pattern': r'\d{8}',
            'title': 'Debe contener exactamente 8 números'
        })
    )

    class Meta:
        model = Cliente
        fields = ['nombre', 'apellido', 'sexo', 'n_telefono', 'email']

        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'apellido': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si se edita un registro antiguo, permitir guardarlo sin exponer de nuevo
        # la opción retirada; al guardar, el dato anterior queda sin especificar.
        if self.instance and self.instance.pk and self.instance.sexo == 'NB':
            self.fields['sexo'].choices = [*self.SEXO_CHOICES, ('NB', 'Sin dato')]

    def clean_sexo(self):
        sexo = self.cleaned_data.get('sexo')
        return '' if sexo == 'NB' else sexo


    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and '@' not in email:
            raise forms.ValidationError("El correo electrónico debe contener '@'")
        return email

    def limpiar_nombre(self, valor):
        texto = " ".join((valor or "").strip().split()).title()
        letras = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", texto).lower()

        if len(letras) < 2:
            raise forms.ValidationError("Debe contener al menos dos letras.")

        if len(set(letras)) == 1:
            raise forms.ValidationError("Ingrese un nombre valido.")

        if not self.PATRON_NOMBRE.match(texto):
            raise forms.ValidationError("Solo se permiten letras, espacios, guiones o apostrofes.")

        return texto

    def clean_nombre(self):
        return self.limpiar_nombre(self.cleaned_data.get('nombre'))

    def clean_apellido(self):
        return self.limpiar_nombre(self.cleaned_data.get('apellido'))

    def clean_n_telefono(self):
        telefono = self.cleaned_data.get('n_telefono', '').strip()

        if not telefono.isdigit() or len(telefono) != 8:
            raise forms.ValidationError("El telefono debe tener exactamente 8 numeros.")

        return telefono


class PagoFacturaForm(forms.Form):
    metodo_pago = forms.ModelChoiceField(
        label='Método de pago',
        queryset=MetodoPago.objects.none(),
        empty_label='Seleccione un método',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['metodo_pago'].queryset = MetodoPago.objects.order_by('nombre')


class ComisionEstadoForm(forms.Form):
    estado = forms.ChoiceField(
        label='Estado de la comisión',
        choices=Comision.ESTADOS,
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class ServicioSelectForm(forms.Form):
    servicios = forms.ModelMultipleChoiceField(
        queryset=Servicio.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Seleccione los servicios"
    )


class OperativoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.personal.nombre} {obj.personal.apellido}"


class OperativoSelectForm(forms.Form):
    operativo = OperativoChoiceField(
        queryset=Operativo.objects.select_related('personal').all(),
        required=True,
        label="Seleccione un operativo",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, operativo_inicial=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operativo'].queryset = Operativo.objects.select_related(
            'personal'
        ).order_by(
            'personal__nombre',
            'personal__apellido'
        )

        if operativo_inicial:
            self.fields['operativo'].initial = operativo_inicial
            self.fields['operativo'].empty_label = None


class FechaHoraForm(forms.Form):

    fecha = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control',
            },
            format='%Y-%m-%d'
        ),
        initial=timezone.localdate,
        required=True,
        label="Seleccione fecha"
    )

    hora = forms.ChoiceField(
        choices=(),
        required=True,
        label="Seleccione hora",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def __init__(self, *args, duracion=30, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.duracion = max(int(duracion or 30), 1)
        except (TypeError, ValueError):
            self.duracion = 30

        fecha_minima, fecha_maxima = obtener_rango_agenda()
        self.fields['fecha'].widget.attrs.update({
            'min': fecha_minima.isoformat(),
            'max': fecha_maxima.isoformat(),
        })

        opciones = []
        apertura = HORA_APERTURA_CLINICA.hour * 60 + HORA_APERTURA_CLINICA.minute
        cierre = HORA_CIERRE_CLINICA.hour * 60 + HORA_CIERRE_CLINICA.minute
        for minuto_del_dia in range(apertura, cierre, 15):
            if minuto_del_dia + self.duracion > cierre:
                continue
            valor = f"{minuto_del_dia // 60:02d}:{minuto_del_dia % 60:02d}"
            etiqueta = datetime.strptime(valor, "%H:%M").strftime("%I:%M %p")
            opciones.append((valor, etiqueta))
        self.fields['hora'].choices = opciones

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']

        if fecha < timezone.localdate():
            raise forms.ValidationError(
                "No se pueden registrar citas en fechas pasadas."
            )

        if not fecha_en_rango_agenda(fecha):
            raise forms.ValidationError(
                "No se pueden registrar citas con mas de un año de anticipacion."
            )

        if not fecha_laboral(fecha):
            raise forms.ValidationError(
                "La clínica no atiende los lunes."
            )

        return fecha
    
    def clean(self):
        cleaned_data = super().clean()

        fecha = cleaned_data.get('fecha')
        hora = cleaned_data.get('hora')

        if fecha and hora:

            hora_cita = datetime.strptime(hora, "%H:%M").time()
            fecha_hora_cita = timezone.make_aware(datetime.combine(fecha, hora_cita))

            if fecha_hora_cita < timezone.localtime():
                raise forms.ValidationError(
                    "No se puede registrar una cita en una hora que ya pasó."
                )

            if not cita_en_horario_laboral(fecha_hora_cita, self.duracion):
                raise forms.ValidationError(
                    "La cita debe iniciar después de la apertura y terminar antes del cierre."
                )

        return cleaned_data


class PropuestaCitaForm(forms.Form):
    nombre = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label="Nombre"
    )

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-control'}),
        label="Email"
    )

    numero_telefonico = forms.CharField(
        max_length=15,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
        label="Número telefónico"
    )

    propuesta_de_dia = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control'}),
        label="Propuesta de día"
    )


class SeguimientoCitaForm(forms.Form):
    operativo = OperativoChoiceField(
        queryset=Operativo.objects.select_related('personal').all(),
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Personal responsable'
    )
    servicios = forms.ModelMultipleChoiceField(
        queryset=Servicio.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        label='Servicios',
        required=True
    )
    fecha = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Nueva fecha'
    )
    hora = forms.TimeField(
        widget=forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}, format='%H:%M'),
        input_formats=['%H:%M'],
        label='Nueva hora'
    )

    def __init__(self, *args, operativo_inicial=None, servicios_iniciales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['operativo'].queryset = Operativo.objects.select_related(
            'personal'
        ).order_by('personal__nombre', 'personal__apellido')
        if operativo_inicial:
            self.fields['operativo'].initial = operativo_inicial
        if servicios_iniciales:
            self.fields['servicios'].initial = servicios_iniciales


class AtencionForm(forms.ModelForm):
    class Meta:
        model = ExpedienteClinico
        fields = ['fecha_de_atencion', 'diagnostico', 'tratamiento', 'progreso', 'observaciones']
        widgets = {
            'fecha_de_atencion': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'),
            'diagnostico': forms.TextInput(attrs={'class': 'form-control'}),
            'tratamiento': forms.TextInput(attrs={'class': 'form-control'}),
            'progreso': forms.TextInput(attrs={'class': 'form-control'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class PersonalForm(forms.ModelForm):
    class Meta:
        model = Personal
        fields = ['nombre', 'apellido', 'n_telefonico', 'fecha_ingreso', 'email']
        labels = {
            'nombre': 'Nombre',
            'apellido': 'Apellido',
            'n_telefonico': 'Teléfono',
            'fecha_ingreso': 'Fecha de ingreso',
            'email': 'Correo electrónico',
        }
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-control'}),
            'apellido': forms.TextInput(attrs={'class': 'form-control'}),
            'n_telefonico': forms.TextInput(attrs={'class': 'form-control'}),
            'fecha_ingreso': forms.DateInput(
                attrs={'class': 'form-control', 'type': 'date'},
                format='%Y-%m-%d'
            ),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }


class PersonalEdicionForm(forms.ModelForm):
    username = forms.CharField(
        required=False,
        max_length=150,
        label='Usuario de acceso',
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )

    class Meta:
        model = Personal
        fields = ['n_telefonico', 'email']
        labels = {
            'n_telefonico': 'Teléfono',
            'email': 'Correo electrónico',
        }
        widgets = {
            'n_telefonico': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def __init__(
        self,
        *args,
        cuenta_usuario=None,
        operativo=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.cuenta_usuario = cuenta_usuario
        if cuenta_usuario:
            self.initial['username'] = cuenta_usuario.username
        else:
            self.fields.pop('username')

        if operativo:
            self.fields['servicios_asignados'] = forms.ModelMultipleChoiceField(
                queryset=Servicio.objects.order_by('nombre'),
                required=False,
                label='Servicios asignados',
                widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            )
            self.initial['servicios_asignados'] = ServicioOperativo.objects.filter(
                operativo=operativo
            ).values_list('servicio_id', flat=True)

        self.order_fields([
            'n_telefonico',
            'email',
            'username',
            'servicios_asignados',
        ])

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not self.cuenta_usuario:
            return username
        if not username:
            raise forms.ValidationError('El nombre de usuario no puede quedar vacío.')
        if User.objects.filter(username__iexact=username).exclude(
            pk=self.cuenta_usuario.pk
        ).exists():
            raise forms.ValidationError('Ese nombre de usuario ya está en uso.')
        return username


class SuspensionCuentaForm(forms.Form):
    DURACIONES = (
        ('1 día', '1 día'),
        ('1 semana', '1 semana'),
        ('1 mes', '1 mes'),
        ('Indefinida', 'Indefinida'),
    )

    motivo = forms.CharField(
        min_length=5,
        max_length=250,
        label='Motivo',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'maxlength': 250,
        }),
    )
    duracion_estimada = forms.ChoiceField(
        choices=DURACIONES,
        label='Plazo estimado',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    confirmar_usuario = forms.CharField(
        max_length=150,
        label='Escribe el usuario actual para confirmar',
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )

    def __init__(self, *args, usuario_esperado='', **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario_esperado = usuario_esperado

    def clean_confirmar_usuario(self):
        usuario = self.cleaned_data['confirmar_usuario'].strip()
        if usuario != self.usuario_esperado:
            raise forms.ValidationError('El usuario no coincide.')
        return usuario


