# mam_meta.py — 文件元数据读写
# ─────────────────────────────────────────────────────
# 写入目标：Windows "备注" (System.Comment) 字段
#   图片 JPEG/PNG/WebP  → exiftool -UserComment  (EXIF UserComment)
#   视频 MP4/MOV/AVI    → exiftool -Comment       (©cmt / comment)
#
# 编码方案：argfile + UTF-8 BOM → exiftool 读作 UTF-8，中文不乱码
#
# 降级方案（exiftool 不可用时）：
#   PNG  → PIL PngInfo 文本块
#   JPEG → piexif EXIF UserComment
#   MP4/MOV → mutagen ©cmt
#
# 将 exiftool.exe 放入项目目录或系统 PATH，放入后无需重启即可生效。
# ─────────────────────────────────────────────────────
import os
import sys
import re
import json
import shutil
import subprocess
import tempfile
import warnings
warnings.filterwarnings("ignore")

# ── 可选依赖 ─────────────────────────────────────────
try:
    from PIL import Image, PngImagePlugin
    _PIL = True
except ImportError:
    _PIL = False

try:
    import piexif
    _PIEXIF = True
except ImportError:
    _PIEXIF = False

try:
    from mutagen.mp4 import MP4 as _MP4
    _MUTAGEN_MP4 = True
except ImportError:
    _MUTAGEN_MP4 = False


# ─────────────────────────────────────────────────────
# exiftool 动态检测（每次调用，放入即生效）
# ─────────────────────────────────────────────────────
def _find_exiftool() -> str | None:
    search_dirs = []

    # 1) 模块目录（开发态）
    module_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs.append(module_dir)

    # 2) 模块上级目录（PyInstaller onedir 常见：_internal 的上一层）
    search_dirs.append(os.path.dirname(module_dir))

    # 3) 可执行文件目录（安装后最常见）
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    search_dirs.append(exe_dir)

    # 4) PyInstaller 运行时目录
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        search_dirs.append(meipass)

    # 5) macOS .app 常见目录
    if sys.platform == 'darwin':
        search_dirs.append(os.path.abspath(os.path.join(exe_dir, '..', 'MacOS')))
        search_dirs.append(os.path.abspath(os.path.join(exe_dir, '..', 'Resources')))

    # 去重且保持顺序
    uniq_dirs = []
    seen = set()
    for d in search_dirs:
        if d and d not in seen:
            seen.add(d)
            uniq_dirs.append(d)

    for base_dir in uniq_dirs:
        for name in ("exiftool.exe", "exiftool(-k).exe", "exiftool"):
            p = os.path.join(base_dir, name)
            if os.path.isfile(p):
                return p

    return shutil.which("exiftool") or shutil.which("exiftool.exe")


# ─────────────────────────────────────────────────────
# 格式化备注内容（写入文件的人类可读行）
# ─────────────────────────────────────────────────────
def _format_comment(record: dict) -> str:
    """
    生成写入 Windows"备注"字段的单行文本。
    格式：phash=<hex>; by=<producer>; date=<YYYY-MM-DD>
          [; from=ph(制作人)>parent(制作人)>...]
          [; parts=chain1,chain2,...]
    chain 格式：ph(制作人)>parent(制作人)>grandparent(制作人)
    """
    parts = []
    if record.get("phash"):
        parts.append(f"phash={record['phash']}")
    if record.get("producer"):
        parts.append(f"by={record['producer']}")
    if record.get("created_at"):
        parts.append(f"date={str(record['created_at'])[:10]}")
    if record.get("derived_from"):
        df = record["derived_from"]
        if isinstance(df, dict):
            chain = df.get('ancestry_chain')
            if chain:
                parts.append(f"from={chain}")
            else:
                ph = (df.get('phash') or '')
                pr = df.get('producer', '')
                parts.append(f"from={ph}({pr})" if pr else f"from={ph}")
        elif isinstance(df, str):
            parts.append(f"from={df}")
    if record.get("composed_from"):
        cf = record["composed_from"]
        if isinstance(cf, list) and cf:
            items = []
            for p in cf:
                if isinstance(p, dict) and p.get('phash'):
                    chain = p.get('ancestry_chain')
                    if chain:
                        items.append(chain)
                    else:
                        ph = p['phash']
                        pr = p.get('producer', '')
                        items.append(f"{ph}({pr})" if pr else ph)
            if items:
                parts.append(f"parts={','.join(items)}")
    return "; ".join(parts) if parts else "MAM素材登记"


def _parse_chain_str(chain_str: str) -> list:
    """
    解析链式字符串 'ph(name)>parent(name)>...' 为 [{'phash':..,'producer':..}, ...]
    """
    levels = []
    for level in chain_str.split('>'):
        level = level.strip()
        lm = re.match(r"([0-9a-f]+)\(([^)]*)\)", level, re.IGNORECASE)
        if lm:
            levels.append({"phash": lm.group(1), "producer": lm.group(2)})
        elif re.match(r"^[0-9a-f]+$", level, re.IGNORECASE):
            levels.append({"phash": level, "producer": ""})
    return levels


def _parse_comment(text: str) -> dict | None:
    """
    解析备注字段，提取 phash 等信息。
    兼容格式：
      1. phash=xxx; by=xxx; date=xxx; from=chain; parts=chain1,chain2  （新格式）
      2. JSON 字符串  （旧格式兼容）
    chain 格式：ph(制作人)>parent(制作人)>...
    解析后 parts_chains = [[{phash,producer},{phash,producer},...], ...]  （每个元素是一条链）
    解析后 derived_from_chain = [{phash,producer},...]
    """
    if not text:
        return None
    text = text.strip()

    # 旧格式：JSON
    if text.startswith("{"):
        try:
            d = json.loads(text)
            if isinstance(d, dict) and d.get("phash"):
                return d
        except:
            pass

    # 新格式：key=value; ...
    m = re.search(r"phash=([0-9a-f]{16})", text, re.IGNORECASE)
    if not m:
        return None
    rec = {"phash": m.group(1).lower()}
    m2 = re.search(r"by=([^;]+)", text)
    if m2:
        rec["producer"] = m2.group(1).strip()
    m3 = re.search(r"date=([^;]+)", text)
    if m3:
        rec["created_at"] = m3.group(1).strip()
    # from= 解析：支持链式 ph(name)>parent(name)>...
    m4 = re.search(r"from=([^;]+)", text)
    if m4:
        from_str = m4.group(1).strip()
        chain = _parse_chain_str(from_str)
        if chain:
            rec["derived_from_chain"] = chain
            rec["derived_from"] = chain[0].get('phash', from_str)
        else:
            rec["derived_from"] = from_str
    # parts= 解析：每个 chain 用逗号分隔
    m5 = re.search(r"parts=([^;]+)", text)
    if m5:
        parts_str = m5.group(1).strip()
        chains = [_parse_chain_str(c.strip()) for c in parts_str.split(',') if c.strip()]
        rec["parts_chains"] = [c for c in chains if c]
    return rec


# ─────────────────────────────────────────────────────
# exiftool 写入（argfile + UTF-8 BOM，解决中文乱码）
# ─────────────────────────────────────────────────────
def _exiftool_write(filepath: str, record: dict) -> bool:
    """
    用 exiftool 把记录写入文件"备注"字段：
      图片 → -UserComment  (EXIF UserComment，Windows"备注")
      视频 → -Comment      (©cmt / comment，Windows"备注")

    使用 argfile + UTF-8 BOM 确保中文不乱码。
    返回 True = 成功
    """
    exe = _find_exiftool()
    if not exe:
        return False

    comment_text = _format_comment(record)
    ext = os.path.splitext(filepath)[1].lower()

    # Windows"备注"对应的 exiftool 标签
    # -UserComment → EXIF:UserComment → System.Comment（图片）
    # -Comment     → ©cmt / QuickTime Comment（视频）
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"):
        tag_line = f"-UserComment={comment_text}"
    else:
        tag_line = f"-Comment={comment_text}"

    # argfile：只放 tag 内容和 -overwrite_original（UTF-8 BOM 保证中文 tag 值正确）
    # 文件路径作为独立参数直接传给 exiftool（避免中文路径在 argfile 中编码错误）
    args_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig",   # utf-8-sig = UTF-8 + BOM
            suffix=".args", delete=False
        ) as f:
            f.write(tag_line + "\n")
            f.write("-overwrite_original\n")
            args_file = f.name

        # 文件路径直接传（不写入 argfile），Windows 可正确处理中文路径
        cmd = [exe, "-@", args_file, os.path.abspath(filepath)]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="ignore").strip()
            _log_warn(f"exiftool 错误: {err}")
        return result.returncode == 0
    except Exception as e:
        _log_warn(f"exiftool 调用失败: {e}")
        return False
    finally:
        if args_file and os.path.exists(args_file):
            try:
                os.unlink(args_file)
            except:
                pass


def _exiftool_read(filepath: str) -> str | None:
    """用 exiftool 读取 UserComment 或 Comment，返回字符串"""
    exe = _find_exiftool()
    if not exe:
        return None
    ext = os.path.splitext(filepath)[1].lower()
    # 图片用 -UserComment，视频用 -Comment
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"):
        tag = "-UserComment"
    else:
        tag = "-Comment"
    cmd = [exe, tag, "-s3", "-charset", "UTF8", os.path.abspath(filepath)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=10
        )
        out = result.stdout.strip()
        return out if out else None
    except:
        return None


# ─────────────────────────────────────────────────────
# 写入元数据（对外接口）
# ─────────────────────────────────────────────────────
def write_metadata(filepath: str, record: dict):
    """
    将素材登记信息写入文件内嵌"备注"字段（不写外部文件）。
    优先 exiftool，降级到 Python 原生库。
    """
    ext = os.path.splitext(filepath)[1].lower()

    # ── 优先：exiftool ──────────────────────────────
    if _exiftool_write(filepath, record):
        _log_info(f"exiftool 写入备注: {os.path.basename(filepath)}")
        return

    # ── 降级：Python 原生库 ─────────────────────────
    text = _format_comment(record)

    if ext == ".png" and _PIL:
        _write_png(filepath, text)
    elif ext in (".jpg", ".jpeg") and _PIEXIF:
        _write_jpeg(filepath, text)
    elif ext in (".mp4", ".mov", ".m4v") and _MUTAGEN_MP4:
        _write_mp4(filepath, text)
    else:
        _log_warn(
            f"无可用写入方式: {os.path.basename(filepath)}"
            f" (exiftool 未找到, ext={ext})"
        )


def _write_png(filepath, text):
    try:
        img = Image.open(filepath)
        pnginfo = PngImagePlugin.PngInfo()
        if hasattr(img, "text"):
            for k, v in img.text.items():
                if k != "MamRecord":
                    pnginfo.add_text(k, v)
        pnginfo.add_text("MamRecord", text)
        img.save(filepath, "PNG", pnginfo=pnginfo)
        _log_info(f"PIL PNG 写入: {os.path.basename(filepath)}")
    except Exception as e:
        _log_warn(f"PIL PNG写入失败: {e}")


def _write_jpeg(filepath, text):
    try:
        try:
            exif_dict = piexif.load(filepath)
        except:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}
        uc = piexif.helper.UserComment.dump(text[:1500], encoding="unicode")
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = uc
        piexif.insert(piexif.dump(exif_dict), filepath)
        _log_info(f"piexif JPEG 写入: {os.path.basename(filepath)}")
    except Exception as e:
        _log_warn(f"piexif JPEG写入失败: {e}")


def _write_mp4(filepath, text):
    try:
        audio = _MP4(filepath)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9cmt"] = [text[:2000]]
        audio.save()
        _log_info(f"mutagen MP4 写入: {os.path.basename(filepath)}")
    except Exception as e:
        _log_warn(f"mutagen MP4写入失败: {e}")


# ─────────────────────────────────────────────────────
# 读取元数据（对外接口）
# ─────────────────────────────────────────────────────
def read_metadata(filepath: str) -> dict | None:
    """
    从文件内嵌字段读取 MAM 记录。优先级：
    1. exiftool 读 UserComment/Comment
    2. PNG 文本块 MamRecord（降级写入时）
    3. JPEG EXIF UserComment（降级写入时）
    4. MP4/MOV ©cmt（降级写入时）
    """
    ext = os.path.splitext(filepath)[1].lower()

    # ── exiftool ───────────────────────────────────
    raw = _exiftool_read(filepath)
    if raw:
        rec = _parse_comment(raw)
        if rec:
            return rec

    # ── PNG 文本块 ─────────────────────────────────
    if ext == ".png" and _PIL:
        try:
            img = Image.open(filepath)
            if hasattr(img, "text") and "MamRecord" in img.text:
                rec = _parse_comment(img.text["MamRecord"])
                if rec:
                    return rec
        except:
            pass

    # ── JPEG EXIF UserComment ──────────────────────
    if ext in (".jpg", ".jpeg") and _PIEXIF:
        try:
            exif_dict = piexif.load(filepath)
            raw_b = exif_dict.get("Exif", {}).get(piexif.ExifIFD.UserComment, b"")
            if raw_b:
                text = piexif.helper.UserComment.load(raw_b)
                if text:
                    rec = _parse_comment(text)
                    if rec:
                        return rec
        except:
            pass

    # ── MP4/MOV ©cmt ──────────────────────────────
    if ext in (".mp4", ".mov", ".m4v") and _MUTAGEN_MP4:
        try:
            audio = _MP4(filepath)
            if audio.tags and "\xa9cmt" in audio.tags:
                text = audio.tags["\xa9cmt"][0]
                rec = _parse_comment(text)
                if rec:
                    return rec
        except:
            pass

    return None


# ─────────────────────────────────────────────────────
# 从元数据或文件名提取 phash
# ─────────────────────────────────────────────────────
def get_phash_from_file(filepath: str, img=None):
    """返回 (phash_str, source_str)"""
    rec = read_metadata(filepath)
    if rec and rec.get("phash"):
        return rec["phash"], "metadata"

    base = os.path.splitext(os.path.basename(filepath))[0]
    if "_" in base:
        cand = base.split("_")[-1]
        if len(cand) == 16 and all(c in "0123456789abcdef" for c in cand.lower()):
            return cand.lower(), "filename"

    from mam_core import get_phash, get_thumbnail
    if img is None:
        img = get_thumbnail(filepath)
    ph = get_phash(img)
    return ph, "computed"


# ─────────────────────────────────────────────────────
# 状态查询
# ─────────────────────────────────────────────────────
def exiftool_status() -> str:
    exe = _find_exiftool()
    if exe:
        return f"✅  exiftool 已就绪: {exe}"
    base = os.path.dirname(os.path.abspath(__file__))
    return f"❌  exiftool 未找到 → Python 原生库降级 | 将 exiftool.exe 放到: {base}"


def check_deps() -> list[str]:
    missing = []
    if not _find_exiftool():
        missing.append("【推荐】exiftool.exe 放入项目目录  # 写Windows备注字段")
    if not _PIL:
        missing.append("pip install Pillow   # PNG 降级方案")
    if not _PIEXIF:
        missing.append("pip install piexif   # JPEG 降级方案")
    if not _MUTAGEN_MP4:
        missing.append("pip install mutagen  # MP4 降级方案")
    return missing


# ─────────────────────────────────────────────────────
def _log_warn(msg): print(f"[mam_meta] ⚠️  {msg}", file=sys.stderr)
def _log_info(msg): print(f"[mam_meta] ✅  {msg}", file=sys.stderr)
