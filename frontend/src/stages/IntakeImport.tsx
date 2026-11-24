import { useEffect, useRef, useState, type FormEvent } from 'react';
import { FilePlus2, FileText, Upload } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { listIntakeScenarios } from '../data/repositories/workspaceApi';

export function IntakeImport({ esrId, onReceived }: {
  esrId?: string;
  onReceived: (id: string) => void;
}) {
  const { importIntakeDocument, receiveIntakeExample } = useWorkspace();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState('');
  const [contentions, setContentions] = useState('');
  const [synthetic, setSynthetic] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [scenarios, setScenarios] = useState<{ id: string; title: string }[]>([]);
  const [scenario, setScenario] = useState('');
  const xml = !esrId && Boolean(file?.name.toLowerCase().endsWith('.xml'));
  const needsRequestDetails = !esrId && Boolean(file) && !xml;

  useEffect(() => {
    if (esrId) return;
    let active = true;
    void listIntakeScenarios().then(({items}) => {
      if (!active) return;
      setScenarios(items);
      setScenario(items[0]?.id ?? '');
    }).catch(() => {});
    return () => { active = false; };
  }, [esrId]);

  async function loadSample() {
    if (!scenario) return;
    setBusy(true);
    setError('');
    try {
      onReceived(await receiveIntakeExample(scenario));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The ESR could not be queued.');
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !synthetic) return;
    const body = new FormData();
    body.set('file', file);
    if (files.length > 0) {
      files.forEach(f => body.append('files', f));
    }
    body.set('synthetic', String(synthetic));
    if (!esrId && !xml) {
      body.set('name', name.trim());
      body.set('contentions', contentions.trim());
    }
    setBusy(true);
    setError('');
    try {
      const received = await importIntakeDocument(body, esrId);
      if (fileInput.current) fileInput.current.value = '';
      setFile(undefined);
      setFiles([]);
      setSynthetic(false);
      onReceived(received);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The document could not be queued.');
    } finally {
      setBusy(false);
    }
  }

  return <form className="intake-import" onSubmit={submit}>
    {!esrId && scenarios.length > 0 && <div className="intake-bundle-picker">
        <label className="intake-field"><span>Bundled request</span>
          <select value={scenario} disabled={busy} onChange={event => setScenario(event.target.value)}>
            {scenarios.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
          </select>
        </label>
        <button type="button" className="intake-secondary" onClick={loadSample} disabled={busy || !scenario}>Open request</button>
    </div>}
    <div className="source-picker">
      <span className="source-picker-label">{esrId ? 'Source document(s)' : 'Upload a request or source document'}</span>
      <label className="source-picker-control">
        <input ref={fileInput} type="file" multiple={Boolean(esrId)} accept={esrId ? '.pdf,.txt,.png,.jpg,.jpeg,.tiff,.bmp' : '.xml,.pdf,.txt,.png,.jpg,.jpeg,.tiff,.bmp'} aria-label="Choose source document" disabled={busy} onChange={event => {
          const selected = Array.from(event.target.files ?? []);
          setFiles(selected);
          setFile(selected[0]);
          setSynthetic(false);
        }} />
        <span className="source-picker-icon">{file ? <FileText size={19} /> : <Upload size={19} />}</span>
        <span className="source-picker-copy"><b>{files.length > 1 ? `${files.length} files selected` : (file?.name ?? 'Choose source file(s)')}</b><small>{file
          ? (xml ? 'Claimant and contentions will be read from the request' : files.length > 1 ? `Ready to batch-upload and ingest ${files.length} medical records` : 'Ready to attach for OCR & LangGraph ingestion')
          : esrId ? 'PDF, Scanned Image (PNG, JPG, TIFF) or UTF-8 text · Select one or multiple files' : 'Exam Management XML, PDF, Scanned Image or UTF-8 text'}</small></span>
        <span className="source-picker-action" aria-hidden="true">{file ? 'Change files' : 'Browse'}</span>
      </label>
    </div>
    {needsRequestDetails && <>
      <label className="intake-field"><span>Claimant name</span><input value={name} onChange={event => setName(event.target.value)} maxLength={100} disabled={busy} required /></label>
      <label className="intake-field"><span>Contentions, one per line</span><textarea value={contentions} onChange={event => setContentions(event.target.value)} maxLength={600} rows={2} disabled={busy} required /></label>
    </>}
    {file && <label className="intake-synthetic-confirmation"><input type="checkbox" checked={synthetic} disabled={busy} onChange={event => setSynthetic(event.target.checked)} /> I confirm this file contains no real claimant records.</label>}
    {error && <p className="intake-import-error" role="alert">{error}</p>}
    <div className="intake-import-actions">
      <button type="submit" className="intake-primary" disabled={busy || !file || !synthetic || (needsRequestDetails && (!name.trim() || !contentions.trim()))}>
        {esrId ? <Upload size={16} /> : <FilePlus2 size={16} />}
        {busy ? 'Saving…' : esrId ? 'Add source document' : xml ? 'Import request' : 'Receive and queue'}
      </button>
    </div>
  </form>;
}
