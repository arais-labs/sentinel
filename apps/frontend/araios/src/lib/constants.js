// Only exports used by system pages (Tasks, Approvals)

export const TASK_STATUS = {
  backlog:     { label: 'Backlog',      tone: 'neutral' },
  todo:        { label: 'To Do',        tone: 'info' },
  in_progress: { label: 'In Progress',  tone: 'warn' },
  in_review:   { label: 'In Review',    tone: 'info' },
  blocked:     { label: 'Blocked',      tone: 'warn' },
  handoff:     { label: 'Handoff',      tone: 'handed' },
  done:        { label: 'Done',         tone: 'success' },
  cancelled:   { label: 'Cancelled',    tone: 'neutral' },

  // Legacy statuses kept readable for existing records.
  open:        { label: 'Open',         tone: 'neutral' },
  detected:    { label: 'Detected',     tone: 'neutral' },
  queued:      { label: 'Queued',       tone: 'info' },
  in_analysis: { label: 'In Analysis',  tone: 'warn' },
  work_ready:  { label: 'Work Ready',   tone: 'success' },
  handed_off:  { label: 'Handed Off',   tone: 'handed' },
  closed:      { label: 'Closed',       tone: 'neutral' },
};
export const TASK_STATUS_ORDER = [
  'backlog',
  'todo',
  'in_progress',
  'in_review',
  'blocked',
  'handoff',
  'done',
  'cancelled',
];

export const TASK_TYPE = {
  task:        { label: 'Task',         tone: 'neutral' },
  feature:     { label: 'Feature',      tone: 'success' },
  bug:         { label: 'Bug',          tone: 'warn' },
  research:    { label: 'Research',     tone: 'info' },
  ops:         { label: 'Ops',          tone: 'neutral' },
  integration: { label: 'Integration',  tone: 'info' },
  docs:        { label: 'Docs',         tone: 'neutral' },

  // GitHub-oriented types still supported.
  pr_review:   { label: 'PR Review',    tone: 'info' },
  code_review: { label: 'Code Review',  tone: 'info' },
  bug_fix:     { label: 'Bug Fix',      tone: 'warn' },
  refactor:    { label: 'Refactor',     tone: 'neutral' },
};

export const APPROVAL_STATUS = {
  pending:  { label: 'Pending',  tone: 'warn' },
  approved: { label: 'Approved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'neutral' },
};
