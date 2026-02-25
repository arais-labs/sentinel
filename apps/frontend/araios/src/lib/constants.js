// Only exports used by system pages (GithubTasks, Approvals)

export const GITHUB_TASK_STATUS = {
  detected:    { label: 'Detected',    tone: 'neutral' },
  queued:      { label: 'Queued',      tone: 'info' },
  in_analysis: { label: 'In Analysis', tone: 'warn' },
  work_ready:  { label: 'Work Ready',  tone: 'success' },
  handed_off:  { label: 'Handed Off',  tone: 'handed' },
  closed:      { label: 'Closed',      tone: 'neutral' },
};
export const GITHUB_TASK_STATUS_ORDER = ['detected', 'queued', 'in_analysis', 'work_ready', 'handed_off', 'closed'];

export const GITHUB_TASK_TYPE = {
  pr_review:   { label: 'PR Review',   tone: 'info' },
  code_review: { label: 'Code Review', tone: 'info' },
  bug_fix:     { label: 'Bug Fix',     tone: 'warn' },
  feature:     { label: 'Feature',     tone: 'success' },
  refactor:    { label: 'Refactor',    tone: 'neutral' },
  docs:        { label: 'Docs',        tone: 'neutral' },
};

export const APPROVAL_STATUS = {
  pending:  { label: 'Pending',  tone: 'warn' },
  approved: { label: 'Approved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'neutral' },
};
