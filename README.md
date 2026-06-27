# hs-creator-crawler · 合盛创作者追踪

> 主流媒体平台**博主主动监控** + 视频下载 + 语音转录 + 飞书 Base 回写。
> 本项目 fork 自 [dragon-hh/ai-boshu-crawler](https://github.com/dragon-hh/ai-boshu-crawler)，已重写为合盛生产级架构。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](./pyproject.toml)
[![Platform: macOS / Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)](./pyproject.toml)

---

## 它解决了什么

| 你现在痛点 | hs-creator-crawler 解法 |
|---|---|
| RSS / 播客是被动订阅，新博主加不进来 | **主动** 追踪博主列表（飞书 Base + JSON 双源） |
| 视频平台没法自动下载 | yt-dlp 统一下载（B 站 / 抖音 / YouTube / X / Threads） |
| 视频内容没法搜 | mlx-whisper（macOS 本地 MPS）+ OpenAI Whisper（云端 fallback）双后端 ASR |
| 内容散落各平台没法聚合分析 | 统一 schema 写回飞书 Base（动态字段映射，零硬编码） |

---

## 已支持平台

| 平台 | 列表 | 下载 | 转录 | 评论 | 状态 |
|---|---|---|---|---|---|
| Bilibili（B 站）| ✅ | ✅ yt-dlp | ✅ 官方 CC 优先 + ASR fallback | 🚧 开发中 | **生产可用** |
| Douyin（抖音）| ✅ | ✅ yt-dlp | ✅ | 🚧 CDP 桥接 | **生产可用**（需 Cookie） |
| YouTube | ✅ | ✅ yt-dlp | ✅ | 🚧 | **生产可用** |
| Xiaohongshu（小红书）| 🚧 | 🚧 | 🚧 | 🚧 | 阶段 3 |
| WeChat MP（公众号）| 🚧 | 🚧 | 🚧 | 🚧 | 阶段 3 |
| WeChat SPH（视频号）| 🚧 | 🚧 | 🚧 | 🚧 | 阶段 3 |
| X / Twitter | 🚧 | 🚧 | 🚧 | 🚧 | 阶段 4 |
| Threads | 🚧 | 🚧 | 🚧 | 🚧 | 阶段 4 |
| Instagram | 🚧 | 🚧 | 🚧 | 🚧 | 阶段 4 |
| TikTok 国际版 | 🚧 | 🚧 | 🚧 | 🚧 | 阶段 4 |

---

## 安装（macOS M4 · 主目标平台）

### 1. 准备系统依赖

```bash
# Homebrew 已装前提
brew install ffmpeg python@3.11 yt-dlp node

# ffmpeg 验证
ffmpeg -version | head -1

# node / yt-dlp 验证
node --version
yt-dlp --version
```

### 2. 克隆 + 创建虚拟环境

```bash
git clone https://github.com/wayne8877/ai-boshu-crawler.git ~/workspace/hs-creator-crawler
cd ~/workspace/hs-creator-crawler

# 用 uv（推荐，比 pip 快 10x，依赖锁 .venv）
brew install uv
uv venv --python 3.11 .venv
source .venv/bin/activate

# 安装项目 + macOS MPS 依赖
uv pip install -e ".[mps,test]"
```

### 3. 生成配置 + 填凭证

```bash
hs-crawler init-config  # 写入 ~/.config/hs-creator-crawler/config.json
# 编辑配置：飞书 app_id/secret / 飞书 Base 表 ID / OpenAI key / DashScope key 等
```

环境变量（推荐，比写在配置文件里更安全）：

```bash
export HS_CC_FEISHU__APP_ID="cli_xxxx"
export HS_CC_FEISHU__APP_SECRET="xxxx"
export HS_CC_ASR__OPENAI_API_KEY="sk-xxxx"
export HS_CC_ASR__DASHSCOPE_API_KEY="sk-xxxx"
```

### 4. 准备博主列表

`creators/{platform}.json`（开发期 JSON，阶段 2 接入飞书 Base）：

```json
[
  {
    "creator_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
    "name": "Google Developers",
    "url": "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
    "tags": ["tech", "AI"],
    "follower_count": 2300000
  }
]
```

### 5. 健康检查

```bash
hs-crawler list-platforms      # 看已注册的平台
hs-crawler health-check        # 跑平台连通性自检
```

### 6. 第一次采集（dry-run）

```bash
hs-crawler ingest --dry-run --max-creators 3 --videos-per-creator 2
```

### 7. 正式采集

```bash
hs-crawler ingest --videos-per-creator 3 --transcribe --sync-to-feishu
```

输出：`~/Downloads/hs-creator-crawler/manifests/{ts}-all-platform-latest.json`

---

## macOS M4 专属说明

### ASR 后端选择

| 场景 | 推荐 | 备注 |
|---|---|---|
| **主路径**（macOS 本地，零成本）| `mlx_whisper` | mlx-community/whisper-large-v3-turbo，MPS 加速 |
| **fallback**（云端兜底）| `openai_whisper` | OpenAI $0.006/分钟，需 `OPENAI_API_KEY` |
| **中文特别好** | `dashscope_qwen` | 阿里百炼 Qwen-ASR，¥0.04/分钟 |

**⚠️ Apple Silicon + Python 3.13 + MPS 卡死警告**：
威哥历史教训——`mlx-whisper` / `faster-whisper` 在 M 系列 + Python 3.13 组合上**会 hang**。
本项目已内置 **5 分钟硬超时**：mlx-whisper 超时立即降级到 fallback 后端，**绝不让 pipeline 卡死**。

### 性能优化

```bash
# 限制 ASR 并发（避免 OOM）
HS_CC_REQUEST_MAX_CONCURRENT=2

# 限制单视频转录时长（默认 3600 秒 = 1 小时）
HS_CC_ASR__MAX_DURATION_SECONDS=1800
```

---

## 合盛体系集成

### 飞书 Base 表 schema（动态映射）

不硬编码表名/字段名，全部走 `config.json`：

```json
{
  "feishu": {
    "app_id": "cli_xxxx",
    "app_secret": "xxxx",
    "tables": {
      "creators": {
        "app_token": "bascnXXXX",
        "table_id": "tblCreators",
        "field_mapping": {
          "platform": "平台",
          "creator_id": "博主 ID",
          "name": "博主名",
          "url": "主页链接",
          "follower_count": "粉丝数",
          "tags": "标签"
        }
      },
      "videos": {
        "app_token": "bascnXXXX",
        "table_id": "tblVideos",
        "field_mapping": {
          "platform": "平台",
          "video_id": "视频 ID",
          "title": "标题",
          "transcript_text": "转录全文",
          "asr_backend": "转录引擎",
          "local_video_path": "本地路径"
        }
      }
    }
  }
}
```

合盛已有 Base 表**不需要改 schema**，只要在配置里调整 `field_mapping` 即可对接。

### 与 hs-strategy / hs-content 协同

- **hs-strategy**（每日情报）调用 `hs-crawler ingest` 拿最新博主内容作为原始材料
- **hs-content**（小红书 / 抖音内容创作）从飞书 Base `videos` 表读候选素材
- 本系统写出的 manifest JSON 在 `~/Downloads/hs-creator-crawler/manifests/`，可直接被其它 skill 索引

---

## 命令清单

```bash
# 全平台采集
hs-crawler ingest --videos-per-creator 3 --transcribe --sync-to-feishu

# 单平台
hs-crawler ingest --no-transcribe --no-sync-to-feishu  # 只下载

# 干跑（不下载、不写）
hs-crawler ingest --dry-run --max-creators 5

# 健康检查
hs-crawler health-check

# 列已注册平台
hs-crawler list-platforms

# 初始化配置
hs-crawler init-config

# 队列处理（替代原 PowerShell 队列脚本）
./scripts/run_long_postprocess_queue.sh BV1xxxxxxx BV1yyyyyyy
./scripts/run_long_postprocess_queue.sh --from-file bvid-list.txt

# Cron 集成（每天 03:00 跑全平台采集）
0 3 * * * cd ~/workspace/hs-creator-crawler && source .venv/bin/activate && hs-crawler ingest --log-json >> ~/Downloads/hs-creator-crawler/logs/cron.log 2>&1
```

---

## 项目结构

```
hs-creator-crawler/
├── LICENSE                          MIT（含原作者致谢）
├── README.md                        本文件
├── pyproject.toml                   项目元数据 + 依赖
├── src/hs_creator_crawler/
│   ├── core/                        核心层：config/models/logging/exceptions
│   ├── platforms/                   平台实现（Bilibili/Douyin/YouTube + base 抽象）
│   ├── asr/                         ASR 后端（mlx-whisper/OpenAI/DashScope）
│   ├── feishu/                      飞书 Base 客户端（动态 schema）
│   ├── pipelines/                   编排层（all_platforms 主入口）
│   └── bin/                         CLI 入口（hs-crawler）
├── scripts/
│   └── run_long_postprocess_queue.sh  Bash 队列脚本（替代 PowerShell）
├── tests/                           pytest 测试
├── docs/                            补充文档
└── .agents/skills/                  保留 dragon-hh 原 .agents/skills/（Codex 风）
```

---

## 与原项目 dragon-hh/ai-boshu-crawler 的关系

| 维度 | 原项目 | 本 fork |
|---|---|---|
| License | 未声明（⚠️红旗）| **MIT** ✅ |
| Python 风格 | print + subprocess + manifest dict | **asyncio + Pydantic + structlog** |
| 配置加载 | 每个脚本独立 `load_config()` | **统一 pydantic-settings** + 环境变量覆盖 |
| 平台接入 | 硬编码表名 schema | **动态 schema 映射**（零硬编码）|
| ASR | `--device cuda --model large-v3-turbo` 硬编码 | **抽象 ASRBackend**，5 个后端 + 自动 fallback |
| 启动 | Windows PowerShell | **macOS / Linux Bash** |
| 单文件最大 | 1487 行（`download_bili_following_latest.py`）| **< 250 行 / 文件**（模块化） |
| 测试 | 无 | **pytest 骨架**（tests/） |
| 类型提示 | 无 | **Pydantic v2 + mypy strict** |

---

## Roadmap

- [x] 阶段 1：架构骨架 + License + YouTube 端到端 + ASR 双后端
- [ ] 阶段 2：Bilibili / Douyin 内部逻辑从 dragon-hh 原仓迁移 + CDP 评论抓取
- [ ] 阶段 3：小红书 / 微信公众号 / 视频号
- [ ] 阶段 4：X / Threads / Instagram / TikTok 国际版
- [ ] 阶段 5：合盛专属博主名单（UPR 树脂 / 阿里国际站钮扣趋势 / 树脂钮扣上下游）
- [ ] 阶段 6：cron 化（替代现有 `0 8,20 * * *` 百度热搜日报）+ 监控告警

---

## 贡献

PR 流程：fork → feature 分支 → 测试 → PR。

测试：

```bash
pytest -xvs tests/
```

Lint：

```bash
ruff check src/
mypy src/hs_creator_crawler/
```

---

## License

MIT — 详见 [LICENSE](./LICENSE)。  
原项目致谢：[dragon-hh/ai-boshu-crawler](https://github.com/dragon-hh/ai-boshu-crawler)
