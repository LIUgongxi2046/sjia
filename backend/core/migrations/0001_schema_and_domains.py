from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.RunSQL(
            "CREATE SCHEMA IF NOT EXISTS sjia",
            reverse_sql="DROP SCHEMA IF EXISTS sjia CASCADE",
        ),
        migrations.RunSQL(
            (
                "CREATE DOMAIN sjia.finite_decimal AS numeric "
                "CHECK (VALUE NOT IN ('NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric))"
            ),
            reverse_sql="DROP DOMAIN IF EXISTS sjia.finite_decimal",
        ),
        migrations.RunSQL(
            "CREATE DOMAIN sjia.sha256_hex AS text "
            "CHECK (VALUE ~ '^[0-9a-f]{64}$')",
            reverse_sql="DROP DOMAIN IF EXISTS sjia.sha256_hex",
        ),
    ]
