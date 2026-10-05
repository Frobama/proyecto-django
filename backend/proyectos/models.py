from django.db import models
from django.contrib.auth.models import AbstractUser
from pathlib import PurePosixPath
from uuid import uuid4
# Create your models here.

class Usuario(AbstractUser):
    pass


class Role(models.TextChoices):
    ADMIN = 'admin', 'Administrador'
    EDITOR = 'editor', 'Editor'
    READER = 'reader', 'Lector'

class Equipo(models.Model):
    nombre = models.CharField(max_length=255)
    equipo_recurrente = models.BooleanField(default=False)
    codigo = models.UUIDField(default=uuid4, unique=True, editable=False)
    usuarios = models.ManyToManyField(Usuario, through='EquipoUsuario', related_name='equipos')

    def __str__(self):
        return self.nombre

class EquipoUsuario(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE)
    rol = models.CharField(max_length=100, choices=Role.choices, default=Role.READER)
    legacy_rol = models.CharField(max_length=100, blank=True, null=True, editable=False)

    class Meta:
        db_table = 'equipo_usuario'
        unique_together = ('usuario', 'equipo')
        constraints = [models.CheckConstraint(check=models.Q(rol__in=Role.values), name='membership_role_valid')]

class Proyecto(models.Model):
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, null=True)
    equipos = models.ManyToManyField(Equipo, through='ProyectoEquipo', related_name='proyectos')

    def __str__(self):
        return self.nombre

class ProyectoEquipo(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE)
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE)
    objetivo = models.CharField(max_length=255, blank=True, null=True)
    descripcion = models.TextField(blank=True, null=True)
    rol = models.CharField(max_length=100, choices=Role.choices, default=Role.READER)

    class Meta:
        db_table = 'proyecto_equipo'
        unique_together = ('proyecto', 'equipo')
        constraints = [models.CheckConstraint(check=models.Q(rol__in=Role.values), name='project_team_role_valid')]


class RequestState(models.TextChoices):
    PENDING = 'pending', 'Pendiente'
    ACCEPTED = 'accepted', 'Aceptada'
    REJECTED = 'rejected', 'Rechazada'
    CANCELLED = 'cancelled', 'Cancelada'


class ShareRequest(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='solicitudes')
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE, related_name='solicitudes')
    solicitado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True)
    rol = models.CharField(max_length=100, choices=Role.choices, default=Role.READER)
    estado = models.CharField(max_length=20, choices=RequestState.choices, default=RequestState.PENDING)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['proyecto', 'equipo'], condition=models.Q(estado='pending'),
                                    name='unique_pending_share_request'),
            models.CheckConstraint(check=models.Q(rol__in=Role.values), name='share_request_role_valid'),
            models.CheckConstraint(check=models.Q(estado__in=RequestState.values), name='share_request_state_valid'),
        ]

def ruta_archivo(instance, filename):
    suffix = PurePosixPath(filename.replace('\\', '/')).suffix[:20]
    return f'proyectos/{instance.proyecto.id}/{uuid4().hex}{suffix}'


ruta_archivo_seccion = ruta_archivo

class Archivo(models.Model):
    nombre_original = models.CharField(max_length=255)
    archivo = models.FileField(upload_to=ruta_archivo)
    categoria = models.CharField(max_length=100, blank=True, null=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='archivos_subidos')
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='archivos')
    fecha_subido = models.DateTimeField(auto_now_add=True)
    locals()['global'] = models.BooleanField(default=False)
    equipos_visibles = models.ManyToManyField(Equipo, related_name='archivos_visibles', blank=True)

    def __str__(self):
        return self.nombre_original
