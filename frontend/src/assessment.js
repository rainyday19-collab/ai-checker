export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

export async function requestApi(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : 'The request failed. Please try again.');
    error.status = response.status;
    error.endpoint = path;
    throw error;
  }
  if (data === null) throw new Error('The backend returned an invalid response. Please try again.');
  return data;
}

export function errorMessage(error) {
  return error instanceof TypeError ? 'Could not reach the backend. Make sure it is running.' : error.message;
}

export async function getSavedAssessment(id, signal) {
  const data = validateAssessmentResponse(await requestApi(`/api/assessments/${id}`, { signal }));
  if (data.id !== id || typeof data.student_filename !== 'string' || !Number.isFinite(Date.parse(data.created_at))) {
    throw new Error('The backend returned an invalid saved assessment. Please try again.');
  }
  return data;
}

export function validateAssessmentResponse(data) {
  const result = data?.result;
  const validScore = (score, maximum) => Number.isFinite(score) && Number.isFinite(maximum) && score >= 0 && maximum > 0 && score <= maximum;
  const validText = (text) => typeof text === 'string' && text.trim().length > 0;
  const validMarkingPoint = (point) => {
    if (!point || !validText(point.criterion) || !validText(point.rationale) ||
        !['met', 'partially_met', 'not_met', 'unclear'].includes(point.status) ||
        !['found', 'not_found', 'unclear'].includes(point.evidence_status) ||
        !validScore(point.awarded_marks, point.max_marks) ||
        (point.evidence != null && typeof point.evidence !== 'string') ||
        (point.source_location != null && typeof point.source_location !== 'string')) return false;
    if (point.status === 'met' && point.awarded_marks !== point.max_marks) return false;
    if (point.status === 'partially_met' && !(point.awarded_marks > 0 && point.awarded_marks < point.max_marks)) return false;
    if (point.status === 'not_met' && point.awarded_marks !== 0) return false;
    if (point.status === 'unclear' && (point.evidence_status !== 'unclear' || point.awarded_marks === point.max_marks)) return false;
    if (point.evidence_status === 'not_found') return point.status === 'not_met' && point.awarded_marks === 0;
    if (point.evidence_status === 'unclear' && point.status !== 'unclear') return false;
    if (!validText(point.evidence) || !validText(point.source_location)) return false;
    return point.awarded_marks === 0 || point.evidence_status === 'found';
  };
  const validMarkingPoints = (item) => {
    if (item.marking_points == null) return true;
    if (!Array.isArray(item.marking_points) || !item.marking_points.length ||
        !item.marking_points.every(validMarkingPoint)) return false;
    const awarded = item.marking_points.reduce((sum, point) => sum + point.awarded_marks, 0);
    const maximum = item.marking_points.reduce((sum, point) => sum + point.max_marks, 0);
    return Math.abs(awarded - item.score) <= 1e-6 && Math.abs(maximum - item.max_score) <= 1e-6;
  };
  const validEvidence = (item) => {
    if (item.evidence_status == null) {
      return item.source_location == null && (item.evidence == null || typeof item.evidence === 'string');
    }
    if (!['found', 'not_found', 'unclear'].includes(item.evidence_status) ||
        (item.evidence != null && typeof item.evidence !== 'string') ||
        (item.source_location != null && typeof item.source_location !== 'string')) return false;
    if (item.evidence_status === 'not_found') return item.score === 0;
    if (!validText(item.evidence) || !validText(item.source_location)) return false;
    return item.evidence_status !== 'unclear' || item.score < item.max_score;
  };
  if (data?.usage != null && (!validText(data.usage.model) ||
      !['input_tokens', 'output_tokens', 'total_tokens'].every((key) => data.usage[key] == null ||
        (Number.isInteger(data.usage[key]) && data.usage[key] >= 0)))) {
    throw new Error('The backend returned invalid usage information. Please try again.');
  }
  if (!['mock', 'openai'].includes(data?.grading_mode) || !result ||
      !validScore(result.total_score, result.max_score) || !validText(result.summary) ||
      !Number.isFinite(result.percentage) || !Array.isArray(result.questions) || !result.questions.length ||
      !result.questions.every((item) => item && validText(item.question) && validText(item.feedback) &&
        validScore(item.score, item.max_score) && validEvidence(item) && validMarkingPoints(item))) {
    throw new Error('The backend returned an invalid assessment. Please try again.');
  }
  const total = result.questions.reduce((sum, item) => sum + item.score, 0);
  const maximum = result.questions.reduce((sum, item) => sum + item.max_score, 0);
  const percentage = Math.round(result.total_score / result.max_score * 10000) / 100;
  if (Math.abs(total - result.total_score) > 1e-6 || Math.abs(maximum - result.max_score) > 1e-6 || Math.abs(percentage - result.percentage) > 0.01) {
    throw new Error('The assessment scores are inconsistent. Please try again.');
  }
  return data;
}
