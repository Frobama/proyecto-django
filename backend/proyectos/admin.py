from django.contrib import admin

from .models import Usuario, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, Archivo, ShareRequest


class DomainReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (Usuario, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, Archivo, ShareRequest):
    admin.site.register(model, DomainReadOnlyAdmin)
