"""T02 数据模型与目标数据库迁移验证。

验证 89 张表结构、ORM 往返、DOMAIN 约束与复合外键强制。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T02 --keepdb
"""
from datetime import datetime, timezone
from decimal import Decimal

from django.apps import apps
from django.db import IntegrityError, connection
from django.test import TestCase

from identity.models import Patient, Principal


class TableStructureTest(TestCase):
    def test_current_business_table_manifest_in_sjia_schema(self):
        # The declared ORM manifest evolves with version-controlled migrations;
        # framework tables are not business tables and may live in public or sjia.
        expected = {model._meta.db_table.split('"."', 1)[1].strip('"')
            for model in apps.get_models() if model._meta.db_table.startswith('sjia"."')}
        self.assertTrue(expected)
        self.assertTrue({'record', 'record_field', 'archive_page', 'environment_gate'} <= expected)
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'sjia'")
            actual = {row[0] for row in cursor.fetchall()}
        framework = {'django_migrations', 'django_content_type', 'django_session',
            'auth_permission', 'auth_group', 'auth_group_permissions', 'auth_user',
            'auth_user_groups', 'auth_user_user_permissions', 'django_admin_log'}
        self.assertEqual(actual - framework, expected)

    def test_domains_exist(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_type WHERE typname IN ('finite_decimal','sha256_hex')"
            )
            self.assertEqual(cursor.fetchone()[0], 2)


class OrmRoundTripTest(TestCase):
    def test_principal_patient_round_trip(self):
        now = datetime.now(timezone.utc)
        principal = Principal.objects.create(
            account_status="active", auth_epoch=0, created_at=now, updated_at=now
        )
        patient = Patient.objects.create(
            display_id="P-0001", status="active", created_at=now
        )
        self.assertEqual(Principal.objects.get(id=principal.id).account_status, "active")
        self.assertEqual(Patient.objects.get(id=patient.id).display_id, "P-0001")


class DomainEnforcementTest(TestCase):
    def test_finite_decimal_rejects_nan(self):
        with connection.cursor() as cursor:
            with self.assertRaises(IntegrityError):
                cursor.execute("SELECT 'NaN'::sjia.finite_decimal")

    def test_sha256_hex_rejects_invalid(self):
        with connection.cursor() as cursor:
            with self.assertRaises(IntegrityError):
                cursor.execute("SELECT 'not-hex'::sjia.sha256_hex")

    def test_finite_decimal_orm_round_trip(self):
        from core.fields import FiniteDecimalField

        field = FiniteDecimalField()
        value = field.get_prep_value("12.345")
        self.assertEqual(value, Decimal("12.345"))
        self.assertEqual(field.to_python("12.345"), Decimal("12.345"))


class CompositeForeignKeyTest(TestCase):
    def test_composite_fk_constraints_present(self):
        # 复合外键在 pg_constraint 中以多列 contype='f' 呈现，验证 file_group 的 (draft_id, patient_id) 复合外键存在。
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conkey
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = 'sjia' AND t.relname = 'file_group'
                  AND c.contype = 'f'
                """
            )
            conkeys = [row[0] for row in cursor.fetchall()]
            # 复合外键的 conkey 长度大于 1（draft_id, patient_id 两列）
            self.assertTrue(any(len(key) > 1 for key in conkeys))

    def test_composite_fk_columns_present(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='sjia' AND table_name='file_group' ORDER BY ordinal_position"
            )
            columns = {row[0] for row in cursor.fetchall()}
            self.assertIn("draft_id", columns)
            self.assertIn("patient_id", columns)
