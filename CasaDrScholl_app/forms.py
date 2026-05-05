from django import forms
from .models import Cliente, Servicio, Operativo




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

from datetime import time, datetime

class FechaHoraForm(forms.Form):
    fecha = forms.DateField(
        widget=forms.DateInput(attrs={
    'type': 'date',
    'class': 'form-control',
    'min': datetime.today().strftime('%Y-%m-%d')

        }),
        required=True,
        label="Seleccione fecha"
    )

    HORA_CHOICES = [
        (time(hour=h).strftime('%H:%M'), time(hour=h).strftime('%I:%M %p'))
        for h in range(8, 18)
    ]

    hora = forms.ChoiceField(
        choices=HORA_CHOICES,
        required=True,
        label="Seleccione hora",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    

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