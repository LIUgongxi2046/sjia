import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router';
import * as api from '../api/endpoints';
import { ApiError, setSessionCsrfToken, subscribeSessionUnauthorized } from '../api/client';
import { assertSessionEpoch, isSessionEpochCurrent, runSessionMutation, runSessionRead } from '../api/sessionBoundary';
import type { SessionEpoch, SessionMutationResult } from '../api/sessionBoundary';
import { clearNavigationMemory } from './navigation';

export interface AccessContextValue {
  principalId: string | null;
  accountStatus: 'active' | 'anonymous';
  csrfToken: string | null;
  patientId: string | null;
  relations: api.RelationItem[];
  error: string | null;
  errorSource: 'session' | 'relations' | 'logout' | null;
  selectPatient: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
  capabilities: string[];
  loading: boolean;
  scopeVersion: number;
  hasCurrentAccess: (principal?: string | null, patient?: string | null, scope?: number) => boolean;
  /** False means access recovery is needed or a newer operation superseded this one. */
  login: (username: string, password: string, preferredPatient?: string) => Promise<boolean>;
  register: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

const defaultContext: AccessContextValue = {
  principalId: null, accountStatus: 'anonymous', csrfToken: null, patientId: null,
  relations: [], error: null, errorSource: null, capabilities: [], loading: true, scopeVersion: 0, hasCurrentAccess: () => false,
  selectPatient: async () => { throw new Error('AccessProvider required'); },
  refresh: async () => { throw new Error('AccessProvider required'); },
  login: async () => { throw new Error('AccessProvider required'); },
  register: async () => { throw new Error('AccessProvider required'); },
  logout: async () => { throw new Error('AccessProvider required'); },
};
const AccessContext = createContext<AccessContextValue>(defaultContext);

function canSelectPatient(relation: api.RelationItem) {
  return relation.status === 'active' && relation.actions.includes('read_raw')
    && Date.parse(relation.valid_from) <= Date.now()
    && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now())
    && (!relation.authorization_scope_valid_until || Date.parse(relation.authorization_scope_valid_until) > Date.now());
}
const failureMessage = (failure: unknown) => failure instanceof Error ? failure.message : '暂时无法完成访问核验。';

export function AccessProvider({ children }: { children: ReactNode }) {
  const location = useLocation(), navigate = useNavigate();
  const previousUrlScope = useRef<{ principal: string | null; patient: string | null } | null>(null);
  const [principalId, setPrincipalId] = useState<string | null>(null);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [scopeVersion, setScopeVersion] = useState(0);
  const [patientAccessSuspended, setPatientAccessSuspended] = useState(false);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);
  const [relations, setRelations] = useState<api.RelationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [errorSource, setErrorSource] = useState<AccessContextValue['errorSource']>(null);
  const generation = useRef(0);
  const identity = useRef<{ principal: string | null; patient: string | null }>({ principal: null, patient: null });
  const mutation = useRef<'authenticate' | 'logout' | null>(null);
  const authentication = useRef<Promise<SessionMutationResult<api.SessionView | null>> | null>(null);
  const logoutRequired = useRef(false);
  const discardScopedUrl = useRef(false);
  const expiresAt = useRef(0);
  const expiryTimer = useRef<number | null>(null);
  const authorizedGeneration = useRef<number | null>(null);
  const verifiedRelation = useRef<api.RelationItem | null>(null);
  const verifiedSession = useRef('');
  const verifiedEpoch = useRef<SessionEpoch>(null);
  const scope = useRef<{ version: number; fingerprint: string | null }>({ version: 0, fingerprint: null });
  const changeScope = useCallback((fingerprint: string | null) => {
    if (scope.current.fingerprint === fingerprint) return;
    scope.current = { version: scope.current.version + 1, fingerprint };
    setScopeVersion(scope.current.version);
    clearNavigationMemory(); api.clearPendingRecordRequests();
  }, []);
  const hasCurrentAccess = useCallback((principal?: string | null, patient?: string | null, expectedScope?: number) => isSessionEpochCurrent(verifiedEpoch.current)
    && authorizedGeneration.current === generation.current
    && (expectedScope === undefined || expectedScope === scope.current.version)
    && (principal === undefined || principal === identity.current.principal) && (patient === undefined || patient === identity.current.patient)
    && expiresAt.current > Date.now() && Boolean(verifiedRelation.current && canSelectPatient(verifiedRelation.current)), []);

  const adoptToken = useCallback((value: string | null) => {
    setSessionCsrfToken(value);
    setCsrfToken(value);
  }, []);

  const clearIdentity = useCallback(() => {
    if (expiryTimer.current !== null) window.clearTimeout(expiryTimer.current);
    expiryTimer.current = null; expiresAt.current = 0; authorizedGeneration.current = null; verifiedRelation.current = null;
    changeScope(null);
    identity.current = { principal: null, patient: null };
    setPrincipalId(null); setPatientId(null); setPatientAccessSuspended(false); setRelations([]); setCapabilities([]);
    adoptToken(null);
    clearNavigationMemory();
    api.clearPendingRecordRequests();
  }, [adoptToken, changeScope]);

  const suspendPatientAccess = useCallback(() => {
    // A failed check discards clinical component state. Recovery uses the
    // existing actor/patient-bound URL marker or the server's pending requests.
    authorizedGeneration.current = null; verifiedRelation.current = null;
    changeScope(null);
    if (identity.current.patient) setPatientAccessSuspended(true);
    identity.current.patient = null;
    setPatientId(null); setRelations([]); setCapabilities([]);
    clearNavigationMemory();
    api.clearPendingRecordRequests();
  }, [changeScope]);

  useEffect(() => {
    const selected = relations.find(relation => relation.patient_id === patientId);
    if (!selected) return;
    const deadlines = [selected.valid_until, selected.authorization_scope_valid_until]
      .filter((value): value is string => Boolean(value)).map(Date.parse);
    if (!deadlines.length) return;
    const deadline = Math.min(...deadlines);
    let timer: number;
    const expire = () => {
      if (identity.current.patient !== selected.patient_id) return;
      const remaining = deadline - Date.now();
      if (remaining > 0) { timer = window.setTimeout(expire, Math.min(remaining, 2147483647)); return; }
      generation.current += 1;
      discardScopedUrl.current = true;
      suspendPatientAccess(); setError('当前访问权限已到重新核验时间，请重新读取有效关联。'); setErrorSource('relations'); setLoading(false);
    };
    timer = window.setTimeout(expire, Math.max(0, Math.min(deadline - Date.now(), 2147483647)));
    return () => window.clearTimeout(timer);
  }, [patientId, relations, suspendPatientAccess]);

  const adoptPatient = useCallback((value: string | null, selected: api.RelationItem | null, sessionCapabilities: string[]) => {
    // Same patient does not imply the same authorization. A changed grant must
    // remount clinical state and recheck any original submission on the server.
    const authority = selected?.authorization_scope_fingerprint;
    const verifiedAuthority = typeof authority === 'string' && /^[a-f0-9]{64}$/.test(authority)
      ? authority : `unknown:${generation.current}`;
    const temporalScope = selected && Object.prototype.hasOwnProperty.call(selected, 'authorization_scope_valid_until')
      ? selected.authorization_scope_valid_until : `unknown:${generation.current}`;
    changeScope(selected ? JSON.stringify([verifiedAuthority, verifiedSession.current, selected.relation_id, selected.patient_id, selected.version, selected.status,
      selected.valid_from, selected.valid_until, temporalScope, [...selected.actions].sort(), [...sessionCapabilities].sort()]) : null);
    if (identity.current.patient !== value) {
      clearNavigationMemory();
      api.clearPendingRecordRequests();
    }
    identity.current.patient = value;
    discardScopedUrl.current = false;
    setPatientAccessSuspended(false);
    setPatientId(value);
  }, [changeScope]);

  const applySession = useCallback(async (session: api.SessionView, requestGeneration: number, epoch: SessionEpoch, requiredPatient?: string): Promise<boolean> => {
    if (requestGeneration !== generation.current) return false;
    assertSessionEpoch(epoch);
    if (Date.parse(session.expires_at) <= Date.now()) { clearIdentity(); setError(null); setErrorSource(null); return false; }
    if (identity.current.principal !== session.principal_id) clearIdentity();
    identity.current.principal = session.principal_id;
    setPrincipalId(session.principal_id);
    adoptToken(session.csrf_token);
    if (expiryTimer.current !== null) window.clearTimeout(expiryTimer.current);
    expiresAt.current = Date.parse(session.expires_at);
    const binding = typeof session.session_binding === 'string' && /^[a-f0-9]{64}$/.test(session.session_binding)
      ? session.session_binding : `unknown:${requestGeneration}`;
    verifiedSession.current = JSON.stringify([session.principal_id, session.audience, binding]);
    const expire = () => {
      const remaining = expiresAt.current - Date.now();
      if (remaining > 0) { expiryTimer.current = window.setTimeout(expire, Math.min(remaining, 2147483647)); return; }
      generation.current += 1; mutation.current = null;
      clearIdentity(); setError(null); setErrorSource(null); setLoading(false); setInitialized(true);
    };
    expiryTimer.current = window.setTimeout(expire, Math.min(expiresAt.current - Date.now(), 2147483647));
    // Authentication is already proven. A failed authorization read must not
    // turn successful registration into another request to create an account.
    try {
      const result = await api.listRelations();
      if (requestGeneration !== generation.current) return false;
      assertSessionEpoch(epoch);
      if (expiresAt.current <= Date.now()) { clearIdentity(); setError(null); setErrorSource(null); return false; }
      const selected = result.relations.find(relation => relation.patient_id === session.selected_patient_id && canSelectPatient(relation));
      if (requiredPatient && (!selected || selected.patient_id !== requiredPatient)) {
        discardScopedUrl.current = true;
        suspendPatientAccess(); setError('当前患儿未取得有效访问权限，请重新核验。'); setErrorSource('relations');
        return false;
      }
      const first = result.relations.find(canSelectPatient);
      verifiedRelation.current = selected ?? first ?? null;
      verifiedEpoch.current = epoch;
      authorizedGeneration.current = requestGeneration;
      setRelations(result.relations);
      setCapabilities(session.capabilities);
      adoptPatient(selected?.patient_id ?? first?.patient_id ?? null, selected ?? first ?? null, session.capabilities);
      setError(null); setErrorSource(null);
      return true;
    } catch (failure) {
      if (requestGeneration !== generation.current) return false;
      if (failure instanceof ApiError && failure.status === 401) {
        clearIdentity();
        setError(null); setErrorSource(null);
      } else {
        suspendPatientAccess();
        setError(failureMessage(failure)); setErrorSource('relations');
      }
      return false;
    }
  }, [adoptPatient, adoptToken, clearIdentity, suspendPatientAccess]);

  useEffect(() => subscribeSessionUnauthorized(() => {
    if (mutation.current || logoutRequired.current) return;
    generation.current += 1;
    clearIdentity(); setError(null); setErrorSource(null); setLoading(false); setInitialized(true);
  }), [clearIdentity]);

  const refresh = useCallback(async () => {
    // A background pageshow/visibility event must not supersede sign-in/out.
    if (mutation.current || logoutRequired.current) return;
    const requestGeneration = ++generation.current;
    setLoading(true);
    try {
      const { value: session, epoch } = await runSessionRead(async () => {
        const bootstrap = await api.bootstrap();
        if (requestGeneration !== generation.current) return null;
        adoptToken(bootstrap.csrf_token);
        return api.readSession();
      });
      if (requestGeneration !== generation.current) return;
      if (!session) return;
      assertSessionEpoch(epoch);
      await applySession(session, requestGeneration, epoch);
    } catch (failure) {
      if (requestGeneration !== generation.current) return;
      if (failure instanceof ApiError && failure.status === 401) {
        clearIdentity(); setError(null); setErrorSource(null);
      } else {
        suspendPatientAccess(); setError(failureMessage(failure)); setErrorSource('session');
      }
    } finally {
      if (requestGeneration === generation.current) { setLoading(false); setInitialized(true); }
    }
  }, [adoptToken, applySession, clearIdentity, suspendPatientAccess]);

  useEffect(() => {
    void refresh();
    const restore = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('pageshow', restore);
    document.addEventListener('visibilitychange', restore);
    return () => {
      generation.current += 1;
      if (expiryTimer.current !== null) window.clearTimeout(expiryTimer.current);
      setSessionCsrfToken(null);
      window.removeEventListener('pageshow', restore);
      document.removeEventListener('visibilitychange', restore);
    };
  }, [refresh]);

  const authenticate = useCallback(async (mode: 'login' | 'register', username: string, password: string, preferredPatient?: string) => {
    if (mutation.current || logoutRequired.current) throw new Error('请先完成当前账号操作。');
    mutation.current = 'authenticate';
    const requestGeneration = ++generation.current;
    clearIdentity(); setError(null); setErrorSource(null); setLoading(true);
    const pending = runSessionMutation(async () => {
      const bootstrap = await api.bootstrap();
      if (requestGeneration !== generation.current) return null;
      const session = await api[mode](username, password, bootstrap.csrf_token);
      if (!preferredPatient) return session;
      if (requestGeneration !== generation.current) return null;
      await api.savePatientSelection(preferredPatient, session.csrf_token);
      if (requestGeneration !== generation.current) return null;
      return api.readSession();
    });
    authentication.current = pending;
    try {
      const { value: session, epoch } = await pending;
      if (!session || requestGeneration !== generation.current) return false;
      return await applySession(session, requestGeneration, epoch, preferredPatient);
    } catch (failure) {
      if (requestGeneration !== generation.current) return false;
      throw failure;
    } finally {
      if (authentication.current === pending) authentication.current = null;
      if (requestGeneration === generation.current) {
        mutation.current = null; setLoading(false); setInitialized(true);
      }
    }
  }, [applySession, clearIdentity]);
  const login = useCallback((username: string, password: string, preferredPatient?: string) => authenticate('login', username, password, preferredPatient), [authenticate]);
  const register = useCallback((username: string, password: string) => authenticate('register', username, password), [authenticate]);

  const logout = useCallback(async () => {
    if (mutation.current === 'logout') return;
    mutation.current = 'logout'; logoutRequired.current = true;
    const requestGeneration = ++generation.current;
    const pendingAuthentication = authentication.current;
    // Invalidate before awaiting the network, including stale relation retries.
    clearIdentity(); setError(null); setErrorSource(null); setLoading(true);
    try {
      // If sign-in was already sent, finish sign-out after its cookie/token
      // rotation rather than letting a late sign-in restore the server session.
      if (pendingAuthentication) await pendingAuthentication.catch(() => null);
      if (requestGeneration !== generation.current) return;
      await runSessionMutation(async () => {
        const bootstrap = await api.bootstrap();
        await api.logout(bootstrap.csrf_token);
      });
      if (requestGeneration !== generation.current) return;
      logoutRequired.current = false;
    } catch (failure) {
      if (requestGeneration === generation.current) {
        setError(failureMessage(failure)); setErrorSource('logout');
      }
      throw failure;
    } finally {
      if (requestGeneration === generation.current) {
        mutation.current = null; setLoading(false); setInitialized(true);
      }
    }
  }, [clearIdentity]);

  const selectPatient = useCallback(async (id: string) => {
    if (!hasCurrentAccess(principalId, patientId, scopeVersion) || loading || error || mutation.current || logoutRequired.current || !principalId
      || !relations.some(relation => relation.patient_id === id && canSelectPatient(relation))) throw new Error('当前关联不允许选择此患儿');
    const initiatingSession = verifiedSession.current;
    const requestGeneration = ++generation.current;
    setLoading(true);
    let phase: 'session' | 'relations' = 'relations';
    let validationStarted = false;
    try {
      const { value: session, epoch } = await runSessionMutation(async () => {
        await api.savePatientSelection(id);
        return api.readSession();
      });
      if (requestGeneration !== generation.current) throw new Error('登录身份或当前患儿已改变，请重新核验。');
      // Selection receipt proves only the saved ID. Read current session and
      // grants before exposing this patient's clinical state or write actions.
      phase = 'session';
      assertSessionEpoch(epoch);
      if (session.principal_id !== principalId) {
        clearIdentity();
        throw new Error('当前账号已改变，请重新核验访问权限。');
      }
      if (!session.session_binding || JSON.stringify([session.principal_id, session.audience, session.session_binding]) !== initiatingSession) {
        throw new Error('当前会话已改变或尚未确认，请重新核验访问权限。');
      }
      phase = 'relations';
      if (session.selected_patient_id !== id) throw new Error('当前患儿选择已改变，请重新核验访问权限。');
      validationStarted = true;
      if (!await applySession(session, requestGeneration, epoch, id)) throw new Error('患儿选择已保存，访问权限尚未完成核验。');
    } catch (failure) {
      if (requestGeneration === generation.current && !validationStarted) {
        if (failure instanceof ApiError && failure.status === 401) { clearIdentity(); setError(null); setErrorSource(null); }
        else { suspendPatientAccess(); setError(failureMessage(failure)); setErrorSource(phase); }
      }
      throw failure;
    } finally {
      if (requestGeneration === generation.current) setLoading(false);
    }
  }, [applySession, clearIdentity, error, hasCurrentAccess, loading, patientId, principalId, relations, scopeVersion, suspendPatientAccess]);

  useEffect(() => {
    const previous = previousUrlScope.current;
    const params = new URLSearchParams(location.search);
    const changedAccount = principalId !== null && (Boolean(previous?.principal && previous.principal !== principalId)
      || params.has('record_intent_actor') && params.get('record_intent_actor') !== principalId);
    const forceClear = logoutRequired.current || discardScopedUrl.current || changedAccount;
    if (!forceClear && (loading || error)) return;
    const changed = previous && (previous.principal !== principalId || previous.patient !== patientId);
    if (forceClear || params.get('record_intent_actor') !== principalId || params.get('record_intent_patient') !== patientId) {
      params.delete('record_intent'); params.delete('record_intent_actor'); params.delete('record_intent_patient');
    }
    if (forceClear || !principalId || !patientId || changed) params.delete('draft');
    previousUrlScope.current = { principal: principalId, patient: patientId };
    const search = params.toString();
    // 当前页面已经导航时，旧页面的参数清理不能重新进入原来的业务路径。
    if (search !== new URLSearchParams(location.search).toString()
      && window.location.pathname === location.pathname && window.location.search === location.search) {
      void navigate({ pathname: location.pathname, search: search ? `?${search}` : '', hash: location.hash }, { replace: true });
    }
  }, [error, loading, location.hash, location.pathname, location.search, navigate, patientId, principalId]);

  const accountStatus = principalId ? 'active' : 'anonymous';
  const hidePatientContent = Boolean(error && patientAccessSuspended) || errorSource === 'logout';
  return (
    <AccessContext.Provider value={{ principalId, accountStatus, csrfToken, patientId, relations, error, errorSource, selectPatient, refresh, capabilities, loading, scopeVersion, hasCurrentAccess, login, register, logout }}>
      {loading && <main className="state-card" role="status">正在检查访问权限</main>}
      {!loading && hidePatientContent && <main className="state-card" role="alert">
        <h2>{errorSource === 'logout' ? '退出登录未完成' : errorSource === 'relations' ? '账号已登录，患儿关联暂未读取' : '账号会话暂未核验'}</h2>
        <p>{errorSource === 'logout' ? '本机患儿资料已清除，请重试退出以结束服务端会话。' : '患儿资料访问已暂停，未保存的本地编辑已清除。重新核验后会读取原提交结果，再由本人决定是否重试；无需重复注册。'}</p><p>{error}</p>
        {errorSource !== 'logout' && <button type="button" className="text-action" onClick={() => void refresh()}>重新核验访问权限</button>}
        <button type="button" className="text-action" onClick={() => void logout().catch(() => { /* Provider presents the failure. */ })}>{errorSource === 'logout' ? '重试退出登录' : '退出登录'}</button>
      </main>}
      {initialized && <div hidden={loading || hidePatientContent}><Fragment key={`${principalId ?? 'anonymous'}:${patientId ?? 'none'}:${scopeVersion}`}>{children}</Fragment></div>}
    </AccessContext.Provider>
  );
}

export function useAccess(): AccessContextValue {
  return useContext(AccessContext);
}
