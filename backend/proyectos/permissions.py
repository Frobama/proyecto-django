from rest_framework import permissions


def usuario_tiene_acceso_al_archivo(usuario, archivo):
    if not usuario or not usuario.is_authenticated:
        return False

    if not archivo or not getattr(archivo, 'proyecto', None):
        return False

    if getattr(archivo, 'global', False):
        return archivo.proyecto.equipos.filter(usuarios=usuario).exists()

    equipos_del_archivo = set(
        archivo.usuario.equipos.filter(proyectos=archivo.proyecto).values_list('id', flat=True)
    )
    equipos_del_usuario = set(
        archivo.proyecto.equipos.filter(usuarios=usuario).values_list('id', flat=True)
    )

    return bool(equipos_del_archivo & equipos_del_usuario)


class EsMiembroDelEquipo(permissions.BasePermission):
    message = 'No tienes permiso para acceder a los archivos de este proyecto.'

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        return usuario_tiene_acceso_al_archivo(request.user, obj)