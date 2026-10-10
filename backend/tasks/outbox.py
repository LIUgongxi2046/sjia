"""Outbox 领取、发布标记与消费回执。"""
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from tasks.models import ConsumerReceipt, OutboxEvent


def emit_event(
    *,
    event_type,
    schema_version,
    aggregate_id,
    aggregate_version,
    correlation_id,
    resource_refs,
    task=None,
    causation_id=None,
    delay_seconds=0,
) -> OutboxEvent:
    now = timezone.now()
    return OutboxEvent.objects.create(
        event_type=event_type,
        schema_version=schema_version,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        occurred_at=now,
        causation_id=causation_id,
        correlation_id=correlation_id,
        task=task,
        resource_refs=resource_refs,
        available_at=now + timedelta(seconds=delay_seconds),
    )


def claim_events(limit, owner, lease_duration_seconds, event_types=None) -> list[OutboxEvent]:
    """Dispatcher 使用 FOR UPDATE SKIP LOCKED 领取 Outbox 租约。"""
    now = timezone.now()
    lease_until = now + timedelta(seconds=lease_duration_seconds)
    with transaction.atomic():
        query = OutboxEvent.objects.filter(published_at__isnull=True, available_at__lte=now)
        if event_types is not None:
            query = query.filter(event_type__in=event_types)
        ids = list(
            query.select_for_update(skip_locked=True)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lt=now))
            .order_by("available_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        if not ids:
            return []
        OutboxEvent.objects.filter(id__in=ids).update(
            lease_owner=owner, lease_until=lease_until, lease_generation=F("lease_generation") + 1
        )
    return list(OutboxEvent.objects.filter(id__in=ids))


def mark_published(event_id, lease_generation) -> int:
    now = timezone.now()
    return OutboxEvent.objects.filter(id=event_id, lease_generation=lease_generation, published_at__isnull=True).update(published_at=now)


def record_receipt(consumer, event, effect_resource_id) -> ConsumerReceipt:
    return ConsumerReceipt.objects.create(
        consumer=consumer, event=event, effect_resource_id=effect_resource_id, processed_at=timezone.now()
    )
