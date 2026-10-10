from typing import Annotated
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints


ContactText = Annotated[str, StringConstraints(strict=True, min_length=1, pattern=r'\S')]


class ContactApprovalPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    contact_id: UUID
    contact_name: ContactText
    purpose: ContactText
    channel: ContactText
    channel_value: ContactText
    owner_id: UUID
    environment: ContactText
    source_revision_id: UUID
    valid_until: AwareDatetime


CONTACT_APPROVAL_VALIDATOR = Draft202012Validator(ContactApprovalPayload.model_json_schema(), format_checker=FormatChecker())


def contact_content_reason(contact, environment):
    config = contact.approval_config
    if config.kind != 'content' or config.schema_version != 'contact-1':
        return 'CONTACT_APPROVAL_SCHEMA_UNAVAILABLE'
    if not CONTACT_APPROVAL_VALIDATOR.is_valid(config.payload):
        return 'CONTACT_APPROVAL_CONTENT_INVALID'
    approved = ContactApprovalPayload.model_validate(config.payload)
    expected = ContactApprovalPayload(contact_id=contact.contact_id, contact_name=contact.contact_name,
        purpose=contact.purpose, channel=contact.channel, channel_value=contact.channel_value,
        owner_id=contact.owner_id, environment=environment, source_revision_id=contact.source_revision_id,
        valid_until=contact.valid_until)
    if approved != expected:
        return 'CONTACT_APPROVAL_CONTENT_MISMATCH'
    return None
