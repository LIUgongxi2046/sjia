import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import * as api from '../api/endpoints';
import { ApiError, setSessionCsrfToken } from '../api/client';
import { clearNavigationMemory } from './navigation';

export interface AccessContextValue {
  principalId: string | null;
  accountStatus: 'active' | 'anonymous';
  csrfToken: string | null;
  patientId: string | null;
  relations: api.RelationItem[];
  error: string | null;
  selectPatient: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
  capabilities: string[];
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const defaultContext: AccessContextValue = {
  principalId: null,
  accountStatus: 'anonymous',
  csrfToken: null,
  patientId: null,
  relations: [],
  error: null,
  selectPatient: async () => { throw new Error('AccessProvider required'); },
  refresh: async () => { throw new Error('AccessProvider required'); },
  capabilities: [],
  loading: true,
  login: async () => { throw new Error('AccessProvider required'); },
  register: async () => { throw new Error('AccessProvider required'); },
  logout: async () => { throw new Error('AccessProvider required'); },
};

const AccessContext = createContext<AccessContextValue>(defaultContext);

export function AccessProvider({ children }: { children: ReactNode }) {
  const [principalId, setPrincipalId] = useState<string | null>(null);
  const [accountStatus, setAccountStatus] = useState<'active' | 'anonymous'>('anonymous');
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);
  const [relations, setRelations] = useState<api.RelationItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => { setSessionCsrfToken(csrfToken); }, [csrfToken]);

  function isActive(relation: api.RelationItem) {
    return relation.status === 'active' && Date.parse(relation.valid_from) <= Date.now()
      && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  }

  function canSelectPatient(relation: api.RelationItem) {
    return isActive(relation) && relation.actions.includes('read_raw');
  }

  const applySession = useCallback(async (token: string | null, requestGeneration: number) => {
    const session = await api.readSession();
    const result = await api.listRelations();
    if (requestGeneration !== generation.current) return;
    setPrincipalId(session.principal_id);
    setAccountStatus(session.account_status === 'active' ? 'active' : 'anonymous');
    setCapabilities(session.capabilities ?? []);
    if (session.csrf_token) setCsrfToken(session.csrf_token);
    else if (token) setCsrfToken(token);
    setRelations(result.relations);
    const selectedRelation = session.selected_patient_id
      ? result.relations.find(relation => relation.patient_id === session.selected_patient_id && canSelectPatient(relation))
      : undefined;
    const firstAvailableRelation = result.relations.find(canSelectPatient);
    setPatientId(selectedRelation?.patient_id ?? firstAvailableRelation?.patient_id ?? null);
  }, []);

  const refresh = useCallback(async () => {
    const requestGeneration = ++generation.current;
    setLoading(true);
    setError(null);
      try {
        const bootstrap = await api.bootstrap();
        if (requestGeneration !== generation.current) return;
        setCsrfToken(bootstrap.csrf_token);
        await applySession(bootstrap.csrf_token, requestGeneration);
      } catch (failure) {
        if (requestGeneration === generation.current) {
          setPrincipalId(null);
          setAccountStatus('anonymous');
          setPatientId(null);
          setRelations([]);
          clearNavigationMemory();
          if (!(failure instanceof ApiError && failure.status === 401)) setError(failure instanceof Error ? failure.message : '身份服务读取失败');
        }
      } finally {
        if (requestGeneration === generation.current) {
          setLoading(false);
          setInitialized(true);
        }
      }
  }, [applySession]);

  useEffect(() => {
    void refresh();
    const restore = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('pageshow', restore);
    document.addEventListener('visibilitychange', restore);
    return () => {
      generation.current += 1;
      window.removeEventListener('pageshow', restore);
      document.removeEventListener('visibilitychange', restore);
    };
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    const requestGeneration = ++generation.current;
    const bootstrap = await api.bootstrap();
    const session = await api.login(username, password, bootstrap.csrf_token);
    await applySession(session.csrf_token, requestGeneration);
    setError(null);
  }, [applySession]);

  const register = useCallback(async (username: string, password: string) => {
    const requestGeneration = ++generation.current;
    const bootstrap = await api.bootstrap();
    const session = await api.register(username, password, bootstrap.csrf_token);
    await applySession(session.csrf_token, requestGeneration);
    setError(null);
  }, [applySession]);

  const logout = useCallback(async () => {
      await api.logout(csrfToken ?? undefined);
      generation.current += 1;
      setPrincipalId(null);
      setAccountStatus('anonymous');
      setPatientId(null);
      setCapabilities([]);
      setRelations([]);
      setCsrfToken(null);
      clearNavigationMemory();
      api.clearPendingRecordRequests();
  }, [csrfToken]);

  const selectPatient = useCallback(async (id: string) => {
    if (!relations.some(relation => relation.patient_id === id && canSelectPatient(relation))) throw new Error('当前关联不允许选择此患儿');
    await api.savePatientSelection(id);
    clearNavigationMemory();
    setPatientId(id);
  }, [relations]);

  return (
    <AccessContext.Provider value={{ principalId, accountStatus, csrfToken, patientId, relations, error, selectPatient, refresh, capabilities, loading, login, register, logout }}>
      {loading && <main className="state-card" role="status">正在检查访问权限</main>}
      {initialized && <div hidden={loading}><Fragment key={`${principalId ?? 'anonymous'}:${patientId ?? 'none'}`}>{children}</Fragment></div>}
    </AccessContext.Provider>
  );
}

export function useAccess(): AccessContextValue {
  return useContext(AccessContext);
}
