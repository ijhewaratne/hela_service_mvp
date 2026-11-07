import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './auth/AuthContext';
import { Admins } from './pages/Admins';
import { Audit } from './pages/Audit';
import { BookingInspector } from './pages/BookingInspector';
import { CategoryPolicies } from './pages/CategoryPolicies';
import { Controls } from './pages/Controls';
import { Dashboard } from './pages/Dashboard';
import { IncidentCase, Incidents } from './pages/Incidents';
import { Login } from './pages/Login';
import { Privacy } from './pages/Privacy';
import { ProviderCase } from './pages/ProviderCase';
import { RequestInspector } from './pages/RequestInspector';
import { Reviews } from './pages/Reviews';
import { Settlements } from './pages/Settlements';
import { Verification } from './pages/Verification';

// Navigation is filtered by permission for convenience only; the API enforces every check (§3.3).
const MODULES = [
  { path: '/', label: 'Operations', perm: 'dispatch.manage' },
  { path: '/verification', label: 'Provider review', perm: 'verification.review' },
  { path: '/incidents', label: 'Incidents', perm: 'incident.respond' },
  { path: '/settlements', label: 'Settlements', perm: 'finance.reconcile' },
  { path: '/reviews', label: 'Reviews', perm: 'review.moderate' },
  { path: '/controls', label: 'Regions & categories', perm: 'policy.manage' },
  { path: '/admins', label: 'Administrators', perm: 'admin.manage' },
  { path: '/privacy', label: 'Privacy', perm: 'privacy.handle' },
  { path: '/audit', label: 'Audit', perm: 'audit.read' },
];

export function App() {
  const { me, can, signOut } = useAuth();
  if (!me) return <Login />;
  const visible = MODULES.filter((m) => can(m.perm));
  const home = visible[0]?.path ?? '/none';
  return (
    <div className="layout">
      <nav className="side" aria-label="Modules">
        <div className="brand">Hela Operations</div>
        <div className="env">SYNTHETIC · NOT FOR REAL DATA</div>
        <div className="muted small" style={{ marginBottom: 12 }}>Times shown in Asia/Colombo</div>
        {visible.map((m) => (
          <NavLink key={m.path} to={m.path} end={m.path === '/'}>{m.label}</NavLink>
        ))}
        <div className="who">
          Signed in as <strong>{me.username}</strong>
          <div className="row"><button className="btn btn-ghost" onClick={() => void signOut()}>Sign out</button></div>
        </div>
      </nav>
      <main>
        <Routes>
          {can('dispatch.manage') && <Route path="/" element={<Dashboard />} />}
          <Route path="/requests/:id" element={<RequestInspector />} />
          <Route path="/bookings/:id" element={<BookingInspector />} />
          {can('verification.review') && <Route path="/verification" element={<Verification />} />}
          {can('verification.review') && <Route path="/providers/:id" element={<ProviderCase />} />}
          {can('incident.respond') && <Route path="/incidents" element={<Incidents />} />}
          {can('incident.respond') && <Route path="/incidents/:id" element={<IncidentCase />} />}
          {can('finance.reconcile') && <Route path="/settlements" element={<Settlements />} />}
          {can('review.moderate') && <Route path="/reviews" element={<Reviews />} />}
          {can('policy.manage') && <Route path="/controls" element={<Controls />} />}
          {can('policy.manage') && <Route path="/controls/:code" element={<CategoryPolicies />} />}
          {can('admin.manage') && <Route path="/admins" element={<Admins />} />}
          {can('privacy.handle') && <Route path="/privacy" element={<Privacy />} />}
          {can('audit.read') && <Route path="/audit" element={<Audit />} />}
          <Route path="/none" element={<p>Your account has no modules assigned. Ask a super-administrator.</p>} />
          <Route path="*" element={<Navigate to={home} replace />} />
        </Routes>
      </main>
    </div>
  );
}
