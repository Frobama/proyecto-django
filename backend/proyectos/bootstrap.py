ROLES = {'admin', 'editor', 'reader'}


def inventory(apps, using='default'):
    Membership = apps.get_model('proyectos', 'EquipoUsuario')
    Grant = apps.get_model('proyectos', 'ProyectoEquipo')
    File = apps.get_model('proyectos', 'Archivo')
    return {
        'groups': list(apps.get_model('proyectos', 'Equipo').objects.using(using).values_list('pk', flat=True)),
        'projects': list(apps.get_model('proyectos', 'Proyecto').objects.using(using).values_list('pk', flat=True)),
        'memberships': [dict(id=item.pk, equipo=item.equipo_id, usuario=item.usuario_id,
                             username=item.usuario.username, active=item.usuario.is_active,
                             rol=item.rol, legacy_rol=item.legacy_rol)
                        for item in Membership.objects.using(using).select_related('usuario').order_by('pk')],
        'associations': list(Grant.objects.using(using).order_by('pk').values('id', 'equipo_id', 'proyecto_id', 'rol')),
        'files': [dict(id=item.pk, proyecto=item.proyecto_id, usuario=item.usuario_id,
                      global_file=getattr(item, 'global'),
                      equipos_visibles=list(item.equipos_visibles.values_list('pk', flat=True)))
                  for item in File.objects.using(using).order_by('pk')],
    }


def validate_inventory(data, mapping):
    if not isinstance(mapping, dict) or set(mapping) != {'memberships', 'associations', 'files'}:
        raise ValueError('El mapa requiere memberships, associations y files completos')
    for section in ('memberships', 'associations', 'files'):
        if not isinstance(mapping[section], dict) or set(mapping[section]) != {str(item['id']) for item in data[section]}:
            raise ValueError(f'IDs incompletos o desconocidos en {section}')
    for section in ('memberships', 'associations'):
        if any(not isinstance(role, str) or role not in ROLES for role in mapping[section].values()):
            raise ValueError(f'Roles no canónicos en {section}')
    members = {}
    for item in data['memberships']:
        if item['active']:
            members.setdefault(item['equipo'], []).append(mapping['memberships'][str(item['id'])])
    for group in data['groups']:
        if 'admin' not in members.get(group, []):
            raise ValueError(f'Equipo {group} sin administrador activo explícito')
    linked = {}
    admins = set()
    reachable = {}
    for item in data['associations']:
        team, project = item['equipo_id'], item['proyecto_id']
        linked.setdefault(project, set()).add(team)
        role = mapping['associations'][str(item['id'])]
        if members.get(team):
            reachable.setdefault(project, set()).add(team)
        if role == 'admin' and 'admin' in members.get(team, []):
            admins.add(project)
    for project in data['projects']:
        if project not in admins:
            raise ValueError(f'Proyecto {project} sin equipo/administrador efectivo explícito')
    for item in data['files']:
        teams = mapping['files'][str(item['id'])]
        if not isinstance(teams, list) or any(type(team) is not int for team in teams) or len(set(teams)) != len(teams):
            raise ValueError(f'Audiencia inválida del archivo {item["id"]}')
        if not set(teams).issubset(linked.get(item['proyecto'], set())):
            raise ValueError(f'Audiencia no asociada del archivo {item["id"]}')
        if not teams or (not item['global_file'] and not set(teams).intersection(reachable.get(item['proyecto'], set()))):
            raise ValueError(f'Archivo {item["id"]} sin audiencia capturada accesible')
