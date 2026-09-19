import { useEffect, useState } from 'react';
import { requestApi, errorMessage } from './assessment.js';
import { useNavigate } from 'react-router-dom';
import { EmptyState, LoadingState, PageHeader, StatusBadge } from './UI.jsx';

const formatNumber = (value) => value === null ? '—' : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);

function validateStatistics(data) {
  const averages = ['average_score', 'average_max_score', 'average_percentage', 'highest_percentage'];
  if (!data || !Number.isInteger(data.total_assessments) || data.total_assessments < 0 ||
    !averages.every((key) => data[key] === null || (Number.isFinite(data[key]) && data[key] >= 0)) ||
    !Array.isArray(data.recent_assessments) || data.recent_assessments.length > 5 ||
    !data.recent_assessments.every((item) => item && Number.isInteger(item.id) && item.id > 0 &&
      typeof item.student_filename === 'string' && typeof item.created_at === 'string' && Number.isFinite(Date.parse(item.created_at)) &&
      Number.isFinite(item.total_score) && Number.isFinite(item.max_score) && item.total_score >= 0 && item.max_score > 0 && item.total_score <= item.max_score &&
      Number.isFinite(item.percentage) && item.percentage >= 0 && item.percentage <= 100)) {
    throw new Error('The backend returned invalid dashboard data. Please try again.');
  }
  return data;
}

export default function Dashboard({ onNewAssessment, onHistory }) {
  const [statistics, setStatistics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    requestApi('/api/statistics', { signal: controller.signal })
      .then(validateStatistics)
      .then((data) => { if (!controller.signal.aborted) setStatistics(data); })
      .catch((failure) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  function openAssessment(id) { navigate(`/history/${id}`); }

  const cards = statistics ? [
    { label: 'Total assessments', value: formatNumber(statistics.total_assessments), hint: 'Saved assessments' },
    { label: 'Average score', value: statistics.average_score === null ? '—' : `${formatNumber(statistics.average_score)} / ${formatNumber(statistics.average_max_score)}`, hint: 'Average awarded / available marks' },
    { label: 'Average percentage', value: statistics.average_percentage === null ? '—' : `${formatNumber(statistics.average_percentage)}%`, hint: 'Mean assessment percentage' },
    { label: 'Highest percentage', value: statistics.highest_percentage === null ? '—' : `${formatNumber(statistics.highest_percentage)}%`, hint: 'Best saved percentage' },
  ] : [];

  return (
    <div className="history-view dashboard-view">
      <PageHeader eyebrow="YOUR OVERVIEW" title="Dashboard" description="Scores and recent assessment activity at a glance." action={<StatusBadge tone="accent">Saved results</StatusBadge>}/>
      <>
        {error && <div className="history-error card"><p role="alert">{error}</p><button className="secondary-button" type="button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Retry dashboard</button></div>}
        {loading ? <LoadingState label="Loading dashboard"/> : statistics && <>
          <div className="statistics-grid">
            {cards.map((card) => <section className="card statistic-card" key={card.label} aria-label={card.label}><h2>{card.label}</h2><strong>{card.value}</strong><p>{card.hint}</p></section>)}
          </div>
          <section className="card history-card" aria-labelledby="recent-title">
            <div className="workspace-heading"><div><h2 id="recent-title">Recent assessments</h2><p className="dashboard-caption">Recent performance · latest five saved results</p></div><button className="secondary-button" type="button" onClick={onHistory}>View all history</button></div>
            {statistics.total_assessments === 0 ? <EmptyState title="Your first assessment starts here" description="Run an assessment to see saved results and statistics on your dashboard." action={<button className="primary-button dashboard-create" type="button" onClick={onNewAssessment}>New Assessment</button>}/> : <ul className="history-list">
              {statistics.recent_assessments.map((item) => <li className="history-item" key={item.id}>
                <div className="history-file"><h3>{item.student_filename}</h3><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></div>
                <div className="recent-performance"><div className="history-score"><strong>{item.total_score} <span>/ {item.max_score}</span></strong><span>{item.percentage}%</span></div><div className="performance-bar" aria-hidden="true"><span style={{ width: `${item.percentage}%` }}/></div></div>
                <button className="secondary-button" type="button" disabled={loading} onClick={() => openAssessment(item.id)} aria-label={`Open assessment ${item.id}: ${item.student_filename}`}>Open result</button>
              </li>)}
            </ul>}
            <p className="example-note">Statistics summarize saved assessments. Mock assessments remain labeled as demo results.</p>
          </section>
        </>}
      </>
    </div>
  );
}
