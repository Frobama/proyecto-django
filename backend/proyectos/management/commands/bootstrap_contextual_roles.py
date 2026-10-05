import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

from proyectos.bootstrap import inventory, validate_inventory
from proyectos.models import Archivo, EquipoUsuario, ProyectoEquipo
from proyectos.services import locked_domain


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Clave JSON duplicada: {key}.')
        result[key] = value
    return result


class Command(BaseCommand):
    help = 'Inventario y validación explícita del cutover de roles. Sin --apply no escribe.'

    def add_arguments(self, parser):
        parser.add_argument('--mapping')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        applied = MigrationRecorder(connection).applied_migrations()
        if ('proyectos', '0003_expand_contextual_roles') not in applied:
            raise CommandError('Aplica solamente proyectos 0003 antes de inventariar.')
        if options['apply'] and options['dry_run']:
            raise CommandError('--apply y --dry-run son incompatibles.')
        if options['apply'] and not options['mapping']:
            raise CommandError('--apply requiere un mapa aprobado.')
        if options['apply'] and ('proyectos', '0004_enforce_contextual_roles') in applied:
            raise CommandError('El cutover ya fue finalizado; usa los servicios de dominio para cambios posteriores.')
        try:
            mapping = None
            if options['mapping']:
                with open(options['mapping'], encoding='utf-8') as source:
                    mapping = json.load(source, object_pairs_hook=unique_json_object)
            with locked_domain():
                data = inventory(apps)
                if mapping is None:
                    self.stdout.write(json.dumps(data, indent=2))
                    return
                validate_inventory(data, mapping)
                if options['apply']:
                    for item in data['memberships']:
                        EquipoUsuario.objects.filter(pk=item['id']).update(rol=mapping['memberships'][str(item['id'])])
                    for item in data['associations']:
                        ProyectoEquipo.objects.filter(pk=item['id']).update(rol=mapping['associations'][str(item['id'])])
                    for item in data['files']:
                        Archivo.objects.get(pk=item['id']).equipos_visibles.set(mapping['files'][str(item['id'])])
                self.stdout.write('Mapa completo y válido. ' + ('Aplicado atómicamente.' if options['apply'] else 'Dry-run: ninguna escritura.'))
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
