import { createContext, useContext, useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  function notify(message, tone = 'success') {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 4200);
  }
  return <ToastContext.Provider value={notify}>
    {children}
    <div className="toast-region" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => <div className={`toast toast-${toast.tone}`} role="status" key={toast.id}>
        <span className="toast-dot" aria-hidden="true"/><span>{toast.message}</span>
        <button type="button" onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))} aria-label="Dismiss notification">×</button>
      </div>)}
    </div>
  </ToastContext.Provider>;
}

export const useToast = () => useContext(ToastContext);

export function PageHeader({ eyebrow, title, description, action }) {
  return <header className="page-header">
    <div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1>{description && <p>{description}</p>}</div>
    {action && <div className="page-header-action">{action}</div>}
  </header>;
}

export function Breadcrumbs({ items }) {
  if (!items?.length) return null;
  return <nav className="breadcrumbs" aria-label="Breadcrumb">
    <ol>{items.map((item, index) => <li key={`${item.label}-${index}`}>
      {item.to ? <Link to={item.to}>{item.label}</Link> : <span aria-current="page">{item.label}</span>}
    </li>)}</ol>
  </nav>;
}

export function LoadingState({ label = 'Loading' }) {
  return <div className="loading-state card" role="status" aria-label={label}>
    <span className="spinner" aria-hidden="true"/><span>{label}</span>
    <div className="skeleton-lines" aria-hidden="true"><i/><i/><i/></div>
  </div>;
}

export function EmptyState({ title, description, action }) {
  return <div className="empty-state">
    <span className="empty-state-icon" aria-hidden="true">✓</span>
    <h3>{title}</h3><p>{description}</p>{action}
  </div>;
}

export function StatusBadge({ children, tone = 'neutral' }) {
  return <span className={`status-badge status-${tone}`}><span aria-hidden="true"/>{children}</span>;
}

export function ConfirmModal({ open, title, description, confirmLabel = 'Delete', busy = false, onCancel, onConfirm }) {
  const titleId = useId();
  const cancelRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    cancelRef.current?.focus();
    const close = (event) => { if (event.key === 'Escape' && !busy) onCancel(); };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [open, busy, onCancel]);
  if (!open) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel(); }}>
    <div className="confirmation-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <span className="modal-danger-icon" aria-hidden="true">!</span>
      <h2 id={titleId}>{title}</h2><p>{description}</p>
      <div className="modal-actions"><button ref={cancelRef} className="secondary-button" type="button" disabled={busy} onClick={onCancel}>Cancel</button><button className="danger-button" type="button" disabled={busy} onClick={onConfirm}>{busy ? 'Deleting…' : confirmLabel}</button></div>
    </div>
  </div>;
}
