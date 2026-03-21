from django import forms
from .models import Cliente, Servicio, Operativo




class ClienteForm(forms.ModelForm):
    SEXO_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Femenino'),
        ('NB', 'No binario'),
        ('O', 'Otros'),
    ]

    sexo = forms.ChoiceField(
    choices=SEXO_CHOICES,
    widget=forms.Select(attrs={'required': True, 'class': 'custom-select'}),
    label="Sexo"
)


    n_telefono = forms.CharField(
        max_length=8,
        min_length=8,
        required=True,
        label="Número de Teléfono",
        widget=forms.TextInput(attrs={'pattern': r'\d{8}', 'title': 'Debe contener exactamente 8 números'})
    )

    class Meta:
        model = Cliente
        fields = ['nombre', 'apellido', 'sexo', 'n_telefono', 'email']

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
        widget=forms.Select(attrs={'class': 'custom-select', 'required': True})
    )



from datetime import time

class FechaHoraForm(forms.Form):
    fecha = forms.DateField(
        widget=forms.SelectDateWidget,
        required=True,
        label="Seleccione fecha"
    )
    
    HORA_CHOICES = [
        (time(hour=h).strftime('%H:%M'), time(hour=h).strftime('%I:%M %p'))
        for h in range(8, 18)  # De 8 a 17 (5pm)
    ]

    hora = forms.ChoiceField(
        choices=HORA_CHOICES,
        required=True,
        label="Seleccione hora",
        widget=forms.Select(attrs={'class': 'custom-select'})  # Usa la clase que ya usas para los selects bonitos
    )


