from django.db import migrations, models


def validate_cutover(apps, schema_editor):
    from proyectos.bootstrap import validate_inventory, inventory

    alias = schema_editor.connection.alias
    data = inventory(apps, alias)
    mapping = {
        'memberships': {str(item['id']): item['rol'] for item in data['memberships']},
        'associations': {str(item['id']): item['rol'] for item in data['associations']},
        'files': {str(item['id']): item['equipos_visibles'] for item in data['files']},
    }
    try:
        validate_inventory(data, mapping)
    except ValueError as error:
        raise RuntimeError(f'Cutover incompleto: {error}. Ejecuta bootstrap_contextual_roles con un mapa aprobado.') from error


class Migration(migrations.Migration):
    dependencies = [('proyectos', '0003_expand_contextual_roles')]

    operations = [
        migrations.RunPython(validate_cutover, migrations.RunPython.noop),
        migrations.AlterField(model_name='equipousuario', name='rol',
                              field=models.CharField(max_length=100, default='reader',
                                                     choices=[('admin', 'Administrador'), ('editor', 'Editor'), ('reader', 'Lector')])),
        migrations.AlterField(model_name='proyectoequipo', name='rol',
                              field=models.CharField(max_length=100, default='reader',
                                                     choices=[('admin', 'Administrador'), ('editor', 'Editor'), ('reader', 'Lector')])),
        migrations.AddConstraint(model_name='equipousuario', constraint=models.CheckConstraint(
            check=models.Q(rol__in=['admin', 'editor', 'reader']), name='membership_role_valid')),
        migrations.AddConstraint(model_name='proyectoequipo', constraint=models.CheckConstraint(
            check=models.Q(rol__in=['admin', 'editor', 'reader']), name='project_team_role_valid')),
    ]
