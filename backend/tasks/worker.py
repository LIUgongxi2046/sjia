from celery import Task as CeleryTask
from django.db import transaction
from django.utils import timezone

from config.celery import app
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt


class RecognitionWorker(CeleryTask):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        event_id = kwargs['event_id']
        event = OutboxEvent.objects.get(id=event_id)
        if event.event_type == 'voice_input.requested':
            from recognition.voice_input_jobs import record_voice_failure

            record_voice_failure(event, task_id, getattr(exc, 'reason_code', 'VOICE_EXECUTION_FAILED'))
            return
        if event.event_type == 'evaluation_run.requested':
            from case_details.evaluation_execution import record_evaluation_failure

            record_evaluation_failure(event, task_id, getattr(exc, 'reason_code', 'DATASET_RUN_EXECUTION_FAILED'))
            return
        if event.event_type == 'archive_page.requested':
            from archives.page_execution import record_page_failure

            record_page_failure(event, task_id, getattr(exc, 'reason_code', getattr(exc, 'code', 'ARCHIVE_PAGE_EXECUTION_FAILED')))
            return
        if event.event_type == 'notification.material_requested':
            from notifications.material_delivery import record_material_failure

            record_material_failure(event, getattr(exc, 'reason_code', 'REMINDER_EXECUTION_FAILED'))
            return
        if event.event_type == 'notification.native_requested':
            from notifications.models import NotificationAttempt

            with transaction.atomic():
                task = Task.objects.select_for_update().get(id=event.task_id, schema_version='native-notification-task-2')
                attempt = NotificationAttempt.objects.select_for_update().get(send_permit_id=task.id)
                if task.status == 'accepted' and attempt.status == 'authorized':
                    error = getattr(exc, 'reason_code', 'NOTIFICATION_EXECUTION_FAILED')
                    task.status, task.status_version = 'failed', task.status_version + 1
                    task.save(update_fields=['status', 'status_version'])
                    attempt.status, attempt.finished_at = 'failed', timezone.now()
                    attempt.save(update_fields=['status', 'finished_at'])
                    TaskAttempt.objects.create(task=task, attempt_number=1, lease_generation=task.lease_generation,
                        started_at=timezone.now(), finished_at=timezone.now(), status='failed', error_code=error,
                        usage_evidence={'event_id': str(event.id), 'error_code': error})
            return
        if event.event_type in ('alert_evaluation.trigger_requested', 'alert_evaluation.static_finished'):
            from tasks.outbox import record_receipt

            with transaction.atomic():
                task = Task.objects.select_for_update().get(id=event.task_id, schema_version='dynamic-trigger-task-2')
                if task.status in ('accepted', 'queued', 'awaiting_confirmation') or task.status == 'running' and task.lease_owner == task_id:
                    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
                    task.save(update_fields=['status', 'status_version', 'lease_until'])
                    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
                    error = getattr(exc, 'reason_code', 'DYNAMIC_TRIGGER_EXECUTION_FAILED')
                    if attempts.exists():
                        attempts.update(status='failed', finished_at=timezone.now(), error_code=error)
                    else:
                        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
                            lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(),
                            status='failed', error_code=error)
                    if not ConsumerReceipt.objects.filter(consumer='dynamic_trigger', event=event).exists():
                        record_receipt('dynamic_trigger', event, task.id)
            return
        if event.event_type in ('assessment.requested', 'alert_evaluation.requested'):
            from tasks.outbox import record_receipt

            with transaction.atomic():
                dynamic = event.event_type == 'alert_evaluation.requested'
                consumer = 'alert_evaluation' if dynamic else 'assessment'
                task = Task.objects.select_for_update().get(id=event.task_id,
                    schema_version='dynamic-evaluation-task-2' if dynamic else 'static-assessment-task-2')
                if task.status in ('accepted', 'queued') or task.status == 'running' and task.lease_owner == task_id:
                    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
                    task.save(update_fields=['status', 'status_version', 'lease_until'])
                    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
                    error = getattr(exc, 'reason_code', 'DYNAMIC_EXECUTION_FAILED' if dynamic else 'ASSESSMENT_EXECUTION_FAILED')
                    if attempts.exists():
                        attempts.update(status='failed', finished_at=timezone.now(), error_code=error)
                    else:
                        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
                            lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(),
                            status='failed', error_code=error)
                    if not ConsumerReceipt.objects.filter(consumer=consumer, event=event).exists():
                        record_receipt(consumer, event, task.id)
            if not dynamic:
                from alerting.triggers import emit_static_completion

                emit_static_completion(event.task_id)
            return
        if event.event_type == 'disposition.local_requested':
            from retention.jobs import record_disposition_failure

            record_disposition_failure(event, task_id, getattr(exc, 'reason_code', 'DISPOSITION_EXECUTION_FAILED'))
            return
        if event.event_type == 'disposition.provider_requested':
            from retention.external_deletion import SIMULATION_DELETE_TASK_SCHEMA

            with transaction.atomic():
                task = Task.objects.select_for_update().get(id=event.task_id, schema_version=SIMULATION_DELETE_TASK_SCHEMA)
                if task.status in ('accepted', 'queued') or task.status == 'running' and task.lease_owner == task_id:
                    task.status, task.status_version, task.lease_until = 'uncertain', task.status_version + 1, timezone.now()
                    task.save(update_fields=['status', 'status_version', 'lease_until'])
                    TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation,
                        status='running').update(status='uncertain', finished_at=timezone.now(),
                        error_code=getattr(exc, 'reason_code', 'EXTERNAL_DELETE_RESULT_UNKNOWN'))
            return
        with transaction.atomic():
            task = Task.objects.select_for_update().filter(id=event.task_id, lease_owner=task_id, status=Task.Status.RUNNING).first()
            if task is None:
                return
            task.status = Task.Status.FAILED
            task.status_version += 1
            task.lease_until = timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status=TaskAttempt.Status.RUNNING).update(
                status=TaskAttempt.Status.FAILED, finished_at=timezone.now(),
                error_code=getattr(exc, 'reason_code', 'PURPOSE_PROCESSING_FAILED' if task.task_type == 'purpose_copy' else 'RECOGNITION_FAILED'))


@app.task(name="tasks.worker.consume", bind=True, base=RecognitionWorker)
def consume(self, event_id):
    event = OutboxEvent.objects.filter(id=event_id).first()
    if event is None:
        return
    if event.event_type == 'archive_page.requested':
        from archives.page_execution import execute_page

        execute_page(event, self.request.id)
    elif event.event_type == 'evaluation_run.requested':
        from case_details.evaluation_execution import execute_evaluation_run

        execute_evaluation_run(event, self.request.id)
    elif event.event_type == 'recognition.requested':
        from recognition.jobs import execute_recognition

        execute_recognition(event, self.request.id)
    elif event.event_type == 'voice_input.requested':
        from recognition.voice_input_jobs import execute_voice_input

        execute_voice_input(event, self.request.id)
    elif event.event_type == 'purpose_copy.requested':
        from files.purpose_jobs import execute_copy

        execute_copy(event, self.request.id)
    elif event.event_type == 'disposition.local_requested':
        from retention.jobs import execute_disposition

        execute_disposition(event, self.request.id)
    elif event.event_type == 'disposition.provider_requested':
        from retention.jobs import execute_simulated_provider_disposition

        execute_simulated_provider_disposition(event, self.request.id)
    elif event.event_type == 'assessment.requested':
        from assessment.service import execute_static_assessment

        execute_static_assessment(event, self.request.id)
        from alerting.triggers import emit_static_completion

        emit_static_completion(event.task_id)
    elif event.event_type == 'alert_evaluation.requested':
        from alerting.service import execute_dynamic_evaluation

        execute_dynamic_evaluation(event, self.request.id)
    elif event.event_type in ('verification.changed', 'configuration.changed', 'clinical.context_changed', 'dynamic.timer'):
        from alerting.triggers import fanout_dynamic_change

        fanout_dynamic_change(event)
    elif event.event_type in ('alert_evaluation.trigger_requested', 'alert_evaluation.static_finished'):
        from alerting.triggers import execute_dynamic_trigger

        execute_dynamic_trigger(event, self.request.id)
    elif event.event_type in ('alert.event_created', 'notification.native_requested'):
        from notifications.native import consume_native

        consume_native(event)
    elif event.event_type == 'notification.material_requested':
        from notifications.material_delivery import consume_material

        consume_material(event)
    return event_id
