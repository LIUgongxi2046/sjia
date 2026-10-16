import { useCallback, useState } from 'react';
import { MemoryRouter, useLocation } from 'react-router';
import { UI2Frame, UI2Sheet } from '../components/UI2Frame';
import { HomeCompanion, HomeQuickActions } from '../components/HomeVisual';
import { ChevronRight, CircleAlert } from '../components/ThreeDIcon';
import { ArchivePanel } from '../features/home/ArchivePanel';
import { useCompanionPreference } from '../state/companionPreference';
import { archiveSpecimenRecords } from './archive-specimen-data';
import specimenStyles from './design-specimen.css?inline';

export function DesignSpecimen({ longCopy = false, initialPanel = false, externallyLabelled = false }: { longCopy?: boolean; initialPanel?: boolean; externallyLabelled?: boolean }) {
  return <><style>{`.specimen-boundary-note{position:sticky;top:0;z-index:1000;margin:0;padding:8px 12px;background:#173f70;color:#fff;font:12px/1.5 system-ui,sans-serif}`}</style>{!externallyLabelled && <aside className="specimen-boundary-note" role="note">合成界面样本，非真实业务结果</aside>}<MemoryRouter><SpecimenContent longCopy={longCopy} initialPanel={initialPanel} /></MemoryRouter></>;
}
function SpecimenContent({ longCopy, initialPanel }: { longCopy: boolean; initialPanel: boolean }) {
  const [panelOpen, setPanelOpen] = useState(initialPanel), [notice, setNotice] = useState('');
  const { image } = useCompanionPreference(), location = useLocation();
  const close = useCallback(() => setPanelOpen(false), []);
  const showEntry = () => setNotice('这里只检查合成样本的入口，没有提交或读取业务资料。');
  return <UI2Frame title="首页" screenId="DEV-S03-SPECIMEN" audience="family" primary="home"
    heading={longCopy ? '长文字排版压力测试，仅供开发核验' : '陪伴安安，健康成长'} kicker="合成界面样本，非真实业务结果"
    note="原设计合成样本，仅供外观比较，不是患者、临床批准或真实业务证据。" onBack={showEntry} onNavigate={showEntry}
    overlay={panelOpen ? <UI2Sheet title="孩子的记录" variant="archive" onClose={close}><ArchivePanel records={archiveSpecimenRecords} status={null} patientLabel="安安（合成样本）" badgeLabel="合成记录" onViewRecord={showEntry} />
      {(notice || location.pathname !== '/') && <p role="status">{notice || '这里是合成记录入口，不读取任何业务记录。'}</p>}
    </UI2Sheet> : undefined}>
    <style>{specimenStyles}</style>
    <section className="home-alert visual-warning-specimen">
      <div className="eyebrow"><CircleAlert size={18} /><strong>重要提示</strong><span className="specimen-read-badge">未查看</span></div>
      <h2>{longCopy ? '合成提示文案：较长的标题应完整换行并保留可读性' : '发热伴皮疹、关节不适'}</h2><p>{longCopy ? '这段文字仅用于检测界面换行、滚动和重排，不能作为任何医疗信息或批准证据。' : '需要联系医生评估就诊安排'}</p>
      <div className="specimen-alert-footer"><small>38.2 °C · 09/27 19:40</small><button onClick={showEntry}>查看详情<ChevronRight size={16} /></button></div>
    </section>
    <HomeCompanion name={longCopy ? '合成文字压力测试患儿标识（并非真实姓名）' : '安安（合成样本）'} description={longCopy ? '长说明仅为界面测试，不代表任何真实患儿或身份' : '原图中的示例资料'} companionImage={image}
      onArchive={() => setPanelOpen(true)} onViewObservation={() => setPanelOpen(true)} observations={longCopy ? ['体温', '皮疹观察', '关节表现'].map(label => ({ label, value: '合成的较长状态说明，用于验证文字不会被截断', time: '时间信息保持完整显示，仅为排版测试' })) : [
        { label: '体温', value: '38.2 °C' }, { label: '皮疹观察', value: '观察到皮疹' }, { label: '关节表现', value: '无法确定' },
      ]} />
    <HomeQuickActions onObserve={showEntry} onReports={showEntry} onTherapies={showEntry} />
    {notice && <p role="status">{notice}</p>}
  </UI2Frame>;
}
