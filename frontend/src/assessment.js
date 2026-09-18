export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

export async function requestApi(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'The request failed. Please try again.');
  }
  if (data === null) throw new Error('The backend returned an invalid response. Please try again.');
  return data;
}

export function errorMessage(error) {
  return error instanceof TypeError ? 'Could not reach the backend. Make sure it is running.' : error.message;
}

export async function getSavedAssessment(id) {
  const data = validateAssessmentResponse(await requestApi(`/api/assessments/${id}`));
  if (data.id !== id || typeof data.student_filename !== 'string' || !Number.isFinite(Date.parse(data.created_at))) {
    throw new Error('The backend returned an invalid saved assessment. Please try again.');
  }
  return data;
}

export function validateAssessmentResponse(data) {
  const result = data?.result;
  const validScore = (score, maximum) => Number.isFinite(score) && Number.isFinite(maximum) && score >= 0 && maximum > 0 && score <= maximum;
  const validText = (text) => typeof text === 'string' && text.trim().length > 0;
  if (data?.usage != null && (!validText(data.usage.model) ||
      !['input_tokens', 'output_tokens', 'total_tokens'].every((key) => data.usage[key] == null ||
        (Number.isInteger(data.usage[key]) && data.usage[key] >= 0)))) {
    throw new Error('The backend returned invalid usage information. Please try again.');
  }
  if (!['mock', 'openai'].includes(data?.grading_mode) || !result ||
      !validScore(result.total_score, result.max_score) || !validText(result.summary) ||
      !Number.isFinite(result.percentage) || !Array.isArray(result.questions) || !result.questions.length ||
      !result.questions.every((item) => item && validText(item.question) && validText(item.feedback) &&
        validScore(item.score, item.max_score) && (item.evidence == null || typeof item.evidence === 'string'))) {
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
