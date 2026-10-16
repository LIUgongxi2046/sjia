import type { DispositionInput, RequestView } from '../v2-types';
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;
type Assert<T extends true> = T;
type Not<T extends boolean> = T extends true ? false : true;
type IsAny<T> = 0 extends (1 & T) ? true : false;
export type PropertyExactlyExistingInput = Assert<Equal<RequestView['disposition_input'], DispositionInput | undefined>>;
export type PropertyIsNotAny = Assert<Not<IsAny<RequestView['disposition_input']>>>;
export type PropertyIsOptional = Assert<{} extends Pick<RequestView, 'disposition_input'> ? true : false>;
export type PropertyIsNotNullable = Assert<Not<null extends RequestView['disposition_input'] ? true : false>>;
export type InputIsNotAny = Assert<Not<IsAny<DispositionInput>>>;
export const omitted: Pick<RequestView, 'disposition_input'> = {};
export const included: Pick<RequestView, 'disposition_input'> = {
  disposition_input: {
    targets: [{ target_type: 'file_object', target_id: '00000000-0000-4000-8000-000000000004', target_version: '1' }],
    policy_id: '00000000-0000-4000-8000-000000000005', evidence_ref: 'fixture-only',
  },
};
// @ts-expect-error Optional is not nullable.
export const nullRejected: Pick<RequestView, 'disposition_input'> = { disposition_input: null };
// @ts-expect-error The existing input requires evidence_ref.
export const incompleteRejected: Pick<RequestView, 'disposition_input'> = { disposition_input: { targets: [], policy_id: 'fixture-only' } };
