from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ArchivoViewSet, EquipoViewSet, ProyectoViewSet, RegistroView

router = DefaultRouter()
router.register(r'proyectos', ProyectoViewSet, basename='proyecto')
router.register(r'equipos', EquipoViewSet, basename='equipo')
router.register(r'archivos', ArchivoViewSet, basename='archivo')

urlpatterns = [
    path('', include(router.urls)),
    path('registro/', RegistroView.as_view(), name='registro'),
]