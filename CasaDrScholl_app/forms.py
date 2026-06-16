from django import forms
from .models import Cliente, Servicio, Operativo
from datetime import time, datetime, date




class ClienteForm(forms.ModelForm):
    SEXO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('MD', 'Ola wenas'),
        ('HP', 'Helicoptero Apache'),
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

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and '@' not in email:
            raise forms.ValidationError("El correo electrónico debe contener '@'")
        return email


class ServicioSelectForm(forms.Form):
    servicios = forms.ModelMultipleChoiceField(
        queryset=Servicio.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label="Seleccione los servicios"
    )


class OperativoSelectForm(forms.Form):
    operativo = forms.ModelChoiceField(
        queryset=Operativo.objects.all(),
        required=True,
        label="Seleccione un operativo",
        widget=forms.Select(attrs={'class': 'form-select'})
    )


class FechaHoraForm(forms.Form):

    fecha = forms.DateField(
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'form-control',
                'min': datetime.today().strftime('%Y-%m-%d')
            },
            format='%Y-%m-%d'
        ),
        initial=date.today,
        required=True,
        label="Seleccione fecha"
    )

    HORA_CHOICES = []

    for hora in range(8, 17):
        for minuto in [0, 15, 30, 45]:

            valor = f"{hora:02d}:{minuto:02d}"

            etiqueta = datetime.strptime(
                valor,
                "%H:%M"
            ).strftime("%I:%M %p")

            HORA_CHOICES.append(
                (valor, etiqueta)
            )

    valor = "17:00"

    etiqueta = datetime.strptime(
        valor,
        "%H:%M"
    ).strftime("%I:%M %p")

    HORA_CHOICES.append(
        (valor, etiqueta)
    )

    hora = forms.ChoiceField(
        choices=HORA_CHOICES,
        required=True,
        label="Seleccione hora",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    def clean_fecha(self):
        fecha = self.cleaned_data['fecha']

        if fecha < date.today():
            raise forms.ValidationError(
                "No se pueden registrar citas en fechas pasadas."
            )
        
        if fecha.weekday() == 0:
            raise forms.ValidationError(
                "La clínica no atiende los lunes."
            )

        return fecha
    
    def clean(self):
        cleaned_data = super().clean()

        fecha = cleaned_data.get('fecha')
        hora = cleaned_data.get('hora')

        if fecha and hora:

            ahora = datetime.now()

            fecha_hora_cita = datetime.strptime(
                f"{fecha} {hora}",
                "%Y-%m-%d %H:%M"
            )

            if fecha_hora_cita < ahora:
                raise forms.ValidationError(
                    "No se puede registrar una cita en una hora que ya pasó."
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

