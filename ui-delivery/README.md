# 登录后页面与预警设计

PRD 2.0 对应的当前高保真设计见 [UI 2.0 交付说明](v2/README.md)，可点击入口为 http://127.0.0.1:5188/?screen=home 。

版本 UI 3.0，日期 2026-09-28，能力 DESIGN。产品画板采用深蓝色顶部与统一的三维黏土图标，不常驻 Logo，页面内容以安安和安安妈妈的设计情景组织。

首页使用“陪伴安安，健康成长”，正文分为预警、儿童与三项症状、快捷记录三组。预警直接展示主要症状、就医行动与时间；完整依据进入详情，检验清单进入报告页面。甲方需求 F01—F12 的覆盖与缺口见 requirements-review.md。

普通预览根据浏览器的可用宽度和高度等比例显示完整390 × 844 px画框，工具栏与底部区域同时保持可见；评审说明独立滚动。画稿导出保持原始设计尺寸。窗口、页面、预警状态与弹层的检查记录见 checks/preview-layout-check.json。

## 查看入口

- 本机页面预览：http://127.0.0.1:5186/?page=home
- 预警画板：http://127.0.0.1:5186/?board=alerts
- Logo 与16套四季儿童形象：http://127.0.0.1:5186/identity/
- 全部页面预览板：screens/contact-board.png。
- 预警预览板：alerts/contact-board.png。
- 图稿 PDF：high-fidelity-boards.pdf。
- 设计规范 PDF：design-specifications.pdf。

## 页面与内容

23 个页面包括已登录身份、患儿关联、首页、体温症状、报告与治疗、时间轴、详情、更正与历史、历次检验、预警与详情、摘要、我的、草稿及异常恢复。screens/png 提供23张主图与8张补充图，states/png 提供32项组件状态，alerts/png 提供10种预警画面，共73张图稿。

产品画稿使用面向家长的正式样式，设计性质与示例资料说明位于外部评审区域及交付文档。设计内容不对应真实账号、病例或临床评估。

底部导航仅保留在首页、记录、我的；20个二级及更深页面使用顶部返回入口，返回实际来源或直接访问时的父页面。

症状记录增加普通话语音入口、聆听与停止、文字核对和确认加入说明。已有说明保持原文，转写内容需要本人确认，体温及症状字段独立核对。浏览器语音服务、权限错误与文字编辑使用实际事件，未构造识别结果。AI 辅助机会及依赖见 ai-assistance-review.md。

全局图标已由三组三维透明素材统一提供；主导航、资料分类、表单、拍照、原图查看及反馈状态沿用同一材质和光照。卡片、按钮、顶部与弹层使用分层高光及环境阴影，文字区保持可读；预警颜色与就医提示保留原有含义。图标素材与引用记录见 asset-manifest.csv、image-generation-manifest.csv 和 page-asset-map.csv。

产品名称“sJIA 守护者联盟”和既有 Logo 组成横向品牌组合，放在登录身份页上部及我的页面管理内容之后。首页保持原有三组核心内容，顶部继续显示当前任务标题。

报告与治疗新增原图流程：系统相机或相册选择、照片整理、逐项确认、设备保存、记录详情。缩略图可以打开全屏查看，支持放大、旋转与翻页。列表、确认页、详情引用同一原始文件。指标默认显示报告异常标记与 sJIA 关注项目，其他结果通过全部指标查看。

新选择的图片与登记内容保存在当前浏览器的 IndexedDB，刷新后可读取。项目尚未提供获准报告、治疗清单及药品原图；已有示例记录不附生成的报告图片。首次添加需要用户选择实际文件。

## 预警状态机

关注使用琥珀色，重要使用橙色，紧急使用红色。评估过程、缺项、无当前事件、失效、失败及未启用分别设计。READ 只更新阅读状态；INVALIDATE 使当前结果失效；重新评估通过独立事件返回新的结果。

preview/alert-machine.js 提供可运行的状态转换，alerts/state-machine.mmd 提供 Mermaid 图，alerts/specifications.md 提供颜色与行为规范。交互预览右侧可选择预警情景，并逐项触发允许事件。

## 交付与复验

design-system.md、tokens.json、screens/page-specifications.md、states/state-specifications.md 和 alerts/specifications.md 是设计说明。route-design-map.csv、asset-manifest.csv、page-asset-map.csv 记录页面与资产引用。检查范围与结果见 ui-audit.md 和 checks。

在项目目录使用 Node 执行 guardian-h5/node_modules/vite/bin/vite.js --config ui-delivery/vite.config.mjs 启动预览。scripts/capture_design.mjs 检查图稿及设计交互，scripts/export_boards.mjs 输出预览板，PDF 导出脚本位于 scripts。

业务应用 guardian-h5 源码独立保留。真实认证、OCR、服务器上传、跨设备保存、临床规则和联系渠道需要正式业务实现；本设计不定义临床阈值或用药建议。scripts/check_document_flow.mjs 使用项目已有 PNG 验证原图文件的选择、查看、本地保存及字节一致性，不将这些图像作为临床资料或图稿素材。
