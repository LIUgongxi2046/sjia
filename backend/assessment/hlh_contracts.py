"""Versioned, offline HLH-2004 reference contracts, separate from MAS/MS.

The source table does not define software missingness or timing rules. The
explicit bounds/window policy below is an engineering contract that still needs
clinical governance before any live use. References are claims at this pure
boundary; authenticated services must resolve them, approvals and authorization.
No active policy, patient input, or approval is created by importing this module.
"""
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Text = Annotated[str, Field(min_length=1, max_length=1024, pattern=r'.*\S.*')]
DecimalText = Annotated[str, Field(max_length=80, pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')]
EvidenceRefs = Annotated[list[Text], Field(min_length=1, max_length=100)]
State = Literal['met', 'not_met', 'unknown']
Qualification = Literal['qualified', 'missing', 'unverified', 'conflict', 'unknown_unit', 'invalid_time', 'invalid_context']
FACT_CODES = (
    'applicability', 'molecular', 'fever', 'splenomegaly', 'hemoglobin',
    'platelet_count', 'neutrophil_count', 'triglycerides', 'triglycerides_fasting',
    'fibrinogen', 'hemophagocytosis', 'no_malignancy_evidence', 'nk_activity',
    'ferritin', 'soluble_cd25', 'age_at_hemoglobin',
)
FactCode = Literal[
    'applicability', 'molecular', 'fever', 'splenomegaly', 'hemoglobin',
    'platelet_count', 'neutrophil_count', 'triglycerides', 'triglycerides_fasting',
    'fibrinogen', 'hemophagocytosis', 'no_malignancy_evidence', 'nk_activity',
    'ferritin', 'soluble_cd25', 'age_at_hemoglobin',
]
CriterionCode = Literal['fever', 'splenomegaly', 'cytopenias', 'lipids_or_fibrinogen',
    'hemophagocytosis', 'nk_activity', 'ferritin', 'soluble_cd25']


class HLHContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class HLHThreshold(HLHContract):
    operator: Literal['lt', 'le', 'gt', 'ge']
    value: DecimalText
    unit: Text


def _source_threshold_schema(schema):
    native = {
        'hemoglobin': ('lt', '90', 'g/L'), 'infant_hemoglobin': ('lt', '100', 'g/L'),
        'platelet_count': ('lt', '100', '10^9/L'), 'neutrophil_count': ('lt', '1', '10^9/L'),
        'fibrinogen': ('le', '1.5', 'g/L'), 'ferritin': ('ge', '500', 'ug/L'),
        'soluble_cd25': ('ge', '2400', 'U/mL'),
    }
    def constraint(operator, value, unit):
        pattern = '^' + value.replace('.', r'\.') + (r'0*' if '.' in value else r'(?:\.0+)?') + '$'
        return {'properties': {'operator': {'const': operator}, 'value': {'pattern': pattern}, 'unit': {'const': unit}}}
    for code, triple in native.items():
        schema['properties'][code]['allOf'] = [constraint(*triple)]
    schema['properties']['triglycerides']['oneOf'] = [constraint('ge', '3', 'mmol/L'), constraint('ge', '265', 'mg/dL')]


class HLHThresholds(HLHContract):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, json_schema_extra=_source_threshold_schema)
    hemoglobin: HLHThreshold
    infant_hemoglobin: HLHThreshold
    platelet_count: HLHThreshold
    neutrophil_count: HLHThreshold
    triglycerides: HLHThreshold
    fibrinogen: HLHThreshold
    ferritin: HLHThreshold
    soluble_cd25: HLHThreshold

    @model_validator(mode='after')
    def original_table_only(self):
        expected = {
            'hemoglobin': ('lt', Decimal('90'), 'g/L'),
            'infant_hemoglobin': ('lt', Decimal('100'), 'g/L'),
            'platelet_count': ('lt', Decimal('100'), '10^9/L'),
            'neutrophil_count': ('lt', Decimal('1.0'), '10^9/L'),
            'fibrinogen': ('le', Decimal('1.5'), 'g/L'),
            'ferritin': ('ge', Decimal('500'), 'ug/L'),
            'soluble_cd25': ('ge', Decimal('2400'), 'U/mL'),
        }
        for code, triple in expected.items():
            item = getattr(self, code)
            if (item.operator, Decimal(item.value), item.unit) != triple:
                raise ValueError(f'HLH_SOURCE_TABLE_THRESHOLD_MISMATCH:{code}')
        item = self.triglycerides
        if (item.operator, Decimal(item.value), item.unit) not in (
            ('ge', Decimal('3.0'), 'mmol/L'), ('ge', Decimal('265'), 'mg/dL'),
        ):
            raise ValueError('HLH_SOURCE_TABLE_THRESHOLD_MISMATCH:triglycerides')
        return self


class HLHTemporalPolicy(HLHContract):
    # There are deliberately no default windows: literature is not their approval.
    maximum_age_seconds: Annotated[dict[FactCode, Annotated[int, Field(ge=0, le=315537897599)]], Field(min_length=16, max_length=16)]
    maximum_cross_laboratory_span_seconds: Annotated[int, Field(ge=0, le=315537897599)]
    require_available_and_verified_by_known_at: Literal[True]
    cross_laboratory_conflict: Literal['all_numeric_criteria_unknown']
    interval_policy: Literal['whole_interval_within_inclusive_window']

    @model_validator(mode='after')
    def complete_windows(self):
        if set(self.maximum_age_seconds) != set(FACT_CODES):
            raise ValueError('HLH_EXPLICIT_WINDOW_REQUIRED_FOR_EVERY_FACT')
        return self


class HLHRulePolicy(HLHContract):
    schema_version: Literal['hlh-2004-rule-1']
    kind: Literal['hlh_2004_reference']
    source_table: Literal['histiocyte-society-2004-table-2', 'henter-2007-table-I']
    source_revision_id: UUID
    evidence_refs: EvidenceRefs
    policy_revision_id: UUID
    semantics_version: Literal['hlh-2004-bounds-1']
    count_bounds_policy: Literal['met_lower_met_plus_unknown_upper']
    unknown_logic: Literal['strong_kleene_compounds']
    applicability_precedence: Literal['not_applicable_then_unknown_then_paths']
    semantics_evidence_refs: EvidenceRefs
    applicability_policy_ref: Text
    clinical_assertion_policy_ref: Text
    infant_age_days_exclusive: Literal[28]
    cytopenia_lineages_required: Literal[2]
    criteria_required: Literal[5]
    criteria_total: Literal[8]
    molecular_path: Literal['professionally_confirmed_consistent_diagnosis']
    fever_definition: Literal['professionally_confirmed_source_table_fever']
    malignancy_qualifier: Literal['required_for_hemophagocytosis_only']
    unit_policy: Literal['exact_native_unit_no_implicit_conversion']
    thresholds: HLHThresholds
    temporal_policy: HLHTemporalPolicy

    @model_validator(mode='after')
    def distinct_evidence(self):
        for refs in (self.evidence_refs, self.semantics_evidence_refs):
            if len(set(refs)) != len(refs):
                raise ValueError('HLH_DUPLICATE_EVIDENCE_REFERENCE')
        return self


class HLHMedicalInterval(HLHContract):
    lower: AwareDatetime
    upper: AwareDatetime
    upper_inclusive: bool
    precision: Literal['date', 'minute', 'second']

    @model_validator(mode='after')
    def interval_shape(self):
        if self.lower > self.upper or self.lower == self.upper and not self.upper_inclusive:
            raise ValueError('HLH_MEDICAL_INTERVAL_INVALID')
        if self.precision == 'second' and (self.lower != self.upper or not self.upper_inclusive):
            raise ValueError('HLH_SECOND_PRECISION_REQUIRES_EXACT_INSTANT')
        if self.precision == 'minute' and (self.upper - self.lower != timedelta(minutes=1) or self.upper_inclusive):
            raise ValueError('HLH_MINUTE_PRECISION_REQUIRES_FULL_INTERVAL')
        if self.precision == 'date' and (self.lower == self.upper or self.upper_inclusive):
            raise ValueError('HLH_DATE_PRECISION_MUST_NOT_COLLAPSE_TO_INSTANT')
        return self


class HLHFactProof(HLHContract):
    fact_version_id: UUID
    source_revision_id: UUID
    verification_id: UUID
    verified_by: UUID
    evidence_ref: Text
    medical_interval: HLHMedicalInterval
    available_at: AwareDatetime
    verified_at: AwareDatetime
    valid_until: AwareDatetime | None
    sample_id: UUID | None


class HLHObservation(HLHContract):
    qualification: Qualification
    proof: HLHFactProof | None

    @model_validator(mode='after')
    def qualified_proof(self):
        if self.qualification == 'qualified' and self.proof is None:
            raise ValueError('HLH_QUALIFIED_FACT_REQUIRES_PROOF')
        return self


class HLHAssertion(HLHObservation):
    value: bool | None

    @model_validator(mode='after')
    def qualified_value(self):
        if self.qualification == 'qualified' and self.value is None:
            raise ValueError('HLH_QUALIFIED_ASSERTION_REQUIRES_VALUE')
        return self


class HLHMolecularAssertion(HLHAssertion):
    assertion_type: Literal['molecular_diagnosis_consistent_with_hlh']


class HLHNumericObservation(HLHObservation):
    value: DecimalText | None
    qualifier: Literal['eq', 'lt', 'le', 'gt', 'ge'] | None
    unit: Text | None

    @model_validator(mode='after')
    def qualified_value(self):
        if self.qualification == 'qualified' and (self.value is None or self.qualifier is None or self.unit is None):
            raise ValueError('HLH_QUALIFIED_NUMERIC_REQUIRES_VALUE_QUALIFIER_UNIT')
        return self


class HLHAgeAtSample(HLHObservation):
    age_days: Annotated[int, Field(ge=0)] | None
    hemoglobin_fact_version_id: UUID | None

    @model_validator(mode='after')
    def qualified_value(self):
        if self.qualification == 'qualified' and (self.age_days is None or self.hemoglobin_fact_version_id is None):
            raise ValueError('HLH_QUALIFIED_AGE_REQUIRES_SAMPLE_BINDING')
        return self


class HLHHemophagocytosis(HLHAssertion):
    specimen_site: Literal['bone_marrow', 'spleen', 'lymph_node', 'other', 'unknown']


class HLHNKActivity(HLHAssertion):
    laboratory_reference: Text | None


class HLHInputs(HLHContract):
    schema_version: Literal['hlh-2004-inputs-1']
    assessment_at: AwareDatetime
    known_at: AwareDatetime
    applicability: HLHAssertion
    molecular: HLHMolecularAssertion
    fever: HLHAssertion
    splenomegaly: HLHAssertion
    hemoglobin: HLHNumericObservation
    platelet_count: HLHNumericObservation
    neutrophil_count: HLHNumericObservation
    triglycerides: HLHNumericObservation
    triglycerides_fasting: HLHAssertion
    fibrinogen: HLHNumericObservation
    hemophagocytosis: HLHHemophagocytosis
    no_malignancy_evidence: HLHAssertion
    nk_activity: HLHNKActivity
    ferritin: HLHNumericObservation
    soluble_cd25: HLHNumericObservation
    age_at_hemoglobin: HLHAgeAtSample

    @model_validator(mode='after')
    def coherent_cutoffs(self):
        if self.assessment_at > self.known_at:
            raise ValueError('HLH_ASSESSMENT_AFTER_KNOWN_AT')
        identities = [getattr(self, code).proof.fact_version_id for code in FACT_CODES
            if getattr(self, code).qualification == 'qualified']
        if len(set(identities)) != len(identities):
            raise ValueError('HLH_FACT_IDENTITIES_MUST_BE_INDEPENDENT')
        return self


class HLHConditionResult(HLHContract):
    state: State
    reason_codes: list[Text]
    fact_version_ids: list[UUID]


class HLHCriterionResult(HLHConditionResult):
    code: CriterionCode
    subconditions: dict[str, HLHConditionResult]


class HLHResult(HLHContract):
    schema_version: Literal['hlh-2004-result-1']
    kind: Literal['hlh_2004_reference']
    use_boundary: Literal['offline_reference_support_only']
    clinical_use_authorized: Literal[False]
    policy_revision_id: UUID
    policy_checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    input_checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    source_table: Literal['histiocyte-society-2004-table-2', 'henter-2007-table-I']
    source_revision_id: UUID
    semantics_version: Literal['hlh-2004-bounds-1']
    assessment_at: AwareDatetime
    known_at: AwareDatetime
    applicability: Literal['applicable', 'not_applicable', 'unknown']
    applicability_condition: HLHConditionResult
    molecular_condition: HLHConditionResult
    criteria: Annotated[list[HLHCriterionResult], Field(min_length=8, max_length=8)]
    criteria_met_lower_bound: Annotated[int, Field(ge=0, le=8)]
    criteria_met_upper_bound: Annotated[int, Field(ge=0, le=8)]
    criteria_path: State
    molecular_or_criteria: State
    reference_result: Literal['criteria_supported', 'criteria_not_supported', 'insufficient', 'not_applicable']
    supported_paths: list[Literal['molecular', 'five_of_eight']]
    limitation_codes: Annotated[list[Text], Field(min_length=1)]


class HLHBinaryEncoding(HLHContract):
    value: Text
    encoded: Literal[0, 1]
    assertion_state: Literal['present', 'absent']
    specimen_site: Literal['bone_marrow', 'spleen', 'lymph_node', 'other', 'unknown'] | None

    @model_validator(mode='after')
    def consistent_assertion(self):
        if self.assertion_state != ('present' if self.encoded else 'absent'):
            raise ValueError('HLH_CATEGORICAL_ASSERTION_ENCODING_MISMATCH')
        return self


class HLHVariable(HLHContract):
    variable_code: FactCode
    concept_code: Text
    value_type: Literal['decimal', 'categorical']
    normalized_unit: Text | None
    mapping_config_id: UUID | None
    categorical_values: list[HLHBinaryEncoding]
    accepted_context_types: Annotated[list[Literal['history', 'current', 'discharge', 'actual']], Field(min_length=1)]
    accepted_methods: Annotated[list[Text | None], Field(min_length=1)]

    @model_validator(mode='after')
    def explicit_shape(self):
        units = {'hemoglobin': {'g/L'}, 'platelet_count': {'10^9/L'}, 'neutrophil_count': {'10^9/L'},
            'triglycerides': {'mmol/L', 'mg/dL'}, 'fibrinogen': {'g/L'}, 'ferritin': {'ug/L'},
            'soluble_cd25': {'U/mL'}, 'age_at_hemoglobin': {'day'}}
        if (len(set(self.accepted_context_types)) != len(self.accepted_context_types)
                or len(set(self.accepted_methods)) != len(self.accepted_methods)):
            raise ValueError('HLH_DUPLICATE_VARIABLE_SCOPE')
        if self.variable_code in units:
            if (self.value_type != 'decimal' or self.normalized_unit not in units[self.variable_code]
                    or self.mapping_config_id is None or self.categorical_values or None in self.accepted_methods):
                raise ValueError('HLH_NUMERIC_BINDING_REQUIRES_EXPLICIT_UNIT_MAPPING_METHOD')
        elif (self.value_type != 'categorical' or self.normalized_unit is not None or self.mapping_config_id is not None
                or {item.encoded for item in self.categorical_values} != {0, 1}
                or len({item.value for item in self.categorical_values}) != len(self.categorical_values)):
            raise ValueError('HLH_ASSERTION_BINDING_REQUIRES_EXPLICIT_BINARY_STATES')
        for item in self.categorical_values:
            if (self.variable_code == 'hemophagocytosis') != (item.specimen_site is not None):
                raise ValueError('HLH_SPECIMEN_MAPPING_REQUIRED_ONLY_FOR_HEMOPHAGOCYTOSIS')
        return self


class HLHMetadataBindings(HLHContract):
    age_sample_binding: Literal['same_record_version_and_exact_medical_interval']
    fasting_sample_binding: Literal['same_report_and_exact_sample_interval']
    nk_laboratory_reference: Literal['verified_record_field_reference_interval']
    hemophagocytosis_specimen: Literal['approved_categorical_value_mapping']
    molecular_assertion: Literal['qualified_concept_not_raw_variant_or_family_history']


class HLHInputPolicy(HLHContract):
    schema_version: Literal['hlh-input-policy-1']
    purpose: Text
    variables: Annotated[list[HLHVariable], Field(min_length=16, max_length=16)]
    metadata_bindings: HLHMetadataBindings
    evidence_refs: EvidenceRefs

    @model_validator(mode='after')
    def complete_independent_variables(self):
        if ({item.variable_code for item in self.variables} != set(FACT_CODES)
                or len({item.concept_code for item in self.variables}) != len(FACT_CODES)
                or len(set(self.evidence_refs)) != len(self.evidence_refs)):
            raise ValueError('HLH_COMPLETE_INDEPENDENT_INPUT_BINDINGS_REQUIRED')
        return self


def hlh_variable(policy: HLHInputPolicy, variable_code: str) -> HLHVariable:
    matches = [item for item in policy.variables if item.variable_code == variable_code]
    if len(matches) != 1:
        raise ValueError('HLH_VARIABLE_NOT_BOUND_BY_POLICY')
    return matches[0]


def validate_hlh_policy_pair(rule: HLHRulePolicy, bindings: HLHInputPolicy) -> None:
    """Validate a rule/input pairing, without granting either an approval."""
    for code in ('hemoglobin', 'platelet_count', 'neutrophil_count', 'triglycerides',
            'fibrinogen', 'ferritin', 'soluble_cd25'):
        if hlh_variable(bindings, code).normalized_unit != getattr(rule.thresholds, code).unit:
            raise ValueError(f'HLH_RULE_INPUT_UNIT_MISMATCH:{code}')
