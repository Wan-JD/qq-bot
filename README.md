# QQ Bot - AstrBot 群聊机器人插件

基于 [AstrBot](https://github.com/Soulter/AstrBot) + [NapCat](https://github.com/NapNeko/NapCatQQ) + 大语言模型 API 的 QQ 群聊机器人插件。

通过 DeepSeek / OpenAI 兼容 API 驱动，支持**自定义人设**、上下文感知、管理员指令控制。人设风格完全由你决定——毒舌、温柔、二次元、学术派……随意切换。

## 功能特性

- **自定义人设**：通过修改提示词即可设定任意性格和说话风格
- **触发词回复**：群聊中包含触发词或被@时回复
- **上下文感知**：不回复时也在听，触发回复能接上话题；自动识别@关系和引用，分清谁对谁说了什么
- **管理员指令**：管理员可通过私聊/群聊控制 bot 主动发消息（怼人、@群友、活跃等）
- **单群定向**：指令只在触发群执行，不广播
- **管理员模式**：指定账号私聊无需触发词，使用专属人设
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

1. 将 `plugin/` 目录下的所有文件复制到：
   ```
   <AstrBot安装目录>/data/plugins/workbuddy_bridge/
   ```
   最终目录结构：
   ```
   plugins/workbuddy_bridge/
   ├── main.py
   ├── metadata.yaml
   └── api_key.txt      ← 手动创建，填入 API Key
   ```

### 4. 配置 API Key

1. 在 `plugin/` 目录下创建 `api_key.txt` 文件
2. 文件中只写一行，内容为你的 API Key：
   ```
   sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
3. 获取 DeepSeek API Key：https://platform.deepseek.com/api_keys

> `api_key.txt` 已在 `.gitignore` 中排除，不会被上传到仓库。仓库中提供了 `api_key.example.txt` 作为模板。

### 5. 配置插件参数

编辑 `plugin/main.py` 顶部的配置区域：

```python
TARGET_GROUP_IDS = ["群号1", "群号2"]   # 目标群聊ID列表
TEST_ACCOUNT = "管理员QQ号"              # 管理员QQ号（无条件服从）
TRIGGER_WORD = "触发词"                  # 触发词（群聊中包含此词或被@时触发）
DEEPSEEK_API_URL = "https://..."        # API 地址（可替换）
DEEPSEEK_MODEL = "deepseek-chat"         # 模型名称（可更换）
NAPCAT_HTTP_API = "http://127.0.0.1:3002"  # NapCat HTTP API 地址
```

### 6. 启动

使用 `scripts/start_qq_bot.bat` 一键启动：

```bat
start_qq_bot.bat
```

启动流程：
1. 停止已有的 Python/QQ 进程
2. 启动 NapCat（等待 8 秒连接）
3. 启动 AstrBot

启动后等待约 15 秒，确保 NapCat 连接成功后再测试。

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
| `@bot 怼 @某人` | 在群里@某人并怼他（结合上下文） |
| `@bot 怼 @某人 理由` | 带理由怼人 |
| `@bot 找 @某人 聊天 内容` | 主动@某人并搭话（内容会对目标说） |
| `@bot 找某人聊天` | 按名字找人搭话 |
| `@bot @某人 内容` | 在群里@某人说内容 |
| `@bot 活跃一下` | 根据当前话题冒个泡 |
| `@bot 去群里说xxx` | 在群里发消息 |
| `@bot 别理某人` | 静默确认 |

所有指令执行后**静默完成**，不会回复确认文字。

### 上下文感知

- 所有目标群的消息（包括未触发回复的）都会被记录
- 自动识别**@关系**（谁@了谁）和**引用回复**，准确还原对话场景
- 每个群保留最近 30 条消息，10 分钟过期
- 回复和指令都会参考上下文，让内容更贴合当前聊天

## 自定义人设

编辑 `plugin/main.py` 中的提示词变量即可完全定义 bot 的性格：

- `SYSTEM_PROMPT`：**通用人设**（群聊中使用，所有人看到的风格）
- `BRO_SYSTEM_PROMPT`：**管理员人设**（管理员私聊或指令模式使用，通常更亲近随意）

你可以将人设设为任何风格，例如：

| 风格 | 示例提示词关键词 |
|------|----------------|
| 毒舌损友 | 攻击性、互损、犀利、贴吧风格 |
| 温柔陪伴 | 温柔、关心、善解人意、安慰 |
| 二次元萌娘 | 萌、喵、酱、~、可爱的语气 |
| 学术助手 | 专业、严谨、引用论文、数据支撑 |
| 搞笑段子手 | 梗、谐音、段子、反转 |

> 项目默认提供的是毒舌损友风格作为参考，你可以完全替换为自己的设定。

## 更换模型

### DeepSeek 系列

编辑 `plugin/main.py`：

```python
DEEPSEEK_MODEL = "deepseek-chat"       # 标准版
# DEEPSEEK_MODEL = "deepseek-reasoner"  # 推理版
```

API 地址保持不变：`https://api.deepseek.com/chat/completions`

### 其他兼容 OpenAI 格式的模型

编辑 `plugin/main.py` 中的两个参数：

```python
DEEPSEEK_API_URL = "https://api.openai.com/v1/chat/completions"  # OpenAI
DEEPSEEK_MODEL = "gpt-4o-mini"
```

支持任何兼容 OpenAI Chat Completions API 格式的服务（如通义千问、智谱GLM、本地Ollama等），只需修改 URL 和模型名即可。

### API Key 文件

无论用什么模型，`api_key.txt` 中填入对应服务的 API Key 即可。

## 注意事项

1. **QQ 登录冲突**：QQ 号不能同时在手机和 NapCat 登录，需先手机下线
2. **风险提示**：使用第三方 QQ 协议端存在被封号风险，请自行评估
3. **API 费用**：按量计费，建议关注用量
4. **端口冲突**：确保 3001（WebSocket）和 3002（HTTP API）端口未被占用

## 项目结构

```
qq-bot/
├── .gitignore                 # Git 忽略规则
├── README.md                  # 本文件
├── api_key.example.txt        # API Key 模板（空文件）
│
├── plugin/                    # AstrBot 插件
│   ├── main.py                # 核心插件代码（人设、指令、上下文处理）
│   └── metadata.yaml          # 插件元数据
│
├── config/                    # 配置文件参考
│   ├── astrbot.json           # AstrBot 配置（敏感信息已清除）
│   └── napcat.json            # NapCat OneBot11 配置
│
└── scripts/
    └── start_qq_bot.bat       # 一键启动脚本
```

## 技术栈

- **AstrBot** — 机器人框架
- **NapCat** — QQ 协议端（OneBot11 实现）
- **LLM API** — 大语言模型（默认 DeepSeek，可替换）
- **aiohttp** — 异步 HTTP 客户端

## License

MIT
