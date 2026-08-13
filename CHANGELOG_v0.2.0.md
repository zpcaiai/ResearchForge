# Changelog

## v0.2.0 — 2026-08-11

### 修复：I/O 契约（v0.1.0 的三个结构性 ERROR 全部清零）

v0.1.0 的 62 个 skill 之间没有机器可校验的接口。修法不是补文档，是把 I/O 变成契约。

- **新增 `manifests/artifact-graph.json`** —— 164 个 artifact 的唯一权威来源。每个 artifact 有且只有一个生产者；每个输入要么是 artifact id，要么显式标记 `external:`，要么是声明的 `feedback:` 回读。
- **`depends_on` 改为从数据流推导**，不再手工维护。v0.1.0 里 orchestrator 的散文点名了 20 个 skill 而 `depends_on` 只列了 7 个 —— 这类不一致现在由 validator 强制。
- **137 个悬空输入 → 0**。全部解析为 artifact id 或 63 个显式外部输入。
- **30/66 不可达 → 0**。并新增第二个入口点 `skill-evolution-manager`（离线维护回路，本就不该从科研 run 可达 —— 这是如实建模，不是绕过检查）。
- **6/7 schema 悬空 → 0**。9 个 schema 全部绑定到 artifact。
- **新增「构建序依赖」与「运行时反馈边」的区分**。实验树搜索回读累积 findings、完整性门反馈给审稿模拟 —— 这些是真实的迭代回路。它们被显式声明为 `feedback:`，参与校验但不参与拓扑排序，因此构建图保持无环而设计保持诚实。共 9 条。
- **62/66 缺触发语义的 description 全部重写**为 "Use when…" 形式。这不是文字工作：description 是 agent 唯一的路由依据，缺了它 skill 会静默地不被调用。
- **`tests/validate_package.py` 重写**。v0.1.0 的 `procedures sufficiently specific` 实际只检测「是否有 7 个以上文件逐字相同」。新版校验单一生产者、悬空输入、depends_on 同步、构建图无环、入口点可达性、feedback 边有效性、schema 绑定，**并在结尾明确列出它没有检查什么**。

### 新增：复现前置于创新

- **状态机改动**：`EVIDENCE_EXPANDED → SOURCE_REPRO_ATTEMPTED → REPRO_LEVEL_ESTABLISHED → IDEAS_READY`。原论文复现现在发生在任何 idea 生成之前。理由：基于跑不起来的代码库估出的 feasibility 和 delta 不是估计。
- **`source-result-reproducer`（新）** —— 复现**原论文自身**的主结果，分级 RL0–RL4，固定失败码词表，强制成对的「报告值 vs 实测值」记录，运行时强制时间盒。与 `baseline-reproduction-auditor` 区分：后者复现的是选定方向的**对比 baseline**，发生在更后面。
- **`reproduction-fallback-planner`（新）** —— **缺失的降级路径**。把 RL 映射为 `comparison_mode`（CM_MEASURED / CM_RELATIVE / CM_REPORTED / CM_NONE）和 `idea_mode_constraints`。**RL0 不再是终止态**：系统被重定向到不需要可用 baseline 的贡献类型（诊断、评测、可复现性研究），复现失败本身成为发现。
- 新增 schema `ReproductionLevel`、`ComparisonMode`。
- `comparison_mode` 作为硬约束对象贯穿 idea 生成、feasibility、实验规格、引用审计与发布门。

### 新增：文献 API 配额与覆盖率

- **`literature-provider-manager`（新）** —— provider 注册（文档速率 vs 实测速率）、配额账本、覆盖率测量（seeded recall / 饱和度 / 跨 provider 一致性）、具名盲区（无全文、无非英语、无近 90 天、无代码检索）、配额耗尽时的声明式降级顺序。
- `UNKNOWN_COVERAGE` 下 `novelty-verifier` 不得断言 `NOVEL_ENOUGH`。**「没找到先前工作」和「没能力找」不再长得一样。**

### 新增：系统自评基准

- **`retrospective-benchmark-builder`（新）** —— 用过去某封闭时间窗的种子论文，配对其后真实发表的后续工作，以 recall@k 评分创新引擎。强制标注**污染下限**（被测模型很可能读过那些后续论文）。附带盲评 rubric —— 回溯基准廉价地捕捉回归，盲评才能确立产出是否有价值。
- Batch 13 的 DoD 从「1 篇 fixture」改为**跨 RL3/RL1/RL0 的 3 篇**，其中 RL0 那篇必须产出诊断/评测类贡献而非停机。

### 新增：可执行的研究协议

- **`REPRO_STUDY_PROTOCOL.md`** —— 20 篇复现研究，含抽样框（**禁止手工挑选**）、分层配额、双臂设计（agent 4h / 人类 8h 天花板）、步骤 0 方差探测（容差必须实测，不能硬编码 ±5%）、冻结的决策规则、以及 n=20 的置信区间诚实性说明。
- **`tools/sample_frame.py`** —— 冻结种子的分层随机抽样。
- **`repro_study_tracker.xlsx`** —— 6 个工作表，263 条公式，Wilson 95% 区间，决策规则自动判定，失败码→工程含义映射。公式已用测试数据集验证。

### 已知未修复

- **58/66 个 skill 的非样板规格仍不足 120 词。** 这在 validator 里保留为 WARN，不是 ERROR。v0.1.0 的规格密度问题是真实的，本次只加厚了 4 个新增的高风险 skill（各约 900 词）。其余需要在对应 batch 编码时逐个加厚 —— 现在补文字只是把问题藏起来。
- 62 → 24 的精简建议未执行。本次只修契约，不动结构；合并是需要你决策的独立议题。
