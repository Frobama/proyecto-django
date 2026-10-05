import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def audit_recurrence_and_backfill_codes(apps, schema_editor):
    Group = apps.get_model('proyectos', 'Equipo')
    groups = Group.objects.using(schema_editor.connection.alias)
    invalid = list(groups.filter(equipo_recurrente=False).annotate(
        project_count=models.Count('proyectos')).filter(project_count__gt=1).values_list('pk', flat=True))
    if invalid:
        raise RuntimeError(f'Equipos no recurrentes con varios proyectos: {invalid}. '
                           'Requieren una decisión explícita antes de aplicar 0005.')
    for pk in groups.order_by('pk').values_list('pk', flat=True).iterator():
        groups.filter(pk=pk).update(codigo=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [('proyectos', '0004_enforce_contextual_roles')]

    operations = [
        migrations.AddField(model_name='equipo', name='codigo', field=models.UUIDField(null=True, editable=False)),
        migrations.RunPython(audit_recurrence_and_backfill_codes, migrations.RunPython.noop),
        migrations.AlterField(model_name='equipo', name='codigo',
                              field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
        migrations.CreateModel(
            name='ShareRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rol', models.CharField(choices=[('admin', 'Administrador'), ('editor', 'Editor'), ('reader', 'Lector')],
                                         default='reader', max_length=100)),
                ('estado', models.CharField(choices=[('pending', 'Pendiente'), ('accepted', 'Aceptada'),
                                                     ('rejected', 'Rechazada'), ('cancelled', 'Cancelada')],
                                            default='pending', max_length=20)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('equipo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                             related_name='solicitudes', to='proyectos.equipo')),
                ('proyecto', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                               related_name='solicitudes', to='proyectos.proyecto')),
                ('solicitado_por', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                    to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('proyecto', 'equipo'), condition=models.Q(estado='pending'),
                                        name='unique_pending_share_request'),
                models.CheckConstraint(check=models.Q(rol__in=['admin', 'editor', 'reader']), name='share_request_role_valid'),
                models.CheckConstraint(check=models.Q(estado__in=['pending', 'accepted', 'rejected', 'cancelled']),
                                       name='share_request_state_valid'),
            ]},
        ),
    ]
