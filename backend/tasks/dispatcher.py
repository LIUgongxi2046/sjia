"""Dispatcher：从 Outbox 领取事件并投递任务标识。

消息内容只含 event_id、task_id、schema_version；publish 在数据库事务外完成，
返回后按 lease_generation 标记 published_at。
"""
from config.celery import app
from tasks.outbox import claim_events, mark_published
from tasks.registry import require_worker_event
from observability.tracing import outbox_span, trace_headers


def publish_task(event) -> str:
    require_worker_event(event.event_type)
    # 消息只含投递标识，不含病例正文；Worker 从数据库读取准确输入。
    message = {
        "event_id": str(event.id),
        "task_id": str(event.task_id) if event.task_id else None,
        "schema_version": event.schema_version,
    }
    queue = ('purpose' if event.event_type in ('purpose_copy.requested', 'disposition.local_requested', 'disposition.provider_requested')
        else 'assessment' if event.event_type in ('assessment.requested', 'alert_evaluation.requested', 'evaluation_run.requested',
            'verification.changed', 'configuration.changed', 'clinical.context_changed', 'dynamic.timer',
            'alert_evaluation.trigger_requested', 'alert_evaluation.static_finished')
        else 'notification' if event.event_type in ('alert.event_created', 'notification.native_requested',
            'notification.material_requested') else 'default')
    with outbox_span(event):
        result = app.send_task("tasks.worker.consume", kwargs={"event_id": str(event.id)},
            queue=queue, headers=trace_headers())
    return result.id


@app.task(name='tasks.dispatcher.dispatch_pending')
def dispatch_once(limit=100, owner="dispatcher", lease_duration_seconds=30, event_types=None) -> int:
    events = claim_events(limit, owner, lease_duration_seconds, event_types=event_types)
    published = 0
    for event in events:
        publish_task(event)
        mark_published(event.id, lease_generation=event.lease_generation)
        published += 1
    return published
