import { useEffect, useState } from 'react';
import {
  ArrowRight,
  Calendar,
  CheckCircle2,
  ChevronDown,
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
  X,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { matchCoordinatedScheduling, matchSchedulingOptions } from '../data/repositories/workspaceApi';
import type {
  CoordinatedMatchResponse,
  Modality,
  SchedulingCaseRequirement,
  SchedulingCaseView,
  SchedulingCompatibleGroup,
  SchedulingMatchResponse,
} from '../domain/model';
import { StageHeading } from './StageHeading';
import { RecordHeader } from './RecordHeader';
import { dateLabel, modalityLabels, resultEntityId, type StageProps } from './StageWorkbench';
import { Status } from './shared';
import { HeadingText } from '../HeadingText';

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
  const { state, holdAppointment } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [match, setMatch] = useState<SchedulingMatchResponse | null>(null);
  const [selectedSlotId, setSelectedSlotId] = useState('');
  const [showExcluded, setShowExcluded] = useState(false);

  const apptSignature = Object.values(state?.appointmentsById ?? {})
    .map(a => `${a.id}:${a.status}`)
    .join(',');

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void matchSchedulingOptions(
      requirementIds,
      match?.selectedFacilityId ?? undefined,
      match?.selectedDoctorId ?? undefined,
    ).then(res => {
      if (cancelled) return;
      setMatch(res);
      setSelectedSlotId(prev => (res.slots.some(s => s.slotId === prev) ? prev : (res.slots[0]?.slotId || '')));
    }).catch(() => {
      // Keep current state if background refresh fails
    });
    return () => {
      cancelled = true;
    };
  }, [apptSignature, open]);

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
        if (res.message.toLowerCase().includes('overlap') || res.message.toLowerCase().includes('no longer available')) {
          try {
            const refreshed = await matchSchedulingOptions(
              requirementIds,
              match.selectedFacilityId ?? undefined,
              match.selectedDoctorId ?? undefined,
            );
            setMatch(refreshed);
            setSelectedSlotId(refreshed.slots[0]?.slotId || '');
            setError(`${res.message} Available conflict-free slots have been refreshed.`);
          } catch {
            setError(res.message);
          }
        } else {
          setError(res.message);
        }
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
  const [showCancelModal, setShowCancelModal] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [cancelError, setCancelError] = useState('');

  const isConfirmed = appt.status === 'confirmed-locally';
  const outreachLog = appt.outreachLog ?? [];
  const outreachCount = outreachLog.length;
  const maxOutreachAttempts = 2;
  const maxOutreachReached = outreachCount >= maxOutreachAttempts;
  const veteranConfirmed = outreachLog.some(entry => entry.outcome === 'answered');
  const outreachDisabled = busy || veteranConfirmed;
  const isLocationPending = appt.modality === 'in-person' && locationMissing;

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
    const trimmed = cancelReason.trim();
    if (!trimmed) {
      setCancelError('Please provide a reason for cancelling or rescheduling this hold.');
      return;
    }
    setBusy(true);
    setCancelError('');
    try {
      const res = await cancelAppointment(appt.id, trimmed);
      if (!res.ok) {
        setCancelError(res.message);
        return;
      }
      setShowCancelModal(false);
      setCancelReason('');
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
              {outreachCount} / {maxOutreachAttempts} outreach attempt(s) logged · Latest: {outreachLog[outreachCount - 1].channel} ({outreachLog[outreachCount - 1].outcome})
            </div>
          )}
          {maxOutreachReached && !veteranConfirmed && (
            <div
              className="triage-inline-note"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: '#b45309',
                background: '#fffbeb',
                border: '1px solid #fef3c7',
                padding: '6px 10px',
                borderRadius: '6px',
                fontSize: '12px',
                marginTop: '6px',
              }}
            >
              <Clock size={13} aria-hidden="true" />
              <span>Standard outreach limit ({maxOutreachAttempts} attempts) reached. Awaiting claimant callback or supervisor escalation. Hold remains active.</span>
            </div>
          )}
          {isLocationPending && (
            <div
              className="triage-inline-note"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                color: '#b45309',
                background: '#fffbeb',
                border: '1px solid #fef3c7',
                padding: '6px 10px',
                borderRadius: '6px',
                fontSize: '12px',
                marginTop: '8px',
              }}
            >
              <MapPin size={13} aria-hidden="true" />
              <span>Veteran residential location pending. Please verify location in the header above before confirming.</span>
            </div>
          )}
          {error && <p className="intake-error" role="alert">{error}</p>}
          <div className="sched-coord-actions">
            <button
              type="button"
              className="intake-text-action"
              disabled={busy}
              onClick={() => {
                setCancelReason('');
                setCancelError('');
                setShowCancelModal(true);
              }}
            >
              Cancel Hold / Reschedule
            </button>
            <button
              type="button"
              className="intake-primary"
              disabled={busy || !veteranConfirmed || Boolean(isLocationPending)}
              title={isLocationPending ? 'Please verify Veteran location in the header above before confirming' : !veteranConfirmed ? 'Veteran confirmation required via outreach' : 'Confirm appointment and advance to Stage 4'}
              onClick={() => void handleConfirm()}
            >
              Confirm Appointment → Stage 4 <ArrowRight size={15} />
            </button>
          </div>
        </div>
      )}

      {showCancelModal && (
        <div
          className="dialog-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby={`cancel-hold-title-${appt.id}`}
          onClick={() => { if (!busy) setShowCancelModal(false); }}
        >
          <div className="workflow-dialog" onClick={e => e.stopPropagation()}>
            <div className="dialog-header">
              <div>
                <span className="dialog-kicker">STAGE 3 · CANCELLATION &amp; RESCHEDULE LOG</span>
                <h2 id={`cancel-hold-title-${appt.id}`}>Cancel Hold / Reschedule</h2>
                <p>Record why this held slot is being released. The reason and full appointment log will be saved to the database.</p>
              </div>
            </div>
            <div className="dialog-body">
              <div className="dialog-case">
                <Calendar size={18} />
                <span>
                  <b>{appt.facilityName || appt.facilityId}</b>
                  <small>{appt.examinerName || appt.examinerId} · {dateLabel(appt.startsAt)} · {modalityLabels[appt.modality]}</small>
                </span>
              </div>

              <div className="dialog-field">
                <span>Quick reasons (optional)</span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }}>
                  {[
                    'Veteran requested different date or time',
                    'No answer after outreach attempts',
                    'Location or travel limit change',
                    'Examiner or facility conflict',
                  ].map(preset => (
                    <button
                      key={preset}
                      type="button"
                      className="intake-secondary"
                      style={{ fontSize: '11px', padding: '4px 9px' }}
                      disabled={busy}
                      onClick={() => {
                        setCancelReason(preset);
                        setCancelError('');
                      }}
                    >
                      {preset}
                    </button>
                  ))}
                </div>
              </div>

              <label className="dialog-field">
                <span>Why are you cancelling or rescheduling? *</span>
                <textarea
                  rows={3}
                  value={cancelReason}
                  disabled={busy}
                  onChange={e => {
                    setCancelReason(e.target.value);
                    if (cancelError) setCancelError('');
                  }}
                  placeholder="Enter the reason for cancelling this hold…"
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    border: '1px solid #ccd5e7',
                    borderRadius: '10px',
                    fontSize: '12px',
                    fontFamily: 'inherit',
                    resize: 'vertical',
                  }}
                />
              </label>

              {cancelError && <p className="dialog-error" role="alert">{cancelError}</p>}
            </div>
            <div className="dialog-footer">
              <button
                type="button"
                className="intake-secondary"
                disabled={busy}
                onClick={() => setShowCancelModal(false)}
              >
                Keep Hold
              </button>
              <button
                type="button"
                className="intake-primary"
                disabled={busy || !cancelReason.trim()}
                onClick={() => void handleCancel()}
              >
                {busy ? 'Saving & Cancelling…' : 'Save & Cancel Hold'}
              </button>
            </div>
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
  const [editing, setEditing] = useState(!activeCase.postalCode);

  return (
    <div className="sched-location-wrap">
      <div className="triage-case-meta">
        <MapPin size={13} aria-hidden="true" style={{ display: 'inline', verticalAlign: '-2px' }} />{' '}
        {activeCase.postalCode ? (
          <span style={{ color: '#166534', fontWeight: 600 }}>
            {activeCase.city ? `${activeCase.city}, ` : ''}{activeCase.stateCode ? `${activeCase.stateCode} ` : ''}{activeCase.postalCode} · Location verified
          </span>
        ) : (
          <span style={{ color: '#b45309', fontWeight: 600 }}>
            Location pending
          </span>
        )}{' '}
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

function CoordinatedScheduleFinder({
  activeCase,
  onHeld,
}: {
  activeCase: SchedulingCaseView;
  onHeld: () => void;
}) {
  const { state, holdCoordinatedAppointments } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState<CoordinatedMatchResponse | null>(null);
  const [selectedPkgId, setSelectedPkgId] = useState('');

  const apptSignature = Object.values(state?.appointmentsById ?? {})
    .map(a => `${a.id}:${a.status}`)
    .join(',');

  const loadPackages = async () => {
    setOpen(true);
    setLoading(true);
    setError('');
    try {
      const res = await matchCoordinatedScheduling(activeCase.esrId);
      setData(res);
      setSelectedPkgId(res.bestPackageId || (res.packages[0]?.packageId ?? ''));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to find coordinated visit packages.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void matchCoordinatedScheduling(activeCase.esrId)
      .then(res => {
        if (cancelled) return;
        setData(res);
        setSelectedPkgId(prev =>
          res.packages.some(p => p.packageId === prev)
            ? prev
            : res.bestPackageId || res.packages[0]?.packageId || '',
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [apptSignature, open, activeCase.esrId]);

  const selectedPkg = data?.packages.find(p => p.packageId === selectedPkgId) ?? data?.packages[0];

  const handleHoldCoordinated = async () => {
    if (!selectedPkg || !selectedPkg.items.length) return;
    setBusy(true);
    setError('');
    try {
      const payload = selectedPkg.items.map(it => ({
        requirementId: it.requirementId,
        examinerId: it.examinerId,
        slot: {
          slotId: it.slotId,
          startsAt: it.startsAt,
          endsAt: it.endsAt,
          facilityId: selectedPkg.facilityId,
          modality: it.modality,
        },
      }));
      const res = await holdCoordinatedAppointments(payload);
      if (!res.ok) {
        setError(res.message);
        return;
      }
      setOpen(false);
      onHeld();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to hold coordinated package.');
    } finally {
      setBusy(false);
    }
  };

  const pendingRequirements = activeCase.requirements.filter(r => !r.appointment);

  if (pendingRequirements.length === 0) {
    return (
      <div
        className="sched-coordinated-done"
        style={{
          padding: '16px 20px',
          background: '#f0fdf4',
          borderRadius: '12px',
          border: '1px solid #bbf7d0',
          marginBottom: '20px',
        }}
      >
        <strong style={{ color: '#166534', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '14px' }}>
          <CheckCircle2 size={18} /> All Approved Examinations Coordinated &amp; Held
        </strong>
        <p style={{ margin: '6px 0 0', fontSize: '13px', color: '#166534' }}>
          All required examinations have been matched to appointment slots. Follow the standard claimant outreach and confirmation steps below.
        </p>
      </div>
    );
  }

  if (!open) {
    return (
      <div className="sched-finder-trigger" style={{ marginBottom: '20px' }}>
        <button type="button" className="intake-primary" onClick={() => void loadPackages()}>
          <Users size={15} /> Find Coordinated Same-Day Packages ({pendingRequirements.length} Pending)
        </button>
      </div>
    );
  }

  return (
    <div className="sched-finder-panel" style={{ marginBottom: '24px' }}>
      <div className="sched-finder-head">
        <div>
          <strong>Coordinated Multi-Exam Schedule · VA RFP 5.2.2 Compliance</strong>
          <small>Evaluating common facilities, qualified specialists &amp; conflict-free same-day slots</small>
        </div>
        <button type="button" className="intake-text-action" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>

      {loading && (
        <div className="sched-loading-row" role="status">
          <Loader2 size={16} className="triage-spinner" />
          <span>Searching cross-specialty availability, verifying active licenses on exam dates, and matching single-visit packages…</span>
        </div>
      )}

      {error && <p className="intake-error" role="alert">{error}</p>}

      {!loading && data && selectedPkg && (
        <div className="sched-coordinated-content" style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginTop: '12px' }}>
          <div
            style={{
              padding: '14px 18px',
              background: selectedPkg.coverageType === 'full' ? '#f0fdf4' : selectedPkg.coverageType === 'partial' ? '#fffbeb' : '#f8fafc',
              border: `1px solid ${selectedPkg.coverageType === 'full' ? '#bbf7d0' : selectedPkg.coverageType === 'partial' ? '#fef08a' : '#cbd5e1'}`,
              borderRadius: '10px',
              display: 'flex',
              flexDirection: 'column',
              gap: '6px',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                  fontWeight: 700,
                  fontSize: '13px',
                  color: selectedPkg.coverageType === 'full' ? '#166534' : selectedPkg.coverageType === 'partial' ? '#854d0e' : '#475569',
                }}
              >
                <CheckCircle2 size={16} /> {selectedPkg.badge}
              </span>
              <span style={{ fontSize: '11px', color: '#64748b' }}>
                {selectedPkg.contractualNotice}
              </span>
            </div>
            <p style={{ margin: 0, fontSize: '13px', color: '#334155', lineHeight: 1.5 }}>
              {selectedPkg.explanation}
            </p>
          </div>

          {data.packages.length > 1 && (
            <div className="sched-step-block">
              <span className="sched-step-kicker">
                Select Coordinated Package ({data.packages.length} options evaluated &amp; ranked)
              </span>
              <select
                value={selectedPkgId}
                onChange={e => setSelectedPkgId(e.target.value)}
                style={{ width: '100%', padding: '9px 12px', borderRadius: '8px', border: '1px solid #cbd5e1', fontSize: '13px' }}
              >
                {data.packages.map((pkg, idx) => {
                  const distLabel = pkg.distanceMiles != null ? ` · ~${pkg.distanceMiles} mi` : '';
                  const waitLabel = pkg.totalIdleWaitMinutes != null && pkg.items.length > 1 ? ` · ${pkg.totalIdleWaitMinutes}m wait` : '';
                  return (
                    <option key={pkg.packageId} value={pkg.packageId}>
                      {idx === 0 ? '★ [Recommended] ' : ''}{pkg.badge} · {pkg.facilityName} ({pkg.date}{distLabel}{waitLabel}) · {pkg.items.length} exams
                    </option>
                  );
                })}
              </select>
            </div>
          )}

          {selectedPkg.items.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ fontSize: '12px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.04em', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>Proposed Single-Visit Itinerary ({dateLabel(selectedPkg.visitStartsAt)} — {dateLabel(selectedPkg.visitEndsAt).split(',').slice(-1)[0].trim()})</span>
                {selectedPkg.totalIdleWaitMinutes != null && selectedPkg.items.length > 1 && (
                  <span style={{ fontSize: '11px', color: '#64748b', fontWeight: 500, textTransform: 'none' }}>
                    Total transition buffer: {selectedPkg.totalIdleWaitMinutes} min
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {selectedPkg.items.map((item, i) => (
                  <div
                    key={item.slotId || item.requirementId}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '12px 16px',
                      background: '#ffffff',
                      border: '1px solid #e2e8f0',
                      borderRadius: '8px',
                      gap: '12px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                      <span
                        style={{
                          width: '26px',
                          height: '26px',
                          borderRadius: '50%',
                          background: '#e0e7ff',
                          color: '#3730a3',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: '12px',
                          fontWeight: 700,
                        }}
                      >
                        {i + 1}
                      </span>
                      <div>
                        <strong style={{ fontSize: '14px', color: '#0f172a' }}>{item.examinationType}</strong>
                        <div style={{ fontSize: '12px', color: '#64748b', marginTop: '2px' }}>
                          <Stethoscope size={12} style={{ display: 'inline', verticalAlign: '-1px', marginRight: '4px' }} />
                          {item.examinerName} · {item.specialty} · License valid thru {item.licenseExpiryDate}
                        </div>
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ fontWeight: 600, fontSize: '13px', color: '#1e293b' }}>
                        <Clock size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: '4px' }} />
                        {dateLabel(item.startsAt)} ({item.durationMinutes} min)
                      </span>
                      <div style={{ fontSize: '11px', color: '#16a34a', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '4px', marginTop: '2px' }}>
                        <ShieldCheck size={12} /> License verified active
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {selectedPkg.unmatchedRequirements && selectedPkg.unmatchedRequirements.length > 0 && (
                <div style={{ padding: '10px 14px', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: '8px', fontSize: '12px', color: '#991b1b' }}>
                  <strong>Remaining Unmatched Requirement(s):</strong>
                  <ul style={{ margin: '4px 0 0', paddingLeft: '18px' }}>
                    {selectedPkg.unmatchedRequirements.map(u => (
                      <li key={u.requirementId}>{u.examinationType} — {u.reason}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="sched-hold-row" style={{ marginTop: '8px' }}>
                <span className="sched-license-ok">
                  <ShieldCheck size={14} /> All examiner licenses &amp; facility equipment pre-validated
                </span>
                <button
                  type="button"
                  className="intake-primary"
                  disabled={busy}
                  onClick={() => void handleHoldCoordinated()}
                >
                  {busy ? 'Holding Package…' : `Hold Coordinated Package (${selectedPkg.items.length} Appointments)`} <ArrowRight size={15} />
                </button>
              </div>
            </div>
          ) : (
            <div style={{ padding: '14px', background: '#f8fafc', borderRadius: '8px', fontSize: '13px', color: '#475569' }}>
              No common facility slots found. Please switch to <strong>Schedule Separately</strong> to book appointments individually.
            </div>
          )}
        </div>
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
  const [schedFilter, setSchedFilter] = useState<'all' | 'needs-scheduling' | 'outreach-pending' | 'scheduled'>('all');
  const [priorityFilter, setPriorityFilter] = useState<'all' | 'expedited' | 'coordinated' | 'location-pending'>('all');
  const [handoff, setHandoff] = useState<{ esrId: string; entityId?: string } | null>(null);
  const scheduling = state?.scheduling;
  const casesById = scheduling?.casesByEsrId ?? {};
  const orderedIds = scheduling?.queueIds ?? Object.keys(casesById);
  const allCases: SchedulingCaseView[] = orderedIds.map(id => casesById[id]).filter(Boolean);

  const needsSchedulingCases = allCases.filter(c => c.pendingCount > 0 && c.requirements.some(r => !r.appointment));
  const outreachPendingCases = allCases.filter(c => c.appointments.some(a => a?.status === 'held'));
  const fullyScheduledCases = allCases.filter(c => c.pendingCount === 0 && c.scheduledCount > 0);

  const schedFilters = [
    { key: 'all', label: 'All cases', count: allCases.length },
    { key: 'needs-scheduling', label: 'Needs Scheduling', count: needsSchedulingCases.length },
    { key: 'outreach-pending', label: 'Outreach Pending', count: outreachPendingCases.length },
    { key: 'scheduled', label: 'Fully Scheduled', count: fullyScheduledCases.length },
  ];

  const queryLower = query.trim().toLowerCase();
  const visibleCases = allCases.filter(c => {
    if (queryLower) {
      const matches = (
        c.claimantName.toLowerCase().includes(queryLower) ||
        c.esrId.toLowerCase().includes(queryLower) ||
        c.claimIdentifier.toLowerCase().includes(queryLower) ||
        c.requirements.some(r => r.examinationType.toLowerCase().includes(queryLower))
      );
      if (!matches) return false;
    }

    if (schedFilter === 'needs-scheduling' && !(c.pendingCount > 0 && c.requirements.some(r => !r.appointment))) {
      return false;
    }
    if (schedFilter === 'outreach-pending' && !c.appointments.some(a => a?.status === 'held')) {
      return false;
    }
    if (schedFilter === 'scheduled' && !(c.pendingCount === 0 && c.scheduledCount > 0)) {
      return false;
    }

    if (priorityFilter === 'expedited' && (c.priority as string) !== 'expedited' && (c.priority as string) !== 'urgent') {
      return false;
    }
    if (priorityFilter === 'coordinated' && c.requirements.length < 2) {
      return false;
    }
    if (priorityFilter === 'location-pending' && (c.postalCode && c.city)) {
      return false;
    }

    return true;
  });

  const activeCase = visibleCases.find(c => c.esrId === selectedId) ?? visibleCases[0];

  useEffect(() => {
    if (visibleCases.length > 0) {
      const isCurrentVisible = visibleCases.some(c => c.esrId === selectedId);
      if (!isCurrentVisible) {
        onSelect(visibleCases[0].esrId);
      }
    }
  }, [schedFilter, priorityFilter, query, visibleCases, selectedId, onSelect]);

  return (
    <div className="intake-page queue-workbench">
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
                  onChange={e => {
                    onQueryChange(e.target.value);
                  }}
                />
                {query && (
                  <button
                    type="button"
                    className="intake-icon-button"
                    aria-label="Clear scheduling search"
                    onClick={() => onQueryChange('')}
                  >
                    <X size={15} />
                  </button>
                )}
              </label>
              <div className="intake-queue-filters" role="group" aria-label="Scheduling operational filters">
                {schedFilters.map(f => (
                  <button
                    type="button"
                    key={f.key}
                    aria-pressed={schedFilter === f.key}
                    onClick={() => {
                      setSchedFilter(f.key as any);
                    }}
                  >
                    {f.label} <span>{f.count}</span>
                  </button>
                ))}
              </div>
              <label className="intake-status-filter" style={{ marginTop: '8px' }}>
                <span>Priority</span>
                <span className="intake-status-control">
                  <select
                    aria-label="Filter scheduling priority and focus"
                    value={priorityFilter}
                    onChange={e => {
                      setPriorityFilter(e.target.value as any);
                    }}
                  >
                    <option value="all">All Priorities &amp; Focus</option>
                    <option value="expedited">Expedited / Urgent SLA</option>
                    <option value="coordinated">Multi-Exam Coordinated (2+ exams)</option>
                    <option value="location-pending">Location Verification Needed</option>
                  </select>
                  <ChevronDown size={15} aria-hidden="true" />
                </span>
              </label>
            </div>

            {visibleCases.length ? (
              <ul className="intake-queue-list">
                {visibleCases.map(c => (
                  <li key={c.esrId}>
                    <button
                      type="button"
                      className="intake-queue-row"
                      aria-pressed={activeCase?.esrId === c.esrId}
                      onClick={() => {
                        onSelect(c.esrId);
                      }}
                    >
                      <span className="intake-queue-row-main">
                        <strong>{c.claimantName}</strong>
                        <small>
                          {c.claimIdentifier} · {c.approvedCount} exam{c.approvedCount === 1 ? '' : 's'}
                          {(c.priority as string) === 'expedited' || (c.priority as string) === 'urgent' ? ' · Expedited' : ''}
                        </small>
                      </span>
                      <Status
                        text={
                          c.pendingCount === 0
                            ? 'Fully Scheduled'
                            : c.appointments.some(a => a?.status === 'held')
                              ? `${c.appointments.filter(a => a?.status === 'held').length} Held · Outreach`
                              : `${c.scheduledCount}/${c.approvedCount} Scheduled`
                        }
                        tone={
                          c.pendingCount === 0
                            ? 'green'
                            : c.appointments.some(a => a?.status === 'held')
                              ? 'amber'
                              : 'slate'
                        }
                      />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="intake-queue-empty">
                <p>No cases match this filter.</p>
                <button
                  type="button"
                  className="intake-text-action"
                  onClick={() => {
                    setSchedFilter('all');
                    setPriorityFilter('all');
                    onQueryChange('');
                  }}
                >
                  Reset filters
                </button>
              </div>
            )}
          </div>
        </aside>

        {activeCase ? (
          <section
            className="intake-case-card triage-case-workspace queue-detail"
            key={activeCase.esrId}
            aria-label={`Scheduling details for ${activeCase.claimantName}`}
            tabIndex={0}
          >
            <RecordHeader name={activeCase.claimantName} initials={activeCase.initials}
              metadata={<><span>{activeCase.claimIdentifier}</span><span>· Priority: {activeCase.priority}</span></>}
              summary={<><Phone size={13} aria-hidden="true" /> {activeCase.phone} · {activeCase.email}</>}
              actions={
                <Status
                  text={activeCase.pendingCount === 0 ? 'All Scheduled' : `${activeCase.pendingCount} Pending`}
                  tone={activeCase.pendingCount === 0 ? 'green' : 'amber'}
                />
              } />
            <LocationVerifyBar key={activeCase.esrId} activeCase={activeCase} />

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
                <CoordinatedScheduleFinder
                  key={activeCase.esrId}
                  activeCase={activeCase}
                  onHeld={() => setHandoff(null)}
                />
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
          <section className="intake-case-empty">
            <Inbox size={24} />
            <h2><HeadingText>{visibleCases.length ? 'No case selected' : 'No examinations waiting in Scheduling'}</HeadingText></h2>
            <p>
              {visibleCases.length
                ? 'Select a patient case from the scheduling queue to coordinate facilities, examiners, and appointment slots.'
                : 'Approve an examination plan in Stage 2 (Medical Triage) to release it here.'}
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
