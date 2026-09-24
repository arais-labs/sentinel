import { useState } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import { api } from '../../lib/api';
import satin from '../../assets/wallpapers/satin.jpg';
import horizon from '../../assets/wallpapers/horizon.jpg';
import geometry from '../../assets/wallpapers/geometry.jpg';
import spectrum from '../../assets/wallpapers/spectrum.jpg';
import paper from '../../assets/wallpapers/paper.jpg';
import monochrome from '../../assets/wallpapers/monochrome.jpg';

const designs = [
  { id: 'satin', name: 'Dark satin', image: satin },
  { id: 'horizon', name: 'Horizon', image: horizon },
  { id: 'geometry', name: 'Geometry', image: geometry },
  { id: 'spectrum', name: 'Spectrum', image: spectrum },
  { id: 'paper', name: 'Paper', image: paper },
  { id: 'monochrome', name: 'Monochrome', image: monochrome },
];

export function WallpaperChooser({ endpoint, onClose }: { endpoint: string; onClose: () => void }) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  async function apply(design: string) {
    if (busy || design === selected) return;
    setBusy(design);
    setError('');
    try {
      await api.post(endpoint, { design });
      setSelected(design);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not apply wallpaper');
    } finally { setBusy(''); }
  }
  return <section className="desktop-wallpapers" aria-label="Desktop wallpaper">
    <div className="desktop-wallpapers-heading">
      <span>Wallpaper<small>Choose a background for this workspace</small></span>
      <button onClick={onClose} aria-label="Close wallpaper chooser"><X size={16} /></button>
    </div>
    <div className="desktop-wallpapers-grid">
      {designs.map(design => <button type="button" key={design.id} disabled={!!busy}
        data-applying={busy === design.id || undefined} aria-busy={busy === design.id}
        aria-pressed={selected === design.id} onClick={() => void apply(design.id)}>
        <span className="desktop-wallpaper-preview"><img src={design.image} alt="" />
          {(busy === design.id || selected === design.id) && <span className="desktop-wallpaper-badge">{busy === design.id ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}</span>}
        </span>
        <span className="desktop-wallpaper-label">{design.name}<small>{busy === design.id ? 'Applying…' : selected === design.id ? 'Selected' : '\u00a0'}</small></span>
      </button>)}
    </div>
    {error && <p role="alert">{error}</p>}
  </section>;
}
