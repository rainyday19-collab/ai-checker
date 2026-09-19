import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage, getSavedAssessment } from './assessment.js';
import { getSubmission } from './submissions.js';
import { getAssignment, getClass } from './classes.js';
import ResultsPreview from './ResultsPreview.jsx';
import { Breadcrumbs, LoadingState, PageHeader, StatusBadge } from './UI.jsx';

export default function SavedResultView({ submission = false, onNewAssessment }) {
  const params = useParams();
  const navigate = useNavigate();
  const id = Number(submission ? params.submissionId : params.assessmentId);
  const [record, setRecord] = useState(null);
  const [assignment, setAssignment] = useState(null);
  const [parentClass, setParentClass] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    async function load() {
      try {
        if (!Number.isSafeInteger(id) || id <= 0) throw new Error('Result not found.');
        const result = submission ? await getSubmission(id, controller.signal) : await getSavedAssessment(id, controller.signal);
        const parent = submission ? await getAssignment(result.assignment_id, controller.signal) : null;
        const classRecord = submission ? await getClass(parent.class_id, controller.signal) : null;
        if (!controller.signal.aborted) { setRecord(result); setAssignment(parent); setParentClass(classRecord); }
      } catch (failure) {
        if (!controller.signal.aborted) setError(errorMessage(failure));
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    load();
    return () => controller.abort();
  }, [id, submission, refresh]);

  const back = () => navigate(submission && record ? `/assignments/${record.assignment_id}` : submission ? '/classes' : '/history');
  const assessment = submission ? record?.assessment : record;
  const reset = () => submission ? navigate(`/assignments/${record.assignment_id}`, { state: { grade: true } }) : onNewAssessment();

  return <div className="history-view">
    {loading ? <LoadingState label="Loading saved result"/> : error ?
      <section className="card history-state"><h1>Could not open result</h1><p role="alert">{error}</p><div className="teacher-actions"><button className="secondary-button" onClick={back}>Back to {submission ? 'Classes' : 'History'}</button><button className="secondary-button" onClick={() => setRefresh((value) => value + 1)}>Retry</button></div></section> : <>
        {submission && <><Breadcrumbs items={[{ label: 'Classes', to: '/classes' }, { label: parentClass.name, to: `/classes/${parentClass.id}` }, { label: assignment.title, to: `/assignments/${assignment.id}` }, { label: record.student_name }]}/>
          <PageHeader eyebrow="SUBMISSION RESULT" title={record.student_name} description={`${assignment.title} · ${parentClass.name}`} action={<StatusBadge tone="success">Graded</StatusBadge>}/>
          <section className="card result-context" aria-label="Submission details"><div><span>Student</span><strong>{record.student_name}</strong></div><div><span>Assignment</span><strong>{assignment.title}</strong></div><div><span>Class</span><strong>{parentClass.name}</strong></div><div><span>Filename</span><strong title={record.original_filename}>{record.original_filename}</strong></div></section>
        </>}
        {assessment ? <ResultsPreview assessment={assessment} saved onBack={back} backLabel={submission ? 'Back to Assignment' : 'Back to History'} onReset={reset}/> :
          <section className="card history-state"><p>The linked assessment was removed from History.</p><button className="secondary-button" onClick={back}>Back to Assignment</button></section>}
      </>}
  </div>;
}
