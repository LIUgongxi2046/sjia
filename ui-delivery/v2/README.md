# sJIA 守护者联盟 · 高保真 UI 2.0

日期：2026-10-07。对应 PRD V2.0.0。能力：DESIGN。

本地可点击入口：http://127.0.0.1:5188/?screen=home 。手机独立入口：http://127.0.0.1:5188/?screen=home&mode=phone 。页面目录提供 39 个家庭及受控支持页面，右侧可以选择适用状态，顶部提供 320、390、430 px 画框宽度。

主要流程：

- 首页 → 拍照报告 → 逐项确认 → 原始文书与归组。
- 记录 → 长期档案 → 批次进度 → 重复资料核对 → 新增内容摘要。
- 记录 → 观察时段 → 历次检验 → 详情与更正历史。
- 首页 → 预警记录 → 提示依据 → 就医摘要。
- 我的 → 患儿管理、资料补充提醒、资料使用说明。
- 独立受控支持：专业核对、评估依据、内容启用、评价集合、用途副本及通知记录。

文件包括 design-system.md、design-specifications.pdf、requirements-review.md、tokens.json、screen-design-map.csv、screens/page-specifications.md、states/state-specifications.md、asset-manifest.csv、page-asset-map.csv、assumptions-and-open-questions.md 和 ui-audit.md。页面稿为可编辑 React／CSS 及浏览器预览。已有 Master、1x、2x 素材按清单复用，没有生成新的图片。

运行：在项目根目录执行 `node guardian-h5/node_modules/vite/bin/vite.js --config ui-delivery/v2/vite.config.mjs`。构建：`node guardian-h5/node_modules/vite/bin/vite.js build --config ui-delivery/v2/vite.config.mjs`。构建输出位于已忽略目录 `.ui-work/v2-build/`。

预览不连接业务 API，不提交病例、调用模型或发送通知。当前没有真实病例填充，未知数据保持未知；状态切换用于设计查看。按照用户限制未执行截图、图像识别或视觉比对，相关检查状态见 ui-audit.md。
