# QQ Bot - AstrBot 群聊机器人插件

基于 [AstrBot](https://github.com/Soulter/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ) + 大语言模型 API 的 QQ 群聊机器人插件。

通过 OpenAI 兼容 API 驱动，支持**多风格切换**、上下文感知、管理员指令控制。

## 功能特性

- **多风格切换**：内置 5 套预设人设（贴吧老哥、温柔学姐、毒舌损友、学术大佬、二次元萌娘），管理员可随时切换，也可自定义风格
- **隐私配置分离**：API Key、管理员QQ、目标群号等敏感信息全部外置到 `config_local.json`，不会进入仓库
- **触发词回复**：群聊中包含触发词或被@时回复
- **上下文感知**：不回复时也在听，触发回复能接上话题；自动识别@关系和引用
- **管理员指令**：管理员可通过私聊/群聊控制 bot 主动发消息
- **风格切换指令**：管理员发送 `/风格切换` 即可选择人设风格
- **多模型支持**：兼容任何 OpenAI Chat Completions 格式的 API

## 系统架构

```
┌─────────────┐    WebSocket     ┌─────────────┐
│   NapCat    │ ◄────────────► │   AstrBot   │
│ (QQ协议端)  │   :3001端口     │  (框架)     │
│             │                 │             │
│  HTTP API   │ ◄──────────────┤  插件系统    │
│  :3002端口  │                 │             │
└─────────────┘                 └──────┬──────┘
                                       │
                                       ▼
                                ┌─────────────┐
                                │  LLM API    │
                                │  (DeepSeek / │
                                │  OpenAI /..) │
                                └─────────────┘
```

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Windows | 10/11 | NapCat 目前主要支持 Windows |
| Python | 3.10+ | AstrBot 运行环境 |
| Node.js | 18+ | NapCat 运行环境 |
| QQ号 | - | 用于登录的 QQ 号（不能同时在手机登录） |

## 安装步骤

### 1. 安装 NapCat（QQ 协议端）

1. 下载 NapCat：https://github.com/NapNeko/NapCatQQ/releases
2. 解压到 `C:\Users\<你的用户名>\NapCat\` 目录
3. 将本项目的 `config/napcat.json` 复制到 NapCat 的配置目录：
   ```
   NapCat.XXX.Shell/versions/<版本号>/resources/app/napcat/config/onebot11_<你的QQ号>.json
   ```
4. 修改 `onebot11_<你的QQ号>.json` 中的 WebSocket 地址（如需要）

### 2. 安装 AstrBot（机器人框架）

1. 安装 AstrBot：https://docs.astrbot.app/getting-started/install
2. 将本项目的 `config/astrbot.json` 复制到 AstrBot 的数据目录：
   ```
   <AstrBot安装目录>/data/cmd_config.json
   ```
3. 关键配置说明（在 `cmd_config.json` 中）：
   - `provider_sources` → `key`：填入你的 API Key（或留空由插件读取）
   - `provider_settings.enable`：设为 `false`（禁用 AstrBot 内置对话，由插件接管）
   - `platform` → `ws_reverse_port`：与 NapCat WebSocket 地址一致（默认 3001）

### 3. 安装插件

将以下目录结构复制到 AstrBot 插件目录：

```
<AstrBot安装目录>/data/plugins/workbuddy_bridge/
├── main.py                           # 核心插件代码
├── metadata.yaml                     # 插件元数据
├── config_local.json                 # ← 手动创建，填入隐私配置
├── api_key.txt                       # ← 手动创建，填入 API Key
└── prompts/                          # 风格预设目录
    ├── 贴吧老哥.json                  # 默认风格
    ├── 温柔学姐.json
    ├── 毒舌损友.json
    ├── 学术大佬.json
    └── 二次元萌娘.json
```

### 4. 配置隐私信息

#### 4.1 创建 `config_local.json`

参照 `config_local.example.json` 创建 `config_local.json`（已在 `.gitignore` 中排除）：

```json
{
    "boss_qq": "你的管理员QQ号",
    "target_groups": ["目标群号1", "目标群号2"],
    "trigger_word": "触发词",
    "napcat_http_api": "http://127.0.0.1:3002",
    "llm_api_url": "https://api.deepseek.com/chat/completions",
    "llm_model": "deepseek-chat",
    "deepseek_api_url": "https://api.deepseek.com/chat/completions",
    "deepseek_model": "deepseek-chat",
    "default_style": "贴吧老哥"
}
```

| 字段 | 说明 |
|------|------|
| `boss_qq` | 管理员QQ号，只有此人可以使用指令和切换风格 |
| `target_groups` | 目标群聊ID列表 |
| `trigger_word` | 群聊触发词 |
| `napcat_http_api` | NapCat HTTP API 地址 |
| `llm_api_url` | LLM API 地址（兼容 OpenAI 格式，优先使用） |
| `llm_model` | 模型名称（优先使用） |
| `deepseek_api_url` | 旧字段，向后兼容 |
| `deepseek_model` | 旧字段，向后兼容 |
| `default_style` | 默认风格名称（对应 prompts/ 下的文件名） |

#### 4.2 创建 `api_key.txt`

在插件目录下创建 `api_key.txt`，内容为你的 API Key：

```
sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> 也可以在 `config_local.json` 中设置 `"api_key"` 字段，但 `api_key.txt` 优先级更高（向后兼容）。

### 5. 启动

使用 `scripts/start_qq_bot.bat` 一键启动：

```bat
start_qq_bot.bat
```

## 使用方法

### 触发回复

| 场景 | 触发条件 |
|------|---------|
| 群聊 | 消息包含触发词或 被@bot |
| 管理员私聊 | 任何消息直接触发，无需触发词 |
| 其他私聊 | 消息包含触发词 |

### 管理员指令

管理员在群聊中 @bot 发送指令，或在私聊中直接发送：

| 指令 | 说明 |
|------|------|
| `/风格切换` | 打开风格选择面板（仅管理员可用） |
| `/风格列表` | 查看所有可用风格 |
| `/当前风格` | 查看当前使用的风格 |
| `/重载风格` | 重新扫描 prompts/ 目录加载风格 |
| `/状态` | 查看 bot 运行状态 |
| `/群列表` | 查看监听的群聊列表 |
| `/清空上下文` | 清空所有群聊上下文（需确认） |
| `/帮助` | 查看所有指令 |
| `@bot 怼 @某人` | 在群里@某人并怼他（结合上下文） |
| `@bot 怼 @某人 理由` | 带理由怼人 |
| `@bot 找 @某人 聊天 内容` | 主动@某人并搭话 |
| `@bot 找某人聊天` | 按名字找人搭话 |
| `@bot @某人 内容` | 在群里@某人说内容 |
| `@bot 活跃一下` | 根据当前话题冒个泡 |
| `@bot 去群里说xxx` | 在群里发消息 |
| `@bot 别理某人` | 静默确认 |

> 以 `/` 开头的是系统指令（如 `/风格切换`），其余为动作指令。系统指令有回复反馈，动作指令执行后**静默完成**。

### 风格切换

1. 管理员发送 `/风格切换`
2. Bot 发送风格列表面板：
   ```
   【风格切换面板】当前: 贴吧老哥
   
     1. 🤡搞笑段子手 - 随时随地讲段子接梗的幽默大师
     2. 🧊冷漠高手 - 高冷话少但每句话都有分量
     3. 🧔哲学大叔 - 说话总带哲理的老灵魂
     4. ⚔️中二少年 - 热血中二病少年
     5. 📚学术大佬 - 学术圈大佬风格
     6. 🎀二次元萌娘 - 二次元风格小可爱
     7. 🤗知心姐姐 - 温暖贴心的大姐姐
     8. 🌸温柔学姐 - 温柔体贴的学姐
     9. 🔪毒舌损友 - 嘴特别毒但关系特别铁
    10. 😏贴吧老哥 - 嘴欠但有趣的大学生 ← 当前
   
   回复序号或风格名切换，其他内容取消
   ```
3. 管理员回复序号或风格名即可切换，bot 会确认切换结果

### 自定义风格

在 `prompts/` 目录下创建新的 `.json` 文件：

```json
{
    "style_name": "我的风格",
    "description": "简短描述",
    "emoji": "🎯",
    "system_prompt": "通用人设提示词...",
    "boss_system_prompt": "管理员专属提示词..."
}
```

- `system_prompt`：群聊中所有普通用户看到的风格
- `boss_system_prompt`：管理员私聊/指令模式使用的风格（通常更亲近）
- `boss_system_prompt` 可省略，省略时使用 `system_prompt`

然后在 `config_local.json` 中设置 `"default_style": "我的风格"` 即可。

### 上下文感知

- 所有目标群的消息（包括未触发回复的）都会被记录
- 自动识别**@关系**（谁@了谁）和**引用回复**，准确还原对话场景
- 每个群保留最近 30 条消息，10 分钟过期
- 回复和指令都会参考上下文，让内容更贴合当前聊天


## 新增玩法

- `/今日总结 [正经|缺德|贴吧]`：按当前群聊上下文生成当天群聊复盘。
- `/画像 @某人`：根据群内发言样本和统计生成群友画像。
- 自动接梗：在 `config_local.json` 启用 `auto_reply_enabled` 后，命中热梗关键词时低概率自然冒泡。
- 定时整活：启用 `scheduled_fun_enabled` 后，可按 `scheduled_fun_times` 定时发一句群聊风格消息。
- 今日人设轮换：启用 `daily_style_rotation_enabled` 后，每天自动为各群随机切换人设。
- 点歌式人格：触发 bot 后发送“用温柔学姐说一句 xxx”，可临时用指定人格回复一次。
- 名场面记录：`/记下来` 保存上一条群聊发言，`/翻旧账 @某人` 随机翻出历史名场面。
- 黑话词典：`/记梗 词 = 解释` 让 bot 理解群内暗号，后续回复会参考。
- 群友召唤术：`/召唤 @某人 理由` 生成自然点名话术。
- 人格混合器：`/融合 毒舌损友 温柔学姐` 生成临时混合人格。
- 群聊小游戏：`/真心话`、`/大冒险`、`/接龙 开始 词`、`/猜词`。
- 多群人格隔离：`group_settings` 可为不同群设置不同默认人设、触发词和自动接梗概率。
- 昵称记忆：`/记昵称 @某人 外号` 后，画像、召唤、排行榜会优先显示外号。
- 热度排行榜：`/排行榜` 查看最近发言和被 @ 统计。

运行时记忆保存在插件目录的 `workbuddy_memory.json`，该文件已被 `.gitignore` 排除，不会提交到仓库。

## 更换模型

### DeepSeek 系列

编辑 `config_local.json`：

```json
{
    "llm_model": "deepseek-chat"
}
```

API 地址保持不变：`https://api.deepseek.com/chat/completions`。旧的 `deepseek_*` 字段仍可使用，但新配置建议优先使用 `llm_*`。

### 其他兼容 OpenAI 格式的模型

编辑 `config_local.json`：

```json
{
    "llm_api_url": "https://api.openai.com/v1/chat/completions",
    "llm_model": "gpt-4o-mini"
}
```

支持任何兼容 OpenAI Chat Completions API 格式的服务（通义千问、智谱GLM、本地Ollama等）。

## 项目结构

```
qq-bot/
├── .gitignore                         # Git 忽略规则（排除 api_key.txt, config_local.json）
├── README.md                          # 本文件
├── api_key.example.txt                # API Key 模板
│
├── plugin/                            # AstrBot 插件（复制到 AstrBot/data/plugins/workbuddy_bridge/）
│   ├── main.py                        # 核心插件代码
│   ├── metadata.yaml                  # 插件元数据
│   ├── config_local.example.json      # 隐私配置模板
│   └── prompts/                       # 风格预设目录（按文件名排序）
│       ├── 中二少年.json              # 热血中二病少年
│       ├── 二次元萌娘.json            # 二次元可爱风
│       ├── 冷漠高手.json              # 高冷惜字如金
│       ├── 哲学大叔.json              # 话里带哲理
│       ├── 学术大佬.json              # 学术风
│       ├── 搞笑段子手.json            # 幽默接梗
│       ├── 毒舌损友.json              # 犀利损友
│       ├── 温柔学姐.json              # 温柔体贴
│       ├── 知心姐姐.json              # 温暖贴心大姐姐
│       └── 贴吧老哥.json              # 默认：嘴欠有趣大学生
│
├── config/                            # 配置文件参考
│   ├── astrbot.json                   # AstrBot 配置
│   └── napcat.json                    # NapCat OneBot11 配置
│
└── scripts/
    └── start_qq_bot.bat               # 一键启动脚本
```

## 注意事项

1. **QQ 登录冲突**：QQ 号不能同时在手机和 NapCat 登录，需先手机下线
2. **风险提示**：使用第三方 QQ 协议端存在被封号风险，请自行评估
3. **API 费用**：按量计费，建议关注用量
4. **端口冲突**：确保 3001（WebSocket）和 3002（HTTP API）端口未被占用
5. **隐私安全**：`config_local.json` 和 `api_key.txt` 已在 `.gitignore` 中排除，不会被提交到仓库

## 技术栈

- **AstrBot** — 机器人框架
- **NapCat** — QQ 协议端（OneBot11 实现）
- **LLM API** — 大语言模型（默认 DeepSeek，可替换）
- **aiohttp** — 异步 HTTP 客户端

## License

MIT
