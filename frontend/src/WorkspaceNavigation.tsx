import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { AlertCircle, Hospital, LogOut, Menu, ShieldCheck, UserRound, X } from 'lucide-react';
import { logout } from './data/repositories/workspaceApi';
import { navItems, type StageKey } from './stages/model';
import { stageHref } from './workspaceRoutes';
import { HeadingText } from './HeadingText';

const mobileViewport = '(max-width: 900px)';

type NavigationProps = {
  stage: StageKey;
  onNavigate: (stage: StageKey) => void;
};

function StageLinks({ stage, onNavigate }: NavigationProps) {
  function follow(event: MouseEvent<HTMLAnchorElement>, target: StageKey) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onNavigate(target);
  }

  return <nav className="workflow-nav" aria-label="Examination workflow">
    {navItems.map(({ name, icon: Icon }) => <a key={name}
      href={stageHref(name, window.location.search)} aria-current={stage === name ? 'page' : undefined}
      aria-label={name} onClick={event => follow(event, name)}>
      <Icon size={19} aria-hidden="true" />
      <span>{name}</span>
    </a>)}
  </nav>;
}

function WorkspaceBrand() {
  return <div className="workflow-brand"><span className="workflow-brand-mark"><Hospital size={21} aria-hidden="true" /></span><strong>MDE-NG</strong></div>;
}

export function WorkspaceNavigation({ stage, onNavigate, open, onClose }: NavigationProps & { open: boolean; onClose: () => void }) {
  const drawer = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = drawer.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const media = window.matchMedia(mobileViewport);
    const closeOnDesktop = (event: MediaQueryListEvent) => { if (!event.matches) onClose(); };
    media.addEventListener('change', closeOnDesktop);
    return () => media.removeEventListener('change', closeOnDesktop);
  }, [open, onClose]);

  return <>
    <aside className="workflow-sidebar" aria-label="MDE-NG">
      <WorkspaceBrand />
      <StageLinks stage={stage} onNavigate={onNavigate} />
      <span className="workflow-rail-footer" aria-hidden="true"><ShieldCheck size={18} /></span>
    </aside>
    <dialog ref={drawer} className="workflow-drawer" id="workflow-drawer" aria-label="Workflow navigation"
      onClose={onClose} onPointerDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="workflow-drawer-content">
        <div className="workflow-drawer-top">
          <WorkspaceBrand />
          <button type="button" className="workflow-icon-button" aria-label="Close navigation" onClick={onClose} autoFocus><X size={20} /></button>
        </div>
        <StageLinks stage={stage} onNavigate={onNavigate} />
      </div>
    </dialog>
  </>;
}

function WorkspaceAccount() {
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState(false);
  const account = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent) {
      if (event.target instanceof Node && !account.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [open]);

  async function signOut() {
    if (signingOut) return;
    setSigningOut(true);
    setSignOutError(false);
    try {
      await logout();
      window.location.reload();
    } catch {
      setSignOutError(true);
      setSigningOut(false);
    }
  }

  return <div ref={account} className="workflow-account" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={event => {
    if (event.key === 'Escape' && open) {
      setOpen(false);
      trigger.current?.focus();
    }
  }}>
    <button ref={trigger} type="button" className="workflow-icon-button workflow-account-trigger"
      aria-label="User account" title="User account" aria-expanded={open} aria-controls={panelId}
      onClick={() => setOpen(current => !current)}>
      <UserRound size={19} aria-hidden="true" />
    </button>
    <div id={panelId} className="workflow-account-panel" hidden={!open}>
      <button type="button" className="workflow-sign-out" onClick={signOut} aria-disabled={signingOut}>
        <LogOut size={16} aria-hidden="true" />{signingOut ? 'Signing out…' : 'Sign out'}
      </button>
      {signOutError && <p className="account-error" role="alert">Sign-out failed. Try again.</p>}
    </div>
  </div>;
}

export function WorkspaceTopbar({ openNavigation, navigationOpen, canSignOut, exceptions, exceptionsSelected, onExceptions, children }: {
  openNavigation: () => void; navigationOpen: boolean; canSignOut: boolean; exceptions: number;
  exceptionsSelected: boolean; onExceptions: () => void; children?: ReactNode;
}) {
  return <header className="workflow-topbar">
    <button type="button" className="workflow-icon-button workflow-mobile-menu" aria-label="Open navigation"
      aria-haspopup="dialog" aria-controls="workflow-drawer" aria-expanded={navigationOpen} onClick={openNavigation}><Menu size={20} /></button>
    <span className="workflow-topbar-title"><HeadingText>Examination workspace</HeadingText></span>
    {children}
    <div className="workflow-tools">
      <button type="button" className="workflow-exceptions" aria-label={`Exceptions (${exceptions})`}
        title="Filter the current queue by exceptions" aria-pressed={exceptionsSelected} onClick={onExceptions}>
        <AlertCircle size={17} aria-hidden="true" /><span>Exceptions</span><b>{exceptions}</b>
      </button>
      {canSignOut && <WorkspaceAccount />}
    </div>
  </header>;
}
