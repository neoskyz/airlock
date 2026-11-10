import { displayText } from './runUpdates';
export type IconName = 'shield' | 'feed' | 'approval' | 'policy' | 'audit' | 'docs' | 'arrow' | 'check' | 'search' | 'bag' | 'wallet' | 'stop' | 'external' | 'copy' | 'close';
export function Icon({
  name,
  size = 20
}: {
  name: IconName;
  size?: number;
}) {
  const paths: Record<IconName, React.ReactNode> = {
    shield: <>
    <path d="m12 3 8 3v6c0 4-8 9-8 9s-8-5-8-9V6l8-3Z" />
    <path d="m8 12 3 3 5-6" />
  </>,
    feed: <>
    <path d="M3 12h4l3-7 4 14 3-7h4" />
  </>,
    approval: <>
    <rect x="5" y="4" width="14" height="17" rx="2" />
    <path d="M9 4V2h6v2M9 13l2 2 4-4" />
  </>,
    policy: <>
    <path d="M4 7h16M4 17h16" />
    <circle cx="9" cy="7" r="3" />
    <circle cx="15" cy="17" r="3" />
  </>,
    audit: <>
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <path d="M4 10h16M10 10v10" />
  </>,
    docs: <>
    <path d="M4 4h6a2 2 0 0 1 2 2v15a4 4 0 0 0-4-2H4V4ZM20 4h-6a2 2 0 0 0-2 2v15a4 4 0 0 1 4-2h4V4Z" />
  </>,
    arrow: <>
    <path d="M5 12h14m-5-5 5 5-5 5" />
  </>,
    check: <path d="m5 12 4 4 10-10" />,
    search: <>
    <circle cx="10" cy="10" r="6" />
    <path d="m15 15 5 5" />
  </>,
    bag: <>
    <path d="M5 8h14l1 13H4L5 8Z" />
    <path d="M9 8V6a3 3 0 0 1 6 0v2" />
  </>,
    wallet: <>
    <rect x="3" y="5" width="18" height="15" rx="3" />
    <path d="M3 8h18m0 4h-6v5h6" />
    <path d="M17 14.5h.1" />
  </>,
    stop: <rect x="7" y="7" width="10" height="10" rx="1" />,
    external: <>
    <path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6" />
  </>,
    copy: <>
    <rect x="8" y="8" width="12" height="13" rx="2" />
    <path d="M15 8V3H3v12h5" />
  </>,
    close: <path d="m6 6 12 12M18 6 6 18" />
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {paths[name]}
  </svg>;
}
export function Connection({
  state
}: {
  state: string;
}) {
  return <span className={`connection ${state}`}>
    <i />
    {state === 'live' ? 'Live sync' : state === 'connecting' ? 'Connecting' : 'Reconnecting'}
  </span>;
}
export function Badge({
  children,
  tone = ''
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return <span className={`badge ${tone}`}>
    {children}
  </span>;
}
export function ErrorBanner({
  message,
  onClose
}: {
  message: string;
  onClose?: () => void;
}) {
  return message ? <div className="error-banner" role="alert">
    <span>
      {displayText(message)}
    </span>
    {onClose && <button aria-label="Dismiss error" onClick={onClose}>
      <Icon name="close" size={16} />
    </button>}
  </div> : null;
}
