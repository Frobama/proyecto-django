from rest_framework import serializers
from .models import Archivo, Proyecto, Equipo, EquipoUsuario, ProyectoEquipo, Usuario


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


class ProyectoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proyecto
        fields = ['id', 'nombre', 'descripcion']


class EquipoSerializer(serializers.ModelSerializer):
    usuarios = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = Equipo
        fields = ['id', 'nombre', 'equipo_recurrente', 'usuarios']


class ProyectoEquipoSerializer(serializers.ModelSerializer):
    equipo = serializers.PrimaryKeyRelatedField(queryset=Equipo.objects.all())

    class Meta:
        model = ProyectoEquipo
        fields = ['equipo', 'objetivo', 'descripcion']

    def validate(self, attrs):
        proyecto = self.context.get('proyecto')
        equipo = attrs.get('equipo')
        if proyecto and equipo and proyecto.equipos.filter(id=equipo.id).exists():
            raise serializers.ValidationError({'equipo': 'Ese equipo ya está asociado a este proyecto.'})
        return attrs

    def create(self, validated_data):
        proyecto = self.context['proyecto']
        return ProyectoEquipo.objects.create(proyecto=proyecto, **validated_data)


class EquipoUsuarioSerializer(serializers.ModelSerializer):
    usuario = serializers.PrimaryKeyRelatedField(queryset=Usuario.objects.all())

    class Meta:
        model = EquipoUsuario
        fields = ['usuario', 'rol']

    def validate_usuario(self, value):
        equipo = self.context.get('equipo')
        if equipo and equipo.usuarios.filter(id=value.id).exists():
            raise serializers.ValidationError('Ese usuario ya pertenece al equipo.')
        return value

    def create(self, validated_data):
        equipo = self.context['equipo']
        return EquipoUsuario.objects.create(equipo=equipo, **validated_data)


class ArchivoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Archivo
        fields = ['id', 'nombre_original', 'archivo', 'categoria', 'usuario', 'proyecto', 'fecha_subido', 'global']
        read_only_fields = ['usuario', 'fecha_subido']

    def validate(self, attrs):
        proyecto = attrs.get('proyecto')
        request = self.context.get('request')
        usuario = request.user if request else None

        if proyecto and usuario and not proyecto.equipos.filter(usuarios=usuario).exists():
            raise serializers.ValidationError({
                'proyecto': 'El usuario debe pertenecer a un equipo del proyecto para subir archivos.'
            })

        return attrs