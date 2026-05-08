# mam_core.py — 核心算法 & 文件工具
import os
import sys
import re
import json
import numpy as np
import cv2
import imagehash
from PIL import Image

IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
VID_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
ALL_EXTS  = IMG_EXTS + VID_EXTS

def _app_data_dir() -> str:
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        path = os.path.join(base, 'MAMDesktop')
    elif sys.platform == 'darwin':
        path = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'MAMDesktop')
    else:
        path = os.path.join(os.path.expanduser('~'), '.mamdesktop')
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except:
        return os.path.dirname(os.path.abspath(__file__))


LEGACY_CONFIG_FILE = "mam_config.json"
LEGACY_DB_CONFIG_FILE = "mam_db_config.json"
LEGACY_PRODUCER_CODE_FILE = "mam_producer_codes.json"

CONFIG_FILE = os.path.join(_app_data_dir(), LEGACY_CONFIG_FILE)
DB_CONFIG_FILE = os.path.join(_app_data_dir(), LEGACY_DB_CONFIG_FILE)
PRODUCER_CODE_FILE = os.path.join(_app_data_dir(), LEGACY_PRODUCER_CODE_FILE)

# 这些前缀常用于国家/来源标记，不应当作人员代码。
# 如有误判，可按需继续补充（统一大写）。
NON_PRODUCER_PREFIX_CODES = {"US"}


# ── 人员代码表 ────────────────────────────────────────────────
def load_producer_codes() -> dict:
    """载入人员代码表，格式: {"KS": "张三", "57": "李四"}"""
    for p in (PRODUCER_CODE_FILE, LEGACY_PRODUCER_CODE_FILE):
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
    return {}


def save_producer_codes(codes: dict):
    os.makedirs(os.path.dirname(PRODUCER_CODE_FILE), exist_ok=True)
    with open(PRODUCER_CODE_FILE, 'w', encoding='utf-8') as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)


def parse_producer_from_filename(filename: str, code_map: dict) -> str:
    """
    从文件名解析制作人。支持多种历史命名格式：

    1. YYYYMMDD-CODE-描述   （标准格式，8位日期）
       例：20260113-XQ-素材.mp4 / 20260131-34-素材.jpg
    2. YYYYMM-DD-CODE-描述  （6+2位日期分段）
       例：202512-05-85-成品.mp4
    3. CODE + 分隔符 + 描述  （- / _ / 空格）
       例：LYI-地狱是真实存在.JPG / RC 申命记28_2_你.JPG / FM-_神的时间.JPG
    4. CODE+数字后缀-描述    （字母+数字混合代码，取字母前缀匹配）
       例：xy2-2_凡将神放在生.JPG  →  xy 是人员
    5. CODE直接接中文（无分隔符）
       例：SXC任何将上帝放在.mp4

    匹配规则（大小写不敏感）：
    - 先精确匹配 code_map
    - 首段 CODE（如 RC 空格/横线后接描述）优先作为人员代码
    - 支持排除前缀（如 US），排除后继续匹配后段代码
    - 数字代码支持前导零归一化（如 0019 -> 19）
    - 若 CODE 含字母+数字混合（如 xy2），尝试纯字母前缀匹配
    - 无分隔符前缀代码（如 SXC如果...）允许回退到代码本身
    - 识别不到返回 '未知'
    """
    name = os.path.splitext(os.path.basename(filename))[0]
    name = re.sub(r'\s*\(\d+\)\s*$', '', name).strip()

    upper_map = {k.upper(): v for k, v in (code_map or {}).items()}

    def lookup(s):
        return upper_map.get(s.upper()) if s else None

    def resolve_code(s, mapped_only=False):
        """CODE → 人名或原码；非法格式返回 None"""
        if not s or not re.match(r'^[A-Za-z0-9]{1,6}$', s):
            return None
        # 1. 精确匹配
        result = lookup(s)
        if result:
            return result
        # 2. 纯数字代码：支持前导零归一化（0019 -> 19）
        if s.isdigit():
            norm = s.lstrip('0') or '0'
            result = lookup(norm)
            if result:
                return result
            return None if mapped_only else norm
        # 3. 字母+数字混合（如 xy2）→ 只取字母前缀再匹配
        m = re.match(r'^([A-Za-z]{1,6})\d+$', s)
        if m:
            result = lookup(m.group(1))
            if result:
                return result
        # 4. code_map 无记录 → 原样返回（保留代码）
        return None if mapped_only else s

    # ── 格式 1：YYYYMMDD-CODE-*（8位日期）──────────────
    m = re.match(r'^\d{8}[-_\s]+([A-Za-z0-9]{1,6})(?:[-_\s]|$)', name)
    if m:
        return resolve_code(m.group(1)) or '未知'

    # ── 格式 2：YYYYMM-DD-CODE-*（6位年月 + 2位日）──────
    m = re.match(r'^\d{4,6}[-_\s]+\d{1,2}[-_\s]+([A-Za-z0-9]{1,6})(?:[-_\s]|$)', name)
    if m:
        result = resolve_code(m.group(1))
        if result:
            return result

    leading_candidate = None

    # ── 格式 3：CODE + 分隔符（- _ 空格）+ 描述 ─────────
    m = re.match(r'^([A-Za-z0-9]{1,6})[-_\s]+', name)
    if m:
        code_head = m.group(1)
        code_head_upper = code_head.upper()
        if code_head_upper not in NON_PRODUCER_PREFIX_CODES:
            # 首段看起来就是人员代码时，优先返回（避免后段章节号如 4 抢中）
            result = resolve_code(code_head, mapped_only=True)
            if result:
                return result
            leading_candidate = resolve_code(code_head, mapped_only=False)
            if leading_candidate:
                return leading_candidate

    # ── 格式 5：CODE直接接中文/非ASCII（无分隔符）────────
    m = re.match(r'^([A-Za-z]{2,6})[^\x00-\x7F]', name)
    if m:
        head = m.group(1)
        if head.upper() in NON_PRODUCER_PREFIX_CODES:
            result = None
        else:
            result = resolve_code(head)
        if result:
            return result

    # ── 兜底：按分隔符切段，从右向左找候选代码（避免把前缀国家码当制作人）────
    # 例：US-AI-情绪--20241129-0019 - 副本 拷贝  -> 优先命中 0019
    tokens = [t.strip() for t in re.split(r'[-_\s]+', name) if t.strip()]

    # 第一轮：只返回“已映射”的命中
    for tk in reversed(tokens):
        if not re.match(r'^[A-Za-z0-9]{1,6}$', tk):
            continue
        if tk.upper() in NON_PRODUCER_PREFIX_CODES:
            continue
        # 跳过常见日期段
        if re.match(r'^\d{8}$', tk):
            continue
        result = resolve_code(tk, mapped_only=True)
        if result:
            return result

    # 第二轮：回退为未映射代码（从右向左）
    for tk in reversed(tokens):
        if not re.match(r'^[A-Za-z0-9]{1,6}$', tk):
            continue
        if tk.upper() in NON_PRODUCER_PREFIX_CODES:
            continue
        if re.match(r'^\d{8}$', tk):
            continue
        result = resolve_code(tk, mapped_only=False)
        if result:
            return result

    if leading_candidate:
        return leading_candidate

    return '未知'


def load_config():
    for p in (CONFIG_FILE, LEGACY_CONFIG_FILE):
        if os.path.exists(p):
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
    return {"user_name": "操作员", "user_id": "001"}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── pHash（使用 imagehash 标准库）─────────────────────
def _cv2_to_pil(img) -> Image.Image | None:
    """OpenCV BGR ndarray → PIL Image（RGB）"""
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def get_phash(img) -> str | None:
    """
    用 imagehash.phash 计算感知哈希，返回 16 位小写 hex 字符串。
    img 可以是 OpenCV ndarray 或 PIL Image。
    """
    try:
        if img is None:
            return None
        if not isinstance(img, Image.Image):
            img = _cv2_to_pil(img)
            if img is None:
                return None
        h = imagehash.phash(img, hash_size=8)   # 8×8 = 64 bit → 16 hex chars
        return str(h)   # imagehash 已保证 16 位小写 hex
    except Exception as e:
        return None


def get_phash_pil(pil_img: Image.Image) -> str | None:
    """直接接受 PIL Image，避免二次转换"""
    try:
        if pil_img is None:
            return None
        h = imagehash.phash(pil_img, hash_size=8)
        return str(h)
    except:
        return None


def hamming(h1: str, h2: str) -> int:
    """两个 16 位 hex phash 字符串的汉明距离"""
    try:
        a = imagehash.hex_to_hash(h1)
        b = imagehash.hex_to_hash(h2)
        return a - b   # imagehash 重载了减法运算符 = 汉明距离
    except:
        return 64


def phash_sim(h1: str, h2: str) -> str:
    return f"{int((1 - hamming(h1, h2) / 64) * 100)}%"


# ── 文件读取 ───────────────────────────────────────────
def cv2_read(filepath):
    """支持中文路径的 OpenCV 读取"""
    try:
        arr = np.fromfile(os.path.abspath(filepath), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.dtype != np.uint8:
            img = (img / 256).astype(np.uint8)
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img
    except:
        return None


def get_thumbnail(filepath):
    """返回缩略图 ndarray（图片直接读，视频取 0.5s 帧）"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMG_EXTS:
        return cv2_read(filepath)
    if ext in VID_EXTS:
        cap = cv2.VideoCapture(filepath)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if frame_count and frame_count > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_count / 2))
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, 1500)
        ok, frame = cap.read()
        cap.release()
        return frame if ok else None
    return None


def get_file_size(filepath):
    try:
        return os.path.getsize(filepath)
    except:
        return 0


def get_asset_type(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMG_EXTS: return "image"
    if ext in VID_EXTS: return "video"
    return "unknown"


def make_thumb_bytes(img):
    if img is None:
        return None
    _, buf = cv2.imencode('.jpg', cv2.resize(img, (100, 100)))
    return buf.tobytes()
