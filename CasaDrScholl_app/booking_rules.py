from datetime import time, timedelta

from django.utils import timezone


HORA_APERTURA_CLINICA = time(8, 0)
HORA_CIERRE_CLINICA = time(17, 0)
DIAS_NO_LABORALES = {0}  # lunes


def obtener_rango_agenda():
    fecha_minima = timezone.localdate()
    return fecha_minima, fecha_minima + timedelta(days=365)


def fecha_en_rango_agenda(fecha):
    fecha_minima, fecha_maxima = obtener_rango_agenda()
    return fecha_minima <= fecha <= fecha_maxima


def fecha_laboral(fecha):
    return fecha.weekday() not in DIAS_NO_LABORALES


def cita_en_horario_laboral(inicio, duracion):
    if duracion <= 0:
        return False

    inicio_local = timezone.localtime(inicio)
    fin_local = inicio_local + timedelta(minutes=duracion)

    return (
        inicio_local.date() == fin_local.date()
        and inicio_local.time() >= HORA_APERTURA_CLINICA
        and fin_local.time() <= HORA_CIERRE_CLINICA
    )
