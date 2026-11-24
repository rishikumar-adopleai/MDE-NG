import { useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { AppointmentLogistics, Communication, Modality, WorkItem } from '../domain/model';
import type { schedulingView } from '../../server/stageViews';
import { ActionFeedback, dateLabel, modalityLabels, resultEntityId, StageActions, StageBlock, StageHandoff, StageWorkbench, useStageAction, type StageProps } from './StageWorkbench';

type SchedulingDetail = ReturnType<typeof schedulingView>;
const contactLabels = { phone: 'Phone', sms: 'Text message', email: 'Email' };

function ProviderMatch({ detail }: { detail: SchedulingDetail }) {
  const { holdAppointment } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const [modality, setModality] = useState<Modality>(detail.recommendedModality ?? detail.modalities[0]);
  const [examinerId, setExaminerId] = useState('');
  const [choice, setChoice] = useState(0);
  const candidates = detail.candidates.flatMap(candidate => {
    const path = candidate.pathways.find(entry => entry.modality === modality);
    return path ? [{ ...candidate, slots: path.slots }] : [];
  }).sort((a, b) => a.slots[0].travelDistanceMiles - b.slots[0].travelDistanceMiles ||
    Date.parse(a.slots[0].slot.startsAt) - Date.parse(b.slots[0].slot.startsAt));
  const examiner = candidates.find(entry => entry.id === examinerId) ?? candidates[0];
  const selected = examiner?.slots[choice];

  return <StageBlock title="Match an eligible examiner" description="Choose a pathway, examiner and available time. Saving a hold keeps the slot reserved while staff record contact and logistics.">
    <div className="stage-field-grid">
      <label className="intake-field">Pathway<select value={modality} onChange={event => { setModality(event.target.value as Modality); setExaminerId(''); setChoice(0); }}>
        {detail.modalities.map(value => <option key={value} value={value}>{modalityLabels[value]}</option>)}
      </select></label>
      <label className="intake-field">Examiner<select value={examiner?.id ?? ''} disabled={!candidates.length} onChange={event => { setExaminerId(event.target.value); setChoice(0); }}>
        {!candidates.length && <option value="">No eligible examiners</option>}
        {candidates.map(item => <option key={item.id} value={item.id}>{item.name} · {item.specialties.join(', ')}</option>)}
      </select></label>
    </div>
    {examiner && <label className="intake-field">Available time<select value={choice} onChange={event => setChoice(Number(event.target.value))}>
      {examiner.slots.map(({ slot, facility, travelDistanceMiles }, index) => <option key={`${slot.startsAt}-${slot.facilityId}`} value={index}>{dateLabel(slot.startsAt)} · {facility}{modality === 'in-person' ? ` · ${travelDistanceMiles} mi` : ''}</option>)}
    </select></label>}
    {selected && modality === 'in-person' && selected.travelDistanceMiles > selected.travelLimitMiles &&
      <p className="stage-muted">This slot requires express claimant consent for travel beyond {selected.travelLimitMiles} miles.</p>}
    {!candidates.length && <p className="triage-inline-note">No conflict-free slots match the current requirements. Try another permitted pathway when available.</p>}
    <ActionFeedback result={feedback} />
    <StageActions><button type="button" className="intake-primary" disabled={pending || !selected} onClick={() => selected && void execute(() => holdAppointment(detail.focusId, examiner.id, selected.slot))}>Hold selected slot <ArrowRight size={16} /></button></StageActions>
  </StageBlock>;
}

function AppointmentEditor({ detail, onClinical }: { detail: SchedulingDetail; onClinical: (esrId: string, entityId?: string) => void }) {
  const { recordOutreach, recordAppointmentLogistics, confirmAppointment, rescheduleAppointment, cancelAppointment } = useWorkspace();
  const { pending, feedback, execute } = useStageAction();
  const appointment = detail.appointment!;
  const confirmation = detail.confirmation!;
  const [channel, setChannel] = useState<'phone' | 'sms' | 'email'>('phone');
  const [outcome, setOutcome] = useState<Communication['outcome']>('answered');
  const [voicemail, setVoicemail] = useState<'' | 'left' | 'unavailable'>('');
  const [time, setTime] = useState('');
  const [contactNote, setContactNote] = useState('');
  const [logistics, setLogistics] = useState<Omit<AppointmentLogistics, 'travelDistanceMiles' | 'travelLimitMiles' | 'recordedAt'>>(() => appointment.localLogistics ?? {
    specialTransportRequired: false, specialTransportStatus: 'not-required', consentStatus: 'pending', vaPreapprovalStatus: 'pending', note: '',
  });
  const [replacement, setReplacement] = useState(0);
  const [militaryReference, setMilitaryReference] = useState('');
  const [cancelReason, setCancelReason] = useState('');
  const held = ['held','reschedule-requested'].includes(appointment.status);
  const phoneNoAnswer = channel === 'phone' && outcome === 'no-response';
  const showOutreachForm = held && !confirmation.attempts.some(attempt => attempt.outcome === 'answered') && confirmation.attempts.length < 3;
  const replaceWith = detail.replacements[replacement];

  return <>
    <StageBlock title={held ? 'Held appointment' : 'Confirmed appointment'}>
      <ul className="stage-records"><li><b>{dateLabel(appointment.startsAt)}</b><p>{detail.appointmentExaminer} · {detail.appointmentFacility?.name} · {modalityLabels[appointment.modality]}</p></li></ul>
      {appointment.localNotification && <p className="stage-muted">Notification record {appointment.localNotification.reference}{appointment.localNotification.representativeReference ? ' · Representative included' : ''}</p>}
      {appointment.status === 'confirmed-locally' && <StageActions><button type="button" className="intake-primary" onClick={() => onClinical(appointment.esrId)}>Open Clinical Evaluation <ArrowRight size={16} /></button></StageActions>}
    </StageBlock>
    <StageBlock title="Claimant contact" description="Record up to three attempts. Text and email appear when the claimant has opted in.">
      {!!confirmation.attempts.length && <ol className="stage-list">{confirmation.attempts.map(attempt => <li key={attempt.id}>{dateLabel(attempt.attemptedAt)} · {attempt.channel} · {attempt.outcome.replaceAll('-', ' ')}{attempt.voicemail && ` · Voicemail ${attempt.voicemail}`}</li>)}</ol>}
      {showOutreachForm && <form onSubmit={event => {
        event.preventDefault();
        void execute(() => recordOutreach(appointment.esrId, appointment.id, channel, outcome, contactNote, {
          attemptedAt: time ? new Date(time).toISOString() : undefined, voicemail: phoneNoAnswer && voicemail ? voicemail : undefined,
        }), () => { setContactNote(''); setTime(''); setVoicemail(''); });
      }}>
        <div className="stage-field-grid">
          <label className="intake-field">Channel<select value={channel} onChange={event => setChannel(event.target.value as typeof channel)}>{detail.contactChannels.map(value => <option key={value} value={value}>{contactLabels[value]}</option>)}</select></label>
          <label className="intake-field">Outcome<select value={outcome} onChange={event => setOutcome(event.target.value as typeof outcome)}>
            <option value="answered">Claimant answered</option><option value="no-response">No response</option><option value="delivered">Message delivered</option><option value="returned">Message returned</option>
          </select></label>
          {phoneNoAnswer && <label className="intake-field">Voicemail<select required value={voicemail} onChange={event => setVoicemail(event.target.value as typeof voicemail)}><option value="">Select disposition</option><option value="left">Message left</option><option value="unavailable">Voicemail unavailable</option></select></label>}
          <label className="intake-field">Attempt time (defaults to now)<input type="datetime-local" value={time} onChange={event => setTime(event.target.value)} /></label>
        </div>
        <label className="intake-field">Contact note<textarea required maxLength={3000} rows={2} value={contactNote} onChange={event => setContactNote(event.target.value)} /></label>
        <StageActions><button type="submit" className="intake-secondary" disabled={pending}>Record contact</button></StageActions>
      </form>}
    </StageBlock>
    {held && <StageBlock title="Travel and logistics" description={appointment.modality === 'in-person' ? `${appointment.localLogistics?.travelDistanceMiles ?? 0} estimated miles · ${appointment.localLogistics?.travelLimitMiles ?? 0}-mile consent threshold` : 'Remote pathway · no claimant travel'}>
      <form onSubmit={event => { event.preventDefault(); void execute(() => recordAppointmentLogistics(appointment.id, logistics)); }}>
        <div className="stage-field-grid">
          <label className="intake-field">Travel consent<select value={logistics.consentStatus} onChange={event => setLogistics({ ...logistics, consentStatus: event.target.value as typeof logistics.consentStatus })}><option value="pending">Select disposition</option><option value="not-required">Not required</option><option value="simulated-recorded">Express consent recorded</option></select></label>
          <label className="intake-field">Preapproval<select value={logistics.vaPreapprovalStatus} onChange={event => setLogistics({ ...logistics, vaPreapprovalStatus: event.target.value as typeof logistics.vaPreapprovalStatus })}><option value="pending">Select disposition</option><option value="not-required">Not required</option><option value="simulated-recorded">Preapproval recorded</option></select></label>
          <label className="intake-field">Special transport<select value={logistics.specialTransportStatus} onChange={event => {
            const status = event.target.value as typeof logistics.specialTransportStatus;
            setLogistics({ ...logistics, specialTransportStatus: status, specialTransportRequired: status === 'simulated-arranged' });
          }}><option value="pending">Select disposition</option><option value="not-required">Not required</option><option value="simulated-arranged">Arrangement recorded</option></select></label>
        </div>
        <label className="intake-field">Logistics note<textarea required rows={2} maxLength={3000} value={logistics.note} onChange={event => setLogistics({ ...logistics, note: event.target.value })} /></label>
        <StageActions><button type="submit" className="intake-secondary" disabled={pending}>Save logistics</button></StageActions>
      </form>
    </StageBlock>}
    <StageBlock title="Appointment changes">
      {detail.canReschedule && <details><summary>Reschedule</summary>
        <label className="intake-field">Replacement time<select value={replacement} onChange={event => setReplacement(Number(event.target.value))} disabled={!replaceWith}>
          {!replaceWith && <option value={0}>No available slot</option>}{detail.replacements.map(({ slot, facility }, index) => <option key={`${slot.startsAt}-${slot.facilityId}`} value={index}>{dateLabel(slot.startsAt)} · {facility}</option>)}
        </select></label>
        {detail.requiresMilitaryReference && <label className="intake-field">Military coordination reference<input value={militaryReference} onChange={event => setMilitaryReference(event.target.value)} /></label>}
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !replaceWith || (detail.requiresMilitaryReference && !militaryReference.trim())} onClick={() => replaceWith && void execute(() => rescheduleAppointment(appointment.id, replaceWith.slot, militaryReference))}>Hold replacement time</button></StageActions>
      </details>}
      <details><summary>Cancel appointment</summary>
        <label className="intake-field">Cancellation reason<textarea rows={2} maxLength={3000} value={cancelReason} onChange={event => setCancelReason(event.target.value)} /></label>
        <StageActions><button type="button" className="intake-secondary" disabled={pending || !cancelReason.trim()} onClick={() => void execute(() => cancelAppointment(appointment.id, cancelReason))}>Cancel appointment and reopen scheduling</button></StageActions>
      </details>
    </StageBlock>
    <div className="stage-action-footer">
      {held && confirmation.reasons.length > 0 && <ul className="stage-list">{confirmation.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
      <ActionFeedback result={feedback} />
      {held && <StageActions><button type="button" className="intake-primary" disabled={pending || confirmation.reasons.length > 0} onClick={() => void execute(() => confirmAppointment(appointment.id), result => onClinical(appointment.esrId, resultEntityId(result.value)))}>
        {confirmation.method === 'reserve' ? 'Confirm reserve appointment' : 'Confirm and release to Clinical'} <ArrowRight size={16} />
      </button></StageActions>}
    </div>
  </>;
}

export function SchedulingPage(props: StageProps) {
  const [handoff, setHandoff] = useState<{esrId: string; entityId?: string}>();
  const render = (detail: SchedulingDetail, item: WorkItem) => <>
    <StageBlock title="Approved exam scope">
      <p className="stage-muted">{detail.requirements.map(requirement => requirement.examinationType).join(' · ')}</p>
      <p className="stage-muted">{detail.accessibilityNeeds.length ? `Accessibility: ${detail.accessibilityNeeds.join(', ')}` : 'No recorded accessibility accommodations'}{detail.examinerPreference ? ` · Examiner preference: ${detail.examinerPreference}` : ''}</p>
    </StageBlock>
    {detail.appointment ? <AppointmentEditor key={`${item.id}-${detail.appointment.startsAt}`} detail={detail} onClinical={(esrId, entityId) => {
      if (detail.appointment?.status === 'confirmed-locally') props.onNavigate('Clinical Evaluation', esrId, entityId);
      else setHandoff({esrId, entityId});
    }} /> : <ProviderMatch detail={detail} />}
  </>;
  return <StageWorkbench {...props} stage="Scheduling" title="Scheduling queue" description="Match approved exams to eligible appointments and record the scheduling handoff."
    handoff={handoff && <StageHandoff stage="Clinical Evaluation" esrId={handoff.esrId} entityId={handoff.entityId} onNavigate={props.onNavigate}>Appointment confirmed and clinical work released</StageHandoff>} render={render} />;
}
