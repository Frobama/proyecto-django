from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from .models import Archivo, Proyecto, Equipo, ProyectoEquipo, ShareRequest, Usuario, Role
from .permissions import file_permissions, group_role, project_role
from .services import max_file_size, share_request_permissions


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
    role = serializers.SerializerMethodField()
    equipo_rol = serializers.ChoiceField(choices=Role.choices, default=Role.ADMIN, write_only=True)

    class Meta:
        model = Proyecto
        fields = ["id", "nombre", "descripcion", "equipos", "equipo", "equipo_rol", "role", "permissions"]

    def validate_equipo(self, value):
        if self.instance is not None:
            raise serializers.ValidationError(
                "Usa el endpoint de equipos para modificar las asociaciones."
            )
        if group_role(self.context['request'].user, value) != Role.ADMIN:
            raise serializers.ValidationError(
                "El usuario debe administrar el equipo seleccionado."
            )
        return value

    def validate(self, attrs):
        if self.instance is not None and ('equipo' in attrs or 'equipo_rol' in self.initial_data):
            raise serializers.ValidationError('Usa el endpoint de equipos para modificar las asociaciones.')
        if self.instance is not None:
            attrs.pop('equipo_rol', None)
        elif 'equipo' in attrs and attrs.get('equipo_rol') != Role.ADMIN:
            raise serializers.ValidationError({'equipo_rol': 'El equipo principal requiere una concesión admin.'})
        return attrs

    @extend_schema_field(serializers.ChoiceField(choices=Role.values, allow_null=True))
    def get_role(self, obj):
        request = self.context.get('request')
        return project_role(request.user if request else None, obj)

    @extend_schema_field(serializers.DictField(child=serializers.BooleanField()))
    def get_permissions(self, obj) -> dict[str, bool]:
        role = self.get_role(obj)
        return {'edit': role == Role.ADMIN, 'delete': role == Role.ADMIN,
                'manage_teams': role == Role.ADMIN, 'upload_files': role in (Role.ADMIN, Role.EDITOR)}


class EquipoSerializer(serializers.ModelSerializer):
    usuarios = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    proyectos = serializers.SerializerMethodField()
    codigo = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = Equipo
        fields = ["id", "nombre", "equipo_recurrente", "usuarios", "proyectos", "codigo", "role", "permissions"]

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_codigo(self, obj):
        return str(obj.codigo) if self.get_role(obj) == Role.ADMIN else None

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_proyectos(self, obj):
        request = self.context.get('request')
        user = request.user if request else None
        return [project.pk for project in obj.proyectos.all() if project_role(user, project)]

    @extend_schema_field(serializers.ChoiceField(choices=Role.values, allow_null=True))
    def get_role(self, obj):
        request = self.context.get('request')
        return group_role(request.user if request else None, obj)

    @extend_schema_field(serializers.DictField(child=serializers.BooleanField()))
    def get_permissions(self, obj) -> dict[str, bool]:
        return dict.fromkeys(
            ["edit", "delete", "manage_members"],
            self.get_role(obj) == Role.ADMIN,
        )


class ProyectoEquipoSerializer(serializers.ModelSerializer):
    equipo = serializers.PrimaryKeyRelatedField(queryset=Equipo.objects.all())
    rol = serializers.ChoiceField(choices=Role.choices, default=Role.READER)

    class Meta:
        model = ProyectoEquipo
        fields = ["equipo", "rol", "objetivo", "descripcion"]

    def validate(self, attrs):
        proyecto = self.context.get("proyecto")
        equipo = attrs.get("equipo")
        if proyecto and equipo and proyecto.equipos.filter(id=equipo.id).exists():
            raise serializers.ValidationError(
                {"equipo": "Ese equipo ya está asociado a este proyecto."}
            )
        return attrs

class EquipoUsuarioSerializer(serializers.Serializer):
    usuario = serializers.IntegerField(required=False, min_value=1)
    username = serializers.CharField(required=False, trim_whitespace=False)
    rol = serializers.ChoiceField(choices=Role.choices, default=Role.READER)

    def validate(self, attrs):
        if ('usuario' in attrs) == ('username' in attrs):
            raise serializers.ValidationError('Indica username exacto o usuario, pero no ambos.')
        return attrs


class RoleUpdateSerializer(serializers.Serializer):
    rol = serializers.ChoiceField(choices=Role.choices)

    def validate(self, attrs):
        if set(self.initial_data) != {'rol'}:
            raise serializers.ValidationError('Indica únicamente el rol.')
        return attrs


class ResourceNameSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    nombre = serializers.CharField(read_only=True)


class RequesterSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)


class ShareRequestSerializer(serializers.ModelSerializer):
    proyecto = ResourceNameSerializer(read_only=True)
    equipo = ResourceNameSerializer(read_only=True)
    solicitado_por = RequesterSerializer(read_only=True, allow_null=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = ShareRequest
        fields = ['id', 'proyecto', 'equipo', 'solicitado_por', 'rol', 'estado', 'permissions']
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(child=serializers.BooleanField()))
    def get_permissions(self, obj) -> dict[str, bool]:
        request = self.context.get('request')
        return share_request_permissions(request.user if request else None, obj)


class ShareRequestCreateSerializer(serializers.Serializer):
    codigo = serializers.UUIDField()
    rol = serializers.ChoiceField(choices=Role.choices, default=Role.READER)


class ShareRequestAnswerSerializer(serializers.Serializer):
    accion = serializers.ChoiceField(choices=['accept', 'reject'])

    def validate(self, attrs):
        if set(self.initial_data) != {'accion'}:
            raise serializers.ValidationError('Indica únicamente la acción.')
        return attrs


class ExactCandidateQuerySerializer(serializers.Serializer):
    username = serializers.CharField(trim_whitespace=False, max_length=150)


class ExactCandidateSerializer(serializers.Serializer):
    usuario = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    ya_es_miembro = serializers.BooleanField(read_only=True)


class GroupDeletionSerializer(serializers.Serializer):
    proyectos = ResourceNameSerializer(many=True, read_only=True)
    permitido = serializers.BooleanField(read_only=True)
    motivos = serializers.ListField(child=serializers.CharField(), read_only=True)


class ArchivoSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    equipos_visibles = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    usuario_nombre = serializers.CharField(source='usuario.username', read_only=True)
    equipos_visibles_detalle = serializers.SerializerMethodField()

    class Meta:
        model = Archivo
        fields = [
            "id",
            "nombre_original",
            "archivo",
            "categoria",
            "usuario",
            "usuario_nombre",
            "proyecto",
            "fecha_subido",
            "global",
            "equipos_visibles",
            "equipos_visibles_detalle",
            "permissions",
        ]
        read_only_fields = ["usuario", "fecha_subido"]

    @extend_schema_field(ResourceNameSerializer(many=True))
    def get_equipos_visibles_detalle(self, obj):
        return list(obj.equipos_visibles.filter(proyectos=obj.proyecto).order_by('pk').values('id', 'nombre'))

    @extend_schema_field(serializers.DictField(child=serializers.BooleanField()))
    def get_permissions(self, obj) -> dict[str, bool]:
        request = self.context.get("request")
        return file_permissions(request.user if request else None, obj)

    def validate_archivo(self, value):
        if value.size > max_file_size():
            raise serializers.ValidationError(f'El tamaño máximo es {max_file_size()} bytes.')
        return value

    def validate(self, attrs):
        proyecto = attrs.get("proyecto")
        request = self.context.get("request")
        usuario = request.user if request else None

        if self.instance is not None and proyecto and proyecto.pk != self.instance.proyecto_id:
            raise serializers.ValidationError({'proyecto': 'No se puede cambiar el proyecto de un archivo.'})
        if self.instance is None and proyecto and project_role(usuario, proyecto) not in (Role.ADMIN, Role.EDITOR):
            raise serializers.ValidationError(
                {
                    "proyecto": "El usuario debe pertenecer a un equipo del proyecto para subir archivos."
                }
            )

        return attrs
