"""Executable outbox vocabulary shared by dispatcher and consumer."""
WORKER_EVENTS = frozenset({
    'summary.requested', 'file_validation.requested', 'reconcile.requested',
    'archive_page.requested', 'evaluation_run.requested', 'recognition.requested',
    'voice_input.requested', 'purpose_copy.requested', 'disposition.local_requested', 'disposition.s3_requested',
    'disposition.provider_requested', 'assessment.requested', 'alert_evaluation.requested',
    'verification.changed', 'configuration.changed', 'clinical.context_changed', 'dynamic.timer',
    'alert_evaluation.trigger_requested', 'alert_evaluation.static_finished',
    'alert.event_created', 'notification.native_requested', 'notification.material_requested',
})


def require_worker_event(event_type):
    from tasks.service import TaskRejected

    if event_type not in WORKER_EVENTS:
        raise TaskRejected('TASK_EVENT_UNREGISTERED')
