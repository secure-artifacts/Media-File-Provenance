# mam_core.py — 核心算法 & 文件工具
import os
import json
import numpy as np
import cv2
import imagehash
from PIL import Image

IMG_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
VID_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
ALL_EXTS  = IMG_EXTS + VID_EXTS

CONFIG_FILE    = "mam_config.json"
DB_CONFIG_FILE = "mam_db_config.json"


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"user_name": "操作员", "user_id": "001"}


def save_config(cfg):
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
        cap.set(cv2.CAP_PROP_POS_MSEC, 500)
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
