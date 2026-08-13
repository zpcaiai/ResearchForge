# ResearchForge Skills v0.1.0 — 严格评审

评审日期：2026-08-11
评审对象：`researchforge-skills-v0.1.0.zip`（62 个 SKILL.md / 7 个 schema / 1 个校验脚本 / 4 份说明文档）
评审方法：全量读取 + 程序化依赖图审计 + 上游仓库抽样核实 + 校验脚本复跑

---

## 0. 一句话结论

**这是一份质量不错的「设计意图文档」，但它把自己包装成了「规格包」，而它离规格还差一层。**

真正的问题不是"62 个太多"，而是：**62 个 skill 之间没有任何机器可校验的接口契约**。
它们看起来是一个系统，实际上是 62 张独立的便利贴，靠散文互相指认。

正面的地方要说清楚：**上游 34 个仓库我抽查了 6 个，全部真实存在，SOURCE_MAP 的功能描述准确无误**（SkillOpt 15.8k★、DeepScientist 3.2k★、FAROS 728★、PaperSpine 28★、SciAgentGYM 26★、EurekAgent 19★）。
架构原则（portfolio before commitment / baseline before improvement / evidence graph as backbone / grader 隔离）是**对的**，而且比市面上多数"自动科研"方案清醒。这份包最有价值的部分是 `Hard gates` 和 `Verification / tests` 两节——那里有真东西。

---

## 1. 客观测量

| 指标 | 数值 | 含义 |
|---|---|---|
| SKILL.md 总词数 | 19,893 | — |
| **其中模板样板文字** | **13,685（69%）** | 每个文件有 8 行逐字相同的样板 |
| 每个 skill 的**独有内容**中位数 | **103 词** | 约等于一段话 |
| 独有内容最少的 skill | vector-figure-reconstructor（61 词） | — |
| Procedure 步骤数 | 3–5 步（除 orchestrator 9 步） | — |
| 声明的输出产物种类 | **152 种** | — |
| **无法对应到任何输出的输入字符串** | **137 个（占输入总数约 90%）** | I/O 完全不成契约 |
| 从 orchestrator 可达的 skill | **32 / 62** | 30 个挂在图外 |
| 从未被任何 skill 依赖的 skill | **26** | — |
| `depends_on` 为空的 skill | 33 | — |
| 依赖环 | 0 | 唯一干净的地方 |
| 引用了 `*.schema.json` 的 skill | **0 / 62** | 7 个 schema 悬空 |
| description 含"何时触发"语义的 skill | **0 / 62** | 见 §2.5 |

---

## 2. 五个结构性问题（按严重度排序）

### 2.1 【致命】I/O 层是散文，不是契约

`idea-portfolio-generator` 的输出写作 `idea_portfolio.json`；
`idea-ranker` 的输入写作 `idea portfolio`。

同一个东西，两个名字，**没有任何东西能发现这个不匹配**。全包 152 个输出产物名 vs 137 个悬空输入名，覆盖率约 10%。

后果很具体：当你让 Codex 按 IMPLEMENTATION_PLAN 分 13 个 batch 编码时，Batch 05 实现的 `idea_portfolio.json` 字段结构，和 Batch 05 后半段实现的 ranker 读取的结构，**没有任何东西保证它们一致**。你会在 Batch 13 的 E2E 里才发现，那时已经有一万行代码建立在错误假设上。

修法：把 `Inputs`/`Outputs` 从 markdown 列表改成引用 schema id 的结构化字段，写一条校验规则「每个 input 必须是某个 skill 的 output 或标记为 `external`」。这条规则今天就能加进 `validate_package.py`，30 行代码。

### 2.2 【致命】依赖图是装饰性的

`researchforge-orchestrator` 的 `depends_on` 只列了 7 个 skill，但它的 Procedure 散文里点名了 20 多个（experiment-tree-search、claim-citation-auditor、manuscript-spine-builder…）。**机器可读的图和人类可读的散文互相矛盾**。

结果：30/62 的 skill 从入口不可达，包括 `claim-citation-auditor`、`sandbox-provisioner`、`experiment-spec-author` 这些你架构里明确的核心组件。任何试图从 catalog 自动生成执行计划的代码，都会漏掉一半系统。

### 2.3 【严重】VALIDATION_REPORT 的措辞比它验证的内容强

脚本输出的这句话：

> `OK: 62 skills, 7 schemas, dependency references valid, procedures sufficiently specific.`

逐条拆开：

- `dependency references valid` = 「depends_on 里的名字都能在 catalog 里找到」。**不检查环、不检查可达性、不检查图和散文是否一致**。
- `procedures sufficiently specific` = 「Procedure 段落的去重后数量 ≥ 总数的 90%」。**它只能检测"62 个文件里有 7 个以上完全逐字相同"**。把所有 Procedure 换成 62 段不同的胡言乱语，这个检查照样通过。
- `7 schemas` = 「7 个 json 文件能被 `json.loads` 解析」。**不检查是否符合 JSON Schema 规范，不检查有没有 skill 用到它们**（答案是 0 个）。

这份包的自我批评意识很强——README 和每个 SKILL.md 都反复声明"这不是运行时实现"，这点值得肯定。但 `procedures sufficiently specific` 这句话属于同类问题的另一种形式：**用一个通过了的弱检查，说出一个强结论**。建议直接改成 `structure OK: sections present, dep names resolve, schemas parse. NOT checked: reachability, I/O linkage, schema usage, semantic quality.`

### 2.4 【严重】69% 是样板，103 词撑不起它承诺的能力

举一个具体的：

```
baseline-reproduction-auditor  —  128 词独有内容
Procedure: 5 步
Hard gate: "非复现的 baseline 不得用于比较性 claim"
```

复现一篇 2024 年 ML 论文的仓库，是这条流水线上**最难、最容易永久卡死**的一环：CUDA 版本、消失的预训练权重、没写在论文里的预处理、需要申请的数据集、作者删库。人类熟手要花几天，成功率不到一半。

128 个词、5 个步骤，描述的是"应该做什么"，不是"怎么做"。而 Hard gate 的存在意味着：**一旦复现失败，整个系统就永久停在 BASELINE_REPRODUCED 之前**。设计里没有任何降级路径（比如：接受"引用论文报告数值 + 标注未复现"的弱比较模式、或允许 proxy baseline）。

这不是"再写详细一点"能解决的。这是**规格没有触及真实难点**。

### 2.5 【中等】62 个 description 全部不含触发语义

Skill 的 `description` 在 Claude Code / Codex 里的作用是**让 agent 判断什么时候该调用它**。当前 62 个 description 全部是"这个 skill 做什么"的陈述句：

> "Design ablations, counterfactuals and sensitivity analyses that test whether…"

没有一个包含 "Use when…" / "Trigger when the user…"。**0/62**。

实际后果：62 个 description 加起来约 2,100 token 常驻上下文，而 agent 在该调用 `assumption-weakness-miner` 的时刻，很可能什么都不调用，或者调用错的那个。这是 skill 系统最常见的失效模式，而且它不会报错——它只是静默地不工作。

---

## 3. 62 → 24：具体的合并方案

不是为了少而少。合并标准是：**两个 skill 如果永远在同一次调用里连续执行、且中间产物没有独立消费者，它们就是一个 skill 的两个步骤。**

| 合并后 | 吸收原有 | 理由 |
|---|---|---|
| `paper-ingest` | + paper-html-visual-reader | 视觉读取是 PDF 解析失败时的 fallback 分支，不是独立能力 |
| `paper-model-builder` | paper-structure-parser + contribution-decomposer | 都在产出 PaperModel 的不同字段 |
| `claim-evidence-graph` | paper-claim-evidence-graph（保留） | — |
| `literature-search` | + paper-library-connector + research-landscape-builder | Zotero 连接是一个 provider，landscape 是一次查询的渲染 |
| `citation-resolver` | + citation-neighborhood-miner | 同一套 scholarly API 客户端 |
| `baseline-finder` | baseline-repo-finder（保留） | — |
| `baseline-reproducer` | baseline-reproduction-auditor（保留，**但必须大幅加厚**，见 §4.2） |
| `idea-seed-miner` | assumption-weakness-miner + novelty-gap-miner + cross-domain-analogy-miner | 三种 mining mode，同一个输出格式（seed），应做成一个 skill 的三个 mode |
| `idea-portfolio-generator` | + genetic-idea-mutator | mutation 是 portfolio 生成的一种策略 |
| `idea-evaluator` | novelty-verifier + feasibility-estimator | 两份报告永远一起被 ranker 消费 |
| `idea-ranker` | 保留 | 排序逻辑独立且可测，值得单独存在 |
| `user-feedback-gate` | 保留 | **这是全包最有价值的 skill 之一**，别动 |
| `research-blueprint-compiler` | + experiment-spec-author + ablation-and-counterfactual-planner | 都是把 idea 编译成可执行计划 |
| `evaluator-builder` | metric-and-hidden-evaluator-builder（保留） | grader 隔离是核心，独立 |
| `sandbox-provisioner` | 保留 | 独立且有明确边界 |
| `codebase-scaffolder` | + debug-and-repair | debug 是 scaffold 后的修复循环 |
| `experiment-runner` | experiment-tree-search + experiment-ledger + artifact-provenance | ledger 和 provenance 是同一个 append-only 事件流的两个视图 |
| `data-analyst` | data-prep-agent + data-analysis-agent | — |
| `integrity-auditor` | statistical-integrity-auditor + result-meta-analyzer | — |
| `finding-memory` | finding-memory-manager + negative-result-curator | 负结果是 findings 的一个 tag |
| `manuscript-builder` | manuscript-spine-builder + paper-drafter | — |
| `claim-citation-auditor` | + paper-quality-integrity-gate | **这是全包最有价值的 skill，别动它的核心逻辑** |
| `review-simulator` | journal-fit-reviewer + rebuttal-and-revision-planner | — |
| `figure-factory` | figure-storyboard + scientific-figure-generator + editable-svg-refiner + vector-figure-reconstructor | 后两者是同一件事（栅格/规格 → 可编辑 SVG） |
| `deck-factory` | defense-ppt-storyline + paper-to-ppt-evidence-mapper + defense-ppt-generator | evidence-mapper 是 storyline 的一步，且当前是孤儿节点 |
| `release-gate` | release-gate-exporter（保留） | — |
| `orchestrator` | + research-progress-controller | 预算/断点续跑属于状态机本体 |

**建议直接从 v1 删掉（不是合并，是删）：**

| skill | 理由 |
|---|---|
| `multi-agent-research-team` | 69 词，孤儿节点，不可达。多 agent 分工是实现细节，不是 skill |
| `tool-composition-planner` | 62 词，孤儿。与 tool-skill-router 职责重叠且更空 |
| `tool-skill-router` / `model-provider-router` / `domain-skill-loader` | 三个"选择正确后端"的 skill。在只有一个后端的 v1 里，这是纯粹的过早抽象。等到真有第二个 provider 再写 |
| `skill-evolution-manager` | SkillOpt 的思路是对的，但**这是在优化一个还不存在的系统**。没有 100 次真实 run 的 trace，held-out 集从哪来？v2 再说 |
| `skill-package-auditor` | 元层的元层 |
| `research-eval-harness` | 概念正确但没有定义 benchmark。见 §4.4 |

**24 个核心 + 6 个删除 + 若干合并 = 从 62 降到 24。**

---

## 4. 工程现实检查：真正会杀死这个项目的四件事

这一节比上面所有内容都重要。上面是"包做得不够好"，这一节是"这个东西可能做不出来"。

### 4.1 域没有被限定

"给一个论文 URL，产出一篇论文" —— 跨全部学科这是不可能的。生物实验要湿实验室，理论数学不需要跑代码，临床要 IRB。

包里从头到尾**没有一句话说明目标学科**。但所有设计（baseline repo、GPU hours、benchmark、seed）都隐含假设了 **CS/ML 领域的、benchmark-driven 的、代码可跑的论文**。

**必须做的第一件事：把 v1 域写死。** 我的建议：
> 单卡 24GB 以内、单次实验 < 1 GPU-hour、有公开 benchmark、有官方 GitHub 仓库的 ML/NLP 论文。

写死之后，`feasibility-estimator` 才有意义，`--gpu-hours 20` 才不是幻想。

### 4.2 Baseline 复现是唯一的真实瓶颈

见 §2.4。**建议把整个 Batch 04 单独拎出来先做，而且不要按 skill 写，按数据写：**

拿 20 篇目标域内的论文，人工尝试复现它们的官方仓库，记录：
- 成功率是多少（我的预期：30–50%）
- 失败的原因分布（依赖冲突 / 权重缺失 / 数据不可得 / 代码不全 / 文档缺失）
- 平均耗时

**这 20 个数据点会决定整个 ResearchForge 是否可行。** 如果成功率是 30%，那么 `BASELINE_REPRODUCED` 这个 hard gate 会让 70% 的 run 死掉，你必须重新设计降级路径。这个实验一周内能做完，成本极低，信息量极大。**在写任何 runtime 代码之前先做这个。**

### 4.3 文献 API 是硬约束，包里一个字没提

`literature-search` / `citation-resolver` / `novelty-verifier` 全部依赖外部学术 API。现实：

- Semantic Scholar API 有严格配额，无 key 时约 100 req/5min
- Crossref 要求 polite pool（带邮箱 UA）
- arXiv API 有速率限制且全文检索能力弱
- **`novelty-verifier` 的核心动作「把 idea 翻译成多个术语族并做全文近重复检索」，现有公开 API 基本做不到**——全文语义检索需要自建向量库或付费服务

包里没有 provider 清单、没有 key 管理、没有配额策略、没有覆盖率降级方案。而 `novelty-verifier` 的 Hard gate 写着「NOVEL_ENOUGH 必须有至少一个 closest-prior-work 比较」——在 API 覆盖不足时，这个 gate 会被"UNKNOWN_COVERAGE"占满，然后 ranker 惩罚所有 idea，系统输出空集。

### 4.4 系统自身没有评估标准

Batch 13 的 DoD 是"一篇 fixture 论文跑通全流程"。**n=1，而且是自己选的那一篇。**

真正的问题是：**你怎么知道它生成的 idea 是好 idea？** 这不是 unit test 能回答的。可行的做法：

- 建一个 **retrospective benchmark**：拿 30 篇 2024 年的论文作为输入，看系统能否生成出**已经在 2025–2026 年被真实发表**的后续工作方向。这是唯一能客观打分的方式，且数据免费。
- 人工评分 rubric：让 3 位领域内博士盲评 system-generated idea vs human baseline idea。

这件事应该在 Batch 05（创新引擎）**之前**设计好，否则你无法判断创新引擎是变好还是变坏。

### 4.5 补充：发表伦理没有提及

系统的终点是"可提交的论文 + 真实引用 + 答辩 PPT"。ICML/NeurIPS/ICLR/Nature 对 LLM 生成内容均有明确披露政策，且多数禁止 LLM 作为作者。包里 `LICENSE_NOTES.md` 对上游代码许可考虑得很周到，但对**产出物本身的学术伦理**零覆盖。建议在 `release-gate-exporter` 里加一条 hard gate：产出物必须附带 AI 参与度声明，且默认标记为"辅助草稿"而非"可直接投稿"。

---

## 5. 修订后的实施顺序

原 IMPLEMENTATION_PLAN 的 13 个 batch 顺序合理，但**每个 batch 的规模被严重低估**——Batch 08（沙箱化多分支实验引擎 + 预算控制）本身就是一个 6 人月的项目。而且它把最高风险的事情放在了中间。

建议改成：

**Phase 0（1–2 周，不写 runtime 代码）**
1. 写死 v1 学科域和资源上限
2. 做 §4.2 的 20 篇 baseline 复现实验，拿到真实成功率
3. 做 §4.4 的 retrospective benchmark（30 篇论文 + 已知后续工作）
4. 摸清文献 API 的实际配额与覆盖率
5. 修 §2.1 的 I/O 契约 + §2.2 的依赖图 + 强化 validator

> **如果第 2 步的复现成功率低于 30%，停下来重新设计，不要进 Phase 1。**

**Phase 1（垂直切片，不是横向 batch）**
不要按"Batch 01 做完再做 Batch 02"。做一条**最窄的端到端垂直切片**：
> 一篇写死的论文 → 3 个 idea → 人工选 1 个 → 跑 1 个已复现的 baseline + 1 个改动 → 1 张图 → 1 段带引用的文字

这条切片能跑通，比 62 个 skill 全部写完有价值一百倍。它会立刻暴露所有 §2.1 的接口问题。

**Phase 2+** 再按原 batch 顺序加厚。

---

## 6. 如果只做一件事

**把 `Inputs` / `Outputs` 改成引用 schema 的结构化字段，并在 validator 里加一条「每个 input 必须能连到某个 output，否则必须显式标记 external」。**

这一条改动会强迫整个架构自我暴露：137 个悬空输入里，有多少是真的外部输入，有多少是命名不一致，有多少是根本没人生产的幻想产物。今天下午就能做完，而它会改变你对这个系统的全部判断。

---

## 附录 A：30 个从 orchestrator 不可达的 skill

ablation-and-counterfactual-planner, baseline-reproduction-auditor, claim-citation-auditor, codebase-scaffolder, data-prep-agent, debug-and-repair, domain-skill-loader, editable-svg-refiner, experiment-spec-author, experiment-tree-search, finding-memory-manager, genetic-idea-mutator, journal-fit-reviewer, metric-and-hidden-evaluator-builder, model-provider-router, multi-agent-research-team, negative-result-curator, paper-html-visual-reader, paper-library-connector, rebuttal-and-revision-planner, research-eval-harness, research-landscape-builder, research-progress-controller, sandbox-provisioner, scientific-figure-generator, skill-evolution-manager, skill-package-auditor, tool-composition-planner, tool-skill-router, vector-figure-reconstructor

## 附录 B：上游仓库抽样核实结果

| 仓库 | 存在 | 星数 | SOURCE_MAP 描述准确性 |
|---|---|---|---|
| microsoft/SkillOpt | ✅ | 15.8k | 准确（rollout/reflect/validation-gated update） |
| ResearAI/DeepScientist | ✅ | 3.2k | 准确（local-first autonomous research studio） |
| OpenNSWM-Lab/FAROS | ✅ | 728 | 准确（Blueprint/Capability/Profile/Provider 运行时抽象） |
| WUBING2023/PaperSpine | ✅ | 28 | 准确（motivation-driven、revision matrix、LaTeX-safe audit） |
| CMarsRover/SciAgentGYM | ✅ | 26 | 准确（多步科学工具调用 benchmark，1780+ 工具） |
| THU-Team-Eureka/EurekAgent | ✅ | 19 | 准确（metric-driven 自主发现，隔离容器） |

抽样 6/34，全部真实且描述无误。SOURCE_MAP 可信。

---

## 附录 C：随评审附带的可直接使用的产出 —— `validate_package_v2.py`

我把 §6 的建议直接实现了。放到 `tests/validate_package_v2.py` 即可运行。
它在 v1 的基础上补上了：可达性、依赖环、I/O 契约闭合、schema 绑定、散文与 depends_on 的一致性、description 触发语义、规格密度，**并且在结尾明确列出它没有检查什么**。

在当前 v0.1.0 上的实际输出：

```text
========================================================================
FAILED

  ERROR  30/62 skills unreachable from 'researchforge-orchestrator': ablation-and-counterfactual-planner, baseline-reproduction-auditor, claim-citation-auditor, codebase-scaffolder, data-prep-agent, debug-and-repair, domain-skill-loader, editable-svg-refiner, experiment-spec-author, experiment-tree-search, finding-memory-manager, genetic-idea-mutator, journal-fit-reviewer, metric-and-hidden-evaluator-builder, model-provider-router, multi-agent-research-team, negative-result-curator, paper-html-visual-reader, paper-library-connector, rebuttal-and-revision-planner, research-eval-harness, research-landscape-builder, research-progress-controller, sandbox-provisioner, scientific-figure-generator, skill-evolution-manager, skill-package-auditor, tool-composition-planner, tool-skill-router, vector-figure-reconstructor
  ERROR  137/147 declared inputs match no declared output and are not marked 'external:' — the artifact contract is not closed.
  ERROR      dangling input 'active run state'  (needed by research-progress-controller)
  ERROR      dangling input 'agent/skill version'  (needed by research-eval-harness)
  ERROR      dangling input 'all audits'  (needed by release-gate-exporter)
  ERROR      dangling input 'analogies'  (needed by idea-portfolio-generator)
  ERROR      dangling input 'analysis artifacts'  (needed by statistical-integrity-auditor)
  ERROR      dangling input 'analysis question'  (needed by data-analysis-agent)
  ERROR      dangling input 'artifact creation/edit events'  (needed by artifact-provenance)
  ERROR      dangling input 'artifact manifest'  (needed by release-gate-exporter)
  ERROR      dangling input 'audience'  (needed by defense-ppt-storyline)
  ERROR      dangling input 'audits'  (needed by paper-quality-integrity-gate)
  ERROR      dangling input 'baseline'  (needed by ablation-and-counterfactual-planner)
  ERROR      dangling input 'baseline assets'  (needed by feasibility-estimator)
  ERROR      dangling input 'baseline config'  (needed by experiment-spec-author)
  ERROR      dangling input 'baseline repo'  (needed by codebase-scaffolder)
  ERROR      dangling input 'baseline/reproduction state'  (needed by research-blueprint-compiler)
  ERROR      ... and 122 more
  ERROR  6/7 schemas referenced by NO skill: ArtifactManifest, ClaimEvidence, ExperimentResult, ExperimentSpec, PaperModel, ResearchBlueprint

  WARN   researchforge-orchestrator: Procedure names 20 skill(s) absent from depends_on: assumption-weakness-miner, baseline-repo-finder, citation-neighborhood-miner, claim-citation-auditor, codebase-scaffolder, contribution-decomposer, cross-domain-analogy-miner, experiment-ledger, experiment-spec-author, experiment-tree-search, feasibility-estimator, finding-memory-manager, idea-ranker, journal-fit-reviewer, literature-search, manuscript-spine-builder, novelty-gap-miner, novelty-verifier, paper-drafter, sandbox-provisioner
  WARN   literature-search: Procedure names 1 skill(s) absent from depends_on: citation-resolver
  WARN   research-blueprint-compiler: Procedure names 1 skill(s) absent from depends_on: tool-skill-router
  WARN   paper-library-connector: Procedure names 1 skill(s) absent from depends_on: citation-resolver
  WARN   research-landscape-builder: Procedure names 1 skill(s) absent from depends_on: novelty-gap-miner
  WARN   62/62 descriptions contain no when-to-use/trigger language; an agent cannot reliably route to them.
  WARN   53/62 skills carry <120 words of non-boilerplate specification (thinnest: vector-figure-reconstructor @ 66 words). Spec density this low is design intent, not an implementable contract.

------------------------------------------------------------------------
CHECKED : section presence, dep name resolution, dependency cycles,
          reachability from entry point, prose/depends_on agreement,
          Inputs->Outputs artifact linkage, schema parse + binding,
          description trigger language, non-boilerplate spec density.
NOT CHECKED : whether any procedure is correct, implementable, or
          sufficient; whether the runtime exists; whether outputs are
          scientifically valid. This validator says nothing about those.
========================================================================
```

四个 ERROR 就是本次评审的全部结构性结论。把它们清零，v0.2.0 才是一份真正的规格包。
