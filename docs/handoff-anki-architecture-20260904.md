# Anki Attention 架构审计交接

**日期：** 2026-09-04  
**仓库：** `/Users/yangzhao/Code/Anki-Attention-is-All-You-Need`  
**当前阶段：** 架构审计和改造路线图已完成；尚未修改业务代码。

## 用户偏好

- 所有生成物和沟通使用中文。
- 用户未系统阅读过当前代码；代码主要由 vibe coding 产生。
- 表达必须低认知负担：结论先行、一次少量信息、优先图解与时序图，避免长篇术语讲解。
- 用户熟悉 Python 包初始化等基础知识，不需要解释 `__init__.py` 的常识；应解释它在本项目中承担的具体职责与架构影响。
- 用户已要求移除整个 Superpowers 插件；不要再调用或建议任何 `superpowers:*` skill。

## 主要产物

最终中文 HTML 审计与计划：

`/var/folders/tl/v_374hlj5nd3c99pjs4f22g00000gn/T/anki-architecture-final-plan-20260904-163214.html`

该报告已经在浏览器打开并截图检查。包含：

- 当前实现与 Anki 官方插件模型的对照；
- 当前线程方式与官方 `QueryOp` 方向的对比图；
- 推荐的 `AnkiWebView + QueryOp` 目标架构图；
- 容易遗漏的兼容性、隐私、并发和生命周期问题；
- 六阶段实施路线图；
- Anki 官方文档依据。

不要在新交接文档里复制报告全文；需要细节时直接阅读该 HTML。

## 已确认的决策

1. **删除完整的 AnkiConnect 兼容路径。**
   - 删除 `127.0.0.1:8765` fallback。
   - 删除数字模块动态 import。
   - 删除向 AnkiConnect monkey patch 的 `attentionConfig`、`attentionSimulate`、`attentionStartDashboard`。
   - 删除两套重复的配置和模拟语义。

2. **目标方向优先采用 Anki 官方设施：**
   - `AnkiWebView`/Qt 窗口承载 HTML 仪表盘；
   - JS bridge 与 Python 通信；
   - 只读长操作使用 `QueryOp`；
   - UI 更新回主线程；
   - 移除自建 `ThreadingHTTPServer`、端口扫描和 `threading.Event.wait()` 同步桥。

3. **FSRS 私有调用必须隔离。**
   - 当前直接使用 `collection._backend.simulate_fsrs_review()`。
   - 当前直接导入生成的 `SimulateFsrsReviewRequest`。
   - Anki 源码明确警告直接 backend 访问可能随时变化；官方架构文档说明 protobuf 不是公共接口。
   - 如果没有公开替代，应只允许它们存在于唯一的 compatibility adapter，并设置版本门槛和显式降级。

4. **实施后再运行一次 `improve-codebase-architecture`。**
   - 当前无需再次扫描。
   - 再扫描时重点验证：数据 module 是否 deep、JS 是否只负责渲染、FSRS 私有依赖是否只存在一处。

## 关键技术判断

- Python module、`gui_hooks`、QAction、`config.json/config.md`、HTML/CSS/JS 和 `.ankiaddon` 打包都符合 Anki 插件模型。
- “Collection 只能在主线程访问”这个说法不准确。Anki 官方推荐长只读操作使用 `QueryOp` 在后台执行；UI 操作在主线程执行。
- 当前 `mw.taskman.run_on_main()` 并非能力本身错误，而是方向不理想：它把整批 collection 查询压回 UI 主线程，可能冻结界面。
- 当前 HTTP server 只绑定 localhost，能运行但不是 Anki 插件的首选 UI 方式；还带来端口、并发请求、profile 关闭竞态和本机数据暴露成本。
- 当前测试绕过插件包，直接加载 `attention_dashboard.py`，说明真实运行 interface 尚未成为 test surface。
- 当前 `manifest.json` 只有 `name/package`，应在确定支持范围后补兼容版本和发布版本信息。

## 推荐执行顺序

以最终 HTML 的路线图为准，简要顺序：

1. 为当前 JSON/业务结果补特征测试，固定现有行为。
2. 删除 AnkiConnect 全路径。
3. 建立 Anki data module，并把 FSRS 私有入口隔离到 compatibility adapter。
4. 迁移到 `AnkiWebView + QueryOp`，删除 HTTP server 和 Event 同步。
5. 深化容量决定与卡片审计；Python 输出可直接展示的结果，JS 不再重算业务规则。
6. 补 manifest 兼容信息，并在最低支持版与最新稳定版 Anki 做烟雾测试。

## 当前代码热点

- `addon/__init__.py`
  - `on_main()`：29–46
  - `direct_anki()`：49–118
  - server 生命周期：121–163
  - `simulator_request()`：178–212
  - AnkiConnect 桥：215–258
  - import 时 QAction/hooks：261–270
- `addon/attention_dashboard.py`
  - AnkiConnect/caller 双通道：21–52
  - 审计函数：59–189
  - 容量预测：226–308
  - `build_dashboard()`：311–541
  - HTTP Handler/server：544–602
- `addon/attention_dashboard.html`
  - 页面请求和业务二次判断：约 290–507
- `tests/test_attention_dashboard.py`
  - 当前只有 3 个纯函数测试。

## 验证状态

最近验证：

```text
python3 -m unittest discover -s tests -v
Ran 3 tests — OK
```

当前 Git 状态只有原有未跟踪项：

```text
?? .agents/
?? skills-lock.json
```

架构审计过程中未修改仓库业务代码。

## Superpowers 卸载状态

已经删除整个 Superpowers 插件，而非单独删除 `writing-plans`：

- 插件运行缓存已删除；
- `config.toml` 中的启用记录已清除；
- `codex plugin list` 显示 `superpowers` 为 `not installed`。

当前会话上下文可能仍残留旧技能文字，但新会话不应再加载它。

## 官方参考

- Anki add-on 入门：`https://addon-docs.ankiweb.net/`
- 后台操作、`QueryOp`/`CollectionOp`：`https://addon-docs.ankiweb.net/background-ops.html`
- hooks、WebView bridge、web exports：`https://addon-docs.ankiweb.net/hooks-and-filters.html`
- Collection 与数据库访问：`https://addon-docs.ankiweb.net/the-anki-module.html`
- Anki 内部架构：`https://docs.ankiweb.net/developers/architecture.html`
- 打包与 manifest：`https://addon-docs.ankiweb.net/sharing.html`

## Suggested skills

下一位 agent 建议按需使用：

- **`improve-codebase-architecture`**：仅在上述改造实施完成后复审，不要现在重复执行。
- **`superset-browser`**：需要打开、截图或验证 HTML/WebView UI 时使用。
- **`i-have-adhd` 插件能力**：如果新会话可调用，使用其低认知负担表达方式；若没有可调用 skill，也应手动遵循“短段落、一次一件事、结论先行、图优先”。
- **不要使用任何 `superpowers:*` skill**：整个插件已按用户要求卸载。

## 下一会话建议起点

若用户要求开始实施，先做路线图第 0–1 阶段：

1. 补固定现状行为的测试；
2. 删除 AnkiConnect；
3. 运行测试和打包验证；
4. 用极简中文汇报改动，不要一次展开后续所有阶段。
