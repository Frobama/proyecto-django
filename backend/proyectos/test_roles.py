import tempfile
from unittest.mock import patch

from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from . import services
from .models import Archivo, EquipoUsuario, ProyectoEquipo, Role, Usuario
from .permissions import file_permissions, group_role, project_role


class DomainFixture:
    def setUp(self):
        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.owner = Usuario.objects.create_user(username='Owner')
        self.editor = Usuario.objects.create_user(username='Editor')
        self.reader = Usuario.objects.create_user(username='Reader')
        self.external = Usuario.objects.create_user(username='External')
        self.project = services.create_project(self.owner, {'nombre': 'Proyecto'})
        self.group = self.project.equipos.get()
        self.editor_member = EquipoUsuario.objects.create(equipo=self.group, usuario=self.editor, rol=Role.EDITOR)
        self.reader_member = EquipoUsuario.objects.create(equipo=self.group, usuario=self.reader, rol=Role.READER)
        self.other = services.create_group(self.external, {'nombre': 'Externo'})
        ProyectoEquipo.objects.create(proyecto=self.project, equipo=self.other, rol=Role.READER)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def upload(self, actor=None, global_file=False):
        return services.save_file(actor or self.editor, {
            'nombre_original': 'documento.txt', 'archivo': SimpleUploadedFile('documento.txt', b'original'),
            'proyecto': self.project, 'global': global_file,
        })


class ContextualPolicyTests(DomainFixture, TestCase):
    def test_all_roles_permissions_and_actual_crud(self):
        for user, role in ((self.owner, 'admin'), (self.editor, 'editor'), (self.reader, 'reader'), (self.external, 'reader')):
            self.client.force_authenticate(user)
            project = self.client.get(f'/api/proyectos/{self.project.pk}/').data
            self.assertEqual(project['role'], role)
            self.assertEqual(project['permissions'], {'edit': role == 'admin', 'delete': role == 'admin',
                                                       'manage_teams': role == 'admin', 'upload_files': role != 'reader'})
            result = self.client.patch(f'/api/proyectos/{self.project.pk}/', {'descripcion': 'cambio'}, format='json')
            self.assertEqual(result.status_code, 200 if role == 'admin' else 403)
            result = self.client.put(f'/api/equipos/{self.group.pk}/', {'nombre': 'Grupo'}, format='json')
            self.assertEqual(result.status_code, 404 if user == self.external else (200 if role == 'admin' else 403))
            result = self.client.post('/api/archivos/', {'nombre_original': 'x', 'archivo': SimpleUploadedFile('x', b'x'),
                                                        'proyecto': self.project.pk}, format='multipart')
            self.assertEqual(result.status_code, 201 if role != 'reader' else 400)

    def test_overlapping_max_of_capped_roles_and_inactive_fail_closed(self):
        EquipoUsuario.objects.create(equipo=self.other, usuario=self.editor, rol=Role.ADMIN)
        self.assertEqual(project_role(self.editor, self.project), Role.EDITOR)
        self.assertEqual(group_role(self.editor, self.other), Role.ADMIN)
        grant = ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.other)
        grant.rol = Role.ADMIN
        grant.save()
        self.assertEqual(project_role(self.editor, self.project), Role.ADMIN)
        self.editor.is_active = False
        self.editor.save()
        self.assertIsNone(project_role(self.editor, self.project))
        self.assertIsNone(group_role(self.editor, self.other))
        grant.rol = 'unknown'
        with patch('proyectos.permissions.ProyectoEquipo.objects.filter') as query:
            query.return_value.values_list.return_value = [(self.other.pk, 'owner')]
            self.assertIsNone(project_role(self.external, self.project))

    def test_exact_username_add_duplicate_inactive_unknown_and_role_patch(self):
        target = Usuario.objects.create_user(username='ExactCase')
        url = f'/api/equipos/{self.group.pk}/miembros/'
        for body in ({'username': 'exactcase'}, {'username': ' ExactCase'}, {'username': 'missing'},
                     {'username': 'ExactCase', 'usuario': target.pk}, {'username': 'ExactCase', 'rol': 'owner'}):
            self.assertEqual(self.client.post(url, body, format='json').status_code, 400)
        target.is_active = False
        target.save()
        self.assertEqual(self.client.post(url, {'username': 'ExactCase'}, format='json').status_code, 400)
        target.is_active = True
        target.save()
        response = self.client.post(url, {'username': 'ExactCase'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['rol'], 'reader')
        self.assertEqual(response.data['username'], 'ExactCase')
        member = EquipoUsuario.objects.get(equipo=self.group, usuario=target)
        self.assertEqual(self.client.post(url, {'usuario': target.pk}, format='json').status_code, 400)
        response = self.client.patch(f'{url}{target.pk}/', {'rol': 'editor'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EquipoUsuario.objects.get(equipo=self.group, usuario=target).pk, member.pk)
        self.assertEqual(response.data['rol'], 'editor')
        self.assertEqual(self.client.patch(f'{url}{target.pk}/', {'rol': 'reader', 'usuario': self.owner.pk}, format='json').status_code, 400)

    def test_authorize_before_identity_resolution_and_reader_self_exit(self):
        url = f'/api/equipos/{self.group.pk}/miembros/'
        for actor in (self.reader, self.external):
            self.client.force_authenticate(actor)
            with patch('proyectos.services.Usuario.objects.filter') as lookup:
                for body in ({'username': 'missing'}, {'usuario': 999999}, {'username': 'ExactCase', 'rol': 'bogus'}):
                    self.assertEqual(self.client.post(url, body, format='json').status_code, 403)
                lookup.assert_not_called()
        self.client.force_authenticate(self.reader)
        members = self.client.get(url).data
        own = next(member for member in members if member['usuario'] == self.reader.pk)
        self.assertEqual(own['permissions'], {'change_role': False, 'remove': True})
        self.assertEqual(self.client.patch(f'{url}{self.reader.pk}/', {'rol': 'admin'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'{url}{self.editor.pk}/').status_code, 403)
        self.assertEqual(self.client.delete(f'{url}{self.reader.pk}/').status_code, 204)

    def test_group_admin_cannot_escalate_capped_project_or_share_unmanaged_group(self):
        self.client.force_authenticate(self.external)
        url = f'/api/proyectos/{self.project.pk}/equipos/'
        self.assertEqual(self.client.patch(f'{url}{self.other.pk}/', {'rol': 'admin'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'{url}{self.group.pk}/').status_code, 403)
        self.client.force_authenticate(self.owner)
        third = services.create_group(self.reader, {'nombre': 'Tercero'})
        self.assertEqual(self.client.post(url, {'equipo': third.pk}, format='json').status_code, 403)
        self.assertEqual(self.client.post(url, {'equipo': self.group.pk}, format='json').status_code, 400)
        roles = {item['id']: item for item in self.client.get(url).data}
        self.assertEqual(roles[self.other.pk]['project_role'], 'reader')
        self.assertIsNone(roles[self.other.pk]['role'])

    def test_default_association_reader_and_project_create_is_atomic(self):
        group = services.create_group(self.owner, {'nombre': 'Compartido'})
        response = self.client.post(f'/api/proyectos/{self.project.pk}/equipos/', {'equipo': group.pk}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['rol'], 'reader')
        before = self.owner.equipos.count()
        self.assertEqual(self.client.post('/api/proyectos/', {'nombre': 'No autorizado', 'equipo': self.other.pk}, format='json').status_code, 400)
        self.assertEqual(before, self.owner.equipos.count())

    def test_last_group_admin_last_project_admin_last_team_and_group_delete(self):
        url = f'/api/equipos/{self.group.pk}/miembros/{self.owner.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.client.patch(url, {'rol': 'editor'}, format='json').status_code, 400)
        url = f'/api/proyectos/{self.project.pk}/equipos/'
        self.assertEqual(self.client.patch(f'{url}{self.group.pk}/', {'rol': 'reader'}, format='json').status_code, 400)
        self.assertEqual(self.client.delete(f'{url}{self.group.pk}/').status_code, 400)
        self.assertEqual(self.client.delete(f'/api/equipos/{self.group.pk}/').status_code, 400)
        self.assertEqual(self.client.delete(f'{url}{self.other.pk}/').status_code, 204)
        self.assertEqual(self.client.delete(f'{url}{self.group.pk}/').status_code, 400)
        self.assertEqual(self.project.equipos.count(), 1)

    def test_file_editor_own_only_private_admin_audience_and_uploader_exit(self):
        file = self.upload()
        peer = Usuario.objects.create_user(username='Peer')
        EquipoUsuario.objects.create(equipo=self.group, usuario=peer, rol=Role.EDITOR)
        url = f'/api/archivos/{file.pk}/'
        for actor, allowed in ((self.editor, True), (peer, False), (self.reader, False), (self.owner, True)):
            self.client.force_authenticate(actor)
            self.assertEqual(self.client.get(url).data['permissions']['edit'], allowed)
            self.assertEqual(self.client.patch(url, {'categoria': 'x'}, format='json').status_code, 200 if allowed else 403)
            if not allowed:
                self.assertEqual(self.client.delete(url).status_code, 403)
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(f'/api/equipos/{self.group.pk}/miembros/{self.editor.pk}/').status_code, 204)
        self.client.force_authenticate(peer)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(file.equipos_visibles.get(), self.group)
        self.client.force_authenticate(self.editor)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_global_to_private_uses_capture_not_new_memberships_or_actor_teams(self):
        file = self.upload(global_file=True)
        original = list(file.equipos_visibles.values_list('pk', flat=True))
        grant = ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.other)
        grant.rol = Role.ADMIN
        grant.save()
        EquipoUsuario.objects.create(equipo=self.other, usuario=self.editor, rol=Role.READER)
        self.client.force_authenticate(self.external)
        url = f'/api/archivos/{file.pk}/'
        self.assertEqual(self.client.patch(url, {'global': False}, format='json').status_code, 400)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(url, {'global': False}, format='json').status_code, 200)
        self.assertEqual(list(file.equipos_visibles.values_list('pk', flat=True)), original)

    def test_project_admin_can_manage_visible_private_files_but_not_hidden_files(self):
        file = self.upload()
        services.change_team(self.owner, self.project.pk, self.other.pk, Role.ADMIN)
        self.assertEqual(project_role(self.external, self.project), Role.ADMIN)
        self.assertFalse(file_permissions(self.external, file)['edit'])
        services.add_member(self.owner, self.group.pk, {'username': self.external.username, 'rol': Role.READER})
        self.assertEqual(group_role(self.external, self.group), Role.READER)
        self.assertEqual(file_permissions(self.external, file), {'edit': True, 'delete': True, 'download': True})
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.patch(f'/api/archivos/{file.pk}/', {'categoria': 'Actualizado'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/archivos/{file.pk}/').status_code, 204)

    def test_capture_all_creator_project_teams_and_private_audience_detach_guard(self):
        EquipoUsuario.objects.create(equipo=self.other, usuario=self.editor, rol=Role.READER)
        file = self.upload(global_file=True)
        self.assertEqual(set(file.equipos_visibles.values_list('pk', flat=True)), {self.group.pk, self.other.pk})
        private = self.upload()
        private.equipos_visibles.set([self.other])
        url = f'/api/proyectos/{self.project.pk}/equipos/{self.other.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertTrue(ProyectoEquipo.objects.filter(proyecto=self.project, equipo=self.other).exists())

    @override_settings(PROYECTOS_MAX_FILE_SIZE=4)
    def test_limit_applies_upload_replacement_and_project_reassignment(self):
        self.assertEqual(self.client.get('/api/me/').data['limits'], {'max_file_size': 4})
        data = {'nombre_original': 'x', 'archivo': SimpleUploadedFile('x', b'12345'), 'proyecto': self.project.pk}
        self.assertEqual(self.client.post('/api/archivos/', data, format='multipart').status_code, 400)
        data['archivo'] = SimpleUploadedFile('x', b'1234')
        response = self.client.post('/api/archivos/', data, format='multipart')
        self.assertEqual(response.status_code, 201)
        url = f'/api/archivos/{response.data["id"]}/'
        self.assertEqual(self.client.patch(url, {'archivo': SimpleUploadedFile('x', b'12345')}, format='multipart').status_code, 400)
        other_project = services.create_project(self.owner, {'nombre': 'Otro'})
        self.assertEqual(self.client.patch(url, {'proyecto': other_project.pk}, format='json').status_code, 400)
        self.assertEqual(Archivo.objects.get(pk=response.data['id']).proyecto, self.project)

    def test_django_admin_cannot_bypass_domain_guards(self):
        for model in (Archivo, EquipoUsuario, ProyectoEquipo, Usuario):
            registered = admin.site._registry[model]
            self.assertFalse(registered.has_change_permission(None))
            self.assertFalse(registered.has_delete_permission(None))
            self.assertFalse(registered.has_add_permission(None))


class StorageLifecycleTests(DomainFixture, TransactionTestCase):
    def test_project_cascade_requires_delete_permission_for_every_file(self):
        private = self.upload()
        shared = self.upload(global_file=True)
        files = [private, shared]
        project_id = self.project.pk
        services.change_team(self.owner, project_id, self.other.pk, Role.ADMIN)
        self.assertEqual(project_role(self.external, self.project), Role.ADMIN)
        self.assertFalse(file_permissions(self.external, private)['delete'])
        self.assertTrue(file_permissions(self.external, shared)['delete'])
        self.client.force_authenticate(self.external)

        response = self.client.delete(f'/api/proyectos/{project_id}/')
        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertEqual(self.project.archivos.count(), 2)
        self.assertEqual(self.project.equipos.count(), 2)
        self.assertEqual(list(private.equipos_visibles.values_list('pk', flat=True)), [self.group.pk])
        for file in files:
            self.assertTrue(file.archivo.storage.exists(file.archivo.name))
            with file.archivo.open('rb') as contents:
                self.assertEqual(contents.read(), b'original')

        self.client.force_authenticate(self.owner)
        with transaction.atomic():
            response = self.client.delete(f'/api/proyectos/{project_id}/')
            self.assertEqual(response.status_code, 204)
            self.assertFalse(Archivo.objects.filter(pk__in=[file.pk for file in files]).exists())
            for file in files:
                self.assertTrue(file.archivo.storage.exists(file.archivo.name))
        for file in files:
            self.assertFalse(file.archivo.storage.exists(file.archivo.name))

    def test_replacement_cleanup_only_after_commit_uuid_and_download(self):
        file = self.upload()
        old, storage = file.archivo.name, file.archivo.storage
        observed = []
        original = services.cleanup_after_commit

        def scheduled(*args, **kwargs):
            observed.append(storage.exists(old))
            original(*args, **kwargs)

        with patch('proyectos.services.cleanup_after_commit', side_effect=scheduled):
            result = services.save_file(self.editor, {'archivo': SimpleUploadedFile('documento.txt', b'new')}, file.pk)
            self.assertNotEqual(old, result.archivo.name)
        self.assertEqual(observed, [True])
        self.assertFalse(storage.exists(old))
        self.assertTrue(storage.exists(result.archivo.name))
        self.client.force_authenticate(self.editor)
        response = self.client.get(f'/api/archivos/{file.pk}/download/')
        self.assertEqual(b''.join(response.streaming_content), b'new')

    def test_file_write_rejects_outer_transaction_before_writing_bytes(self):
        storage = Archivo._meta.get_field('archivo').storage
        with transaction.atomic(), patch.object(storage, 'save') as saved:
            with self.assertRaisesMessage(RuntimeError, 'durable atomic block'):
                self.upload()
            saved.assert_not_called()
        self.assertFalse(Archivo.objects.exists())

    def test_failed_replacement_compensates_new_key_and_preserves_old(self):
        file = self.upload()
        old, storage = file.archivo.name, file.archivo.storage
        keys = []
        original = storage.save

        def saved(*args, **kwargs):
            key = original(*args, **kwargs)
            keys.append(key)
            return key

        with patch.object(storage, 'save', side_effect=saved), patch.object(Archivo, 'save', side_effect=RuntimeError('rollback')):
            with self.assertRaisesMessage(RuntimeError, 'rollback'):
                services.save_file(self.editor, {'archivo': SimpleUploadedFile('x.txt', b'new')}, file.pk)
        self.assertEqual(len(keys), 1)
        self.assertFalse(storage.exists(keys[0]))
        self.assertTrue(storage.exists(old))
        self.assertEqual(Archivo.objects.get(pk=file.pk).archivo.name, old)

    def test_failed_create_after_bytes_written_compensates_new_key(self):
        storage = Archivo._meta.get_field('archivo').storage
        keys = []
        original = storage.save

        def saved(*args, **kwargs):
            key = original(*args, **kwargs)
            keys.append(key)
            return key

        with patch.object(storage, 'save', side_effect=saved), patch.object(Archivo, 'save', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.upload()
        self.assertFalse(Archivo.objects.exists())
        self.assertEqual(len(keys), 1)
        self.assertFalse(storage.exists(keys[0]))

    def test_delete_rollback_keeps_bytes_and_record(self):
        file = self.upload()
        key, storage = file.archivo.name, file.archivo.storage
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                services.delete_file(self.owner, file.pk)
                self.assertTrue(storage.exists(key))
                raise RuntimeError('rollback')
        self.assertTrue(Archivo.objects.filter(pk=file.pk).exists())
        self.assertTrue(storage.exists(key))
        services.delete_file(self.owner, file.pk)
        self.assertFalse(storage.exists(key))

    def test_shared_legacy_key_rechecked_missing_key_tolerated(self):
        first = self.upload()
        key, storage = first.archivo.name, first.archivo.storage
        second = Archivo.objects.create(nombre_original='legacy', archivo=key, usuario=self.editor, proyecto=self.project)
        second.equipos_visibles.add(self.group)
        services.delete_file(self.owner, first.pk)
        self.assertTrue(storage.exists(key))
        services.delete_file(self.owner, second.pk)
        self.assertFalse(storage.exists(key))
        missing = self.upload()
        storage.delete(missing.archivo.name)
        services.delete_file(self.owner, missing.pk)
        self.assertFalse(Archivo.objects.exists())

    def test_project_cascade_commit_and_rollback_cleanup(self):
        file = self.upload()
        key, storage = file.archivo.name, file.archivo.storage
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                services.update_project(self.owner, self.project.pk, {}, delete=True)
                raise RuntimeError('rollback')
        self.assertTrue(storage.exists(key))
        self.assertTrue(Archivo.objects.filter(pk=file.pk).exists())
        services.update_project(self.owner, self.project.pk, {}, delete=True)
        self.assertFalse(storage.exists(key))
        self.assertFalse(Archivo.objects.exists())
