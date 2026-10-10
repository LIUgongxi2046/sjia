import type { HomeView } from '../../api/endpoints';

export function assessmentText(home: HomeView): { heading: string; body: string } {
  switch (home.assessment_state) {
    case 'configuration_pending': return { heading: '临床提示尚未启用', body: '当前无法评估风险' };
    case 'not_evaluated': return { heading: '尚未完成评估', body: '目前没有可用的评估结果。' };
    case 'insufficient': return { heading: '评估资料不足', body: '请查看记录中的待补充内容。' };
    case 'stale': return { heading: '评估结果已失效', body: '已有结果不能作为当前提示，请查看最新资料。' };
    case 'current': return home.current_alert_id
      ? { heading: '有当前提示', body: '请打开详情查看获准内容和依据。' }
      : { heading: '暂无当前提示事件', body: '当前评估未产生提示事件，不代表排除风险。' };
    default: throw new Error(`未知评估状态：${home.assessment_state}`);
  }
}
