from rest_framework import permissions

class EsMiembroDelEquipo(permissions.BasePermission):
    message = 'No tienes permiso para acceder a los archivos de esta sección.'

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        return obj.seccion.proyecto.equipos.filter(usuarios=request.user).exists()