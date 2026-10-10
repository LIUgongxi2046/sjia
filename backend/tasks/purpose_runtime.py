from config.celery import app


app.conf.update(CELERY_BEAT_SCHEDULE={
    'dispatch-purpose-outbox': app.conf.beat_schedule['dispatch-purpose-outbox'],
    'reconcile-local-dispositions': app.conf.beat_schedule['reconcile-local-dispositions'],
})
if set(app.conf.beat_schedule) != {'dispatch-purpose-outbox', 'reconcile-local-dispositions'}:
    raise RuntimeError('用途调度配置范围不一致')
