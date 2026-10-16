// Complete synthetic wire fixtures shared by nonvisual production-code tests.
export const fixtureTime = '2099-01-01T00:00:00Z';
export const fixtureDigest = 'a'.repeat(64);
export const syntheticBootstrap = (csrf_token = 'synthetic') => ({ csrf_token, return_paths: ['/home', '/records', '/account', '/associations'], schema_version: '1.0.0' });
export const syntheticSession = (principal_id, selected_patient_id = principal_id, csrf_token = 'synthetic') => ({ principal_id, session_binding: fixtureDigest, account_status: 'active', expires_at: fixtureTime, audience: 'family', capabilities: [], csrf_token, selected_patient_id });
export const syntheticField = field => ({ required: false, allowed_units: [], dictionary_category: null, minimum: null, maximum: null, max_length: null, max_occurrences: 100, time_required: false, ...field });
export const syntheticFieldConfig = (patient_id, kind, fields) => ({ patient_id, kind, state: 'available', config_version: '00000000-0000-4000-8000-000000000900', checksum: fixtureDigest, reason_code: null, watch_field_codes: [], fields: fields.map(syntheticField) });
export const syntheticIntent = (intent_id, request_id, generation = '1', status = 'prepared') => ({ intent_id, request_id, generation, status, expires_at: fixtureTime, payload_sha256: fixtureDigest });
