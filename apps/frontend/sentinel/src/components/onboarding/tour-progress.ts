import { tourSteps } from './product-tour-content';

export type TourProgress = { step: string; verified: string[]; skipped: string[] };
export const taskKey = (step: string, task: string) => `${step}/${task}`;
const validTasks = new Set(tourSteps.flatMap((step) => step.tasks.map((task) => taskKey(step.id, task.id))));
const validSteps = new Set(tourSteps.map((step) => step.id));
export const emptyProgress = (): TourProgress => ({ step: tourSteps[0].id, verified: [], skipped: [] });
export function readProgress(instance: string): TourProgress {
  try {
    const value = JSON.parse(localStorage.getItem(`sentinel.product-tour:${instance}`) ?? 'null');
    if (!value || typeof value !== 'object') return emptyProgress();
    return {
      step: validSteps.has(value.step) ? value.step : tourSteps[0].id,
      verified: Array.isArray(value.verified)
        ? [
            ...new Set<string>(
              value.verified.filter((id: unknown) => typeof id === 'string' && validTasks.has(id))
            ),
          ]
        : [],
      skipped: Array.isArray(value.skipped)
        ? value.skipped.filter((id: unknown) => typeof id === 'string' && validSteps.has(id))
        : [],
    };
  } catch {
    return emptyProgress();
  }
}
export function saveProgress(instance: string, progress: TourProgress) {
  try {
    localStorage.setItem(`sentinel.product-tour:${instance}`, JSON.stringify(progress));
  } catch {
    /* The guide also works without persistence. */
  }
}
