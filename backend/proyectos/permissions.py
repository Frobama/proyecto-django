from rest_framework import permissions

from .models import EquipoUsuario, ProyectoEquipo, Role


ROLE_LEVEL = {Role.READER: 1, Role.EDITOR: 2, Role.ADMIN: 3}


def active_user(user):
    return bool(user and user.is_authenticated and user.is_active)


def group_role(user, group):
    if not active_user(user):
        return None
    role = EquipoUsuario.objects.filter(equipo=group, usuario=user).values_list('rol', flat=True).first()
    return role if role in ROLE_LEVEL else None


def project_role(user, project, audience=None):
    if not active_user(user):
        return None
    memberships = dict(EquipoUsuario.objects.filter(usuario=user).values_list('equipo_id', 'rol'))
    grants = ProyectoEquipo.objects.filter(proyecto=project)
    if audience is not None:
        grants = grants.filter(equipo_id__in=audience)
    level = max((min(ROLE_LEVEL.get(memberships.get(team), 0), ROLE_LEVEL.get(role, 0))
                 for team, role in grants.values_list('equipo_id', 'rol')), default=0)
    return next((role for role, value in ROLE_LEVEL.items() if value == level), None)


def file_role(user, file):
    audience = None if getattr(file, 'global') else file.equipos_visibles.values_list('pk', flat=True)
    return project_role(user, file.proyecto, audience)


def usuario_tiene_acceso_al_archivo(usuario, archivo):
    return file_role(usuario, archivo) is not None


def file_permissions(user, file):
    visible = usuario_tiene_acceso_al_archivo(user, file)
    role = project_role(user, file.proyecto) if visible else None
    write = role == Role.ADMIN or (role == Role.EDITOR and file.usuario_id == getattr(user, 'pk', None))
    return {'edit': write, 'delete': write, 'download': visible}


class EsMiembroDelEquipo(permissions.BasePermission):
    message = 'No tienes permiso para acceder a los archivos de este proyecto.'

    def has_object_permission(self, request, view, obj):
        return usuario_tiene_acceso_al_archivo(request.user, obj)
