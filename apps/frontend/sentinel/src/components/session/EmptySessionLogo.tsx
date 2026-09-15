import { useThemeStore } from '../../store/theme-store';
import { Logo } from '../ui/Logo';
import { SolidInstanceIcon } from '../ui/SolidInstanceIcon';

export function EmptySessionLogo() {
  const theme = useThemeStore(state => state.theme);
  return (
    <div className="session-empty-logo mb-4 text-(--text-primary)" aria-hidden="true">
      <SolidInstanceIcon color={theme === 'dark' ? '#eeeeee' : '#202020'} bevel={.045}>
        <Logo size={92} />
      </SolidInstanceIcon>
    </div>
  );
}
