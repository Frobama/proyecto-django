from contextlib import contextmanager

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, RequestState, Role, ShareRequest, Usuario
from .permissions import active_user, file_permissions, group_role, project_role
from .storage import cleanup_after_commit, delete_unreferenced


def max_file_size():
    return getattr(settings, 'PROYECTOS_MAX_FILE_SIZE', 20 * 1024 * 1024)


@contextmanager
def locked_domain(durable=False):
    # A coarse parent lock keeps the MVP's membership/grant/audience checks serializable.
    # Every supported writer takes groups first; projects are discovered only afterwards.
    with transaction.atomic(durable=durable):
        list(Equipo.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
        list(Proyecto.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
        yield


def require_admin(role):
    if role != Role.ADMIN:
        raise PermissionDenied('Se requiere el rol de administrador.')


def get_resource(model, pk, lock=False):
    query = model.objects.select_for_update() if lock else model.objects
    try:
        return query.get(pk=pk)
    except model.DoesNotExist as error:
        raise NotFound('Recurso no encontrado.') from error


def require_group_member(actor, group):
    if group_role(actor, group) is None:
        raise PermissionDenied('No tienes permiso para acceder a este equipo.')


def check_group(group):
    if not EquipoUsuario.objects.filter(equipo=group, usuario__is_active=True, rol=Role.ADMIN).exists():
        raise ValidationError('El equipo debe conservar al menos un administrador activo.')


def reachable_teams(project, excluded_group=None):
    return set(ProyectoEquipo.objects.filter(
        proyecto=project, rol__in=Role.values,
        equipo__equipousuario__usuario__is_active=True,
        equipo__equipousuario__rol__in=Role.values,
    ).exclude(equipo_id=excluded_group).values_list('equipo_id', flat=True))


def project_integrity_issues(project, excluded_group=None):
    issues = []
    grants = ProyectoEquipo.objects.filter(proyecto=project).exclude(equipo_id=excluded_group)
    if not grants.exists():
        issues.append('El proyecto debe conservar al menos un equipo.')
    if not grants.filter(
        rol=Role.ADMIN,
        equipo__equipousuario__rol=Role.ADMIN,
        equipo__equipousuario__usuario__is_active=True,
    ).exists():
        issues.append('El proyecto debe conservar al menos un administrador efectivo activo.')
    reachable = reachable_teams(project, excluded_group)
    for file in project.archivos.filter(**{'global': False}).prefetch_related('equipos_visibles'):
        if not reachable.intersection(team.pk for team in file.equipos_visibles.all()):
            issues.append('La operación dejaría un archivo privado sin audiencia accesible.')
            break
    return issues


def check_project(project, excluded_group=None):
    issues = project_integrity_issues(project, excluded_group)
    if issues:
        raise ValidationError(issues)


def check_capacity(group):
    if not group.equipo_recurrente and group.proyectos.exists():
        raise ValidationError('Un equipo no recurrente puede estar asociado como máximo a un proyecto.')


def group_deletion_preview(actor, group_id):
    group = get_resource(Equipo, group_id)
    require_admin(group_role(actor, group))
    projects = list(group.proyectos.order_by('pk'))
    issues = list(dict.fromkeys(issue for project in projects
                               for issue in project_integrity_issues(project, group.pk)))
    return {'proyectos': [{'id': project.pk, 'nombre': project.nombre} for project in projects],
            'permitido': not issues, 'motivos': issues}


def create_group(actor, data):
    if not active_user(actor):
        raise PermissionDenied('Se requiere un usuario activo.')
    with locked_domain():
        group = Equipo.objects.create(**data)
        EquipoUsuario.objects.create(equipo=group, usuario=actor, rol=Role.ADMIN)
        return group


def create_project(actor, data):
    if not active_user(actor):
        raise PermissionDenied('Se requiere un usuario activo.')
    data = data.copy()
    selected = data.pop('equipo', None)
    cap = data.pop('equipo_rol', Role.ADMIN)
    with locked_domain():
        if selected is not None:
            selected = get_resource(Equipo, selected.pk)
            require_admin(group_role(actor, selected))
            if cap != Role.ADMIN:
                raise ValidationError({'equipo_rol': 'El equipo principal requiere una concesión admin.'})
            check_capacity(selected)
        project = Proyecto.objects.create(**data)
        group = selected
        if group is None:
            group = Equipo.objects.create(nombre=f'{project.nombre} - Equipo principal'[:255])
            EquipoUsuario.objects.create(equipo=group, usuario=actor, rol=Role.ADMIN)
        ProyectoEquipo.objects.create(proyecto=project, equipo=group, rol=Role.ADMIN,
                                      objetivo='Equipo principal del proyecto')
        return project


def update_group(actor, group_id, data, delete=False):
    with locked_domain():
        group = get_resource(Equipo, group_id)
        require_admin(group_role(actor, group))
        if delete:
            projects = list(group.proyectos.order_by('pk'))
            for project in projects:
                check_project(project, excluded_group=group.pk)
            group.delete()
        else:
            if data.get('equipo_recurrente') is False and group.proyectos.count() > 1:
                raise ValidationError('Un equipo con varios proyectos debe conservar su estado recurrente.')
            for field, value in data.items():
                setattr(group, field, value)
            group.save()
        return group


def update_project(actor, project_id, data, delete=False):
    with locked_domain():
        project = get_resource(Proyecto, project_id)
        require_admin(project_role(actor, project))
        if delete:
            for file in project.archivos.select_for_update().order_by('pk'):
                if not file_permissions(actor, file)['delete']:
                    raise PermissionDenied('No tienes permiso para eliminar todos los archivos de este proyecto.')
            project.delete()
        else:
            for field, value in data.items():
                setattr(project, field, value)
            project.save()
        return project


def add_member(actor, group_id, data):
    with locked_domain():
        group = get_resource(Equipo, group_id)
        require_admin(group_role(actor, group))
        # Resolve identity only after authorizing the actor, including legacy numeric IDs.
        query = {'username__exact': data['username']} if 'username' in data else {'pk': data['usuario']}
        target = Usuario.objects.filter(is_active=True, **query).first()
        if target is None or ('username' in data and target.username != data['username']):
            raise ValidationError('No existe un usuario activo con esa identidad exacta.')
        if EquipoUsuario.objects.filter(equipo=group, usuario=target).exists():
            raise ValidationError('Ese usuario ya pertenece al equipo.')
        return EquipoUsuario.objects.create(equipo=group, usuario=target, rol=data.get('rol', Role.READER))


def exact_user_candidate(actor, group_id, username):
    group = get_resource(Equipo, group_id)
    require_admin(group_role(actor, group))
    target = Usuario.objects.filter(is_active=True, username__exact=username).first()
    if target is None or target.username != username:
        raise NotFound('Usuario no encontrado.')
    return {'usuario': target.pk, 'username': target.username,
            'ya_es_miembro': EquipoUsuario.objects.filter(equipo=group, usuario=target).exists()}


def change_member(actor, group_id, user_id, role=None, delete=False):
    with locked_domain():
        group = get_resource(Equipo, group_id)
        require_group_member(actor, group)
        if not delete or actor.pk != int(user_id):
            require_admin(group_role(actor, group))
        member = EquipoUsuario.objects.filter(equipo=group, usuario_id=user_id).select_related('usuario').first()
        if member is None:
            raise NotFound('Ese usuario no pertenece al equipo.')
        if delete:
            member.delete()
        else:
            member.rol = role
            member.save(update_fields=['rol'])
        check_group(group)
        for project in group.proyectos.all():
            check_project(project)
        return member


def add_team(actor, project_id, data):
    data = data.copy()
    with locked_domain():
        project = get_resource(Proyecto, project_id)
        require_admin(project_role(actor, project))
        data['equipo'] = get_resource(Equipo, data['equipo'].pk)
        require_admin(group_role(actor, data['equipo']))
        if ProyectoEquipo.objects.filter(proyecto=project, equipo=data['equipo']).exists():
            raise ValidationError('Ese equipo ya está asociado a este proyecto.')
        check_capacity(data['equipo'])
        return ProyectoEquipo.objects.create(proyecto=project, **data)


def change_team(actor, project_id, team_id, role=None, delete=False):
    with locked_domain():
        project = get_resource(Proyecto, project_id)
        require_admin(project_role(actor, project))
        grant = ProyectoEquipo.objects.filter(proyecto=project, equipo_id=team_id).first()
        if grant is None:
            raise NotFound('Ese equipo no está asociado al proyecto.')
        if delete:
            grant.delete()
        else:
            grant.rol = role
            grant.save(update_fields=['rol'])
        check_project(project)
        return grant


def share_request_permissions(actor, request):
    pending = request.estado == RequestState.PENDING
    receiver = pending and group_role(actor, request.equipo) == Role.ADMIN
    sender_active = project_role(request.solicitado_por, request.proyecto) == Role.ADMIN
    available = not ProyectoEquipo.objects.filter(proyecto=request.proyecto, equipo=request.equipo).exists()
    capacity = request.equipo.equipo_recurrente or not request.equipo.proyectos.exists()
    return {'accept': bool(receiver and sender_active and available and capacity),
            'reject': bool(receiver),
            'cancel': bool(pending and project_role(actor, request.proyecto) == Role.ADMIN)}


def create_share_request(actor, project_id, data):
    with locked_domain():
        project = get_resource(Proyecto, project_id)
        require_admin(project_role(actor, project))
        group = Equipo.objects.filter(codigo=data['codigo']).first()
        if group is None:
            raise ValidationError('No se puede solicitar compartir con ese código.')
        if ProyectoEquipo.objects.filter(proyecto=project, equipo=group).exists():
            raise ValidationError('Ese equipo ya está asociado a este proyecto.')
        if ShareRequest.objects.filter(proyecto=project, equipo=group, estado=RequestState.PENDING).exists():
            raise ValidationError('Ya existe una solicitud pendiente para ese equipo.')
        return ShareRequest.objects.create(proyecto=project, equipo=group, solicitado_por=actor,
                                           rol=data.get('rol', Role.READER))


def pending_share_request(**scope):
    request = ShareRequest.objects.select_for_update().filter(**scope).first()
    if request is None:
        raise NotFound('Solicitud no encontrada.')
    if request.estado != RequestState.PENDING:
        raise ValidationError('La solicitud ya no está pendiente.')
    return request


def cancel_share_request(actor, project_id, request_id):
    with locked_domain():
        project = get_resource(Proyecto, project_id)
        require_admin(project_role(actor, project))
        request = pending_share_request(pk=request_id, proyecto=project)
        request.estado = RequestState.CANCELLED
        request.save(update_fields=['estado'])
        return request


def answer_share_request(actor, group_id, request_id, action):
    with locked_domain():
        group = get_resource(Equipo, group_id)
        require_admin(group_role(actor, group))
        request = pending_share_request(pk=request_id, equipo=group)
        if action == 'accept':
            require_admin(project_role(request.solicitado_por, request.proyecto))
            if ProyectoEquipo.objects.filter(proyecto=request.proyecto, equipo=group).exists():
                raise ValidationError('Ese equipo ya está asociado a este proyecto.')
            check_capacity(group)
            ProyectoEquipo.objects.create(proyecto=request.proyecto, equipo=group, rol=request.rol)
            request.estado = RequestState.ACCEPTED
        elif action == 'reject':
            request.estado = RequestState.REJECTED
        else:
            raise ValidationError('Acción inválida.')
        request.save(update_fields=['estado'])
        return request


def save_file(actor, data, file_id=None):
    data = data.copy()
    new_key = None
    storage = Archivo._meta.get_field('archivo').storage
    try:
        with locked_domain(durable=True):
            if file_id is None:
                project = data['proyecto']
                if project_role(actor, project) not in (Role.EDITOR, Role.ADMIN):
                    raise PermissionDenied('No tienes permiso para subir archivos a este proyecto.')
                file = Archivo(usuario=actor, **data)
                audience = set(EquipoUsuario.objects.filter(
                    usuario=actor, equipo__proyectos=project, rol__in=Role.values,
                ).values_list('equipo_id', flat=True))
            else:
                file = get_resource(Archivo, file_id, lock=True)
                if not file_permissions(actor, file)['edit']:
                    raise PermissionDenied('No tienes permiso para modificar este archivo.')
                if 'proyecto' in data and data['proyecto'].pk != file.proyecto_id:
                    raise ValidationError({'proyecto': 'No se puede cambiar el proyecto de un archivo.'})
                audience = set(file.equipos_visibles.values_list('pk', flat=True))
                for field, value in data.items():
                    setattr(file, field, value)
            if not getattr(file, 'global'):
                if not audience.intersection(reachable_teams(file.proyecto)):
                    raise ValidationError('El archivo privado debe tener una audiencia accesible.')
                if project_role(actor, file.proyecto, audience) is None:
                    raise ValidationError('No tienes acceso a la audiencia privada capturada.')
            old_key = Archivo.objects.filter(pk=file_id).values_list('archivo', flat=True).first() if file_id else None
            if 'archivo' in data:
                upload = data['archivo']
                if upload.size > max_file_size():
                    raise ValidationError({'archivo': f'El tamaño máximo es {max_file_size()} bytes.'})
                file.archivo.save(upload.name, upload, save=False)
                new_key = file.archivo.name
            file.save()
            if file_id is None:
                file.equipos_visibles.set(audience)
            if old_key and old_key != file.archivo.name:
                cleanup_after_commit(storage, old_key)
        return file
    except Exception:
        if new_key:
            delete_unreferenced(storage, new_key)
        raise


def delete_file(actor, file_id):
    with locked_domain():
        file = get_resource(Archivo, file_id, lock=True)
        if not file_permissions(actor, file)['delete']:
            raise PermissionDenied('No tienes permiso para eliminar este archivo.')
        file.delete()
