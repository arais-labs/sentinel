import React from 'react';
import {
  Copy,
  GitBranch,
  Search,
  FileText,
  CheckCircle,
  RefreshCw,
  Settings,
  MessageCircle,
  Plus,
} from 'lucide-react';

// Re-export lucide icons with Icon* naming convention
export const IconCopy = (props) => <Copy size={16} {...props} />;
export const IconGitBranch = (props) => <GitBranch size={16} {...props} />;
export const IconSearch = (props) => <Search size={16} {...props} />;
export const IconDocument = (props) => <FileText size={16} {...props} />;
export const IconCheckCircle = (props) => <CheckCircle size={16} {...props} />;
export const IconRefresh = (props) => <RefreshCw size={16} {...props} />;
export const IconSettings = (props) => <Settings size={16} {...props} />;
export const IconMessageCircle = (props) => <MessageCircle size={16} {...props} />;
export const IconPlus = (props) => <Plus size={16} {...props} />;

export function Logo({ size = 24, className = "", fill = "none" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={fill}
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Widened coordinates (3 and 21) for a regular aspect ratio */}
      <path
        d="M12 2L21 7V17L12 22L3 17V7L12 2Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3" fill="currentColor" />
      {/* Rays adjusted to new corners */}
      <path
        d="M12 2V6 M12 18V22 M3 17L7 15 M17 9L21 7 M3 7L7 9 M17 15L21 17"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
