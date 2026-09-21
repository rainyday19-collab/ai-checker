import { useId, useRef, useState } from 'react';

const supportedTypes = { pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg' };

function readableSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function kind(file) {
  const extension = file.name.split('.').pop()?.toLowerCase();
  if (!supportedTypes[extension] || (file.type && file.type !== supportedTypes[extension])) return null;
  return extension === 'pdf' ? 'pdf' : 'image';
}

export default function StudentWorkUpload({ files, onFilesChange, disabled = false }) {
  const pickerRef = useRef(null);
  const cameraRef = useRef(null);
  const dragDepth = useRef(0);
  const errorId = useId();
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);

  function add(selectedFiles) {
    if (disabled || !selectedFiles?.length) return;
    const incoming = Array.from(selectedFiles);
    const kinds = incoming.map(kind);
    if (kinds.some((value) => value == null)) {
      setError('Unsupported file. Choose one PDF or PNG/JPG image pages.');
      return;
    }
    const combined = [...files, ...incoming];
    const combinedKinds = combined.map(kind);
    if (combinedKinds.includes('pdf') && combined.length > 1) {
      setError(combinedKinds.every((value) => value === 'pdf')
        ? 'Only one PDF can be graded at a time.'
        : 'A PDF cannot be combined with image pages.');
      return;
    }
    if (combined.length > 10) {
      setError('Add at most 10 image pages.');
      return;
    }
    setError('');
    onFilesChange(combined);
  }

  function remove(index) {
    setError('');
    onFilesChange(files.filter((_, itemIndex) => itemIndex !== index));
  }

  function move(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= files.length) return;
    const reordered = [...files];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    onFilesChange(reordered);
  }

  return <div className="student-work-upload">
    <input ref={pickerRef} type="file" hidden multiple disabled={disabled}
      accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
      onChange={(event) => { add(event.target.files); event.target.value = ''; }}/>
    <input ref={cameraRef} type="file" hidden disabled={disabled} accept="image/png,image/jpeg" capture="environment"
      onChange={(event) => { add(event.target.files); event.target.value = ''; }}/>
    <div className={`upload-area multi-upload-area ${dragging ? 'is-dragging' : ''}`}
      onDragEnter={(event) => { event.preventDefault(); dragDepth.current += 1; if (!disabled) setDragging(true); }}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = disabled ? 'none' : 'copy'; }}
      onDragLeave={(event) => { event.preventDefault(); dragDepth.current -= 1; if (dragDepth.current <= 0) setDragging(false); }}
      onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); add(event.dataTransfer.files); }}>
      <span className="upload-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5" strokeLinecap="round" strokeLinejoin="round"/></svg></span>
      <strong className="upload-title">Student work pages</strong>
      <span className="upload-description">One PDF, or up to 10 ordered PNG/JPG images</span>
      <div className="upload-actions">
        <button className="secondary-button" type="button" disabled={disabled} onClick={() => pickerRef.current?.click()}>Choose files</button>
        <button className="secondary-button" type="button" disabled={disabled || files.some((file) => kind(file) === 'pdf')} onClick={() => cameraRef.current?.click()}>Take photo</button>
      </div>
      <span className="upload-preview">Drag and drop files here, or add more image pages in order</span>
    </div>
    {error && <p className="upload-error" id={errorId} role="alert">{error}</p>}
    {files.length > 0 && <ol className="upload-page-list" aria-label="Selected student work pages" aria-describedby={error ? errorId : undefined}>
      {files.map((file, index) => <li key={`${file.name}-${file.size}-${file.lastModified}-${index}`}>
        <div className="upload-page-details"><strong>{kind(file) === 'pdf' ? 'PDF' : `Page ${index + 1}`}</strong><span>{file.name}</span><small>{file.name.split('.').pop()?.toUpperCase()} · {readableSize(file.size)}</small></div>
        <div className="upload-page-actions">
          {kind(file) === 'image' && <><button type="button" disabled={disabled || index === 0} onClick={() => move(index, -1)} aria-label={`Move ${file.name} up`}>Move up</button><button type="button" disabled={disabled || index === files.length - 1} onClick={() => move(index, 1)} aria-label={`Move ${file.name} down`}>Move down</button></>}
          <button type="button" disabled={disabled} onClick={() => remove(index)} aria-label={`Remove ${file.name}`}>Remove</button>
        </div>
      </li>)}
    </ol>}
  </div>;
}
