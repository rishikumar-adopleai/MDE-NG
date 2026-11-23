import { createSeedWorkspace } from '../src/data/initialization/seedWorkspace.ts';
import { refreshWorkItems } from '../src/domain/selectors.ts';
import { stageViews } from './stageViews.ts';
import { preparePracticeWorkflow, refreshPracticeSlots } from './practiceWorkflow.mjs';
import * as workflow from '../src/services/workflowService.ts';

const actions = {
  reviewContention: workflow.reviewContention,
  recordOutreach: workflow.recordOutreach,
  holdAppointment: workflow.holdAppointment,
  confirmAppointment: workflow.confirmAppointment,
  setSchedulingDistanceLimit: workflow.setSchedulingDistanceLimit,
  recordAppointmentLogistics: workflow.recordAppointmentLogistics,
  rescheduleAppointment: workflow.rescheduleAppointment,
  cancelAppointment: workflow.cancelAppointment,
  saveDBQDraft: workflow.saveDBQDraft,
  validateDBQ: workflow.validateDBQ,
  reviewDiagnostic: workflow.reviewDiagnostic,
  recordDiagnosticResult: workflow.recordDiagnosticResult,
  recordExamEncounter: workflow.recordExamEncounter,
  recordDiagnosticException: workflow.recordDiagnosticException,
  completeClinicalReview: workflow.completeClinicalReview,
  requestCorrection: workflow.requestCorrection,
  addQAFinding: workflow.addQAFinding,
  resubmitCorrection: workflow.resubmitCorrection,
  resolveQAFinding: workflow.resolveQAFinding,
  approveQAReview: workflow.approveQAReview,
  runQAChecklist: workflow.runQAChecklist,
  ensureResultsPackage: workflow.ensureResultsPackage,
  validateResultsPackage: workflow.validateResultsPackage,
  prepareResultsPackage: workflow.prepareResultsPackage,
  completeReconciliationTask: workflow.completeReconciliationTask,
  createReconciliationTask: workflow.createReconciliationTask,
  recordDeliveryReview: workflow.recordDeliveryReview,
  simulatePackageTransmission: workflow.simulatePackageTransmission,
  recordSimulatedPackageResponse: workflow.recordSimulatedPackageResponse,
  recordSimulatedContentionCancellation: workflow.recordSimulatedContentionCancellation,
  closeESR: workflow.closeESR,
  decideInsight: workflow.decideInsight,
};

if (process.argv[2] === '--seed') {
  process.stdout.write(JSON.stringify(createSeedWorkspace()));
} else {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  const payload = JSON.parse(input);
  if (payload.state) refreshPracticeSlots(payload.state);
  if (process.argv[2] === '--bootstrap') {
    let state = payload;
    for (const esr of Object.values(payload.esrsById)) {
      if (!esr.workflowClosedAt && (esr.receiptStatus !== 'received' || !esr.aepRecordedAt)) {
        state = workflow.recordIntakeReceipt(state, esr.id);
      }
    }
    await new Promise((resolve, reject) => process.stdout.write(JSON.stringify(refreshWorkItems(state)), error => error ? reject(error) : resolve()));
    process.exitCode = 0;
  } else if (payload.action === '$prepare-practice') {
    process.stdout.write(JSON.stringify(preparePracticeWorkflow(payload.state, payload.targets)));
  } else if (payload.action === '$stage-view') {
    const item = Object.hasOwn(payload.state.workItemsById, payload.args[0]) ? payload.state.workItemsById[payload.args[0]] : undefined;
    try {
      if (!item || ['completed', 'cancelled'].includes(item.status) || !Object.hasOwn(stageViews, item.stage)) {
        throw new workflow.WorkflowRuleError('This work item is no longer available in the selected stage.');
      }
      process.stdout.write(JSON.stringify({ stage: item.stage, detail: stageViews[item.stage](payload.state, item) }));
    } catch (error) {
      if (!(error instanceof workflow.WorkflowRuleError)) throw error;
      process.stdout.write(JSON.stringify({ error: error.message }));
    }
  } else {
    await applyCommand(payload);
  }
}

function applyCommand({ action, args, state, actorId }) {
  if (!Object.hasOwn(actions, action)) throw new Error('Unknown workspace action.');
  try {
    const supplied = args.map(arg => arg === null ? undefined : arg);
    const outcome = action === 'completeClinicalReview'
      ? workflow.completeClinicalReview(state, supplied[0], supplied[1], actorId)
      : action === 'approveQAReview'
        ? workflow.approveQAReview(state, supplied[0], supplied[1], actorId)
        : actions[action](state, ...supplied);
    const { state: changed, ...value } = 'state' in outcome ? outcome : { state: outcome };
    const now = new Date().toISOString();
    for (const activity of Object.values(changed.activitiesById)) {
      if (!state.activitiesById[activity.id]) activity.actorId = actorId;
    }
    for (const map of ['communicationsById', 'outreachAttemptsById', 'diagnosticResultsById', 'dbqVersionsById', 'deliveryEventsById']) {
      for (const item of Object.values(changed[map])) {
        if (!state[map][item.id] && 'actorId' in item) item.actorId = actorId;
      }
    }
    const next = refreshWorkItems({ ...changed, revision: state.revision + 1, updatedAt: now }, now);
    if (action === 'approveQAReview') {
      const review = next.qaReviewsById[supplied[0]];
      const pack = Object.values(next.resultsPackagesById).find(item =>
        item.esrId === review.esrId && item.preparationStatus !== 'closed' && item.validationStatus !== 'blocked');
      value.packageReady = Boolean(pack);
      value.entityId = pack?.id;
    }
    if (action === 'confirmAppointment') {
      const examId = next.appointmentsById[supplied[0]].examinationId;
      value.entityId = Object.values(next.workItemsById).find(item =>
        item.stage === 'Clinical Evaluation' && next.dbqsById[item.entityId]?.examinationId === examId)?.entityId;
    }
    if (action === 'completeClinicalReview') {
      value.entityId = Object.values(next.qaReviewsById).find(item => item.dbqId === supplied[0])?.id;
    }
    if (action === 'requestCorrection') value.entityId = next.qaFindingsById[supplied[0]]?.dbqId;
    if (action === 'recordSimulatedPackageResponse') {
      if (supplied[1] === 'report-fix') value.entityId = Object.values(next.qaReviewsById).find(item => item.dbqId === supplied[3])?.id;
      if (supplied[1] === 'new-exam-requested') value.entityId = next.contentionsById[supplied[3]]?.examinationRequirementIds.at(-1);
    }
    process.stdout.write(JSON.stringify({ state: next, value }));
  } catch (error) {
    if (error instanceof TypeError) {
      process.stdout.write(JSON.stringify({ error: 'Invalid arguments for this workflow action.' }));
    } else if (error instanceof workflow.WorkflowRuleError) {
      process.stdout.write(JSON.stringify({ error: error.message }));
    } else {
      throw error;
    }
  }
}
