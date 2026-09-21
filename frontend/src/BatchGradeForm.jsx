import { useEffect, useRef, useState } from 'react';
import { errorMessage } from './assessment.js';
import * as api from './submissions.js';
import StudentWorkUpload from './StudentWorkUpload.jsx';
import { StatusBadge } from './UI.jsx';

const MAX_BATCH_STUDENTS = 20;

const makeStudent = (id) => ({
  id, name: '', files: [], status: 'waiting', result: null, error: '', validationError: '',
});

const statusLabel = { waiting: 'Waiting', grading: 'Grading…', completed: 'Completed', failed: 'Failed' };
const statusTone = { waiting: 'neutral', grading: 'warning', completed: 'success', failed: 'danger' };

export default function BatchGradeForm({ assignmentId, onBusyChange, onComplete, onClose, onOpenResult }) {
  const nextId = useRef(2);
  const runningRef = useRef(false);
  const mounted = useRef(true);
  const [students, setStudents] = useState(() => [makeStudent(1)]);
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [processed, setProcessed] = useState(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      onBusyChange(false);
    };
  }, []); // The form owns one continuous batch lifecycle until it unmounts.

  function updateStudent(id, changes) {
    setStudents((previous) => previous.map((student) => student.id === id ? { ...student, ...changes } : student));
  }

  function addStudent() {
    if (running || started || students.length >= MAX_BATCH_STUDENTS) return;
    const id = nextId.current++;
    setStudents((previous) => [...previous, makeStudent(id)]);
  }

  function removeStudent(id) {
    if (running || started || students.length === 1) return;
    setStudents((previous) => previous.filter((student) => student.id !== id));
  }

  function validateStudent(student) {
    if (!student.name.trim()) return 'Enter this student’s name.';
    if (!student.files.length) return 'Add this student’s work.';
    return '';
  }

  async function startBatch(event) {
    event.preventDefault();
    if (runningRef.current || started || students.length > MAX_BATCH_STUDENTS) return;
    const validation = new Map(students.map((student) => [student.id, validateStudent(student)]));
    setStudents((previous) => previous.map((student) => ({
      ...student, validationError: validation.get(student.id), error: '', status: 'waiting', result: null,
    })));
    if ([...validation.values()].some(Boolean)) return;

    const queue = students.map((student) => ({ ...student, name: student.name.trim(), files: [...student.files] }));
    runningRef.current = true;
    setStarted(true);
    setProcessed(0);
    setRunning(true);
    onBusyChange(true);
    try {
      for (let index = 0; index < queue.length; index += 1) {
        if (!mounted.current) break;
        const student = queue[index];
        updateStudent(student.id, { name: student.name, status: 'grading', error: '', validationError: '' });
        try {
          const result = await api.gradeSubmission(assignmentId, student.name, student.files);
          if (!mounted.current) break;
          // Release successful upload references; the saved submission retains metadata and result only.
          updateStudent(student.id, { status: 'completed', result, files: [], error: '' });
        } catch (failure) {
          if (!mounted.current) break;
          updateStudent(student.id, { status: 'failed', error: errorMessage(failure), result: null });
        }
        if (mounted.current) setProcessed(index + 1);
      }
      if (mounted.current) onComplete();
    } finally {
      runningRef.current = false;
      if (mounted.current) setRunning(false);
      onBusyChange(false);
    }
  }

  async function retry(student) {
    if (runningRef.current || student.status !== 'failed') return;
    const validationError = validateStudent(student);
    if (validationError) {
      updateStudent(student.id, { validationError });
      return;
    }
    runningRef.current = true;
    setRunning(true);
    onBusyChange(true);
    updateStudent(student.id, { status: 'grading', error: '', validationError: '' });
    try {
      const result = await api.gradeSubmission(assignmentId, student.name.trim(), student.files);
      if (!mounted.current) return;
      updateStudent(student.id, { name: student.name.trim(), status: 'completed', result, files: [], error: '' });
      onComplete();
    } catch (failure) {
      if (mounted.current) updateStudent(student.id, { status: 'failed', error: errorMessage(failure), result: null });
    } finally {
      runningRef.current = false;
      if (mounted.current) setRunning(false);
      onBusyChange(false);
    }
  }

  const readyCount = students.filter((student) => student.name.trim() && student.files.length).length;
  const successfulCount = students.filter((student) => student.status === 'completed').length;
  const failedCount = students.filter((student) => student.status === 'failed').length;
  const complete = started && !running && processed === students.length;
  const progress = students.length ? Math.round(processed / students.length * 100) : 0;

  return <section className="card teacher-form batch-form" aria-labelledby="batch-grade-title">
    <div className="batch-heading"><div><h3 id="batch-grade-title">Batch Grade</h3><p className="teacher-meta">Each student is graded separately and uses one AI grading request.</p></div><strong>{started ? `${students.length} students in batch` : `${readyCount} student${readyCount === 1 ? '' : 's'} ready`}</strong></div>
    {started && <div className="batch-progress" aria-live="polite">
      <div><strong>{complete ? 'Batch complete' : 'Grading students'}</strong><span>{processed} / {students.length}</span></div>
      <div className="batch-progress-track" role="progressbar" aria-label="Batch grading progress" aria-valuemin="0" aria-valuemax={students.length} aria-valuenow={processed}><span style={{ width: `${progress}%` }}/></div>
      {running && <p>Keep this page open while grading is in progress. Refreshing or closing may stop the remaining students.</p>}
    </div>}
    <form onSubmit={startBatch}>
      <ol className="batch-student-list">
        {students.map((student, index) => <li className={`batch-student-card batch-${student.status}`} key={student.id} aria-current={student.status === 'grading' ? 'step' : undefined}>
          <div className="batch-card-heading"><div><span>Student {index + 1}{student.name.trim() ? ` · ${student.name.trim()}` : ''}</span><StatusBadge tone={statusTone[student.status]}>{statusLabel[student.status]}</StatusBadge></div>
            {!started && <button type="button" className="delete-button" disabled={running || students.length === 1} onClick={() => removeStudent(student.id)} aria-label={`Remove student ${index + 1}`}>Remove student</button>}
          </div>
          {student.status !== 'completed' && <>
            <label className="teacher-field batch-name-field"><span>Student name <span className="required-mark" aria-hidden="true">*</span></span><input maxLength={150} disabled={running} value={student.name} onChange={(event) => updateStudent(student.id, { name: event.target.value, validationError: '' })} placeholder="For example, Alice Johnson"/></label>
            <StudentWorkUpload files={student.files} onFilesChange={(files) => updateStudent(student.id, { files, validationError: '' })} disabled={running}/>
          </>}
          {student.validationError && <p className="teacher-error" role="alert">{student.validationError}</p>}
          {student.status === 'failed' && <div className="batch-result batch-failed-result"><p role="alert">{student.error || 'Grading failed. No result was saved.'}</p><button className="secondary-button" type="button" disabled={running} onClick={() => retry(student)}>Retry</button></div>}
          {student.status === 'completed' && student.result && <div className="batch-result"><div><strong>{student.result.total_score} / {student.result.max_score}</strong><span>{student.result.percentage}%</span></div><button className="secondary-button" type="button" disabled={running} onClick={() => onOpenResult(student.result.id)}>View Result</button></div>}
        </li>)}
      </ol>
      {!started && <button className="secondary-button batch-add-student" type="button" disabled={running || students.length >= MAX_BATCH_STUDENTS} onClick={addStudent}>+ Add another student</button>}
      {!started && students.length >= MAX_BATCH_STUDENTS && <p className="teacher-meta">Maximum batch size reached: 20 students.</p>}
      {complete && <div className="batch-summary" role="status"><strong>Batch complete</strong><span>{students.length} students processed · {successfulCount} successful · {failedCount} failed</span></div>}
      <div className="teacher-actions batch-footer-actions"><button type="button" className="secondary-button" disabled={running} onClick={onClose}>{complete ? 'Close batch' : 'Cancel'}</button>
        {!started && <button type="submit" className="primary-button" disabled={running || readyCount !== students.length} aria-busy={running}>{`Grade ${students.length} Student${students.length === 1 ? '' : 's'}`}</button>}</div>
    </form>
  </section>;
}
