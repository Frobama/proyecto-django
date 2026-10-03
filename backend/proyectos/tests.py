from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo
from .serializers import ArchivoSerializer, EquipoSerializer, ProyectoSerializer


Usuario = get_user_model()


class AutenticacionTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "ClaveSegura123!"
        self.usuario = Usuario.objects.create_user(
            username="usuario_existente",
            email="existente@example.com",
            password=self.password,
        )

    def test_registro_guarda_password_hasheada(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": "nuevo_usuario",
                "email": "nuevo@example.com",
                "password": "NuevaClave123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        usuario = Usuario.objects.get(username="nuevo_usuario")
        self.assertTrue(usuario.check_password("NuevaClave123!"))
        self.assertNotIn("password", response.data)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_registro_rechaza_username_duplicado(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": self.usuario.username,
                "email": "otro@example.com",
                "password": "NuevaClave123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registro_rechaza_password_corta(self):
        response = self.client.post(
            reverse("registro"),
            {
                "username": "usuario_invalido",
                "email": "invalido@example.com",
                "password": "corta",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_refresh_y_verify(self):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.usuario.username, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": response.data["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)

        verify_response = self.client.post(
            reverse("token_verify"),
            {"token": response.data["access"]},
            format="json",
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)

    def test_schema_openapi_esta_disponible(self):
        response = self.client.get(reverse("schema"), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("/api/proyectos/", response.data["paths"])
        self.assertIn("/api/equipos/", response.data["paths"])
        self.assertIn("/api/archivos/", response.data["paths"])


class ProyectosYEquiposTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "ClaveSegura123!"
        self.usuario = Usuario.objects.create_user(
            username="admin_proyecto", password=self.password
        )
        self.otro_usuario = Usuario.objects.create_user(
            username="otro_usuario", password=self.password
        )
        self.tercer_usuario = Usuario.objects.create_user(
            username="usuario_fuera", password=self.password
        )

    def _obtener_token(self, usuario):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": usuario.username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["access"]

    def test_usuario_puede_crear_proyecto_y_verlo_en_lista(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        response = self.client.post(
            "/api/proyectos/",
            {"nombre": "Proyecto alpha", "descripcion": "desc"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        lista = self.client.get("/api/proyectos/")
        self.assertEqual(lista.status_code, status.HTTP_200_OK)
        self.assertEqual(len(lista.data), 1)
        self.assertEqual(lista.data[0]["nombre"], "Proyecto alpha")

    def test_usuario_puede_crear_equipo_y_añadir_miembro(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        response = self.client.post(
            "/api/equipos/", {"nombre": "Equipo alpha"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        equipo_id = response.data["id"]

        miembro = self.client.post(
            f"/api/equipos/{equipo_id}/miembros/",
            {"usuario": self.otro_usuario.id, "rol": "analista"},
            format="json",
        )
        self.assertEqual(miembro.status_code, status.HTTP_201_CREATED)
        self.assertEqual(miembro.data["usuario"], self.otro_usuario.id)

        lista = self.client.get("/api/equipos/")
        self.assertEqual(lista.status_code, status.HTTP_200_OK)
        self.assertEqual(len(lista.data), 1)

    def test_usuario_ajeno_no_puede_añadir_miembros_a_un_equipo_del_que_no_forma_parte(
        self,
    ):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        equipo = self.client.post(
            "/api/equipos/", {"nombre": "Equipo cerrado"}, format="json"
        )
        self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)
        equipo_id = equipo.data["id"]

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.tercer_usuario)}"
        )
        respuesta = self.client.post(
            f"/api/equipos/{equipo_id}/miembros/",
            {"usuario": self.otro_usuario.id},
            format="json",
        )
        self.assertEqual(respuesta.status_code, status.HTTP_403_FORBIDDEN)

    def test_un_equipo_puede_pertenecer_a_varios_proyectos(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        proyecto_1 = self.client.post(
            "/api/proyectos/", {"nombre": "Proyecto A"}, format="json"
        )
        proyecto_2 = self.client.post(
            "/api/proyectos/", {"nombre": "Proyecto B"}, format="json"
        )
        equipo = self.client.post(
            "/api/equipos/", {"nombre": "Equipo compartido"}, format="json"
        )
        self.assertEqual(proyecto_1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(proyecto_2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

        asociacion_1 = self.client.post(
            f"/api/proyectos/{proyecto_1.data['id']}/equipos/",
            {"equipo": equipo.data["id"], "objetivo": "Primer proyecto"},
            format="json",
        )
        asociacion_2 = self.client.post(
            f"/api/proyectos/{proyecto_2.data['id']}/equipos/",
            {"equipo": equipo.data["id"], "objetivo": "Segundo proyecto"},
            format="json",
        )

        self.assertEqual(asociacion_1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(asociacion_2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Proyecto.objects.filter(equipos=equipo.data["id"]).count(), 2)

    def test_un_equipo_puede_quitarse_de_un_proyecto(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        proyecto = self.client.post(
            "/api/proyectos/", {"nombre": "Proyecto C"}, format="json"
        )
        equipo = self.client.post(
            "/api/equipos/", {"nombre": "Equipo para quitar"}, format="json"
        )
        self.assertEqual(proyecto.status_code, status.HTTP_201_CREATED)
        self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

        asociacion = self.client.post(
            f"/api/proyectos/{proyecto.data['id']}/equipos/",
            {"equipo": equipo.data["id"], "objetivo": "Quitar después"},
            format="json",
        )
        self.assertEqual(asociacion.status_code, status.HTTP_201_CREATED)

        respuesta = self.client.delete(
            f"/api/proyectos/{proyecto.data['id']}/equipos/{equipo.data['id']}/"
        )
        self.assertEqual(respuesta.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Proyecto.objects.get(pk=proyecto.data["id"])
            .equipos.filter(pk=equipo.data["id"])
            .exists()
        )

    def test_un_miembro_puede_ser_eliminado_de_un_equipo(self):
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        equipo = self.client.post(
            "/api/equipos/", {"nombre": "Equipo con miembros"}, format="json"
        )
        self.assertEqual(equipo.status_code, status.HTTP_201_CREATED)

        miembro = self.client.post(
            f"/api/equipos/{equipo.data['id']}/miembros/",
            {"usuario": self.otro_usuario.id, "rol": "analista"},
            format="json",
        )
        self.assertEqual(miembro.status_code, status.HTTP_201_CREATED)

        lista = self.client.get(f"/api/equipos/{equipo.data['id']}/miembros/")
        self.assertEqual(lista.status_code, status.HTTP_200_OK)
        self.assertEqual(len(lista.data), 2)

        respuesta = self.client.delete(
            f"/api/equipos/{equipo.data['id']}/miembros/{self.otro_usuario.id}/"
        )
        self.assertEqual(respuesta.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Equipo.objects.get(pk=equipo.data["id"])
            .usuarios.filter(pk=self.otro_usuario.id)
            .exists()
        )


class ProteccionArchivoTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.password = "ClaveSegura123!"
        self.usuario = Usuario.objects.create_user(
            username="miembro",
            password=self.password,
        )
        self.equipo = Equipo.objects.create(nombre="Equipo de prueba")
        EquipoUsuario.objects.create(usuario=self.usuario, equipo=self.equipo)
        self.proyecto = Proyecto.objects.create(nombre="Proyecto de prueba")
        ProyectoEquipo.objects.create(proyecto=self.proyecto, equipo=self.equipo)
        self.otro_usuario = Usuario.objects.create_user(
            username="otro_miembro",
            password=self.password,
        )
        self.otro_equipo = Equipo.objects.create(nombre="Otro equipo")
        ProyectoEquipo.objects.create(proyecto=self.proyecto, equipo=self.otro_equipo)
        EquipoUsuario.objects.create(usuario=self.otro_usuario, equipo=self.otro_equipo)

    def tearDown(self):
        for archivo in Archivo.objects.all():
            archivo.archivo.delete(save=False)
        super().tearDown()

    def _obtener_token(self, usuario):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": usuario.username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["access"]

    def test_archivos_requiere_autenticacion(self):
        response = self.client.get("/api/archivos/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_archivos_acepta_access_token(self):
        token = self._obtener_token(self.usuario)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get("/api/archivos/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_archivos_acepta_sesion_de_django(self):
        self.client.login(username=self.usuario.username, password=self.password)

        response = self.client.get("/api/archivos/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_archivo_privado_solo_es_visible_para_su_equipo(self):
        Archivo.objects.create(
            nombre_original="informe.txt",
            archivo=SimpleUploadedFile("informe.txt", b"contenido confidencial"),
            categoria="documento",
            usuario=self.usuario,
            proyecto=self.proyecto,
            **{"global": False},
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        response_usuario = self.client.get("/api/archivos/")
        self.assertEqual(response_usuario.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_usuario.data), 1)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.otro_usuario)}"
        )
        response_otro = self.client.get("/api/archivos/")
        self.assertEqual(response_otro.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_otro.data), 0)

    def test_archivo_global_es_visible_para_todos_los_equipos_del_proyecto(self):
        Archivo.objects.create(
            nombre_original="informe-global.txt",
            archivo=SimpleUploadedFile("informe-global.txt", b"contenido global"),
            categoria="documento",
            usuario=self.usuario,
            proyecto=self.proyecto,
            **{"global": True},
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.usuario)}"
        )
        response_usuario = self.client.get("/api/archivos/")
        self.assertEqual(response_usuario.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_usuario.data), 1)

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(self.otro_usuario)}"
        )
        response_otro = self.client.get("/api/archivos/")
        self.assertEqual(response_otro.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_otro.data), 1)

    def test_archivo_no_se_puede_crear_si_usuario_no_pertenece_al_proyecto(self):
        otro_proyecto = Proyecto.objects.create(nombre="Proyecto ajeno")
        usuario_ajeno = Usuario.objects.create_user(
            username="usuario_ajeno", password=self.password
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {self._obtener_token(usuario_ajeno)}"
        )

        response = self.client.post(
            "/api/archivos/",
            {
                "nombre_original": "ajeno.txt",
                "archivo": SimpleUploadedFile("ajeno.txt", b"contenido"),
                "categoria": "documento",
                "proyecto": otro_proyecto.id,
                "global": "false",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class IntegracionNexoTests(APITestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(username="nombre_real")
        self.otro_usuario = Usuario.objects.create_user(username="otro_equipo")
        self.ajeno = Usuario.objects.create_user(username="fuera_del_proyecto")
        self.equipo = Equipo.objects.create(nombre="Equipo propio")
        EquipoUsuario.objects.create(
            usuario=self.usuario, equipo=self.equipo, rol="analista"
        )
        self.otro_equipo = Equipo.objects.create(nombre="Equipo asociado")
        EquipoUsuario.objects.create(usuario=self.otro_usuario, equipo=self.otro_equipo)
        self.proyecto = Proyecto.objects.create(nombre="Proyecto compartido")
        self.proyecto.equipos.add(self.equipo, self.otro_equipo)
        self.archivo = Archivo.objects.create(
            nombre_original="informe.txt",
            archivo=SimpleUploadedFile("informe.txt", b"contenido exacto\x00\xff"),
            usuario=self.usuario,
            proyecto=self.proyecto,
        )
        self.download_url = f"/api/archivos/{self.archivo.pk}/download/"
        self.client.force_authenticate(self.usuario)

    def tearDown(self):
        for archivo in Archivo.objects.all():
            archivo.archivo.delete(save=False)
        super().tearDown()

    def test_me_devuelve_identidad_y_capacidades(self):
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "id": self.usuario.pk,
                "username": "nombre_real",
                "permissions": {"create_project": True, "create_group": True},
            },
        )
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/me/").status_code, 401)

    def test_capacidades_reflejan_membresia_sin_jerarquia_de_roles(self):
        response = self.client.get(f"/api/proyectos/{self.proyecto.pk}/")
        self.assertCountEqual(
            response.data["equipos"], [self.equipo.pk, self.otro_equipo.pk]
        )
        self.assertEqual(
            response.data["permissions"],
            {
                "edit": True,
                "delete": True,
                "manage_teams": True,
                "upload_files": True,
            },
        )
        teams = self.client.get(f"/api/proyectos/{self.proyecto.pk}/equipos/").data
        permissions = {team["id"]: team["permissions"] for team in teams}
        self.assertEqual(
            permissions[self.equipo.pk],
            {"edit": True, "delete": True, "manage_members": True},
        )
        self.assertEqual(
            permissions[self.otro_equipo.pk],
            {"edit": False, "delete": False, "manage_members": False},
        )
        projects = self.client.get(f"/api/equipos/{self.equipo.pk}/proyectos/").data
        self.assertTrue(projects[0]["permissions"]["edit"])
        file = self.client.get(f"/api/archivos/{self.archivo.pk}/").data
        self.assertEqual(
            file["permissions"], {"edit": True, "delete": True, "download": True}
        )

    def test_capacidades_sin_contexto_son_falsas(self):
        for serializer, instance in (
            (ProyectoSerializer, self.proyecto),
            (EquipoSerializer, self.equipo),
            (ArchivoSerializer, self.archivo),
        ):
            self.assertFalse(any(serializer(instance).data["permissions"].values()))

    def test_creacion_con_equipo_conserva_equipo_principal(self):
        response = self.client.post(
            "/api/proyectos/",
            {"nombre": "Nuevo", "equipo": self.equipo.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("equipo", response.data)
        project = Proyecto.objects.get(pk=response.data["id"])
        principal = project.equipos.exclude(pk=self.equipo.pk).get()
        self.assertEqual(principal.nombre, "Nuevo - Equipo principal")
        self.assertTrue(principal.usuarios.filter(pk=self.usuario.pk).exists())
        self.assertIsNone(
            EquipoUsuario.objects.get(equipo=principal, usuario=self.usuario).rol
        )
        self.assertCountEqual(response.data["equipos"], [self.equipo.pk, principal.pk])
        self.assertTrue(all(response.data["permissions"].values()))

    def test_creacion_sin_equipo_y_creacion_de_grupo(self):
        response = self.client.post(
            "/api/proyectos/", {"nombre": "Sin selección"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["equipos"]), 1)
        self.assertTrue(all(response.data["permissions"].values()))
        response = self.client.post(
            "/api/equipos/", {"nombre": "Nuevo grupo"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["usuarios"], [self.usuario.pk])
        self.assertTrue(all(response.data["permissions"].values()))
        self.assertEqual(
            EquipoUsuario.objects.get(
                equipo_id=response.data["id"], usuario=self.usuario
            ).rol,
            "owner",
        )

    def test_creacion_rechaza_equipo_ajeno_o_invalido_sin_escrituras(self):
        before = (
            Proyecto.objects.count(),
            Equipo.objects.count(),
            ProyectoEquipo.objects.count(),
        )
        for equipo in (self.otro_equipo.pk, 999999, "invalido", None):
            with self.subTest(equipo=equipo):
                response = self.client.post(
                    "/api/proyectos/",
                    {"nombre": "Inválido", "equipo": equipo},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("equipo", response.data)
                self.assertEqual(
                    before,
                    (
                        Proyecto.objects.count(),
                        Equipo.objects.count(),
                        ProyectoEquipo.objects.count(),
                    ),
                )

    def test_equipo_de_creacion_no_modifica_asociaciones_en_patch(self):
        response = self.client.patch(
            f"/api/proyectos/{self.proyecto.pk}/",
            {"equipo": self.equipo.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.proyecto.equipos.count(), 2)

    def test_creacion_de_proyecto_revierte_si_falla_asociacion_principal_o_opcional(
        self,
    ):
        before = (
            Proyecto.objects.count(),
            Equipo.objects.count(),
            EquipoUsuario.objects.count(),
            ProyectoEquipo.objects.count(),
        )
        create = ProyectoEquipo.objects.create
        for failure_at in (1, 2):
            calls = []

            def crear_relacion(**kwargs):
                calls.append(kwargs)
                if len(calls) == failure_at:
                    raise RuntimeError("Fallo simulado")
                return create(**kwargs)

            with self.subTest(failure_at=failure_at):
                with patch(
                    "proyectos.views.ProyectoEquipo.objects.create",
                    side_effect=crear_relacion,
                ):
                    with self.assertRaisesMessage(RuntimeError, "Fallo simulado"):
                        self.client.post(
                            "/api/proyectos/",
                            {"nombre": "Rollback", "equipo": self.equipo.pk},
                            format="json",
                        )
                self.assertEqual(
                    before,
                    (
                        Proyecto.objects.count(),
                        Equipo.objects.count(),
                        EquipoUsuario.objects.count(),
                        ProyectoEquipo.objects.count(),
                    ),
                )

    def test_creacion_de_grupo_revierte_si_falla_membresia(self):
        before = (Equipo.objects.count(), EquipoUsuario.objects.count())
        with patch(
            "proyectos.views.EquipoUsuario.objects.create",
            side_effect=RuntimeError("Fallo simulado"),
        ):
            with self.assertRaisesMessage(RuntimeError, "Fallo simulado"):
                self.client.post("/api/equipos/", {"nombre": "Rollback"}, format="json")
        self.assertEqual(
            before, (Equipo.objects.count(), EquipoUsuario.objects.count())
        )

    def test_descarga_exige_autenticacion_y_visibilidad(self):
        for usuario, expected in (
            (None, 401),
            (self.ajeno, 404),
            (self.otro_usuario, 404),
        ):
            with self.subTest(usuario=usuario):
                self.client.force_authenticate(usuario)
                self.assertEqual(
                    self.client.get(self.download_url).status_code, expected
                )

    def test_descarga_privada_compartida_y_global_entrega_bytes(self):
        shared = Usuario.objects.create_user(username="mismo_equipo")
        self.equipo.usuarios.add(shared)
        for usuario in (self.usuario, shared):
            self.client.force_authenticate(usuario)
            response = self.client.get(self.download_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response["Content-Disposition"], 'attachment; filename="informe.txt"'
            )
            self.assertEqual(
                b"".join(response.streaming_content), b"contenido exacto\x00\xff"
            )
        setattr(self.archivo, "global", True)
        self.archivo.save()
        self.client.force_authenticate(self.otro_usuario)
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b"".join(response.streaming_content), b"contenido exacto\x00\xff"
        )
        self.client.force_authenticate(self.ajeno)
        self.assertEqual(self.client.get(self.download_url).status_code, 404)

    def test_descarga_sanea_nombre_original(self):
        self.archivo.nombre_original = "../privado\\informe\r\n.txt"
        self.archivo.save()
        response = self.client.get(self.download_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="informe.txt"'
        )

    def test_descarga_archivo_faltante_devuelve_404(self):
        self.archivo.archivo.storage.delete(self.archivo.archivo.name)
        self.assertEqual(self.client.get(self.download_url).status_code, 404)
        self.archivo.archivo = ""
        self.archivo.save()
        self.assertEqual(self.client.get(self.download_url).status_code, 404)

    @override_settings(DEBUG=True)
    def test_media_directo_no_expone_bytes(self):
        url = self.archivo.archivo.url
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(self.usuario)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_nombre_maximo_de_proyecto_no_desborda_nombre_del_equipo(self):
        response = self.client.post(
            "/api/proyectos/", {"nombre": "a" * 255}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        project = Proyecto.objects.get(pk=response.data["id"])
        self.assertLessEqual(len(project.equipos.get().nombre), 255)

    def test_archivo_real_se_sube_modifica_reemplaza_y_elimina(self):
        response = self.client.post(
            "/api/archivos/",
            {
                "nombre_original": "nuevo.txt",
                "archivo": SimpleUploadedFile("nuevo.txt", b"primera entrega"),
                "proyecto": self.proyecto.pk,
                "categoria": "Ensayos",
                "global": "false",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        url = f"/api/archivos/{response.data['id']}/"
        self.assertTrue(response.data["permissions"]["edit"])
        self.assertEqual(response.data["usuario"], self.usuario.pk)
        response = self.client.patch(
            url, {"nombre_original": "final.txt", "global": True}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(
            url,
            {"archivo": SimpleUploadedFile("final.txt", b"segunda entrega")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200)
        self.client.force_authenticate(self.otro_usuario)
        response = self.client.get(f"{url}download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"segunda entrega")
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_retirar_membresia_revoca_lectura_edicion_borrado_y_descarga(self):
        shared = Usuario.objects.create_user(username="integrante_retirado")
        self.equipo.usuarios.add(shared)
        self.client.force_authenticate(shared)
        url = f"/api/archivos/{self.archivo.pk}/"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_authenticate(self.usuario)
        response = self.client.delete(
            f"/api/equipos/{self.equipo.pk}/miembros/{shared.pk}/"
        )
        self.assertEqual(response.status_code, 204)
        self.client.force_authenticate(shared)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(f"{url}download/").status_code, 404)
        self.assertEqual(
            self.client.patch(url, {"categoria": "cambio"}, format="json").status_code,
            404,
        )
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(Archivo.objects.filter(pk=self.archivo.pk).exists())

    def test_proyecto_y_grupo_se_editan_y_eliminan_solo_con_acceso(self):
        project_url = f"/api/proyectos/{self.proyecto.pk}/"
        group_url = f"/api/equipos/{self.equipo.pk}/"
        self.client.force_authenticate(self.ajeno)
        for url in (project_url, group_url):
            self.assertEqual(
                self.client.patch(url, {"nombre": "ajeno"}, format="json").status_code,
                404,
            )
            self.assertEqual(self.client.delete(url).status_code, 404)
        self.client.force_authenticate(self.usuario)
        for url in (project_url, group_url):
            response = self.client.patch(url, {"nombre": "Actualizado"}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["nombre"], "Actualizado")
        self.assertEqual(self.client.delete(project_url).status_code, 204)
        self.assertFalse(Archivo.objects.filter(pk=self.archivo.pk).exists())
        self.assertEqual(self.client.delete(group_url).status_code, 204)
