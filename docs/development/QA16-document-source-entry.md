# QA16 文书归组的原文入口

当前状态为 ACCEPTED，仅限原文入口及实际软件读取范围；本记录作为专项状态来源。需求为 FR-15、FR-17、GRAC-03、GRAC-05，关联 T43、T45、QA12、QA14。来源为本次全部功能开发与真实资料验证授权。代码基线为 8ca48b9 及当前工作区，环境为 5177 前端、8003 接口与独立真实资料数据库；last_verified 为 2026-10-18。

已读取开发索引、T43、PRD 第 8.19.3 与 8.19.5 节、UI2GroupingPage、useArchiveDocument、目录入口及文书来源接口。实际输入为已保存的一份真实原页文书，包含83项未专业核对的事实。接口、文书版本、原件及字段来源已经完成软件保存与回读检查。

归组页面在取得服务端文书后，为原文入口使用准确 document_id 和 version_id。链接通过 document_version 指定文书版本；目录入口仅指定 document 时，使用当前实际读取的文书版本。来源页面继续执行原有身份、患儿、版本、用途及原件权限检查。数据、接口和临床事实语义保持原有要求。

修改范围为 guardian-h5/src/features/archives/UI2GroupingPage.tsx。实际账号与真实文书的7项 Chrome DOM 检查全部通过，包括准确原文链接、保存文字、未知状态、刷新、关键词片段、文书导航及83项后续保存内容；页面运行错误为0。TypeScript 和生产构建通过，执行命令为 npm run build。原始证据为 .qa-work/QA12/reports/saved-reads-browser.json；接口更正、历史、归组与关键词查找的12项检查和30次实际响应为 saved-workflow.json。真实正文不进入 Git。

实际手机、视觉对照和临床准确性由原任务继续验收，本专项不登记这些范围通过。

实际点击入口、来源接口及原页身份另有3项 Chrome检查通过，证据为source-navigation-browser.json。来源请求返回200，准确文书版本与原页一致，来源页面呈现已保存原始文书。最终 TypeScript 与生产构建输出为 .qa-work/QA12/frontend-source-build.log。
