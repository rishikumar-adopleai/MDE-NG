import { useState } from 'react';
import {
  ArrowRight,
  Calendar,
  CheckCircle2,
  Clock,
  Inbox,
  Loader2,
  MapPin,
  Phone,
  Search,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  Users,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { matchSchedulingOptions } from '../data/repositories/workspaceApi';
import type {
  Modality,
  SchedulingCaseRequirement,
  SchedulingCaseView,
  SchedulingCompatibleGroup,
  SchedulingMatchResponse,
} from '../domain/model';
import { StageHeading } from './StageHeading';
import { dateLabel, modalityLabels, resultEntityId, type StageProps } from './StageWorkbench';
import { Status } from './shared';

function ProgressiveScheduleFinder({
  requirementIds,
  title,
  modality,
  onHeld,
}: {
  requirementIds: string[];
  title: string;
  modality: Modality;
  onHeld: () => void;
}) {
  const { holdAppointment } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [match, setMatch] = useState<SchedulingMatchResponse | null>(null);
  const [selectedSlotId, setSelectedSlotId] = useState('');
  const [showExcluded, setShowExcluded] = useState(false);

  const runMatch = async (facilityId?: string, examinerId?: string) => {
    setOpen(true);
    setLoading(true);
    setError('');
    if (facilityId && match && facilityId !== match.selectedFacilityId) {
      setSelectedSlotId('');
    }
    try {
      const res = await matchSchedulingOptions(
        requirementIds,
        facilityId,
        facilityId && match && facilityId !== match.selectedFacilityId ? undefined : examinerId,
      );
      setMatch(res);
      setSelectedSlotId(res.slots[0]?.slotId || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to match locations and examiners.');
    } finally {
      setLoading(false);
    }
  };

  const handleHold = async () => {
    if (!match || !match.selectedDoctorId) return;
    const slot = match.slots.find(s => s.slotId === selectedSlotId) ?? match.slots[0];
    if (!slot) return;
    setBusy(true);
    setError('');
    try {
      const res = await holdAppointment(requirementIds.length === 1 ? requirementIds[0] : requirementIds, match.selectedDoctorId, {
        slotId: slot.slotId,
        startsAt: slot.startsAt,
        endsAt: slot.endsAt,
        facilityId: slot.facilityId,
        modality: slot.modality,
      });
      if (!res.ok) {
        setError(res.message);
        return;
      }
      setOpen(false);
      onHeld();
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="sched-finder-trigger">
        <button type="button" className="intake-primary" onClick={() => void runMatch()}>
          <MapPin size={15} /> Find Location &amp; Valid Doctor
        </button>
      </div>
    );
  }

  return (
    <div className="sched-finder-panel">
      <div className="sched-finder-head">
        <div>
          <strong>3-Step Schedule Match · {title}</strong>
          <small>Locked pathway: {modalityLabels[modality]}</small>
        </div>
        <button type="button" className="intake-text-action" onClick={() => setOpen(false)}>Close</button>
      </div>

      {loading && (
        <div className="sched-loading-row" role="status">
          <Loader2 size={16} className="triage-spinner" />
          <span>Finding nearest facilities, checking active medical licenses on exam dates, and loading open slots…</span>
        </div>
      )}

      {error && <p className="intake-error" role="alert">{error}</p>}

      {!loading && match && (
        <div className="sched-steps-grid">
          {/* Step 1: Nearest Location (only schedulable facilities with valid doctors & slots) */}
          <div className="sched-step-block">
            <span className="sched-step-kicker">
              Step 1 · Eligible Facility ({match.facilities.length} available)
            </span>
            {match.locationVerificationRequired && (
              <p className="triage-inline-note">
                Veteran location cannot be resolved cleanly; verify location before confirming in-person travel.
              </p>
            )}
            {match.facilities.length > 0 ? (
              <select
                value={match.selectedFacilityId}
                onChange={e => void runMatch(e.target.value)}
              >
                {match.facilities.map(f => {
                  const distLabel =
                    f.distanceMiles === null || f.distanceMiles === undefined
                      ? 'Distance unknown'
                      : f.distancePrecision === 'approximate-zip'
                        ? `~${f.distanceMiles} mi (ZIP approx.)`
                        : `${f.distanceMiles} mi`;
                  return (
                    <option key={f.facilityId} value={f.facilityId}>
                      {f.facilityName} · {f.city}, {f.stateCode} ({distLabel}
                      {match.travelLimitMiles != null && !f.withinTravelLimit ? ' · Over limit' : ''}
                      {f.validDoctorCount ? ` · ${f.validDoctorCount} valid doctor(s)` : ''})
                    </option>
                  );
                })}
              </select>
            ) : (
              <p className="intake-error">
                {match.schedulingException?.message || 'No eligible facility + qualified examiner + open slot combination found.'}
              </p>
            )}
          </div>

          {/* Step 2: Suitable Valid Doctor (with strict license check) */}
          <div className="sched-step-block">
            <span className="sched-step-kicker">Step 2 · Suitable Valid Doctor</span>
            {match.validDoctors.length > 0 ? (
              <select
                value={match.selectedDoctorId}
                onChange={e => void runMatch(match.selectedFacilityId, e.target.value)}
              >
                {match.validDoctors.map(d => (
                  <option key={d.examinerId} value={d.examinerId}>
                    {d.doctorName} · {d.primarySpecialty} · License valid thru {d.licenseExpiryDate}
                  </option>
                ))}
              </select>
            ) : (
              <p className="triage-inline-note">No active, license-valid doctors match at this facility. Try another nearby location.</p>
            )}
            {match.excludedDoctors.length > 0 && (
              <div className="sched-excluded-wrap">
                <button type="button" className="intake-text-action" onClick={() => setShowExcluded(v => !v)}>
                  <ShieldAlert size={13} /> {showExcluded ? 'Hide' : 'Show'} {match.excludedDoctors.length} excluded doctor(s) (license/availability)
                </button>
                {showExcluded && (
                  <ul className="sched-excluded-list">
                    {match.excludedDoctors.map(ex => (
                      <li key={ex.examinerId}>
                        <strong>{ex.doctorName}</strong> ({ex.primarySpecialty}) — {ex.reason}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          {/* Step 3: Available Date & Time */}
          <div className="sched-step-block">
            <span className="sched-step-kicker">Step 3 · Available Date &amp; Time</span>
            {match.slots.length > 0 ? (
              <>
                <select value={selectedSlotId} onChange={e => setSelectedSlotId(e.target.value)}>
                  {match.slots.map(slot => (
                    <option key={slot.slotId} value={slot.slotId}>
                      {dateLabel(slot.startsAt)} ({slot.durationMinutes} min) · License verified
                    </option>
                  ))}
                </select>
                <div className="sched-hold-row">
                  <span className="sched-license-ok">
                    <ShieldCheck size={14} /> License verified active past exam date
                  </span>
                  <button type="button" className="intake-primary" disabled={busy || !selectedSlotId} onClick={() => void handleHold()}>
                    {busy ? 'Holding…' : 'Hold Selected Slot'} <ArrowRight size={15} />
                  </button>
                </div>
              </>
            ) : (
              <p className="triage-inline-note">No conflict-free slots available for this doctor.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function LocationVerifyInputs({
  esrId,
  initialCity = '',
  initialState = '',
  initialPostal = '',
  onSaved,
}: {
  esrId: string;
  initialCity?: string;
  initialState?: string;
  initialPostal?: string;
  onSaved?: () => void;
}) {
  const { verifyVeteranLocation } = useWorkspace();
  const [city, setCity] = useState(initialCity);
  const [stateCode, setStateCode] = useState(initialState);
  const [postalCode, setPostalCode] = useState(initialPostal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    const cleanZip = postalCode.trim();
    if (!/^\d{5}(?:-\d{4})?$/.test(cleanZip)) {
      setError('Enter a valid 5-digit ZIP code.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await verifyVeteranLocation(esrId, {
        city: city.trim() || undefined,
        stateCode: stateCode.trim().toUpperCase() || undefined,
        postalCode: cleanZip,
      });
      if (!res.ok) setError(res.message);
      else onSaved?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sched-location-bar">
      <input
        type="text"
        value={city}
        onChange={e => setCity(e.target.value)}
        placeholder="City (optional)"
        disabled={busy}
      />
      <input
        type="text"
        value={stateCode}
        onChange={e => setStateCode(e.target.value)}
        placeholder="State (e.g. OH)"
        maxLength={30}
        disabled={busy}
      />
      <input
        type="text"
        value={postalCode}
        onChange={e => setPostalCode(e.target.value)}
        placeholder="ZIP (e.g. 43215)"
        maxLength={10}
        disabled={busy}
      />
      <button type="button" className="intake-secondary" disabled={busy} onClick={() => void handleSave()}>
        {busy ? 'Saving…' : 'Save location'}
      </button>
      {error && <p className="intake-error" role="alert">{error}</p>}
    </div>
  );
}

function HeldAppointmentCard({
  appt,
  locationMissing,
  onConfirmed,
}: {
  appt: NonNullable<SchedulingCaseRequirement['appointment']>;
  locationMissing?: boolean;
  onConfirmed: (esrId: string, entityId?: string) => void;
}) {
  const { recordOutreach, confirmAppointment, cancelAppointment } = useWorkspace();
  const [channel, setChannel] = useState<'phone' | 'sms' | 'email'>('phone');
  const [outcome, setOutcome] = useState('answered');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const isConfirmed = appt.status === 'confirmed-locally';
  const outreachLog = appt.outreachLog ?? [];
  const outreachCount = outreachLog.length;
  const maxOutreachReached = outreachCount >= 2;
  const veteranConfirmed = outreachLog.some(entry => entry.outcome === 'answered');
  const outreachDisabled = busy || maxOutreachReached || veteranConfirmed;

  const handleOutreach = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await recordOutreach(appt.esrId, appt.id, channel, outcome as never, note || 'Veteran contacted for scheduling.');
      if (!res.ok) setError(res.message);
      else setNote('');
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await confirmAppointment(appt.id);
      if (!res.ok) setError(res.message);
      else onConfirmed(appt.esrId, resultEntityId(res.value));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await cancelAppointment(appt.id, 'Rescheduling requested by staff');
      if (!res.ok) setError(res.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`sched-appt-card${isConfirmed ? ' is-confirmed' : ''}`}>
      <div className="sched-appt-head">
        <div>
          <strong>{appt.facilityName || appt.facilityId}</strong>
          <small>
            {appt.examinerName || appt.examinerId} · {dateLabel(appt.startsAt)} · {modalityLabels[appt.modality]}
          </small>
          {appt.examinerLicenseExpiry && (
            <small className="sched-license-ok"><ShieldCheck size={13} /> License verified through {appt.examinerLicenseExpiry}</small>
          )}
        </div>
        <Status text={isConfirmed ? 'Confirmed → Stage 4' : 'Slot Held'} tone={isConfirmed ? 'green' : 'amber'} />
      </div>

      {!isConfirmed && (
        <div className="sched-coordination-box">
          <div className="sched-coord-row">
            <select value={channel} disabled={outreachDisabled} onChange={e => setChannel(e.target.value as 'phone' | 'sms' | 'email')}>
              <option value="phone">Phone</option>
              <option value="sms">Text message</option>
              <option value="email">Email</option>
            </select>
            <select value={outcome} disabled={outreachDisabled} onChange={e => setOutcome(e.target.value)}>
              <option value="answered">Veteran confirmed</option>
              <option value="voicemail">Left voicemail</option>
              <option value="no-answer">No answer</option>
            </select>
            <input
              type="text"
              value={note}
              disabled={outreachDisabled}
              onChange={e => setNote(e.target.value)}
              placeholder="Coordination note (optional)…"
            />
            <button type="button" className="intake-secondary" disabled={outreachDisabled} onClick={() => void handleOutreach()}>
              Log outreach
            </button>
          </div>
          {outreachCount > 0 && (
            <div className="sched-outreach-count">
              {outreachCount} / 2 outreach attempt(s) logged · Latest: {outreachLog[outreachCount - 1].channel} ({outreachLog[outreachCount - 1].outcome})
            </div>
          )}
          {maxOutreachReached && !veteranConfirmed && (
            <p className="triage-inline-note">
              Maximum 2 outreach attempts reached without Veteran confirmation. Cancel Hold / Reschedule to release this slot.
            </p>
          )}
          {error && <p className="intake-error" role="alert">{error}</p>}
          {(locationMissing || error.includes('Verify Veteran location')) && (
            <LocationVerifyInputs esrId={appt.esrId} onSaved={() => setError('')} />
          )}
          <div className="sched-coord-actions">
            <button type="button" className="intake-text-action" disabled={busy} onClick={() => void handleCancel()}>
              Cancel Hold / Reschedule
            </button>
            <button type="button" className="intake-primary" disabled={busy || !veteranConfirmed} onClick={() => void handleConfirm()}>
              Confirm Appointment → Stage 4 <ArrowRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function LocationVerifyBar({
  activeCase,
}: {
  activeCase: SchedulingCaseView;
}) {
  const [editing, setEditing] = useState(false);

  return (
    <div className="sched-location-wrap">
      <div className="triage-case-meta">
        {activeCase.claimIdentifier} · <MapPin size={13} style={{ display: 'inline', verticalAlign: '-2px' }} />{' '}
        {activeCase.city || 'Location pending'}
        {activeCase.stateCode ? `, ${activeCase.stateCode}` : ''} {activeCase.postalCode}{' '}
        <button
          type="button"
          className="intake-text-action"
          onClick={() => setEditing(v => !v)}
        >
          {editing ? 'Cancel' : activeCase.postalCode ? 'Edit location' : 'Verify location'}
        </button>
      </div>
      {editing && (
        <LocationVerifyInputs
          key={`${activeCase.esrId}-${activeCase.postalCode}`}
          esrId={activeCase.esrId}
          initialCity={activeCase.city || ''}
          initialState={activeCase.stateCode || ''}
          initialPostal={activeCase.postalCode || ''}
          onSaved={() => setEditing(false)}
        />
      )}
    </div>
  );
}

export function SchedulingPage({
  openSource,
  query,
  onQueryChange,
  filters,
  activeFilter,
  onFilterChange,
  selectedId,
  onSelect,
  onNavigate,
}: StageProps) {
  const { state } = useWorkspace();
  const [groupMode, setGroupMode] = useState<'separate' | 'grouped'>('separate');
  const [handoff, setHandoff] = useState<{ esrId: string; entityId?: string } | null>(null);
  const scheduling = state?.scheduling;
  const casesById = scheduling?.casesByEsrId ?? {};
  const orderedIds = scheduling?.queueIds ?? Object.keys(casesById);
  const allCases: SchedulingCaseView[] = orderedIds.map(id => casesById[id]).filter(Boolean);

  const queryLower = query.trim().toLowerCase();
  const visibleCases = allCases.filter(c => {
    if (activeFilter === 'ready' && c.pendingCount === 0) return false;
    if (!queryLower) return true;
    return (
      c.claimantName.toLowerCase().includes(queryLower) ||
      c.esrId.toLowerCase().includes(queryLower) ||
      c.requirements.some(r => r.examinationType.toLowerCase().includes(queryLower))
    );
  });

  const activeCase = visibleCases.find(c => c.esrId === selectedId) ?? visibleCases[0];

  return (
    <div className="intake-page">
      <StageHeading
        stage="Scheduling"
        description="Match Stage 2 approved examinations to the nearest facility, license-valid doctor, and open time slot."
      />

      <div className="intake-board">
        <aside className="intake-queue-card" aria-label="Scheduling queue">
          <div className="intake-queue-heading">
            <h2>Scheduling queue</h2>
            <span>{allCases.length} case{allCases.length === 1 ? '' : 's'}</span>
          </div>
          <div className="intake-queue-controls is-open">
            <div className="intake-queue-toolbar">
              <label className="intake-search">
                <Search size={15} aria-hidden="true" />
                <input
                  placeholder="Search claimant, ESR, or exam"
                  value={query}
                  onChange={e => onQueryChange(e.target.value)}
                />
              </label>
              <div className="intake-queue-filters">
                {filters.map(f => (
                  <button
                    type="button"
                    key={f.key}
                    aria-pressed={activeFilter === f.key}
                    onClick={() => onFilterChange(f.key)}
                  >
                    {f.key === 'all' ? 'All cases' : f.label} <span>{allCases.length}</span>
                  </button>
                ))}
              </div>
            </div>

            {visibleCases.length ? (
              <ul className="intake-queue-list">
                {visibleCases.map(c => (
                  <li key={c.esrId}>
                    <button
                      type="button"
                      className="intake-queue-row"
                      aria-pressed={activeCase?.esrId === c.esrId}
                      onClick={() => onSelect(c.esrId)}
                    >
                      <span className="intake-queue-row-main">
                        <strong>{c.claimantName}</strong>
                        <small>{c.claimIdentifier} · {c.approvedCount} exam{c.approvedCount === 1 ? '' : 's'}</small>
                      </span>
                      <Status
                        text={`${c.scheduledCount}/${c.approvedCount} Scheduled`}
                        tone={c.pendingCount === 0 ? 'green' : 'amber'}
                      />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="intake-queue-empty">
                <p>No cases in Scheduling.</p>
              </div>
            )}
          </div>
        </aside>

        {activeCase ? (
          <section className="intake-case-card triage-case-workspace">
            <header className="triage-case-header">
              <div className="triage-claimant-block">
                <span className="intake-avatar">{activeCase.initials}</span>
                <div>
                  <LocationVerifyBar key={activeCase.esrId} activeCase={activeCase} />
                  <h2>{activeCase.claimantName}</h2>
                  <p className="triage-case-sub">
                    <Phone size={13} style={{ display: 'inline', verticalAlign: '-2px' }} /> {activeCase.phone} · {activeCase.email} · Priority: {activeCase.priority}
                  </p>
                </div>
              </div>
              <Status
                text={activeCase.pendingCount === 0 ? 'All Scheduled' : `${activeCase.pendingCount} Pending`}
                tone={activeCase.pendingCount === 0 ? 'green' : 'amber'}
              />
            </header>

            <div className="triage-kpi-strip sched-kpi-strip">
              <div className="triage-kpi-box">
                <span>Approved exams</span>
                <strong>{activeCase.approvedCount}</strong>
              </div>
              <div className="triage-kpi-box">
                <span>Scheduled</span>
                <strong>{activeCase.scheduledCount} / {activeCase.approvedCount}</strong>
              </div>
            </div>

            {activeCase.specialInstructions && (
              <div className="triage-va-instructions">
                <strong>VA Special Instructions:</strong> {activeCase.specialInstructions}
              </div>
            )}

            {handoff && handoff.esrId === activeCase.esrId && (
              <div className="triage-approved-banner">
                <div className="triage-approved-info">
                  <CheckCircle2 size={18} />
                  <div>
                    <strong>Appointment Confirmed &amp; Released to Stage 4 (Clinical Evaluation)</strong>
                  </div>
                </div>
                <button
                  type="button"
                  className="intake-secondary"
                  onClick={() => onNavigate('Clinical Evaluation', handoff.esrId, handoff.entityId)}
                >
                  Open in Clinical Evaluation <ArrowRight size={15} />
                </button>
              </div>
            )}

            <div className="sched-mode-bar">
              <h3>Approved Examinations ({activeCase.requirements.length})</h3>
              <div className="sched-mode-toggle" role="group" aria-label="Grouping mode">
                <button
                  type="button"
                  className={groupMode === 'separate' ? 'is-active' : ''}
                  onClick={() => setGroupMode('separate')}
                >
                  Schedule Separately
                </button>
                <button
                  type="button"
                  className={groupMode === 'grouped' ? 'is-active' : ''}
                  onClick={() => setGroupMode('grouped')}
                >
                  <Users size={14} /> Group Compatible Exams
                </button>
              </div>
            </div>

            {groupMode === 'grouped' ? (
              <div className="triage-contention-list">
                {activeCase.compatibleGroups.map((grp: SchedulingCompatibleGroup) => (
                  <article key={grp.groupId} className="triage-card">
                    <header className="triage-card-header">
                      <div>
                        <h3>{grp.titles.join(' + ')}</h3>
                        <div className="triage-card-tags">
                          <span className="triage-tag-specialty">{grp.specialtyGroup}</span>
                          <span className="triage-tag-dbq">{modalityLabels[grp.modality]} (Locked from Stage 2)</span>
                        </div>
                      </div>
                      <Status text={grp.isBundled ? `Bundled (${grp.requirementIds.length})` : 'Separate'} tone="slate" />
                    </header>
                    <p className="triage-pathway-reason">{grp.reason}</p>
                    <ProgressiveScheduleFinder
                      requirementIds={grp.requirementIds}
                      title={grp.titles.join(' + ')}
                      modality={grp.modality}
                      onHeld={() => setHandoff(null)}
                    />
                  </article>
                ))}
                {activeCase.appointments.map(appt => appt && (
                  <HeldAppointmentCard
                    key={appt.id}
                    appt={appt}
                    locationMissing={!activeCase.postalCode}
                    onConfirmed={(esrId, entityId) => setHandoff({ esrId, entityId })}
                  />
                ))}
              </div>
            ) : (
              <div className="triage-contention-list">
                {activeCase.requirements.map((req, idx) => (
                  <article key={req.id} className="triage-card">
                    <header className="triage-card-header">
                      <div className="triage-card-title-group">
                        <span className="triage-card-number">{String(idx + 1).padStart(2, '0')}</span>
                        <div>
                          <h3>{req.examinationType}</h3>
                          <div className="triage-card-tags">
                            <span className="triage-tag-specialty">
                              <Stethoscope size={12} /> {req.requiredSpecialties.join(', ')} ({req.requiredCredentials.join(', ')})
                            </span>
                            <span className="triage-tag-dbq">{modalityLabels[req.selectedModality || 'in-person']} (Locked)</span>
                          </div>
                        </div>
                      </div>
                      <Status
                        text={req.appointment?.status === 'confirmed-locally' ? 'Confirmed' : req.appointment ? 'Held' : 'Pending'}
                        tone={req.appointment?.status === 'confirmed-locally' ? 'green' : req.appointment ? 'amber' : 'slate'}
                      />
                    </header>

                    <div className="sched-req-meta">
                      <span><strong>Required DBQs:</strong> {req.requestedDbqTypes.join(', ')}</span>
                      {req.requestedDiagnostics.length > 0 && (
                        <span><strong>Diagnostics:</strong> {req.requestedDiagnostics.join(', ')}</span>
                      )}
                    </div>

                    {req.appointment ? (
                      <HeldAppointmentCard
                        appt={req.appointment}
                        locationMissing={!activeCase.postalCode}
                        onConfirmed={(esrId, entityId) => setHandoff({ esrId, entityId })}
                      />
                    ) : (
                      <ProgressiveScheduleFinder
                        requirementIds={[req.id]}
                        title={req.examinationType}
                        modality={req.selectedModality || 'in-person'}
                        onHeld={() => setHandoff(null)}
                      />
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        ) : (
          <section className="intake-empty-state">
            <Inbox size={26} />
            <h3>No examinations waiting in Scheduling</h3>
            <p>Approve an examination plan in Stage 2 (Medical Triage) to release it here.</p>
          </section>
        )}
      </div>
    </div>
  );
}
