import { useEffect, useState } from 'react';
import { IconPlus } from './Icons';

export default function PosList({ items, placeholder, onChange }) {
  const [localItems, setLocalItems] = useState(items);

  useEffect(() => { setLocalItems(items); }, [JSON.stringify(items)]);

  const update = (newItems) => {
    setLocalItems(newItems);
    onChange(newItems.filter(i => i.trim()));
  };

  const addItem = () => setLocalItems([...localItems, '']);

  const removeItem = (idx) => update(localItems.filter((_, i) => i !== idx));

  const changeItem = (idx, val) => {
    const newItems = [...localItems];
    newItems[idx] = val;
    setLocalItems(newItems);
  };

  const commitItem = (idx, val) => {
    const newItems = [...localItems];
    newItems[idx] = val;
    update(newItems);
  };

  return (
    <div className="pos-list">
      {localItems.map((item, idx) => (
        <div key={idx} className="pos-list-item">
          <div className="pos-list-bullet" />
          <input
            className="pos-list-input"
            value={item}
            placeholder={placeholder}
            onChange={(e) => changeItem(idx, e.target.value)}
            onBlur={(e) => commitItem(idx, e.target.value)}
          />
          <button className="pos-list-remove" onClick={() => removeItem(idx)} title="Remove">{'\u2715'}</button>
        </div>
      ))}
      <button className="pos-list-add-btn" onClick={addItem}>
        <IconPlus />
        Add item
      </button>
    </div>
  );
}
