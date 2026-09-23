export default function ResultsPreview({ assessment, onReset, saved = false, onBack, backLabel = 'Back to History' }) {
  const { result, grading_mode: mode } = assessment;
  const isDemo = mode === 'mock';
  const evidenceStatusLabel = { found: 'Found', not_found: 'Not found', unclear: 'Unclear' };
  const pointStatusLabel = { met: 'Met', partially_met: 'Partly met', not_met: 'Not met', unclear: 'Unclear' };
  return (
    <section className="results card" aria-labelledby="results-title">
      <div className="results-heading">
        <div><span className="eyebrow">{saved ? 'SAVED ASSESSMENT' : 'FEEDBACK AT A GLANCE'}</span><h2 id="results-title">Assessment results</h2><p>{saved ? `${assessment.student_filename} · ${new Date(assessment.created_at).toLocaleString()}` : 'Scores, feedback, and supporting evidence.'}</p></div>
        {isDemo && <span className="badge example-badge">Demo result</span>}
      </div>
      <div className="score-summary">
        <div className="score-total"><span className="score-label">Total score</span><div className="score">{result.total_score}<span> / {result.max_score}</span></div></div>
        <div className="score-context">
          <div className="performance-heading"><strong>{isDemo ? 'Example assessment' : 'Assessment score'}</strong><span>{result.percentage}%</span></div>
          <div className="score-track" role="progressbar" aria-label="Assessment percentage" aria-valuenow={result.percentage} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${result.percentage}%` }}/></div>
          <p>{result.summary}</p>
        </div>
        <div className="criteria-count"><strong>{result.questions.length}</strong><span>{isDemo ? 'example criteria' : 'criteria assessed'}</span></div>
      </div>
      <div className="question-list">
        {result.questions.map((item, index) => (
          <article className="question-row" key={`${index}-${item.question}`} aria-labelledby={`question-${index}`}>
            <span className={`question-icon ${item.score === item.max_score ? 'full-marks' : ''}`} aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m6 12 4 4 8-8" strokeLinecap="round" strokeLinejoin="round"/></svg></span>
            <div className="question-content">
              <div className="question-heading"><h3 id={`question-${index}`}>{item.question}</h3><span className="mark-pill" aria-label={`${item.score} of ${item.max_score} marks awarded`}>{item.score} <span>/ {item.max_score}</span></span></div>
              <p className="feedback result-feedback"><span>Feedback</span>{item.feedback}</p>
              {item.marking_points?.length ? <details className="marking-points">
                <summary>Marking points ({item.marking_points.length})</summary>
                <div className="marking-point-list">
                  {item.marking_points.map((point, pointIndex) => <div className={`marking-point marking-point-${point.status}`} key={`${pointIndex}-${point.criterion}`}>
                    <div className="marking-point-heading"><span className="marking-point-symbol" aria-hidden="true">{point.status === 'met' ? '✓' : point.status === 'partially_met' ? '◐' : point.status === 'unclear' ? '?' : '✕'}</span><strong>{point.criterion}</strong><span>{point.awarded_marks} / {point.max_marks}</span></div>
                    <p><span>Status</span>{pointStatusLabel[point.status]}</p>
                    <p><span>Rationale</span>{point.rationale}</p>
                    <p><span>Evidence status</span>{evidenceStatusLabel[point.evidence_status]}</p>
                    {point.evidence && <p><span>{isDemo ? 'Example evidence' : 'Evidence'}</span>{point.evidence}</p>}
                    {point.source_location && <p><span>Source</span>{point.source_location}</p>}
                  </div>)}
                </div>
              </details> : <>
                {item.evidence_status && <p className="feedback result-evidence"><span>Evidence status</span>{evidenceStatusLabel[item.evidence_status]}</p>}
                {item.evidence && <p className="feedback result-evidence"><span>{isDemo ? 'Example evidence' : 'Evidence'}</span>{item.evidence}</p>}
                {item.source_location && <p className="feedback result-evidence"><span>Source</span>{item.source_location}</p>}
              </>}
            </div>
          </article>
        ))}
      </div>
      {isDemo && <p className="example-note">Fixed development data. Your files were validated and processed, but their answers were not analyzed or graded.</p>}
      {assessment.usage && <details className="ai-usage">
        <summary>AI usage</summary>
        <dl>
          <div><dt>Model</dt><dd>{assessment.usage.model}</dd></div>
          <div><dt>Input tokens</dt><dd>{assessment.usage.input_tokens ?? 'Unavailable'}</dd></div>
          <div><dt>Output tokens</dt><dd>{assessment.usage.output_tokens ?? 'Unavailable'}</dd></div>
          <div><dt>Total tokens</dt><dd>{assessment.usage.total_tokens ?? 'Unavailable'}</dd></div>
        </dl>
      </details>}
      <div className="result-actions">{onBack && <button className="secondary-button" type="button" onClick={onBack}>{backLabel}</button>}<button className="primary-button" type="button" onClick={onReset}>{saved ? 'Grade Another Work' : 'Check Another Work'}</button></div>
    </section>
  );
}
