import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import { HomeRedirect, RequireAuth, RequireRole } from './auth/guards';
import { ToastProvider } from './components/Toast';
import { Shell } from './layout/Shell';
import { LoginPage } from './pages/LoginPage';
import { CasesPage } from './pages/cases/CasesPage';
import { CaseDetailPage } from './pages/cases/CaseDetailPage';
import { RunPage } from './pages/runs/RunPage';
import { CamPage } from './pages/cams/CamPage';
import { MastersPage } from './pages/masters/MastersPage';
import { PreferencesPage } from './pages/preferences/PreferencesPage';
import { AuditPage } from './pages/audit/AuditPage';
import { UsersPage } from './pages/admin/UsersPage';

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <Shell />
                </RequireAuth>
              }
            >
              <Route path="/" element={<HomeRedirect />} />
              <Route
                path="/cases"
                element={
                  <RequireRole roles={['analyst', 'reviewer']}>
                    <CasesPage />
                  </RequireRole>
                }
              />
              <Route
                path="/cases/:caseId"
                element={
                  <RequireRole roles={['analyst', 'reviewer']}>
                    <CaseDetailPage />
                  </RequireRole>
                }
              />
              <Route
                path="/runs/:runId"
                element={
                  <RequireRole roles={['analyst', 'reviewer']}>
                    <RunPage />
                  </RequireRole>
                }
              />
              <Route
                path="/cams/:camId"
                element={
                  <RequireRole roles={['analyst', 'reviewer']}>
                    <CamPage />
                  </RequireRole>
                }
              />
              <Route
                path="/admin/masters"
                element={<Navigate to="/admin/masters/prompts" replace />}
              />
              <Route
                path="/admin/masters/:tab"
                element={
                  <RequireRole roles={['business_admin']}>
                    <MastersPage />
                  </RequireRole>
                }
              />
              <Route
                path="/admin/users"
                element={
                  <RequireRole roles={['it_admin']}>
                    <UsersPage />
                  </RequireRole>
                }
              />
              <Route
                path="/audit"
                element={
                  <RequireRole roles={['auditor', 'business_admin']}>
                    <AuditPage />
                  </RequireRole>
                }
              />
              <Route path="/preferences" element={<PreferencesPage />} />
              <Route path="*" element={<HomeRedirect />} />
            </Route>
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
