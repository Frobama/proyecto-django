from pathlib import PurePosixPath

from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo, RequestState, Role, ShareRequest
from .serializers import (
    ArchivoSerializer, EquipoSerializer, EquipoUsuarioSerializer,
    ProyectoEquipoSerializer, ProyectoSerializer, RegistroSerializer, RoleUpdateSerializer,
    ExactCandidateQuerySerializer, ExactCandidateSerializer, GroupDeletionSerializer,
    ShareRequestAnswerSerializer, ShareRequestCreateSerializer, ShareRequestSerializer,
)
from .permissions import EsMiembroDelEquipo, group_role, project_role, usuario_tiene_acceso_al_archivo


class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer
    permission_classes = [IsAuthenticated]
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        candidates = Proyecto.objects.filter(equipos__usuarios=self.request.user).distinct()
        ids = [project.pk for project in candidates if project_role(self.request.user, project)]
        return Proyecto.objects.filter(pk__in=ids).prefetch_related('equipos')

    def perform_create(self, serializer):
        serializer.instance = services.create_project(self.request.user, serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_project(self.request.user, serializer.instance.pk, serializer.validated_data)

    def perform_destroy(self, instance):
        services.update_project(self.request.user, instance.pk, {}, delete=True)

    @action(detail=True, methods=['get', 'post'], url_path='equipos')
    def equipos(self, request, pk=None):
        project = self.get_object()
        if request.method == 'GET':
            result = []
            for grant in ProyectoEquipo.objects.filter(proyecto=project).select_related('equipo'):
                item = EquipoSerializer(grant.equipo, context=self.get_serializer_context()).data
                item['project_role'] = grant.rol
                result.append(item)
            return Response(result)
        services.require_admin(project_role(request.user, project))
        serializer = ProyectoEquipoSerializer(data=request.data, context={'proyecto': project})
        serializer.is_valid(raise_exception=True)
        grant = services.add_team(request.user, project.pk, serializer.validated_data)
        return Response({'equipo': grant.equipo_id, 'proyecto': project.pk, 'rol': grant.rol,
                         'objetivo': grant.objetivo, 'descripcion': grant.descripcion}, status=201)

    @extend_schema(request=RoleUpdateSerializer)
    @action(detail=True, methods=['patch', 'delete'], url_path=r'equipos/(?P<equipo_pk>\d+)')
    def eliminar_equipo(self, request, pk=None, equipo_pk=None):
        project = self.get_object()
        if request.method == 'DELETE':
            services.change_team(request.user, project.pk, equipo_pk, delete=True)
            return Response(status=204)
        services.require_admin(project_role(request.user, project))
        serializer = RoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant = services.change_team(request.user, project.pk, equipo_pk, serializer.validated_data['rol'])
        return Response({'equipo': grant.equipo_id, 'proyecto': project.pk, 'rol': grant.rol,
                          'objetivo': grant.objetivo, 'descripcion': grant.descripcion})

    @extend_schema(methods=['GET'], responses=ShareRequestSerializer(many=True))
    @extend_schema(methods=['POST'], request=ShareRequestCreateSerializer, responses={201: ShareRequestSerializer})
    @action(detail=True, methods=['get', 'post'], url_path='solicitudes')
    def solicitudes(self, request, pk=None):
        project = self.get_object()
        services.require_admin(project_role(request.user, project))
        if request.method == 'GET':
            requests = ShareRequest.objects.filter(proyecto=project).select_related(
                'proyecto', 'equipo', 'solicitado_por').order_by('pk')
            return Response(ShareRequestSerializer(requests, many=True, context=self.get_serializer_context()).data)
        serializer = ShareRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.create_share_request(request.user, project.pk, serializer.validated_data)
        return Response(ShareRequestSerializer(result, context=self.get_serializer_context()).data, status=201)

    @extend_schema(responses={204: None})
    @action(detail=True, methods=['delete'], url_path=r'solicitudes/(?P<solicitud_pk>\d+)')
    def cancelar_solicitud(self, request, pk=None, solicitud_pk=None):
        project = self.get_object()
        services.cancel_share_request(request.user, project.pk, solicitud_pk)
        return Response(status=204)


def member_data(actor, member):
    admin = group_role(actor, member.equipo) == Role.ADMIN
    return {'usuario': member.usuario_id, 'username': member.usuario.username, 'rol': member.rol,
            'permissions': {'change_role': admin, 'remove': admin or actor.pk == member.usuario_id}}


class EquipoViewSet(viewsets.ModelViewSet):
    serializer_class = EquipoSerializer
    permission_classes = [IsAuthenticated]
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        if not self.request.user.is_active:
            return Equipo.objects.none()
        return Equipo.objects.filter(equipousuario__usuario=self.request.user,
                                     equipousuario__rol__in=Role.values).distinct().prefetch_related('usuarios')

    def perform_create(self, serializer):
        serializer.instance = services.create_group(self.request.user, serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_group(self.request.user, serializer.instance.pk, serializer.validated_data)

    def perform_destroy(self, instance):
        services.update_group(self.request.user, instance.pk, {}, delete=True)

    @extend_schema(responses=GroupDeletionSerializer)
    @action(detail=True, methods=['get'], url_path='eliminacion')
    def eliminacion(self, request, pk=None):
        group = self.get_object()
        return Response(services.group_deletion_preview(request.user, group.pk))

    @extend_schema(parameters=[ExactCandidateQuerySerializer], responses=ExactCandidateSerializer)
    @action(detail=True, methods=['get'], url_path='candidato')
    def candidato(self, request, pk=None):
        group = self.member_group(request, pk)
        services.require_admin(group_role(request.user, group))
        serializer = ExactCandidateQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(services.exact_user_candidate(request.user, group.pk, serializer.validated_data['username']))

    @extend_schema(responses=ShareRequestSerializer(many=True))
    @action(detail=True, methods=['get'], url_path='solicitudes')
    def solicitudes(self, request, pk=None):
        group = self.member_group(request, pk)
        services.require_admin(group_role(request.user, group))
        requests = ShareRequest.objects.filter(equipo=group, estado=RequestState.PENDING).select_related(
            'proyecto', 'equipo', 'solicitado_por').order_by('pk')
        return Response(ShareRequestSerializer(requests, many=True, context=self.get_serializer_context()).data)

    @extend_schema(request=ShareRequestAnswerSerializer, responses=ShareRequestSerializer)
    @action(detail=True, methods=['post'], url_path=r'solicitudes/(?P<solicitud_pk>\d+)')
    def responder_solicitud(self, request, pk=None, solicitud_pk=None):
        group = self.member_group(request, pk)
        services.require_admin(group_role(request.user, group))
        serializer = ShareRequestAnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.answer_share_request(request.user, group.pk, solicitud_pk, serializer.validated_data['accion'])
        return Response(ShareRequestSerializer(result, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['get'], url_path='proyectos')
    def proyectos(self, request, pk=None):
        group = self.get_object()
        projects = [project for project in group.proyectos.all() if project_role(request.user, project)]
        return Response(ProyectoSerializer(projects, many=True, context=self.get_serializer_context()).data)

    def member_group(self, request, pk):
        group = Equipo.objects.filter(pk=pk).first()
        if group is None:
            raise Http404('Equipo no encontrado.')
        services.require_group_member(request.user, group)
        return group

    @extend_schema(request=EquipoUsuarioSerializer)
    @action(detail=True, methods=['get', 'post'], url_path='miembros')
    def miembros(self, request, pk=None):
        group = self.member_group(request, pk)
        if request.method == 'GET':
            members = EquipoUsuario.objects.filter(equipo=group).select_related('usuario', 'equipo')
            return Response([member_data(request.user, member) for member in members])
        services.require_admin(group_role(request.user, group))
        serializer = EquipoUsuarioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.add_member(request.user, group.pk, serializer.validated_data)
        return Response(member_data(request.user, member), status=201)

    @extend_schema(request=RoleUpdateSerializer)
    @action(detail=True, methods=['patch', 'delete'], url_path=r'miembros/(?P<usuario_pk>\d+)')
    def eliminar_miembro(self, request, pk=None, usuario_pk=None):
        group = self.member_group(request, pk)
        if request.method == 'DELETE':
            services.change_member(request.user, group.pk, usuario_pk, delete=True)
            return Response(status=204)
        services.require_admin(group_role(request.user, group))
        serializer = RoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.change_member(request.user, group.pk, usuario_pk, serializer.validated_data['rol'])
        return Response(member_data(request.user, member))


class ArchivoViewSet(viewsets.ModelViewSet):
    serializer_class = ArchivoSerializer
    permission_classes = [IsAuthenticated, EsMiembroDelEquipo]
    lookup_value_regex = r'\d+'

    def get_queryset(self):
        candidates = Archivo.objects.filter(proyecto__equipos__usuarios=self.request.user).distinct()
        ids = [file.pk for file in candidates if usuario_tiene_acceso_al_archivo(self.request.user, file)]
        return Archivo.objects.filter(pk__in=ids).prefetch_related('equipos_visibles')

    def perform_create(self, serializer):
        serializer.instance = services.save_file(self.request.user, serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.save_file(self.request.user, serializer.validated_data, serializer.instance.pk)

    def perform_destroy(self, instance):
        services.delete_file(self.request.user, instance.pk)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        file = self.get_object()
        if not file.archivo:
            raise Http404('Archivo no encontrado.')
        try:
            contents = file.archivo.open('rb')
        except FileNotFoundError:
            raise Http404('Archivo no encontrado.')
        name = PurePosixPath(file.nombre_original.replace('\\', '/')).name
        name = ''.join(character for character in name if character.isprintable())
        if name in ('', '.', '..'):
            name = 'archivo'
        return FileResponse(contents, as_attachment=True, filename=name)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=inline_serializer(name='MeResponse', fields={
        'id': serializers.IntegerField(), 'username': serializers.CharField(),
        'permissions': inline_serializer(name='MePermissions', fields={
            'create_project': serializers.BooleanField(), 'create_group': serializers.BooleanField(),
        }),
        'limits': inline_serializer(name='MeLimits', fields={'max_file_size': serializers.IntegerField()}),
    }))
    def get(self, request):
        return Response({'id': request.user.id, 'username': request.user.username,
                         'permissions': {'create_project': True, 'create_group': True},
                         'limits': {'max_file_size': services.max_file_size()}})


class RegistroView(generics.CreateAPIView):
    serializer_class = RegistroSerializer
    permission_classes = [AllowAny]
