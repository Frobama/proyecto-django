import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .bootstrap import inventory


class ExplicitCutoverTests(TransactionTestCase):
    old = [('proyectos', '0002_remove_archivo_seccion_archivo_global_and_more')]
    expand = [('proyectos', '0003_expand_contextual_roles')]
    final = [('proyectos', '0005_group_codes_share_requests')]

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def setUp(self):
        old_apps = self.migrate_to(self.old)
        User = old_apps.get_model('proyectos', 'Usuario')
        Group = old_apps.get_model('proyectos', 'Equipo')
        Project = old_apps.get_model('proyectos', 'Proyecto')
        self.user = User.objects.create(username='Historic', is_active=True)
        self.group = Group.objects.create(nombre='Histórico')
        self.project = Project.objects.create(nombre='Histórico')
        self.member = old_apps.get_model('proyectos', 'EquipoUsuario').objects.create(
            equipo=self.group, usuario=self.user, rol='analista-desconocido')
        self.grant = old_apps.get_model('proyectos', 'ProyectoEquipo').objects.create(
            proyecto=self.project, equipo=self.group, objetivo='Equipo principal del proyecto')
        self.file = old_apps.get_model('proyectos', 'Archivo').objects.create(
            nombre_original='histórico', archivo='legacy/shared.txt', usuario=self.user, proyecto=self.project)
        self.apps = self.migrate_to(self.expand)
        self.mapping = {'memberships': {str(self.member.pk): 'admin'},
                        'associations': {str(self.grant.pk): 'admin'},
                        'files': {str(self.file.pk): [self.group.pk]}}

    def tearDown(self):
        # Remove only this isolated historical fixture before restoring final schema.
        self.apps.get_model('proyectos', 'Archivo').objects.all().delete()
        self.apps.get_model('proyectos', 'Proyecto').objects.all().delete()
        self.apps.get_model('proyectos', 'Equipo').objects.all().delete()
        self.apps.get_model('proyectos', 'Usuario').objects.all().delete()
        self.migrate_to(self.final)
        super().tearDown()

    def command(self, mapping=None, apply=False, raw_json=None):
        output = io.StringIO()
        kwargs = {'stdout': output}
        if mapping is not None or raw_json is not None:
            kwargs.update(mapping='approved-test-mapping.json', apply=apply, dry_run=not apply)
            text = raw_json if raw_json is not None else json.dumps(mapping)
            with patch('builtins.open', return_value=io.StringIO(text)):
                call_command('bootstrap_contextual_roles', **kwargs)
        else:
            call_command('bootstrap_contextual_roles', **kwargs)
        return output.getvalue()

    def test_duplicate_top_level_json_keys_reject_before_dry_run_or_apply(self):
        before = inventory(self.apps)
        for section in self.mapping:
            raw = '{' + json.dumps(section) + ': {}, ' + json.dumps(self.mapping)[1:]
            for apply in (False, True):
                with self.subTest(section=section, apply=apply):
                    with patch('proyectos.management.commands.bootstrap_contextual_roles.locked_domain') as lock_scope:
                        with self.assertRaisesMessage(CommandError, f'Clave JSON duplicada: {section}'):
                            self.command(raw_json=raw, apply=apply)
                        lock_scope.assert_not_called()
                    self.assertEqual(inventory(self.apps), before)

    def test_duplicate_nested_json_ids_reject_conflicting_roles_and_audiences(self):
        before = inventory(self.apps)
        for section, conflicting_value in (('memberships', 'reader'), ('associations', 'reader'), ('files', [])):
            key = next(iter(self.mapping[section]))
            duplicate = ('{' + json.dumps(key) + ': ' + json.dumps(conflicting_value) + ', '
                         + json.dumps(key) + ': ' + json.dumps(self.mapping[section][key]) + '}')
            raw = '{' + ', '.join(
                json.dumps(name) + ': ' + (duplicate if name == section else json.dumps(values))
                for name, values in self.mapping.items()
            ) + '}'
            for apply in (False, True):
                with self.subTest(section=section, apply=apply):
                    with patch('proyectos.management.commands.bootstrap_contextual_roles.locked_domain') as lock_scope:
                        with self.assertRaisesMessage(CommandError, f'Clave JSON duplicada: {key}'):
                            self.command(raw_json=raw, apply=apply)
                        lock_scope.assert_not_called()
                    self.assertEqual(inventory(self.apps), before)

    def test_expand_preserves_original_role_no_inference_and_final_aborts(self):
        data = inventory(self.apps)
        self.assertEqual(data['memberships'][0]['rol'], 'analista-desconocido')
        self.assertEqual(data['memberships'][0]['legacy_rol'], 'analista-desconocido')
        self.assertIsNone(data['associations'][0]['rol'])
        self.assertEqual(data['files'][0]['equipos_visibles'], [])
        self.assertIn('Historic', self.command())
        with self.assertRaisesMessage(RuntimeError, 'Cutover incompleto'):
            self.migrate_to(self.final)
        self.assertEqual(inventory(self.apps), data)

    def test_dry_run_no_writes_then_approved_apply_and_constraints(self):
        before = inventory(self.apps)
        self.assertIn('ninguna escritura', self.command(self.mapping))
        self.assertEqual(inventory(self.apps), before)
        self.assertIn('Aplicado atómicamente', self.command(self.mapping, apply=True))
        final_apps = self.migrate_to(self.final)
        member = final_apps.get_model('proyectos', 'EquipoUsuario').objects.get(pk=self.member.pk)
        self.assertEqual(member.rol, 'admin')
        self.assertEqual(member.legacy_rol, 'analista-desconocido')
        file = final_apps.get_model('proyectos', 'Archivo').objects.get(pk=self.file.pk)
        self.assertEqual(list(file.equipos_visibles.values_list('pk', flat=True)), [self.group.pk])
        self.assertEqual(file.archivo.name, 'legacy/shared.txt')
        with self.assertRaisesMessage(CommandError, 'finalizado'):
            self.command(self.mapping, apply=True)

    def test_incomplete_mapping_and_invalid_policy_abort_before_any_writes(self):
        before = inventory(self.apps)
        bad_maps = [
            {**self.mapping, 'files': {}},
            {**self.mapping, 'memberships': {str(self.member.pk): 'reader'}},
            {**self.mapping, 'associations': {str(self.grant.pk): 'reader'}},
            {**self.mapping, 'files': {str(self.file.pk): []}},
            {**self.mapping, 'files': {str(self.file.pk): [999999]}},
        ]
        for mapping in bad_maps:
            with self.subTest(mapping=mapping), self.assertRaises(CommandError):
                self.command(mapping, apply=True)
            self.assertEqual(inventory(self.apps), before)

    def test_apply_failure_rolls_back_entire_mapping_preserving_legacy(self):
        before = inventory(self.apps)
        with patch('proyectos.management.commands.bootstrap_contextual_roles.Archivo.objects.get',
                   side_effect=RuntimeError('controlled rollback')):
            with self.assertRaises(RuntimeError):
                self.command(self.mapping, apply=True)
        self.assertEqual(inventory(self.apps), before)
