"""Professionally governed choices, never guardian-supplied clinical parameters."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from assessment.policies import Evidence, PolicyModel, Text


class ApprovedPresetOption(PolicyModel):
    schedule_config_id: UUID
    label: Text
    description: Text


class PatientPresetAssignment(PolicyModel):
    schema_version: Literal['patient-alert-presets-1']
    environment: Text
    patient_id: UUID
    purpose: Text
    display_name: Text
    options: Annotated[list[ApprovedPresetOption], Field(min_length=1)]
    default_schedule_config_id: UUID
    stale_selection_action: Literal['require_reselection']
    evidence_refs: Evidence

    @model_validator(mode='after')
    def exact_choices(self):
        schedules = [option.schedule_config_id for option in self.options]
        if len(set(schedules)) != len(schedules) or self.default_schedule_config_id not in schedules:
            raise ValueError('预设选项必须唯一，默认计划必须属于明确批准的选项')
        return self


class PresetSelectionInput(PolicyModel):
    assignment_id: UUID = Field(strict=False)
    assignment_checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    expected_version: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)$')]
    schedule_config_id: UUID = Field(strict=False)


class PresetIntentInput(PolicyModel):
    operation_id: Literal['alert_preset_select']
    target_key: Annotated[str, Field(min_length=1, max_length=256)]
    request_id: UUID = Field(strict=False)
    schema_version: Literal['1.0.0']
    payload: PresetSelectionInput


class PresetWriteHeaders(PolicyModel):
    intent_id: UUID = Field(strict=False)
    request_id: UUID = Field(strict=False)
    generation: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]


class PresetOptionView(PolicyModel):
    schedule_config_id: UUID
    label: Text
    description: Text
    preset_key: Text
    config_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]


class PresetDirectoryView(PolicyModel):
    schema_version: Literal['patient-alert-presets-view-1']
    patient_id: UUID
    assignment_id: UUID
    assignment_checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    assignment_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]
    display_name: Text
    purpose: Literal['family_observation']
    selection_version: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)$')]
    selected_schedule_config_id: UUID | None
    selection_state: Literal['selected', 'professional_default', 'requires_reselection']
    options: Annotated[list[PresetOptionView], Field(min_length=1)]

    @model_validator(mode='after')
    def consistent_selection(self):
        ids = [row.schedule_config_id for row in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate approved options')
        if self.selection_state == 'requires_reselection':
            if self.selected_schedule_config_id is not None:
                raise ValueError('stale choice cannot be active')
        elif self.selected_schedule_config_id not in ids:
            raise ValueError('selection must be in approved directory')
        if (self.selection_state == 'professional_default') != (self.selection_version == '0'):
            raise ValueError('default is the explicit version-zero choice')
        return self
