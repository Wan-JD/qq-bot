"""
WorkBuddy Bridge Plugin for AstrBot with DeepSeek
支持多风格切换的QQ群聊机器人插件
- 单群定向发送，不广播
- 上下文感知，不回复也在听
- 支持@、引用、怼人等指令
- 管理员专属 /系统指令（风格切换、状态查看、上下文管理等）
- 提示词外置到 prompts/ 目录，支持多套预设
- 隐私配置外置到 config_local.json
"""

import aiohttp
import re
import asyncio
import random
import time
import json
from pathlib import Path
from collections import deque
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ============================================================
# 配置加载（从 config_local.json + api_key.txt 读取）
# ============================================================
_PLUGIN_DIR = Path(__file__).parent

def _load_config() -> dict:
    """从 config_local.json 加载配置（隐私信息，不入仓库）"""
    config_path = _PLUGIN_DIR / "config_local.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[WorkBuddy] Failed to load config_local.json: {e}")
    else:
        logger.warning(f"[WorkBuddy] config_local.json not found at {config_path}, using defaults")
    return {}

_config = _load_config()

_API_KEY_PATH = _PLUGIN_DIR / "api_key.txt"
if _API_KEY_PATH.exists():
    DEEPSEEK_API_KEY = _API_KEY_PATH.read_text(encoding="utf-8").strip()
else:
    DEEPSEEK_API_KEY = _config.get("api_key", "")

TARGET_GROUP_IDS = _config.get("target_groups", [])
TEST_ACCOUNT = str(_config.get("boss_qq", ""))
TRIGGER_WORD = _config.get("trigger_word", "")
DEEPSEEK_API_URL = _config.get("deepseek_api_url", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = _config.get("deepseek_model", "deepseek-chat")
NAPCAT_HTTP_API = _config.get("napcat_http_api", "http://127.0.0.1:3002")
DEFAULT_STYLE = _config.get("default_style", "贴吧老哥")

CONTEXT_MAX_MSGS = 30
CONTEXT_MAX_AGE = 600

# ============================================================
# 提示词加载（从 prompts/ 目录读取，按文件名排序保证序号稳定）
# ============================================================
_PROMPTS_DIR = _PLUGIN_DIR / "prompts"

def _load_prompts() -> list:
    """扫描 prompts/ 目录，按文件名排序加载所有风格预设，返回有序列表"""
    styles = []
    if not _PROMPTS_DIR.exists():
        logger.warning(f"[WorkBuddy] prompts/ directory not found at {_PROMPTS_DIR}")
        return styles
    files = sorted(_PROMPTS_DIR.glob("*.json"), key=lambda f: f.stem)
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            name = data.get("style_name", f.stem)
            data["_filename"] = f.name
            styles.append(data)
            logger.info(f"[WorkBuddy] Loaded style [{len(styles)}]: {name}")
        except Exception as e:
            logger.error(f"[WorkBuddy] Failed to load prompt file {f.name}: {e}")
    return styles

STYLE_LIST = _load_prompts()       # 有序列表，保证序号永远一致
STYLE_NAMES = [s["style_name"] for s in STYLE_LIST]  # 对应序号的名字列表


# ============================================================
# 插件类
# ============================================================

@register("workbuddy_bridge", "WorkBuddy", "WorkBuddy QQ Bridge with DeepSeek", "4.1.0")
class WorkBuddyBridge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._processed_msgs = {}
        self._group_context = {gid: deque(maxlen=CONTEXT_MAX_MSGS) for gid in TARGET_GROUP_IDS}
        self._current_style = DEFAULT_STYLE
        # 等待状态：{user_id: "pending_type"} 类型有 style_switch, clear_ctx
        self._pending_action = {}

    def _get_style_data(self, style_name: str) -> dict:
        """按名称查找风格数据"""
        for s in STYLE_LIST:
            if s.get("style_name") == style_name:
                return s
        return {}

    @property
    def _system_prompt(self) -> str:
        style = self._get_style_data(self._current_style)
        return style.get("system_prompt", "你是一个群聊机器人，自然地回复即可。")

    @property
    def _boss_system_prompt(self) -> str:
        style = self._get_style_data(self._current_style)
        return style.get("boss_system_prompt") or style.get("system_prompt", "你是一个群聊机器人。")

    async def initialize(self):
        logger.info("=" * 50)
        logger.info("WorkBuddy Bridge v4.1.0 initialized")
        logger.info(f"Target groups: {TARGET_GROUP_IDS}")
        logger.info(f"Boss account: {TEST_ACCOUNT}")
        logger.info(f"Current style: {self._current_style}")
        logger.info(f"Available styles ({len(STYLE_LIST)}): {STYLE_NAMES}")
        logger.info(f"NapCat HTTP API: {NAPCAT_HTTP_API}")
        logger.info("=" * 50)

    # ----------------------------------------------------------
    # 系统指令处理（以 / 开头，仅管理员）
    # ----------------------------------------------------------

    def _build_style_panel(self) -> str:
        """生成风格列表面板"""
        if not STYLE_LIST:
            return "没有找到任何风格预设，请检查 prompts/ 目录"
        lines = [f"【风格切换面板】当前: {self._current_style}", ""]
        for i, s in enumerate(STYLE_LIST, 1):
            name = s.get("style_name", "")
            emoji = s.get("emoji", "")
            desc = s.get("description", "")
            marker = " ← 当前" if name == self._current_style else ""
            lines.append(f"  {i}. {emoji}{name} - {desc}{marker}")
        lines.append("")
        lines.append("回复序号或风格名切换，其他内容取消")
        return "\n".join(lines)

    async def _handle_system_command(self, cmd: str, from_group: str = None) -> str | None:
        """
        处理 / 开头的系统指令。
        返回 str 表示要发送给管理员的消息，None 表示不是系统指令。
        """
        cmd = cmd.strip()
        if not cmd.startswith("/"):
            return None

        action = cmd[1:].strip()  # 去掉 /

        # === /风格切换 ===
        if action in ("风格切换", "切换风格", "style", "switch"):
            self._pending_action[TEST_ACCOUNT] = "style_switch"
            return self._build_style_panel()

        # === /风格列表 ===
        if action in ("风格列表", "列表", "styles", "list"):
            return self._build_style_panel()

        # === /当前风格 ===
        if action in ("当前风格", "当前", "now"):
            style = self._get_style_data(self._current_style)
            emoji = style.get("emoji", "")
            desc = style.get("description", "")
            return f"当前风格: {emoji}{self._current_style}\n{desc}"

        # === /状态 ===
        if action in ("状态", "status", "info"):
            ctx_counts = {gid: len(msgs) for gid, msgs in self._group_context.items() if msgs}
            lines = [
                f"【Bot 状态】",
                f"版本: v4.1.0",
                f"风格: {self._current_style}",
                f"模型: {DEEPSEEK_MODEL}",
                f"触发词: {TRIGGER_WORD}",
                f"管理员: {TEST_ACCOUNT}",
                f"监听群: {len(TARGET_GROUP_IDS)} 个",
                f"上下文: {ctx_counts}",
            ]
            return "\n".join(lines)

        # === /清空上下文 ===
        if action in ("清空上下文", "清除上下文", "clear", "清空"):
            self._pending_action[TEST_ACCOUNT] = "clear_ctx"
            return "确认清空所有群的上下文？\n回复「确认」执行，其他取消"

        # === /清空指定群 ===
        clear_match = re.match(r'清空\s*(\d+)', action)
        if clear_match:
            gid = clear_match.group(1)
            if gid in self._group_context:
                self._group_context[gid].clear()
                return f"已清空群 {gid} 的上下文"
            else:
                return f"群 {gid} 不在监听列表中"

        # === /重载风格 ===
        if action in ("重载风格", "reload", "重载"):
            global STYLE_LIST, STYLE_NAMES
            STYLE_LIST = _load_prompts()
            STYLE_NAMES = [s["style_name"] for s in STYLE_LIST]
            return f"已重载 {len(STYLE_LIST)} 套风格: {', '.join(STYLE_NAMES)}"

        # === /监控群 /监听群列表 ===
        if action in ("群列表", "监听群", "groups"):
            lines = ["【监听群列表】"]
            for gid in TARGET_GROUP_IDS:
                count = len(self._group_context.get(gid, []))
                lines.append(f"  {gid} (上下文: {count}条)")
            return "\n".join(lines)

        # === /帮助 ===
        if action in ("帮助", "help", "指令"):
            lines = [
                "【管理员指令列表】",
                "",
                "--- 风格 ---",
                "/风格切换    打开风格选择面板",
                "/风格列表    查看所有可用风格",
                "/当前风格    查看当前使用的风格",
                "/重载风格    重新扫描 prompts/ 目录",
                "",
                "--- 管理 ---",
                "/状态        查看 bot 运行状态",
                "/群列表      查看监听的群",
                "/清空上下文  清空所有群聊上下文",
                "",
                "--- 动作指令（不需要/） ---",
                "怼 @某人     在群里怼人",
                "找某人聊天   主动搭话",
                "@某人 内容   对某人说话",
                "活跃一下     冒个泡",
                "去群里说xxx  在群里发言",
            ]
            return "\n".join(lines)

        return None

    async def _handle_pending_action(self, user_id: str, message: str) -> str | None:
        """
        处理等待中的管理员操作。
        返回 str 表示要发送的消息，None 表示不是待处理操作。
        """
        if user_id not in self._pending_action:
            return None

        action_type = self._pending_action.pop(user_id)
        message = message.strip()

        if action_type == "style_switch":
            if not message:
                return None
            # 精确匹配：只接受纯数字序号或完全匹配的风格名
            if message.isdigit():
                idx = int(message) - 1
                if 0 <= idx < len(STYLE_LIST):
                    target = STYLE_LIST[idx]["style_name"]
                else:
                    return f"无效序号，请输入 1-{len(STYLE_LIST)}"
            else:
                # 精确匹配风格名（不模糊）
                target = None
                for s in STYLE_LIST:
                    if message == s["style_name"]:
                        target = s["style_name"]
                        break
                if not target:
                    return "未匹配到风格，已取消"

            if target:
                old = self._current_style
                self._current_style = target
                emoji = self._get_style_data(target).get("emoji", "")
                logger.info(f"[WorkBuddy] Style switched: {old} -> {target}")
                return f"风格已切换: {emoji}{target}"
            return None

        if action_type == "clear_ctx":
            if message == "确认":
                for gid in self._group_context:
                    self._group_context[gid].clear()
                logger.info("[WorkBuddy] All contexts cleared by boss")
                return "已清空所有群的上下文"
            else:
                return "已取消"

        return None

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------

    def _is_duplicate(self, event: AstrMessageEvent) -> bool:
        try:
            msg_id = event.message_obj.message_id
            if not msg_id:
                return False
            if msg_id in self._processed_msgs:
                return True
            self._processed_msgs[msg_id] = time.time()
            now = time.time()
            expired = [k for k, v in self._processed_msgs.items() if now - v > 30]
            for k in expired:
                del self._processed_msgs[k]
            return False
        except Exception:
            return False

    def _record_context(self, group_id: str, sender_name: str, sender_id: str, text: str, at_targets: list = None, reply_to: str = None):
        if group_id in self._group_context:
            now = time.time()
            while self._group_context[group_id] and now - self._group_context[group_id][0][0] > CONTEXT_MAX_AGE:
                self._group_context[group_id].popleft()
            self._group_context[group_id].append((now, sender_name, sender_id, text, at_targets or [], reply_to))

    def _get_context_text(self, group_id: str, last_n: int = 10) -> str:
        if group_id not in self._group_context:
            return ""
        msgs = list(self._group_context[group_id])[-last_n:]
        if not msgs:
            return ""
        lines = []
        for ts, name, uid, text, at_targets, reply_to in msgs:
            ago = int(time.time() - ts)
            if ago < 60:
                time_str = "刚刚"
            elif ago < 3600:
                time_str = f"{ago // 60}分钟前"
            else:
                time_str = f"{ago // 3600}小时前"
            prefix = f"[{time_str}] {name}(ID:{uid})"
            if at_targets:
                at_names = []
                for at_qq in at_targets:
                    for _, n, u, _, _, _ in reversed(msgs):
                        if u == at_qq:
                            at_names.append(f"{n}({at_qq})")
                            break
                    else:
                        at_names.append(at_qq)
                prefix += f" @{','.join(at_names)}"
            if reply_to:
                prefix += f" [回复]"
            lines.append(f"{prefix}: {text}")
        return "\n".join(lines)

    def _parse_at_qq(self, raw_msg: str, exclude_self_id: str = None) -> list:
        qqs = re.findall(r'\[CQ:at,qq=(\d+)\]', raw_msg)
        if exclude_self_id:
            qqs = [q for q in qqs if q != str(exclude_self_id)]
        return qqs

    def _parse_reply_id(self, raw_msg: str) -> str:
        m = re.search(r'\[CQ:reply,id=(-?\d+)\]', raw_msg)
        return m.group(1) if m else ""

    async def _call_deepseek(self, user_message: str, system_prompt: str = None, context: str = None) -> str:
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            messages = [{"role": "system", "content": system_prompt or self._system_prompt}]
            if context:
                messages.append({
                    "role": "user",
                    "content": f"最近群聊记录（注意：'@某人'表示该消息在@某人，'ID:xxx'用于区分不同人）：\n{context}\n\n现在有人说：{user_message}\n\n要求：只回复1-2句短句，不超过40字，像QQ聊天一样自然随意。不要用emoji。接上话题节奏，注意区分不同群友说的话，搞清楚谁对谁说了什么再回复。"
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"{user_message}\n\n要求：只回复1-2句短句，不超过40字，像QQ聊天一样自然随意。不要用emoji。"
                })
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.85,
                "max_tokens": 80
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    else:
                        error_text = await resp.text()
                        logger.error(f"DeepSeek API error: {resp.status} - {error_text}")
                        return ""
        except Exception as e:
            logger.error(f"DeepSeek call error: {e}")
            return ""

    async def _napcat_api(self, endpoint: str, data: dict) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{NAPCAT_HTTP_API}/{endpoint}", json=data, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.error(f"[WorkBuddy] NapCat {endpoint} error {resp.status}")
                        return {}
        except Exception as e:
            logger.error(f"[WorkBuddy] NapCat {endpoint} error: {e}")
            return {}

    async def _send_group_msg(self, group_id, message: str, reply_msg_id: str = None):
        if reply_msg_id:
            full_msg = f"[CQ:reply,id={reply_msg_id}] {message}"
        else:
            full_msg = message
        result = await self._napcat_api("send_group_msg", {
            "group_id": int(group_id),
            "message": full_msg
        })
        if result and result.get("retcode") == 0:
            logger.info(f"[WorkBuddy] -> group {group_id}: {message[:60]}")
            return True
        else:
            logger.error(f"[WorkBuddy] Failed to send to group {group_id}: {result}")
            return False

    def _clean_response(self, text: str) -> str:
        cleaned = re.sub(
            r'[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffefa-zA-Z0-9\s，。？！、；：""''（）\[\]【】~…—]',
            '', text
        ).strip()
        return cleaned if cleaned else text.strip()

    # ----------------------------------------------------------
    # 动作指令处理（无 / 前缀，管理员专属）
    # 执行后静默完成，不返回确认文字
    # ----------------------------------------------------------

    async def _handle_action_command(self, command: str, raw_msg: str, self_id: str, from_group: str = None, source_msg_id: str = None) -> bool:
        cmd = command.strip()
        if not cmd or cmd.startswith("/"):
            return False

        at_qqs = self._parse_at_qq(raw_msg, exclude_self_id=self_id)
        reply_id = self._parse_reply_id(raw_msg)
        target_group = from_group

        # === 找xxx聊天 ===
        chat_match = re.search(r'(?:去|去和|去跟)?找\s*(.+?)\s*(?:聊天|说话|聊|说|扯淡|侃大山|唠嗑|搭话|聊两句)\s*[：:]*\s*(.*)', cmd)
        if chat_match:
            if not target_group:
                return True
            target_qq = at_qqs[0] if at_qqs else None
            target_name = chat_match.group(1).strip()
            chat_content = chat_match.group(2).strip() if chat_match.group(2) else ""

            if target_qq:
                ctx = self._get_context_text(target_group, 5)
                display_name = target_name
                for line in ctx.split('\n'):
                    if f'ID:{target_qq}' in line:
                        parts = line.split('] ')
                        if parts:
                            name_part = parts[-1].split('(')[0].strip()
                            if name_part:
                                display_name = name_part
                                break
                if chat_content:
                    prompt = f"你在群里主动找{display_name}(QQ:{target_qq})聊天，你要对他说：{chat_content}。用你的人设风格，1句话不超过30字。"
                else:
                    prompt = f"你在群里自然地找{display_name}(QQ:{target_qq})搭句话。用你的人设风格，1句话。"
                msg = await self._call_deepseek(prompt, self._boss_system_prompt, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
            else:
                ctx = self._get_context_text(target_group, 5)
                if chat_content:
                    prompt = f"你在群里主动找{target_name}聊天，你要对他说：{chat_content}。用你的人设风格，1句话不超过30字。"
                else:
                    prompt = f"你在群里自然地找{target_name}搭句话。用你的人设风格，1句话。"
                msg = await self._call_deepseek(prompt, self._boss_system_prompt, ctx)
                msg = self._clean_response(msg)
                if target_name.isdigit() and len(target_name) >= 6:
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_name}] {msg}")
                else:
                    await self._send_group_msg(target_group, msg)
            return True

        # === 怼 / 攻击 / 开喷 ===
        if re.match(r'^(?:怼|攻击|开喷|输出|喷|骂|干)', cmd):
            if not target_group:
                return True
            target_qq = at_qqs[0] if at_qqs else None
            content = re.sub(r'^(?:怼|攻击|开喷|输出|喷|骂|干|给)\s*', '', cmd).strip()
            content = re.sub(r'\[CQ:at,qq=\d+\]', '', content).strip()
            content = re.sub(r'@\S+\s*', '', content).strip()
            content = re.sub(r'(全力一击|最强|给我|狠狠|全力|用你的|一下|吧|呢|啊|呀|哈|哈)', '', content).strip()
            content = re.sub(r'^(一下|一击|一波|一顿|一场)', '', content).strip()
            ctx = self._get_context_text(target_group, 8)

            if target_qq:
                target_msg = ""
                if ctx and target_qq in ctx:
                    for line in ctx.split('\n'):
                        if f'ID:{target_qq}' in line:
                            target_msg = line.split(': ', 1)[-1] if ': ' in line else ""
                            break
                if content:
                    prompt = f"群里有个人的QQ号是{target_qq}，他之前说过：「{target_msg}」。现在你要怼他，理由是：{content}。你的人设风格，1句话不超过30字。要结合他之前说的话来怼。"
                elif target_msg:
                    prompt = f"群里有个人的QQ号是{target_qq}，他最近说过：「{target_msg}」。根据他说的内容犀利地怼他一句，你的人设风格，1句话不超过30字。"
                else:
                    prompt = f"你要在群里怼一个人，你的人设风格，犀利但朋友互损级别，1句话不超过30字。"
                roast_msg = await self._call_deepseek(prompt, self._system_prompt, ctx if not target_msg else None)
            else:
                if content:
                    prompt = f"你要在群里开喷，理由是：{content}。你的人设风格，犀利但朋友互损级别，1句话不超过30字。"
                    roast_msg = await self._call_deepseek(prompt, self._system_prompt, ctx)
                else:
                    roasts = [
                        "你搁这整活呢 属实逆天了",
                        "这发言我蚌埠住了",
                        "你清醒一点",
                        "纯纯的抽象",
                        "不是哥们你在干嘛呢",
                        "什么成分啊这是",
                        "有一说一有点东西但不多",
                        "绷不住了兄弟",
                    ]
                    roast_msg = random.choice(roasts)

            roast_msg = self._clean_response(roast_msg)
            await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {roast_msg}" if target_qq else roast_msg)
            return True

        # === 有@其他人 ===
        if at_qqs:
            if not target_group:
                return True
            target_qq = at_qqs[0]
            content = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', raw_msg).strip()
            content = re.sub(r'\[CQ:reply,id=-?\d+\]\s*', '', content).strip()
            content = re.sub(r'^[\s：:]+', '', content).strip()
            is_quote_cmd = re.match(r'^(引用|回复|回|quote|reply)\b', content)

            if is_quote_cmd or (not content and reply_id):
                if source_msg_id:
                    ctx = self._get_context_text(target_group, 5)
                    prompt = f"你在群里要转发/引用一条消息说：{content if not is_quote_cmd else ''}。你的人设风格，1句话不超过30字。"
                    msg = await self._call_deepseek(prompt, self._system_prompt, ctx)
                    msg = self._clean_response(msg)
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
                else:
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] 咋了")
                return True

            if content and len(content) > 0:
                ctx = self._get_context_text(target_group, 5)
                prompt = f"你在群里对一个人说：{content}。你的人设风格，1句话不超过30字。"
                msg = await self._call_deepseek(prompt, self._system_prompt, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
                return True

        # === 引用指令 ===
        if re.match(r'^(引用|回复|回)\b', cmd) and reply_id:
            if not target_group:
                return True
            await self._send_group_msg(target_group, "有被引用到 确实")
            return True

        # === 活跃一下 ===
        if re.search(r'(活跃|冒个泡|刷个存在|去群里冒泡|整点动静)', cmd):
            if not target_group:
                target_group = random.choice(TARGET_GROUP_IDS)
            ctx = self._get_context_text(target_group, 5)
            ai_msg = await self._call_deepseek("自然地参与一下当前话题，随便说一句", self._system_prompt, ctx)
            msg = self._clean_response(ai_msg) if ai_msg else "你们在聊啥呢"
            await self._send_group_msg(target_group, msg)
            return True

        # === 别理xxx ===
        if re.search(r'(?:别理|忽略|不要理|拉黑|不理)', cmd):
            return True

        # === 去群里说xxx ===
        say_match = re.search(r'(?:去群里?|在群里|群里?)\s*(?:说|发|讲)?\s*(.+)', cmd)
        if say_match:
            if not target_group:
                return True
            content = say_match.group(1).strip()
            if content:
                ctx = self._get_context_text(target_group, 5)
                prompt = f"你要在群里说：{content}。用你的人设风格改写，1句话不超过30字，保持原意。"
                msg = await self._call_deepseek(prompt, self._system_prompt, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, msg)
            return True

        return False

    # ----------------------------------------------------------
    # 主消息处理
    # ----------------------------------------------------------

    @filter.event_message_type(EventMessageType.ALL)
    async def on_all_messages(self, event: AstrMessageEvent):
        try:
            if self._is_duplicate(event):
                return

            message = event.get_message_str()
            user_id = event.get_sender_id()
            sender_name = event.get_sender_name()
            group_id = event.get_group_id()
            is_private = event.is_private_chat()

            self_id = event.get_self_id()
            if user_id == self_id:
                return

            raw_msg = ""
            source_msg_id = ""
            try:
                raw_msg = str(event.message_obj.raw_message) if hasattr(event, 'message_obj') else message
                source_msg_id = str(event.message_obj.message_id) if hasattr(event, 'message_obj') else ""
            except Exception:
                raw_msg = message

            # 记录上下文
            if group_id in TARGET_GROUP_IDS:
                clean_for_ctx = re.sub(r'\[CQ:[^\]]+\]', '', raw_msg).strip()
                at_targets_all = self._parse_at_qq(raw_msg)
                reply_to = self._parse_reply_id(raw_msg)
                self._record_context(group_id, sender_name, str(user_id), clean_for_ctx, at_targets_all, reply_to)

            is_boss = (str(user_id) == TEST_ACCOUNT)

            # ========== 等待中的管理员操作（优先级最高） ==========
            if is_boss and str(user_id) in self._pending_action:
                reply = await self._handle_pending_action(str(user_id), message)
                if reply:
                    if group_id:
                        await self._send_group_msg(group_id, reply)
                    else:
                        yield event.plain_result(reply)
                return

            # 检查@
            has_at = False
            try:
                if self_id and f"[CQ:at,qq={self_id}]" in raw_msg:
                    has_at = True
            except Exception:
                pass
            is_at = event.is_at_or_wake_command if hasattr(event, 'is_at_or_wake_command') else False

            # ========== 判断是否需要回复 ==========
            should_process = False

            if group_id in TARGET_GROUP_IDS:
                if TRIGGER_WORD in message or has_at or is_at:
                    should_process = True
                    if TRIGGER_WORD in message:
                        message = message.replace(TRIGGER_WORD, "").strip()
                        message = re.sub(r'^[，,、\s]+', '', message)
                    if self_id:
                        message = re.sub(rf'\[CQ:at,qq={self_id}\]\s*', '', raw_msg).strip()
                        if not message:
                            message = re.sub(r'\[CQ:[^\]]+\]', '', raw_msg).strip()
                            message = message.replace(TRIGGER_WORD, "").strip()
                            message = re.sub(r'^[，,、\s]+', '', message)

            elif is_private and is_boss:
                should_process = True

            elif is_private and TRIGGER_WORD in message:
                should_process = True
                message = message.replace(TRIGGER_WORD, "").strip()
                message = re.sub(r'^[，,、\s]+', '', message)

            if not should_process:
                return

            if not message.strip():
                message = "你好"

            logger.info(f"[WorkBuddy] Processing from {sender_name}({user_id}): {message[:50]}")

            # ========== 管理员消息处理 ==========
            if is_boss:
                # 1. 尝试系统指令（/开头）
                sys_reply = await self._handle_system_command(message, from_group=group_id)
                if sys_reply is not None:
                    # 系统指令有回复
                    if group_id:
                        await self._send_group_msg(group_id, sys_reply)
                    else:
                        yield event.plain_result(sys_reply)
                    return

                # 2. 尝试动作指令（静默执行）
                is_cmd = await self._handle_action_command(
                    message, raw_msg,
                    self_id=str(self_id),
                    from_group=group_id,
                    source_msg_id=source_msg_id
                )
                if is_cmd:
                    return

                # 3. 普通聊天
                response = await self._call_deepseek(message, self._boss_system_prompt)
            else:
                # 普通用户
                ctx = self._get_context_text(group_id, 10) if group_id else None
                response = await self._call_deepseek(message, self._system_prompt, context=ctx)

            if not response:
                return

            response = self._clean_response(response)
            if not response:
                return

            yield event.plain_result(response)
            logger.info(f"[WorkBuddy] -> {sender_name}: {response[:30]}")

        except Exception as e:
            logger.error(f"[WorkBuddy] Error: {e}")
            import traceback
            logger.error(traceback.format_exc())
