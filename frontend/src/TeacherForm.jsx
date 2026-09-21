import { useRef, useState } from 'react';
import { createClass, createAssignment } from './classes.js';
import { errorMessage } from './assessment.js';
import UploadArea from './UploadArea.jsx';

export default function TeacherForm({ classId, onCreated, onCancel }) {
  const assignment = classId != null;
  const [values, setValues] = useState({ name: '', subject: '', title: '', description: '', mark_scheme_text: '' });
  const [schemeFile, setSchemeFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);
  const label = assignment ? 'Assignment' : 'Class';

  function update(event) {
    setValues((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  }

  async function submit(event) {
    event.preventDefault();
    if (submitting.current) return;
    if (!(assignment ? values.title.trim() : values.name.trim() && values.subject.trim())) {
      setError('Complete the required fields.');
      return;
    }
    if (assignment && !schemeFile && !values.mark_scheme_text.trim()) {
      setError('Upload a mark scheme or paste grading criteria.');
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError('');
    try {
      const description = values.description.trim() || null;
      if (assignment) {
        await createAssignment(classId, { title: values.title.trim(), description,
          mark_scheme_text: values.mark_scheme_text.trim() || null }, schemeFile);
      } else {
        await createClass({ name: values.name.trim(), subject: values.subject.trim(), description });
      }
      onCreated();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  const input = (name, title, maxLength, required = false, helper = '') => <label className="teacher-field">
    <span>{title}{required ? <span className="required-mark" aria-hidden="true"> *</span> : null}</span>
    <input name={name} value={values[name]} onChange={update} required={required} maxLength={maxLength}/>
    {helper && <small>{helper}</small>}
  </label>;

  return <section className="card teacher-form" aria-labelledby="create-title">
    <h2 id="create-title">Create {label}</h2>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        {assignment ? input('title', 'Title', 200, true) : <div className="teacher-form-grid">
          {input('name', 'Class name', 150, true, 'For example, Year 11 Computer Science.')}{input('subject', 'Subject', 150, true, 'Shown on class and assignment pages.')}
        </div>}
        <label className="teacher-field"><span>Description <span className="optional-label">Optional</span></span><textarea name="description" value={values.description} onChange={update} rows="3" maxLength={5000} placeholder={`Add context about this ${label.toLowerCase()}…`}/><small>Keep this concise; teachers will see it near the page title.</small></label>
        {assignment && <>
          <h3>Mark Scheme <span className="required-mark" aria-hidden="true">*</span></h3>
          <UploadArea title="Upload assignment mark scheme" description="PDF, DOCX, PNG, or JPG/JPEG — saved for future submissions" file={schemeFile} onFileChange={setSchemeFile} disabled={busy}/>
          <div className="or-divider"><span>OR</span></div>
          <label className="teacher-field"><span>Mark Scheme / grading criteria</span><textarea name="mark_scheme_text" value={values.mark_scheme_text} onChange={update} rows="6" maxLength={50000} placeholder="Paste expected answers and criteria for awarding marks…"/><small>Use this instead of an upload, or to add complementary criteria.</small></label>
          <p className="teacher-meta">Maximum marks determined during grading. If providing both sources, use complementary criteria.</p>
        </>}
      </fieldset>
      {error && <p className="teacher-error" role="alert">{error}</p>}
      <div className="teacher-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>Cancel</button>
        <button type="submit" className="primary-button" disabled={busy}>{busy ? 'Creating…' : `Create ${label}`}</button>
      </div>
    </form>
  </section>;
}
