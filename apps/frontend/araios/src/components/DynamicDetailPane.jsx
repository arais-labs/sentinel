/**
 * DynamicDetailPane renders the right-side detail pane for a module record
 * from the module's field schema.
 *
 * @param {object}   config        - Module config (fields, actions)
 * @param {object}   record        - Selected record data
 * @param {boolean}  saving        - Whether a save is in progress
 * @param {function} onPatch       - Called with patch dict to update record
 * @param {function} onDelete      - Called to delete record
 * @param {function} onAction      - Called with action id for custom actions
 * @param {function} onEdit        - Called to open the edit form
 */
export default function DynamicDetailPane({ config, record, saving, onPatch, onDelete, onAction, onEdit }) {
  if (!record) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[color:var(--text-muted)] gap-2 opacity-50">
        <span style={{ fontSize: '2rem' }}>↖</span>
        <p className="text-sm font-medium">Select a record to view details</p>
      </div>
    );
  }

  const fields = (config.fields || []).filter(f => f.type !== 'readonly' || record[f.key]);
  const detailActions = (config.actions || []).filter(a => a.placement === 'detail');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="detail-hero">
        <div className="flex-1">
          <h2 className="detail-name">{record[config.list_config?.titleField] || record.id}</h2>
          {config.list_config?.subtitleField && (
            <p className="text-sm text-[color:var(--text-secondary)]">
              {record[config.list_config.subtitleField]}
            </p>
          )}
        </div>
        {config.list_config?.badgeField && record[config.list_config.badgeField] && (
          <span className="badge badge-neutral">{record[config.list_config.badgeField]}</span>
        )}
      </div>

      <div className="detail-content">
        <div className="space-y-4">
          {fields.map(field => (
            <FieldView
              key={field.key}
              field={field}
              value={record[field.key]}
              onBlur={val => onPatch({ [field.key]: val })}
            />
          ))}

          {/* Action buttons */}
          <div className="flex items-center gap-3 pt-4 border-t border-[color:var(--border-subtle)] flex-wrap">
            {onEdit && (
              <button className="btn-secondary" onClick={onEdit} disabled={saving}>
                Edit
              </button>
            )}
            {detailActions.map(action => {
              if (action.type === 'delete') {
                return (
                  <button
                    key={action.id}
                    className="btn-danger"
                    onClick={onDelete}
                    disabled={saving}
                  >
                    {action.label}
                  </button>
                );
              }
              return (
                <button
                  key={action.id}
                  className="btn-secondary"
                  onClick={() => onAction(action.id)}
                  disabled={saving}
                >
                  {action.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldView({ field, value, onBlur }) {
  if (value == null || value === '') return null;

  if (field.type === 'textarea') {
    return (
      <div className="form-field">
        <label className="form-label">{field.label}</label>
        <textarea
          className="editor-textarea"
          defaultValue={value}
          onBlur={e => onBlur(e.target.value)}
          rows={3}
        />
      </div>
    );
  }

  if (field.type === 'select') {
    return (
      <div className="form-field">
        <label className="form-label">{field.label}</label>
        <select
          className="form-input"
          defaultValue={value}
          onBlur={e => onBlur(e.target.value)}
          onChange={e => onBlur(e.target.value)}
        >
          {(field.options || []).map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </div>
    );
  }

  if (field.type === 'readonly' || field.type === 'badge') {
    return (
      <div className="form-field">
        <label className="form-label">{field.label}</label>
        <span className="badge badge-neutral">{value}</span>
      </div>
    );
  }

  if (field.type === 'url') {
    return (
      <div className="form-field">
        <label className="form-label">{field.label}</label>
        <a
          href={value}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[color:var(--accent-solid)] text-sm hover:underline break-all"
        >
          {value}
        </a>
      </div>
    );
  }

  const typeMap = { email: 'email', url: 'url', number: 'number', date: 'date' };
  return (
    <div className="form-field">
      <label className="form-label">{field.label}</label>
      <input
        className="form-input"
        type={typeMap[field.type] || 'text'}
        defaultValue={value}
        onBlur={e => onBlur(e.target.value)}
      />
    </div>
  );
}
