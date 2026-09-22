from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ArchivoViewSet

router = DefaultRouter()
router.register(r'archivos', ArchivoViewSet, basename='archivo')

urlpatterns = [
    path('', include(router.urls)),
]