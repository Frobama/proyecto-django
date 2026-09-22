from rest_framework import serializers
from .models import Archivo, Seccion, Proyecto, Equipo

class ArchivoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Archivo
        fields = ['id', 'nombre_original', 'archivo', 'categoria', 'usuario', 'seccion', 'fecha_subido']
        read_only_fields = ['usuario', 'fecha_subido']