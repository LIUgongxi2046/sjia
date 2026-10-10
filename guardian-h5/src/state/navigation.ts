// NavigationMemory：滚动位置与返回上下文只保存在内存，不进入 URL、history.state 或持久存储。
export interface ReturnContext {
  pathname: string;
  search: string;
  key: string;
  scroll: number;
}

export interface RecordFilters {
  category: '全部' | '体温症状' | '检验报告' | '治疗资料';
  organization: string;
  verification: 'all' | 'self' | 'pending' | 'verified' | 'unverified' | 'needs_correction' | 'revoked';
  source: 'all' | 'self_report' | 'report_transcription' | 'authorized_clinical';
  project: string;
  start: string;
  end: string;
  open: boolean;
}

export function defaultRecordFilters(): RecordFilters {
  return { category: '全部', organization: '', verification: 'all', source: 'all', project: '', start: '', end: '', open: false };
}

export interface ArchiveFilters extends Omit<RecordFilters, 'category'> {
  category: 'all' | 'inpatient' | 'outpatient' | 'pending';
  query: string; documentType: string; episode: string; unknownTime: boolean; unknownOrganization: boolean; unknownEpisode: boolean;
}

export function defaultArchiveFilters(): ArchiveFilters {
  return { ...defaultRecordFilters(), category: 'all', query: '', documentType: '', episode: '', unknownTime: false, unknownOrganization: false, unknownEpisode: false };
}

class NavigationMemory {
  archive = { open: false, category: '', time: 'latest' };
  records: { owner: string | null; filters: RecordFilters } = { owner: null, filters: defaultRecordFilters() };
  archives: { owner: string | null; filters: ArchiveFilters; applied: ArchiveFilters } = { owner: null, filters: defaultArchiveFilters(), applied: defaultArchiveFilters() };
  private scrollPositions = new Map<string, number>();
  private returnContext = new Map<string, ReturnContext>();

  rememberScroll(key: string, scrollY: number): void {
    this.scrollPositions.set(key, scrollY);
  }

  readScroll(key: string): number {
    return this.scrollPositions.get(key) ?? 0;
  }

  setReturnContext(contextKey: string, context: ReturnContext): void {
    this.returnContext.set(contextKey, context);
  }

  readReturnContext(contextKey: string): ReturnContext | undefined {
    return this.returnContext.get(contextKey);
  }

  clear(): void {
    this.archive = { open: false, category: '', time: 'latest' };
    this.records = { owner: null, filters: defaultRecordFilters() };
    this.archives = { owner: null, filters: defaultArchiveFilters(), applied: defaultArchiveFilters() };
    this.scrollPositions.clear();
    this.returnContext.clear();
  }
}

export const navigationMemory = new NavigationMemory();

// 退出或切换患儿时清理内存，避免跨账号残留。
export function clearNavigationMemory(): void {
  navigationMemory.clear();
}
