from django.contrib import admin
from .models import Usuario, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, Archivo
# Register your models here.

admin.site.register(Usuario)
admin.site.register(Equipo)
admin.site.register(EquipoUsuario)
admin.site.register(Proyecto)
admin.site.register(ProyectoEquipo)
admin.site.register(Archivo)