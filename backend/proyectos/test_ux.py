from uuid import uuid4
from unittest.mock import patch

from django.contrib import admin
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase

from . import services
from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, RequestState, Role, ShareRequest, Usuario
from .serializers import EquipoSerializer
from .test_roles import DomainFixture


class GroupUXTests(DomainFixture, TestCase):
    def test_selected_principal_reused_with_admin_and_capacity(self):
        group = services.create_group(self.owner, {'nombre': 'Seleccionado'})
        before = (Equipo.objects.count(), EquipoUsuario.objects.count())
        for role in ('reader', 'editor'):
            result = self.client.post('/api/proyectos/', {'nombre': 'Nuevo', 'equipo': group.pk,
                                                        'equipo_rol': role}, format='json')
            self.assertEqual(result.status_code, 400)
        result = self.client.post('/api/proyectos/', {'nombre': 'Nuevo', 'equipo': group.pk}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.data['equipos'], [group.pk])
        self.assertEqual(result.data['role'], 'admin')
        self.assertEqual(before, (Equipo.objects.count(), EquipoUsuario.objects.count()))
        self.assertEqual(ProyectoEquipo.objects.get(proyecto_id=result.data['id']).rol, Role.ADMIN)
        self.assertEqual(self.client.post('/api/proyectos/', {'nombre': 'Segundo', 'equipo': group.pk},
                                          format='json').status_code, 400)
        self.assertEqual(group.proyectos.count(), 1)
        self.assertEqual(self.client.patch(f'/api/equipos/{group.pk}/', {'equipo_recurrente': True},
                                          format='json').status_code, 200)
        self.assertEqual(self.client.post('/api/proyectos/', {'nombre': 'Segundo', 'equipo': group.pk},
                                         format='json').status_code, 201)
        self.assertEqual(self.client.patch(f'/api/equipos/{group.pk}/', {'equipo_recurrente': False},
                                          format='json').status_code, 400)
        group.refresh_from_db()
        self.assertTrue(group.equipo_recurrente)

    def test_nonrecurrent_association_capacity_and_empty_group(self):
        group = services.create_group(self.owner, {'nombre': 'Una vez'})
        url = f'/api/proyectos/{self.project.pk}/equipos/'
        self.assertEqual(self.client.post(url, {'equipo': group.pk}, format='json').status_code, 201)
        second = services.create_project(self.owner, {'nombre': 'Segundo'})
        self.assertEqual(self.client.post(f'/api/proyectos/{second.pk}/equipos/', {'equipo': group.pk},
                                         format='json').status_code, 400)
        self.assertEqual(self.client.delete(f'{url}{group.pk}/').status_code, 204)
        self.assertEqual(self.client.patch(f'/api/equipos/{group.pk}/', {'equipo_recurrente': False},
                                          format='json').status_code, 200)
        self.assertEqual(self.client.post(f'/api/proyectos/{second.pk}/equipos/', {'equipo': group.pk},
                                         format='json').status_code, 201)

    def test_group_code_unique_readonly_private_and_project_ids(self):
        self.assertNotEqual(self.group.codigo, self.other.codigo)
        url = f'/api/equipos/{self.group.pk}/'
        code = self.client.get(url).data['codigo']
        self.assertEqual(code, str(self.group.codigo))
        self.assertEqual(self.client.get(url).data['proyectos'], [self.project.pk])
        self.client.patch(url, {'codigo': str(uuid4())}, format='json')
        self.group.refresh_from_db()
        self.assertEqual(code, str(self.group.codigo))
        for actor in (self.editor, self.reader):
            self.client.force_authenticate(actor)
            self.assertIsNone(self.client.get(url).data['codigo'])
        self.client.force_authenticate(self.owner)
        external = next(item for item in self.client.get(f'/api/proyectos/{self.project.pk}/equipos/').data
                        if item['id'] == self.other.pk)
        self.assertIsNone(external['codigo'])
        self.assertIsNone(EquipoSerializer(self.group).data['codigo'])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url, {'codigo': code}).status_code, 401)

    def test_preview_and_delete_guard_no_hidden_file_metadata(self):
        file = self.upload()
        url = f'/api/equipos/{self.group.pk}/'
        preview = self.client.get(f'{url}eliminacion/')
        self.assertEqual(preview.status_code, 200)
        self.assertFalse(preview.data['permitido'])
        self.assertEqual(preview.data['proyectos'], [{'id': self.project.pk, 'nombre': self.project.nombre}])
        self.assertNotIn(file.nombre_original, str(preview.data))
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertTrue(Equipo.objects.filter(pk=self.group.pk).exists())
        self.assertEqual(self.project.equipos.count(), 2)
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get(f'{url}eliminacion/').status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)

    def test_associated_group_does_not_disclose_inaccessible_projects(self):
        self.other.equipo_recurrente = True
        self.other.save()
        hidden = services.create_project(self.external, {'nombre': 'Privado', 'equipo': self.other})
        groups = self.client.get(f'/api/proyectos/{self.project.pk}/equipos/').data
        shared = next(group for group in groups if group['id'] == self.other.pk)
        self.assertEqual(shared['proyectos'], [self.project.pk])
        self.assertIsNone(shared['codigo'])
        self.assertEqual(self.client.get(f'/api/proyectos/{hidden.pk}/').status_code, 404)
        self.assertEqual(EquipoSerializer(self.other).data['proyectos'], [])
        self.client.force_authenticate(self.external)
        self.assertCountEqual(self.client.get(f'/api/equipos/{self.other.pk}/').data['proyectos'], [self.project.pk, hidden.pk])

    def test_group_admin_withdrawal_without_project_admin_and_private_rollback(self):
        file = self.upload()
        file.equipos_visibles.set([self.other])
        self.client.force_authenticate(self.external)
        self.assertEqual(services.project_role(self.external, self.project), Role.READER)
        url = f'/api/equipos/{self.other.pk}/'
        preview = self.client.get(f'{url}eliminacion/').data
        self.assertFalse(preview['permitido'])
        self.assertNotIn(file.nombre_original, str(preview))
        self.assertEqual(self.client.delete(url).status_code, 400)
        file.equipos_visibles.add(self.group)
        self.assertTrue(self.client.get(f'{url}eliminacion/').data['permitido'])
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertTrue(Proyecto.objects.filter(pk=self.project.pk).exists())
        self.assertTrue(Archivo.objects.filter(pk=file.pk).exists())
        self.assertEqual(list(file.equipos_visibles.values_list('pk', flat=True)), [self.group.pk])

    def test_multi_project_delete_all_or_none_and_rechecks_preview(self):
        self.other.equipo_recurrente = True
        self.other.save()
        second = services.create_project(self.external, {'nombre': 'Segundo', 'equipo': self.other})
        self.client.force_authenticate(self.external)
        url = f'/api/equipos/{self.other.pk}/'
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.other.proyectos.count(), 2)
        control = services.create_group(self.external, {'nombre': 'Control'})
        services.add_team(self.external, second.pk, {'equipo': control, 'rol': Role.ADMIN})
        self.assertTrue(self.client.get(f'{url}eliminacion/').data['permitido'])
        services.change_team(self.external, second.pk, control.pk, delete=True)
        self.assertEqual(self.client.delete(url).status_code, 400)
        self.assertEqual(self.other.proyectos.count(), 2)
        services.add_team(self.external, second.pk, {'equipo': control, 'rol': Role.ADMIN})
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(ProyectoEquipo.objects.filter(equipo_id=self.other.pk).exists())
        self.assertTrue(Proyecto.objects.filter(pk=second.pk).exists())
        services.check_project(second)
        services.check_project(self.project)

    def test_exact_candidate_active_case_sensitive_authorize_first_no_writes(self):
        target = Usuario.objects.create_user(username='ExactCase')
        url = f'/api/equipos/{self.group.pk}/candidato/'
        for username in ('exactcase', ' ExactCase', 'ExactCase ', 'missing'):
            result = self.client.get(url, {'username': username})
            self.assertEqual(result.status_code, 404)
            self.assertEqual(str(result.data['detail']), 'Usuario no encontrado.')
        target.is_active = False
        target.save()
        self.assertEqual(self.client.get(url, {'username': target.username}).status_code, 404)
        target.is_active = True
        target.save()
        before = EquipoUsuario.objects.count()
        self.assertEqual(self.client.get(url, {'username': target.username}).data,
                         {'usuario': target.pk, 'username': target.username, 'ya_es_miembro': False})
        self.assertTrue(self.client.get(url, {'username': self.owner.username}).data['ya_es_miembro'])
        self.assertEqual(before, EquipoUsuario.objects.count())
        self.assertEqual(self.client.get(url).status_code, 400)
        for actor in (self.reader, self.external):
            self.client.force_authenticate(actor)
            with patch('proyectos.services.Usuario.objects.filter') as lookup:
                self.assertEqual(self.client.get(url, {'username': target.username}).status_code, 403)
                self.assertEqual(self.client.get(url).status_code, 403)
                lookup.assert_not_called()

    def test_file_dto_real_username_and_only_associated_captured_details(self):
        file = self.upload()
        file.equipos_visibles.add(self.other)
        services.change_team(self.owner, self.project.pk, self.other.pk, delete=True)
        data = self.client.get(f'/api/archivos/{file.pk}/').data
        self.assertEqual(data['usuario_nombre'], self.editor.username)
        self.assertCountEqual(data['equipos_visibles'], [self.group.pk, self.other.pk])
        self.assertEqual(data['equipos_visibles_detalle'], [{'id': self.group.pk, 'nombre': self.group.nombre}])
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.get(f'/api/archivos/{file.pk}/').status_code, 404)


class ShareRequestTests(DomainFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.receiver = services.create_group(self.external, {'nombre': 'Receptor'})
        self.url = f'/api/proyectos/{self.project.pk}/solicitudes/'
        self.inbox = f'/api/equipos/{self.receiver.pk}/solicitudes/'

    def request_share(self, role=Role.READER):
        return services.create_share_request(self.owner, self.project.pk,
                                             {'codigo': self.receiver.codigo, 'rol': role})

    def test_pending_no_access_exact_dto_then_consent_capped_association(self):
        self.upload()
        public = self.upload(global_file=True)
        services.change_team(self.owner, self.project.pk, self.other.pk, delete=True)
        result = self.client.post(self.url, {'codigo': str(self.receiver.codigo)}, format='json')
        self.assertEqual(result.status_code, 201)
        request_id = result.data['id']
        self.assertEqual(result.data, {
            'id': request_id, 'proyecto': {'id': self.project.pk, 'nombre': self.project.nombre},
            'equipo': {'id': self.receiver.pk, 'nombre': self.receiver.nombre},
            'solicitado_por': {'id': self.owner.pk, 'username': self.owner.username},
            'rol': 'reader', 'estado': 'pending',
            'permissions': {'accept': False, 'reject': False, 'cancel': True},
        })
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.get(f'/api/proyectos/{self.project.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/archivos/{public.pk}/').status_code, 404)
        inbox = self.client.get(self.inbox).data
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]['permissions'], {'accept': True, 'reject': True, 'cancel': False})
        self.assertNotIn('codigo', inbox[0]['equipo'])
        result = self.client.post(f'{self.inbox}{request_id}/', {'accion': 'accept'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['estado'], 'accepted')
        self.assertFalse(any(result.data['permissions'].values()))
        self.assertEqual(ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.receiver).rol, Role.READER)
        self.assertEqual(self.client.get(f'/api/proyectos/{self.project.pk}/').data['role'], 'reader')
        self.assertEqual(len(self.client.get('/api/archivos/').data), 1)
        self.assertEqual(self.client.get(self.inbox).data, [])
        self.assertEqual(self.client.post(f'{self.inbox}{request_id}/', {'accion': 'accept'}, format='json').status_code, 400)

    def test_only_authorized_sender_can_resolve_code_and_duplicate_errors(self):
        for actor in (self.editor, self.reader):
            self.client.force_authenticate(actor)
            with patch('proyectos.services.Equipo.objects.filter') as lookup:
                self.assertEqual(self.client.post(self.url, {'codigo': 'invalid'}, format='json').status_code, 403)
                self.assertEqual(self.client.get(self.url).status_code, 403)
                lookup.assert_not_called()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url, {'codigo': str(uuid4())}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {'codigo': str(self.receiver.codigo), 'rol': 'owner'},
                                         format='json').status_code, 400)
        self.request_share()
        self.assertEqual(self.client.post(self.url, {'codigo': str(self.receiver.codigo)}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {'codigo': str(self.other.codigo)}, format='json').status_code, 400)

    def test_wrong_receiver_scope_authority_and_no_role_upgrade(self):
        request = self.request_share(Role.EDITOR)
        self.assertEqual(self.client.get(self.inbox).status_code, 403)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 403)
        EquipoUsuario.objects.create(equipo=self.receiver, usuario=self.reader, rol=Role.READER)
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get(self.inbox).status_code, 403)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 403)
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.post(f'/api/equipos/{self.other.pk}/solicitudes/{request.pk}/',
                                         {'accion': 'accept'}, format='json').status_code, 404)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept', 'rol': 'admin'},
                                         format='json').status_code, 400)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 200)
        self.assertEqual(ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.receiver).rol, Role.EDITOR)

    def test_cancel_reject_history_replay_wrong_project(self):
        request = self.request_share()
        second = services.create_project(self.owner, {'nombre': 'Otro'})
        self.assertEqual(self.client.delete(f'/api/proyectos/{second.pk}/solicitudes/{request.pk}/').status_code, 404)
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.delete(f'{self.url}{request.pk}/').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(f'{self.url}{request.pk}/').status_code, 204)
        self.assertEqual(self.client.delete(f'{self.url}{request.pk}/').status_code, 400)
        self.client.force_authenticate(self.external)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 400)
        replacement = self.request_share()
        self.assertEqual(self.client.post(f'{self.inbox}{replacement.pk}/', {'accion': 'reject'}, format='json').status_code, 200)
        self.assertFalse(ProyectoEquipo.objects.filter(proyecto=self.project, equipo=self.receiver).exists())
        self.assertEqual(self.client.post(f'{self.inbox}{replacement.pk}/', {'accion': 'reject'}, format='json').status_code, 400)
        self.client.force_authenticate(self.owner)
        self.assertEqual([item['estado'] for item in self.client.get(self.url).data], ['cancelled', 'rejected'])

    def test_sender_revocation_or_inactivity_prevents_accept_but_allows_reject(self):
        request = self.request_share()
        services.change_member(self.owner, self.group.pk, self.editor.pk, Role.ADMIN)
        services.change_member(self.owner, self.group.pk, self.owner.pk, Role.EDITOR)
        self.client.force_authenticate(self.external)
        permissions = self.client.get(self.inbox).data[0]['permissions']
        self.assertEqual(permissions, {'accept': False, 'reject': True, 'cancel': False})
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 403)
        self.assertFalse(ProyectoEquipo.objects.filter(equipo=self.receiver, proyecto=self.project).exists())
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'reject'}, format='json').status_code, 200)
        request = services.create_share_request(self.editor, self.project.pk, {'codigo': self.receiver.codigo})
        self.editor.is_active = False
        self.editor.save()
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 403)

    def test_capacity_and_existing_association_cannot_be_upgraded(self):
        request = self.request_share(Role.ADMIN)
        second = services.create_project(self.external, {'nombre': 'Segundo', 'equipo': self.receiver})
        self.client.force_authenticate(self.external)
        self.assertFalse(self.client.get(self.inbox).data[0]['permissions']['accept'])
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 400)
        services.update_group(self.external, self.receiver.pk, {'equipo_recurrente': True})
        ProyectoEquipo.objects.create(proyecto=self.project, equipo=self.receiver, rol=Role.READER)
        self.assertFalse(self.client.get(self.inbox).data[0]['permissions']['accept'])
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 400)
        self.assertEqual(ProyectoEquipo.objects.get(proyecto=self.project, equipo=self.receiver).rol, Role.READER)
        services.check_project(second)

    def test_accept_rolls_back_association_if_state_write_fails(self):
        request = self.request_share()
        with patch.object(ShareRequest, 'save', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                services.answer_share_request(self.external, self.receiver.pk, request.pk, 'accept')
        request.refresh_from_db()
        self.assertEqual(request.estado, RequestState.PENDING)
        self.assertFalse(ProyectoEquipo.objects.filter(equipo=self.receiver, proyecto=self.project).exists())

    def test_db_pending_uniqueness_canonical_roles_and_readonly_admin(self):
        request = self.request_share()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareRequest.objects.create(proyecto=self.project, equipo=self.receiver, solicitado_por=self.owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareRequest.objects.filter(pk=request.pk).update(rol='owner')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareRequest.objects.filter(pk=request.pk).update(estado='unknown')
        registered = admin.site._registry[ShareRequest]
        self.assertFalse(registered.has_add_permission(None))
        self.assertFalse(registered.has_change_permission(None))
        self.assertFalse(registered.has_delete_permission(None))

    def test_deleted_requester_serializes_null_and_cannot_authorize_accept(self):
        request = self.request_share()
        services.change_member(self.owner, self.group.pk, self.editor.pk, Role.ADMIN)
        self.owner.delete()
        self.client.force_authenticate(self.external)
        data = self.client.get(self.inbox).data[0]
        self.assertIsNone(data['solicitado_por'])
        self.assertEqual(data['permissions'], {'accept': False, 'reject': True, 'cancel': False})
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'accept'}, format='json').status_code, 403)
        self.assertEqual(self.client.post(f'{self.inbox}{request.pk}/', {'accion': 'reject'}, format='json').status_code, 200)


class GroupDeletionStorageTests(DomainFixture, TransactionTestCase):
    def test_group_delete_preserves_project_file_and_bytes_even_on_rollback(self):
        file = self.upload()
        file.equipos_visibles.add(self.other)
        services.change_team(self.owner, self.project.pk, self.other.pk, Role.ADMIN)
        key, storage = file.archivo.name, file.archivo.storage
        group_id = self.group.pk
        with self.assertRaises(RuntimeError), transaction.atomic():
            services.update_group(self.owner, group_id, {}, delete=True)
            raise RuntimeError('rollback')
        self.assertTrue(Equipo.objects.filter(pk=group_id).exists())
        self.assertEqual(self.project.equipos.count(), 2)
        self.assertEqual(file.equipos_visibles.count(), 2)
        services.update_group(self.owner, group_id, {}, delete=True)
        self.assertEqual(self.project.equipos.get(), self.other)
        self.assertTrue(Archivo.objects.filter(pk=file.pk).exists())
        self.assertTrue(storage.exists(key))
        with storage.open(key) as contents:
            self.assertEqual(contents.read(), b'original')
