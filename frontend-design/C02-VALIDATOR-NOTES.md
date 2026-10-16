# C02 前端契约工具候选

日期：2026-10-13。此说明描述独立 C02 工具及已批准契约兼容候选；完整命令终态以随包结果与日志为准，不能据此宣布应用功能或临床验收完成。基线为 1761 文件清单 `0439f5d6f344d71e799930bd3d6db9cfbe922a5d62e6d540422ed1f6f65ff2e3`，本候选未应用到冻结源或运行实例。

## 范围与语义

- V1 描述入口与 V2 检查使用同一属性描述器；两者使用既有有限本地 Schema 注册器。未知网络引用、缺失文件、越界路径和条件/联合分支内的坏引用仍失败。
- `anyOf`/`oneOf` 遍历全部分支；可明确投影的类型类别合并。混合字面枚举联合等无法保持精确表示的情况继续失败，不把枚举泛化为 string，不任取一个分支。
- `$ref` 的同级约束、`allOf` 及仅含 required 的分支均保留。交集求交而非求并，合法窄化不会被误报为冲突；空交集拒绝。嵌套 `$id` 维持自己的解析基址。
- 只有显式空 Schema `{}`/`true` 可描述为 unknown；TS 必须确为 unknown，any、object 或任意固定类型不能代替。`oneOf` 含不受限分支时不能按 anyOf 展平。
- 条件 then/else 的必填项不会变成无条件必填，也不会被从业务 Schema 删除。结构投影只比较无条件可选性；跨字段条件、oneOf 排他性、格式、数值范围仍由原 JSON Schema/领域运行校验负责，工具不声称 TS 本身验证了这些运行语义。合成正反例证明原条件仍拒绝缺少 then 必填或落入 else 禁止字段的对象。
- 目前不表示 bottom/never 分支；例如某联合含不可能成立的子分支，即使整体尚可满足也可能保守失败。不得捕获错误后跳过该分支。顶层对象同时包含 oneOf 与 anyOf 时明确拒绝，不能用其中一个覆盖另一个。
- 仅支持可还原为单一确定属性名的 patternProperties（如 `^clinical_selections$`）；一般正则属性需要另行定义有限绑定，不能忽略。

## 精确 TS 绑定

`check-v2-types.mjs` 通过 TypeScript 语义导出符号读取命名声明与重导出，不做同名文件猜测。特殊绑定在 `explicitBindings` 中逐项列明：

- 明确的导出类型加属性/数组/非空路径，用于内联对象。
- 内联联合用准确判别字段与完整字面集合筛选，必须唯一匹配；顶层对象联合对照全部实际/预期成员，多余或缺失分支都失败。
- `PageAttemptViewBase` 仅投影已命名 `PageAttemptView` 的基底，明确移除其唯一扩展 `pricing_scope`；完整 `PageAttemptView` 也独立检查，扩展属性不存在时拒绝。
- 三项 Upload 类型绑定到指定模块、准确 Schema 字面引用的唯一 `validateApiValue<T>` 类型参数；校验语义符号必须来自真实 schema.ts 导出。同名局部函数不能冒充；零匹配、多匹配或无显式类型都拒绝。
- 对条件对象联合枚举全部成员属性，不能只检查共有属性；可选性按全部成员推导，显式必填字段的 undefined 仍失败。unknown 吸收窄域之前逐成员验证，any 仍拒绝。
- 八项 query/receipt 绑定已在获批契约片中接入真实 API 参数、URLSearchParams 构造或回执类型。没有可证明绑定的定义仍失败。不得新增 any、跳过 Schema 定义或无语义依据修改产品 DTO。

失败时报告列出全部 193 个对象/对象联合的独立结果并退出 1；只有全部通过才退出 0。`checked_types` 是历史设计结构检查数，不是业务功能完成数。

## 本轮验证与环境

本机原 `.venv` 缺少 Markdown 解析库。未安装/改变依赖；只读复用 `.venv` 与 `node_modules`，通过已有系统 Markdown 包补齐解释器搜索路径。以下命令在独立候选源目录执行，`backend/.venv` 与 `guardian-h5/node_modules` 是指向既有依赖的只读使用链接；不要在其上安装包。默认 Vite build 已通过，但其 bundled 配置加载会在共享 node_modules/.vite-temp 临时写入后删除；本轮已记录该环境副作用。后续 native 配置加载复跑同样通过，可避免该临时路径，未为此改应用配置或依赖。

```sh
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/backend/.venv/lib/python3.12/site-packages:/usr/lib/python3/dist-packages"
export TMPDIR="$PWD/.planning-work/tmp"
backend/.venv/bin/python -m unittest discover -s frontend-design -p 'test*.py' -v
backend/.venv/bin/python -O -m unittest discover -s frontend-design -p 'test*.py' -v
node frontend-design/test_check_v2_types.mjs
backend/.venv/bin/python frontend-design/check_v2.py
node frontend-design/check-v2-types.mjs
node guardian-h5/node_modules/typescript/bin/tsc -p frontend-design/tsconfig.json
node guardian-h5/node_modules/typescript/bin/tsc -p guardian-h5/tsconfig.json --noEmit
(cd guardian-h5 && npm run build -- --configLoader native)
```

## 完整 CLI 的受控前置依赖

冻结交付清单未包含前端 LLD 准确引用的 `demo-data/README.md`。经授权，仅在本地 validator staging 放入原库该文件的准确字节：4398 bytes，SHA-256 `72c75ff8e7b7f1d2b87bb472748924dd690b6ac972ef7cebddd5ffbd2471c4e2`。该文档包含原始健康资料说明，公开再分发依据未确立，因此是受控非交付依赖，不是占位文件、错误链接或可忽略资产。本片不含该文档及其链接原资料；没有读取任何原病例文件。复现完整 CLI 需在有权取得该依赖的本地工作副本按原相对路径提供准确版本。仅取得本补丁不能保证无需前置资料即可跑完整 CLI；纯工具测试与类型检查不需要该依赖。

## 获批契约兼容片

- RequestView 增加原 Schema 已存在的可选 `disposition_input?: DispositionInput`；不更改提交/返回 wire 或后端授权。
- PageOriginalCopy 状态、DynamicSignal 的 hlh 类型、ClinicalMetricSummary 固定状态字面量及 RegroupRequest 的八项共有字段按真实 Schema/序列化语义校正。
- AssessmentObservationTime raw 类型保留 Schema 允许的可选身份字段。静态和动态 options 在真实 Schema 校验后显式投影成既有 13 字段应用时间对象；仅不向应用传入 id/patient_id，null、未知时间和值均不补造。
- AssessmentViewWire 表达原 Schema 可选 clinical_selections，应用 AssessmentView 仍要求数组。真实 Schema 校验后使用存在性 predicate 缩窄；缺数组维持原报错，不填空数组、不用断言伪造。
- 单独纯测试验证 raw Schema 正反例、守卫拒绝及全部静态/动态候选和时段的投影。前十二项类型/接线变更编译 JS 与改前逐字节相同；A/B 投影是明确获批的应用边界兼容处理，不能说整片运行字节完全不变。

业务 Schema、后端权限、医学语义、数据库、运行服务及 Provider 均未由本片改变。本片仅基于原 0439 清单；不合并其他权限候选目录。完整193检查、纯测试、两项目类型检查与 build 需在相同最终候选上记录；浏览器、视觉、真实业务及临床验收均不在本片结论中。
