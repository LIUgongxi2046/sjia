# dependency_edge 的 SQL 主键为 (dependent_id, prerequisite_id, edge_type)。Django 需要单列主键，
# 因此增加代理主键 id，原复合主键改为唯一约束 dependency_edge_pk_uniq。
import uuid

from django.db import models


class DependencyNode(models.Model):
    class Validity(models.TextChoices):
        VALID = "valid", "valid"
        INVALID = "invalid", "invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    patient = models.ForeignKey("identity.Patient", on_delete=models.RESTRICT, null=True, db_column="patient_id", related_name="+")
    resource_type = models.TextField()
    resource_id = models.UUIDField()
    resource_version = models.TextField()
    validity = models.CharField(max_length=16, choices=Validity.choices)

    class Meta:
        db_table = 'sjia"."dependency_node'
        constraints = [
            models.UniqueConstraint(fields=["resource_type", "resource_id", "resource_version"], name="dependency_node_resource_version_uniq"),
        ]


class DependencyEdge(models.Model):
    class EdgeType(models.TextChoices):
        SOURCE = "source", "source"
        VERIFICATION = "verification", "verification"
        MAPPING = "mapping", "mapping"
        POLICY = "policy", "policy"
        INPUT = "input", "input"
        PUBLICATION = "publication", "publication"
        CONTACT = "contact", "contact"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    dependent = models.ForeignKey(DependencyNode, on_delete=models.RESTRICT, db_column="dependent_id", related_name="+")
    prerequisite = models.ForeignKey(DependencyNode, on_delete=models.RESTRICT, db_column="prerequisite_id", related_name="+")
    edge_type = models.CharField(max_length=16, choices=EdgeType.choices)

    class Meta:
        db_table = 'sjia"."dependency_edge'
        constraints = [
            models.UniqueConstraint(fields=["dependent_id", "prerequisite_id", "edge_type"], name="dependency_edge_pk_uniq"),
        ]
        indexes = [
            models.Index(fields=["prerequisite_id", "dependent_id"], name="dependency_reverse"),
        ]
