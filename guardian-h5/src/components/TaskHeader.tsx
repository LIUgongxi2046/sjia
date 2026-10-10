import type { ReactNode } from 'react';

// 深蓝任务标题：使用 headerDesign 渐变（--header-start/--header-end/--header-text）。
export function TaskHeader({ title, subtitle, trailing, leading, kicker = '家庭记录' }: { title: string; subtitle?: string; trailing?: ReactNode; leading?: ReactNode; kicker?: string }) {
  return (
    <div className="task-header">
      <p className="task-header-kicker">{kicker}</p>
      <div className="task-header-row">
      {leading}
      <div className="task-header-copy">
        <h1 tabIndex={-1}>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {trailing && <div className="task-header-trailing">{trailing}</div>}
      </div>
    </div>
  );
}
