import { useState } from 'react';
import UploadArea from './UploadArea.jsx';
import ResultsPreview from './ResultsPreview.jsx';
import HistoryView from './HistoryView.jsx';
import Dashboard from './Dashboard.jsx';
import { API_URL, validateAssessmentResponse } from './assessment.js';

export default function App() {
  const [view, setView] = useState('dashboard');
  const [studentWork, setStudentWork] = useState(null);
  const [markScheme, setMarkScheme] = useState(null);
  const [criteria, setCriteria] = useState('');
  const [requestStatus, setRequestStatus] = useState('idle');
  const [requestError, setRequestError] = useState('');
  const [assessment, setAssessment] = useState(null);
  const [resetKey, setResetKey] = useState(0);
  const isLoading = requestStatus === 'loading';
  const canCheck = Boolean(studentWork && (markScheme || criteria.trim()));

  function updateFile(setFile, file) {
    setFile(file);
    setRequestStatus('idle');
    setRequestError('');
    setAssessment(null);
  }

  function resetAssessment() {
    setView('new');
    setStudentWork(null);
    setMarkScheme(null);
    setCriteria('');
    setAssessment(null);
    setRequestStatus('idle');
    setRequestError('');
    // Remount upload controls to clear their local validation errors and picker state.
    setResetKey((key) => key + 1);
    document.getElementById('workspace-title')?.focus();
  }

  async function checkWork() {
    if (!canCheck || isLoading) return;
    const formData = new FormData();
    formData.append('student_work', studentWork);
    if (markScheme) formData.append('mark_scheme', markScheme);
    if (criteria.trim()) formData.append('criteria_text', criteria.trim());
    setRequestStatus('loading');
    setRequestError('');
    setAssessment(null);
    try {
      // The browser sets the multipart Content-Type and boundary for FormData.
      const response = await fetch(`${API_URL}/api/assess`, { method: 'POST', body: formData });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(typeof data?.detail === 'string' ? data.detail : `The request failed (${response.status}). Check your files and criteria.`);
      }
      setAssessment(validateAssessmentResponse(data));
      setRequestStatus('success');
    } catch (error) {
      setRequestStatus('error');
      setRequestError(error instanceof TypeError ? 'Could not reach the backend. Make sure it is running on the configured API URL.' : error.message);
    }
  }

  const statusMessage = isLoading ? 'Sending files to the backend…'
    : requestStatus === 'success' ? 'Demo result saved to History. Uploaded work was not graded.'
    : requestStatus === 'error' ? requestError
    : canCheck ? 'Ready to check. Development mode returns a fixed demo result.'
    : 'Add student work and a mark scheme or pasted criteria to continue.';

  return (
    <>
      <header className="site-header"><div className="header-inner">
        <a className="brand" href="#" aria-label="AI Checker home"><span className="brand-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="4" y="3" width="16" height="18" rx="4"/><path d="m8 12 3 3 5-6" strokeLinecap="round" strokeLinejoin="round"/></svg></span><span>AI Checker</span></a>
        <nav aria-label="Main navigation">
          <button className={`nav-view ${view === 'dashboard' ? 'active' : ''}`} type="button" disabled={isLoading} aria-current={view === 'dashboard' ? 'page' : undefined} onClick={() => setView('dashboard')}>Dashboard</button>
          <button className={`nav-view ${view === 'new' ? 'active' : ''}`} type="button" disabled={isLoading} aria-current={view === 'new' ? 'page' : undefined} onClick={() => setView('new')}>New Assessment</button>
          <button className={`nav-view ${view === 'history' ? 'active' : ''}`} type="button" disabled={isLoading} aria-current={view === 'history' ? 'page' : undefined} onClick={() => setView('history')}>History</button>
          <span className="nav-badge"><span aria-hidden="true"/>AI-powered</span>
          <button className="icon-button" type="button" disabled aria-label="GitHub repository (coming soon)" title="GitHub repository coming soon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M9 19c-4 1-4-2-6-2m12 5v-4c0-1 .3-2 1-2.5 3-.4 5-1.5 5-5A4 4 0 0 0 20 7c.3-1 .3-2-.1-3-2 0-3 1-4 1.5a13 13 0 0 0-8 0C7 5 6 4 4 4c-.4 1-.4 2-.1 3A4 4 0 0 0 3 10.5c0 3.5 2 4.6 5 5 .7.5 1 1.5 1 2.5v4" strokeLinecap="round" strokeLinejoin="round"/></svg></button>
        </nav>
      </div></header>
      <main>
        {view === 'dashboard' ? <Dashboard onNewAssessment={resetAssessment} onHistory={() => setView('history')}/> : view === 'history' ? <HistoryView onNewAssessment={resetAssessment}/> : <>
        <section className="intro" aria-labelledby="page-title">
          <span className="hero-badge"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/></svg>AI-powered assessment</span>
          <h1 id="page-title">Grade smarter <span>with AI</span></h1>
          <p>Upload student work and a mark scheme. AI Checker analyzes each answer, awards marks, and explains the result.</p>
        </section>
        <section className="workspace card" aria-labelledby="workspace-title">
          <div className="workspace-heading"><div><span className="eyebrow">YOUR WORKSPACE</span><h2 id="workspace-title" tabIndex={-1}>New assessment</h2></div><span className="badge">Draft assessment</span></div>
          <p className="workflow-note" id="how-it-works">01 Add student work <span aria-hidden="true">/</span> 02 Provide a mark scheme <span aria-hidden="true">/</span> 03 Review the feedback</p>
          <div className="input-grid" key={resetKey}>
            <section className="input-panel" aria-labelledby="work-title">
              <div className="section-heading"><span className="step">01</span><div><h3 id="work-title">Student work</h3><p>The completed answers you want to review.</p></div></div>
              <UploadArea title="Drop student work here" description="An image or PDF of the completed work" file={studentWork} disabled={isLoading} onFileChange={(file) => updateFile(setStudentWork, file)}/>
              <p className="input-hint">Clear, readable pages help produce better feedback.</p>
            </section>
            <section className="input-panel" aria-labelledby="scheme-title">
              <div className="section-heading"><span className="step">02</span><div><h3 id="scheme-title">Mark scheme</h3><p>Set the standard for a fair assessment.</p></div></div>
              <UploadArea title="Drop your mark scheme here" description="An image or PDF of your grading criteria" file={markScheme} disabled={isLoading} onFileChange={(file) => updateFile(setMarkScheme, file)}/>
              <div className="or-divider"><span>OR</span></div>
              <div className="label-row"><label htmlFor="criteria">Paste grading criteria</label><span>Optional if uploading</span></div>
              <textarea id="criteria" rows="4" disabled={isLoading} value={criteria} onChange={(event) => { setCriteria(event.target.value); setRequestStatus('idle'); setRequestError(''); setAssessment(null); }} placeholder="Enter the expected answers, marks available, and any criteria for awarding partial credit…"/>
            </section>
          </div>
          <div className={`check-action request-${requestStatus}`}><p id="preview-note" role={requestStatus === 'error' ? 'alert' : 'status'}><span className="status-dot" aria-hidden="true"/>{statusMessage}</p><button className="primary-button" type="button" disabled={!canCheck || isLoading} onClick={checkWork} aria-busy={isLoading} aria-describedby="preview-note">{isLoading ? 'Sending…' : 'Check Work'} <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" strokeLinecap="round" strokeLinejoin="round"/></svg></button></div>
        </section>
        {assessment && <ResultsPreview assessment={assessment} onReset={resetAssessment}/>}
        </>}
      </main>
      <footer><span>AI Checker</span> Clear marks. Meaningful feedback.</footer>
    </>
  );
}
