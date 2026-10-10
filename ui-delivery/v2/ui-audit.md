# UI 2.0 交付检查

能力：DESIGN。范围依据为 PRD 2.0、当前家庭 H5 路由和已有品牌资产。页面分母为 39，状态组合分母为 328；六个受控支持页面标记为 INFERRED。

## 当前产物

- 39 个可点击页面，覆盖 FR-01—FR-22。
- 328 个适用页面／状态组合，支持独立选择。
- 统一 Token、页面规范、状态规范、来源与待确认清单。
- 复用儿童、Logo 和三组三维图标，资产按真实文件检查。
- 可编辑 React／CSS 设计来源与独立 Vite 构建。

## 检查证据

构建、浏览器及清单检查结果记录于 checks/。只有已执行且保存结果的检查登记通过。页面状态和局部交互检查不能证明临床规则、真实保存、通知、服务端草稿或完整病例流程。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 独立生产构建 | 通过；1889 个模块；lucide-react 存在浏览器构建指令提示 | checks/build.log、checks/build-check.json |
| 页面与资产清单 | 通过；39 个页面、9 项资产、没有新增生成图片 | checks/structure-check.json |
| 页面与状态 | 328 个组合全部通过 DOM 与边界检查 | checks/browser-check.json |
| 手机宽度 | 320、360、390、430、580 px，共 195 次检查通过 | checks/browser-check.json |
| 文字放大 | 39 个页面在 200% 文字尺寸下通过横向边界检查 | checks/browser-check.json |
| 操作与键盘 | 首页进入报告与逐项确认；说明弹层焦点、Tab、Escape 与焦点恢复；设计操作反馈；提醒启用与关闭通过 | scripts/check_browser.mjs、checks/browser-check.json |
| 资源与服务范围 | 页面异常与控制台错误数量为零；业务请求数量为零 | checks/browser-check.json |
| PDF 内容 | 15 页；39 个页面名称均可提取；资产尺寸与文件校验和已记录 | checks/export-check.json、asset-manifest.csv |

复验命令在项目根目录执行：`node ui-delivery/v2/scripts/check_browser.mjs`。需要先启动 README.md 所述设计预览服务。PDF 内容检查使用文本提取与文件结构检查，没有进行图像检查。

## 未验证范围

按照用户限制，未执行截图、图像识别或视觉比对，未输出新的页面 PNG，也不声称像素一致。当前交付的页面稿是可以打开和操作的浏览器设计。没有生成新的图片资产，Master、1x、2x 复用已有文件。

真实患者资料填充、全部数据密集状态、手机硬件与微信兼容、辅助阅读器实际朗读、正式后端接入及真实临床内容均未登记通过。UI 源码位于独立目录，没有修改 guardian-h5 业务代码。
