from rest_framework import generics, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .models import Archivo, Equipo, EquipoUsuario, Proyecto, ProyectoEquipo
from .serializers import (
    ArchivoSerializer,
    EquipoSerializer,
    EquipoUsuarioSerializer,
    ProyectoEquipoSerializer,
    ProyectoSerializer,
    RegistroSerializer,
)
from .permissions import EsMiembroDelEquipo, usuario_tiene_acceso_al_archivo


class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Proyecto.objects.filter(equipos__usuarios=self.request.user).distinct()

    def perform_create(self, serializer):
        proyecto = serializer.save()
        equipo = Equipo.objects.create(nombre=f'{proyecto.nombre} - Equipo principal')
        equipo.usuarios.add(self.request.user)
        ProyectoEquipo.objects.create(proyecto=proyecto, equipo=equipo, objetivo='Equipo principal del proyecto')

    @action(detail=True, methods=['get', 'post'], url_path='equipos')
    def equipos(self, request, pk=None):
        proyecto = self.get_object()

        if request.method == 'GET':
            equipos = proyecto.equipos.all().distinct()
            return Response(EquipoSerializer(equipos, many=True).data)

        if not proyecto.equipos.filter(usuarios=request.user).exists():
            return Response({'detail': 'No tienes permiso para asociar equipos a este proyecto.'}, status=403)

        serializer = ProyectoEquipoSerializer(data=request.data, context={'proyecto': proyecto})
        serializer.is_valid(raise_exception=True)
        relacion = serializer.save()
        return Response({
            'equipo': relacion.equipo.id,
            'objetivo': relacion.objetivo,
            'descripcion': relacion.descripcion,
            'proyecto': proyecto.id,
        }, status=201)

    @action(detail=True, methods=['delete'], url_path='equipos/(?P<equipo_pk>[^/.]+)')
    def eliminar_equipo(self, request, pk=None, equipo_pk=None):
        proyecto = self.get_object()

        if not proyecto.equipos.filter(usuarios=request.user).exists():
            return Response({'detail': 'No tienes permiso para quitar equipos de este proyecto.'}, status=403)

        proyecto.equipos.through.objects.filter(proyecto_id=proyecto.id, equipo_id=equipo_pk).delete()
        return Response(status=204)


class EquipoViewSet(viewsets.ModelViewSet):
    serializer_class = EquipoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Equipo.objects.filter(usuarios=self.request.user).distinct()

    def perform_create(self, serializer):
        equipo = serializer.save()
        EquipoUsuario.objects.create(usuario=self.request.user, equipo=equipo, rol='owner')

    @action(detail=True, methods=['get'], url_path='proyectos')
    def proyectos(self, request, pk=None):
        equipo = self.get_object()
        if not equipo.usuarios.filter(id=request.user.id).exists():
            return Response({'detail': 'No tienes permiso para ver los proyectos de este equipo.'}, status=403)

        proyectos = equipo.proyectos.all().distinct()
        return Response(ProyectoSerializer(proyectos, many=True).data)

    @action(detail=True, methods=['get', 'post'], url_path='miembros')
    def miembros(self, request, pk=None):
        equipo = Equipo.objects.filter(pk=pk).first()
        if equipo is None:
            return Response({'detail': 'Equipo no encontrado.'}, status=404)

        if not equipo.usuarios.filter(id=request.user.id).exists():
            return Response({'detail': 'No tienes permiso para gestionar miembros de este equipo.'}, status=403)

        if request.method == 'GET':
            members = []
            for relacion in EquipoUsuario.objects.filter(equipo=equipo).select_related('usuario'):
                members.append({
                    'usuario': relacion.usuario.id,
                    'username': relacion.usuario.username,
                    'rol': relacion.rol,
                })
            return Response(members)

        serializer = EquipoUsuarioSerializer(data=request.data, context={'equipo': equipo})
        serializer.is_valid(raise_exception=True)
        instancia = serializer.save()
        return Response({
            'usuario': instancia.usuario.id,
            'rol': instancia.rol,
            'equipo': equipo.id,
        }, status=201)

    @action(detail=True, methods=['delete'], url_path='miembros/(?P<usuario_pk>[^/.]+)')
    def eliminar_miembro(self, request, pk=None, usuario_pk=None):
        equipo = Equipo.objects.filter(pk=pk).first()
        if equipo is None:
            return Response({'detail': 'Equipo no encontrado.'}, status=404)

        if not equipo.usuarios.filter(id=request.user.id).exists():
            return Response({'detail': 'No tienes permiso para gestionar miembros de este equipo.'}, status=403)

        relacion = EquipoUsuario.objects.filter(equipo=equipo, usuario_id=usuario_pk).first()
        if relacion is None:
            return Response({'detail': 'Ese usuario no pertenece a este equipo.'}, status=404)

        relacion.delete()
        return Response(status=204)


class ArchivoViewSet(viewsets.ModelViewSet):
    serializer_class = ArchivoSerializer
    permission_classes = [IsAuthenticated, EsMiembroDelEquipo]

    def get_queryset(self):
        queryset = Archivo.objects.filter(
            proyecto__equipos__usuarios=self.request.user
        ).distinct()

        ids_visibles = [
            archivo.id
            for archivo in queryset
            if usuario_tiene_acceso_al_archivo(self.request.user, archivo)
        ]

        return Archivo.objects.filter(id__in=ids_visibles).distinct()

    def perform_create(self, serializer):
        serializer.save(usuario=self.request.user)


class RegistroView(generics.CreateAPIView):
    serializer_class = RegistroSerializer
    permission_classes = [AllowAny]