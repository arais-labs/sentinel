/**
 * Reusable list-pane card component used across all triage-layout pages.
 *
 * @param {boolean}     active        - Whether this card is currently selected
 * @param {function}    onClick       - Click handler
 * @param {object}      avatarStyle   - Inline styles for the avatar (e.g. background color)
 * @param {ReactNode}   avatarContent - Content inside the avatar (initials, icon, emoji)
 * @param {string}      title         - Primary title text
 * @param {string}      subtitle      - Secondary line below title
 * @param {string|ReactNode} meta     - Top-right metadata (date, version, etc.)
 * @param {ReactNode}   badge         - Bottom-right badge element
 */
export default function ListCard({ active, onClick, avatarStyle, avatarContent, title, subtitle, meta, badge }) {
  return (
    <article
      className={`lead-row ${active ? 'active' : ''}`}
      onClick={onClick}
    >
      <div className="row-main">
        <div className="row-avatar" style={avatarStyle}>
          {avatarContent}
        </div>
        <div className="row-body">
          <div className="row-top">
            <h3 className="row-name">{title}</h3>
            {meta != null && <span className="row-date">{meta}</span>}
          </div>
          <div className="row-foot">
            <span className="row-sub">{subtitle}</span>
            {badge}
          </div>
        </div>
      </div>
    </article>
  );
}
