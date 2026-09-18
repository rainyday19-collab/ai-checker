import { requestApi, validateAssessmentResponse } from './assessment.js';

function validateDetail(data) {
  if (!data || !Number.isInteger(data.id) || typeof data.student_name !== 'string' ||
      typeof data.original_filename !== 'string' || !Number.isInteger(data.assignment_id)) {
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
      typeof item.original_filename === 'string' && ['pending', 'graded', 'failed'].includes(item.status))) {
    throw new Error('The backend returned an invalid submissions list. Please try again.');
  }
  return data;
}

export async function getSubmission(id, signal) {
  return validateDetail(await requestApi(`/api/submissions/${id}`, { signal }));
}

export async function gradeSubmission(assignmentId, name, file) {
  const body = new FormData();
  body.append('student_name', name.trim());
  body.append('student_work', file);
  return validateDetail(await requestApi(`/api/assignments/${assignmentId}/submissions/grade`, { method: 'POST', body }));
}

export const deleteSubmission = (id) => requestApi(`/api/submissions/${id}`, { method: 'DELETE' });
