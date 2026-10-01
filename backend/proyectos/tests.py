from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo


Usuario = get_user_model()


class AutenticacionTests(APITestCase):
	def setUp(self):
		self.client = APIClient()
		self.password = 'ClaveSegura123!'
		self.usuario = Usuario.objects.create_user(
			username='usuario_existente',
			email='existente@example.com',
			password=self.password,
		)

	def test_registro_guarda_password_hasheada(self):
		response = self.client.post(
			reverse('registro'),
			{
				'username': 'nuevo_usuario',
				'email': 'nuevo@example.com',
				'password': 'NuevaClave123!',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		usuario = Usuario.objects.get(username='nuevo_usuario')
		self.assertTrue(usuario.check_password('NuevaClave123!'))
		self.assertNotIn('password', response.data)
		self.assertFalse(usuario.is_staff)
		self.assertFalse(usuario.is_superuser)

	def test_registro_rechaza_username_duplicado(self):
		response = self.client.post(
			reverse('registro'),
			{
				'username': self.usuario.username,
				'email': 'otro@example.com',
				'password': 'NuevaClave123!',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_registro_rechaza_password_corta(self):
		response = self.client.post(
			reverse('registro'),
			{
				'username': 'usuario_invalido',
				'email': 'invalido@example.com',
				'password': 'corta',
			},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

	def test_login_refresh_y_verify(self):
		response = self.client.post(
			reverse('token_obtain_pair'),
			{'username': self.usuario.username, 'password': self.password},
			format='json',
		)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertIn('access', response.data)
		self.assertIn('refresh', response.data)

		refresh_response = self.client.post(
			reverse('token_refresh'),
			{'refresh': response.data['refresh']},
			format='json',
		)
		self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)

		verify_response = self.client.post(
			reverse('token_verify'),
			{'token': response.data['access']},
			format='json',
		)
		self.assertEqual(verify_response.status_code, status.HTTP_200_OK)


class ProyectosYEquiposTests(APITestCase):
	def setUp(self):
		self.client = APIClient()
		self.password = 'ClaveSegura123!'
		self.usuario = Usuario.objects.create_user(username='admin_proyecto', password=self.password)
		self.otro_usuario = Usuario.objects.create_user(username='otro_usuario', password=self.password)
		self.tercer_usuario = Usuario.objects.create_user(username='usuario_fuera', password=self.password)

	def _obtener_token(self, usuario):
		response = self.client.post(
			reverse('token_obtain_pair'),
			{'username': usuario.username, 'password': self.password},
			format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return response.data['access']

	def test_usuario_puede_crear_proyecto_y_verlo_en_lista(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		response = self.client.post('/api/proyectos/', {'nombre': 'Proyecto alpha', 'descripcion': 'desc'}, format='json')
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)

		lista = self.client.get('/api/proyectos/')
		self.assertEqual(lista.status_code, status.HTTP_200_OK)
		self.assertEqual(len(lista.data), 1)
		self.assertEqual(lista.data[0]['nombre'], 'Proyecto alpha')

	def test_usuario_puede_crear_equipo_y_añadir_miembro(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		response = self.client.post('/api/equipos/', {'nombre': 'Equipo alpha'}, format='json')
		self.assertEqual(response.status_code, status.HTTP_201_CREATED)
		equipo_id = response.data['id']

		miembro = self.client.post(
			f'/api/equipos/{equipo_id}/miembros/',
			{'usuario': self.otro_usuario.id, 'rol': 'analista'},
			format='json',
		)
		self.assertEqual(miembro.status_code, status.HTTP_201_CREATED)
		self.assertEqual(miembro.data['usuario'], self.otro_usuario.id)

		lista = self.client.get('/api/equipos/')
		self.assertEqual(lista.status_code, status.HTTP_200_OK)
		self.assertEqual(len(lista.data), 1)

	def test_usuario_ajeno_no_puede_añadir_miembros_a_un_equipo_del_que_no_forma_parte(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		equipo = self.client.post('/api/equipos/', {'nombre': 'Equipo cerrado'}, format='json')
		self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)
		equipo_id = equipo.data['id']

		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.tercer_usuario)}")
		respuesta = self.client.post(
			f'/api/equipos/{equipo_id}/miembros/',
			{'usuario': self.otro_usuario.id},
			format='json',
		)
		self.assertEqual(respuesta.status_code, status.HTTP_403_FORBIDDEN)

	def test_un_equipo_puede_pertenecer_a_varios_proyectos(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		proyecto_1 = self.client.post('/api/proyectos/', {'nombre': 'Proyecto A'}, format='json')
		proyecto_2 = self.client.post('/api/proyectos/', {'nombre': 'Proyecto B'}, format='json')
		equipo = self.client.post('/api/equipos/', {'nombre': 'Equipo compartido'}, format='json')
		self.assertEqual(proyecto_1.status_code, status.HTTP_201_CREATED)
		self.assertEqual(proyecto_2.status_code, status.HTTP_201_CREATED)
		self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

		asociacion_1 = self.client.post(
			f"/api/proyectos/{proyecto_1.data['id']}/equipos/",
			{'equipo': equipo.data['id'], 'objetivo': 'Primer proyecto'},
			format='json',
		)
		asociacion_2 = self.client.post(
			f"/api/proyectos/{proyecto_2.data['id']}/equipos/",
			{'equipo': equipo.data['id'], 'objetivo': 'Segundo proyecto'},
			format='json',
		)

		self.assertEqual(asociacion_1.status_code, status.HTTP_201_CREATED)
		self.assertEqual(asociacion_2.status_code, status.HTTP_201_CREATED)
		self.assertEqual(Proyecto.objects.filter(equipos=equipo.data['id']).count(), 2)

	def test_un_equipo_puede_quitarse_de_un_proyecto(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		proyecto = self.client.post('/api/proyectos/', {'nombre': 'Proyecto C'}, format='json')
		equipo = self.client.post('/api/equipos/', {'nombre': 'Equipo para quitar'}, format='json')
		self.assertEqual(proyecto.status_code, status.HTTP_201_CREATED)
		self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

		asociacion = self.client.post(
			f"/api/proyectos/{proyecto.data['id']}/equipos/",
			{'equipo': equipo.data['id'], 'objetivo': 'Quitar después'},
			format='json',
		)
		self.assertEqual(asociacion.status_code, status.HTTP_201_CREATED)

		respuesta = self.client.delete(f"/api/proyectos/{proyecto.data['id']}/equipos/{equipo.data['id']}/")
		self.assertEqual(respuesta.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(Proyecto.objects.get(pk=proyecto.data['id']).equipos.filter(pk=equipo.data['id']).exists())

	def test_un_miembro_puede_ser_eliminado_de_un_equipo(self):
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		equipo = self.client.post('/api/equipos/', {'nombre': 'Equipo con miembros'}, format='json')
		self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

		miembro = self.client.post(
			f"/api/equipos/{equipo.data['id']}/miembros/",
			{'usuario': self.otro_usuario.id, 'rol': 'analista'},
			format='json',
		)
		self.assertEqual(miembro.status_code, status.HTTP_201_CREATED)

		lista = self.client.get(f"/api/equipos/{equipo.data['id']}/miembros/")
		self.assertEqual(lista.status_code, status.HTTP_200_OK)
		self.assertEqual(len(lista.data), 2)

		respuesta = self.client.delete(f"/api/equipos/{equipo.data['id']}/miembros/{self.otro_usuario.id}/")
		self.assertEqual(respuesta.status_code, status.HTTP_204_NO_CONTENT)
		self.assertFalse(Equipo.objects.get(pk=equipo.data['id']).usuarios.filter(pk=self.otro_usuario.id).exists())


class ProteccionArchivoTests(APITestCase):
	def setUp(self):
		self.client = APIClient()
		self.password = 'ClaveSegura123!'
		self.usuario = Usuario.objects.create_user(
			username='miembro',
			password=self.password,
		)
		self.equipo = Equipo.objects.create(nombre='Equipo de prueba')
		EquipoUsuario.objects.create(usuario=self.usuario, equipo=self.equipo)
		self.proyecto = Proyecto.objects.create(nombre='Proyecto de prueba')
		ProyectoEquipo.objects.create(proyecto=self.proyecto, equipo=self.equipo)
		self.otro_usuario = Usuario.objects.create_user(
			username='otro_miembro',
			password=self.password,
		)
		self.otro_equipo = Equipo.objects.create(nombre='Otro equipo')
		ProyectoEquipo.objects.create(proyecto=self.proyecto, equipo=self.otro_equipo)
		EquipoUsuario.objects.create(usuario=self.otro_usuario, equipo=self.otro_equipo)

	def _obtener_token(self, usuario):
		response = self.client.post(
			reverse('token_obtain_pair'),
			{'username': usuario.username, 'password': self.password},
			format='json',
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return response.data['access']

	def test_archivos_requiere_autenticacion(self):
		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_archivos_acepta_access_token(self):
		token = self._obtener_token(self.usuario)
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_archivos_acepta_sesion_de_django(self):
		self.client.login(username=self.usuario.username, password=self.password)

		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_archivo_privado_solo_es_visible_para_su_equipo(self):
		Archivo.objects.create(
			nombre_original='informe.txt',
			archivo=SimpleUploadedFile('informe.txt', b'contenido confidencial'),
			categoria='documento',
			usuario=self.usuario,
			proyecto=self.proyecto,
			**{'global': False},
		)

		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		response_usuario = self.client.get('/api/archivos/')
		self.assertEqual(response_usuario.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_usuario.data), 1)

		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.otro_usuario)}")
		response_otro = self.client.get('/api/archivos/')
		self.assertEqual(response_otro.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_otro.data), 0)

	def test_archivo_global_es_visible_para_todos_los_equipos_del_proyecto(self):
		Archivo.objects.create(
			nombre_original='informe-global.txt',
			archivo=SimpleUploadedFile('informe-global.txt', b'contenido global'),
			categoria='documento',
			usuario=self.usuario,
			proyecto=self.proyecto,
			**{'global': True},
		)

		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}")
		response_usuario = self.client.get('/api/archivos/')
		self.assertEqual(response_usuario.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_usuario.data), 1)

		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.otro_usuario)}")
		response_otro = self.client.get('/api/archivos/')
		self.assertEqual(response_otro.status_code, status.HTTP_200_OK)
		self.assertEqual(len(response_otro.data), 1)

	def test_archivo_no_se_puede_crear_si_usuario_no_pertenece_al_proyecto(self):
		otro_proyecto = Proyecto.objects.create(nombre='Proyecto ajeno')
		usuario_ajeno = Usuario.objects.create_user(username='usuario_ajeno', password=self.password)
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(usuario_ajeno)}")

		response = self.client.post(
			'/api/archivos/',
			{
				'nombre_original': 'ajeno.txt',
				'archivo': SimpleUploadedFile('ajeno.txt', b'contenido'),
				'categoria': 'documento',
				'proyecto': otro_proyecto.id,
				'global': 'false',
			},
			format='multipart',
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
