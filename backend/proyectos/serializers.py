from rest_framework import serializers
from .models import Archivo, Proyecto, Equipo, Usuario


class RegistroSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Usuario
        fields = ['username', 'email', 'password']

    def create(self, validated_data):
        password = validated_data.pop('password')
        usuario = Usuario(**validated_data)
        usuario.set_password(password)
        usuario.save()
        return usuario

class ArchivoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Archivo
        fields = ['id', 'nombre_original', 'archivo', 'categoria', 'usuario', 'proyecto', 'fecha_subido', 'global']
        read_only_fields = ['usuario', 'fecha_subido']