import { CalendarDays, ClipboardCheck, HeartPulse, Inbox, Send, Stethoscope } from 'lucide-react';
import type { StageKey as DomainStageKey } from '../domain/model';

export type StageKey = DomainStageKey;
export type Tone = 'blue' | 'green' | 'amber' | 'red' | 'violet' | 'slate';
export type CaseItem = { id: string; claim: string; name: string; initials: string; location: string; priority: 'Routine' | 'Elevated' | 'Urgent'; exam: string; dueFull: string; days: number; elapsed: number; total: number; state: string; owner: string; contentions: string[] };

export const navItems: { name: StageKey; icon: typeof Inbox }[] = [
  { name: 'Intake', icon: Inbox }, { name: 'Medical Triage', icon: HeartPulse },
  { name: 'Scheduling', icon: CalendarDays }, { name: 'Clinical Evaluation', icon: Stethoscope },
  { name: 'Internal QA', icon: ClipboardCheck }, { name: 'VA Delivery & Review', icon: Send },
];

export const stageCopy: Record<StageKey, [string, string, string]> = {
  Intake: ['EVENT INTAKE', 'Intake event workbench', 'Review received requests, document processing status, and exceptions before routing work to Medical Triage.'],
  'Medical Triage': ['EVIDENCE & EXAM PLANNING', 'Medical evidence canvas', 'Review each contention, its source records, and a human-confirmed examination plan.'],
  Scheduling: ['EXAMINER & APPOINTMENT COORDINATION', 'Appointment scheduling studio', 'Match eligible examiners, record outreach, and manage each examination appointment.'],
  'Clinical Evaluation': ['CLINICAL DOCUMENTATION', 'Clinical DBQ workstation', 'Complete examination-specific DBQs with saved drafts, diagnostic review, and clinician-controlled disposition.'],
  'Internal QA': ['QUALITY & CORRECTION', 'QA findings workbench', 'Review individual report versions, resolve findings, and route correction work to the clinician.'],
  'VA Delivery & Review': ['RESULTS DELIVERY', 'Delivery & reconciliation console', 'Validate approved results and record package preparation and review.'],
};

