# A′ 臂 — 检验本项目自己给出的工程建议

**执行日期：** 2026-08-11 ｜ **样本：** 与 A 臂完全相同的 20 篇（种子 20260811，零替换）

## 这不是 B 臂

协议的 B 臂是「熟练 ML 工程师、8 小时时间盒、盲于 A 臂日志」。我不是人类，把自己的结果标成人类天花板会污染这项研究里最有价值的那个数字。**B 臂仍未执行，agent 与人的差距仍未测。**

A′ 测的是一个更窄但更紧迫的问题：**A 臂产出的那条工程建议，到底成不成立。**

## 建了什么

v1.1.0 的建议是「依赖时间机器」。四条 lever 里两条在本环境可达：

| lever | 可达 | 实现 |
|---|---|---|
| L1 历史索引快照 | ✅ | `uv --exclude-newer <仓库最后提交日期>`，按当时可得的包版本解析 |
| L2 多版本解释器池 | ✅ | `uv python install 3.9/3.10/3.11/3.12`，优先用仓库声明的版本 |
| L3 conda/mamba 后端 | ❌ | miniforge 在 GitHub releases，被代理拦 |
| L4 torch/CUDA 专用索引 | ❌ | `download.pytorch.org` 被代理拦 |

## 一个方法学陷阱，差点让我报出错误的结论

第一版 A′ 用 `uv pip compile` 做解析，得到 15% → 30% 的漂亮翻倍。**那是假的。**

`uv pip compile` 不强制 `requires-python`，也不执行 `setup.py`。而 A 臂用的 `pip install --dry-run` **会**尝试构建。两者根本不可比。

改成对真实 venv 做 `uv pip install --dry-run` 后，再对全部 3 个「翻转」做**真实安装**验证：

| # | 仓库 | dry-run | 真实安装 | 真实原因 |
|---|---|---|---|---|
| 9 | VPPO-RL | 成功 | **失败** | `flash-attn` 的 setup.py 构建时 `import torch`，与 pip 失败在同一处 |
| 20 | pillar-pretrain | 成功 | **失败** | 同上 |
| 11 | Uni-MoE | 成功 | 无法测定 | requirements 指向被封的 `download.pytorch.org` |

## 结论：零提升

**3 个翻转全部在真实安装时失败。A′ 相对 A 的真实提升是 0。**

- L1 日期快照：救了 **1** 篇（#14，而那篇 A 臂本来就成功）
- L2 多版本解释器：救了 **0** 篇
- 换解析器：表面救了 3 篇，真实救了 **0** 篇

**v1.1.0 的「依赖时间机器」建议不被数据支持。** 我已在 `result-reproducer._remediation()` 里改正，并明确写上「日期索引已被测试且无效」，防止它以后被重新提出来。

## 真正的阻塞是什么

不是旧的版本 pin，不是错的 Python 版本。是 **CUDA 构建工具链**：`flash-attn`、`xformers`、`deepspeed` 这一类包在安装时需要编译，且构建阶段就需要 torch。

我顺带测了 uv 报错里给出的修复方案（先装 torch，再 `--no-build-isolation`）能否自动化——写了一个构建依赖追踪器（`build_dep_chaser.py`）：

```
round 1: miniclip needs pdm at build time      -> installed
round 2: miniclip needs pdm.backend            -> installed
round 3: FAILED — 回到 flash-attn，需要真实 CUDA 编译
DID NOT CONVERGE
```

**能自动剥掉浅层，剥不动最后那层。** 在有 CUDA 工具链的机器上它可能收敛——这里测不了。

## 修正后的工程优先级

| 原建议（v1.1.0） | 状态 |
|---|---|
| 历史 wheel 索引快照 | ❌ **已测试，无效** |
| 多版本解释器池 | ❌ **已测试，无效** |
| conda/mamba 后端 | 未测（主机被封） |
| PyTorch 专用索引 | 未测（主机被封）——但错误分布指向它是**真正**重要的那条 |

**修正后的第一优先级：为 CUDA 扩展包提供按 (torch, CUDA, Python) 三元组匹配的预编译轮子缓存。** 这是一个**访问问题**，不是解析问题——`download.pytorch.org` 和 flash-attn 的 release wheels 已经提供了它，需要的是能连上它们。

## 为什么这条记录值得保留

我给出了一条工程建议，建了它，测了它，它不成立，我把它写进代码注释以防自己或别人再提一次。这项研究的价值不在于它证实了什么，而在于它**证伪了提出它的人**。
