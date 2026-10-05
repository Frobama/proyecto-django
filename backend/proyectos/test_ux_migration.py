from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class GroupCodeMigrationTests(TransactionTestCase):
    old = [('proyectos', '0004_enforce_contextual_roles')]
    final = [('proyectos', '0005_group_codes_share_requests')]

    def migrate_to(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def setUp(self):
        self.apps = self.migrate_to(self.old)

    def tearDown(self):
        self.apps.get_model('proyectos', 'Proyecto').objects.all().delete()
        self.apps.get_model('proyectos', 'Equipo').objects.all().delete()
        self.apps.get_model('proyectos', 'Usuario').objects.all().delete()
        self.migrate_to(self.final)
        super().tearDown()

    def test_existing_groups_backfilled_per_row_preserving_roles_and_flags(self):
        Group = self.apps.get_model('proyectos', 'Equipo')
        User = self.apps.get_model('proyectos', 'Usuario')
        user = User.objects.create(username='Historic')
        groups = [Group.objects.create(nombre='Uno'), Group.objects.create(nombre='Dos', equipo_recurrente=True)]
        Membership = self.apps.get_model('proyectos', 'EquipoUsuario')
        members = [Membership.objects.create(equipo=group, usuario=user, rol='admin', legacy_rol='owner')
                   for group in groups]
        final_apps = self.migrate_to(self.final)
        Group = final_apps.get_model('proyectos', 'Equipo')
        existing = list(Group.objects.order_by('pk'))
        created = Group.objects.create(nombre='Tres')
        self.assertEqual(len({group.codigo for group in existing + [created]}), 3)
        self.assertTrue(all(group.codigo for group in existing))
        self.assertEqual([group.equipo_recurrente for group in existing], [False, True])
        for member in final_apps.get_model('proyectos', 'EquipoUsuario').objects.filter(pk__in=[m.pk for m in members]):
            self.assertEqual((member.rol, member.legacy_rol), ('admin', 'owner'))

    def test_legacy_nonrecurrent_multiple_projects_aborts_without_repair(self):
        Group = self.apps.get_model('proyectos', 'Equipo')
        group = Group.objects.create(nombre='Legacy')
        Project = self.apps.get_model('proyectos', 'Proyecto')
        Grant = self.apps.get_model('proyectos', 'ProyectoEquipo')
        for name in ('Uno', 'Dos'):
            Grant.objects.create(proyecto=Project.objects.create(nombre=name), equipo=group, rol='admin')
        with self.assertRaisesMessage(RuntimeError, 'Equipos no recurrentes con varios proyectos'):
            self.migrate_to(self.final)
        group.refresh_from_db()
        self.assertFalse(group.equipo_recurrente)
        self.assertEqual(group.proyectos.count(), 2)
        self.assertEqual(list(Grant.objects.values_list('rol', flat=True)), ['admin', 'admin'])
