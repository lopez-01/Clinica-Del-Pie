from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from CasaDrScholl_app.models import Personal, Administrativo, Operativo

class Command(BaseCommand):
    help = 'Crea usuarios automáticamente y los vincula con Personal'

    def handle(self, *args, **kwargs):
        all_personales = Personal.objects.all().order_by('id_personal')

        for personal in all_personales:
            username = f"personal{personal.id_personal}"

            user, created = User.objects.get_or_create(username=username)

            if created:
                user.set_password("1234")
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Usuario creado: {username}'))
            else:
                self.stdout.write(f'Usuario ya existe: {username}')

            personal.user = user
            personal.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f'Personal {personal.id_personal} vinculado a {username}'
                )
            )

            if Administrativo.objects.filter(personal=personal).exists():
                self.stdout.write(self.style.SUCCESS(f'{username} es ADMINISTRATIVO'))
            elif Operativo.objects.filter(personal=personal).exists():
                self.stdout.write(self.style.SUCCESS(f'{username} es OPERATIVO'))
            else:
                self.stdout.write(self.style.WARNING(f'{username} sin rol asignado'))
