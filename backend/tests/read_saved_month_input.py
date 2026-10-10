import json

from tests.verify_v2_migration import require
from django.db import connection, transaction
from records.models import RecordField
from medical_time.models import MedicalTime


with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    times = {value.id: value for value in MedicalTime.objects.filter(precision='date', local_date__isnull=False)}
    fields = RecordField.objects.filter(medical_time_id__in=times).order_by('id')
    values = []
    for field in fields:
        medical_time = times[field.medical_time_id]
        require(field.patient_id == medical_time.patient_id, '原字段与医学时间所属患儿不一致')
        values.append({'local_date': medical_time.local_date.isoformat(), 'timezone': medical_time.timezone_name})
    require(values, '当前保存资料没有可用于月份控件检查的日期')
    minute_times = {value.id: value for value in MedicalTime.objects.filter(precision='minute', local_datetime__isnull=False)}
    minute_values = []
    for field in RecordField.objects.filter(medical_time_id__in=minute_times).order_by('id'):
        medical_time = minute_times[field.medical_time_id]
        require(field.patient_id == medical_time.patient_id, '原字段与准确时间所属患儿不一致')
        value = medical_time.local_datetime
        minute_values.append({'local_datetime': value.isoformat(timespec='minutes' if value.second == 0 and value.microsecond == 0 else 'auto'),
            'timezone': medical_time.timezone_name})

print(json.dumps({'values': values, 'minute_values': minute_values, 'transaction_read_only': True, 'clinical_data_created': False}))
