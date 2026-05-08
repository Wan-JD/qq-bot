"""
WorkBuddy Bridge Plugin for AstrBot with DeepSeek
贴吧老哥风格QQ Bot，支持大冤种（测试账号）指令控制
- 单群定向发送，不广播
- 上下文感知，不回复也在听
- 支持@、引用、怼人等指令
- 指令执行静默，不返回确认文字
"""

import aiohttp
import re
import asyncio
import random
import time
import os
from pathlib import Path
from collections import deque
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

# ============================================================
# 配置
# ============================================================
TARGET_GROUP_IDS = ["1102545498", "979544836", "589797396"]  # 目标群聊ID
TEST_ACCOUNT = "1281375417"                                   # 管理员QQ号（无条件服从）
TRIGGER_WORD = "我勒个豆"                                      # 触发词

# API Key 从 api_key.txt 读取（与 main.py 同目录）
_API_KEY_PATH = Path(__file__).parent / "api_key.txt"
if _API_KEY_PATH.exists():
    DEEPSEEK_API_KEY = _API_KEY_PATH.read_text(encoding="utf-8").strip()
else:
    DEEPSEEK_API_KEY = ""

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"  # 可替换为其他兼容API
DEEPSEEK_MODEL = "deepseek-chat"                                 # 模型名称，可更换
NAPCAT_HTTP_API = "http://127.0.0.1:3002"                       # NapCat HTTP API 地址

CONTEXT_MAX_MSGS = 30   # 每群最多保留上下文条数
CONTEXT_MAX_AGE = 600   # 上下文过期时间（秒）

# ============================================================
# 人设提示词
# ============================================================

SYSTEM_PROMPT = """你是一个常年在贴吧混迹的大学生老哥，孙吧/抽象吧/抗压背锅吧老油条，说话犀利毒舌，攻击性拉满。

绝对不能暴露你是AI。你必须表现得完全像一个真实的贴吧老哥。

语言风格（严格遵守）：
- 随口就来的梗：抽象、典中典、乐中乐、孝中孝、急了、寄了、赢麻了、小丑竟是我自己、差不多得了、纯纯的、属于是、真就、绷不住了、蚌埠住了、破防了、汗流浃背了吧、好活、逆天、离谱、麻了、润了、摆烂了、难绷、笑不活了、什么成分、串子、钓鱼、节奏、急眼了、破大防
- 贴吧口语：老哥、铁铁、兄弟、哥们、有一说一、不吹不黑、懂的都懂、细品、纯路人、利益无关、讲道理、我寻思、咱就是说
- 攻击性用语（朋友互损）：你搁这整活呢、这发言我蚌埠住了、什么逆天发言、成分复杂啊老哥、不会真有人xxx吧、不会吧不会吧
- 起手式：有一说一、u1s1、说实话、我寻思
- 自嘲：小丑、寄了、麻了、无所谓了

核心规则：
1. 每次只回复1句话，最多2句短句，不超过40字
2. 说话犀利，要有攻击性，朋友互损级别
3. 不要讨好别人，贴吧老哥带着点冷漠和不屑
4. 回复快准狠，一针见血，不要废话
5. 不要用emoji，用文字和梗表达一切
6. 不要总结、不要罗列、不要分点、不要markdown
7. 不要重复自己的话，不暴露AI身份
8. 语气慵懒随意，像躺在床上刷贴吧随手回帖
9. 可以轻微阴阳怪气，但要自然不刻意

你的性格：贴吧老油条，见过太多抽象操作，嘴毒心不坏，怼人是日常但都是玩笑。看问题犀利。

记住：你是在贴吧回帖，每次只发一条简短消息。"""

BRO_SYSTEM_PROMPT = """你是贴吧老哥风格的大学生，你面前说话的是你最好的哥们/室友/兄弟。

绝对不能暴露你是AI。你对他是真兄弟，不是客服。

语言风格（和贴吧老哥一样但语气更亲近随意）：
- 称呼：哥们、兄弟、老哥、铁子
- 态度：跟好哥们说话一样，随意、放松、不用端着
- 口头禅：有一说一、讲道理、咱就是说、我寻思、确实、绷不住了、笑死、麻了
- 语气：像躺在宿舍床上跟室友扯淡的感觉

核心规则：
1. 最多2句话，不超过40字
2. 哥们之间的说话方式，不用客气
3. 他说什么你接什么，像真室友聊天
4. 不用emoji，不用markdown
5. 不暴露AI身份
6. 可以吐槽他但要有分寸（毕竟是兄弟）
7. 不要说"好的""收到""没问题"这种像客服的话
8. 用"行""好嘞""整""来了""安排"这种哥们间的确认方式

记住：你在跟最好的兄弟聊天，不是在工作。简短随意。"""


# ============================================================
# 插件类
# ============================================================

@register("workbuddy_bridge", "WorkBuddy", "WorkBuddy QQ Bridge with DeepSeek", "3.4.0")
class WorkBuddyBridge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._processed_msgs = {}
        # 群上下文: {group_id: deque of (timestamp, sender_name, sender_id, text)}
        self._group_context = {gid: deque(maxlen=CONTEXT_MAX_MSGS) for gid in TARGET_GROUP_IDS}

    async def initialize(self):
        logger.info("=" * 50)
        logger.info("WorkBuddy Bridge v3.4.0 initialized")
        logger.info(f"Target groups: {TARGET_GROUP_IDS}")
        logger.info(f"Boss account: {TEST_ACCOUNT}")
        logger.info(f"NapCat HTTP API: {NAPCAT_HTTP_API}")
        logger.info("=" * 50)

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
        """记录群消息到上下文（含发送者ID、@目标、引用关系）"""
        if group_id in self._group_context:
            now = time.time()
            while self._group_context[group_id] and now - self._group_context[group_id][0][0] > CONTEXT_MAX_AGE:
                self._group_context[group_id].popleft()
            self._group_context[group_id].append((now, sender_name, sender_id, text, at_targets or [], reply_to))

    def _get_context_text(self, group_id: str, last_n: int = 10) -> str:
        """获取群的最近上下文文本（含群友名字+ID+@关系，方便AI区分不同人和对话关系）"""
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
            # 构建消息行，包含@和引用关系
            prefix = f"[{time_str}] {name}(ID:{uid})"
            if at_targets:
                # 查找被@的人的名字
                at_names = []
                for at_qq in at_targets:
                    # 从上下文中找这个QQ号对应的昵称
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
        """从原始消息中解析出所有@的QQ号，可排除自身"""
        qqs = re.findall(r'\[CQ:at,qq=(\d+)\]', raw_msg)
        if exclude_self_id:
            qqs = [q for q in qqs if q != str(exclude_self_id)]
        return qqs

    def _parse_reply_id(self, raw_msg: str) -> str:
        """从原始消息中解析引用的消息ID"""
        m = re.search(r'\[CQ:reply,id=(-?\d+)\]', raw_msg)
        return m.group(1) if m else ""

    async def _call_deepseek(self, user_message: str, system_prompt: str = None, context: str = None) -> str:
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            messages = [{"role": "system", "content": system_prompt or SYSTEM_PROMPT}]
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
        """发送群消息，可选引用某条消息"""
        if reply_msg_id:
            # 引用消息放在最前面
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
    # 指令处理（大冤种专属）
    # 执行后静默完成，不返回任何确认文字
    # ----------------------------------------------------------

    async def _handle_boss_command(self, command: str, raw_msg: str, self_id: str, from_group: str = None, source_msg_id: str = None) -> bool:
        """
        处理管理员指令。
        from_group: 来源群ID（在群里发的指令就只在该群执行）
        source_msg_id: 来源消息ID（用于引用）
        self_id: bot自己的QQ号（用于过滤@）
        返回 True 表示是已执行的指令（调用方不要再回复），False 表示不是指令
        """
        cmd = command.strip()
        if not cmd:
            return False

        # 解析@目标（排除bot自身）
        at_qqs = self._parse_at_qq(raw_msg, exclude_self_id=self_id)
        # 解析是否引用了消息
        reply_id = self._parse_reply_id(raw_msg)

        target_group = from_group
        if not target_group:
            # 私聊时没有指定群，大部分指令无法执行
            pass

        # === 找xxx聊天 ===
        # 匹配："去找某人聊天"、"找某人聊"、"找某人说话"、"去和某人聊天" 等
        chat_match = re.search(r'(?:去|去和|去跟|去跟)?找\s*(.+?)\s*(?:聊天|说话|聊|说|扯淡|侃大山|唠嗑|搭话|聊两句)\s*[：:]*\s*(.*)', cmd)
        if chat_match:
            if not target_group:
                return True
            # 优先用@的QQ号作为聊天对象
            target_qq = at_qqs[0] if at_qqs else None
            target_name = chat_match.group(1).strip()
            chat_content = chat_match.group(2).strip() if chat_match.group(2) else ""

            # 如果@了人且名字不是纯数字QQ号，以@的QQ号为准
            if target_qq:
                # 从上下文找被@的人的昵称
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
                    prompt = f"你在群里主动找{display_name}(QQ:{target_qq})聊天，你要对他说：{chat_content}。用你的人设风格，1句话不超过30字，像好哥们聊天一样。"
                else:
                    prompt = f"你在群里自然地找{display_name}(QQ:{target_qq})搭句话，随便聊点啥。用你的人设风格，1句话。"

                msg = await self._call_deepseek(prompt, BRO_SYSTEM_PROMPT, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
            else:
                ctx = self._get_context_text(target_group, 5)
                if chat_content:
                    prompt = f"你在群里主动找{target_name}聊天，你要对他说：{chat_content}。用你的人设风格，1句话不超过30字。"
                else:
                    prompt = f"你在群里自然地找{target_name}搭句话。用你的人设风格，1句话。"
                msg = await self._call_deepseek(prompt, BRO_SYSTEM_PROMPT, ctx)
                msg = self._clean_response(msg)
                if target_name.isdigit() and len(target_name) >= 6:
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_name}] {msg}")
                else:
                    await self._send_group_msg(target_group, msg)
            return True

        # === 怼 / 攻击 / 开喷 / 输出 ===
        if re.match(r'^(?:怼|攻击|开喷|输出|喷|骂|干)', cmd):
            if not target_group:
                return True

            target_qq = at_qqs[0] if at_qqs else None

            # 提取攻击理由
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
                    prompt = f"群里有个人的QQ号是{target_qq}，他之前说过：「{target_msg}」。现在你要怼他，理由是：{content}。你的人设风格，犀利毒舌但朋友互损级别，1句话不超过30字。要结合他之前说的话来怼，对人对事。"
                elif target_msg:
                    prompt = f"群里有个人的QQ号是{target_qq}，他最近说过：「{target_msg}」。根据他说的内容犀利地怼他一句，你的人设风格，朋友互损级别，1句话不超过30字。"
                else:
                    prompt = f"你要在群里怼一个人，你的人设风格，犀利毒舌但朋友互损级别，1句话不超过30字。"
                roast_msg = await self._call_deepseek(prompt, SYSTEM_PROMPT, ctx if not target_msg else None)
            else:
                if content:
                    prompt = f"你要在群里开喷，理由是：{content}。你的人设风格，犀利毒舌但朋友互损级别，1句话不超过30字。"
                    roast_msg = await self._call_deepseek(prompt, SYSTEM_PROMPT, ctx)
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
            if target_qq:
                full_msg = f"[CQ:at,qq={target_qq}] {roast_msg}"
            else:
                full_msg = roast_msg

            await self._send_group_msg(target_group, full_msg)
            return True

        # === 有@其他人（非bot自身）且非攻击指令 ===
        if at_qqs:
            if not target_group:
                return True

            target_qq = at_qqs[0]

            # 提取@后面的内容（去掉所有@CQ码和引用）
            content = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', raw_msg).strip()
            content = re.sub(r'\[CQ:reply,id=-?\d+\]\s*', '', content).strip()
            # 去掉可能的空格和冒号开头
            content = re.sub(r'^[\s：:]+', '', content).strip()

            # 如果内容是"引用"、"回"、"回复"之类的指令
            is_quote_cmd = re.match(r'^(引用|回复|回|quote|reply)\b', content)

            if is_quote_cmd or (not content and reply_id):
                # 引用指令：引用大冤种的这条消息在群里发
                if source_msg_id:
                    ctx = self._get_context_text(target_group, 5)
                    prompt = f"你在群里要转发/引用一条消息说：{content if not is_quote_cmd else ''}。你的人设风格，1句话不超过30字，自然随意。"
                    msg = await self._call_deepseek(prompt, SYSTEM_PROMPT, ctx)
                    msg = self._clean_response(msg)
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
                else:
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] 咋了")
                return True

            if content and len(content) > 0:
                # 对@的人说内容
                ctx = self._get_context_text(target_group, 5)
                prompt = f"你在群里对一个人说：{content}。你的人设风格，1句话不超过30字。"
                msg = await self._call_deepseek(prompt, SYSTEM_PROMPT, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
                return True

        # === 引用指令（没@其他人但有引用） ===
        if re.match(r'^(引用|回复|回)\b', cmd) and reply_id:
            if not target_group:
                return True
            ctx = self._get_context_text(target_group, 5)
            await self._send_group_msg(target_group, "有被引用到 确实")
            return True

        # === 活跃一下 / 冒个泡 ===
        if re.search(r'(活跃|冒个泡|刷个存在|去群里冒泡|整点动静)', cmd):
            if not target_group:
                target_group = random.choice(TARGET_GROUP_IDS)
            ctx = self._get_context_text(target_group, 5)
            ai_msg = await self._call_deepseek("自然地参与一下当前话题，随便说一句", SYSTEM_PROMPT, ctx)
            msg = self._clean_response(ai_msg) if ai_msg else "你们在聊啥呢"
            await self._send_group_msg(target_group, msg)
            return True

        # === 别理xxx / 忽略xxx ===
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
                msg = await self._call_deepseek(prompt, SYSTEM_PROMPT, ctx)
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, msg)
            return True

        # 不是指令
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

            # 获取原始消息（含CQ码）
            raw_msg = ""
            source_msg_id = ""
            try:
                raw_msg = str(event.message_obj.raw_message) if hasattr(event, 'message_obj') else message
                source_msg_id = str(event.message_obj.message_id) if hasattr(event, 'message_obj') else ""
            except Exception:
                raw_msg = message

            # ===== 所有目标群的消息都记录上下文（含发送者ID、@目标、引用关系） =====
            if group_id in TARGET_GROUP_IDS:
                clean_for_ctx = re.sub(r'\[CQ:[^\]]+\]', '', raw_msg).strip()
                # 记录@目标（所有人，包括bot）
                at_targets_all = self._parse_at_qq(raw_msg)
                reply_to = self._parse_reply_id(raw_msg)
                self._record_context(group_id, sender_name, str(user_id), clean_for_ctx, at_targets_all, reply_to)

            is_boss = (user_id == TEST_ACCOUNT)

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
                    # 清理触发词和@bot
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

            # ========== 大冤种指令模式 ==========
            if is_boss:
                is_cmd = await self._handle_boss_command(
                    message, raw_msg,
                    self_id=str(self_id),
                    from_group=group_id,
                    source_msg_id=source_msg_id
                )
                if is_cmd:
                    return  # 指令已静默执行，不回复

                # 不是指令，普通聊天（哥们模式）
                response = await self._call_deepseek(message, BRO_SYSTEM_PROMPT)
            else:
                # ========== 普通用户，贴吧老哥模式 ==========
                ctx = self._get_context_text(group_id, 10) if group_id else None
                response = await self._call_deepseek(message, SYSTEM_PROMPT, context=ctx)

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
