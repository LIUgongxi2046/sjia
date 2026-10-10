import uuid

from django.db import models

from core.fields import FiniteDecimalField


class DictionaryEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    dictionary_version = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="dictionary_version_id", related_name="+")
    concept_code = models.TextField()
    category = models.TextField()
    label = models.TextField()
    definition_ref = models.TextField()

    class Meta:
        db_table = 'sjia"."dictionary_entry'
        constraints = [
            models.UniqueConstraint(fields=["dictionary_version_id", "concept_code"], name="dictionary_entry_version_concept_uniq"),
            models.UniqueConstraint(fields=["id", "dictionary_version_id"], name="dictionary_entry_id_version_uniq"),
        ]


class DictionaryAlias(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    entry_id = models.UUIDField()
    dictionary_version_id = models.UUIDField()
    original_alias = models.TextField()
    normalized_alias = models.TextField()
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")

    class Meta:
        db_table = 'sjia"."dictionary_alias'
        constraints = [
            models.UniqueConstraint(fields=["entry_id", "normalized_alias"], name="dictionary_alias_entry_normalized_uniq"),
        ]
        indexes = [
            models.Index(fields=["dictionary_version_id", "normalized_alias"], name="dictionary_alias_lookup"),
        ]


class UnitMapping(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    config = models.ForeignKey("configurations.ConfigVersion", on_delete=models.RESTRICT, db_column="config_id", related_name="+")
    concept_code = models.TextField()
    source_revision = models.ForeignKey("sources.SourceRevision", on_delete=models.RESTRICT, db_column="source_revision_id", related_name="+")
    raw_unit = models.TextField()
    normalized_unit = models.TextField()
    method_scope = models.TextField()
    multiplier = FiniteDecimalField()
    additive = FiniteDecimalField()
    exact_values_only = models.BooleanField()

    class Meta:
        db_table = 'sjia"."unit_mapping'
        constraints = [
            models.UniqueConstraint(fields=["config_id", "concept_code", "source_revision_id", "raw_unit", "method_scope"], name="unit_mapping_key_uniq"),
        ]
