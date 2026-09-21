import { useEffect, useRef, useState } from 'react';
import * as api from './submissions.js';
import { errorMessage } from './assessment.js';
import StudentWorkUpload from './StudentWorkUpload.jsx';
import { useLocation, useNavigate } from 'react-router-dom';
import { ConfirmModal, EmptyState, LoadingState, StatusBadge, useToast } from './UI.jsx';

export default function SubmissionsView({ assignment, onBusyChange }) {
  const [items, setItems] = useState([]);
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState(Boolean(location.state?.grade));
  const [name, setName] = useState('');
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [grading, setGrading] = useState(false);
  const [working, setWorking] = useState(null);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [exporting, setExporting] = useState(false);
  const toast = useToast();
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const canGrade = Boolean(assignment.mark_scheme?.has_file || assignment.mark_scheme_text?.trim());
  const gradedCount = items.filter((item) => item.status === 'graded' && item.assessment_id != null).length;
  const busy = grading || working != null || exporting;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api.getSubmissions(assignment.id, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setItems(data); })
      .catch((failure) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [assignment.id, refresh]);

  function showForm() {
    setName('');
    setFiles([]);
    setFormError('');
    setForm(true);
  }

  async function grade(event) {
    event.preventDefault();
    if (inFlight.current || !name.trim() || !files.length || !canGrade) return;
    inFlight.current = true;
    setGrading(true);
    onBusyChange(true);
    setFormError('');
    try {
      const data = await api.gradeSubmission(assignment.id, name, files);
      // Browser Back may leave this page while the backend finishes grading.
      if (!mounted.current) return;
      navigate(`/submissions/${data.id}`);
      setForm(false);
      setName('');
      setFiles([]);
      setRefresh((value) => value + 1);
    } catch (failure) {
      setFormError(errorMessage(failure));
    } finally {
      inFlight.current = false;
      setGrading(false);
      onBusyChange(false);
    }
  }

  function open(id) { navigate(`/submissions/${id}`); }

  async function exportCsv() {
    if (busy || !gradedCount) return;
    setExporting(true);
    try {
      const { blob, filename } = await api.downloadAssignmentCsv(assignment.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      toast('Assignment results exported.');
    } catch (failure) {
      toast(errorMessage(failure), 'error');
    } finally {
      setExporting(false);
    }
  }

  async function remove(id) {
    if (busy) return;
    setWorking(id);
    setError('');
    try {
      await api.deleteSubmission(id);
      setItems((previous) => previous.filter((item) => item.id !== id));
      setPendingDelete(null);
      toast('Submission deleted.');
    } catch (failure) { setError(errorMessage(failure)); }
    finally { setWorking(null); }
  }

  return <section className="submissions-view" aria-labelledby="submissions-title">
    <div className="teacher-heading"><div><h2 id="submissions-title">Student submissions</h2><p className="teacher-meta">{items.length} saved submission{items.length === 1 ? '' : 's'}</p></div>
      {!form && <div className="submission-actions"><button className="secondary-button" type="button" disabled={busy || loading || gradedCount === 0} title={gradedCount === 0 ? 'Export is available after a submission has been graded.' : 'Download graded results as CSV'} onClick={exportCsv}>{exporting ? 'Exporting…' : 'Export CSV'}</button><button className="primary-button" disabled={busy || !canGrade} onClick={showForm}>+ Grade Student Work</button></div>}
    </div>
    {!canGrade && <p className="teacher-meta">This assignment has no saved mark scheme. Create an assignment with criteria before grading.</p>}
    {!loading && !error && gradedCount === 0 && <p className="teacher-meta export-hint">Export CSV becomes available after the first successfully graded Submission.</p>}
    <>
      {form && <section className="card teacher-form"><h3>Grade Student Work</h3><p className="teacher-meta">Using the mark scheme saved with this assignment.</p>
        <form onSubmit={grade}>
          <label className="teacher-field"><span>Student name <span className="required-mark" aria-hidden="true">*</span></span><input required maxLength={150} disabled={grading} value={name} onChange={(event) => setName(event.target.value)} placeholder="For example, Alice Johnson"/><small>Used to identify this saved Submission.</small></label>
          <StudentWorkUpload files={files} onFilesChange={setFiles} disabled={grading}/>
          {formError && <p className="teacher-error" role="alert">{formError}</p>}
          {grading && <p className="teacher-meta" role="status">Grading student work… Keep this page open.</p>}
          <div className="teacher-actions"><button type="button" className="secondary-button" disabled={grading} onClick={() => setForm(false)}>Cancel</button>
            <button type="submit" className="primary-button" disabled={grading || !canGrade || !name.trim() || !files.length} aria-busy={grading}>{grading ? 'Grading…' : 'Grade Work'}</button></div>
        </form>
      </section>}
      <div className="card history-card submissions-card">
        {error && <div className="history-error"><p role="alert">{error}</p><button className="secondary-button" disabled={busy || loading} onClick={() => setRefresh((value) => value + 1)}>Retry list</button></div>}
        {loading ? <LoadingState label="Loading submissions"/> : !error && !items.length ? <EmptyState title="No submissions yet" description="Grade the first Student Work using this assignment’s saved Mark Scheme." action={!form && canGrade ? <button className="primary-button" type="button" onClick={showForm}>Grade Student Work</button> : null}/> :
          <ul className="history-list">{items.map((item) => <li className="history-item" key={item.id}>
            <div className="history-file"><h3>{item.student_name}</h3><p className="teacher-meta submission-filename" title={item.original_filenames.join(', ')}>{item.page_count > 1 ? `${item.page_count} image pages` : item.original_filename}</p><time>{item.graded_at ? new Date(item.graded_at).toLocaleString() : 'Not graded'}</time></div>
            <div className="history-score"><strong>{item.assessment_id == null ? '—' : `${item.total_score} / ${item.max_score}`}</strong><span>{item.percentage == null ? 'Result unavailable' : `${item.percentage}%`}</span><StatusBadge tone={item.assessment_id != null && item.status === 'graded' ? 'success' : 'warning'}>{item.assessment_id == null && item.status === 'graded' ? 'Result removed' : item.status === 'graded' ? 'Graded' : item.status}</StatusBadge></div>
            <div className="history-item-actions"><button className="secondary-button" disabled={busy || form} onClick={() => open(item.id)}>{working === item.id ? 'Working…' : 'Open result'}</button>
              <button className="delete-button" disabled={busy || form} onClick={() => setPendingDelete(item)}>Delete</button></div>
          </li>)}</ul>}
      </div>
      <ConfirmModal open={Boolean(pendingDelete)} title="Delete submission?" description={`This will remove ${pendingDelete?.student_name || 'this student'}’s submission from the assignment. Its assessment will remain in History.`} busy={working != null} onCancel={() => setPendingDelete(null)} onConfirm={() => remove(pendingDelete.id)}/>
    </>
  </section>;
}
