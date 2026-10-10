from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('recognition', '0002_transcriptionreceipt_voiceinput')]

    operations = [migrations.RunSQL(
        sql='''
        ALTER TABLE sjia.voice_input
          ADD CONSTRAINT voice_original_patient_fk FOREIGN KEY (original_file_id, patient_id)
            REFERENCES sjia.file_object (id, patient_id) ON DELETE RESTRICT,
          ADD CONSTRAINT voice_raw_response_patient_fk FOREIGN KEY (raw_response_file_id, patient_id)
            REFERENCES sjia.file_object (id, patient_id) ON DELETE RESTRICT,
          ADD CONSTRAINT voice_authorization_patient_fk FOREIGN KEY (use_authorization_id, patient_id)
            REFERENCES sjia.data_use_authorization (id, patient_id) ON DELETE RESTRICT,
          ADD CONSTRAINT voice_task_patient_fk FOREIGN KEY (task_id, patient_id)
            REFERENCES sjia.task (id, patient_id) ON DELETE RESTRICT,
          ADD CONSTRAINT voice_record_version_patient_fk FOREIGN KEY (saved_record_version_id, patient_id)
            REFERENCES sjia.record_version (id, patient_id) ON DELETE RESTRICT;
        ALTER TABLE sjia.transcription_receipt
          ADD CONSTRAINT transcription_original_patient_fk FOREIGN KEY (confirmed_original_file_id, patient_id)
            REFERENCES sjia.file_object (id, patient_id) ON DELETE RESTRICT,
          ADD CONSTRAINT transcription_authorization_patient_fk FOREIGN KEY (use_authorization_id, patient_id)
            REFERENCES sjia.data_use_authorization (id, patient_id) ON DELETE RESTRICT;
        ''',
        reverse_sql='''
        ALTER TABLE sjia.transcription_receipt
          DROP CONSTRAINT transcription_original_patient_fk,
          DROP CONSTRAINT transcription_authorization_patient_fk;
        ALTER TABLE sjia.voice_input
          DROP CONSTRAINT voice_original_patient_fk,
          DROP CONSTRAINT voice_raw_response_patient_fk,
          DROP CONSTRAINT voice_authorization_patient_fk,
          DROP CONSTRAINT voice_task_patient_fk,
          DROP CONSTRAINT voice_record_version_patient_fk;
        ''',
    )]
