import { requestApi } from './assessment.js';

export const getClasses = (signal) => requestApi('/api/classes', { signal });
export const getClass = (id, signal) => requestApi(`/api/classes/${id}`, { signal });
export const getAssignments = (classId, signal) => requestApi(`/api/classes/${classId}/assignments`, { signal });
export const getAssignment = (id, signal) => requestApi(`/api/assignments/${id}`, { signal });

const post = (path, values) => requestApi(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values),
});
export const createClass = (values) => post('/api/classes', values);
export const createAssignment = (classId, values, file) => {
  const body = new FormData();
  body.append('title', values.title);
  if (values.description) body.append('description', values.description);
  if (values.mark_scheme_text) body.append('mark_scheme_text', values.mark_scheme_text);
  if (file) body.append('mark_scheme_file', file);
  return requestApi(`/api/classes/${classId}/assignments`, { method: 'POST', body });
};
export const deleteClass = (id) => requestApi(`/api/classes/${id}`, { method: 'DELETE' });
export const deleteAssignment = (id) => requestApi(`/api/assignments/${id}`, { method: 'DELETE' });
