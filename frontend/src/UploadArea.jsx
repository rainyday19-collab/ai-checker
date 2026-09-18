import { useId, useRef, useState } from 'react';

const supportedTypes = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
};

function readableSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadArea({ title, description, file, onFileChange, disabled = false }) {
  const inputRef = useRef(null);
  const dragDepth = useRef(0);
  const errorId = useId();
  const [error, setError] = useState('');
  const [isDragging, setIsDragging] = useState(false);

  function selectFile(files) {
    if (disabled) return;
    if (!files.length) return;
    if (files.length !== 1) {
      setError('Please select one file at a time.');
      return;
    }
    const selected = files[0];
    const extension = selected.name.split('.').pop().toLowerCase();
    // File metadata is a frontend check; content validation belongs on the backend later.
    if (!supportedTypes[extension] || (selected.type && selected.type !== supportedTypes[extension])) {
      setError('Unsupported file. Please choose a PDF, PNG, or JPG/JPEG.');
      return;
    }
    setError('');
    onFileChange(selected);
  }

  function removeFile() {
    setError('');
    inputRef.current.value = '';
    onFileChange(null);
  }

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        disabled={disabled}
        hidden
        accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
        onChange={(event) => {
          selectFile(event.target.files);
          // Reset the picker so selecting the same file again triggers onChange.
          event.target.value = '';
        }}
      />
      <div
        className={`upload-area upload-interactive ${isDragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
        onDragEnter={(event) => {
          event.preventDefault();
          dragDepth.current += 1;
          if (!disabled) setIsDragging(true);
        }}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
        }}
        onDragLeave={(event) => {
          event.preventDefault();
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) setIsDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          dragDepth.current = 0;
          setIsDragging(false);
          selectFile(event.dataTransfer.files);
        }}
      >
        <button
          className="upload-picker"
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current.click()}
          aria-label={file ? `Change file: ${file.name}` : title}
          aria-describedby={error ? errorId : undefined}
        >
          <span className="upload-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5" strokeLinecap="round" strokeLinejoin="round"/></svg></span>
          <span className="upload-title">{file ? file.name : title}</span>
          <span className="upload-description">{file ? `${file.name.split('.').pop().toUpperCase()} · ${readableSize(file.size)}` : description}</span>
          {!file && <span className="format-badges"><span>PDF</span><span>PNG</span><span>JPG</span></span>}
          <span className="upload-preview">{file ? 'Click to change file, or drop a replacement' : 'Drag and drop, or click to browse'}</span>
        </button>
        {file && <button className="remove-file" type="button" disabled={disabled} onClick={removeFile} aria-label={`Remove ${file.name}`}>Remove file</button>}
      </div>
      {error && <p className="upload-error" id={errorId} role="alert">{error}{file && ' Your previous file is still selected.'}</p>}
    </div>
  );
}
