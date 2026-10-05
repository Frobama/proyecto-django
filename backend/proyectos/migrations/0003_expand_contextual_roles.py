from django.db import migrations, models


def preserve_roles(apps, schema_editor):
    Membership = apps.get_model('proyectos', 'EquipoUsuario')
    Membership.objects.using(schema_editor.connection.alias).update(legacy_rol=models.F('rol'))


class Migration(migrations.Migration):
    dependencies = [('proyectos', '0002_remove_archivo_seccion_archivo_global_and_more')]

    operations = [
        migrations.AddField(model_name='equipousuario', name='legacy_rol',
                            field=models.CharField(max_length=100, blank=True, null=True, editable=False)),
        migrations.RunPython(preserve_roles, migrations.RunPython.noop),
        migrations.AlterField(model_name='equipousuario', name='rol',
                              field=models.CharField(max_length=100, blank=True, null=True,
                                                     choices=[('admin', 'Administrador'), ('editor', 'Editor'), ('reader', 'Lector')])),
        migrations.AddField(model_name='proyectoequipo', name='rol',
                            field=models.CharField(max_length=100, null=True, blank=True,
                                                   choices=[('admin', 'Administrador'), ('editor', 'Editor'), ('reader', 'Lector')])),
        migrations.AddField(model_name='archivo', name='equipos_visibles',
                            field=models.ManyToManyField(to='proyectos.equipo', related_name='archivos_visibles', blank=True)),
    ]
