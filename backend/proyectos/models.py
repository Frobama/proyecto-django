from django.db import models
from django.contrib.auth.models import AbstractUser
# Create your models here.

class Usuario(AbstractUser):
    pass

class Equipo(models.Model):
    nombre = models.CharField(max_length=255)
    usuarios = models.ManyToManyField(Usuario, through='EquipoUsuario', related_name='equipos')

    def __str__(self):
        return self.nombre

class EquipoUsuario(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    equipo = models.ForeignKey(Equipo, on_delete=models.CASCADE)
    rol = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = 'equipo_usuario'
        unique_together = ('usuario', 'equipo')

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

    class Meta:
        db_table = 'proyecto_equipo'
        unique_together = ('proyecto', 'equipo')

class Seccion(models.Model):
    proyecto = models.ForeignKey(Proyecto, on_delete=models.CASCADE, related_name='secciones')
    nombre = models.CharField(max_length=255)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} - {self.proyecto.nombre}"

def ruta_archivo_seccion(instance, filename):
    # Organiza físicamente los archivos: proyectos/<id_proyecto>/secciones/<id_seccion>/<nombre_archivo>
    return f'proyectos/{instance.seccion.proyecto.id}/secciones/{instance.seccion.id}/{filename}'

class Archivo(models.Model):
    nombre_original = models.CharField(max_length=255)
    archivo = models.FileField(upload_to=ruta_archivo_seccion)
    categoria = models.CharField(max_length=100, blank=True, null=True)
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='archivos_subidos')
    seccion = models.ForeignKey(Seccion, on_delete=models.CASCADE, related_name='archivos')
    fecha_subido = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre_original