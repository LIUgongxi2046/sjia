"""Per-row disposition is distinct from a medical unknown or ordinary failure.

The unfiltered manager is reserved for audit/retention, not presentation. Existing
relations keep their stable keys. Null payloads never stand for clinical values.
"""
from django.db import models


class ContentDisposed(Exception):
    reason_code = 'CONTENT_DISPOSED'


class ErasureQuerySet(models.QuerySet):
    # Never infer/reveal a disposition from an ORM miss. Callers which return a
    # tombstone must first authorize its retained patient/record identity and
    # explicitly call assert_content_available. Chained filters stay intact.
    pass


class LiveContentManager(models.Manager.from_queryset(ErasureQuerySet)):
    def get_queryset(self):
        from retention.models import ContentAccessRevocation
        revoked = ContentAccessRevocation.objects.filter(adapter=self.model._meta.label_lower).values('object_id')
        return super().get_queryset().filter(content_erased_at__isnull=True).exclude(pk__in=revoked)


class ErasureModel(models.Model):
    content_erased_at = models.DateTimeField(null=True, editable=False)
    content_disposition_id = models.UUIDField(null=True, editable=False)
    objects = LiveContentManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = 'all_objects'
        default_manager_name = 'objects'
