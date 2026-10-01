from rest_framework import serializers
from .models import Archivo, Proyecto, Equipo, EquipoUsuario, ProyectoEquipo, Usuario
from .permissions import usuario_tiene_acceso_al_archivo


def equipos_del_usuario(context):
    if "equipos_del_usuario" not in context:
        request = context.get("request")
        usuario = request.user if request else None
        context["equipos_del_usuario"] = (
            set(usuario.equipos.values_list("id", flat=True))
            if usuario and usuario.is_authenticated
            else set()
        )
    return context["equipos_del_usuario"]


class RegistroSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Usuario
        fields = ["username", "email", "password"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        usuario = Usuario(**validated_data)
        usuario.set_password(password)
        usuario.save()
        return usuario


class ProyectoSerializer(serializers.ModelSerializer):
    equipos = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    equipo = serializers.PrimaryKeyRelatedField(
        queryset=Equipo.objects.all(), write_only=True, required=False
    )
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Proyecto
        fields = ["id", "nombre", "descripcion", "equipos", "equipo", "permissions"]

    def validate_equipo(self, value):
        if self.instance is not None:
            raise serializers.ValidationError(
                "Usa el endpoint de equipos para modificar las asociaciones."
            )
        if value.pk not in equipos_del_usuario(self.context):
            raise serializers.ValidationError(
                "El usuario debe pertenecer al equipo seleccionado."
            )
        return value

    def get_permissions(self, obj):
        permitido = any(
            equipo.pk in equipos_del_usuario(self.context)
            for equipo in obj.equipos.all()
        )
        return dict.fromkeys(
            ["edit", "delete", "manage_teams", "upload_files"], permitido
        )


class EquipoSerializer(serializers.ModelSerializer):
    usuarios = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Equipo
        fields = ["id", "nombre", "equipo_recurrente", "usuarios", "permissions"]

    def get_permissions(self, obj):
        return dict.fromkeys(
            ["edit", "delete", "manage_members"],
            obj.pk in equipos_del_usuario(self.context),
        )


class ProyectoEquipoSerializer(serializers.ModelSerializer):
    equipo = serializers.PrimaryKeyRelatedField(queryset=Equipo.objects.all())

    class Meta:
        model = ProyectoEquipo
        fields = ["equipo", "objetivo", "descripcion"]

    def validate(self, attrs):
        proyecto = self.context.get("proyecto")
        equipo = attrs.get("equipo")
        if proyecto and equipo and proyecto.equipos.filter(id=equipo.id).exists():
            raise serializers.ValidationError(
                {"equipo": "Ese equipo ya está asociado a este proyecto."}
            )
        return attrs

    def create(self, validated_data):
        proyecto = self.context["proyecto"]
        return ProyectoEquipo.objects.create(proyecto=proyecto, **validated_data)


class EquipoUsuarioSerializer(serializers.ModelSerializer):
    usuario = serializers.PrimaryKeyRelatedField(queryset=Usuario.objects.all())

    class Meta:
        model = EquipoUsuario
        fields = ["usuario", "rol"]

    def validate_usuario(self, value):
        equipo = self.context.get("equipo")
        if equipo and equipo.usuarios.filter(id=value.id).exists():
            raise serializers.ValidationError("Ese usuario ya pertenece al equipo.")
        return value

    def create(self, validated_data):
        equipo = self.context["equipo"]
        return EquipoUsuario.objects.create(equipo=equipo, **validated_data)


class ArchivoSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Archivo
        fields = [
            "id",
            "nombre_original",
            "archivo",
            "categoria",
            "usuario",
            "proyecto",
            "fecha_subido",
            "global",
            "permissions",
        ]
        read_only_fields = ["usuario", "fecha_subido"]

    def get_permissions(self, obj):
        request = self.context.get("request")
        permitido = usuario_tiene_acceso_al_archivo(
            request.user if request else None, obj
        )
        return dict.fromkeys(["edit", "delete", "download"], permitido)

    def validate(self, attrs):
        proyecto = attrs.get("proyecto")
        request = self.context.get("request")
        usuario = request.user if request else None

        if (
            proyecto
            and usuario
            and not proyecto.equipos.filter(usuarios=usuario).exists()
        ):
            raise serializers.ValidationError(
                {
                    "proyecto": "El usuario debe pertenecer a un equipo del proyecto para subir archivos."
                }
            )

        return attrs
