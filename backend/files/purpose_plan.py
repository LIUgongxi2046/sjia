from files.errors import FileRejected
from files.purpose_processing import PROCESSING_VERSION, processing_artifact_checksum
from files.purpose_schemas import ConfirmedTextProcessingPlan, PurposeProcessingPlan
from files.purpose_text import TEXT_PROCESSING_VERSION, text_processing_artifact_checksum


def processing_plan(config):
    if config.kind != 'provider':
        raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')
    if config.schema_version == 'purpose-processing-2':
        return PurposeProcessingPlan.model_validate(config.payload)
    if config.schema_version == 'purpose-processing-text-1':
        return ConfirmedTextProcessingPlan.model_validate(config.payload)
    raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')


def plan_processing_version(plan):
    if plan.schema_version == 'purpose-processing-2':
        return PROCESSING_VERSION
    if plan.schema_version == 'purpose-processing-text-1':
        return TEXT_PROCESSING_VERSION
    raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')


def plan_artifact_checksum(plan):
    if plan.schema_version == 'purpose-processing-2':
        return processing_artifact_checksum()
    if plan.schema_version == 'purpose-processing-text-1':
        return text_processing_artifact_checksum()
    raise FileRejected('PURPOSE_CONFIGURATION_UNAVAILABLE')
