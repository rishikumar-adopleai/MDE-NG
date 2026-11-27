import { HeadingText } from '../HeadingText';
import { useState, type ComponentProps, type FormEvent } from 'react';
import { ArrowRight, Eye, EyeOff, Hospital } from 'lucide-react';
import { authenticateAccount } from '../data/repositories/workspaceApi';

function PasswordInput({ label, ...input }: ComponentProps<'input'> & { label: string }) {
  const [visible, setVisible] = useState(false);
  const action = `${visible ? 'Hide' : 'Show'} ${label}`;

  return <div className="account-password">
    <input {...input} type={visible ? 'text' : 'password'} />
    <button type="button" aria-label={action} title={action} aria-controls={input.id}
      onClick={() => setVisible(!visible)}>
      {visible ? <EyeOff size={19} aria-hidden="true" /> : <Eye size={19} aria-hidden="true" />}
    </button>
  </div>;
}

export function AccountAccess() {
  const [mode, setMode] = useState<'login' | 'signup'>('login');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const signup = mode === 'signup';

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const email = String(fields.get('email') ?? '');
    const password = String(fields.get('password') ?? '');
    if (signup && password !== fields.get('confirm')) {
      setError('The passwords do not match.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await authenticateAccount(mode, email, password);
      window.location.reload();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'The account service is unavailable.');
      setBusy(false);
    }
  }

  return <main className="account-page">
    <section className="account-card" aria-labelledby="account-title">
      <header className="account-heading">
        <div className="account-brand"><span><Hospital size={21} aria-hidden="true" /></span>MDE-NG</div>
        <h1 id="account-title"><HeadingText>{signup ? 'Create your account' : 'Sign in'}</HeadingText></h1>
        <p>{signup ? 'Create an account to work in Intake.' : 'Access the examination workspace with your account.'}</p>
      </header>
      <form className="account-form" method="post" onSubmit={submit} aria-busy={busy}>
        <label htmlFor="account-email">Email address</label>
        <input id="account-email" name="email" type="email" autoComplete="username" autoCapitalize="none" maxLength={254} required />
        <label htmlFor="account-password">Password</label>
        <PasswordInput key={mode} label="password" id="account-password" name="password" autoComplete={signup ? 'new-password' : 'current-password'}
          minLength={signup ? 12 : undefined} maxLength={128} aria-describedby={signup ? 'account-password-hint' : undefined} required />
        {signup && <>
          <small id="account-password-hint">Use at least 12 characters.</small>
          <label htmlFor="account-confirm">Confirm password</label>
          <PasswordInput label="confirmation password" id="account-confirm" name="confirm" autoComplete="new-password" minLength={12} maxLength={128} required />
        </>}
        {error && <p className="account-error" role="alert">{error}</p>}
        <button type="submit" className="account-submit" disabled={busy}>
          {busy ? (signup ? 'Creating account…' : 'Signing in…') : (signup ? 'Create account' : 'Sign in')}
          <ArrowRight size={17} aria-hidden="true" />
        </button>
      </form>
      <footer className="account-footer">
        <span>{signup ? 'Already have an account?' : 'New to the workspace?'}</span>
        <button type="button" disabled={busy} onClick={() => { setError(''); setMode(signup ? 'login' : 'signup'); }}>
          {signup ? 'Sign in' : 'Create an account'}
        </button>
      </footer>
    </section>
  </main>;
}
