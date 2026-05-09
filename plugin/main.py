"""
WorkBuddy Bridge Plugin for AstrBot with OpenAI-compatible LLM
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
from datetime import datetime
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
    LLM_API_KEY = _API_KEY_PATH.read_text(encoding="utf-8").strip()
else:
    LLM_API_KEY = _config.get("api_key", "")

TARGET_GROUP_IDS = [str(g) for g in _config.get("target_groups", [])]
TEST_ACCOUNT = str(_config.get("boss_qq", ""))
TRIGGER_WORD = _config.get("trigger_word", "")
LLM_API_URL = _config.get("llm_api_url") or _config.get("deepseek_api_url", "https://api.deepseek.com/chat/completions")
LLM_MODEL = _config.get("llm_model") or _config.get("deepseek_model", "deepseek-chat")
NAPCAT_HTTP_API = _config.get("napcat_http_api", "http://127.0.0.1:3002")
DEFAULT_STYLE = _config.get("default_style", "贴吧老哥")
GROUP_SETTINGS = {str(k): v for k, v in _config.get("group_settings", {}).items()}

# Backward-compatible aliases for older local snippets or logs.
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_API_URL = LLM_API_URL
DEEPSEEK_MODEL = LLM_MODEL

CONTEXT_MAX_MSGS = int(_config.get("context_max_msgs", 30))
CONTEXT_MAX_AGE = int(_config.get("context_max_age", 600))
AUTO_REPLY_ENABLED = bool(_config.get("auto_reply_enabled", False))
AUTO_REPLY_CHANCE = float(_config.get("auto_reply_chance", 0.02))
AUTO_REPLY_KEYWORD_CHANCE = float(_config.get("auto_reply_keyword_chance", 0.35))
AUTO_REPLY_COOLDOWN = int(_config.get("auto_reply_cooldown", 180))
MEME_KEYWORDS = _config.get(
    "meme_keywords",
    ["绷不住", "抽象", "逆天", "典中典", "离谱", "笑死", "蚌埠住", "急了"],
)
SCHEDULED_FUN_ENABLED = bool(_config.get("scheduled_fun_enabled", False))
SCHEDULED_FUN_TIMES = _config.get("scheduled_fun_times", ["22:00"])
DAILY_STYLE_ROTATION_ENABLED = bool(_config.get("daily_style_rotation_enabled", False))
DAILY_STYLE_ANNOUNCE = bool(_config.get("daily_style_announce", True))

_MEMORY_PATH = _PLUGIN_DIR / "workbuddy_memory.json"


# ============================================================
# 本地记忆（群友画像、黑话词典、名场面、统计数据）
# ============================================================

def _empty_memory() -> dict:
    return {
        "profiles": {},
        "moments": {},
        "glossary": {},
        "aliases": {},
        "stats": {},
        "daily_fun_sent": {},
        "last_daily_style_date": "",
    }


def _load_memory() -> dict:
    if not _MEMORY_PATH.exists():
        return _empty_memory()
    try:
        data = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
        base = _empty_memory()
        for key, value in data.items():
            base[key] = value
        return base
    except Exception as e:
        logger.error(f"[WorkBuddy] Failed to load workbuddy_memory.json: {e}")
        return _empty_memory()


def _save_memory(memory: dict):
    try:
        _MEMORY_PATH.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"[WorkBuddy] Failed to save workbuddy_memory.json: {e}")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")

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

@register("workbuddy_bridge", "WorkBuddy", "WorkBuddy QQ Bridge with OpenAI-compatible LLM", "4.1.0")
class WorkBuddyBridge(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._processed_msgs = {}
        self._group_context = {gid: deque(maxlen=CONTEXT_MAX_MSGS) for gid in TARGET_GROUP_IDS}
        self._memory = _load_memory()
        self._current_style = DEFAULT_STYLE
        self._group_styles = {
            gid: GROUP_SETTINGS.get(gid, {}).get("default_style", DEFAULT_STYLE)
            for gid in TARGET_GROUP_IDS
        }
        self._runtime_styles = {}
        self._games = {}
        self._last_auto_reply_at = {}
        self._scheduler_task = None
        # 等待状态：{user_id: {"type": "...", "group_id": "..."}}
        self._pending_action = {}

    def _get_style_data(self, style_name: str) -> dict:
        """按名称查找风格数据，包含运行时融合人设。"""
        if style_name in self._runtime_styles:
            return self._runtime_styles[style_name]
        for s in STYLE_LIST:
            if s.get("style_name") == style_name:
                return s
        return {}

    def _group_config(self, group_id: str | None) -> dict:
        return GROUP_SETTINGS.get(str(group_id), {}) if group_id else {}

    def _get_current_style_name(self, group_id: str | None = None) -> str:
        if group_id:
            return self._group_styles.get(str(group_id), DEFAULT_STYLE)
        return self._current_style

    def _set_current_style(self, style_name: str, group_id: str | None = None):
        if group_id:
            self._group_styles[str(group_id)] = style_name
        else:
            self._current_style = style_name

    def _get_prompt(self, group_id: str | None = None, style_name: str | None = None, boss: bool = False) -> str:
        style = self._get_style_data(style_name or self._get_current_style_name(group_id))
        if boss:
            return style.get("boss_system_prompt") or style.get("system_prompt", "你是一个群聊机器人。")
        return style.get("system_prompt", "你是一个群聊机器人，自然地回复即可。")

    @property
    def _system_prompt(self) -> str:
        return self._get_prompt()

    @property
    def _boss_system_prompt(self) -> str:
        return self._get_prompt(boss=True)

    def _config_warnings(self) -> list[str]:
        warnings = []
        if not LLM_API_KEY:
            warnings.append("LLM API Key 未配置")
        if not TARGET_GROUP_IDS:
            warnings.append("target_groups 为空")
        if TEST_ACCOUNT == "":
            warnings.append("boss_qq 未配置")
        all_styles = set(STYLE_NAMES) | set(self._runtime_styles.keys())
        if DEFAULT_STYLE not in all_styles:
            warnings.append(f"default_style 不存在: {DEFAULT_STYLE}")
        for gid, cfg in GROUP_SETTINGS.items():
            style = cfg.get("default_style")
            if style and style not in all_styles:
                warnings.append(f"群 {gid} default_style 不存在: {style}")
        return warnings

    async def initialize(self):
        logger.info("=" * 50)
        logger.info("WorkBuddy Bridge v4.1.0 initialized")
        logger.info(f"Target groups: {TARGET_GROUP_IDS}")
        logger.info(f"Boss account: {TEST_ACCOUNT}")
        logger.info(f"Current style: {self._current_style}")
        logger.info(f"Group styles: {self._group_styles}")
        logger.info(f"Available styles ({len(STYLE_LIST)}): {STYLE_NAMES}")
        logger.info(f"LLM: {LLM_MODEL} @ {LLM_API_URL}")
        logger.info(f"NapCat HTTP API: {NAPCAT_HTTP_API}")
        for warning in self._config_warnings():
            logger.warning(f"[WorkBuddy] Config warning: {warning}")
        if SCHEDULED_FUN_ENABLED or DAILY_STYLE_ROTATION_ENABLED:
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("=" * 50)

    async def terminate(self):
        if self._scheduler_task:
            self._scheduler_task.cancel()
        _save_memory(self._memory)


    # ----------------------------------------------------------
    # 玩法与记忆能力
    # ----------------------------------------------------------

    def _memory_bucket(self, key: str, group_id: str) -> dict:
        return self._memory.setdefault(key, {}).setdefault(str(group_id), {})

    def _get_alias(self, group_id: str, user_id: str, fallback: str = "") -> str:
        alias = self._memory_bucket("aliases", group_id).get(str(user_id))
        if alias:
            return alias
        return fallback or str(user_id)

    def _record_group_memory(self, group_id: str, sender_name: str, sender_id: str, text: str, at_targets: list):
        if not group_id or not sender_id:
            return
        stats = self._memory_bucket("stats", group_id)
        user = stats.setdefault(str(sender_id), {
            "message_count": 0,
            "mention_count": 0,
            "names": {},
            "last_seen": "",
        })
        user["message_count"] = int(user.get("message_count", 0)) + 1
        user["last_seen"] = _now_text()
        names = user.setdefault("names", {})
        if sender_name:
            names[sender_name] = int(names.get(sender_name, 0)) + 1
        for target in at_targets or []:
            target_stats = stats.setdefault(str(target), {
                "message_count": 0,
                "mention_count": 0,
                "names": {},
                "last_seen": "",
            })
            target_stats["mention_count"] = int(target_stats.get("mention_count", 0)) + 1
        profiles = self._memory_bucket("profiles", group_id)
        profile = profiles.setdefault(str(sender_id), {"recent_samples": []})
        samples = profile.setdefault("recent_samples", [])
        clean = text.strip()
        if clean:
            samples.append(clean[:80])
            del samples[:-8]
        _save_memory(self._memory)

    def _group_glossary_text(self, group_id: str | None) -> str:
        if not group_id:
            return ""
        glossary = self._memory_bucket("glossary", group_id)
        if not glossary:
            return ""
        lines = [f"{k}: {v}" for k, v in list(glossary.items())[:20]]
        return "群内黑话词典：\n" + "\n".join(lines)

    def _context_with_memory(self, group_id: str | None, last_n: int = 10) -> str:
        ctx = self._get_context_text(group_id, last_n) if group_id else ""
        glossary = self._group_glossary_text(group_id)
        if glossary and ctx:
            return f"{glossary}\n\n最近聊天：\n{ctx}"
        return glossary or ctx

    def _target_from_message(self, raw_msg: str, text: str, self_id: str | None = None) -> str:
        at_qqs = self._parse_at_qq(raw_msg, exclude_self_id=self_id)
        if at_qqs:
            return at_qqs[0]
        m = re.search(r'(\d{5,})', text)
        return m.group(1) if m else ""

    def _find_style_name(self, text: str) -> str:
        text = text.strip()
        for name in list(STYLE_NAMES) + list(self._runtime_styles.keys()):
            if text == name or name in text:
                return name
        return ""

    def _find_recent_message_for_moment(self, group_id: str, target_qq: str = "") -> dict | None:
        msgs = list(self._group_context.get(group_id, []))
        for ts, name, uid, text, at_targets, reply_to in reversed(msgs):
            if target_qq and str(uid) != str(target_qq):
                continue
            clean = text.strip()
            if not clean:
                continue
            if str(uid) == TEST_ACCOUNT and (clean.startswith("/") or "记下来" in clean):
                continue
            return {
                "time": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                "name": name,
                "user_id": str(uid),
                "text": clean[:500],
            }
        return None

    async def _summarize_today(self, group_id: str, mode: str = "贴吧") -> str:
        ctx = self._context_with_memory(group_id, CONTEXT_MAX_MSGS)
        if not ctx:
            return "今天还没攒下什么可总结的聊天记录"
        prompt = (
            f"把下面群聊做一个{mode}风格的今日总结。"
            "要像群友复盘，不要像工作报告。包含：今日主题、名场面、离谱指数、最后一句短评。"
            "总长不超过220字。\n\n"
            f"{ctx}"
        )
        return self._clean_response(await self._call_llm(prompt, self._get_prompt(group_id, boss=True), max_tokens=360))

    async def _profile_user(self, group_id: str, user_id: str) -> str:
        stats = self._memory_bucket("stats", group_id).get(str(user_id), {})
        profile = self._memory_bucket("profiles", group_id).get(str(user_id), {})
        alias = self._get_alias(group_id, user_id)
        samples = "\n".join(profile.get("recent_samples", [])[-8:])
        prompt = (
            f"根据群聊统计和发言样本，给 QQ:{user_id}（昵称/外号：{alias}）写一份群友画像。"
            "风格要像群里熟人锐评，友好但有梗。包含：人设标签3个、聊天习惯、可能的隐藏属性、一句短评。"
            "不要编造敏感隐私，总长不超过180字。\n\n"
            f"统计：{json.dumps(stats, ensure_ascii=False)}\n发言样本：\n{samples}"
        )
        return self._clean_response(await self._call_llm(prompt, self._get_prompt(group_id, boss=True), max_tokens=300))

    async def _summon_user(self, group_id: str, user_id: str, reason: str) -> str:
        alias = self._get_alias(group_id, user_id)
        ctx = self._context_with_memory(group_id, 8)
        prompt = (
            f"你要在群里自然地召唤 {alias}(QQ:{user_id}) 出来聊天。"
            f"理由：{reason or '没有特别理由，就是想让他出来说句话'}。"
            "写一句像熟人点名的话，别像通知，不超过35字。"
        )
        msg = self._clean_response(await self._call_llm(prompt, self._get_prompt(group_id, boss=True), ctx, max_tokens=80))
        return f"[CQ:at,qq={user_id}] {msg}" if user_id else msg

    async def _style_once(self, message: str, group_id: str | None) -> str:
        patterns = [
            r'(?:让)?(?:bot|机器人)?用(.+?)(?:风格|人设)?(?:说一句|说|回复|讲)\s*[：:，,]?\s*(.+)',
            r'^用(.+?)(?:风格|人设)?(?:说一句|说|回复|讲)\s*[：:，,]?\s*(.+)',
        ]
        for pattern in patterns:
            m = re.search(pattern, message, re.I)
            if not m:
                continue
            style_name = self._find_style_name(m.group(1))
            content = m.group(2).strip()
            if style_name and content:
                prompt = f"用「{style_name}」的人设回复这句话：{content}\n只回1-2句，不超过50字。"
                return self._clean_response(await self._call_llm(prompt, self._get_prompt(group_id, style_name), self._context_with_memory(group_id, 6), max_tokens=100))
        return ""

    def _ranking_text(self, group_id: str) -> str:
        stats = self._memory_bucket("stats", group_id)
        if not stats:
            return "排行榜还没数据，先让群里多聊两句"
        by_msg = sorted(stats.items(), key=lambda kv: int(kv[1].get("message_count", 0)), reverse=True)[:5]
        by_mention = sorted(stats.items(), key=lambda kv: int(kv[1].get("mention_count", 0)), reverse=True)[:5]
        lines = ["【群聊热度排行榜】", "发言最多："]
        for i, (uid, data) in enumerate(by_msg, 1):
            lines.append(f"{i}. {self._get_alias(group_id, uid)}({uid}) {data.get('message_count', 0)}条")
        lines.append("被@最多：")
        for i, (uid, data) in enumerate(by_mention, 1):
            lines.append(f"{i}. {self._get_alias(group_id, uid)}({uid}) {data.get('mention_count', 0)}次")
        return "\n".join(lines)

    def _game_reply(self, group_id: str, action: str) -> str:
        truth = [
            "最近一次嘴硬是什么时候？",
            "群里谁最像隐藏大佬？",
            "今天最想吐槽的一件事是什么？",
            "如果给自己今天的状态起个标题，叫什么？",
        ]
        dare = [
            "用一句话夸一下上一位发言的人。",
            "发一句你最常用的口头禅。",
            "用当前心情造一个离谱比喻。",
            "点名一个人接着回答真心话。",
        ]
        words = ["火锅", "作业", "键盘", "奶茶", "月亮", "网速"]
        if action in ("游戏", "小游戏"):
            return "小游戏：/真心话、/大冒险、/接龙 开始 词、/接龙 xxx、/猜词、/猜 xxx"
        if action == "真心话":
            return "真心话：" + random.choice(truth)
        if action == "大冒险":
            return "大冒险：" + random.choice(dare)
        if action.startswith("接龙"):
            rest = action.replace("接龙", "", 1).strip()
            game = self._games.setdefault(group_id, {})
            if rest.startswith("开始"):
                word = rest.replace("开始", "", 1).strip() or random.choice(words)
                game["chain_last"] = word
                return f"接龙开始：{word}。下一句要用「{word[-1]}」开头"
            last = game.get("chain_last")
            if not last:
                return "还没开接龙，先发 /接龙 开始 火锅"
            if not rest:
                return f"当前接到「{last}」，下一句用「{last[-1]}」开头"
            if rest[0] == last[-1]:
                game["chain_last"] = rest
                return f"接上了：{rest}。下一个用「{rest[-1]}」开头"
            return f"没接上，得用「{last[-1]}」开头"
        if action == "猜词":
            word = random.choice(words)
            self._games.setdefault(group_id, {})["guess_word"] = word
            return f"猜词开始：{len(word)}个字，提示：常见生活词。发 /猜 你的答案"
        if action.startswith("猜"):
            guess = action.replace("猜", "", 1).strip()
            word = self._games.setdefault(group_id, {}).get("guess_word")
            if not word:
                return "还没开猜词，先发 /猜词"
            if guess == word:
                self._games[group_id].pop("guess_word", None)
                return f"猜中了，就是「{word}」"
            return "不对，再猜"
        return ""

    async def _auto_reply(self, group_id: str, message: str) -> str:
        ctx = self._context_with_memory(group_id, 8)
        prompt = (
            f"群里有人刚说：{message}\n"
            "你低调接个梗或补一句，不要解释，不要像机器人，不超过30字。"
        )
        return self._clean_response(await self._call_llm(prompt, self._get_prompt(group_id), ctx, max_tokens=80))

    def _should_auto_reply(self, group_id: str, message: str) -> bool:
        cfg = self._group_config(group_id)
        enabled = bool(cfg.get("auto_reply_enabled", AUTO_REPLY_ENABLED))
        if not enabled:
            return False
        now = time.time()
        if now - self._last_auto_reply_at.get(group_id, 0) < int(cfg.get("auto_reply_cooldown", AUTO_REPLY_COOLDOWN)):
            return False
        chance = float(cfg.get("auto_reply_chance", AUTO_REPLY_CHANCE))
        if any(k and k in message for k in cfg.get("meme_keywords", MEME_KEYWORDS)):
            chance = max(chance, float(cfg.get("auto_reply_keyword_chance", AUTO_REPLY_KEYWORD_CHANCE)))
        ok = random.random() < chance
        if ok:
            self._last_auto_reply_at[group_id] = now
        return ok

    async def _scheduler_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                now = datetime.now()
                await self._maybe_rotate_daily_style(now)
                if SCHEDULED_FUN_ENABLED:
                    await self._maybe_send_scheduled_fun(now)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[WorkBuddy] scheduler error: {e}")

    async def _maybe_rotate_daily_style(self, now: datetime):
        if not DAILY_STYLE_ROTATION_ENABLED or not STYLE_LIST:
            return
        today = now.strftime("%Y-%m-%d")
        if self._memory.get("last_daily_style_date") == today:
            return
        self._memory["last_daily_style_date"] = today
        for gid in TARGET_GROUP_IDS:
            style = random.choice(STYLE_LIST)["style_name"]
            self._group_styles[gid] = style
            if DAILY_STYLE_ANNOUNCE:
                await self._send_group_msg(gid, f"今日人设轮换：{style}")
        _save_memory(self._memory)

    async def _maybe_send_scheduled_fun(self, now: datetime):
        hm = now.strftime("%H:%M")
        if hm not in SCHEDULED_FUN_TIMES:
            return
        today = now.strftime("%Y-%m-%d")
        sent = self._memory.setdefault("daily_fun_sent", {})
        for gid in TARGET_GROUP_IDS:
            key = f"{gid}:{today}:{hm}"
            if sent.get(key):
                continue
            ctx = self._context_with_memory(gid, 10)
            prompt = "按当前群聊气质发一句定时整活，像群友突然冒泡，不超过40字。"
            msg = self._clean_response(await self._call_llm(prompt, self._get_prompt(gid), ctx, max_tokens=90)) or "今日离谱发言提名开始"
            await self._send_group_msg(gid, msg)
            sent[key] = True
        _save_memory(self._memory)


    # ----------------------------------------------------------
    # 系统指令处理（以 / 开头，仅管理员）
    # ----------------------------------------------------------

    def _build_style_panel(self, show_switch_hint: bool = True, group_id: str | None = None) -> str:
        """生成风格列表面板"""
        if not STYLE_LIST and not self._runtime_styles:
            return "没有找到任何风格预设，请检查 prompts/ 目录"
        title = "【风格切换面板】" if show_switch_hint else "【风格列表】"
        current = self._get_current_style_name(group_id)
        scope = f"群 {group_id}" if group_id else "全局"
        lines = [f"{title}{scope}当前: {current}", ""]
        all_styles = STYLE_LIST + list(self._runtime_styles.values())
        for i, s in enumerate(all_styles, 1):
            name = s.get("style_name", "")
            emoji = s.get("emoji", "")
            desc = s.get("description", "")
            marker = " ← 当前" if name == current else ""
            lines.append(f"  {i}. {emoji}{name} - {desc}{marker}")
        if show_switch_hint:
            lines.append("")
            lines.append("回复序号或风格名切换，其他内容取消")
        return "\n".join(lines)


    async def _handle_public_command(self, cmd: str, group_id: str = None, raw_msg: str = "", self_id: str = None) -> str | None:
        """普通群友也可以玩的安全指令。"""
        if not group_id or not cmd.startswith("/"):
            return None
        action = cmd[1:].strip()

        game_reply = self._game_reply(group_id, action)
        if game_reply:
            return game_reply

        if action.startswith("今日总结"):
            mode = action.replace("今日总结", "", 1).strip() or "贴吧"
            return await self._summarize_today(group_id, mode)

        if action.startswith("画像"):
            target = self._target_from_message(raw_msg, action, self_id)
            if not target:
                return "用法：/画像 @某人"
            return await self._profile_user(group_id, target)

        if action.startswith("翻旧账") or action.startswith("名场面"):
            target = self._target_from_message(raw_msg, action, self_id)
            moments_group = self._memory_bucket("moments", group_id)
            pool = moments_group.get(target, []) if target else [m for arr in moments_group.values() for m in arr]
            if not pool:
                return "账本还是空的，先让管理员 /记下来 攒点素材"
            m = random.choice(pool)
            return f"【旧账】{m.get('name')}：{m.get('text')}"

        if action.startswith("查梗"):
            key = action.replace("查梗", "", 1).strip()
            glossary = self._memory_bucket("glossary", group_id)
            if not key:
                return "【黑话词典】\n" + "\n".join(f"{k}: {v}" for k, v in glossary.items()) if glossary else "词典还是空的"
            return f"{key}: {glossary.get(key, '没记过这个梗')}"

        if action in ("排行榜", "热度排行榜", "rank"):
            return self._ranking_text(group_id)

        return None

    async def _handle_system_command(self, cmd: str, from_group: str = None, raw_msg: str = "", self_id: str = None) -> str | None:
        """
        处理 / 开头的系统指令。
        返回 str 表示要发送给管理员的消息，None 表示不是系统指令。
        """
        cmd = cmd.strip()
        if not cmd.startswith("/"):
            return None

        action = cmd[1:].strip()
        group_id = str(from_group) if from_group else None

        if action in ("风格切换", "切换风格", "style", "switch"):
            self._pending_action[TEST_ACCOUNT] = {"type": "style_switch", "group_id": group_id}
            return self._build_style_panel(group_id=group_id)

        if action in ("风格列表", "列表", "styles", "list"):
            return self._build_style_panel(show_switch_hint=False, group_id=group_id)

        if action in ("当前风格", "当前", "now"):
            current = self._get_current_style_name(group_id)
            style = self._get_style_data(current)
            emoji = style.get("emoji", "")
            desc = style.get("description", "")
            return f"当前风格: {emoji}{current}\n{desc}"

        if action in ("状态", "status", "info"):
            ctx_counts = {gid: len(msgs) for gid, msgs in self._group_context.items() if msgs}
            warnings = self._config_warnings()
            lines = [
                "【Bot 状态】",
                "版本: v4.1.0",
                f"全局风格: {self._current_style}",
                f"本群风格: {self._get_current_style_name(group_id) if group_id else '非群聊'}",
                f"模型: {LLM_MODEL}",
                f"触发词: {self._group_config(group_id).get('trigger_word', TRIGGER_WORD)}",
                f"管理员: {TEST_ACCOUNT}",
                f"监听群: {len(TARGET_GROUP_IDS)} 个",
                f"自动接梗: {self._group_config(group_id).get('auto_reply_enabled', AUTO_REPLY_ENABLED)}",
                f"上下文: {ctx_counts}",
                "配置健康: " + ("正常" if not warnings else "；".join(warnings)),
            ]
            return "\n".join(lines)

        if action in ("清空上下文", "清除上下文", "clear", "清空"):
            self._pending_action[TEST_ACCOUNT] = {"type": "clear_ctx", "group_id": group_id}
            return "确认清空上下文？\n回复「确认」执行，其他取消"

        clear_match = re.match(r'清空\s*(\d+)', action)
        if clear_match:
            gid = clear_match.group(1)
            if gid in self._group_context:
                self._group_context[gid].clear()
                return f"已清空群 {gid} 的上下文"
            return f"群 {gid} 不在监听列表中"

        if action in ("重载风格", "reload", "重载"):
            global STYLE_LIST, STYLE_NAMES
            STYLE_LIST = _load_prompts()
            STYLE_NAMES = [s["style_name"] for s in STYLE_LIST]
            return f"已重载 {len(STYLE_LIST)} 套风格: {', '.join(STYLE_NAMES)}"

        if action in ("群列表", "监听群", "groups"):
            lines = ["【监听群列表】"]
            for gid in TARGET_GROUP_IDS:
                count = len(self._group_context.get(gid, []))
                style = self._get_current_style_name(gid)
                auto = self._group_config(gid).get("auto_reply_enabled", AUTO_REPLY_ENABLED)
                lines.append(f"  {gid} 风格:{style} 上下文:{count}条 自动接梗:{auto}")
            return "\n".join(lines)

        if action.startswith("今日总结"):
            if not group_id:
                return "今日总结需要在群聊里使用"
            mode = action.replace("今日总结", "", 1).strip() or "贴吧"
            return await self._summarize_today(group_id, mode)

        if action.startswith("画像"):
            if not group_id:
                return "群友画像需要在群聊里使用"
            target = self._target_from_message(raw_msg, action, self_id)
            if not target:
                return "用法：/画像 @某人"
            return await self._profile_user(group_id, target)

        if action.startswith("记下来") or action.startswith("名场面记录"):
            if not group_id:
                return "名场面需要在群聊里记录"
            target = self._target_from_message(raw_msg, action, self_id)
            moment = self._find_recent_message_for_moment(group_id, target)
            if not moment:
                return "没找到能记录的上一条消息"
            self._memory_bucket("moments", group_id).setdefault(moment["user_id"], []).append(moment)
            _save_memory(self._memory)
            return f"记下来了：{moment['name']} - {moment['text'][:40]}"

        if action.startswith("翻旧账") or action.startswith("名场面"):
            if not group_id:
                return "翻旧账需要在群聊里使用"
            target = self._target_from_message(raw_msg, action, self_id)
            moments_group = self._memory_bucket("moments", group_id)
            pool = moments_group.get(target, []) if target else [m for arr in moments_group.values() for m in arr]
            if not pool:
                return "账本还是空的，先发 /记下来 攒点素材"
            m = random.choice(pool)
            return f"【旧账】{m.get('name')}：{m.get('text')}"

        if action.startswith("记梗"):
            if not group_id:
                return "黑话词典需要在群聊里使用"
            body = action.replace("记梗", "", 1).strip()
            if "=" in body:
                key, value = body.split("=", 1)
            elif "：" in body:
                key, value = body.split("：", 1)
            elif ":" in body:
                key, value = body.split(":", 1)
            else:
                return "用法：/记梗 词 = 解释"
            key, value = key.strip(), value.strip()
            if not key or not value:
                return "词和解释都得有"
            self._memory_bucket("glossary", group_id)[key] = value
            _save_memory(self._memory)
            return f"记住了：{key} = {value}"

        if action.startswith("查梗"):
            if not group_id:
                return "查梗需要在群聊里使用"
            key = action.replace("查梗", "", 1).strip()
            glossary = self._memory_bucket("glossary", group_id)
            if not key:
                return "【黑话词典】\n" + "\n".join(f"{k}: {v}" for k, v in glossary.items()) if glossary else "词典还是空的"
            return f"{key}: {glossary.get(key, '没记过这个梗')}"

        if action.startswith("记昵称"):
            if not group_id:
                return "昵称记忆需要在群聊里使用"
            target = self._target_from_message(raw_msg, action, self_id)
            name = re.sub(r'记昵称|\[CQ:at,qq=\d+\]|\d{5,}', '', action).strip(" =：:")
            if not target or not name:
                return "用法：/记昵称 @某人 外号"
            self._memory_bucket("aliases", group_id)[target] = name
            _save_memory(self._memory)
            return f"记住了，{target} 以后叫「{name}」"

        if action.startswith("召唤"):
            if not group_id:
                return "召唤术需要在群聊里使用"
            target = self._target_from_message(raw_msg, action, self_id)
            reason = re.sub(r'召唤|\[CQ:at,qq=\d+\]|\d{5,}', '', action).strip(" ：:")
            if not target:
                return "用法：/召唤 @某人 理由"
            return await self._summon_user(group_id, target, reason)

        if action.startswith("融合"):
            body = action.replace("融合", "", 1).strip()
            names = [n for n in STYLE_NAMES if n in body]
            if len(names) < 2:
                parts = body.split()
                names = [self._find_style_name(p) for p in parts]
                names = [n for n in names if n]
            if len(names) < 2:
                return "用法：/融合 毒舌损友 温柔学姐"
            a, b = names[0], names[1]
            sa, sb = self._get_style_data(a), self._get_style_data(b)
            name = f"融合:{a}+{b}"
            self._runtime_styles[name] = {
                "style_name": name,
                "emoji": "🧪",
                "description": f"{a} 与 {b} 的临时混合人格",
                "system_prompt": f"你现在融合两种人设：\n【{a}】{sa.get('system_prompt', '')}\n\n【{b}】{sb.get('system_prompt', '')}\n\n回复时同时保留两者特点，但不要解释融合过程。",
                "boss_system_prompt": f"你是管理员的熟人，融合了「{a}」和「{b}」两种说话方式。自然、短句、有现场感。",
            }
            self._set_current_style(name, group_id)
            return f"融合完成，当前风格切到 {name}"

        game_reply = self._game_reply(group_id or "_private", action)
        if game_reply:
            return game_reply

        if action in ("排行榜", "热度排行榜", "rank"):
            if not group_id:
                return "排行榜需要在群聊里使用"
            return self._ranking_text(group_id)

        if action in ("帮助", "help", "指令"):
            lines = [
                "【管理员指令列表】",
                "",
                "--- 风格 ---",
                "/风格切换 /风格列表 /当前风格 /重载风格",
                "/融合 风格A 风格B",
                "",
                "--- 记忆与群友 ---",
                "/今日总结 [正经|缺德|贴吧]",
                "/画像 @某人",
                "/记下来、/翻旧账 @某人",
                "/记梗 词 = 解释、/查梗 词",
                "/记昵称 @某人 外号",
                "/排行榜",
                "",
                "--- 互动 ---",
                "/召唤 @某人 理由",
                "/真心话 /大冒险 /接龙 开始 词 /猜词",
                "",
                "--- 管理 ---",
                "/状态 /群列表 /清空上下文",
                "",
                "--- 动作指令（不需要/） ---",
                "怼 @某人、找某人聊天、@某人 内容、活跃一下、去群里说xxx",
                "也可以：用温柔学姐说一句 xxx",
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

        pending = self._pending_action.pop(user_id)
        if isinstance(pending, str):
            action_type = pending
            group_id = None
        else:
            action_type = pending.get("type")
            group_id = pending.get("group_id")
        message = message.strip()

        if action_type == "style_switch":
            if not message:
                return None
            all_styles = STYLE_LIST + list(self._runtime_styles.values())
            if message.isdigit():
                idx = int(message) - 1
                if 0 <= idx < len(all_styles):
                    target = all_styles[idx]["style_name"]
                else:
                    return f"无效序号，请输入 1-{len(all_styles)}"
            else:
                target = self._find_style_name(message)
                if not target:
                    return "未匹配到风格，已取消"

            old = self._get_current_style_name(group_id)
            self._set_current_style(target, group_id)
            emoji = self._get_style_data(target).get("emoji", "")
            logger.info(f"[WorkBuddy] Style switched: {old} -> {target} group={group_id}")
            scope = f"群 {group_id}" if group_id else "全局"
            return f"{scope}风格已切换: {emoji}{target}"

        if action_type == "clear_ctx":
            if message == "确认":
                if group_id and group_id in self._group_context:
                    self._group_context[group_id].clear()
                    return f"已清空群 {group_id} 的上下文"
                for gid in self._group_context:
                    self._group_context[gid].clear()
                logger.info("[WorkBuddy] All contexts cleared by boss")
                return "已清空所有群的上下文"
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

    async def _call_llm(
        self,
        user_message: str,
        system_prompt: str = None,
        context: str = None,
        max_tokens: int = 120,
        temperature: float = 0.85,
    ) -> str:
        try:
            if not LLM_API_KEY:
                logger.error("[WorkBuddy] LLM API key is empty")
                return ""
            headers = {
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json"
            }
            messages = [{"role": "system", "content": system_prompt or self._system_prompt}]
            if context:
                messages.append({
                    "role": "user",
                    "content": f"可参考的信息：\n{context}\n\n现在要处理：{user_message}\n\n要求：贴近QQ群聊语气，短句自然，不要暴露AI身份，不要用emoji。"
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"{user_message}\n\n要求：只回复1-2句短句，像QQ聊天一样自然随意。不要用emoji。"
                })
            payload = {
                "model": LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(LLM_API_URL, headers=headers, json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    error_text = await resp.text()
                    logger.error(f"LLM API error: {resp.status} - {error_text}")
                    return ""
        except Exception as e:
            logger.error(f"LLM call error: {e}")
            return ""

    async def _call_deepseek(self, user_message: str, system_prompt: str = None, context: str = None) -> str:
        return await self._call_llm(user_message, system_prompt, context)

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
            r"[^\u4e00-\u9fff\u3000-\u303f\uff00-\uffefa-zA-Z0-9\s，。？！、；：\"'（）\[\]【】~…—]",
            "",
            text,
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

        # 私聊时支持指定群号：去群123456说xxx / 去群123456里说xxx / 在群123456发xxx
        if not target_group:
            group_in_cmd = re.match(r'(?:去|在)?群\s*(\d+)\s*(.*)', cmd)
            if group_in_cmd:
                matched_gid = group_in_cmd.group(1)
                rest_cmd = group_in_cmd.group(2).strip()
                if matched_gid in TARGET_GROUP_IDS:
                    target_group = matched_gid
                    cmd = rest_cmd  # 后续用去掉群号后的指令继续匹配
                    cmd = re.sub(r'^[里中面]+', '', cmd).strip()
                    if not cmd:
                        return True

        # === 群友召唤术 ===
        if cmd.startswith("召唤"):
            if not target_group:
                return True
            target_qq = at_qqs[0] if at_qqs else ""
            reason = re.sub(r'召唤|\[CQ:at,qq=\d+\]|\d{5,}', '', cmd).strip(" ：:")
            if target_qq:
                await self._send_group_msg(target_group, await self._summon_user(target_group, target_qq, reason))
            return True

        # === 点歌式临时人格 ===
        style_once = await self._style_once(cmd, target_group)
        if style_once and target_group:
            await self._send_group_msg(target_group, style_once)
            return True

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
                msg = await self._call_llm(prompt, self._get_prompt(target_group, boss=True), self._context_with_memory(target_group, 5))
                msg = self._clean_response(msg)
                await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
            else:
                ctx = self._get_context_text(target_group, 5)
                if chat_content:
                    prompt = f"你在群里主动找{target_name}聊天，你要对他说：{chat_content}。用你的人设风格，1句话不超过30字。"
                else:
                    prompt = f"你在群里自然地找{target_name}搭句话。用你的人设风格，1句话。"
                msg = await self._call_llm(prompt, self._get_prompt(target_group, boss=True), self._context_with_memory(target_group, 5))
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
                roast_msg = await self._call_llm(prompt, self._get_prompt(target_group), self._context_with_memory(target_group, 8) if not target_msg else None)
            else:
                if content:
                    prompt = f"你要在群里开喷，理由是：{content}。你的人设风格，犀利但朋友互损级别，1句话不超过30字。"
                    roast_msg = await self._call_llm(prompt, self._get_prompt(target_group), self._context_with_memory(target_group, 8))
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
                    msg = await self._call_llm(prompt, self._get_prompt(target_group), self._context_with_memory(target_group, 5))
                    msg = self._clean_response(msg)
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] {msg}")
                else:
                    await self._send_group_msg(target_group, f"[CQ:at,qq={target_qq}] 咋了")
                return True

            if content and len(content) > 0:
                ctx = self._get_context_text(target_group, 5)
                prompt = f"你在群里对一个人说：{content}。你的人设风格，1句话不超过30字。"
                msg = await self._call_llm(prompt, self._get_prompt(target_group), self._context_with_memory(target_group, 5))
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
            ai_msg = await self._call_llm("自然地参与一下当前话题，随便说一句", self._get_prompt(target_group), self._context_with_memory(target_group, 5))
            msg = self._clean_response(ai_msg) if ai_msg else "你们在聊啥呢"
            await self._send_group_msg(target_group, msg)
            return True

        # === 别理xxx ===
        if re.search(r'(?:别理|忽略|不要理|拉黑|不理)', cmd):
            return True

        # === 去群里说xxx / 说xxx（私聊指定群号时，群号已提取，直接匹配"说/发/讲"开头） ===
        say_match = re.search(r'^(?:去群里?|在群里|群里?)\s*(?:说|发|讲)?\s*(.+)', cmd)
        if not say_match and target_group:
            # 私聊指定群号后，cmd可能是"说xxx"或直接是内容
            say_match = re.match(r'^(?:说|发|讲)\s*(.+)', cmd)
        if say_match:
            if not target_group:
                return True
            content = say_match.group(1).strip()
            if content:
                rewrite_match = re.search(r'(?:帮我)?把(?:这句话|下面这句|下面这句话)?说得\s*(.+?)\s*一点[：:]\s*(.+)', content)
                if rewrite_match:
                    tone = rewrite_match.group(1).strip()
                    original = rewrite_match.group(2).strip()
                    prompt = f"把这句话改写得{tone}一点：{original}\n保持原意，像群友发言，1句话不超过45字。"
                else:
                    prompt = f"你要在群里说：{content}。用你的人设风格改写，1句话不超过30字，保持原意。"
                msg = await self._call_llm(prompt, self._get_prompt(target_group), self._context_with_memory(target_group, 5))
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
            group_id = str(group_id) if group_id is not None else None
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
                self._record_group_memory(group_id, sender_name, str(user_id), clean_for_ctx, at_targets_all)

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
                group_trigger = self._group_config(group_id).get("trigger_word", TRIGGER_WORD)
                if group_trigger in message or has_at or is_at:
                    should_process = True
                    if group_trigger in message:
                        message = message.replace(group_trigger, "").strip()
                        message = re.sub(r'^[，,、\s]+', '', message)
                    if self_id:
                        message = re.sub(rf'\[CQ:at,qq={self_id}\]\s*', '', raw_msg).strip()
                        if not message:
                            message = re.sub(r'\[CQ:[^\]]+\]', '', raw_msg).strip()
                            message = message.replace(self._group_config(group_id).get("trigger_word", TRIGGER_WORD), "").strip()
                            message = re.sub(r'^[，,、\s]+', '', message)

            elif is_private and is_boss:
                should_process = True

            elif is_private and TRIGGER_WORD in message:
                should_process = True
                message = message.replace(TRIGGER_WORD, "").strip()
                message = re.sub(r'^[，,、\s]+', '', message)

            if not should_process:
                if group_id in TARGET_GROUP_IDS and self._should_auto_reply(group_id, message):
                    auto = await self._auto_reply(group_id, message)
                    if auto:
                        yield event.plain_result(auto)
                return

            if not message.strip():
                message = "你好"

            logger.info(f"[WorkBuddy] Processing from {sender_name}({user_id}): {message[:50]}")

            # ========== 管理员消息处理 ==========
            if is_boss:
                # 1. 尝试系统指令（/开头）
                sys_reply = await self._handle_system_command(message, from_group=group_id, raw_msg=raw_msg, self_id=str(self_id))
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
                style_once = await self._style_once(message, group_id)
                if style_once:
                    response = style_once
                else:
                    response = await self._call_llm(message, self._get_prompt(group_id, boss=True), self._context_with_memory(group_id, 8))
            else:
                # 普通用户
                if message.startswith("/") and group_id:
                    public_reply = await self._handle_public_command(message, group_id=group_id, raw_msg=raw_msg, self_id=str(self_id))
                    if public_reply is not None:
                        yield event.plain_result(public_reply)
                        return
                style_once = await self._style_once(message, group_id)
                if style_once:
                    response = style_once
                else:
                    ctx = self._context_with_memory(group_id, 10) if group_id else None
                    response = await self._call_llm(message, self._get_prompt(group_id), context=ctx)

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
