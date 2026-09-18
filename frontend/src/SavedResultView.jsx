import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage, getSavedAssessment } from './assessment.js';
import { getSubmission } from './submissions.js';
import { getAssignment } from './classes.js';
import ResultsPreview from './ResultsPreview.jsx';

export default function SavedResultView({ submission = false, onNewAssessment }) {
  const params = useParams();
  const navigate = useNavigate();
  const id = Number(submission ? params.submissionId : params.assessmentId);
  const [record, setRecord] = useState(null);
  const [assignment, setAssignment] = useState(null);
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
        if (!controller.signal.aborted) { setRecord(result); setAssignment(parent); }
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
    {loading ? <section className="card history-state" role="status">Loading saved result…</section> : error ?
      <section className="card history-state"><h1>Could not open result</h1><p role="alert">{error}</p><div className="teacher-actions"><button className="secondary-button" onClick={back}>Back to {submission ? 'Classes' : 'History'}</button><button className="secondary-button" onClick={() => setRefresh((value) => value + 1)}>Retry</button></div></section> : <>
        {submission && <section className="card teacher-details"><h1>{record.student_name}</h1><p className="teacher-copy">{assignment.title} · {record.original_filename}</p></section>}
        {assessment ? <ResultsPreview assessment={assessment} saved onBack={back} backLabel={submission ? 'Back to Assignment' : 'Back to History'} onReset={reset}/> :
          <section className="card history-state"><p>The linked assessment was removed from History.</p><button className="secondary-button" onClick={back}>Back to Assignment</button></section>}
      </>}
  </div>;
}
