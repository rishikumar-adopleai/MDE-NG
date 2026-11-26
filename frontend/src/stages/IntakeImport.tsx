import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { ArrowRight, FileText, Upload } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { inspectIntakeEsr, listIntakeScenarios, WorkspaceApiError, type EsrInspectionResult } from '../data/repositories/workspaceApi';
import { droppedPackages, extensions, fromDirectoryHandle, fromDirectoryInput, fromDroppedDirectory, listClaimantFolders, readClaimantFolder, selectedPackages, type IntakeFolder, type IntakePackage, type PickerDirectory } from './intakePackages';
import './intake-packages.css';

type Props = { esrId?: string; onReceived: (id: string) => void; onCancel: () => void };

function PackageReview({ group, esrId, scenario, attested, locked, setLocked, onReceived, multiple }: {
  group: IntakePackage; scenario: string; attested: boolean; locked: boolean; setLocked: (value: boolean) => void; multiple: boolean;
} & Pick<Props, 'esrId' | 'onReceived'>) {
  const { importIntakeDocument, receiveIntakeExample } = useWorkspace();
  const [inspection, setInspection] = useState<EsrInspectionResult>();
  const [created, setCreated] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState('');
  const [name, setName] = useState('');
  const [contentions, setContentions] = useState('');
  const [instructions, setInstructions] = useState('');
  const [city, setCity] = useState('');
  const [stateCode, setStateCode] = useState('');
  const [postalCode, setPostalCode] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const sourceControlled = Boolean(scenario || inspection?.examManagementRequest);

  useEffect(() => { if (inspection || created) heading.current?.focus(); }, [inspection, created]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || !attested || created || group.problem || inspection?.isDuplicate) return;
    setLocked(true); setError('');
    setPending(esrId ? 'Attaching documents…' : inspection ? 'Creating request…' : 'Reading request and checking for duplicates…');
    try {
      const body = new FormData();
      group.files.forEach((file, index) => body.append(index ? 'files' : 'file', file, file.name));
      body.set('synthetic', 'true');
      if (esrId) { onReceived(await importIntakeDocument(body, esrId)); return; }
      if (!inspection) {
        if (scenario) body.set('scenario_id', scenario);
        const result = await inspectIntakeEsr(body);
        setName(result.claimantName || ''); setContentions(result.contentions.map(item => item.title).join('\n'));
        setInstructions(result.specialInstructions || '');
        setCity(result.city || ''); setStateCode(result.stateCode || ''); setPostalCode(result.postalCode || '');
        setInspection(result);
        return;
      }
      body.set('name', name.trim()); body.set('contentions', contentions.trim()); body.set('special_instructions', instructions.trim());
      body.set('service_branch', inspection.serviceBranch); body.set('esr_reference', inspection.esrReference);
      if (city.trim()) body.set('city', city.trim());
      if (stateCode.trim()) body.set('state_code', stateCode.trim());
      if (postalCode.trim()) body.set('postal_code', postalCode.trim());
      if (inspection.travelLimitMiles != null) body.set('travel_limit_miles', String(inspection.travelLimitMiles));
      const id = scenario ? await receiveIntakeExample(scenario) : await importIntakeDocument(body, undefined, inspection.examManagementRequest);
      if (multiple) setCreated(id); else onReceived(id);
    } catch (cause) {
      if (inspection && cause instanceof WorkspaceApiError && cause.status === 409) setInspection(undefined);
      setError(cause instanceof Error ? cause.message : 'Could not complete this step. Your files are still selected.');
    } finally { setPending(''); setLocked(false); }
  }

  return <form className="intake-package" onSubmit={submit} aria-label={group.label}>
    <h3 tabIndex={-1} ref={heading}>{group.label}</h3>
    {created ? <><p role="status">ESR created. This folder’s documents belong to this case only.</p>
      <button type="button" className="intake-secondary" disabled={locked} onClick={() => onReceived(created)}>Open created case</button></> : <>
      {!scenario && <details className="intake-selection"><summary>{group.files.length} {group.files.length === 1 ? 'file' : 'files'} selected</summary>
        <ul>{group.files.map((file, index) => <li key={index}><FileText size={15} />{file.name}</li>)}</ul></details>}
      {group.skipped > 0 && <p>{group.skipped} unsupported file(s) skipped.</p>}
      {inspection && <>
        <div className="intake-review-facts"><div><span>Request reference</span><strong>{inspection.esrReference || 'Not provided'}</strong></div><div><span>Service branch</span><strong>{inspection.serviceBranch || 'Not provided'}</strong></div><div><span>Files inspected</span><strong>{inspection.fileCount ?? 'Not reported'}</strong></div></div>
        <p className="intake-preview-status" role="status">{inspection.isDuplicate ? inspection.duplicateReason || 'This request matches an existing case.' : 'Review the claimant, requested conditions and files before creating this ESR.'}</p>
        {sourceControlled && (!name.trim() || !contentions.trim()) && <p role="status">The request is missing a claimant or requested condition. Select a corrected source.</p>}
        {inspection.isDuplicate && inspection.existingEsrId && <button type="button" className="intake-secondary" disabled={locked} onClick={() => onReceived(inspection.existingEsrId!)}>Open existing case</button>}
        <label className="intake-field">Claimant name<input value={name} onChange={event => setName(event.target.value)} required readOnly={sourceControlled} disabled={locked} /></label>
        <div className="intake-location-row">
          <label className="intake-field">City (optional)<input value={city} onChange={event => setCity(event.target.value)} readOnly={sourceControlled && Boolean(inspection.city)} disabled={locked} placeholder="e.g. Columbus" /></label>
          <label className="intake-field">State<input value={stateCode} onChange={event => setStateCode(event.target.value)} readOnly={sourceControlled && Boolean(inspection.stateCode)} disabled={locked} maxLength={30} placeholder="OH or Ohio" /></label>
          <label className="intake-field">ZIP code<input value={postalCode} onChange={event => setPostalCode(event.target.value)} readOnly={sourceControlled && Boolean(inspection.postalCode)} disabled={locked} maxLength={10} placeholder="43215" /></label>
        </div>
        <label className="intake-field">Requested conditions (one per line)<textarea value={contentions} onChange={event => setContentions(event.target.value)} required readOnly={sourceControlled} disabled={locked} rows={3} /></label>
        <label className="intake-field">VA instructions<textarea value={instructions} onChange={event => setInstructions(event.target.value)} readOnly={sourceControlled} disabled={locked} rows={2} placeholder="No additional instructions provided" /></label>
      </>}
      {(group.problem || error) && <p className="intake-import-error" role="alert">{group.problem || error}</p>}
      {pending && <p role="status">{pending}</p>}
      {!group.problem && <button type="submit" className="intake-primary" disabled={locked || !attested || Boolean(inspection && (inspection.isDuplicate || !name.trim() || !contentions.trim()))}>
        {esrId ? 'Attach documents' : inspection ? 'Create ESR' : 'Preview request'}<ArrowRight size={16} /></button>}
    </>}
  </form>;
}

export function IntakeImport({ esrId, onReceived, onCancel }: Props) {
  const [groups, setGroups] = useState<IntakePackage[]>([]);
  const [attested, setAttested] = useState(false);
  const [batch, setBatch] = useState<Awaited<ReturnType<typeof listClaimantFolders>>>();
  const [checked, setChecked] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [selection, setSelection] = useState(0);
  const [scenarios, setScenarios] = useState<{id: string; title: string}[]>([]);
  const [scenario, setScenario] = useState('');
  const chooser = useRef<HTMLInputElement>(null);
  const directoryChooser = useRef<HTMLInputElement>(null);
  const pickParent = useRef(false);
  const useDirectoryInput = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    if (esrId) return;
    let active = true;
    void listIntakeScenarios().then(({items}) => { if (active) setScenarios(items); }).catch(() => {});
    return () => { active = false; };
  }, [esrId]);

  function select(next: IntakePackage[], sample = '') {
    if (esrId && next.length > 1) throw new Error('Only one claimant’s package can be attached to an existing case.');
    setGroups(next); setBatch(undefined); setScenario(sample); setError(''); setAttested(false); setSelection(value => value + 1);
  }

  async function openFolder(folder: IntakeFolder, layout: 'parent' | 'single' | 'drop') {
    if (layout !== 'single') {
      const parent = await listClaimantFolders(folder);
      if (parent.folders.length) { setBatch(parent); setChecked([]); setError(''); return; }
      if (layout === 'parent') throw new Error('No claimant subfolders found. If this is one claimant, use Choose One Claimant Folder.');
    }
    select([await readClaimantFolder(folder)]);
  }

  async function chooseFolder(parent: boolean) {
    pickParent.current = parent;
    const picker = (window as Window & {showDirectoryPicker?: (options: {mode: 'read'}) => Promise<PickerDirectory>}).showDirectoryPicker;
    if (!picker || useDirectoryInput.current) {
      if (directoryChooser.current && 'webkitdirectory' in directoryChooser.current) directoryChooser.current.click();
      else setError('This browser cannot browse folders. Drop the folders or choose one ZIP per claimant.');
      return;
    }
    setBusy(true);
    try {
      const directory = await picker.call(window, {mode: 'read'});
      await openFolder(fromDirectoryHandle(directory), parent ? 'parent' : 'single');
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      if (cause instanceof DOMException && cause.name === 'SecurityError') {
        useDirectoryInput.current = true; setError('Folder access was blocked. Choose the folder again to use the alternate browser picker.');
      } else setError(cause instanceof Error ? cause.message : 'Could not read this folder. Try dropping it instead.');
    } finally { setBusy(false); }
  }

  async function addCheckedFolders() {
    if (!batch || !checked.length || busy) return;
    setBusy(true);
    try { select(await Promise.all(batch.folders.filter(folder => checked.includes(folder.name)).map(readClaimantFolder))); }
    finally { setBusy(false); }
  }

  async function handleDrop(event: DragEvent) {
    event.preventDefault(); setDragging(false);
    if (busy) return;
    const items = Array.from(event.dataTransfer.items).filter(item => item.kind === 'file');
    const sources = items.map(item => ({entry: item.webkitGetAsEntry?.(), file: item.getAsFile()}));
    const entries = sources.flatMap(({entry}) => entry ? [entry] : []);
    const loose = sources.length ? sources.flatMap(({entry, file}) => !entry?.isDirectory && file ? [file] : []) : Array.from(event.dataTransfer.files);
    setBusy(true);
    try {
      if (!esrId && entries.length === 1 && entries[0].isDirectory && !loose.length) {
        await openFolder(fromDroppedDirectory(entries[0] as FileSystemDirectoryEntry), 'drop');
      } else select(await droppedPackages(entries, loose, Boolean(esrId)));
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Folder selection failed. Try one ZIP per claimant.'); }
    finally { setBusy(false); }
  }

  return <section className="intake-receive" aria-labelledby="receive-title">
    <header><span className="intake-kicker">{esrId ? 'Case Documents' : 'New ESR'}</span>
      <h2 id="receive-title" tabIndex={-1} ref={heading}>{esrId ? 'Add Source Documents' : 'Receive Exam Requests'}</h2>
      <p>{esrId ? 'Attach only records belonging to the selected claimant.' : 'Use one folder per claimant. Each folder gets its own preview and ESR; nothing is created until you approve it.'}</p>
    </header>
    <div className="intake-package-selection">
      <div className={`intake-dropzone${dragging ? ' dragging' : ''}`} onDrop={handleDrop} onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)}>
        <Upload size={24} aria-hidden="true" /><h3>{esrId ? 'Add Claimant Documents' : 'Choose Claimant Folders'}</h3>
        <p>{esrId ? 'Choose one claimant’s folder, documents or ZIP for this case.' : 'For several claimants, choose their parent folder, then tick the cases you want. You can also drag selected claimant folders here together.'}</p>
        <input ref={directoryChooser} type="file" hidden {...{webkitdirectory: ''}} disabled={busy} onChange={async event => {
          const files = Array.from(event.target.files ?? []); event.target.value = ''; if (!files.length) return;
          setBusy(true);
          try { await openFolder(fromDirectoryInput(files), pickParent.current ? 'parent' : 'single'); }
          catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not read this folder.'); }
          finally { setBusy(false); }
        }} />
        <input ref={chooser} type="file" multiple hidden accept={extensions.join(',')} disabled={busy} onChange={event => {
          const files = Array.from(event.target.files ?? []); event.target.value = ''; if (!files.length) return;
          try { select(selectedPackages(files)); } catch (cause) { setError((cause as Error).message); }
        }} />
        <div className="intake-picker-actions">
          {!esrId && <button type="button" className="intake-primary" disabled={busy} onClick={() => void chooseFolder(true)}>Choose Parent Folder</button>}
          <button type="button" className="intake-secondary" disabled={busy} onClick={() => void chooseFolder(false)}>Choose One Claimant Folder</button>
          <button type="button" className="intake-secondary" disabled={busy} onClick={() => chooser.current?.click()}>Choose Files or ZIPs</button>
        </div>
      </div>
      {batch && <fieldset className="intake-folder-choice" disabled={busy}>
        <legend>Select Claimant Folders</legend>
        <p>Folders in <strong>{batch.name}</strong>. Each checked folder becomes a separate request. {batch.rootFiles > 0 && `Files directly in the parent (${batch.rootFiles}) are excluded.`}</p>
        <p>If these are evidence folders for one claimant, use Choose One Claimant Folder instead.</p>
        <label><input type="checkbox" checked={checked.length === batch.folders.length} ref={node => { if (node) node.indeterminate = checked.length > 0 && checked.length < batch.folders.length; }} onChange={event => setChecked(event.target.checked ? batch.folders.map(folder => folder.name) : [])} />Select all folders</label>
        <div className="intake-folder-list">{batch.folders.map(folder => <label key={folder.name}><input type="checkbox" checked={checked.includes(folder.name)} onChange={event => setChecked(event.target.checked ? [...checked, folder.name] : checked.filter(name => name !== folder.name))} />{folder.name}</label>)}</div>
        {groups.length > 0 && <p>Using these folders replaces your current selection.</p>}
        <div className="intake-picker-actions"><button type="button" className="intake-primary" disabled={!checked.length} onClick={() => void addCheckedFolders()}>Use Selected Folders ({checked.length})</button><button type="button" className="intake-secondary" onClick={() => setBatch(undefined)}>Cancel Selection</button></div>
      </fieldset>}
      {error && <p className="intake-import-error" role="alert">{error}{groups.length > 0 && ' Your previous selection is still shown.'}</p>}
      {!esrId && scenarios.length > 0 && <label className="intake-field">Or use a bundled practice request<select value={scenario} disabled={busy} onChange={event => {
        const sample = scenarios.find(item => item.id === event.target.value); select(sample ? [{label: sample.title, files: [], skipped: 0}] : [], sample?.id);
      }}><option value="">Select a request</option>{scenarios.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
      {groups.length > 0 && !scenario && <label className="intake-synthetic-confirmation"><input type="checkbox" checked={attested} disabled={busy} onChange={event => setAttested(event.target.checked)} />I confirm these are synthetic records with no real claimant information.</label>}
      {groups.length > 1 && <p role="status">{groups.length} separate claimant packages. Preview and create each request individually.</p>}
    </div>
    {groups.map((group, index) => <PackageReview key={`${selection}-${index}`} group={group} esrId={esrId} scenario={scenario} attested={attested || Boolean(scenario)} locked={busy || Boolean(batch)} setLocked={setBusy} onReceived={onReceived} multiple={groups.length > 1} />)}
    <footer className="intake-package-selection"><button type="button" className="intake-secondary" disabled={busy} onClick={onCancel}>Back to Intake</button></footer>
  </section>;
}
