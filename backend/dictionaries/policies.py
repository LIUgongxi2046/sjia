import unicodedata
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from assessment.policies import Evidence, MappingPolicy, PolicyModel, Text


def normalize_alias(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


class AliasDefinition(PolicyModel):
    original_alias: Text
    source_revision_id: UUID


class EntryDefinition(PolicyModel):
    id: UUID
    concept_code: Text
    category: Text
    label: Text
    definition_ref: Text
    aliases: list[AliasDefinition]

    @model_validator(mode='after')
    def unique_aliases(self):
        values = [normalize_alias(item.original_alias) for item in self.aliases]
        if len(values) != len(set(values)):
            raise ValueError('同一字典项目的别名不能重复')
        return self


class DictionaryPolicy(PolicyModel):
    schema_version: Literal['dictionary-1']
    entries: Annotated[list[EntryDefinition], Field(min_length=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def unique_entries(self):
        for attribute in ('id', 'concept_code'):
            values = [getattr(item, attribute) for item in self.entries]
            if len(values) != len(set(values)):
                raise ValueError('字典项目身份与项目代码不能重复')
        return self


class MappingPolicyV1(MappingPolicy):
    schema_version: Literal['mapping-1']
