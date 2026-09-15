import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, Copy, X } from 'lucide-react';
import type { MessageAttachment } from '../../types/api';
import { copyImage, imageSource } from '../../lib/message-images';
import './message-images.css';

export function ImageCopyButton({ image }: { image: MessageAttachment }) {
  const [status, setStatus] = useState('Copy image');
  useEffect(() => { setStatus('Copy image'); }, [image.base64, image.mime_type]);
  return <button type="button" onClick={() => {
    void copyImage(image).then(() => setStatus('Image copied')).catch(() => setStatus('Copy unavailable'));
  }}><Copy size={13} /><span aria-live="polite">{status}</span></button>;
}

function ImageViewer({ images, initialIndex, onClose, onSelect }: {
  images: MessageAttachment[]; initialIndex: number; onClose: () => void; onSelect?: (index: number) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(initialIndex);
  const [actualSize, setActualSize] = useState(false);
  const [dimensions, setDimensions] = useState('');
  const image = images[index];
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  function select(next: number) {
    if (next < 0 || next >= images.length) return;
    setIndex(next); onSelect?.(next); setDimensions('');
    viewport.current?.scrollTo(0, 0);
  }
  return createPortal(<dialog ref={dialog} className="message-image-viewer" aria-label="Image viewer"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}
    onKeyDown={event => {
      event.stopPropagation();
      if (event.key === 'ArrowLeft') { event.preventDefault(); select(index - 1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); select(index + 1); }
    }}>
    <header>
      <span className="message-image-title">{image.filename || `Image ${index + 1}`}<small>{dimensions}</small></span>
      {images.length > 1 && <div className="message-image-navigation">
        <button type="button" aria-label="Previous image" disabled={index === 0} onClick={() => select(index - 1)}><ChevronLeft size={16} /></button>
        <span>{index + 1} / {images.length}</span>
        <button type="button" aria-label="Next image" disabled={index === images.length - 1} onClick={() => select(index + 1)}><ChevronRight size={16} /></button>
      </div>}
      <button type="button" aria-pressed={actualSize} onClick={() => setActualSize(value => !value)}>{actualSize ? 'Fit to window' : 'Actual size'}</button>
      <ImageCopyButton image={image} />
      <button type="button" aria-label="Close image viewer" onClick={onClose} autoFocus><X size={18} /></button>
    </header>
    <div ref={viewport} className="message-image-viewport" data-actual-size={actualSize}>
      <img key={index} src={imageSource(image)} alt={image.filename || `Image ${index + 1}`} onLoad={event => setDimensions(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`)} />
    </div>
  </dialog>, document.body);
}

export function MessageImages({ images, selectedIndex = 0, onSelect }: {
  images: MessageAttachment[]; selectedIndex?: number; onSelect?: (index: number) => void;
}) {
  const [viewing, setViewing] = useState<number | null>(null);
  if (!images.length) return null;
  return <>
    <div className="message-images" data-multiple={images.length > 1}>
      {images.map((image, index) => <figure key={index} data-selected={images.length > 1 && selectedIndex === index}>
        <button type="button" className="message-image-thumbnail" aria-label={`Open ${image.filename || `image ${index + 1}`} full size`}
          onClick={() => { onSelect?.(index); setViewing(index); }}>
          <img src={imageSource(image)} alt={image.filename || `Image ${index + 1}`} loading="lazy" />
        </button>
        <figcaption><span>{image.filename || `Image ${index + 1}`}</span><ImageCopyButton image={image} /></figcaption>
      </figure>)}
    </div>
    {viewing !== null && <ImageViewer images={images} initialIndex={viewing} onClose={() => setViewing(null)} onSelect={onSelect} />}
  </>;
}
