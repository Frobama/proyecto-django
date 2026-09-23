from rest_framework import generics, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from .models import Archivo
from .serializers import ArchivoSerializer, RegistroSerializer
from .permissions import EsMiembroDelEquipo

class ArchivoViewSet(viewsets.ModelViewSet):
    serializer_class = ArchivoSerializer
    permission_classes = [IsAuthenticated, EsMiembroDelEquipo]

    def get_queryset(self):
        return Archivo.objects.filter(
            proyecto__equipos__usuarios=self.request.user
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(usuario=self.request.user)


class RegistroView(generics.CreateAPIView):
    serializer_class = RegistroSerializer
    permission_classes = [AllowAny]