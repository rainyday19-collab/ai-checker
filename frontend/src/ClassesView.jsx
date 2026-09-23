import { useEffect, useRef, useState } from 'react';
import * as api from './classes.js';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage } from './assessment.js';
import TeacherForm from './TeacherForm.jsx';
import SubmissionsView from './SubmissionsView.jsx';
import { Breadcrumbs, ConfirmModal, EmptyState, LoadingState, PageHeader, StatusBadge, useToast } from './UI.jsx';

const date = (value) => new Date(value).toLocaleDateString();

export default function ClassesView({ onGradingChange }) {
  const go = useNavigate();
  const { classId: classParam, assignmentId: assignmentParam } = useParams();
  const assignmentId = assignmentParam == null ? null : Number(assignmentParam);
  const [items, setItems] = useState([]);
  const [selectedClass, setSelectedClass] = useState(null);
  const [assignment, setAssignment] = useState(null);
  const classId = classParam == null ? assignment?.class_id ?? null : Number(classParam);
  const [form, setForm] = useState(false);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [grading, setGrading] = useState(false);
  const [rubric, setRubric] = useState(null);
  const [rubricBusy, setRubricBusy] = useState(false);
  const [rubricError, setRubricError] = useState('');
  const [pendingDelete, setPendingDelete] = useState(null);
  const toast = useToast();
  const deleteInFlight = useRef(false);
  const busy = loading || deleting || grading || rubricBusy;

  useEffect(() => {
    setRubric(null);
    setRubricError('');
  }, [assignmentId]);

  function gradingChanged(value) {
    setGrading(value);
    onGradingChange(value);
  }

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    async function load() {
      try {
        if (assignmentParam != null) {
          if (!Number.isSafeInteger(assignmentId) || assignmentId <= 0) throw new Error('Assignment not found.');
          const item = await api.getAssignment(assignmentId, controller.signal);
          const parent = await api.getClass(item.class_id, controller.signal);
          if (!controller.signal.aborted) { setAssignment(item); setSelectedClass(parent); }
        } else if (classParam != null) {
          if (!Number.isSafeInteger(classId) || classId <= 0) throw new Error('Class not found.');
          const [parent, content] = await Promise.all([
            api.getClass(classId, controller.signal), api.getAssignments(classId, controller.signal),
          ]);
          if (!controller.signal.aborted) { setSelectedClass(parent); setItems(content); }
        } else {
          const classes = await api.getClasses(controller.signal);
          if (!controller.signal.aborted) setItems(classes);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    load();
    return () => controller.abort();
  }, [classParam, assignmentParam, refresh]);

  function navigate(nextClass = null, nextAssignment = null) {
    go(nextAssignment != null ? `/assignments/${nextAssignment}` : nextClass != null ? `/classes/${nextClass}` : '/classes');
  }

  async function viewRubric() {
    if (rubricBusy || assignmentId == null) return;
    setRubricBusy(true);
    setRubricError('');
    try { setRubric(await api.getAssignmentRubric(assignmentId)); }
    catch (failure) { setRubricError(errorMessage(failure)); }
    finally { setRubricBusy(false); }
  }

  async function retryRubric() {
    if (rubricBusy || assignmentId == null) return;
    setRubricBusy(true);
    setRubricError('');
    try {
      setRubric(await api.retryAssignmentRubric(assignmentId));
      toast('Grading rubric is ready.');
      setRefresh((value) => value + 1);
    } catch (failure) { setRubricError(errorMessage(failure)); }
    finally { setRubricBusy(false); }
  }

  async function remove(kind, id) {
    if (deleteInFlight.current) return;
    deleteInFlight.current = true;
    setDeleting(true);
    setError('');
    try {
      if (kind === 'class') await api.deleteClass(id);
      else await api.deleteAssignment(id);
      setPendingDelete(null);
      toast(`${kind === 'class' ? 'Class' : 'Assignment'} deleted.`);
      if (kind === 'class' && classId === id) navigate();
      else if (kind === 'assignment' && assignmentId === id) navigate(classId);
      else setRefresh((value) => value + 1);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      deleteInFlight.current = false;
      setDeleting(false);
    }
  }

  const detail = classParam != null || assignmentParam != null;
  const title = assignmentId != null ? assignment?.title : detail ? selectedClass?.name : 'Classes';
  const breadcrumbs = !detail ? [] : assignmentId != null ? [
    { label: 'Classes', to: '/classes' }, { label: selectedClass?.name || 'Class', to: classId ? `/classes/${classId}` : '/classes' }, { label: assignment?.title || 'Assignment' },
  ] : [{ label: 'Classes', to: '/classes' }, { label: selectedClass?.name || 'Class' }];
  const pageDescription = assignmentId != null
    ? `${selectedClass?.name || 'Class'}${assignment?.description ? ` · ${assignment.description}` : ''}`
    : detail ? selectedClass?.subject : 'Organize assignments and assessments by class.';

  return <div className="history-view classes-view">
    <Breadcrumbs items={breadcrumbs}/>
    <PageHeader eyebrow="TEACHER WORKSPACE" title={title || (error ? 'Could not open page' : 'Loading…')} description={pageDescription}
      action={!form && assignmentId == null ? <button className="primary-button" disabled={busy || Boolean(error)} onClick={() => setForm(true)}>Create {detail ? 'Assignment' : 'Class'}</button> : null}/>
    {error && <div className="card history-error"><p role="alert">{error}</p><button className="secondary-button" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Retry</button></div>}
    {form && <TeacherForm
      classId={classId}
      onCancel={() => setForm(false)}
      onCreated={() => { setForm(false); toast(`${detail ? 'Assignment' : 'Class'} created.`); setRefresh((value) => value + 1); }}
    />}
    {loading ? <LoadingState label={`Loading ${assignmentId != null ? 'assignment' : detail ? 'class' : 'classes'}`}/> : !error && <>
      {detail && selectedClass && <section className="card teacher-details">
        <div className="teacher-heading"><div><h2>{assignmentId != null ? 'Class' : 'Class details'}</h2><p>{selectedClass.name} · {selectedClass.subject}</p></div>
          {assignmentId == null && <button className="delete-button" disabled={deleting || form} onClick={() => setPendingDelete({ kind: 'class', id: classId, name: selectedClass.name })}>Delete Class</button>}
        </div>
        {selectedClass.description && <p className="teacher-copy">{selectedClass.description}</p>}
      </section>}
      {assignmentId != null && assignment ? <>
        <section className="card teacher-details">
          <div className="teacher-heading"><h2>Assignment details</h2><button className="delete-button" disabled={deleting || grading} onClick={() => setPendingDelete({ kind: 'assignment', id: assignmentId, name: assignment.title })}>Delete Assignment</button></div>
          <p className="teacher-copy">{assignment.description || 'No description added.'}</p>
          <p className="teacher-meta">Maximum marks determined during grading · Created {date(assignment.created_at)}</p>
          <div className="section-title-row"><h3>Mark Scheme</h3><StatusBadge tone={assignment.mark_scheme.has_file || assignment.mark_scheme.has_text ? 'success' : 'warning'}>{assignment.mark_scheme.has_file || assignment.mark_scheme.has_text ? 'Saved' : 'Missing'}</StatusBadge></div>
          {assignment.mark_scheme.has_file && <p className="teacher-copy file-summary"><strong>{assignment.mark_scheme.original_filename}</strong><span>{assignment.mark_scheme.type === 'pdf' ? 'PDF document' : assignment.mark_scheme.type === 'docx' ? 'Word document' : 'Image file'}</span></p>}
          {assignment.mark_scheme.has_text && <details><summary className="teacher-meta">Text criteria · Saved — expand to view</summary><p className="teacher-copy criteria-copy">{assignment.mark_scheme_text}</p></details>}
          {!assignment.mark_scheme.has_file && !assignment.mark_scheme.has_text && <p className="teacher-meta">No mark scheme added to this legacy assignment.</p>}
          <div className="section-title-row"><h3>Grading rubric</h3><StatusBadge tone={assignment.rubric.status === 'ready' ? 'success' : 'warning'}>{assignment.rubric.status === 'ready' ? 'Ready' : assignment.rubric.status === 'failed' ? 'Failed' : assignment.rubric.status === 'preparing' ? 'Preparing' : 'Unavailable'}</StatusBadge></div>
          {assignment.rubric.status === 'ready' ? <>
            <p className="teacher-meta">{assignment.rubric.total_marks} marks · {assignment.rubric.question_count} question{assignment.rubric.question_count === 1 ? '' : 's'} · {assignment.rubric.marking_point_count} marking point{assignment.rubric.marking_point_count === 1 ? '' : 's'}</p>
            <button className="secondary-button" type="button" disabled={rubricBusy} onClick={viewRubric}>{rubricBusy ? 'Loading…' : rubric ? 'Refresh rubric' : 'View rubric'}</button>
          </> : assignment.rubric.status === 'failed' ? <div className="rubric-failure"><p className="teacher-error" role="alert">{assignment.rubric.error || 'Rubric preparation failed.'}</p><button className="secondary-button" type="button" disabled={rubricBusy} onClick={retryRubric}>{rubricBusy ? 'Preparing…' : 'Retry rubric preparation'}</button></div> : <p className="teacher-meta">Rubric preparation must finish before grading.</p>}
          {rubricError && <p className="teacher-error" role="alert">{rubricError}</p>}
          {rubric && <section className="rubric-panel" aria-label="Canonical grading rubric">
            <div className="rubric-panel-heading"><strong>{rubric.title || 'Canonical rubric'}</strong><span>{rubric.total_marks} marks</span></div>
            {rubric.questions.map((question) => <div className="rubric-question" key={question.question_id}>
              <div><strong>{question.question_text}</strong><span>{question.max_marks} marks · {question.question_id}</span></div>
              <ul>{question.marking_points.map((point) => <li key={point.id}><span>{point.criterion}</span><small>{point.max_marks} mark{point.max_marks === 1 ? '' : 's'} · {point.criterion_type} · {point.id}</small>{point.guidance && <small>{point.guidance}</small>}</li>)}</ul>
            </div>)}
          </section>}
        </section>
        <SubmissionsView key={assignment.id} assignment={assignment} onBusyChange={gradingChanged}/>
      </> : <>
        {detail && <h2 className="teacher-section-title">Assignments</h2>}
        {!items.length ? <section className="card"><EmptyState title={detail ? 'No assignments yet' : 'No classes yet'} description={detail ? 'Create an assignment and add its Mark Scheme.' : 'Create your first class to organize assignments and grading.'} action={<button className="primary-button" type="button" onClick={() => setForm(true)}>Create {detail ? 'Assignment' : 'Class'}</button>}/></section> : <div className="teacher-card-grid">
          {items.map((item) => <article className="card teacher-item" key={item.id}>
            <button className="teacher-card-open" disabled={busy || form} onClick={() => detail ? navigate(classId, item.id) : navigate(item.id)}>
              <span className="teacher-item-title">{detail ? item.title : item.name}</span>
              <span>{detail ? 'Maximum marks determined during grading' : item.subject}</span>
              {detail ? <StatusBadge tone={item.mark_scheme.has_file || item.mark_scheme.has_text ? 'success' : 'warning'}>{item.mark_scheme.has_file || item.mark_scheme.has_text ? 'Mark Scheme saved' : 'Mark Scheme missing'}</StatusBadge> : <span className="teacher-meta">{item.assignment_count} assignment{item.assignment_count === 1 ? '' : 's'}</span>}
              <span className="teacher-meta">Created {date(item.created_at)}</span>
            </button>
            <div className="teacher-item-actions"><button className="delete-button" disabled={busy || form} onClick={() => setPendingDelete({ kind: detail ? 'assignment' : 'class', id: item.id, name: detail ? item.title : item.name })}>Delete</button></div>
          </article>)}
        </div>}
      </>}
    </>}
    <ConfirmModal open={Boolean(pendingDelete)} title={`Delete ${pendingDelete?.kind || 'item'}?`} description={pendingDelete?.kind === 'class' ? `This will delete “${pendingDelete.name}” and its assignments. Existing assessment history will be kept.` : `This will delete “${pendingDelete?.name}”. Existing assessment history will be kept.`} busy={deleting} onCancel={() => setPendingDelete(null)} onConfirm={() => remove(pendingDelete.kind, pendingDelete.id)}/>
  </div>;
}
