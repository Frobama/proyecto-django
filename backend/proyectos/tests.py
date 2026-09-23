from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import Equipo, EquipoUsuario, Proyecto, ProyectoEquipo


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

	def test_archivos_requiere_autenticacion(self):
		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

	def test_archivos_acepta_access_token(self):
		token_response = self.client.post(
			reverse('token_obtain_pair'),
			{'username': self.usuario.username, 'password': self.password},
			format='json',
		)
		self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_response.data['access']}")

		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)

	def test_archivos_acepta_sesion_de_django(self):
		self.client.login(username=self.usuario.username, password=self.password)

		response = self.client.get('/api/archivos/')

		self.assertEqual(response.status_code, status.HTTP_200_OK)

# Create your tests here.
