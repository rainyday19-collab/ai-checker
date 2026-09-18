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

  const input = (name, title, maxLength, required = false) => <label className="teacher-field">
    {title}{required ? ' *' : ''}
    <input name={name} value={values[name]} onChange={update} required={required} maxLength={maxLength}/>
  </label>;

  return <section className="card teacher-form" aria-labelledby="create-title">
    <h2 id="create-title">Create {label}</h2>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        {assignment ? input('title', 'Title', 200, true) : <div className="teacher-form-grid">
          {input('name', 'Class name', 150, true)}{input('subject', 'Subject', 150, true)}
        </div>}
        <label className="teacher-field">Description<textarea name="description" value={values.description} onChange={update} rows="3" maxLength={5000}/></label>
        {assignment && <>
          <h3>Mark Scheme *</h3>
          <UploadArea title="Upload assignment mark scheme" description="PDF, PNG, or JPG/JPEG — saved for future submissions" file={schemeFile} onFileChange={setSchemeFile} disabled={busy}/>
          <div className="or-divider"><span>OR</span></div>
          <label className="teacher-field">Mark scheme / grading criteria<textarea name="mark_scheme_text" value={values.mark_scheme_text} onChange={update} rows="6" maxLength={50000} placeholder="Paste expected answers and criteria for awarding marks…"/></label>
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
