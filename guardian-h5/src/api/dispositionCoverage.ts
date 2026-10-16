import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../backend-design/disposition-coverage.schema.json';
import { fetchJson } from './client';

const validator = new Ajv2020({ strict: true, strictTypes: false, allowUnionTypes: true });
addFormats(validator);
const check = validator.compile<DispositionCoverage>(schema);
export interface CoverageObject { model: string; id: string; versions: Record<string, string>; retention_policy_id: string | null; references: { field: string; model: string | null; id: string }[] }
export interface DispositionCoverage {
  patient_id: string; purpose: string; observed_at: string; manifest_sha256: string;
  whole_data_disposal_supported: false; execution_authorized: false; scope_expansion_authorized: false;
  inventory_truncated: boolean;
  inventory: { model: string; total_objects: number; included_objects: number; truncated: boolean }[];
  objects: CoverageObject[];
  catalog: { executors: { target_type: string; availability: string; environment_supported?: boolean; coverage: string }[];
    unsupported_categories: { category: string; executor_registered: false; required_contract: string }[];
    execution_prerequisites: string[] };
  policies: { policy_id: string; version: string; approval_metadata_current: boolean;
    configured_disposition: 'delete' | 'retain' | null; configured_retention_days: number | null;
    unresolved_policy_inputs: string[] }[];
  dependencies: { coverage: 'registered_edges_only'; total_patient_nodes: number; truncated: boolean;
    cross_patient_edge_rejected: boolean; nodes: { id: string; resource_type: string; resource_id: string; resource_version: string; validity: string }[];
    edges: { id: string; dependent_node_id: string; prerequisite_node_id: string; edge_type: string;
      prerequisite: { resource_type: string; resource_id: string; resource_version: string; scope: string } }[] };
  unresolved_coverage: string[];
}
export function validateDispositionCoverage(value: unknown): DispositionCoverage {
  if (!check(value)) throw new Error('处置覆盖清单未通过接口校验，本次内容未被采用。');
  return value;
}
export async function readDispositionCoverage(patientId: string, purpose: string, signal: AbortSignal): Promise<DispositionCoverage> {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(patientId) || !purpose.trim() || purpose.length > 128) throw new Error('请填写准确患儿 UUID 和已授权用途。');
  const result = validateDispositionCoverage(await fetchJson(`/api/v2/support/patients/${patientId}/disposition-coverage?${new URLSearchParams({ purpose })}`, { signal }));
  if (result.patient_id !== patientId || result.purpose !== purpose) throw new Error('清单与本次患儿或用途不一致。');
  return result;
}
