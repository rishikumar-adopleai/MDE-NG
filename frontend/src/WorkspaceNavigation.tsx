import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { AlertCircle, Hospital, LayoutDashboard, LogOut, Menu, ShieldCheck, UserRound, X } from 'lucide-react';
import { logout } from './data/repositories/workspaceApi';
import { navItems, type StageKey } from './stages/model';
import { workspaceHref, type WorkspacePage } from './workspaceRoutes';

const mobileViewport = '(max-width: 900px)';

type NavigationProps = {
  page: WorkspacePage;
  onNavigate: (page: WorkspacePage) => void;
};

function StageLinks({ page, onNavigate }: NavigationProps) {
  function follow(event: MouseEvent<HTMLAnchorElement>, target: WorkspacePage) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onNavigate(target);
  }

  return <nav className="workflow-nav" aria-label="Examination workspace">
    <a href={workspaceHref('Dashboard')} aria-current={page === 'Dashboard' ? 'page' : undefined}
      aria-label="Dashboard" onClick={event => follow(event, 'Dashboard')}>
      <LayoutDashboard size={19} aria-hidden="true" /><span>Dashboard</span>
    </a>
    {navItems.map(({ name, icon: Icon }) => <a key={name}
      href={workspaceHref(name)} aria-current={page === name ? 'page' : undefined}
      aria-label={name} onClick={event => follow(event, name)}>
      <Icon size={19} aria-hidden="true" />
      <span>{name}</span>
    </a>)}
  </nav>;
}

function WorkspaceBrand() {
  return <div className="workflow-brand"><span className="workflow-brand-mark"><Hospital size={21} aria-hidden="true" /></span><strong>MDE-NG</strong></div>;
}

export function WorkspaceNavigation({ page, onNavigate, open, onClose }: NavigationProps & { open: boolean; onClose: () => void }) {
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
      <StageLinks page={page} onNavigate={onNavigate} />
      <span className="workflow-rail-footer" aria-hidden="true"><ShieldCheck size={18} /></span>
    </aside>
    <dialog ref={drawer} className="workflow-drawer" id="workflow-drawer" aria-label="Workflow navigation"
      onClose={onClose} onPointerDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="workflow-drawer-content">
        <div className="workflow-drawer-top">
          <WorkspaceBrand />
          <button type="button" className="workflow-icon-button" aria-label="Close navigation" onClick={onClose} autoFocus><X size={20} /></button>
        </div>
        <StageLinks page={page} onNavigate={onNavigate} />
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

export function WorkspaceTopbar({ openNavigation, navigationOpen, canSignOut, exceptions, exceptionsSelected, onExceptions, showExceptions = true, children }: {
  openNavigation: () => void; navigationOpen: boolean; canSignOut: boolean; exceptions: number;
  exceptionsSelected: boolean; onExceptions: () => void; showExceptions?: boolean; children?: ReactNode;
}) {
  return <header className="workflow-topbar">
    <button type="button" className="workflow-icon-button workflow-mobile-menu" aria-label="Open navigation"
      aria-haspopup="dialog" aria-controls="workflow-drawer" aria-expanded={navigationOpen} onClick={openNavigation}><Menu size={20} /></button>
    <span className="workflow-topbar-title">MDE-NG</span>
    {children}
    <div className="workflow-tools">
      {showExceptions && <button type="button" className="workflow-exceptions" aria-label={`Exceptions (${exceptions})`}
        title="Filter the current queue by exceptions" aria-pressed={exceptionsSelected} onClick={onExceptions}>
        <AlertCircle size={17} aria-hidden="true" /><span>Exceptions</span><b>{exceptions}</b>
      </button>}
      {canSignOut && <WorkspaceAccount />}
    </div>
  </header>;
}
