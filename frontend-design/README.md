# 前端详细设计工件

日期：2026-10-07。版本：FRONT V2.0。能力：DESIGN。有效性：CURRENT。当前 UI2 全部页面、状态、接口及资产由 [完整交互规范](v2-interaction.md)、[39 路由](v2-routes.csv)、[新增类型](v2-types.ts)、[状态采用表](v2-state-adoption.csv)与 [准确 Token 映射](v2-token-map.json)维护。

当前检查执行 `env TMPDIR="$PWD/.planning-work/tmp" PYTHONPATH=.data-work/deps backend/.venv/bin/python frontend-design/check_v2.py`，使用成熟 TypeScript、PostCSS、JSON Schema、Markdown 与 CSV 工具。唯一主记录为 [T38](../docs/development/T38.md)，准确证据由 CHECK-T38 保存。业务、手机与视觉分别验收；视觉 NOT_RUN，不能由构建或 DOM 声明还原通过。

| 文件 | 维护内容 |
| --- | --- |
| [前端 LLD](../sJIA合并MAS预警平台前端LLD.md) | 七节设计；上游依赖、组件、状态、字段传播、轮询、隐私、性能与验收 |
| [types.ts](./types.ts) | BACK 1.0.0 DTO、医学时间、来源及客户端上下文类型 |
| [routes.csv](./routes.csv) | 23 个业务及状态入口、S00、角色、需求、状态、门禁与接口引用 |
| [api-bindings.csv](./api-bindings.csv) | 全部家庭及 family_support 操作的组件职责、Schema、恢复与缓存 |
| [token-map.json](./token-map.json) | UI Token 到 CSS 变量、组件及预警表现的映射 |
| [acceptance.csv](./acceptance.csv) | FRONT-Q01—Q20 的入口、实际输入、动作、完成条件及当前阻塞 |
| [validation-report.json](./validation-report.json) | 设计检查范围、工具版本、命令、原始输出和输入 SHA-256 |

执行 `PYTHONPATH=.data-work/deps python3 frontend-design/validate.py`。校验使用项目已安装 TypeScript 7.0.2，以及 `.data-work/deps` 中的 jsonschema、referencing、markdown-it-py。TypeScript API 按当前安装包的 `dist/api/sync/api.js` 读取语义类型及真实 App AST；Schema 引用由 referencing 解析。全部中间结果限定在已忽略 `.front-work/`。

验证 DTO 的顶层属性、必填、类型、空值、直接枚举及 TaskStatus；嵌套条件、Schema 格式和跨字段业务仍需要运行时校验。路由检查核对当前 App 和 UI 维护表；Token 检查只核对资产路径，不读取图像像素。

既有文件维护 V1 兼容类型、接口和检查范围。当前实际应用与验收状态以唯一交付记录为准，自建认证、Ark 和本地转写已有代码；本期全部功能仍需真实业务复验。当前设计检查不调用认证、OCR、规则或通知，不创建病例或替代服务响应。视觉及 Mermaid 渲染保持 NOT_RUN。

## 2026-10-11 原始需求补充

原有39页V2目录是历史设计范围，不是当前应用全部路由。新增原始需求入口见 [补充路由](original-scope-routes.csv)，业务契约见 [原始需求接口说明](../backend-design/original-scope-api.md)。`/alert-presets` 为新增页面，沿用已确认的原版立体视觉体系，没有对应的旧完整页面PNG；不能将其声称为旧稿一比一复刻。入口仅在真实关系动作包含 `select_alert_preset` 时可用，服务端仍逐次验证授权、专业批准和配置版本。新增入口的工程和浏览器检查须独立记录，不能沿用39页旧检查结论。

## C02 工具兼容候选（2026-10-13）

联合、条件投影、显式 unknown 与内联/重导出类型的检查范围见 [C02 工具说明](C02-VALIDATOR-NOTES.md)。纯工具回归与完整 CLI 终态分开记录；缺失本地依赖和真实契约差异不能被解释为通过。
