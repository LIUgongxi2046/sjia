from django.db import migrations

SQL = '''
CREATE FUNCTION sjia.content_family_request_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
   RAISE EXCEPTION 'CONTENT_FAMILY_REQUEST_IMMUTABLE' USING ERRCODE='check_violation';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER content_family_request_immutable BEFORE UPDATE ON sjia.content_disposition_family_request
 FOR EACH ROW EXECUTE FUNCTION sjia.content_family_request_immutable();
'''

class Migration(migrations.Migration):
    dependencies=[('retention','0008_contentdispositionfamilyrequest_and_more')]
    operations=[migrations.RunSQL(SQL, reverse_sql='DROP TRIGGER content_family_request_immutable ON sjia.content_disposition_family_request; DROP FUNCTION sjia.content_family_request_immutable();')]
