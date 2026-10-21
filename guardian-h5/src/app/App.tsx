import { Navigate, Outlet, Route, Routes } from 'react-router';
import { lazy, Suspense } from 'react';
import { AccessProvider, useAccess } from '../state/access';
import { Layout } from './Layout';
import { UI2HomePage } from '../features/home/UI2HomePage';
import { LoginPage } from '../features/account/LoginPage';
import { DataUsePage } from '../features/account/AccountPage';
import { UI2AccountPage } from '../features/account/UI2AccountPage';
import { DraftsPage } from '../features/account/DraftsPage';
import { UI2AssociationApplyPage } from '../features/association/UI2AssociationApplyPage';
import { UI2AssociationsPage, UI2AssociationDetailPage } from '../features/association/UI2AssociationsPage';
import { ObservationsPage } from '../features/registration/ObservationsPage';
import { ReportsPage } from '../features/registration/ReportsPage';
import { TherapiesPage } from '../features/registration/TherapiesPage';
import { UI2RecordsPage } from '../features/records/UI2RecordsPage';
import { UI2OriginalPage } from '../features/records/UI2OriginalPage';
import { RecordDetailPage, RecordVersionPage } from '../features/records/RecordDetailPage';
import { AnalytePage } from '../features/records/AnalytePage';
import { CorrectionPage, HistoryPage } from '../features/records/CorrectionPage';
import { UI2AlertsPage, UI2AlertDetailPage } from '../features/alerts/UI2AlertsPage';
import { UI2SummariesPage, UI2SummaryDetailPage } from '../features/summaries/UI2SummariesPage';
import { UI2StatusPage } from '../features/status/UI2StatusPage';
import { PrototypePage } from '../features/status/PrototypePage';
import { UI2FamilyFrame } from '../components/UI2FamilyFrame';
import { UI2ReadState } from '../components/UI2ReadState';

const CorrectionRequestPage = lazy(() => import('../features/records/CorrectionRequestPage').then(module => ({ default: module.CorrectionRequestPage })));
const SupportApp = lazy(() => import('../features/support/SupportApp'));
const RegroupPage = lazy(() => import('../features/archives/UI2RegroupPage').then(module => ({ default: module.UI2RegroupPage })));
const GroupingPage = lazy(() => import('../features/archives/UI2GroupingPage').then(module => ({ default: module.UI2GroupingPage })));
const DuplicatesPage = lazy(() => import('../features/archives/UI2DuplicatesPage').then(module => ({ default: module.UI2DuplicatesPage })));
const ArchivePage = lazy(() => import('../features/archives/UI2ArchivePage').then(module => ({ default: module.UI2ArchivePage })));
const BatchPage = lazy(() => import('../features/archives/UI2BatchPage').then(module => ({ default: module.UI2BatchPage })));
const ConfirmPage = lazy(() => import('../features/archives/UI2ConfirmPage').then(module => ({ default: module.UI2ConfirmPage })));
const DeltaPage = lazy(() => import('../features/archives/UI2DeltaPage').then(module => ({ default: module.UI2DeltaPage })));
const PeriodPage = lazy(() => import('../features/records/UI2ObservationPeriodPage').then(module => ({ default: module.UI2ObservationPeriodPage })));
const AlertPresetsPage = lazy(() => import('../features/account/AlertPresetsPage').then(module => ({ default: module.AlertPresetsPage })));
const RemindersPage = lazy(() => import('../features/account/UI2RemindersPage').then(module => ({ default: module.UI2RemindersPage })));

export function App() {
  return <Routes>
    <Route path="support/*" element={<Suspense fallback={<main role="status">正在读取当前资料</main>}><SupportApp /></Suspense>} />
    <Route path="*" element={<FamilyApp />} />
  </Routes>;
}

function FamilyApp() {
  return (
    <AccessProvider>
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route path="account/data-use" element={<DataUsePage />} />
      <Route element={<RequireFamilySession />}>
      <Route path="home" element={<UI2HomePage />} />
      <Route path="register/observations" element={<ObservationsPage ui2 />} />
      <Route path="register/therapies" element={<TherapiesPage />} />
      <Route element={<ReportsPage />}>
        <Route path="register/reports" element={null} />
        <Route path="register/reports/confirm" element={null} />
      </Route>
      <Route path="archives/confirm" element={<Suspense fallback={<UI2FamilyFrame title="逐项确认" screenId="UI2-27" note="每项事实分别保留原值、时间、语境和出处。" backTo="/archives/batches"><UI2ReadState loading /></UI2FamilyFrame>}><ConfirmPage /></Suspense>} />
      <Route path="alerts" element={<UI2AlertsPage />} />
      <Route path="alerts/:alertId" element={<UI2AlertDetailPage />} />
      <Route path="account" element={<UI2AccountPage />} />
      <Route path="alert-presets" element={<Suspense fallback={<main role="status">正在读取已批准的预警方案</main>}><AlertPresetsPage /></Suspense>} />
      <Route path="account/reminders" element={<Suspense fallback={<UI2FamilyFrame title="资料补充提醒" screenId="UI2-32" note="本人自主启用，可暂停、延后和关闭。" backTo="/account"><UI2ReadState loading /></UI2FamilyFrame>}><RemindersPage /></Suspense>} />
      <Route path="records" element={<UI2RecordsPage />} />
      <Route path="records/period" element={<Suspense fallback={<UI2FamilyFrame title="观察时段" screenId="UI2-31" note="观察范围和真实采样时间分别保存，时段标签属于本人自报。" backTo="/records"><UI2ReadState loading /></UI2FamilyFrame>}><PeriodPage /></Suspense>} />
      <Route path="archives" element={<Suspense fallback={<UI2FamilyFrame title="长期档案" screenId="UI2-25" note="按医院、就诊和文书查找，随时回到原文。" backTo="/records"><UI2ReadState loading /></UI2FamilyFrame>}><ArchivePage /></Suspense>} />
      <Route path="archives/batches" element={<Suspense fallback={<UI2FamilyFrame title="批次进度" screenId="UI2-26" note="逐页保存进度，失败和完成内容分别呈现。" backTo="/archives"><UI2ReadState loading /></UI2FamilyFrame>}><BatchPage /></Suspense>} />
      <Route path="archives/batches/:batchId" element={<Suspense fallback={<UI2FamilyFrame title="批次进度" screenId="UI2-26" note="逐页保存进度，失败和完成内容分别呈现。" backTo="/archives"><UI2ReadState loading /></UI2FamilyFrame>}><BatchPage /></Suspense>} />
      <Route path="archives/changes" element={<Suspense fallback={<UI2FamilyFrame title="这次新增了什么" screenId="UI2-30" note="明确比较基准，逐条变化可以查看来源。" backTo="/archives/batches"><UI2ReadState loading /></UI2FamilyFrame>}><DeltaPage /></Suspense>} />
      <Route path="archives/source" element={<UI2OriginalPage />} />
      <Route path="archives/regroup" element={<Suspense fallback={<main role="status">正在读取重组来源</main>}><RegroupPage /></Suspense>} />
      <Route path="archives/grouping" element={<Suspense fallback={<UI2FamilyFrame title="文书与就诊归组" screenId="UI2-28" note="一份文书可以包含多次就诊。" backTo="/archives/confirm"><UI2ReadState loading /></UI2FamilyFrame>}><GroupingPage /></Suspense>} />
      <Route path="archives/duplicates" element={<Suspense fallback={<UI2FamilyFrame title="重复资料核对" screenId="UI2-29" note="同一文件、同次采样和独立检查分别判断。" backTo="/archives/confirm"><UI2ReadState loading /></UI2FamilyFrame>}><DuplicatesPage /></Suspense>} />
      <Route path="summaries" element={<UI2SummariesPage />} />
      <Route path="summaries/:summaryId" element={<UI2SummaryDetailPage />} />
      <Route path="records/versions/:versionId" element={<RecordVersionPage />} />
      <Route path="records/:recordId" element={<RecordDetailPage />} />
      <Route path="records/:recordId/history" element={<HistoryPage />} />
      <Route path="records/:recordId/correction-request" element={<Suspense fallback={<main role="status">正在读取更正申请</main>}><CorrectionRequestPage /></Suspense>} />
      <Route path="records/:recordId/correct" element={<CorrectionPage />} />
      <Route path="records/analytes/:analyteId" element={<AnalytePage />} />
      <Route path="drafts" element={<DraftsPage />} />
      <Route path="associations" element={<UI2AssociationsPage />} />
      <Route path="associations/new" element={<UI2AssociationApplyPage />} />
      <Route path="associations/:relationId" element={<UI2AssociationDetailPage />} />
      <Route path="access-denied" element={<UI2StatusPage kind="access" />} />
      <Route path="service-unavailable" element={<UI2StatusPage kind="service" />} />
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/home" replace />} />
        <Route path="prototype" element={<PrototypePage />} />
      </Route>
      <Route path="*" element={<UI2StatusPage kind="missing" />} />
      </Route>
    </Routes>
    </AccessProvider>
  );
}

function RequireFamilySession() {
  const access = useAccess();
  if (!access.principalId) {
    if (access.loading || access.error) return null;
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
