import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertTriangle, ArrowLeft, CheckCircle2, Edit3, FileCheck2, FileText, FolderOpen, Info, Search, ShieldCheck, Upload } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { inspectIntakeEsr, listIntakeScenarios, type EsrInspectionResult } from '../data/repositories/workspaceApi';

export function IntakeImport({ esrId, onReceived }: {
  esrId?: string;
  onReceived: (id: string) => void;
}) {
  const { importIntakeDocument, receiveIntakeExample } = useWorkspace();
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [files, setFiles] = useState<File[]>([]);
  const [folderName, setFolderName] = useState('');
  const [dragActive, setDragActive] = useState(false);
  const [synthetic, setSynthetic] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState<{ id: string; title: string }[]>([]);
  const [scenario, setScenario] = useState('');
  const [inspection, setInspection] = useState<EsrInspectionResult | null>(null);
  const [inspecting, setInspecting] = useState(false);

  // Editable fields on Step 2
  const [confirmedName, setConfirmedName] = useState('');
  const [confirmedContentions, setConfirmedContentions] = useState('');
  const [confirmedInstructions, setConfirmedInstructions] = useState('');

  useEffect(() => {
    if (esrId) return;
    let active = true;
    void listIntakeScenarios().then(({ items }) => {
      if (!active) return;
      setScenarios(items);
      setScenario(items[0]?.id ?? '');
    }).catch(() => {});
    return () => { active = false; };
  }, [esrId]);

  function handleFolderChange(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length === 0) return;
    const validExts = ['.pdf', '.xml', '.txt', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.zip'];
    const validFiles = selected.filter(f => validExts.some(ext => f.name.toLowerCase().endsWith(ext)));
    if (validFiles.length === 0) {
      setError('No supported medical documents (.pdf, .xml, .txt, images, .zip) were found in the selected folder.');
      return;
    }
    setFiles(validFiles);
    setFile(validFiles[0]);
    const relPath = (validFiles[0] as unknown as { webkitRelativePath?: string }).webkitRelativePath || '';
    const detectedDir = relPath.includes('/') ? relPath.split('/')[0] : 'Medical Folder';
    setFolderName(detectedDir);
    setSynthetic(false);
    setScenario('');
    setInspection(null);
    setError('');
  }

  function handleFilesChange(event: React.ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length === 0) return;
    const validExts = ['.pdf', '.xml', '.txt', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.zip'];
    const validFiles = selected.filter(f => validExts.some(ext => f.name.toLowerCase().endsWith(ext)));
    if (validFiles.length === 0) {
      setError('No supported medical documents (.pdf, .xml, .txt, images, .zip) were found.');
      return;
    }
    setFiles(validFiles);
    setFile(validFiles[0]);
    setFolderName(validFiles.length > 1 ? 'Selected Files' : '');
    setSynthetic(false);
    setScenario('');
    setInspection(null);
    setError('');
  }

  async function readEntryRecursively(entry: any, outFiles: File[]) {
    if (entry.isFile) {
      await new Promise<void>((resolve) => {
        entry.file((f: File) => {
          outFiles.push(f);
          resolve();
        }, () => resolve());
      });
    } else if (entry.isDirectory) {
      const dirReader = entry.createReader();
      const readAllEntries = async () => {
        const entries: any[] = await new Promise((resolve) => {
          dirReader.readEntries((ents: any[]) => resolve(ents), () => resolve([]));
        });
        if (entries.length > 0) {
          for (const child of entries) {
            await readEntryRecursively(child, outFiles);
          }
          await readAllEntries();
        }
      };
      await readAllEntries();
    }
  }

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (busy || inspecting) return;

    const items = e.dataTransfer.items;
    const extractedFiles: File[] = [];
    let detectedFolder = '';

    if (items && items.length > 0) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const entry = (item as any).webkitGetAsEntry ? (item as any).webkitGetAsEntry() : null;
        if (entry) {
          if (entry.isDirectory && !detectedFolder) {
            detectedFolder = entry.name;
          }
          await readEntryRecursively(entry, extractedFiles);
        } else {
          const f = item.getAsFile();
          if (f) extractedFiles.push(f);
        }
      }
    } else if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      extractedFiles.push(...Array.from(e.dataTransfer.files));
    }

    const validExts = ['.pdf', '.xml', '.txt', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.zip'];
    const validFiles = extractedFiles.filter(f => validExts.some(ext => f.name.toLowerCase().endsWith(ext)));

    if (validFiles.length === 0) {
      setError('No supported medical documents (.pdf, .xml, .txt, images, .zip) found in dropped items.');
      return;
    }

    setFiles(validFiles);
    setFile(validFiles[0]);
    setFolderName(detectedFolder || (validFiles.length > 1 ? 'Dropped Package' : ''));
    setSynthetic(false);
    setScenario('');
    setInspection(null);
    setError('');
  }

  async function handleInspect(fromScenario?: boolean) {
    setError('');
    setInspecting(true);
    const body = new FormData();
    if (fromScenario && scenario) {
      body.set('scenario_id', scenario);
    } else if (file) {
      body.set('file', file);
      if (files.length > 0) {
        files.forEach(f => body.append('files', f));
      }
    } else {
      setInspecting(false);
      return;
    }
    body.set('synthetic', 'true');

    try {
      const result = await inspectIntakeEsr(body);
      setInspection(result);
      setConfirmedName(result.claimantName || '');
      setConfirmedContentions((result.contentions || []).map(c => c.title).join('\n'));
      setConfirmedInstructions(result.specialInstructions || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not inspect request payload.');
    } finally {
      setInspecting(false);
    }
  }

  async function handleConfirmIngest() {
    if (!inspection) return;
    setBusy(true);
    setError('');
    try {
      if (scenario && !file) {
        const id = await receiveIntakeExample(scenario);
        onReceived(id);
      } else if (file || files.length > 0) {
        const body = new FormData();
        const primaryFile = file || files[0];
        body.set('file', primaryFile);
        if (files.length > 0) {
          files.forEach(f => {
            if (f !== primaryFile && !(f.name === primaryFile.name && f.size === primaryFile.size)) {
              body.append('files', f);
            }
          });
        }
        body.set('synthetic', 'true');
        body.set('name', confirmedName.trim() || inspection.claimantName);
        body.set('contentions', confirmedContentions.trim());
        body.set('special_instructions', confirmedInstructions.trim());
        body.set('service_branch', inspection.serviceBranch);
        body.set('esr_reference', inspection.esrReference);

        const receivedId = await importIntakeDocument(body, undefined);
        onReceived(receivedId);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The ESR could not be queued.');
    } finally {
      setBusy(false);
    }
  }

  // Direct attachment for existing ESR
  async function submitDirectAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if ((!file && files.length === 0) || !synthetic) return;
    const body = new FormData();
    const primaryFile = file || files[0];
    body.set('file', primaryFile);
    if (files.length > 0) {
      files.forEach(f => {
        if (f !== primaryFile && !(f.name === primaryFile.name && f.size === primaryFile.size)) {
          body.append('files', f);
        }
      });
    }
    body.set('synthetic', String(synthetic));
    setBusy(true);
    setError('');
    try {
      const received = await importIntakeDocument(body, esrId);
      if (fileInput.current) fileInput.current.value = '';
      if (folderInput.current) folderInput.current.value = '';
      setFile(undefined);
      setFiles([]);
      setFolderName('');
      setSynthetic(false);
      onReceived(received);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The document could not be queued.');
    } finally {
      setBusy(false);
    }
  }

  // Case 1: Attaching source document or folder to an already open case
  if (esrId) {
    return <form className="intake-import" onSubmit={submitDirectAttach}>
      <input
        ref={folderInput}
        type="file"
        multiple
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        style={{ display: 'none' }}
        disabled={busy}
        onChange={handleFolderChange}
      />
      <input
        ref={fileInput}
        type="file"
        multiple
        accept=".pdf,.txt,.xml,.zip,.png,.jpg,.jpeg,.tiff,.bmp,.tif"
        style={{ display: 'none' }}
        disabled={busy}
        onChange={handleFilesChange}
      />
      <div className="source-picker">
        <span className="source-picker-label">Attach authorized source document(s) or folder</span>
        <div
          className={`source-picker-control ${dragActive ? 'drag-active' : ''}`}
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(true); }}
          onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(false); }}
          onDrop={handleDrop}
        >
          <span className="source-picker-icon">{folderName ? <FolderOpen size={19} /> : file ? <FileText size={19} /> : <Upload size={19} />}</span>
          <span className="source-picker-copy">
            <b>{folderName ? `Medical Folder: ${folderName} (${files.length} files)` : files.length > 1 ? `${files.length} medical files selected` : (file?.name ?? 'Choose medical folder or file(s)')}</b>
            <small>{file ? `Ready to attach for OCR & LangGraph ingestion · ${files.slice(0, 3).map(f => f.name).join(', ')}${files.length > 3 ? ` (+${files.length - 3} more)` : ''}` : 'Drop a folder or files here, or use the buttons below (.pdf, .zip, scanned images)'}</small>
            <div className="source-picker-actions-row">
              <button
                type="button"
                className="source-picker-btn"
                disabled={busy}
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); folderInput.current?.click(); }}
              >
                <FolderOpen size={14} /> Select Folder
              </button>
              <button
                type="button"
                className="source-picker-btn"
                disabled={busy}
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); fileInput.current?.click(); }}
              >
                <Upload size={14} /> Select File(s) / ZIP
              </button>
            </div>
          </span>
        </div>
      </div>
      {file && <label className="intake-synthetic-confirmation">
        <input type="checkbox" checked={synthetic} disabled={busy} onChange={event => setSynthetic(event.target.checked)} />
        I confirm this selection contains no real claimant records.
      </label>}
      {error && <p className="intake-import-error" role="alert">{error}</p>}
      <div className="intake-import-actions">
        <button type="submit" className="intake-primary" disabled={busy || !file || !synthetic}>
          <Upload size={16} />
          {busy ? 'Uploading & Processing…' : `Add ${files.length > 1 ? `${files.length} source documents` : 'source document'}`}
        </button>
      </div>
    </form>;
  }

  // Case 2: New ESR Step 2: Review Extracted Details (Editable) & Confirm
  if (inspection) {
    return <div className="intake-import inspection-confirm-mode">
      <div className="inspection-header">
        <span className="inspection-step-pill">Step 2: Review & Edit Extracted Details</span>
        <h4>Confirm New ESR Intake</h4>
        <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--intake-muted)' }}>
          Details were automatically extracted from your uploaded records. You can review or modify any field before starting AI ingestion.
        </p>
      </div>

      {inspection.isDuplicate ? (
        <div className="intake-duplicate-banner" role="alert">
          <AlertTriangle size={20} className="duplicate-alert-icon" />
          <div className="duplicate-alert-body">
            <strong>Duplicate Case or File Detected</strong>
            <p>{inspection.duplicateReason}</p>
            {inspection.existingEsrId && (
              <button type="button" className="intake-secondary view-existing-btn" onClick={() => onReceived(inspection.existingEsrId!)}>
                Open Existing Case ({inspection.existingEsrId})
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="intake-verified-banner">
          <ShieldCheck size={18} />
          <span>Duplicate check passed. No conflicting active ESR or duplicate file hash found in queue.</span>
        </div>
      )}

      {/* Extracted Claimant & Request Details Card (With In-Place Editing) */}
      <div className="inspection-card">
        <div className="inspection-card-row">
          <div>
            <span className="inspection-label">ESR Identifier</span>
            <span className="inspection-mono">{inspection.esrReference}</span>
          </div>
          <div>
            <span className="inspection-label">Benefit Program</span>
            <span>{inspection.benefitProgram}</span>
          </div>
          <div>
            <span className="inspection-label">Service Branch</span>
            <span>{inspection.serviceBranch}</span>
          </div>
        </div>

        {/* Editable Claimant Name */}
        <label className="intake-field" style={{ margin: 0 }}>
          <span className="inspection-label" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <Edit3 size={12} /> Claimant Name (Auto-Extracted · Editable)
          </span>
          <input
            value={confirmedName}
            onChange={e => setConfirmedName(e.target.value)}
            disabled={busy}
            style={{ fontWeight: 700, fontSize: '14.5px', color: '#105b4a' }}
            required
          />
        </label>

        {/* Editable Contentions */}
        <label className="intake-field" style={{ margin: 0 }}>
          <span className="inspection-label" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <FileCheck2 size={12} /> Contentions (One condition per line · Editable)
          </span>
          <textarea
            value={confirmedContentions}
            onChange={e => setConfirmedContentions(e.target.value)}
            rows={3}
            disabled={busy}
            style={{ fontSize: '12.5px', lineHeight: 1.4 }}
            required
          />
        </label>

        {/* Editable VA Special Instructions */}
        <label className="intake-field" style={{ margin: 0 }}>
          <span className="inspection-label" style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
            <Info size={12} /> VA Special Instructions (Auto-Extracted · Editable)
          </span>
          <textarea
            value={confirmedInstructions}
            onChange={e => setConfirmedInstructions(e.target.value)}
            rows={3}
            disabled={busy}
            style={{ fontSize: '12px', lineHeight: 1.4 }}
          />
        </label>

        {/* File & Source Meta */}
        <div className="inspection-meta-footer">
          <span>Payload: <b>{inspection.fileType}</b></span>
          <span>Total size: <b>{(inspection.fileSize / 1024).toFixed(1)} KB</b></span>
          {inspection.fileCount && <span>Files: <b>{inspection.fileCount}</b></span>}
          {inspection.pageCount && <span>Pages: <b>{inspection.pageCount}</b></span>}
        </div>
      </div>

      {error && <p className="intake-import-error" role="alert">{error}</p>}

      <div className="intake-import-actions confirmation-actions">
        <button type="button" className="intake-secondary" onClick={() => setInspection(null)} disabled={busy}>
          <ArrowLeft size={16} /> Change File / Folder
        </button>
        <button
          type="button"
          className="intake-primary"
          onClick={handleConfirmIngest}
          disabled={busy || inspection.isDuplicate || !confirmedName.trim() || !confirmedContentions.trim()}
          title={inspection.isDuplicate ? 'Duplicate ESR creation blocked' : 'Confirm and begin AI intake'}
        >
          <CheckCircle2 size={16} />
          {busy ? 'Processing with LangGraph…' : 'Confirm & Ingest into Intake Agent'}
        </button>
      </div>
    </div>;
  }

  // Case 3: New ESR Step 1: Select Request Source (Zero manual typing required!)
  return <div className="intake-import">
    <div className="inspection-header">
      <span className="inspection-step-pill">Step 1: Select Request Source</span>
      <h4>Upload Medical Data or Select EMS Request</h4>
      <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--intake-muted)' }}>
        Select a medical folder or documents. Claimant name, contentions, and instructions will be automatically extracted.
      </p>
    </div>

    {scenarios.length > 0 && <div className="intake-bundle-picker">
      <label className="intake-field">
        <span>Bundled EMS Request Scenario</span>
        <select value={scenario} disabled={busy || inspecting} onChange={event => {
          setScenario(event.target.value);
          setFile(undefined);
          setFiles([]);
          setFolderName('');
        }}>
          {scenarios.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>
      </label>
      <button
        type="button"
        className="intake-secondary"
        onClick={() => handleInspect(true)}
        disabled={busy || inspecting || !scenario}
      >
        <Search size={15} />
        {inspecting ? 'Extracting…' : 'Extract & Preview'}
      </button>
    </div>}

    <div className="intake-or-divider"><span>OR UPLOAD MEDICAL FOLDER / FILES</span></div>

    <input
      ref={folderInput}
      type="file"
      multiple
      {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
      style={{ display: 'none' }}
      disabled={busy || inspecting}
      onChange={handleFolderChange}
    />
    <input
      ref={fileInput}
      type="file"
      multiple
      accept=".xml,.pdf,.zip,.txt,.png,.jpg,.jpeg,.tiff,.bmp,.tif"
      style={{ display: 'none' }}
      disabled={busy || inspecting}
      onChange={handleFilesChange}
    />

    <div className="source-picker">
      <span className="source-picker-label">Upload Medical Folder, Exam Management XML, PDF, or Scanned Images</span>
      <div
        className={`source-picker-control ${dragActive ? 'drag-active' : ''}`}
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(true); }}
        onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(false); }}
        onDrop={handleDrop}
      >
        <span className="source-picker-icon">{folderName ? <FolderOpen size={19} /> : file ? <FileText size={19} /> : <Upload size={19} />}</span>
        <span className="source-picker-copy">
          <b>
            {folderName ? (
              <>Medical Folder: {folderName} ({files.length} file{files.length === 1 ? '' : 's'})</>
            ) : files.length > 1 ? (
              <>{files.length} medical files selected</>
            ) : (
              file?.name ?? 'Choose medical folder or file(s)'
            )}
          </b>
          <small>
            {file ? (
              `${files.length > 1 ? `${files.length} records selected` : file.name} · Click below to auto-extract claimant details & contentions`
            ) : (
              'Drag and drop a folder or file(s) here, or select using the buttons below (.pdf, .xml, .zip, scanned images)'
            )}
          </small>
          <div className="source-picker-actions-row">
            <button
              type="button"
              className="source-picker-btn"
              disabled={busy || inspecting}
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); folderInput.current?.click(); }}
            >
              <FolderOpen size={14} /> Select Medical Folder
            </button>
            <button
              type="button"
              className="source-picker-btn"
              disabled={busy || inspecting}
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); fileInput.current?.click(); }}
            >
              <Upload size={14} /> Select File(s) / ZIP
            </button>
          </div>
        </span>
      </div>
    </div>

    {file && <label className="intake-synthetic-confirmation">
      <input type="checkbox" checked={synthetic} disabled={busy || inspecting} onChange={event => setSynthetic(event.target.checked)} />
      I confirm this upload contains synthetic test data and no real PII/PHI.
    </label>}

    {error && <p className="intake-import-error" role="alert">{error}</p>}

    <div className="intake-import-actions">
      <button
        type="button"
        className="intake-primary"
        onClick={() => handleInspect(false)}
        disabled={busy || inspecting || !file || !synthetic}
      >
        <Search size={16} />
        {inspecting ? 'Extracting Details & Checking Duplicates…' : 'Extract Details & Check Duplicates'}
      </button>
    </div>
  </div>;
}
