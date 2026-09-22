from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Archivo
from .serializers import ArchivoSerializer
from .permissions import EsMiembroDelEquipo

class ArchivoViewSet(viewsets.ModelViewSet):
    serializer_class = ArchivoSerializer
    permission_classes = [IsAuthenticated, EsMiembroDelEquipo]

    def get_queryset(self):
        return Archivo.objects.filter(
            seccion__proyecto__equipos__usuarios=self.request.user
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(usuario=self.request.user)