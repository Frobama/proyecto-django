from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from rest_framework.exceptions import APIException

from . import services
from .models import Archivo, Equipo, EquipoUsuario, ProyectoEquipo, RequestState, Role, ShareRequest, Usuario
from .test_roles import DomainFixture


@skipUnlessDBFeature('has_select_for_update')
class PostgreSQLInvariantTests(DomainFixture, TransactionTestCase):
    def race(self, *operations):
        barrier = Barrier(len(operations))

        def execute(operation):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                barrier.wait(timeout=5)
                operation()
                return 'ok'
            except APIException as error:
                return error.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = [executor.submit(execute, operation) for operation in operations]
            return [future.result(timeout=20) for future in futures]

    def test_simultaneous_self_demotion_keeps_last_group_admin(self):
        self.editor_member.rol = Role.ADMIN
        self.editor_member.save()
        results = self.race(
            lambda: services.change_member(self.owner, self.group.pk, self.owner.pk, Role.READER),
            lambda: services.change_member(self.editor, self.group.pk, self.editor.pk, Role.READER),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(EquipoUsuario.objects.filter(equipo=self.group, rol=Role.ADMIN).count(), 1)
        services.check_project(self.project)

    def test_simultaneous_self_exit_keeps_last_group_admin(self):
        self.editor_member.rol = Role.ADMIN
        self.editor_member.save()
        results = self.race(
            lambda: services.change_member(self.owner, self.group.pk, self.owner.pk, delete=True),
            lambda: services.change_member(self.editor, self.group.pk, self.editor.pk, delete=True),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(EquipoUsuario.objects.filter(equipo=self.group, rol=Role.ADMIN).count(), 1)

    def test_simultaneous_grant_demotion_keeps_effective_project_admin(self):
        grant = ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.other)
        grant.rol = Role.ADMIN
        grant.save()
        results = self.race(
            lambda: services.change_team(self.owner, self.project.pk, self.group.pk, Role.READER),
            lambda: services.change_team(self.external, self.project.pk, self.other.pk, Role.READER),
        )
        self.assertCountEqual(results, ['ok', 400])
        services.check_project(self.project)
        self.assertEqual(ProyectoEquipo.objects.filter(proyecto=self.project, rol=Role.ADMIN).count(), 1)

    def test_concurrent_detach_keeps_last_link(self):
        EquipoUsuario.objects.create(equipo=self.other, usuario=self.owner, rol=Role.ADMIN)
        grant = ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.other)
        grant.rol = Role.ADMIN
        grant.save()
        results = self.race(
            lambda: services.change_team(self.owner, self.project.pk, self.group.pk, delete=True),
            lambda: services.change_team(self.owner, self.project.pk, self.other.pk, delete=True),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(self.project.equipos.count(), 1)
        services.check_project(self.project)

    def test_concurrent_audience_detach_keeps_private_file_reachable(self):
        third = services.create_group(self.owner, {'nombre': 'Control'})
        services.add_team(self.owner, self.project.pk, {'equipo': third, 'rol': Role.ADMIN})
        file = self.upload()
        file.equipos_visibles.add(self.other)
        results = self.race(
            lambda: services.change_team(self.owner, self.project.pk, self.group.pk, delete=True),
            lambda: services.change_team(self.owner, self.project.pk, self.other.pk, delete=True),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(self.project.equipos.count(), 2)
        services.check_project(self.project)

    def test_upload_against_detach_does_not_strand_private_file(self):
        control = services.create_group(self.owner, {'nombre': 'Control'})
        services.add_team(self.owner, self.project.pk, {'equipo': control, 'rol': Role.ADMIN})
        results = self.race(
            lambda: self.upload(),
            lambda: services.change_team(self.owner, self.project.pk, self.group.pk, delete=True),
        )
        self.assertIn('ok', results)
        self.assertEqual(len([result for result in results if result == 'ok']), 1)
        self.assertTrue(any(result in (400, 403) for result in results))
        services.check_project(self.project)
        if Archivo.objects.exists():
            self.assertTrue(self.project.equipos.filter(pk=self.group.pk).exists())

    def test_duplicate_membership_is_serialized(self):
        target = Usuario.objects.create_user(username='Concurrent')
        results = self.race(
            lambda: services.add_member(self.owner, self.group.pk, {'username': target.username}),
            lambda: services.add_member(self.owner, self.group.pk, {'username': target.username}),
        )
        self.assertCountEqual(results, ['ok', 400])

    def test_duplicate_association_is_serialized(self):
        group = services.create_group(self.owner, {'nombre': 'Nuevo'})
        results = self.race(
            lambda: services.add_team(self.owner, self.project.pk, {'equipo': group, 'rol': Role.READER}),
            lambda: services.add_team(self.owner, self.project.pk, {'equipo': group, 'rol': Role.READER}),
        )
        self.assertCountEqual(results, ['ok', 400])

    def test_concurrent_file_replacements_keep_final_bytes_and_clean_old_keys(self):
        file = self.upload()
        original, storage = file.archivo.name, file.archivo.storage
        keys = []

        def replace(content):
            result = services.save_file(self.editor, {'archivo': SimpleUploadedFile('same.txt', content)}, file.pk)
            keys.append(result.archivo.name)

        self.assertCountEqual(self.race(lambda: replace(b'one'), lambda: replace(b'two')), ['ok', 'ok'])
        final = Archivo.objects.get(pk=file.pk)
        self.assertFalse(storage.exists(original))
        self.assertEqual(sum(storage.exists(key) for key in keys), 1)
        self.assertTrue(storage.exists(final.archivo.name))

    def test_concurrent_file_delete_and_replacement_no_stale_resource_error(self):
        file = self.upload()
        original, storage = file.archivo.name, file.archivo.storage
        results = self.race(
            lambda: services.delete_file(self.owner, file.pk),
            lambda: services.save_file(self.editor, {'archivo': SimpleUploadedFile('x', b'replacement')}, file.pk),
        )
        self.assertIn('ok', results)
        self.assertTrue(all(result in ('ok', 404) for result in results))
        self.assertFalse(Archivo.objects.filter(pk=file.pk).exists())
        self.assertFalse(storage.exists(original))

    def test_nonrecurrent_concurrent_selected_creates_one_principal(self):
        group = services.create_group(self.owner, {'nombre': 'Único'})
        before = Equipo.objects.count()
        results = self.race(
            lambda: services.create_project(self.owner, {'nombre': 'Uno', 'equipo': group}),
            lambda: services.create_project(self.owner, {'nombre': 'Dos', 'equipo': group}),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(group.proyectos.count(), 1)
        self.assertEqual(before, Equipo.objects.count())
        self.assertEqual(ProyectoEquipo.objects.get(equipo=group).rol, Role.ADMIN)

    def test_nonrecurrent_concurrent_associations_keep_one_project(self):
        group = services.create_group(self.owner, {'nombre': 'Único'})
        second = services.create_project(self.owner, {'nombre': 'Segundo'})
        results = self.race(
            lambda: services.add_team(self.owner, self.project.pk, {'equipo': group}),
            lambda: services.add_team(self.owner, second.pk, {'equipo': group}),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(group.proyectos.count(), 1)

    def test_duplicate_pending_request_serialized(self):
        receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        results = self.race(
            lambda: services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo}),
            lambda: services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo}),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(ShareRequest.objects.filter(equipo=receiver, estado=RequestState.PENDING).count(), 1)

    def test_accept_cancel_one_confirmed_outcome(self):
        receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        request = services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo})
        results = self.race(
            lambda: services.answer_share_request(self.external, receiver.pk, request.pk, 'accept'),
            lambda: services.cancel_share_request(self.owner, self.project.pk, request.pk),
        )
        self.assertCountEqual(results, ['ok', 400])
        request.refresh_from_db()
        self.assertIn(request.estado, (RequestState.ACCEPTED, RequestState.CANCELLED))
        self.assertEqual(ProyectoEquipo.objects.filter(proyecto=self.project, equipo=receiver).exists(),
                         request.estado == RequestState.ACCEPTED)

    def test_two_accepts_nonrecurrent_capacity(self):
        receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        second = services.create_project(self.owner, {'nombre': 'Segundo'})
        first_request = services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo})
        second_request = services.create_share_request(self.owner, second.pk, {'codigo': receiver.codigo})
        results = self.race(
            lambda: services.answer_share_request(self.external, receiver.pk, first_request.pk, 'accept'),
            lambda: services.answer_share_request(self.external, receiver.pk, second_request.pk, 'accept'),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(receiver.proyectos.count(), 1)
        self.assertEqual(ShareRequest.objects.filter(equipo=receiver, estado=RequestState.ACCEPTED).count(), 1)
        self.assertEqual(ShareRequest.objects.filter(equipo=receiver, estado=RequestState.PENDING).count(), 1)

    def test_accept_against_selected_creation_keeps_nonrecurrent_capacity(self):
        receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        request = services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo})
        results = self.race(
            lambda: services.answer_share_request(self.external, receiver.pk, request.pk, 'accept'),
            lambda: services.create_project(self.external, {'nombre': 'Principal', 'equipo': receiver}),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(receiver.proyectos.count(), 1)
        request.refresh_from_db()
        self.assertEqual(ProyectoEquipo.objects.filter(proyecto=self.project, equipo=receiver).exists(),
                         request.estado == RequestState.ACCEPTED)

    def test_accept_against_sender_membership_loss_rechecks_authority(self):
        receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        services.change_member(self.owner, self.group.pk, self.editor.pk, Role.ADMIN)
        request = services.create_share_request(self.owner, self.project.pk, {'codigo': receiver.codigo})
        results = self.race(
            lambda: services.answer_share_request(self.external, receiver.pk, request.pk, 'accept'),
            lambda: services.change_member(self.editor, self.group.pk, self.owner.pk, delete=True),
        )
        self.assertEqual(results[1], 'ok')
        self.assertIn(results[0], ('ok', 403))
        request.refresh_from_db()
        self.assertEqual(request.estado, RequestState.ACCEPTED if results[0] == 'ok' else RequestState.PENDING)
        self.assertEqual(ProyectoEquipo.objects.filter(proyecto=self.project, equipo=receiver).exists(),
                         request.estado == RequestState.ACCEPTED)
        services.check_project(self.project)

    def test_concurrent_group_withdrawals_keep_private_audience(self):
        control = services.create_group(self.owner, {'nombre': 'Control'})
        services.add_team(self.owner, self.project.pk, {'equipo': control, 'rol': Role.ADMIN})
        file = self.upload()
        file.equipos_visibles.add(self.other)
        results = self.race(
            lambda: services.update_group(self.owner, self.group.pk, {}, delete=True),
            lambda: services.update_group(self.external, self.other.pk, {}, delete=True),
        )
        self.assertCountEqual(results, ['ok', 400])
        self.assertEqual(self.project.equipos.count(), 2)
        self.assertTrue(Archivo.objects.filter(pk=file.pk).exists())
        services.check_project(self.project)
