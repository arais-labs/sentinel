type KeyObserver = (event: KeyboardEvent) => void;
type Capture = { observers: Set<KeyObserver>; dispatch: KeyObserver };

// Install before product components register their shortcut handlers. They can
// consume keydown, and macOS may not deliver the corresponding letter keyup.
// The idle bridge retains no keys or app state; only an open guide subscribes.
// Preserve its position in the listener order across development hot reloads.
const existing = import.meta.hot?.data.tourKeyboard as Capture | undefined;
const capture: Capture = existing ?? {
  observers: new Set(),
  dispatch(event) {
    capture.observers.forEach((observer) => observer(event));
  },
};
if (!existing) window.addEventListener('keydown', capture.dispatch, true);
if (import.meta.hot) import.meta.hot.data.tourKeyboard = capture;

export function observeTourKeydown(observer: KeyObserver) {
  capture.observers.add(observer);
  return () => {
    capture.observers.delete(observer);
  };
}
