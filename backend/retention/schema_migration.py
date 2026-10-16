"""Frozen migration helper: ordinary constraints survive nullable erasure state.

No policy-supplied SQL is accepted. Every identifier comes from frozen migration
model state plus the checked-in field manifest. Future registry changes need a
new migration and adapter version, never silent runtime mutation.
"""
from django.db import migrations


def install_guards(apps, schema_editor, registry):
    q = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('''
CREATE OR REPLACE FUNCTION sjia.content_write_fence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate text; ident uuid; marker jsonb; own_id text;
BEGIN
  -- Same global write barrier is also held by live erasure/restore. Shared locks
  -- allow normal producers concurrently; exclusive erasure waits for commits.
  PERFORM pg_advisory_xact_lock_shared(1397377345, 26);
  marker := to_jsonb(NEW);
  own_id := marker ->> TG_ARGV[1];
  IF TG_OP = 'UPDATE' AND to_jsonb(OLD)->>'content_erased_at' IS NOT NULL THEN
    IF to_jsonb(OLD) IS DISTINCT FROM marker THEN
      RAISE EXCEPTION 'CONTENT_DISPOSED' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF marker->>'content_erased_at' IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM sjia.content_erasure e WHERE e.adapter=TG_ARGV[0]
       AND e.object_id=own_id::uuid AND e.plan_id=(marker->>'content_disposition_id')::uuid) THEN
       RAISE EXCEPTION 'CONTENT_ERASURE_PROOF_REQUIRED' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_ARGV[0]='files.fileobject' AND (marker->>'object_key' !~ '^[^/]+(/[^/]+)*$'
      OR marker->>'object_key' ~ '(^|/)[.]{1,2}(/|$)') THEN
    RAISE EXCEPTION 'CONTENT_OBJECT_KEY_NONCANONICAL' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_ARGV[0]='files.fileobject' AND TG_OP='UPDATE' AND marker->>'status'='disposed'
      AND (to_jsonb(OLD)-'status')=(marker-'status') THEN
    RETURN NEW;
  END IF;
  IF TG_ARGV[0]='files.fileobject' AND EXISTS (
      SELECT 1 FROM sjia.file_object f WHERE f.object_key=marker->>'object_key'
        AND f.id::text<>own_id AND (f.status='disposed' OR EXISTS (
          SELECT 1 FROM sjia.content_access_revocation r
          WHERE r.adapter='files.fileobject' AND r.object_id=f.id))) THEN
    RAISE EXCEPTION 'CONTENT_DISPOSED' USING ERRCODE = 'check_violation';
  END IF;
  -- Check UUID tokens in scalar, array and embedded snapshot values. This is a
  -- conservative no-resurrection fence; it neither discovers nor deletes scope.
  FOR candidate IN SELECT DISTINCT (regexp_matches(marker::text,
    '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', 'g'))[1]
  LOOP
    ident := candidate::uuid;
    IF EXISTS (SELECT 1 FROM sjia.content_erasure e WHERE e.object_id=ident)
       OR EXISTS (SELECT 1 FROM sjia.content_access_revocation e WHERE e.object_id=ident) THEN
      RAISE EXCEPTION 'CONTENT_DISPOSED' USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  IF TG_ARGV[0]='records.recordversion' AND marker->>'supersedes_version' IS NOT NULL
      AND EXISTS (SELECT 1 FROM sjia.record_version rv WHERE rv.record_id=(marker->>'record_id')::uuid
          AND rv.patient_id=(marker->>'patient_id')::uuid
          AND rv.version=(marker->>'supersedes_version')::bigint AND rv.content_erased_at IS NOT NULL) THEN
    RAISE EXCEPTION 'CONTENT_DISPOSED' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
''')
        for label, spec in sorted(registry.items()):
            model = apps.get_model(spec['model'])
            table = model._meta.db_table
            quoted = q(table)
            payload = [model._meta.get_field(name).column for name in spec['payload_fields']]
            required = [model._meta.get_field(name).column for name in spec['live_required_fields']]
            # Wrap only checks which depend on payload columns. Relational/key
            # constraints remain untouched, including historical version chains.
            cursor.execute('''SELECT c.conname, pg_get_expr(c.conbin,c.conrelid)
                FROM pg_constraint c WHERE c.conrelid=%s::regclass AND c.contype='c'
                AND EXISTS (SELECT 1 FROM unnest(c.conkey) k
                    JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k
                    WHERE a.attname=ANY(%s))''', [quoted, payload])
            for name, expression in cursor.fetchall():
                cursor.execute(f'ALTER TABLE {quoted} DROP CONSTRAINT {q(name)}')
                cursor.execute(f'ALTER TABLE {quoted} ADD CONSTRAINT {q(name)} CHECK '
                    f'(content_erased_at IS NOT NULL OR ({expression}))')
            live = ' AND '.join(f'{q(name)} IS NOT NULL' for name in required) or 'TRUE'
            blank = ' AND '.join(f'{q(name)} IS NULL' for name in payload) or 'TRUE'
            short = model._meta.model_name[:36]
            cursor.execute(f'ALTER TABLE {quoted} ADD CONSTRAINT {q(short+"_erasure_shape")} CHECK '
                f'((content_erased_at IS NULL AND content_disposition_id IS NULL AND ({live})) OR '
                f'(content_erased_at IS NOT NULL AND content_disposition_id IS NOT NULL AND ({blank})))')
            cursor.execute(f'CREATE TRIGGER content_write_fence BEFORE INSERT OR UPDATE ON {quoted} '
                f'FOR EACH ROW EXECUTE FUNCTION sjia.content_write_fence(%s,%s)',
                [label, model._meta.pk.column])
        # Sources of new copies that have no payload adapter still share the
        # same barrier and cannot attach a new object to an erased identity.
        for app, name in [('records','RecordAttachment'),('files','FileObject'),('files','FileGroup'),
                ('files','FilePage'),('files','UploadPart'),('archives','ArchivePage'),
                ('archives','ArchiveDocumentPage'),('tasks','ConsumerReceipt')]:
            model=apps.get_model(app,name)
            cursor.execute(f'CREATE TRIGGER content_write_fence BEFORE INSERT OR UPDATE ON {q(model._meta.db_table)} '
                f'FOR EACH ROW EXECUTE FUNCTION sjia.content_write_fence(%s,%s)',
                [model._meta.label_lower,model._meta.pk.column])
        # Existing current-version view must not expose a disposed historical
        # payload even when the root record relationship remains active.
        cursor.execute('''CREATE OR REPLACE VIEW sjia.current_case_record_version AS
            SELECT rv.* FROM sjia.record_version rv
            JOIN sjia.record r ON (r.id,r.patient_id,r.current_version)=(rv.record_id,rv.patient_id,rv.version)
            JOIN sjia.patient p ON p.id=rv.patient_id
            WHERE r.status='active' AND p.status='active' AND rv.content_erased_at IS NULL''')
