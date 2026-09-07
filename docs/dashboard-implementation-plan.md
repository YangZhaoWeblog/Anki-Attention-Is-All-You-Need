# 注意力看板正式接入方案

状态：第二轮复审的 3 项 P2 已修复，待再次独立复审。实现与验收日期：2026-09-07。

方案已完成设计审查与正式接入。首轮实现 review 的 2 项 P1、9 项 P2，以及第二轮复审新增的 3 项 P2，均已按问题清单补回归并修复；尚未取得再次独立复审结论。

## 1. 目标与当前基线

将已确认的棱光白交互原型接入真实 Anki 数据，完成“看负担 → 找卡片 → 看证据 → 编辑或退出”的操作闭环。保留现有 **AnkiWebView + QueryOp + pycmd** 架构；写操作新增原生 **CollectionOp**。不恢复 AnkiConnect、自建 HTTP 服务或独立卡片状态库。

产品判断以 Vault 的 **Attention Is All You Need.md** 为准；交互参考为 [可点击原型](../work/visualizations/anki-ui-prototype.html)。本方案负责模块、数据、接口和验收，不复制产品讨论。2026-09-04 的 [架构交接](handoff-anki-architecture-20260904.md)作为历史材料保留，其行号与测试数量不代表当前版本。

本次检查基于 **main** 的工作区，HEAD 为 **e8f8556c8fc1865328f2f10b8f00f05d7b5f07d2**。工作区已有未提交的架构与业务修改；后续必须以当前文件为基线，保留这些修改，不通过重置恢复旧提交。

| 对象 | 当前代码事实 | 本次变化 |
| --- | --- | --- |
| 窗口与通信 | [dashboard_window.py](../addon/dashboard_window.py)只处理读取命令 | 补初始化、详情、设置与明确的写操作 |
| 复习证据 | [summarize_reviews](../addon/attention_dashboard.py#L88)先丢弃 90 天前记录；慢与 Again 次数取整个 90 天 | 分开计算近 30 天总耗时、跨日期的最近 5 次表现 |
| 候选来源 | [build_dashboard](../addon/attention_dashboard.py#L329)从最近 90 天复习过且未暂停的卡片开始 | 覆盖所选范围的长间隔卡；“值得检查”按反复表现入池，待优化单独按原生标签读取 |
| 已开始学习数量 | [new_today / new_7](../addon/attention_dashboard.py#L409)使用创建时间查询 | 改为第一次有效学习事件，导入卡片不算开始学习 |
| 卡片内容 | [card_infos](../addon/data.py#L62)有字段和标签，未提供完整详情与笔记影响范围 | 增加原生 ID、渲染内容、原始字段、启停状态与同笔记卡片数量 |
| 页面与设置 | 正式页面仍是旧布局；预算来自 localStorage，牌组为页面选择 | 接入选定 UI；牌组与预算由插件配置恢复 |

**当前 51 项 unittest 全部通过。** 这是旧行为基线，不代表本方案的新增规则和真实写操作已通过。

## 2. 数据与配置

**Python 负责业务判断；HTML 负责呈现、筛选交互和编辑中的临时输入。** 不把原型内的卡片样例、示例预算或演示判定函数带入正式数据链路。

| 结构 | 最小字段与语义 |
| --- | --- |
| 牌组引用 | **deckId、name、parentId、descendantCount**；持久选择使用 ID，显示使用当前名称；父牌组包含全部子牌组 |
| 基本设置 | 插件配置新增 **settingsByProfile[profileName] = {deckId,dailyBudgetMinutes}**；沿用现有模拟天数配置；不存选择历史 |
| 复习证据 | **reviewId、reviewedAt、durationMs、rating、reviewType**；过滤无效记录后，最近 5 次按时间取样；样本不足时使用实际分母 |
| 候选行 | **cardId、noteId、deckId、summary、thumbnail、tags、suspended、recent30Minutes、recentReviews、reasons**；列表元数据与详情分开加载 |
| 笔记详情 | **cardId、noteId、fields[{name,value}]、questionHtml、answerHtml、tags、suspended、noteCardCount**；字段值与渲染题面不是同一个数据源 |
| 预算结果 | **deckId、periodStart、periodEnd、activeDays、forecastMinutes、budgetMinutes、headroomMinutes、source、newAllowance**；首次学习额度未知时为明确未知状态 |

**首次范围与配置恢复。** 有效的上次选择优先；没有上次选择时读取 Anki 当前牌组。改名依 ID 跟随；已保存 ID 不存在时返回需要重选的状态，停止给该范围的预算建议。不得把失效选择转换为全库范围。旧 **managedDecks** 配置保留兼容读取，但不再硬编码为新用户首次选择；未迁移字段和未知配置键不覆盖。

在 **AttentionData / AnkiData** 中新增 **deck_refs()、current_deck_id()、resolve_deck(deckId)**：分别基于 **all_names_and_ids()**、**col.decks.selected()** 和 **name_if_exists()**；本次当前牌组读取统一使用 **selected()**，不由各调用方自行选择 API。父子关系使用 Anki 的牌组树/子牌组接口。**initialize** 返回 **selectedDeckId、selectionRequired、deckRefs、dailyBudgetMinutes**。请求中的 ID 每次解析为当前名称，再复用已有搜索转义函数与 Anki 原生父牌组搜索语义。首次升级没有 **settingsByProfile** 时，预算取原插件配置默认值，牌组取当前选中牌组，不将旧 **managedDecks:["ALL"]** 当作用户明确选择。原字段保留但不参与已选 deck 的计算。

正式页面删除 **attentionDailyBudgetMinutes** 的 localStorage 读写及 URL 预算覆盖入口。设置改动先提交 **saveSettings**，收到成功响应后更新已确认选择并加载对应范围；失败恢复上次成功的控件值，不启动新范围预测。设置提交串行，期间仅禁用设置控件。配置写入使用 **addonManager.writeConfig** 保留其他键；页面缓存只允许列表折叠等 UI 偏好，不镜像一份持久预算。

**候选计算。** 所选范围的未暂停卡按反复慢、反复 Again、被表现证据验证的结构疑点形成“值得检查”；不能先用 **rated:90** 限制取样。近 30 天累计耗时只用于排序和展示，不单独让卡片入池。第一轮接入的入池阈值为：最近最多 5 次有效复习中至少 2 次达到 40 秒；或最近最多 5 次中至少 2 次 Again，且这些 Again 跨至少 2 个复习日；如果最近最多 5 次中达到 3 次 Again，即使不跨天也入池；或结构疑点同时伴随慢、Again 或单次明显耗时证据。阈值集中在业务层，标记为启发式初始规则，不增加阈值设置面板。结构疑点复用现有检测器并逐项核对；图片无法分析不能作为坏卡结论。

具体取数：以所选范围的全部未暂停卡 ID 为起点，移除 **rated:90** 前置条件；使用 **card_infos** 取得结构检查所需字段，并扩展 **reviews_of_cards** 返回 **revlog.type**。**summarize_reviews / CardReview** 分离跨全部日期的最近 5 次有效回答与 30/90 天累计量；回答有效性沿用评分与正耗时过滤。保留最近记录的时间和评分，分别计算 **recentSlowCount、recentAgainCount、recentAgainDayCount**；即使没有有效复习记录，也为结构疑点判断保留该卡片信息，但结构疑点不能脱离表现证据单独入池。取数分批执行，不按显示前 20 张裁剪计算输入。

事件映射为 **reviewId ← id、reviewedAt ← id / 1000（Unix 秒）、durationMs ← time、rating ← ease、reviewType ← type**。显示日期使用当前时区转换，不把格式化日期用于事件排序；首次学习计数直接使用下述原生查询，不复用正耗时过滤。

**两个工作入口。** “值得检查”排除暂停、**看板忽略**和**需优化卡片**；待优化由**需优化卡片**决定，即使同时暂停或带忽略标签也显示。候选池按优先级排列，不拆出额外的“今日优先”队列。概览只展示少量候选；候选总数、当前筛选数和当前显示数分别返回，不把截取前 20 张当成全部候选。详情按需读取，避免将所有图片编码进列表响应。

查询与写入闭环：“值得检查”基础查询附加 **-is:suspended -tag:看板忽略 -tag:需优化卡片**，再应用候选资格；待优化基础查询只附加 **tag:需优化卡片**，不附加暂停或忽略排除。两者独立构建结果。**setHidden** 只增删目标卡所属笔记的 **看板忽略**，不修改启停；**setOptimization** 增删 **需优化卡片**，启用时仅暂停当前卡，关闭时不恢复复习。后端重新查询同笔记全部卡片 ID 作为影响范围；刷新后重新计算两个入口的数量。复习负担计算不使用这两个标签的候选排除条件。

**首次学习与预测。** 用所选范围内的 Anki 原生 **introduced:1 / introduced:7** 替换 **added:1 / added:7**，查询只组合牌组范围与 **introduced:n**，明确移除旧的 **-is:suspended**，也不附加候选标签排除。按卡片 ID 计数，不因后来暂停或隐藏而抹掉已发生的首次学习，也不从耗时是否大于零反推是否开始学习。首次回答判定和学习日边界委托 Anki；导入旧日志、reset 与已删除历史的结果沿用原生查询，不另建首次学习记录。测试覆盖只导入未回答、首次回答 5 张、重复回答、旧历史导入与学习日切换，使用原生搜索结果作集成对照。[Anki First Answered 查询](https://docs.ankiweb.net/searching.html#first-answered)

当前主预测已经按所选牌组执行，继续复用 FSRS 适配器；合并首页重复的预测表达，主值只有一个来源，排程估算只作为明确标注的降级依据。不要把“未来新增额度”减去口径不一致的历史创建数。

## 3. 页面、接口与写入边界

**窗口模块负责 Qt、生命周期和请求分发；数据模块封装 Anki 读取；纯业务层组织指标；新增小型操作模块封装写入。** FSRS 私有接口继续只留在 **fsrs_adapter.py**，不借 UI 改造扩散兼容代码。

以下为拟定的内部桥接契约，不是已经存在的 API。每个请求携带 **requestId**；页面加载和范围变更另携带 **generation**。响应回送同一标识，旧范围响应不覆盖新页面。窗口关闭或配置档切换后不向旧 WebView 回写。

| 命令 | 请求 | 成功响应 / 执行位置 |
| --- | --- | --- |
| **initialize** | 无业务参数 | **selectedDeckId、selectionRequired、deckRefs、dailyBudgetMinutes** |
| **loadDashboard** | **deckId、dailyBudgetMinutes** | 预算结果、候选元数据、待优化计数；**QueryOp** |
| **loadCard** | **cardId** | 当前卡片详情与该笔记完整影响范围；**QueryOp** |
| **saveSettings** | **deckId、dailyBudgetMinutes** | 校验后的设置；通过插件配置 API 在主线程保存 |
| **saveNote** | **noteId、changedFields、tags** | 实际受影响笔记/卡片 ID；**CollectionOp** 调用原生 note 更新 |
| **setSuspended** | **cardId、suspended** | 实际启停状态；原生 suspend / unsuspend 操作 |
| **setOptimization / setHidden** | **cardId、enabled** | 标签修改影响整条笔记；标记待优化时同时暂停当前卡片，移除标签不自动恢复 |
| **prepareDelete / deleteNote** | **cardId** / **noteId、confirmedCardIds** | 先返回完整影响卡片列表与数量，确认后按笔记删除；实际删除前复核范围 |

失败返回 **requestId、generation、error{code,message}**，不得先从页面移除卡片再假定写入成功。写操作在执行中禁止重复提交；成功后重新读取实际数据、重算所选范围、保留当前工作入口与排序，并按已有规则选择剩余候选。无候选时仍能看到结果反馈。

**编辑保存边界。** 编辑器展示并保留真实字段中的富文本、图片引用、cloze 标记和回链；保存只提交用户修改的原始字段，不能将摘要、去 HTML 后的题面或渲染答案写回笔记。取消不写入；页面内未保存判断只比较本次载入值和当前输入。首次实现先用含图片、回链、多字段、cloze 的测试笔记验证“未改动字段原样保留”和显式保存，再接通批量使用。这里不新增拆卡、字段结构修改或完整排版工具栏。

Anki 原生 Editor 不能直接套成当前的“保存／放弃”表单：已核对的原生编辑路径会在字段变化时保存。目标仍采用页面内字段编辑与显式 **saveNote**，不通过备份后回滚来伪装取消，也不直接复用会自动写入的编辑器模式。[原生 Editor 行为](https://github.com/ankitects/anki/blob/main/qt/aqt/editor_legacy.py#L407-L428)

**显示与回链。** 题面/答案使用 Anki 模板渲染结果，媒体复用 Anki 自带服务。卡片内容与管理控件隔离，卡片脚本不能直接取得管理用写入桥；链接经用户点击交给系统打开，保留 Obsidian、MarginNote 原始 URL，不引入“登记来源”流程。自定义脚本模板的支持边界需要在目标 Anki 版本实测；不以纯文本降级后仍显示“完整内容”掩盖差异。

**封面与缩略图。** 缩略图位用于识别卡片，而不是证明卡片包含图片。有图片时展示真实媒体，默认使用 **contain**，避免裁掉图表、截图或知识结构；无图但有可读题面时生成文字预览卡；无图且无可读内容时使用棱光白体系下的 **叠卡折光默认封面**。默认封面不显示 **Aa**、灰块、纯色块或“预览不可用”文案；卡片正文区域仍展示可获得的牌组、标签、标题兜底、耗时证据和处理入口。标题兜底按笔记类型、牌组、字段名和卡片 ID 组合，不伪造题面；默认封面只用于总览候选卡片和详情顶部等封面区域，不放入左侧候选列表，也不作为结构疑点或质量诊断证据。

**原生操作。** note 字段与标签通过原生 **update_note(s)**，暂停与恢复通过原生 scheduler 操作，删除通过 **remove_notes**；由 **CollectionOp** 组织集合写入和界面更新。组合的“暂停并标记待优化”要作为一个用户动作处理，失败时重新读取真实状态，不声称全部完成。是否可作为一条原生撤销记录，需在实际版本中验证；不增加插件自己的撤销历史。[后台与写操作](https://addon-docs.ankiweb.net/background-ops.html) · [原生 note 操作](https://github.com/ankitects/anki/blob/main/qt/aqt/operations/note.py)

## 4. 实现顺序与验收

### 第一步：范围、证据与配置

修改 **data.py、attention_dashboard.py** 及对应测试；窗口补初始化和设置读写。固定 deck ID、跨日期最近记录、首次学习与两个队列的口径。此步保留现有 FSRS 算法边界，不增加每日积压顺延模拟。

验收：长间隔且近期无记录的反复慢卡能出现；2 次达到 40 秒进入值得检查；近 30 天累计耗时不单独入池；结构疑点没有表现证据时不入池；2 次 Again 只有跨至少 2 个复习日才单独入池；3 次 Again 即使不跨天也入池；样本分母正确；父子牌组范围一致；改名能恢复选择、已删除牌组要求重选；标签优先级符合产品文档；导入 100 张而首次学习 5 张时统计为 5。

### 第二步：正式页面与内容读取

重写 **attention_dashboard.html** 的呈现，沿用选定的棱光白、两栏/专注阅读、无框分隔线把手、两侧轮播、叠卡折光默认封面与处理菜单。按需拆分静态资源，并保持同一窗口承载。预算、证据、候选数和详情全部来自桥接响应；删除原型样例、示例预算和前端重复业务判定。

验收：图片卡、文字卡和无可读内容卡都能认出；无可读内容卡不出现 **Aa**、灰块、纯色块或“预览不可用”文案；兜底封面不改变候选原因；左侧列表仍保持文字扫读；完整答案、字段与回链可用；切换牌组时旧响应不覆盖新数据；菜单、焦点、长卡滚动、编辑防误切与减少动态效果均检查。富文本字段编辑先通过前述样例验证，再开放真实保存。

### 第三步：写操作与真实结果

新增 **dashboard_actions.py**，扩展窗口命令白名单、错误处理与成功后的刷新。接通保存、启停、标签、删除；配置读写与卡片写入分开处理，不新增治理数据库、逐卡完成状态或黑名单。

验收必须使用隔离的 Anki 测试配置档：同笔记多卡标签联动、跨牌组删除范围、取消不写入、写失败保留输入、保存与刷新一致、连续点击不重复执行、关闭窗口/切换配置档没有迟到回调写入。再检查原生撤销的实际效果。用户现有集合不作为破坏性测试环境。

## 5. 验证记录与完成条件

2026-09-07 已完成正式实现与隔离验收。验证范围与边界如下：

| 验证维度 | 环境与结果 | 结论 |
| --- | --- | --- |
| 纯业务、数据、配置与操作测试 | 系统 Python 运行 unittest；全部通过，Anki 专用用例按环境跳过 | 新旧规则回归通过 |
| 原生集合操作 | Anki 26.08.1 Python 包，临时 collection；首次学习、富文本保存、标签、启停、删除通过 | 未接触用户集合 |
| 真实 WebView 闭环 | Anki 26.08.1，临时 base/profile，offscreen WebView；窗口、详情、渲染、pycmd、CollectionOp、隐藏后刷新通过 | 插件真实运行链路通过 |
| Review 回归 | 隔离 WebView 覆盖草稿身份、保存并继续、部分成功回读、迟到详情、CSS、概览跳转、图片限制、影响范围、日期、空筛选、菜单、折叠、箭头、独立列表滚动及 iframe 就绪等待 | 完整回归通过；**100vh + padding** 连续 8 次高度均为 170px |
| 处理后选卡 | 独立 A–D 队列隐藏中间卡 B | 标签真实写入，刷新后继续选择 C |
| 打包 | **scripts/package.sh**，解包目录与当前 **addon/** 逐字节比较 | 11 个文件一致；**work/**、测试和原型数据未进入包 |

```bash
python3 -m unittest discover -s tests -v
PYTHONPATH="/Applications/Anki.app/Contents/Resources/app_packages" python3.13 -m unittest tests.test_anki_integration -v
PYTHONPATH="/Applications/Anki.app/Contents/Resources/app_packages" QT_QPA_PLATFORM=offscreen python3.13 scripts/anki_gui_smoke.py
PYTHONPATH="/Applications/Anki.app/Contents/Resources/app_packages" QT_QPA_PLATFORM=offscreen python3.13 scripts/anki_gui_regressions.py
PYTHONPATH="/Applications/Anki.app/Contents/Resources/app_packages" QT_QPA_PLATFORM=offscreen python3.13 scripts/anki_gui_queue_regression.py
./scripts/package.sh
```

仍待独立复审与最低支持版本 Anki 24.04 的兼容性运行。自定义脚本卡片模板未声明为已验证；正式内容 iframe 禁止脚本取得管理桥，普通文字、图片、CSS 与外部回链按当前 Anki 媒体服务加载。CUA 原生管道不可用，因此本轮 WebView 结论来自隔离 Anki 的自动 DOM、几何与集合回读，不包含人工截图复验。
