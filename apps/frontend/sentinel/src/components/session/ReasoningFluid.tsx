import { useEffect, useId, useState } from 'react';

const fluidColors = ['#459aff', '#24cfdf', '#4bd888', '#d8e74e', '#ffb039', '#ff6258'];

export function reasoningColor(position: number) {
  const scaled = Math.max(0, Math.min(1, position)) * (fluidColors.length - 1);
  const index = Math.min(fluidColors.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  const channels = [1, 3, 5].map(offset => Math.round(
    parseInt(fluidColors[index].slice(offset, offset + 2), 16) * (1 - fraction)
    + parseInt(fluidColors[index + 1].slice(offset, offset + 2), 16) * fraction));
  return `rgb(${channels.join(', ')})`;
}

/** Horizontal liquid gauge: a continuous spectrum and two soft surface waves. */
export function ReasoningFluid({ amount }: { amount: number }) {
  const id = useId().replace(/:/g, '');
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  const fill = Math.max(0, Math.min(1, amount));
  const edge = fill * 1000;
  const amplitude = fill > 0 && fill < 1 ? Math.min(24, edge / 3) : 0;
  const surface = (phase: number, bend = -phase, middle = 20) => {
    const a = edge + amplitude * phase;
    const b = edge + amplitude * bend;
    return `M0 0H${edge}C${a} ${middle * .3} ${a} ${middle * .7} ${edge} ${middle}S${b} ${middle + (40 - middle) * .65} ${edge} 40H0Z`;
  };
  const colors = fluidColors;
  const front = [surface(.4, -.8, 17), surface(-.7, .3, 24), surface(.9, .5, 14), surface(-.2, -.9, 21), surface(.6, -.2, 26), surface(.4, -.8, 17)].join(';');
  const back = [surface(-.5, .7, 23), surface(.8, -.4, 15), surface(.2, .9, 25), surface(-.8, -.3, 18), surface(.3, -.6, 20), surface(-.5, .7, 23)].join(';');
  return <svg className="session-liquid-gauge" viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true">
    <defs>
      <linearGradient id={`${id}-spectrum`} x1="0" y1="0" x2="1000" y2="0" gradientUnits="userSpaceOnUse">
        {colors.map((color, index) => <stop key={color} offset={`${index / (colors.length - 1) * 100}%`} stopColor={color} />)}
      </linearGradient>
      <linearGradient id={`${id}-light`} x1="0" y1="0" x2="0" y2="1">
        <stop stopColor="#fff" stopOpacity=".18" />
        <stop offset=".6" stopColor="#fff" stopOpacity="0" />
        <stop offset="1" stopColor="#000" stopOpacity=".06" />
      </linearGradient>
    </defs>
    {fill > 0 && <>
      <path fill={`url(#${id}-spectrum)`} opacity=".35" d={surface(-1)}>
        {!reduced && <animate attributeName="d" dur="9.7s" repeatCount="indefinite" values={back} calcMode="spline" keyTimes="0;.14;.39;.58;.84;1" keySplines=".4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1" />}
      </path>
      <path fill={`url(#${id}-spectrum)`} d={surface(1)}>
        {!reduced && <animate attributeName="d" dur="7.3s" repeatCount="indefinite" values={front} calcMode="spline" keyTimes="0;.14;.39;.58;.84;1" keySplines=".4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1" />}
      </path>
      <path fill={`url(#${id}-light)`} d={surface(1)}>
        {!reduced && <animate attributeName="d" dur="7.3s" repeatCount="indefinite" values={front} calcMode="spline" keyTimes="0;.14;.39;.58;.84;1" keySplines=".4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1;.4 0 .6 1" />}
      </path>
    </>}
  </svg>;
}
