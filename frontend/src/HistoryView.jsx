import { useEffect, useState } from 'react';
import { requestApi, errorMessage } from './assessment.js';
import { useNavigate } from 'react-router-dom';

export default function HistoryView({ onNewAssessment }) {
  const [items, setItems] = useState([]);
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const busy = loading || deleting !== null;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    requestApi('/api/assessments', { signal: controller.signal })
      .then((data) => {
        if (!Array.isArray(data) || !data.every((item) => item && Number.isInteger(item.id) && item.id > 0 &&
          typeof item.student_filename === 'string' && typeof item.created_at === 'string' &&
          Number.isFinite(Date.parse(item.created_at)) && Number.isFinite(item.total_score) &&
          Number.isFinite(item.max_score) && Number.isFinite(item.percentage))) {
          throw new Error('The backend returned an invalid history response. Please try again.');
        }
        if (!controller.signal.aborted) setItems(data);
      })
      .catch((failure) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  function openAssessment(id) { navigate(`/history/${id}`); }

  async function deleteAssessment(id) {
    setDeleting(id);
    setError('');
    try {
      await requestApi(`/api/assessments/${id}`, { method: 'DELETE' });
      setItems((previous) => previous.filter((item) => item.id !== id));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="history-view">
      <div className="history-page-heading"><div><span className="eyebrow">YOUR ASSESSMENTS</span><h1>Assessment history</h1><p>Revisit saved scores, feedback, and example evidence.</p></div></div>

        <section className="card history-card" aria-label="Saved assessments">
          <div className="workspace-heading"><h2>Saved assessments</h2><span className="badge">Local history</span></div>
          {error && <div className="history-error"><p role="alert">{error}</p><button className="secondary-button" type="button" disabled={busy} onClick={() => setRefresh((value) => value + 1)}>Retry history</button></div>}
          {loading ? <p className="history-state" role="status">Loading assessments…</p> : !error && !items.length ? (
            <div className="history-state"><h3>No assessments yet</h3><p>Check your first piece of work to save a demo result here.</p><button className="secondary-button" type="button" onClick={onNewAssessment}>New assessment</button></div>
          ) : (
            <ul className="history-list">
              {items.map((item) => (
                <li className="history-item" key={item.id}>
                  <div className="history-file"><h3>{item.student_filename}</h3><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></div>
                  <div className="history-score"><strong>{item.total_score} <span>/ {item.max_score}</span></strong><span>{item.percentage}%</span></div>
                  <div className="history-item-actions">
                    <button className="secondary-button" type="button" disabled={busy} onClick={() => openAssessment(item.id)} aria-label={`Open assessment ${item.id}: ${item.student_filename}`}>Open result</button>
                    <button className="delete-button" type="button" disabled={busy} onClick={() => deleteAssessment(item.id)} aria-label={`Delete assessment ${item.id}: ${item.student_filename}`}>{deleting === item.id ? 'Deleting…' : 'Delete'}</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
    </div>
  );
}
