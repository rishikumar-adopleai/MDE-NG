import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, FileText, Upload } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { inspectIntakeEsr, listIntakeScenarios, type EsrInspectionResult } from '../data/repositories/workspaceApi';

const extensions = ['.pdf', '.xml', '.txt', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.tif', '.zip'];

async function collectFiles(entry: FileSystemEntry): Promise<File[]> {
  if (entry.isFile) return [await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))];
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const files: File[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return files;
    for (const child of batch) files.push(...await collectFiles(child));
  }
}

function packageBody(files: File[]) {
  const body = new FormData();
  files.forEach((file, index) => body.append(index === 0 ? 'file' : 'files', file));
  body.set('synthetic', 'true');
  return body;
}

export function IntakeImport({ esrId, onReceived, onCancel }: {
  esrId?: string; onReceived: (id: string) => void; onCancel: () => void;
}) {
  const { importIntakeDocument, receiveIntakeExample } = useWorkspace();
  const [files, setFiles] = useState<File[]>([]);
  const [attested, setAttested] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [pending, setPending] = useState('');
  const [dragging, setDragging] = useState(false);
  const [scenarios, setScenarios] = useState<{id: string; title: string}[]>([]);
  const [scenario, setScenario] = useState('');
  const [inspection, setInspection] = useState<EsrInspectionResult>();
  const [name, setName] = useState('');
  const [contentions, setContentions] = useState('');
  const [instructions, setInstructions] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const chooser = useRef<HTMLInputElement>(null);
  const busy = Boolean(pending);
  const sourceControlled = Boolean(scenario || inspection?.examManagementRequest);

  useEffect(() => {
    heading.current?.focus();
  }, [inspection]);
  useEffect(() => {
    if (esrId) return;
    let active = true;
    void listIntakeScenarios().then(({items}) => { if (active) setScenarios(items); }).catch(() => {});
    return () => { active = false; };
  }, [esrId]);

  function selectFiles(selection: File[]) {
    setScenario(''); setAttested(false); setError(''); setNotice('');
    const supported = selection.filter(file => extensions.some(ext => file.name.toLowerCase().endsWith(ext)));
    if (new Set(supported.map(file => file.name)).size !== supported.length) {
      setFiles([]); setError('Two selected files have the same name. Rename one or attach them separately so neither is skipped.'); return;
    }
    setFiles(supported);
    if (!supported.length) setError('No supported files found. Choose documents or a ZIP package.');
    else if (supported.length < selection.length) setNotice(`${selection.length - supported.length} unsupported file(s) skipped.`);
  }

  async function handleDrop(event: DragEvent) {
    event.preventDefault(); setDragging(false);
    if (busy) return;
    const fallback = Array.from(event.dataTransfer.files);
    const sources = Array.from(event.dataTransfer.items).filter(item => item.kind === 'file').map(item => ({
      entry: item.webkitGetAsEntry?.(), file: item.getAsFile(),
    }));
    setPending('Reading selection…');
    try {
      const files = sources.length ? (await Promise.all(sources.map(({entry, file}) => entry ? collectFiles(entry) : file ? [file] : []))).flat() : fallback;
      selectFiles(files);
    } catch { selectFiles([]); setError('The folder could not be read. Choose individual files or a ZIP instead.'); }
    finally { setPending(''); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || (!scenario && (!files.length || !attested)) || inspection?.isDuplicate) return;
    setError('');
    setPending(esrId ? 'Attaching documents…' : inspection ? 'Creating request…' : 'Reading request and checking for duplicates…');
    try {
      const body = packageBody(files);
      if (esrId) onReceived(await importIntakeDocument(body, esrId));
      else if (!inspection) {
        if (scenario) body.set('scenario_id', scenario);
        const result = await inspectIntakeEsr(body);
        setName(result.claimantName || ''); setContentions(result.contentions.map(item => item.title).join('\n'));
        setInstructions(result.specialInstructions || ''); setInspection(result);
      } else if (scenario) onReceived(await receiveIntakeExample(scenario));
      else {
        body.set('name', name.trim()); body.set('contentions', contentions.trim()); body.set('special_instructions', instructions.trim());
        body.set('service_branch', inspection.serviceBranch); body.set('esr_reference', inspection.esrReference);
        onReceived(await importIntakeDocument(body, undefined, inspection.examManagementRequest));
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not complete this step. Your selection is still here.'); }
    finally { setPending(''); }
  }

  return <section className="intake-receive" aria-labelledby="receive-title">
    <header><span className="intake-kicker">{esrId ? 'Case documents' : `New ESR · Step ${inspection ? 2 : 1} of 2`}</span>
      <h2 id="receive-title" tabIndex={-1} ref={heading}>{esrId ? 'Add source documents' : inspection ? 'Review request details' : 'Receive an exam request'}</h2>
      <p>{esrId ? 'Attach supporting records to this case.' : inspection ? (sourceControlled ? 'These details come from the original request and are read-only. Change the source if they are incorrect.' : 'Check the claimant and requested conditions before creating the case.') : 'Choose the request and its supporting records. You’ll review the extracted details before a case is created.'}</p>
    </header>
    <form onSubmit={submit}>
      {!inspection ? <>
        <div className={`intake-dropzone${dragging ? ' dragging' : ''}`} onDrop={handleDrop}
          onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)}>
          <Upload size={24} aria-hidden="true" /><h3>Drop files or a folder here</h3>
          <p>PDF, exam request XML, text, scanned images or a ZIP package</p>
          <input ref={chooser} type="file" multiple hidden accept={extensions.join(',')} disabled={busy} onChange={event => {
            const selected = Array.from(event.target.files ?? []); event.target.value = ''; if (selected.length) selectFiles(selected);
          }} /><button type="button" className="intake-secondary" disabled={busy} onClick={() => chooser.current?.click()}>{files.length ? 'Choose different files' : 'Choose files or ZIP'}</button>
        </div>
        {files.length > 0 && <><details className="intake-selection"><summary>{files.length} {files.length === 1 ? 'file' : 'files'} selected</summary>
          <ul>{files.map(file => <li key={file.name}><FileText size={15} />{file.name}</li>)}</ul></details>
          <label className="intake-synthetic-confirmation"><input type="checkbox" checked={attested} disabled={busy} onChange={event => setAttested(event.target.checked)} />I confirm these are synthetic records with no real claimant information.</label></>}
        {notice && <p role="status">{notice}</p>}
        {!esrId && scenarios.length > 0 && <label className="intake-field"><span>Or use a bundled practice request</span>
          <select value={scenario} disabled={busy} onChange={event => { setScenario(event.target.value); setFiles([]); setAttested(false); setError(''); setNotice(''); }}>
            <option value="">Select a request</option>{scenarios.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select></label>}
      </> : <>
        <div className="intake-review-facts"><div><span>Request reference</span><strong>{inspection.esrReference || 'Not provided'}</strong></div><div><span>Service branch</span><strong>{inspection.serviceBranch || 'Not provided'}</strong></div><div><span>Files inspected</span><strong>{inspection.fileCount ?? 'Not reported'}</strong></div></div>
        <p className="intake-preview-status" role="status">{inspection.isDuplicate ? inspection.duplicateReason || 'This request matches an existing case.' : 'No duplicate found during inspection.'}</p>
        {sourceControlled && (!name.trim() || !contentions.trim()) && <p role="status">The source is missing a claimant name or requested condition. Choose a corrected request to continue.</p>}
        {inspection.isDuplicate && inspection.existingEsrId && <button type="button" className="intake-secondary" onClick={() => onReceived(inspection.existingEsrId!)}>Open existing case</button>}
        <label className="intake-field">Claimant name<input value={name} onChange={event => setName(event.target.value)} required readOnly={sourceControlled} disabled={busy} /></label>
        <label className="intake-field">Requested conditions (one per line)<textarea value={contentions} onChange={event => setContentions(event.target.value)} required readOnly={sourceControlled} disabled={busy} rows={3} /></label>
        <label className="intake-field">VA instructions<textarea value={instructions} onChange={event => setInstructions(event.target.value)} readOnly={sourceControlled} disabled={busy} rows={2} placeholder="No additional instructions provided" /></label>
      </>}
      {error && <p className="intake-import-error" role="alert">{error}</p>}
      {busy && <p role="status">{pending}</p>}
      <footer><button type="button" className="intake-text-action" onClick={onCancel} disabled={busy}>Cancel</button>
        {inspection && <button type="button" className="intake-secondary" disabled={busy} onClick={() => {setInspection(undefined); setError('');}}><ArrowLeft size={15} />Change source</button>}
        <button type="submit" className="intake-primary" disabled={busy || (!scenario && (!files.length || !attested)) || Boolean(inspection && (inspection.isDuplicate || !name.trim() || !contentions.trim()))}>
          {esrId ? 'Attach documents' : inspection ? 'Create ESR' : 'Preview request'}<ArrowRight size={16} /></button>
      </footer>
    </form>
  </section>;
}
