import { useState } from 'react';
import Modal from './Modal';

/**
 * DynamicForm renders a create/edit form inside a Modal from a module's field schema.
 *
 * @param {string}   title         - Modal title
 * @param {Array}    fields        - Module field definitions
 * @param {object}   initial       - Initial form values (for edit mode)
 * @param {boolean}  saving        - Submit loading state
 * @param {function} onSubmit      - Called with form data object
 * @param {function} onClose       - Close modal handler
 */
export default function DynamicForm({ title, fields, initial = {}, saving, onSubmit, onClose }) {
  const formFields = fields.filter(f => f.type !== 'readonly');
  const emptyState = Object.fromEntries(formFields.map(f => [f.key, initial[f.key] ?? '']));
  const [form, setForm] = useState(emptyState);

  const set = (key, val) => setForm(prev => ({ ...prev, [key]: val }));

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit(form);
  };

  return (
    <Modal title={title} onClose={onClose}>
      <form className="modal-body" onSubmit={handleSubmit}>
        {formFields.map((field, i) => (
          <div key={field.key} className="form-field">
            <label className="form-label">
              {field.label}
              {field.required && <span className="text-rose-400 ml-1">*</span>}
            </label>
            <FieldInput field={field} value={form[field.key]} onChange={val => set(field.key, val)} autoFocus={i === 0} />
          </div>
        ))}
        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FieldInput({ field, value, onChange, autoFocus }) {
  const base = { className: 'form-input', value: value ?? '', autoFocus };

  if (field.type === 'textarea') {
    return (
      <textarea
        className="editor-textarea"
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        autoFocus={autoFocus}
        rows={3}
      />
    );
  }

  if (field.type === 'select') {
    return (
      <select {...base} onChange={e => onChange(e.target.value)}>
        <option value="">— select —</option>
        {(field.options || []).map(opt => (
          <option key={opt} value={opt}>{opt}</option>
        ))}
      </select>
    );
  }

  const typeMap = { email: 'email', url: 'url', number: 'number', date: 'date' };
  return (
    <input
      {...base}
      type={typeMap[field.type] || 'text'}
      required={field.required}
      onChange={e => onChange(e.target.value)}
    />
  );
}
