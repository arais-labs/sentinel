import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check, ChevronDown, Crosshair, Keyboard, List, Pause, X } from 'lucide-react';
import { Logo } from '../ui/Logo';
import { requestWorkspaceTab, sessionLayoutKey } from '../../store/workspace-store';
import { useActiveSessionStore } from '../../store/active-session-store';
import { shortcutKeys, tourChapters, tourSteps } from './product-tour-content';
import { TourKeycaps, TourShortcuts } from './TourShortcuts';
import { useTourObservation } from './useTourObservation';
import { visibleTarget } from './tour-observation';
import { emptyProgress, readProgress, saveProgress, taskKey } from './tour-progress';
import './product-tour.css';

export function ProductTour({ instanceName, onClose }: { instanceName: string; onClose: () => void }) {
  const navigate = useNavigate(),
    location = useLocation();
  const [progress, setProgress] = useState(() => readProgress(instanceName));
  const [index, setIndex] = useState(-1);
  const [panel, setPanel] = useState<'lesson' | 'contents' | 'shortcuts'>('lesson');
  const [collapsed, setCollapsed] = useState(false);
  const [spotlight, setSpotlight] = useState(false);
  const [advanceFrom, setAdvanceFrom] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const current = tourSteps[index];
  const isVerified = (step: (typeof tourSteps)[number]) =>
    step.tasks.every((task) => progress.verified.includes(taskKey(step.id, task.id)));
  const task = current?.tasks.find((item) => !progress.verified.includes(taskKey(current.id, item.id)));
  const view = useTourObservation(current, task, instanceName, () => {
    if (!current || !task) return;
    const id = taskKey(current.id, task.id);
    if (
      current.tasks.every((item) => item === task || progress.verified.includes(taskKey(current.id, item.id)))
    ) {
      setAdvanceFrom(current.id);
    }
    setProgress((value) =>
      value.verified.includes(id)
        ? value
        : {
            ...value,
            verified: [...value.verified, id],
            skipped: value.skipped.filter((step) => step !== current.id),
          }
    );
  });
  const total = tourSteps.reduce((sum, step) => sum + step.tasks.length, 0);
  const remaining = tourSteps.filter((step) => !isVerified(step));
  const invitation = index < 0;
  const summary = index >= tourSteps.length;
  const modal = invitation || summary;
  const resumeIndex = Math.max(
    0,
    tourSteps.findIndex((step) => step.id === progress.step)
  );
  useEffect(() => {
    saveProgress(instanceName, progress);
  }, [instanceName, progress]);

  // Advance only after a new completion. Revisiting an already verified lesson
  // stays put, and opening the reference or a native dialog pauses the timer.
  useEffect(() => {
    if (!current || advanceFrom !== current.id || task || panel !== 'lesson' || collapsed || view.nativeModal)
      return;
    const timer = window.setTimeout(() => go(index + 1), 700);
    return () => window.clearTimeout(timer);
  }, [advanceFrom, current?.id, task?.id, index, panel, collapsed, view.nativeModal]);

  // The invitation owns focus. During practice the live application owns it,
  // including all shortcuts and native dialogs; the guide just observes.
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    heading.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
      }
      if (event.key !== 'Tab') return;
      const buttons = Array.from(
        dialog.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []
      );
      if (
        event.shiftKey &&
        (document.activeElement === buttons[0] || document.activeElement === heading.current)
      ) {
        event.preventDefault();
        buttons.at(-1)?.focus();
      } else if (!event.shiftKey && document.activeElement === buttons.at(-1)) {
        event.preventDefault();
        buttons[0]?.focus();
      }
    };
    document.addEventListener('keydown', key, true);
    return () => {
      document.removeEventListener('keydown', key, true);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [modal]);

  function go(next: number) {
    setAdvanceFrom(null);
    setIndex(next);
    setPanel('lesson');
    setCollapsed(false);
    setSpotlight(false);
    if (tourSteps[next]) setProgress((value) => ({ ...value, step: tourSteps[next].id }));
  }
  function showPane(pane: NonNullable<typeof current>['preparePane']) {
    if (!pane) return;
    const session = useActiveSessionStore.getState().byInstance[instanceName] ?? null;
    requestWorkspaceTab(sessionLayoutKey(instanceName, session), pane);
    const workspace = `/instances/${encodeURIComponent(instanceName)}/workspace`;
    if (location.pathname !== workspace) navigate(workspace);
  }
  function start(next: number) {
    showPane('sessions');
    go(next);
  }
  function skip() {
    if (current && !isVerified(current))
      setProgress((value) => ({ ...value, skipped: [...new Set([...value.skipped, current.id])] }));
    go(index + 1);
  }

  const chapters = (
    <div className="tour-chapters">
      {tourChapters.map((chapter, number) => {
        const steps = tourSteps.filter((step) => step.chapter === chapter);
        const count = steps.filter(isVerified).length;
        return (
          <button key={chapter} onClick={() => start(tourSteps.indexOf(steps[0]))}>
            <span className="tour-chapter-number">
              {count === steps.length ? <Check size={14} /> : `0${number + 1}`}
            </span>
            <span>
              <strong>{chapter}</strong>
              <small>
                {chapter === 'Conversations'
                  ? 'Write, switch, organize'
                  : chapter === 'Your workspace'
                    ? 'Arrange, connect, navigate'
                    : 'Inspect, tune, automate'}
              </small>
            </span>
            <span className="tour-chapter-count">
              {count} / {steps.length}
            </span>
            <ArrowRight size={14} />
          </button>
        );
      })}
    </div>
  );

  if (modal)
    return createPortal(
      <div className="sentinel-tour tour-overlay" data-product-tour data-tour-welcome>
        <div
          ref={dialog}
          className="tour-invitation"
          role="dialog"
          aria-modal="true"
          aria-labelledby="tour-title"
        >
          <header className="tour-brand">
            <span>
              <Logo size={22} />
              <strong>SENTINEL</strong>
              <span>FIELD GUIDE</span>
            </span>
            <button className="tour-icon" aria-label="Close guided tour" onClick={onClose}>
              <X size={17} />
            </button>
          </header>
          <div className="tour-invitation-body">
            <div className="tour-eyebrow">{summary ? 'Your progress' : 'Learn by doing'}</div>
            <h1 ref={heading} tabIndex={-1} id="tour-title">
              {summary ? (
                'Your workspace. Your rhythm.'
              ) : (
                <>
                  Get comfortable.
                  <br />
                  Then get to work.
                </>
              )}
            </h1>
            <p>
              {summary
                ? `${progress.verified.length} of ${total} actions verified in your workspace. ${remaining.length ? 'Skipped actions stay open, so you can return when the right chat or workspace is ready.' : 'You have practiced the controls and shortcuts in the actual app.'}`
                : 'A hands-on introduction to your conversations, workspaces, and the work happening inside them. Try the controls yourself; the guide checks the result.'}
            </p>
            {chapters}
            <div className="tour-invitation-note">
              <Keyboard size={16} />
              <span>Real shortcuts. Confirmed actions. At your own pace.</span>
            </div>
          </div>
          <footer className="tour-invitation-footer">
            <button className="tour-text" onClick={onClose}>
              {summary ? 'Return to workspace' : 'Not now'}
            </button>
            {summary ? (
              remaining.length > 0 && (
                <button className="tour-primary" onClick={() => start(tourSteps.indexOf(remaining[0]))}>
                  Review unfinished steps
                  <ArrowRight size={15} />
                </button>
              )
            ) : (
              <div className="tour-invitation-buttons">
                {!!progress.verified.length && (
                  <button
                    className="tour-text"
                    onClick={() => {
                      setProgress(emptyProgress());
                      start(0);
                    }}
                  >
                    Start over
                  </button>
                )}
                <button className="tour-primary" onClick={() => start(resumeIndex)}>
                  {progress.verified.length || resumeIndex ? 'Resume guide' : 'Begin the guide'}
                  <ArrowRight size={16} />
                </button>
              </div>
            )}
          </footer>
        </div>
      </div>,
      document.body
    );

  // The native top layer covers the guide while the user completes a real dialog.
  if (view.nativeModal) return null;
  const width = Math.min(400, view.width - 24);
  const left = view.rect && view.rect.left > width + 32 ? 16 : Math.max(12, view.width - width - 16);
  const atTop = view.rect ? view.rect.top > view.height / 2 : false;
  const done = !task;
  return createPortal(
    <div className="sentinel-tour tour-live" data-product-tour data-dragging={view.dragging || undefined}>
      {view.rect && !view.dragging && !collapsed && panel === 'lesson' && (
        <div className="tour-highlight" data-spotlight={spotlight || undefined} style={view.rect} />
      )}
      <aside
        className="tour-coach"
        data-collapsed={collapsed || undefined}
        role="region"
        aria-label="Guided tour"
        style={{ width, left, ...(atTop ? { top: Math.min(64, view.height / 8) } : { bottom: 16 }) }}
      >
        <header className="tour-coach-header">
          <span>
            <Logo size={17} />
            <strong>FIELD GUIDE</strong>
            <span>
              {index + 1} / {tourSteps.length}
            </span>
          </span>
          <nav aria-label="Guide controls">
            <button
              className="tour-icon"
              aria-label="Guide contents"
              aria-pressed={panel === 'contents'}
              onClick={() => {
                setPanel(panel === 'contents' ? 'lesson' : 'contents');
                setCollapsed(false);
              }}
            >
              <List size={16} />
            </button>
            <button
              className="tour-icon"
              aria-label="Shortcut reference"
              aria-pressed={panel === 'shortcuts'}
              onClick={() => {
                setPanel(panel === 'shortcuts' ? 'lesson' : 'shortcuts');
                setCollapsed(false);
              }}
            >
              <Keyboard size={16} />
            </button>
            <button
              className="tour-icon"
              aria-label={collapsed ? 'Expand guide' : 'Minimize guide'}
              onClick={() => setCollapsed(!collapsed)}
            >
              <ChevronDown size={16} style={{ transform: collapsed ? 'rotate(180deg)' : undefined }} />
            </button>
            <button className="tour-icon" aria-label="Finish guide later" onClick={onClose}>
              <X size={16} />
            </button>
          </nav>
        </header>
        {!collapsed && (
          <>
            <div
              className="tour-progress"
              role="progressbar"
              aria-label="Verified actions"
              aria-valuenow={progress.verified.length}
              aria-valuemin={0}
              aria-valuemax={total}
            >
              {tourChapters.map((chapter) => {
                const tasks = tourSteps
                  .filter((step) => step.chapter === chapter)
                  .flatMap((step) => step.tasks.map((item) => taskKey(step.id, item.id)));
                return (
                  <span key={chapter}>
                    <i
                      style={{
                        width: `${(tasks.filter((id) => progress.verified.includes(id)).length / tasks.length) * 100}%`,
                      }}
                    />
                  </span>
                );
              })}
            </div>
            <div className="tour-coach-body">
              {panel === 'shortcuts' ? (
                <TourShortcuts />
              ) : panel === 'contents' ? (
                <div className="tour-contents">
                  <h2>Your field guide.</h2>
                  <p>Jump to a topic. Only actions you complete are marked verified.</p>
                  {tourChapters.map((chapter) => (
                    <section key={chapter}>
                      <h3>{chapter}</h3>
                      {tourSteps.map(
                        (step, i) =>
                          step.chapter === chapter && (
                            <button
                              key={step.id}
                              aria-current={i === index ? 'step' : undefined}
                              onClick={() => go(i)}
                            >
                              <span
                                className="tour-step-indicator"
                                data-verified={isVerified(step) || undefined}
                              >
                                {isVerified(step) ? <Check size={12} /> : i + 1}
                              </span>
                              <span>
                                {step.title}
                                <small>
                                  {isVerified(step)
                                    ? 'Verified'
                                    : progress.skipped.includes(step.id)
                                      ? 'Skipped · return anytime'
                                      : `${step.tasks.filter((item) => progress.verified.includes(taskKey(step.id, item.id))).length} / ${step.tasks.length} actions`}
                                </small>
                              </span>
                              <ArrowRight size={13} />
                            </button>
                          )
                      )}
                    </section>
                  ))}
                </div>
              ) : (
                <div className="tour-lesson" key={current.id}>
                  <div className="tour-eyebrow">{current.chapter}</div>
                  <h2>{current.title}</h2>
                  <p>{current.instruction}</p>
                  {task?.shortcut && (
                    <div className="tour-practice-keys">
                      <TourKeycaps combinations={shortcutKeys[task.shortcut]} pressed={view.pressed} />
                      <span>{task.shortcut === 'command' ? 'Hold to reveal' : 'Try it in the app'}</span>
                    </div>
                  )}
                  <ol className="tour-tasks" aria-label="Practice actions">
                    {current.tasks.map((item, i) => {
                      const verified = progress.verified.includes(taskKey(current.id, item.id));
                      return (
                        <li
                          key={item.id}
                          data-verified={verified || undefined}
                          data-current={item === task || undefined}
                        >
                          <span className="tour-task-indicator">
                            {verified ? <Check size={13} /> : i + 1}
                          </span>
                          <span>{verified ? item.success : item.label}</span>
                        </li>
                      );
                    })}
                  </ol>
                  <div
                    className="tour-feedback"
                    data-verified={done || undefined}
                    role="status"
                    aria-live="polite"
                  >
                    {done ? (
                      <>
                        <Check size={14} />
                        {advanceFrom === current.id
                          ? 'Verified · Moving to the next step…'
                          : 'Verified in your workspace'}
                      </>
                    ) : (
                      <>
                        <span className="tour-listening" />
                        {view.shortcutHint ??
                          (task?.shortcut
                            ? 'Waiting for the shortcut and its result'
                            : 'Waiting for the action in the app')}
                      </>
                    )}
                  </div>
                  {view.unavailable && !done && (
                    <div className="tour-unavailable">
                      <p>{current.unavailable}</p>
                      {current.preparePane && (
                        <button className="tour-text" onClick={() => showPane(current.preparePane)}>
                          Show {current.preparePane === 'sessions' ? 'Chat' : 'Files'}
                          <ArrowRight size={12} />
                        </button>
                      )}
                    </div>
                  )}
                  <p className="tour-explanation">{current.detail}</p>
                  {view.rect && (
                    <button
                      className="tour-locate"
                      onClick={() => {
                        setSpotlight(!spotlight);
                        visibleTarget(current.target)?.scrollIntoView({
                          block: 'nearest',
                          inline: 'nearest',
                          behavior: matchMedia('(prefers-reduced-motion: reduce)').matches
                            ? 'auto'
                            : 'smooth',
                        });
                      }}
                    >
                      <Crosshair size={13} />
                      {spotlight ? 'Clear highlight' : 'Locate the control'}
                    </button>
                  )}
                </div>
              )}
            </div>
            <footer className="tour-coach-footer">
              {panel !== 'lesson' ? (
                <button className="tour-text" onClick={() => setPanel('lesson')}>
                  <ArrowLeft size={14} />
                  Back to practice
                </button>
              ) : (
                <>
                  <button
                    className="tour-icon"
                    disabled={index === 0}
                    aria-label="Previous step"
                    onClick={() => go(index - 1)}
                  >
                    <ArrowLeft size={15} />
                  </button>
                  <button className="tour-text" onClick={done ? onClose : skip}>
                    {done ? (
                      <>
                        <Pause size={12} />
                        Finish later
                      </>
                    ) : (
                      'Skip for now'
                    )}
                  </button>
                  <button className="tour-primary" disabled={!done} onClick={() => go(index + 1)}>
                    {index === tourSteps.length - 1 ? 'Review progress' : 'Continue'}
                    <ArrowRight size={14} />
                  </button>
                </>
              )}
            </footer>
          </>
        )}
      </aside>
    </div>,
    document.body
  );
}
