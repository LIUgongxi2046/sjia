import type {
  AssessmentCreationOptions, AssessmentCreationOptionsWire, AssessmentObservationPeriodWire,
  AssessmentObservationTime, AssessmentSelectionCandidate, AssessmentSelectionCandidateWire,
  DynamicCreationOptions, DynamicCreationOptionsWire,
} from '../../../frontend-design/v2-types';

/** Preserve the existing 13-field application shape without mutating the wire value. */
export function projectAssessmentObservationTime(value: AssessmentObservationTime | null): AssessmentSelectionCandidate['medical_time'] {
  if (value === null) return null;
  return {
    raw_text: value.raw_text, precision: value.precision, local_year: value.local_year,
    local_month: value.local_month, end_year: value.end_year, end_month: value.end_month,
    relative_text: value.relative_text, local_date: value.local_date, local_datetime: value.local_datetime,
    timezone_name: value.timezone_name, utc_offset_minutes: value.utc_offset_minutes,
    instant: value.instant, resolution_status: value.resolution_status,
  };
}

function projectCandidate(value: AssessmentSelectionCandidateWire): AssessmentSelectionCandidate {
  return { ...value, medical_time: projectAssessmentObservationTime(value.medical_time) };
}

function projectPeriod(value: AssessmentObservationPeriodWire): AssessmentCreationOptions['observation_periods'][number] {
  return { ...value, start_time: projectAssessmentObservationTime(value.start_time), end_time: projectAssessmentObservationTime(value.end_time) };
}

export function projectAssessmentCreationOptions(value: AssessmentCreationOptionsWire): AssessmentCreationOptions {
  return { ...value,
    plans: value.plans.map(plan => ({ ...plan,
      selection_variables: plan.selection_variables.map(variable => ({ ...variable, candidates: variable.candidates.map(projectCandidate) })),
    })),
    observation_periods: value.observation_periods.map(projectPeriod),
  };
}

export function projectDynamicCreationOptions(value: DynamicCreationOptionsWire): DynamicCreationOptions {
  return { ...value,
    plans: value.plans.map(plan => ({ ...plan,
      selection_variables: plan.selection_variables.map(variable => ({ ...variable, candidates: variable.candidates.map(projectCandidate) })),
    })),
    observation_periods: value.observation_periods.map(projectPeriod),
  };
}
