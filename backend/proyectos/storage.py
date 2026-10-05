import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Archivo


logger = logging.getLogger(__name__)


def delete_unreferenced(storage, name, using='default'):
    if not name or Archivo.objects.using(using).filter(archivo=name).exists():
        return
    try:
        storage.delete(name)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception('No se pudo eliminar el archivo de almacenamiento %s', name)


def cleanup_after_commit(storage, name, using='default'):
    transaction.on_commit(lambda: delete_unreferenced(storage, name, using), using=using)


@receiver(post_delete, sender=Archivo)
def cleanup_deleted_file(sender, instance, using, **kwargs):
    cleanup_after_commit(instance.archivo.storage, instance.archivo.name, using)
