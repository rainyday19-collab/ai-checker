import { API_URL, requestApi, validateAssessmentResponse } from './assessment.js';

function validateDetail(data) {
  if (!data || !Number.isInteger(data.id) || typeof data.student_name !== 'string' ||
      typeof data.original_filename !== 'string' || !Array.isArray(data.original_filenames) ||
      !Number.isInteger(data.page_count) || data.page_count < 1 || !Number.isInteger(data.assignment_id)) {
    throw new Error('The backend returned an invalid submission. Please try again.');
  }
  if (data.assessment) {
    validateAssessmentResponse(data.assessment);
    if (data.assessment.id !== data.assessment_id) throw new Error('The submission result could not be verified.');
  }
  return data;
}

export async function getSubmissions(assignmentId, signal) {
  const data = await requestApi(`/api/assignments/${assignmentId}/submissions`, { signal });
  if (!Array.isArray(data) || !data.every((item) => item && Number.isInteger(item.id) &&
      item.assignment_id === assignmentId && typeof item.student_name === 'string' &&
      typeof item.original_filename === 'string' && Array.isArray(item.original_filenames) &&
      Number.isInteger(item.page_count) && item.page_count >= 1 && ['pending', 'graded', 'failed'].includes(item.status))) {
    throw new Error('The backend returned an invalid submissions list. Please try again.');
  }
  return data;
}

export async function getSubmission(id, signal) {
  return validateDetail(await requestApi(`/api/submissions/${id}`, { signal }));
}

export async function gradeSubmission(assignmentId, name, files) {
  const body = new FormData();
  body.append('student_name', name.trim());
  files.forEach((file) => body.append('student_work', file));
  return validateDetail(await requestApi(`/api/assignments/${assignmentId}/submissions/grade`, { method: 'POST', body }));
}

export const deleteSubmission = (id) => requestApi(`/api/submissions/${id}`, { method: 'DELETE' });

export async function downloadAssignmentCsv(assignmentId) {
  const response = await fetch(`${API_URL}/api/assignments/${assignmentId}/export.csv`);
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'The CSV export failed. Please try again.');
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `assignment-${assignmentId}-results.csv`;
  return { blob: await response.blob(), filename };
}
