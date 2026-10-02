import { Server } from 'lucide-react';
import claudeLogo from '../../assets/provider-logos/claude.svg?raw';
import openaiLogo from '../../assets/provider-logos/openai.svg?raw';
import geminiLogo from '../../assets/provider-logos/gemini.svg?raw';
import './provider-logo.css';

/** The same vector provider marks in chat, Voice, and settings. */
export function ProviderLogo({ id, size = 15 }: { id?: string; size?: number }) {
  const source = id === 'anthropic' ? claudeLogo : id === 'gemini' ? geminiLogo : openaiLogo;
  return id === 'ollama'
    ? <span aria-hidden="true" className="provider-logo" style={{ width: size, height: size }}><Server size={size} /></span>
    : <span aria-hidden="true" className="provider-logo" style={{ width: size, height: size }} dangerouslySetInnerHTML={{ __html: source }} />;
}
