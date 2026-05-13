# mam_gui.py — 主界面（纯 UI，业务逻辑见 mam_core / mam_db / mam_meta）
import sys

import threading
import socket
from werkzeug.serving import make_server

SERVER_IMPORT_ERROR = ""
try:
    from mam_canvatools_server import app as flask_app, run_network_self_check as server_network_self_check
except Exception as e:
    flask_app = None
    server_network_self_check = None
    SERVER_IMPORT_ERROR = str(e)


import os
import re
import json
import time
import traceback
import cv2
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from mam_core import (load_config, save_config, get_phash, get_thumbnail,
                       get_file_size, get_asset_type, make_thumb_bytes,
                       hamming, ALL_EXTS, IMG_EXTS, VID_EXTS,
                       load_producer_codes, save_producer_codes, parse_producer_from_filename)
from mam_db   import DBManager
from mam_meta import write_metadata, read_metadata, get_phash_from_file, check_deps, exiftool_status
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTabWidget, QTableWidget, QTableWidgetItem,
    QMessageBox, QFormLayout, QFrame, QTextEdit, QHeaderView, QScrollArea,
    QDialog, QDialogButtonBox, QTreeWidget, QTreeWidgetItem, QSplitter, QComboBox,
    QFileDialog, QProgressBar, QProgressDialog, QStackedWidget, QSpinBox, QCheckBox,
    QGroupBox
)
from PyQt6.QtCore  import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt6.QtGui   import QPixmap, QImage, QColor, QFont


import sys
import traceback

def global_exception_handler(exc_type, exc_value, exc_traceback):
    print("UNHANDLED EXCEPTION!", file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

sys.excepthook = global_exception_handler

# 兼容层：保留现有方法中的控件名，底层统一使用 PyQt6 原生控件
PushButton = QPushButton
PrimaryPushButton = QPushButton
TransparentPushButton = QPushButton
LineEdit = QLineEdit
TextEdit = QTextEdit
ProgressBar = QProgressBar
TableWidget = QTableWidget
SmoothScrollArea = QScrollArea
CardWidget = QFrame
SimpleCardWidget = QFrame
StrongBodyLabel = QLabel
BodyLabel = QLabel
SubtitleLabel = QLabel
CaptionLabel = QLabel

# ── 全局日志总线 ────────────────────────────────────
class _Bus(QObject):
    sig = pyqtSignal(str)
log_bus = _Bus()
def gui_log(msg): log_bus.sig.emit(msg)

# ── 数据库单例 ───────────────────────────────────────
db = DBManager()


def _diag_log_file() -> str:
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        log_dir = os.path.join(base, 'MAMDesktop', 'logs')
    elif sys.platform == 'darwin':
        log_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Logs', 'MAMDesktop')
    else:
        log_dir = os.path.join(os.path.expanduser('~'), '.mamdesktop', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"mam_gui_{datetime.now().strftime('%Y%m%d')}.log")


DIAG_LOG_FILE = _diag_log_file()


def _append_diag_log(message: str, detail: str = ""):
    try:
        os.makedirs(os.path.dirname(DIAG_LOG_FILE), exist_ok=True)
        with open(DIAG_LOG_FILE, 'a', encoding='utf-8') as f:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"[{ts}] {message}\n")
            if detail:
                f.write(detail.rstrip() + "\n")
    except:
        pass


def _install_exception_hook():
    def _hook(exc_type, exc_value, exc_tb):
        tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _append_diag_log("未捕获异常", tb)
        try:
            QMessageBox.critical(
                None,
                "程序异常",
                f"程序发生未捕获异常：{exc_value}\n\n诊断日志：{DIAG_LOG_FILE}"
            )
        except:
            pass
    sys.excepthook = _hook


# ─── 单实例检测（Windows）────────────────────────────
def _activate_existing_main_window():
    """找到已运行的 MAMDesktop 主窗口并激活到前台。"""
    import ctypes
    import ctypes.wintypes
    user32 = ctypes.windll.user32
    TARGET = "MAM 素材溯源管理系统"
    found = [None]

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )

    def _enum_cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if TARGET in buf.value:
                found[0] = hwnd
                return False   # 找到后停止枚举
        return True

    user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
    hwnd = found[0]
    if hwnd:
        user32.ShowWindow(hwnd, 9)      # SW_RESTORE（最小化时还原）
        user32.SetForegroundWindow(hwnd)


def _single_instance_check() -> bool:
    """
    返回 True 表示本进程是第一个实例，可以继续启动。
    返回 False 表示已有实例在运行，已激活其窗口，当前进程应退出。
    非 Windows 平台始终返回 True。
    """
    if not sys.platform.startswith('win'):
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    MUTEX_NAME = "Local\\MAMDesktop_SingleInstance_v31"
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    err = kernel32.GetLastError()   # 183 = ERROR_ALREADY_EXISTS
    if err == 183:
        _activate_existing_main_window()
        if mutex:
            kernel32.CloseHandle(mutex)
        return False
    # 第一个实例：把 mutex 句柄挂到 builtins，防止 GC 释放
    import builtins
    builtins._mam_single_instance_mutex = mutex
    return True


# ─────────────────────────────────────────────────────
# 辅助：确保素材已在库中（自动登记）
# ─────────────────────────────────────────────────────
def ensure_registered(filepath, operator_name, fill_missing_producer=False, db_inst=None):
    """
    若素材未登记则自动登记并写入元数据。
    fill_missing_producer=True 时，命中旧素材且作者为空，会补成当前人员。
    返回 (phash, record_dict) 或 (None, None)
    """
    fname = os.path.basename(filepath)
    img = get_thumbnail(filepath)
    if img is None:
        gui_log(f"❌ 无法读取: {fname}")
        return None, None

    ph, source = get_phash_from_file(filepath, img)
    if not ph:
        gui_log(f"❌ phash计算失败: {fname}")
        return None, None

    current_db = db_inst or db
    existing = current_db.lookup(ph, threshold=0)
    if existing:
        gui_log(f"🔎 命中库中素材: {fname}  phash:{existing['phash']}")
        producer = (existing.get('producer') or '').strip()
        if fill_missing_producer and not producer and existing.get('distance', 0) == 0:
            if current_db.fill_asset_producer_if_missing(existing['phash'], operator_name):
                existing = dict(existing)
                existing['producer'] = operator_name
                gui_log(f"📝 命中旧素材，已补制作人: {fname} -> {operator_name}")
        return existing['phash'], existing

    # 新素材 → 登记
    atype = get_asset_type(filepath)
    fsize = get_file_size(filepath)
    now   = datetime.now()
    rec   = {
        "phash": ph, "filename": fname,
        "asset_type": atype, "file_size": fsize,
        "producer": operator_name, "created_at": now.isoformat()
    }
    write_metadata(filepath, rec)
    current_db.upsert_asset(ph, fname, atype, fsize, operator_name, now,
                    json.dumps(rec, ensure_ascii=False, default=str),
                    make_thumb_bytes(img))
    gui_log(f"📌 自动登记: {fname}  作者:{operator_name}  phash:{ph}")
    return ph, rec

# ─────────────────────────────────────────────────────
# 拖拽区
# ─────────────────────────────────────────────────────
class DropArea(QFrame):
    filesChanged = pyqtSignal(list)

    def __init__(self, title="拖入文件", multi=False):
        super().__init__()
        self.multi  = multi
        self._files = []
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
        self.setStyleSheet(
            "DropArea{border:2px dashed #c7c7cc;border-radius:10px;background:#fafafa;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight:700;color:#3b4a5a;font-size:12px;")
        lay.addWidget(lbl)
        sc = SmoothScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
        self._box = QWidget(); self._pv = QHBoxLayout(self._box)
        self._pv.setContentsMargins(0, 0, 0, 0)
        self._pv.setSpacing(8)
        self._pv.setAlignment(Qt.AlignmentFlag.AlignLeft); sc.setWidget(self._box)
        lay.addWidget(sc); self._draw()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()

    def dropEvent(self, e):
        added = []
        for u in e.mimeData().urls():
            p = os.path.abspath(u.toLocalFile())
            if os.path.isdir(p):
                for rt, _, fs in os.walk(p):
                    for f in fs:
                        if f.lower().endswith(ALL_EXTS): added.append(os.path.join(rt, f))
            elif p.lower().endswith(ALL_EXTS):
                added.append(p)
        if not added: return
        if self.multi:
            self._files.extend(f for f in added if f not in self._files)
        else:
            self._files = [added[0]]
        self._draw(); self.filesChanged.emit(self._files)

    def _draw(self):
        while self._pv.count():
            w = self._pv.takeAt(0).widget()
            if w: w.deleteLater()
        if not self._files:
            ph = QLabel("拖入文件或文件夹…"); ph.setStyleSheet("color:#aaa;font-size:12px;")
            self._pv.addWidget(ph); return
        for fp in self._files[:60]:
            box = QWidget(); bv = QVBoxLayout(box)
            bv.setContentsMargins(2, 2, 2, 2); bv.setSpacing(4)
            lbl = QLabel(); lbl.setFixedSize(104, 104)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("border:1px solid #c8d4e0;background:#111;border-radius:6px;")
            th = get_thumbnail(fp)
            if th is not None:
                rgb = cv2.cvtColor(th, cv2.COLOR_BGR2RGB); h, w_img, ch = rgb.shape
                qi  = QImage(rgb.data, w_img, h, ch * w_img, QImage.Format.Format_RGB888)
                pm  = QPixmap.fromImage(qi).scaled(
                    98, 98,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                lbl.setPixmap(pm)
            else:
                lbl.setText("?")
            name = os.path.basename(fp)
            short_name = name if len(name) <= 14 else f"{name[:13]}…"
            nm = QLabel(short_name)
            nm.setStyleSheet("font-size:12px;color:#4f5f70;")
            nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
            nm.setFixedWidth(116)
            bv.addWidget(lbl); bv.addWidget(nm); self._pv.addWidget(box)

    def clear(self):
        self._files = []
        self._draw()
        self.filesChanged.emit(self._files)
    def files(self): return list(self._files)
    def file(self):  return self._files[0] if self._files else None


class FolderDropArea(QFrame):
    foldersChanged = pyqtSignal(list)

    def __init__(self, title="拖入文件夹", multi=True):
        super().__init__()
        self.multi = multi
        self._folders = []
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setStyleSheet(
            "FolderDropArea{border:2px dashed #c7c7cc;border-radius:10px;background:#fbfcfe;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight:700;color:#3b4a5a;font-size:12px;")
        lay.addWidget(lbl)
        sc = SmoothScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        self._box = QWidget()
        self._pv = QVBoxLayout(self._box)
        self._pv.setContentsMargins(0, 0, 0, 0)
        self._pv.setSpacing(4)
        self._pv.setAlignment(Qt.AlignmentFlag.AlignTop)
        sc.setWidget(self._box)
        lay.addWidget(sc)
        self._draw()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        added = []
        for u in e.mimeData().urls():
            p = os.path.abspath(u.toLocalFile())
            if os.path.isdir(p):
                added.append(p)
            elif os.path.isfile(p):
                added.append(os.path.dirname(p))
        if not added:
            return
        ordered = []
        seen = set()
        for p in added:
            if p not in seen:
                seen.add(p)
                ordered.append(p)
        if self.multi:
            for p in ordered:
                if p not in self._folders:
                    self._folders.append(p)
        else:
            self._folders = [ordered[0]]
        self._draw()
        self.foldersChanged.emit(self._folders)

    def _draw(self):
        while self._pv.count():
            w = self._pv.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self._folders:
            ph = QLabel("拖入总目录或子目录…")
            ph.setStyleSheet("color:#98a5b3;font-size:12px;")
            self._pv.addWidget(ph)
            return
        for fd in self._folders[:80]:
            name = os.path.basename(fd.rstrip('/\\')) or fd
            row = QLabel(f"📁 {name}")
            row.setToolTip(fd)
            row.setStyleSheet(
                "background:#f2f7fd;border:1px solid #d6e3f0;border-radius:6px;"
                "padding:5px 8px;color:#2b4a66;font-size:12px;"
            )
            self._pv.addWidget(row)

    def clear(self):
        self._folders = []
        self._draw()
        self.foldersChanged.emit(self._folders)

    def folders(self):
        return list(self._folders)

    def folder(self):
        return self._folders[0] if self._folders else None

# ─────────────────────────────────────────────────────
# 批量扫描线程（支持随时停止）
# ─────────────────────────────────────────────────────
class ScanWorker(QThread):
    progress = pyqtSignal(int, int, int, int, int)  # total, done, added, skipped, failed
    log_line = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, folder, operator, known_phashes, code_map=None,
                 workers=4, upload_batch=120):
        super().__init__()
        self._folder       = folder
        self._operator     = operator
        self._known        = set(known_phashes)   # 线程内独立副本
        self._code_map     = code_map or {}
        self._should_stop  = False
        self._workers      = max(1, int(workers))
        self._upload_batch = max(20, int(upload_batch))

    def stop(self):
        self._should_stop = True

    def _build_asset_payload(self, fp):
        """在线程池中执行：读取文件、计算 phash、生成写库载荷。"""
        if self._should_stop:
            return {'status': 'stopped', 'fp': fp}
        img = get_thumbnail(fp)
        if img is None:
            return {'status': 'failed', 'fp': fp, 'error': '无法读取缩略图'}

        ph, _ = get_phash_from_file(fp, img)
        if not ph:
            return {'status': 'failed', 'fp': fp, 'error': 'phash 计算失败'}

        fname = os.path.basename(fp)
        atype = get_asset_type(fp)
        fsize = get_file_size(fp)
        now = datetime.now()
        producer = parse_producer_from_filename(fname, self._code_map)
        if not producer:
            producer = "未知"
        rec = {
            "phash": ph,
            "filename": fname,
            "asset_type": atype,
            "file_size": fsize,
            "producer": producer,
            "created_at": now.isoformat(),
        }

        write_metadata(fp, rec)
        payload = (
            ph, fname, atype, fsize, producer, now,
            json.dumps(rec, ensure_ascii=False, default=str),
            make_thumb_bytes(img)
        )
        return {
            'status': 'ok',
            'fp': fp,
            'fname': fname,
            'phash': ph,
            'payload': payload,
        }

    def run(self):
        folder      = self._folder
        folder_name = os.path.basename(folder.rstrip('/\\'))
        # 自动识别 Canva 文件夹名中的 【ID】
        m_id        = re.search(r'【【(\d+|[a-zA-Z0-9_]+)】】', folder_name)
        canva_id    = m_id.group(1) if m_id else None
        canva_name  = re.sub(r'【.*?】', '', folder_name).strip() if m_id else None

        # 尝试在目录中寻找 tracker_CANVA_*.json 获取正确的制作人
        canva_creator = self._operator
        try:
            for f in os.listdir(folder):
                if f.startswith("tracker_CANVA_") and f.endswith(".json"):
                    with open(os.path.join(folder, f), "r", encoding="utf-8") as jf:
                        j_data = json.load(jf)
                        if j_data.get("creator"):
                            canva_creator = j_data["creator"]
                            if not canva_id and j_data.get("template_id"):
                                canva_id = j_data["template_id"]
                    break
        except:
            pass

        # ── 遍历收集所有媒体文件 ────────────────────────
        self.log_line.emit(f"📂 正在扫描文件列表: {folder}")
        all_files = []
        for rt, _, fs in os.walk(folder):
            for f in fs:
                if f.lower().endswith(ALL_EXTS):
                    all_files.append(os.path.join(rt, f))
        total = len(all_files)
        if canva_id:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件  |  🎨 Canva模板ID: 【{canva_id}】(制作人:{canva_creator})")
        else:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件")
        if total == 0:
            self.finished.emit({'total': 0, 'added': 0, 'skipped': 0,
                                'failed': 0, 'canva_id': canva_id, 'stopped': False})
            return

        # 扫描线程使用独立数据库连接，避免和 UI 操作共享连接导致卡顿
        scan_db = DBManager()
        if not scan_db.clone_auth(db):
            ok, msg = scan_db.connect()
            if not ok:
                self.log_line.emit(f"❌ 扫描线程 API 鉴权失败: {msg}")
            self.finished.emit({'total': total, 'added': 0, 'skipped': 0,
                                'failed': total, 'canva_id': canva_id, 'stopped': False})
            return

        added = skipped = failed = done = 0
        canva_phashes = []

        upload_buffer = []
        executor = ThreadPoolExecutor(max_workers=self._workers)
        futures = [executor.submit(self._build_asset_payload, fp) for fp in all_files]
        try:
            for future in as_completed(futures):
                if self._should_stop:
                    for f in futures:
                        f.cancel()
                    break

                done += 1
                try:
                    result = future.result()
                except Exception as e:
                    failed += 1
                    self.log_line.emit(f"❌ 处理异常: {str(e)[:80]}")
                    self.progress.emit(total, done, added, skipped, failed)
                    continue

                status = result.get('status')
                if status == 'failed':
                    failed += 1
                    self.log_line.emit(f"❌ {os.path.basename(result.get('fp', ''))}: {result.get('error', '处理失败')}")
                    self.progress.emit(total, done, added, skipped, failed)
                    continue
                if status == 'stopped':
                    self.progress.emit(total, done, added, skipped, failed)
                    continue

                ph = result['phash']
                if ph in self._known:
                    skipped += 1
                    if canva_id:
                        canva_phashes.append(ph)
                    self.progress.emit(total, done, added, skipped, failed)
                    continue

                self._known.add(ph)
                upload_buffer.append(result['payload'])
                added += 1
                if canva_id:
                    canva_phashes.append(ph)

                if len(upload_buffer) >= self._upload_batch:
                    scan_db.upsert_assets_bulk(upload_buffer)
                    upload_buffer.clear()

                if added % 100 == 0:
                    self.log_line.emit(f"✅ 已新增 {added} 个素材")

                self.progress.emit(total, done, added, skipped, failed)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if upload_buffer:
            scan_db.upsert_assets_bulk(upload_buffer)

        # ── Canva 模板自动登记 ───────────────────────────
        if canva_id and canva_phashes:
            try:
                unique_ph = list(dict.fromkeys(canva_phashes))
                scan_db.add_canva_template(canva_id, canva_name, canva_creator, unique_ph, "常规入库扫描登记")
                self.log_line.emit(
                    f"🎨 Canva模板【{canva_id}】({canva_name}) 已登记，制作人:{canva_creator}，关联{len(unique_ph)}个素材"
                )
            except Exception as e:
                self.log_line.emit(f"⚠️ Canva模板登记失败: {e}")

        scan_db.close()

        self.finished.emit({
            'total': total, 'added': added, 'skipped': skipped,
            'failed': failed, 'canva_id': canva_id, 'stopped': self._should_stop
        })


# ─────────────────────────────────────────────────────
# 后台线程
# ─────────────────────────────────────────────────────
class Worker(QThread):
    done     = pyqtSignal(object)
    error    = pyqtSignal(str)
    progress = pyqtSignal(int)  # 0-100
    def __init__(self, fn): super().__init__(); self._fn = fn
    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            _append_diag_log("后台任务异常", traceback.format_exc())
            self.error.emit(str(e))

# ─────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────


class JSONDropArea(QFrame):
    filesChanged = pyqtSignal(list)
    def __init__(self, title="拖入 JSON 文件或包含 JSON 的文件夹"):
        super().__init__()
        self._files = []
        self.setAcceptDrops(True)
        self.setMinimumHeight(80)
        self.setStyleSheet("border:2px dashed #c7c7cc;border-radius:10px;background:#fafafa;")
        lay = QVBoxLayout(self)
        self.lbl = QLabel(title)
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl.setStyleSheet("color:#aaa;font-size:14px;")
        lay.addWidget(self.lbl)
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls(): e.acceptProposedAction()
    def dropEvent(self, e):
        added = []
        for u in e.mimeData().urls():
            p = os.path.abspath(u.toLocalFile())
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith('.json'):
                            added.append(os.path.join(root, f))
            elif p.lower().endswith('.json'):
                added.append(p)
        if added:
            self._files = added
            if len(self._files) == 1:
                self.lbl.setText(f"已选择: {os.path.basename(self._files[0])}")
            else:
                self.lbl.setText(f"已选择: {len(self._files)} 个 JSON 文件")
            self.filesChanged.emit(self._files)
    def clear(self):
        self._files = []
        self.lbl.setText("拖入 JSON 文件或包含 JSON 的文件夹")
        self.filesChanged.emit([])
    def files(self):
        return self._files
    def file(self):
        return self._files[0] if self._files else None

class BatchDeriveWorker(QThread):
    progress = pyqtSignal(int, int, int, int)
    log_line = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, json_paths, operator):
        super().__init__()
        self._json_paths = json_paths
        self._operator = operator
        self._should_stop = False

    def stop(self):
        self._should_stop = True

    def run(self):
        all_data = []
        
        for jp in self._json_paths:
            if self._should_stop: return
            try:
                import json
                with open(jp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    base_dir = os.path.dirname(jp)
                    for item in data:
                        item['_base_dir'] = base_dir
                    all_data.extend(data)
            except Exception as e:
                self.log_line.emit(f"❌ 读取JSON失败 ({os.path.basename(jp)}): {e}")

        if not all_data:
            self.log_line.emit("❌ 未找到任何有效的衍生记录")
            self.finished.emit({'total': 0, 'done': 0, 'success': 0, 'failed': 0})
            return

        total = len(all_data)
        done = success = failed = 0
        self.log_line.emit(f"📂 共发现 {total} 条衍生记录准备处理")

        thread_db = DBManager()
        if not thread_db.clone_auth(db):
            ok, msg = thread_db.connect(init_tables=False, warm_cache=False)
            if not ok:
                self.log_line.emit(f"❌ API 鉴权失败: {msg}")
            self.finished.emit({'total': total, 'done': 0, 'success': 0, 'failed': total})
            return

        try:
            for item in all_data:
                if self._should_stop:
                    break

                done += 1
                base_dir = item.pop('_base_dir', '')
                sources = item.get("source", [])
                target = item.get("target", "")

                if not sources or not target:
                    failed += 1
                    self.log_line.emit(f"⚠️ 记录缺少source或target: 第 {done} 条")
                    self.progress.emit(total, done, success, failed)
                    continue

                if not os.path.isabs(target):
                    target = os.path.join(base_dir, target)
                
                try:
                    dst_phash, _ = ensure_registered(target, self._operator, fill_missing_producer=True)
                    if not dst_phash:
                        failed += 1
                        self.log_line.emit(f"❌ target注册失败: {target}")
                        self.progress.emit(total, done, success, failed)
                        continue
                except Exception as e:
                    failed += 1
                    self.log_line.emit(f"❌ target处理异常 {target}: {e}")
                    self.progress.emit(total, done, success, failed)
                    continue

                item_success = True
                for src in sources:
                    if not os.path.isabs(src):
                        src = os.path.join(base_dir, src)
                    try:
                        src_phash, _ = ensure_registered(src, self._operator, fill_missing_producer=True)
                        if not src_phash:
                            item_success = False
                            self.log_line.emit(f"❌ source注册失败: {src}")
                            continue

                        thread_db.add_derive(src_phash, dst_phash, "生图衍生", self._operator, remark="JSON批量导入")
                    except Exception as e:
                        item_success = False
                        self.log_line.emit(f"❌ source处理异常 {src}: {e}")

                if item_success:
                    success += 1
                    if success % 10 == 0:
                        self.log_line.emit(f"✅ 已成功建立 {success} 条衍生关系")
                else:
                    failed += 1
                    
                self.progress.emit(total, done, success, failed)
        finally:
            thread_db.close()

        self.finished.emit({'total': total, 'done': done, 'success': success, 'failed': failed})


import zipfile
import hashlib


import tempfile
import queue

class FastBindWorker(QThread):
    progress = pyqtSignal(int, int)
    log_line = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, data, operator):
        super().__init__()
        self.data = data
        self.operator = operator

    def run(self):
        canva_id = str(self.data.get('canvaId', '')).replace('【', '').replace('】', '')
        assets = self.data.get('assets', [])
        
        if not canva_id or not assets:
            self.log_line.emit("❌ 极速登记失败: 缺失必要参数")
            self.finished.emit({"success": False})
            return

        self.log_line.emit(f"🎬 极速极速纯哈希登记开始: 编号【{canva_id}】，素材数: {len(assets)}")
        
        thread_db = DBManager()
        if not thread_db.clone_auth(db):
            ok, msg = thread_db.connect(init_tables=False, warm_cache=False)
            if not ok:
                self.log_line.emit(f"❌ 数据库连接失败: {msg}")
                self.finished.emit({"success": False})
                return

        from mam_canvatools_server import download_bytes, guess_extension
        import shutil

        temp_dir = tempfile.mkdtemp(prefix="canva_fastbind_")
        phashes = []
        
        try:
            for idx, a in enumerate(assets):
                try:
                    url = a.get('url')
                    name = a.get('name', f"asset_{idx}")
                    content_bytes, headers = download_bytes(url)
                    
                    if not "." in name:
                        content_type = headers.get("content-type", "")
                        ext = guess_extension(content_type, ".bin")
                        name = f"{name}{ext}"
                    
                    fpath = os.path.join(temp_dir, name)
                    with open(fpath, 'wb') as f:
                        f.write(content_bytes)
                    
                    self.log_line.emit(f"  📥 下载完毕: {name} ({len(content_bytes)} bytes)")
                    
                    ph, rec = ensure_registered(fpath, self.operator, fill_missing_producer=True)
                    if ph:
                        phashes.append(ph)
                        self.log_line.emit(f"  ✅ 登记成功: Hash {ph}")
                    
                except Exception as e:
                    self.log_line.emit(f"  ❌ 登记失败 ({a.get('name')}): {e}")
                
                self.progress.emit(len(assets), idx + 1)

            if phashes:
                tmpl_creator = self.operator
                tracker = self.data.get('canvaTracker')
                if tracker and isinstance(tracker, dict):
                    tmpl_creator = tracker.get('creator', self.operator)
                thread_db.add_canva_template(canva_id, canva_id, tmpl_creator, phashes, "前端极速登记")
                self.log_line.emit(f"✅ 编号【{canva_id}】与 {len(phashes)} 个素材绑定成功！(制作者: {tmpl_creator})")
            else:
                self.log_line.emit("⚠️ 没有产生任何有效的哈希记录。")
                
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except: pass
            thread_db.close()

        self.finished.emit({"success": True})


class CanvaAutoMonitorWorker(QThread):
    found_jsons = pyqtSignal(list)
    found_canva_products = pyqtSignal(list, str, object)
    log_line = pyqtSignal(str)

    def __init__(self, save_dir, processed_file):
        super().__init__()
        self.save_dir = save_dir
        self.processed_file = processed_file
        self._should_stop = False
        self._processed = []
        self._load_processed()

    def _load_processed(self):
        if os.path.exists(self.processed_file):
            try:
                import json
                with open(self.processed_file, 'r', encoding='utf-8') as f:
                    self._processed = json.load(f)
            except:
                self._processed = []

    def _save_processed(self):
        try:
            import json
            with open(self.processed_file, 'w', encoding='utf-8') as f:
                json.dump(self._processed, f)
        except:
            pass

    def stop(self):
        self._should_stop = True

    def run(self):
        import time
        import re
        self.log_line.emit(f"👁 启动统一自动监控引擎: {self.save_dir}")
        
        def extract_tid(name):
            m = re.search(r'【(\d+)】', name)
            return m.group(1) if m else None
            
        MEDIA_EXTS = ('.mp4', '.mov', '.png', '.jpg', '.jpeg', '.gif', '.webp')

        while not self._should_stop:
            try:
                if os.path.exists(self.save_dir):
                    for fname in os.listdir(self.save_dir):
                        # Avoid processing incomplete files
                        if fname.lower().endswith('.crdownload') or fname.lower().endswith('.tmp'):
                            continue
                            
                        fpath = os.path.join(self.save_dir, fname)
                        if fname in self._processed or not os.path.isfile(fpath):
                            continue
                            
                        # Only process zip or media files
                        is_zip = fname.lower().endswith('.zip')
                        is_media = fname.lower().endswith(MEDIA_EXTS)
                        
                        if not is_zip and not is_media:
                            continue

                        # Wait for file to finish downloading/writing
                        try:
                            size_before = os.path.getsize(fpath)
                            time.sleep(1)
                            size_after = os.path.getsize(fpath)
                            if size_before != size_after or size_before == 0:
                                continue
                        except:
                            continue
                            
                        if is_zip:
                            self.log_line.emit(f"📦 发现新压缩包: {fname}，统一安全解压中...")
                            extract_dir = os.path.join(self.save_dir, os.path.splitext(fname)[0])
                            os.makedirs(extract_dir, exist_ok=True)
                            
                            try:
                                import zipfile
                                with zipfile.ZipFile(fpath, 'r') as zf:
                                    zf.extractall(extract_dir)
                                
                                self._processed.append(fname)
                                self._save_processed()
                                
                                # Route A: Batch Derive (check JSON)
                                found_jsons = []
                                media_files = []
                                tracker_data = None
                                for root, _, _files in os.walk(extract_dir):
                                    for f in _files:
                                        if f == 'canva_tracker.json':
                                            try:
                                                import json
                                                with open(os.path.join(root, f), 'r', encoding='utf-8') as tf:
                                                    tracker_data = json.load(tf)
                                            except: pass
                                        elif f.lower().endswith('.json'):
                                            found_jsons.append(os.path.join(root, f))
                                        elif f.lower().endswith(MEDIA_EXTS):
                                            if root == extract_dir:
                                                media_files.append(os.path.join(root, f))

                                if found_jsons:
                                    self.found_jsons.emit(found_jsons)
                                
                                # Route B: Canva Batch Products
                                tid = extract_tid(fname)
                                if tid or tracker_data:
                                    self.found_canva_products.emit(media_files, tid or "", tracker_data)
                                    
                            except Exception as e:
                                self.log_line.emit(f"❌ 解压或处理失败: {e}")
                                
                        elif is_media:
                            tid = extract_tid(fname)
                            if tid:
                                self.log_line.emit(f"🎥 发现新单体成品: {fname} (ID:{tid})")
                                self._processed.append(fname)
                                self._save_processed()
                                self.found_canva_products.emit([fpath], tid, None)

            except Exception as e:
                pass
            time.sleep(3)
        self.log_line.emit("⏸ 自动监控已停止。")


class MamApp(QMainWindow):
    canva_log_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAM 素材溯源管理系统 v3.2.0")
        self.setMinimumSize(1280, 920)
        self._cfg     = load_config()
        self._workers = []
        self._lib_data = []
        self._last_canva_id = None

        self._canva_auto_worker = None
        self._auto_monitor_processed_file = os.path.join(os.path.expanduser('~'), '.canva_tools_processed.json')

        import queue
        self._fast_bind_queue = queue.Queue()
        try:
            from mam_canvatools_server import app as flask_app
            flask_app.config['FAST_BIND_QUEUE'] = self._fast_bind_queue
        except: pass
        
        self._fast_bind_timer = QTimer(self)
        self._fast_bind_timer.timeout.connect(self._check_fast_bind_queue)
        self._fast_bind_timer.start(500)
        
        self._canva_auto_worker = None
        self._auto_monitor_processed_file = os.path.join(os.path.expanduser('~'), '.canva_tools_processed.json')

        self._current_worker = None  # 当前后台 worker，用于发送进度信号
        self._code_table_dirty = False
        self._build_ui()
        log_bus.sig.connect(self._log)
        self.canva_log_sig.connect(self._canva_append_log)
        self._log(f"🧭 诊断日志: {DIAG_LOG_FILE}")
        # 延迟连接数据库，避免窗口显示前卡住主线程
        QTimer.singleShot(200, self._init_db_connect)
        # Auto-start CanvaTools if enabled
        try:
            import json
            canvatools_config_file = os.path.join(os.path.expanduser('~'), '.canva_tools_config.json')
            if os.path.exists(canvatools_config_file):
                with open(canvatools_config_file, 'r', encoding='utf-8') as f:
                    c_cfg = json.load(f)
                    if c_cfg.get('autostart', False):
                        QTimer.singleShot(1500, self._start_canva_server)
        except: pass

        # exiftool 状态
        self._log(exiftool_status())
        # 检查 Python 依赖
        missing = check_deps()
        for m in missing:
            self._log(f"⚠️ 缺少依赖: {m}")

    def _init_db_connect(self):
        """窗口显示后在后台线程初始化连接，不阻塞 UI。"""
        self._log("⏳ 正在连接服务器获取Token...")
        w = Worker(lambda: db.connect())
        w.setParent(self)

        def _on_db_init_done(result):
            ok, msg = result
            self._log("✅ 服务器连接成功" if ok else f"⚠️ 服务器: {msg}")
            if ok:
                self._refresh_code_table_after_db_connect()

        w.done.connect(_on_db_init_done)
        w.error.connect(lambda e: self._log(f"⚠️ 服务器连接异常: {e}"))
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _refresh_code_table_after_db_connect(self):
        """数据库连通后刷新人员代码表，避免扫描时 code_map 为空。"""
        if not hasattr(self, '_code_table'):
            return
        if self._code_table_dirty:
            self._log("ℹ️ 人员代码有未保存修改，跳过自动刷新")
            return
        old_count = self._code_table.rowCount()
        self._load_code_table()
        new_count = self._code_table.rowCount()
        if new_count != old_count:
            self._log(f"👤 已加载人员名单：{new_count} 条")

    def _recommended_workers(self, cap=8):
        cpu = os.cpu_count() or 4
        return max(2, min(cap, cpu))

    # ═══════════════════ UI 构建 ═══════════════════════
    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # ── 左侧导航区
        nav = QFrame(); nav.setObjectName("navPane")
        nav.setMinimumWidth(238); nav.setMaximumWidth(300)
        nav_l = QVBoxLayout(nav)
        nav_l.setContentsMargins(18, 18, 18, 18)
        nav_l.setSpacing(10)

        brand = QLabel("素材溯源管理")
        brand.setObjectName("brandTitle")
        nav_l.addWidget(brand)
        sub = QLabel("MAM Desktop")
        sub.setObjectName("brandSub")
        nav_l.addWidget(sub)

        self._lbl_user = QLabel(f"操作员 · {self._cfg['user_name']}")
        self._lbl_user.setObjectName("userBadge")
        nav_l.addWidget(self._lbl_user)
        nav_l.addSpacing(8)

        self._main_stack = QStackedWidget()
        self._page_names = []
        self._nav_buttons = []

        pages = [
            ("登记中心", "📥", self._tab_register()),
            ("衍生关联", "🔗", self._tab_derive()),
            ("成品封装", "🔒", self._tab_compose()),
            ("批量封装", "📁", self._tab_compose_batch()),
            ("Canva 模板", "🎨", self._tab_canva()),
            ("CanvaTools", "🛠", self._tab_canvatools()),
            ("Canva批量", "🗃", self._tab_canva_batch()),
            ("溯源查询", "🔍", self._tab_query()),
            ("素材总览", "🗂", self._tab_library()),
                        ("批量衍生", "🎬", self._tab_derive_batch()),
            ("批量扫描", "⚡", self._tab_batch_scan()),
        ]

        for idx, (name, icon, page) in enumerate(pages):
            self._main_stack.addWidget(page)
            self._page_names.append(name)
            btn = QPushButton(f"{icon}  {name}")
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, i=idx: self._switch_main_page(i))
            self._nav_buttons.append(btn)
            nav_l.addWidget(btn)

        nav_l.addStretch(1)
        btn_cfg = QPushButton("系统设置")
        btn_cfg.setObjectName("ghostButton")
        btn_cfg.clicked.connect(self._dlg_settings)
        nav_l.addWidget(btn_cfg)
        shell.addWidget(nav)

        # ── 右侧主内容区
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(18, 14, 18, 14)
        right_l.setSpacing(10)

        top = QFrame(); top.setObjectName("topCard")
        top_l = QHBoxLayout(top)
        top_l.setContentsMargins(16, 12, 16, 12)
        self._page_title = QLabel("登记中心")
        self._page_title.setObjectName("pageTitle")
        self._page_hint = QLabel("保持原有业务功能，全面升级视觉与结构。")
        self._page_hint.setObjectName("pageHint")
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(self._page_title)
        title_col.addWidget(self._page_hint)
        top_l.addLayout(title_col)
        top_l.addStretch(1)
        right_l.addWidget(top)

        right_l.addWidget(self._main_stack, 1)

        log_card = QFrame(); log_card.setObjectName("logCard")
        log_l = QVBoxLayout(log_card)
        log_l.setContentsMargins(12, 10, 12, 10)
        log_l.setSpacing(8)
        log_title_layout = QHBoxLayout()
        log_title = QLabel("运行日志")
        log_title.setObjectName("logTitle")
        btn_open_log = QPushButton("打开日志目录")
        btn_open_log.clicked.connect(self._open_log_dir)
        btn_open_log.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; padding: 2px 8px;")
        log_title_layout.addWidget(log_title)
        log_title_layout.addStretch(1)
        log_title_layout.addWidget(btn_open_log)
        
        # 添加进度条
        self._progress = QProgressBar()
        self._progress.setMaximumHeight(20)
        self._progress.setTextVisible(True)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #dbe4ee; border-radius: 4px; background: #f5f5f7; }"
            "QProgressBar::chunk { background: #2980b9; border-radius: 3px; }"
        )
        self._progress.hide()  # 默认隐藏，任务时显示
        
        self._log_box = TextEdit(); self._log_box.setReadOnly(True)
        self._log_box.setMinimumHeight(200)
        self._log_box.setObjectName("logbox")
        log_l.addLayout(log_title_layout)
        log_l.addWidget(self._progress)
        log_l.addWidget(self._log_box)
        right_l.addWidget(log_card)

        shell.addWidget(right, 1)
        self._switch_main_page(0)

    def _switch_main_page(self, index: int):
        self._main_stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)
        if 0 <= index < len(self._page_names):
            self._page_title.setText(self._page_names[index])

    def _clear_register_inputs(self):
        self._drop_raw.clear()

    def _clear_derive_batch_inputs(self):
        if hasattr(self, '_drop_derive_batch'):
            self._drop_derive_batch.clear()

    def _clear_derive_inputs(self):
        self._drop_src.clear()
        self._drop_dst.clear()
        self._update_rel_type_label()

    def _clear_compose_inputs(self):
        self._drop_parts.clear()
        self._drop_product.clear()

    def _clear_canva_inputs(self):
        self._drop_canva.clear()
        self._canva_name.clear()
        self._canva_remark.clear()
        self._canva_id_lbl.setText("(点击生成后显示)")
        self._btn_copy_canva.setEnabled(False)
        self._last_canva_id = None
        
        self._canva_auto_worker = None
        self._auto_monitor_processed_file = os.path.join(os.path.expanduser('~'), '.canva_tools_processed.json')


    def _clear_compose_batch_inputs(self):
        if hasattr(self, '_drop_compose_batch'):
            self._drop_compose_batch.clear()
        self._compose_pending_jobs = {}
        if hasattr(self, '_compose_batch_status'):
            self._compose_batch_status.setText("等待开始…")
        if hasattr(self, '_compose_pending_status'):
            self._compose_pending_status.setText("待确认目录：0")
        if hasattr(self, '_compose_pending_list_lay'):
            self._render_compose_pending_jobs()

    def _clear_canva_batch_inputs(self):
        if hasattr(self, '_drop_canva_batch'):
            self._drop_canva_batch.clear()

    def _clear_query_inputs(self):
        self._drop_query.clear()
        self._clear_query_results()

    def _tab_register(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("拖入原始素材，系统自动计算 phash 并写入文件元数据（备注字段）和数据库"))
        self._drop_raw = DropArea("拖入素材（可多个）", multi=True); v.addWidget(self._drop_raw)

        action = QHBoxLayout(); action.setSpacing(10)
        btn = PushButton("⚡  执行批量登记")
        btn.setStyleSheet("background:#2980b9;color:#fff;height:42px;font-size:14px;border:none;border-radius:9px;")
        btn.clicked.connect(self._do_register)
        btn_clr = PushButton("🗑  清空素材")
        btn_clr.setMinimumWidth(160)
        btn_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:9px;")
        btn_clr.clicked.connect(self._clear_register_inputs)
        action.addWidget(btn, 1); action.addWidget(btn_clr)
        v.addLayout(action)
        return w



    # ── Tab: CanvaTools ──────────────────────────────────
    def _tab_canvatools(self):
        w = QWidget(); layout = QVBoxLayout(w)
        layout.setContentsMargins(14, 10, 14, 10); layout.setSpacing(10)
        
        instruction = QLabel('说明：打开软件点击启动服务即可驻留后台运行，直接在Canva内使用插件打包。')
        instruction.setWordWrap(True)
        instruction.setStyleSheet("color: #666; font-size: 13px;")
        layout.addWidget(instruction)

        # Config paths
        self.canvatools_config_file = os.path.join(os.path.expanduser('~'), '.canva_tools_config.json')
        self.canvatools_config = {}
        if os.path.exists(self.canvatools_config_file):
            try:
                import json
                with open(self.canvatools_config_file, 'r', encoding='utf-8') as f:
                    self.canvatools_config = json.load(f)
            except:
                pass

        # Canva App Bind Group
        canva_group = QGroupBox("Canva 应用绑定")
        canva_layout = QFormLayout()

        self.canva_app_id_input = QLineEdit(self.canvatools_config.get('canva_app_id', ''))
        self.canva_app_id_input.setPlaceholderText("例: AAFevuEFx08 （从 Canva 开发者后台复制）")
        self.canva_app_id_input.setEchoMode(QLineEdit.EchoMode.Password)
        canva_layout.addRow("App ID:", self.canva_app_id_input)

        saved_app_id = self.canvatools_config.get('canva_app_id', '')
        self.canva_bind_status_label = QLabel(
            "✓ 已绑定（已隐藏）" if saved_app_id else "未绑定（入库请求将被拒绝）"
        )
        self.canva_bind_status_label.setStyleSheet("color: #388e3c;" if saved_app_id else "color: #d32f2f;")
        canva_layout.addRow("状态:", self.canva_bind_status_label)

        canva_btn_row = QHBoxLayout()
        bind_btn = QPushButton("绑定应用")
        bind_btn.clicked.connect(self._on_bind_canva_app)
        canva_btn_row.addWidget(bind_btn)
        
        canva_btn_widget = QWidget()
        canva_btn_widget.setLayout(canva_btn_row)
        canva_layout.addRow("", canva_btn_widget)
        canva_group.setLayout(canva_layout)
        layout.addWidget(canva_group)

        # Local Service Config
        port_group = QGroupBox("本地服务配置")
        port_layout = QFormLayout()
        
        saved_port = int(self.canvatools_config.get('port', '3001') or 3001)
        self.port_backend = QLineEdit(str(saved_port))
        port_layout.addRow("通信端口:", self.port_backend)
        
        default_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        saved_path = self.canvatools_config.get('save_path', default_desktop)
        self.save_path_input = QLineEdit(saved_path)
        self.save_path_input.textChanged.connect(self._save_canva_config)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._on_browse_canva_save_path)
        
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.save_path_input)
        path_layout.addWidget(browse_btn)
        
        port_layout.addRow("自动保存路径:", path_layout)
        port_group.setLayout(port_layout)
        layout.addWidget(port_group)

        # Buttons
        btn_layout = QHBoxLayout()
        self.start_canva_btn = QPushButton('▶ 启动服务')
        self.start_canva_btn.setMinimumHeight(40)
        self.start_canva_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; border-radius: 6px;")
        self.start_canva_btn.clicked.connect(self._start_canva_server)
        btn_layout.addWidget(self.start_canva_btn)

        self.stop_canva_btn = QPushButton('⏹ 停止服务')
        self.stop_canva_btn.setMinimumHeight(40)
        self.stop_canva_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; border-radius: 6px;")
        self.stop_canva_btn.clicked.connect(self._stop_canva_server)
        self.stop_canva_btn.setEnabled(False)
        btn_layout.addWidget(self.stop_canva_btn)

        layout.addLayout(btn_layout)
        
        # Additional settings
        settings_group = QGroupBox("高级与自动化")
        settings_layout = QVBoxLayout()
        
        self._chk_canva_autostart = QCheckBox("随系统自动启动服务")
        is_autostart = self.canvatools_config.get('autostart', False)
        self._chk_canva_autostart.setChecked(is_autostart)
        self._chk_canva_autostart.toggled.connect(lambda: self._save_canva_config())
        settings_layout.addWidget(self._chk_canva_autostart)
        
        self._chk_canva_hashmode = QCheckBox("开启模板制作人员专有模式 (展示隐形写入按钮)")
        is_hashmode = self.canvatools_config.get('hash_only_mode', False)
        self._chk_canva_hashmode.setChecked(is_hashmode)
        self._chk_canva_hashmode.toggled.connect(lambda: self._save_canva_config())
        settings_layout.addWidget(self._chk_canva_hashmode)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)

        layout.addStretch(1)

        self.werkzeug_server = None
        self._canva_server_thread = None

        return w

    def _canva_append_log(self, text):
        self._log(f"[CanvaTools] {text}")

    def _save_canva_config(self):
        try:
            self.canvatools_config = {
                'port': self.port_backend.text().strip(),
                'canva_app_id': self.canva_app_id_input.text().strip(),
                'save_path': self.save_path_input.text().strip(),
                'autostart': getattr(self, '_chk_canva_autostart', None) is not None and self._chk_canva_autostart.isChecked(),
                'hash_only_mode': getattr(self, '_chk_canva_hashmode', None) is not None and self._chk_canva_hashmode.isChecked(),
            }
            import json
            with open(self.canvatools_config_file, 'w', encoding='utf-8') as f:
                json.dump(self.canvatools_config, f, ensure_ascii=False, indent=2)
            os.environ['STANDALONE_SAVE_PATH'] = self.canvatools_config['save_path']
        except Exception as e:
            pass

    def _on_bind_canva_app(self):
        app_id = self.canva_app_id_input.text().strip()
        if not app_id:
            self.canva_bind_status_label.setText("请先填写 App ID")
            self.canva_bind_status_label.setStyleSheet("color: #d32f2f;")
            return
        self._save_canva_config()
        self.canva_bind_status_label.setText("✓ 已绑定（已隐藏）")
        self.canva_bind_status_label.setStyleSheet("color: #388e3c;")
        self.canva_log_sig.emit("[*] Canva App 已绑定。")
        QMessageBox.information(self, "绑定成功", "已成功绑定 Canva App。服务器将仅接受来自该 App 的入库请求。")

    def _on_browse_canva_save_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择打包保存目录", self.save_path_input.text())
        if dir_path:
            self.save_path_input.setText(dir_path)
            self._save_canva_config()

    def _check_port_in_use(self, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('localhost', port)) == 0

    def _start_canva_server(self):
        if flask_app is None:
            detail = SERVER_IMPORT_ERROR or "未知错误"
            QMessageBox.critical(self, "错误", f"后端模块加载失败。\n\n详细原因: {detail}")
            return

        try:
            port = int(self.port_backend.text())
        except ValueError:
            QMessageBox.warning(self, "错误", "端口必须为数字！")
            return
            
        if self._check_port_in_use(port):
            QMessageBox.warning(self, "端口冲突", f"端口 {port} 已被占用，请修改！")
            return
            
        self._save_canva_config()
        self.start_canva_btn.setEnabled(False)
        self.start_canva_btn.setText(f"● 运行中 (端口: {port})")
        self.start_canva_btn.setStyleSheet("background-color: #2E7D32; color: white; border-radius: 6px;")
        self.stop_canva_btn.setEnabled(True)
        self.port_backend.setEnabled(False)

        self.canva_log_sig.emit(f"[*] 准备启动 CanvaTools 服务...")

        self._canva_server_thread = threading.Thread(target=self._run_canva_flask, args=(port,), daemon=True)
        self._canva_server_thread.start()

    def _run_canva_flask(self, port):
        import builtins
        # Redirect prints to our log_sig
        class RedirectStd:
            def write(self_, text):
                if text.strip(): self.canva_log_sig.emit(text.strip())
            def flush(self_): pass
        
        old_stdout = sys.stdout
        sys.stdout = RedirectStd()
        
        try:
            os.environ['STANDALONE_APP_ID'] = self.canva_app_id_input.text().strip()
            os.environ['STANDALONE_PORT'] = str(port)
            os.environ['STANDALONE_SAVE_PATH'] = self.save_path_input.text().strip()
            
            self.werkzeug_server = make_server('0.0.0.0', port, flask_app, threaded=True)
            self.canva_log_sig.emit(f"[*] 服务已成功启动于 http://localhost:{port}")
            self.canva_log_sig.emit(f"[*] Canva 插件的 Development URL 请换成此地址。")
            self.werkzeug_server.serve_forever()
        except Exception as e:
            self.canva_log_sig.emit(f"[!] 运行异常: {e}")
        finally:
            sys.stdout = old_stdout

    def _stop_canva_server(self):
        if self.werkzeug_server:
            def shutdown_task(srv):
                try: srv.shutdown()
                except: pass
            threading.Thread(target=shutdown_task, args=(self.werkzeug_server,), daemon=True).start()
            self.werkzeug_server = None

        self.start_canva_btn.setEnabled(True)
        self.start_canva_btn.setText("▶ 启动服务")
        self.start_canva_btn.setStyleSheet("background-color: #4CAF50; color: white; border-radius: 6px;")
        self.stop_canva_btn.setEnabled(False)
        self.port_backend.setEnabled(True)
        self.canva_log_sig.emit("[*] 服务已停止。")

    def closeEvent(self, event):
        if getattr(self, 'werkzeug_server', None):
            self._stop_canva_server()
        super().closeEvent(event)

    # ── Tab2：衍生关联 ──────────────────────────────────
    def _tab_derive(self):
        """衍生关联面板（独立板块）"""
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10); v.setSpacing(10)
        lb0 = QLabel(
            "将原始素材拖入左侧，处理后的文件拖到右侧。"
            "关系类型根据文件格式自动识别，无需手动选择。")
        lb0.setStyleSheet("color:#6e6e73;font-size:12px;")
        lb0.setWordWrap(True); v.addWidget(lb0)
        dr0 = QHBoxLayout(); dr0.setSpacing(10)
        self._drop_src = DropArea("来源 / 原始素材")
        self._drop_dst = DropArea("衍生 / 处理后素材")
        dr0.addWidget(self._drop_src); dr0.addWidget(self._drop_dst)
        v.addLayout(dr0)
        self._lbl_rel_type = QLabel("关系类型：拖入两侧文件后自动识别")
        self._lbl_rel_type.setStyleSheet(
            "color:#6e6e73;font-size:12px;padding:2px 0;")
        self._drop_src.filesChanged.connect(self._update_rel_type_label)
        self._drop_dst.filesChanged.connect(self._update_rel_type_label)
        v.addWidget(self._lbl_rel_type)
        act0 = QHBoxLayout(); act0.setSpacing(10)
        btn0 = PushButton("🔗  建立衍生关联")
        btn0.setStyleSheet(
            "background:#e67e22;color:#fff;border:none;border-radius:9px;"
            "height:42px;font-size:14px;font-weight:600;")
        btn0.clicked.connect(self._do_derive)
        btn0_clr = PushButton("🗑  清空两侧")
        btn0_clr.setMinimumWidth(160)
        btn0_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:9px;")
        btn0_clr.clicked.connect(self._clear_derive_inputs)
        act0.addWidget(btn0, 1); act0.addWidget(btn0_clr)
        v.addLayout(act0)
        v.addStretch()
        return w

    def _tab_compose(self):
        """成品封装面板（独立板块）"""
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(14, 10, 14, 10); v.setSpacing(10)
        lb1 = QLabel(
            "将所有组件素材拖入左侧，最终成品拖到右侧。"
            "系统自动记录所有来源文件的完整组合关系。")
        lb1.setStyleSheet("color:#6e6e73;font-size:12px;")
        lb1.setWordWrap(True); v.addWidget(lb1)
        dr1 = QHBoxLayout(); dr1.setSpacing(10)
        self._drop_parts   = DropArea("组件素材（可多个）", multi=True)
        self._drop_product = DropArea("最终成品文件")
        dr1.addWidget(self._drop_parts); dr1.addWidget(self._drop_product)
        v.addLayout(dr1)
        act1 = QHBoxLayout(); act1.setSpacing(10)
        btn1 = PushButton("🔒  封装成品")
        btn1.setStyleSheet(
            "background:#27ae60;color:#fff;border:none;border-radius:9px;"
            "height:42px;font-size:14px;font-weight:600;")
        btn1.clicked.connect(self._do_compose)
        btn1_clr = PushButton("🗑  清空两侧")
        btn1_clr.setMinimumWidth(160)
        btn1_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:9px;")
        btn1_clr.clicked.connect(self._clear_compose_inputs)
        act1.addWidget(btn1, 1); act1.addWidget(btn1_clr)
        v.addLayout(act1)
        v.addStretch()
        return w

    def _switch_relate(self, idx: int):
        """切换衍生关联 / 成品封装页面"""
        if hasattr(self, '_relate_stack'):
            self._relate_stack.setCurrentIndex(idx)
        if hasattr(self, '_btn_rel_derive'):
            self._btn_rel_derive.setChecked(idx == 0)
        if hasattr(self, '_btn_rel_compose'):
            self._btn_rel_compose.setChecked(idx == 1)

    def _update_rel_type_label(self, *_):
        """拖入文件后自动刷新关系类型标签"""
        src = self._drop_src.file(); dst = self._drop_dst.file()
        if src and dst:
            rel = self._detect_rel_type(src, dst)
            labels = {
                "image_to_image": "图片 → 图片（修图）",
                "image_to_video": "图片 → 视频（生视频）",
                "video_to_video": "视频 → 视频（视频剪辑）",
            }
            self._lbl_rel_type.setText(
                f"✅ 自动检测：{labels.get(rel, rel)}")
            self._lbl_rel_type.setStyleSheet(
                "color:#007aff;font-size:12px;"
                "font-weight:bold;padding:2px 0;")
        else:
            self._lbl_rel_type.setText("关系类型：拖入两侧文件后自动识别")
            self._lbl_rel_type.setStyleSheet(
                "color:#6e6e73;font-size:12px;padding:2px 0;")

    def _detect_rel_type(self, src_fp: str, dst_fp: str) -> str:
        """根据来源与衍生素材的文件类型自动确定关系类型"""
        src_t = get_asset_type(src_fp)
        dst_t = get_asset_type(dst_fp)
        if dst_t == 'video':
            return 'image_to_video' if src_t == 'image' else 'video_to_video'
        return 'image_to_image'

    def _tab_canva(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("为一组素材生成唯一ID，将ID复制到Canva模板名称中（如：夏日促销【20260313210000】）"))
        self._drop_canva = DropArea("拖入此次Canva使用的所有素材", multi=True); v.addWidget(self._drop_canva)
        r1 = QHBoxLayout(); r1.addWidget(QLabel("模板名称："))
        self._canva_name = LineEdit(); self._canva_name.setPlaceholderText("例：夏日促销Banner")
        r1.addWidget(self._canva_name); v.addLayout(r1)
        r2 = QHBoxLayout(); r2.addWidget(QLabel("备注："))
        self._canva_remark = LineEdit(); r2.addWidget(self._canva_remark); v.addLayout(r2)
        canva_action = QHBoxLayout(); canva_action.setSpacing(10)
        btn = PushButton("🎨  生成模板ID并登记")
        btn.setStyleSheet(
            "background:#9b59b6;color:#fff;height:42px;font-size:14px;"
            "border:none;border-radius:9px;")
        btn.clicked.connect(self._do_canva)
        btn_clr = PushButton("🗑  清空输入")
        btn_clr.setMinimumWidth(160)
        btn_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:9px;")
        btn_clr.clicked.connect(self._clear_canva_inputs)
        canva_action.addWidget(btn, 1); canva_action.addWidget(btn_clr)
        v.addLayout(canva_action)
        # ID 显示行 + 一键复制按钮
        id_row = QHBoxLayout()
        self._canva_id_lbl = QLabel("(点击生成后显示)")
        self._canva_id_lbl.setStyleSheet(
            "font-size:17px;font-weight:bold;color:#2c3e50;"
            "background:#ecf0f1;padding:12px;border-radius:6px;"
        )
        self._canva_id_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_copy_canva = PushButton("\U0001f4cb 复制ID")
        self._btn_copy_canva.setMinimumWidth(108)
        self._btn_copy_canva.setEnabled(False)
        self._btn_copy_canva.clicked.connect(self._copy_canva_id)
        id_row.addWidget(self._canva_id_lbl, 1); id_row.addWidget(self._btn_copy_canva)
        v.addLayout(id_row)

        self._tbl_canva = TableWidget(0, 4)
        self._tbl_canva.setHorizontalHeaderLabels(["模板ID", "模板名称", "创建人", "素材数"])
        self._tbl_canva.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._tbl_canva)
        btn2 = PushButton("🔄 刷新列表"); btn2.clicked.connect(self._refresh_canva); v.addWidget(btn2)
        return w

    def _tab_compose_batch(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel(
            "批量文件夹封装：拖入总目录或子目录。"
            "系统只按目录自身和一级子目录拆分任务，目录之间互相独立。"
        ))

        tip = QLabel(
            "规则：每个目录内文件名包含“成品”的文件会作为成品分别登记，其余文件作为关联组件。"
            "若某目录有多个成品，会在下方‘待确认目录’逐条提示，不再弹窗打断。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#5f748a;font-size:12px;")
        v.addWidget(tip)

        self._drop_compose_batch = FolderDropArea(
            "拖入总目录 / 子目录（自动识别文件名含“成品”的文件）",
            multi=True
        )
        v.addWidget(self._drop_compose_batch)

        act = QHBoxLayout(); act.setSpacing(10)
        btn = PushButton("📁  批量文件夹封装")
        btn.setStyleSheet(
            "background:#2e86de;color:#fff;border:none;border-radius:8px;"
            "height:42px;font-size:14px;font-weight:600;"
        )
        btn.clicked.connect(self._do_compose_batch)

        btn_clr = PushButton("🗑  清空批量目录")
        btn_clr.setMinimumWidth(160)
        btn_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:8px;"
        )
        btn_clr.clicked.connect(self._clear_compose_batch_inputs)
        act.addWidget(btn, 1); act.addWidget(btn_clr)
        v.addLayout(act)

        self._compose_batch_status = QLabel("等待开始…")
        self._compose_batch_status.setStyleSheet(
            "font-size:13px;color:#2c3e50;padding:6px 8px;"
            "background:#eef3f8;border:1px solid #d4e0ec;border-radius:8px;"
        )
        v.addWidget(self._compose_batch_status)

        pending_box = QFrame()
        pending_box.setStyleSheet(
            "QFrame{border:1px solid #d7e2ee;border-radius:10px;background:#f8fbff;}"
        )
        pv = QVBoxLayout(pending_box)
        pv.setContentsMargins(10, 10, 10, 10); pv.setSpacing(8)

        hdr = QHBoxLayout(); hdr.setSpacing(8)
        title = QLabel("待确认目录（多成品）")
        title.setStyleSheet("font-size:13px;font-weight:700;color:#2f4a67;")
        self._compose_pending_status = QLabel("待确认目录：0")
        self._compose_pending_status.setStyleSheet("font-size:12px;color:#6f849a;")
        hdr.addWidget(title); hdr.addStretch(); hdr.addWidget(self._compose_pending_status)
        pv.addLayout(hdr)

        ops = QHBoxLayout(); ops.setSpacing(8)
        btn_all_ok = PushButton("✅ 剩余全部登记")
        btn_all_ok.setStyleSheet(
            "background:#2f9e62;color:#fff;height:34px;font-size:12px;"
            "border:none;border-radius:7px;"
        )
        btn_all_ok.clicked.connect(self._approve_all_compose_pending)

        btn_all_skip = PushButton("⏭ 剩余全部跳过")
        btn_all_skip.setStyleSheet(
            "background:#e9eef5;color:#2f4a67;height:34px;font-size:12px;"
            "border:1px solid #d4e0ec;border-radius:7px;"
        )
        btn_all_skip.clicked.connect(self._skip_all_compose_pending)
        ops.addWidget(btn_all_ok); ops.addWidget(btn_all_skip); ops.addStretch()
        pv.addLayout(ops)

        self._compose_pending_scroll = SmoothScrollArea()
        self._compose_pending_scroll.setWidgetResizable(True)
        self._compose_pending_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._compose_pending_box = QWidget()
        self._compose_pending_list_lay = QVBoxLayout(self._compose_pending_box)
        self._compose_pending_list_lay.setContentsMargins(0, 0, 0, 0)
        self._compose_pending_list_lay.setSpacing(7)
        self._compose_pending_list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._compose_pending_scroll.setWidget(self._compose_pending_box)
        self._compose_pending_scroll.setMinimumHeight(150)
        pv.addWidget(self._compose_pending_scroll)

        v.addWidget(pending_box)

        self._compose_pending_jobs = {}
        self._compose_batch_stats = {
            "folders_total": 0,
            "folders_finished": 0,
            "folders_skipped": 0,
            "ok_products": 0,
            "fail_products": 0,
            "completion_notified": False,
        }
        self._render_compose_pending_jobs()
        v.addStretch()
        return w

    def _tab_canva_batch(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(QLabel(
            "Canva 批量目录登记：拖入总目录或子目录。"
            "系统只按目录自身和一级子目录拆分任务，目录之间互相独立。"
        ))

        tip = QLabel(
            "规则：目录名必须包含【模板ID】且数据库中能查到该模板。"
            "若缺少ID或ID不存在，将提示并跳过不登记。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#5f748a;font-size:12px;")
        v.addWidget(tip)

        self._drop_canva_batch = FolderDropArea(
            "拖入已解压的 Canva 目录（例：夏天海边【20260315005702162】）",
            multi=True
        )
        v.addWidget(self._drop_canva_batch)

        act = QHBoxLayout(); act.setSpacing(10)
        btn = PushButton("🗃  按模板ID批量登记")
        btn.setStyleSheet(
            "background:#8e44ad;color:#fff;border:none;border-radius:8px;"
            "height:42px;font-size:14px;font-weight:600;"
        )
        btn.clicked.connect(self._do_canva_batch)

        btn_clr = PushButton("🗑  清空批量目录")
        btn_clr.setMinimumWidth(160)
        btn_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:8px;"
        )
        btn_clr.clicked.connect(self._clear_canva_batch_inputs)
        act.addWidget(btn, 1); act.addWidget(btn_clr)
        v.addLayout(act)

        self._chk_canva_batch_auto_monitor = QCheckBox("开启 Canva 成品自动关联监控 (共用 CanvaTools 路径)")
        self._chk_canva_batch_auto_monitor.setStyleSheet("color: #2c3e50; font-size: 14px; font-weight: bold; margin-top: 10px;")
        
        # Load state
        is_batch_auto_checked = self._cfg.get('canva_batch_auto_monitor', False)
        self._chk_canva_batch_auto_monitor.setChecked(is_batch_auto_checked)
        self._chk_canva_batch_auto_monitor.toggled.connect(self._toggle_canva_batch_auto_monitor)
        
        v.addWidget(self._chk_canva_batch_auto_monitor)

        v.addStretch()
        return w

    # ── Tab5：源迹查询 ──────────────────────────────────────────────

    def _tab_derive_batch(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.addWidget(QLabel("拖入“图片生成视频记录.json”进行批量自动登记并建立衍生关联。"))
        
        # 拖拽区
        self._drop_derive_batch = JSONDropArea("拖入 JSON 文件或文件夹")
        v.addWidget(self._drop_derive_batch)
        
        # 按钮区
        btn_action = QHBoxLayout(); btn_action.setSpacing(10)
        
        btn_browse = PushButton("📁  浏览选择文件")
        btn_browse.setStyleSheet("background:#95a5a6;color:#fff;height:42px;font-size:14px;border:none;border-radius:9px;")
        def _browse():
            fp, _ = QFileDialog.getOpenFileName(self, "选择JSON记录", "", "JSON Files (*.json)") # For folders, use drag and drop
            if fp:
                from PyQt6.QtCore import QUrl
                class MockMime:
                    def urls(self): return [QUrl.fromLocalFile(fp)]
                class MockEvent:
                    def mimeData(self): return MockMime()
                self._drop_derive_batch.dropEvent(MockEvent())
        btn_browse.clicked.connect(_browse)
        
        btn_start = PushButton("🎬  开始批量导入与关联")
        btn_start.setStyleSheet("background:#8e44ad;color:#fff;height:42px;font-size:14px;border:none;border-radius:9px;")
        btn_start.clicked.connect(self._do_derive_batch)
        
        btn_action.addWidget(btn_browse)
        btn_action.addWidget(btn_start)
        v.addLayout(btn_action)

        self._chk_auto_monitor = QCheckBox("自动检测 Canva 导出目录并解压登记")
        self._chk_auto_monitor.setStyleSheet("color: #2c3e50; font-size: 14px; font-weight: bold; margin-top: 10px;")
        
        # Load state from config
        is_auto_checked = self._cfg.get('canva_auto_monitor', False)
        self._chk_auto_monitor.setChecked(is_auto_checked)
        self._chk_auto_monitor.toggled.connect(self._toggle_auto_monitor)
        
        v.addWidget(self._chk_auto_monitor)
        
        # Start monitoring if it was checked
        if is_auto_checked:
            QTimer.singleShot(500, lambda: self._toggle_auto_monitor(True))

        v.addStretch(1)
        return w


    def _toggle_canva_batch_auto_monitor(self, checked):
        self._cfg['canva_batch_auto_monitor'] = checked
        save_config(self._cfg)
        self._update_auto_monitor_state()

    def _toggle_auto_monitor(self, checked):
        self._cfg['canva_auto_monitor'] = checked
        save_config(self._cfg)
        self._update_auto_monitor_state()

    def _update_auto_monitor_state(self):
        needs_monitor = self._cfg.get('canva_auto_monitor', False) or self._cfg.get('canva_batch_auto_monitor', False)
        
        if needs_monitor:
            if self._canva_auto_worker is None or not self._canva_auto_worker.isRunning():
                import json
                save_dir = os.path.join(os.path.expanduser('~'), 'Desktop')
                canvatools_config_file = os.path.join(os.path.expanduser('~'), '.canva_tools_config.json')
                if os.path.exists(canvatools_config_file):
                    try:
                        with open(canvatools_config_file, 'r', encoding='utf-8') as f:
                            c_cfg = json.load(f)
                            save_dir = c_cfg.get('save_path', save_dir)
                    except: pass
                
                self._canva_auto_worker = CanvaAutoMonitorWorker(save_dir, self._auto_monitor_processed_file)
                self._canva_auto_worker.setParent(self)
                self._canva_auto_worker.log_line.connect(self._log)
                self._canva_auto_worker.found_jsons.connect(self._on_canva_auto_found_jsons)
                self._canva_auto_worker.found_canva_products.connect(self._on_canva_auto_found_products)
                self._canva_auto_worker.start()
        else:
            if self._canva_auto_worker:
                self._canva_auto_worker.stop()
                self._canva_auto_worker = None

    def _on_canva_auto_found_jsons(self, json_paths):
        if self._current_worker is not None:
            self._log("⏳ 当前有正在执行的后台任务，自动监控触发的批量衍生将放弃本次请求，下次检测会再次尝试。")
            return
            
        self._log(f"🎬 自动解压完毕，自动触发批量衍生导入 (共 {len(json_paths)} 个JSON)...")
        
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.show()
        w = BatchDeriveWorker(json_paths, self._cfg.get('user_name', 'System'))
        w.setParent(self)

        def _on_prog(total, done, success, failed):
            if total > 0:
                self._progress.setValue(int(done * 100 / total))
                self._progress.setFormat(f"已处理: {done}/{total} [成功:{success} 失败:{failed}]")

        def _on_fin(res):
            self._progress.hide()
            self._clear_derive_batch_inputs()
            if w in self._workers:
                self._workers.remove(w)
            self._current_worker = None
            msg = f"🎉 自动监控衍生处理完成！\n总数：{res['total']}，成功：{res['success']}，失败：{res['failed']}"
            self._log(msg)

        w.log_line.connect(self._log)
        w.progress.connect(_on_prog)
        w.finished.connect(_on_fin)
        
        self._workers.append(w)
        self._current_worker = w
        w.start()



    def _check_fast_bind_queue(self):
        if self._current_worker is not None: return
        
        try:
            data = self._fast_bind_queue.get_nowait()
        except queue.Empty:
            return

        w = FastBindWorker(data, self._cfg.get('user_name', 'System'))
        w.setParent(self)
        
        def _on_fin(res):
            if w in self._workers:
                self._workers.remove(w)
            self._current_worker = None

        w.log_line.connect(self._log)
        w.finished.connect(_on_fin)
        
        self._workers.append(w)
        self._current_worker = w
        w.start()

    def _do_derive_batch(self):
        if self._current_worker is not None:
            QMessageBox.warning(self, "提示", "当前有后台任务正在进行，请稍候。")
            return
            
        fps = self._drop_derive_batch.files()
        if not fps:
            QMessageBox.critical(self, "错误", "请先拖入 JSON 文件或包含 JSON 的文件夹！")
            return

        btn = self.sender()
        btn.setEnabled(False)
        btn.setText("⏳ 正在处理...")
        self._log(f"🎬 开始通过 JSON 批量衍生导入 (共 {len(fps)} 个文件)...")
        
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.show()

        w = BatchDeriveWorker(fps, self._cfg.get('user_name', 'System'))
        w.setParent(self)

        def _on_prog(total, done, success, failed):
            if total > 0:
                self._progress.setValue(int(done * 100 / total))
                self._progress.setFormat(f"已处理: {done}/{total} [成功:{success} 失败:{failed}]")

        def _on_fin(res):
            self._progress.hide()
            btn.setEnabled(True)
            btn.setText("🎬  开始批量导入与关联")
            self._clear_derive_batch_inputs()
            if w in self._workers:
                self._workers.remove(w)
            self._current_worker = None
            msg = f"🎉 批量衍生处理完成！\n总数：{res['total']}，成功：{res['success']}，失败：{res['failed']}"
            self._log(msg)
            QMessageBox.information(self, "完成", msg)

        w.log_line.connect(self._log)
        w.progress.connect(_on_prog)
        w.finished.connect(_on_fin)
        
        self._workers.append(w)
        self._current_worker = w
        w.start()

    def _tab_query(self):
        w = QWidget(); v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8); v.setSpacing(10)

        sp = QSplitter(Qt.Orientation.Horizontal)
        sp.setChildrenCollapsible(False)

        # ── 左侧控制区
        left_card = QFrame()
        left_card.setStyleSheet(
            "QFrame{background:#ffffff;border:1px solid #d8e2ec;border-radius:10px;}")
        lv = QVBoxLayout(left_card)
        lv.setContentsMargins(12, 12, 12, 12); lv.setSpacing(10)

        left_title = QLabel("查询输入")
        left_title.setStyleSheet("font-size:15px;font-weight:700;color:#24384f;")
        lv.addWidget(left_title)

        perf_row = QHBoxLayout()
        perf_row.addWidget(QLabel("查询线程："))
        self._query_threads = QSpinBox()
        self._query_threads.setRange(1, 32)
        self._query_threads.setValue(int(self._cfg.get('query_threads', self._recommended_workers())))
        self._query_threads.setToolTip("并发处理查询文件的线程数")
        self._query_threads.setMaximumWidth(90)
        perf_row.addWidget(self._query_threads)
        perf_row.addStretch(1)
        lv.addLayout(perf_row)

        self._query_fuzzy = QCheckBox("启用慢速模糊匹配（速度慢，兼容轻微改图）")
        self._query_fuzzy.setChecked(bool(self._cfg.get('query_fuzzy', False)))
        self._query_fuzzy.setStyleSheet("font-size:12px;color:#516980;")
        lv.addWidget(self._query_fuzzy)

        self._drop_query = DropArea("拖入文件（支持多个）", multi=True)
        self._drop_query.setMinimumHeight(180)
        lv.addWidget(self._drop_query)

        query_action = QHBoxLayout(); query_action.setSpacing(10)
        btn = PushButton("🔍  批量查询源迹")
        btn.setStyleSheet(
            "background:#6f42c1;color:#fff;height:42px;font-size:14px;"
            "border:none;border-radius:9px;")
        btn.clicked.connect(self._do_query)
        btn_clr = PushButton("🗑  清空输入")
        btn_clr.setMinimumWidth(160)
        btn_clr.setStyleSheet(
            "background:#eef3f8;color:#2f4a67;height:42px;font-size:14px;"
            "border:1px solid #d4e0ec;border-radius:9px;")
        btn_clr.clicked.connect(self._clear_query_inputs)
        query_action.addWidget(btn, 1); query_action.addWidget(btn_clr)
        lv.addLayout(query_action)



        btn_copy_all = PushButton("📋  全部复制（Google Sheets）")
        btn_copy_all.setStyleSheet(
            "background:#1fa866;color:#fff;height:38px;font-size:13px;"
            "border:none;border-radius:9px;")
        btn_copy_all.clicked.connect(self._copy_all_lineage)
        lv.addWidget(btn_copy_all)

        lv.addStretch(1)
        sp.addWidget(left_card)

        # ── 右侧结果区（滚动卡片）
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(8)

        self._query_result_title = QLabel("查询结果")
        self._query_result_title.setStyleSheet("font-size:15px;font-weight:700;color:#24384f;")
        rv.addWidget(self._query_result_title)

        self._query_scroll = SmoothScrollArea()
        self._query_scroll.setWidgetResizable(True)
        self._query_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._query_result_box = QWidget()
        self._query_result_lay = QVBoxLayout(self._query_result_box)
        self._query_result_lay.setContentsMargins(2, 2, 2, 2)
        self._query_result_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._query_result_lay.setSpacing(8)

        self._query_placeholder = QLabel(
            "← 拖入文件后点击查询，结果会以合并卡片显示在这里"
        )
        self._query_placeholder.setStyleSheet(
            "color:#8aa0b5;font-size:14px;padding:34px;border:1px dashed #cfdae6;border-radius:10px;")
        self._query_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._query_result_lay.addWidget(self._query_placeholder)

        self._query_scroll.setWidget(self._query_result_box)
        rv.addWidget(self._query_scroll)

        sp.addWidget(right)
        sp.setSizes([320, 860])
        v.addWidget(sp)

        self._lineage_results = []
        return w
    # ── Tab6：全量库 ────────────────────────────────────
    def _tab_library(self):
        w = QWidget(); v = QVBoxLayout(w)
        sr = QHBoxLayout()
        self._search_box = LineEdit(); self._search_box.setPlaceholderText("搜索文件名 / 作者…")
        self._search_box.textChanged.connect(self._filter_lib)
        btn_fix = PushButton("🛠 修正错误")
        btn_fix.setStyleSheet(
            "background:#c0392b;color:#fff;height:34px;font-size:12px;"
            "border:none;border-radius:7px;padding:0 12px;"
        )
        btn_fix.setToolTip("按条件批量修正作者名（需密码验证）")
        btn_fix.clicked.connect(self._fix_wrong_producer_with_password)
        btn = PushButton("🔄 刷新"); btn.clicked.connect(self._refresh_lib)
        sr.addWidget(self._search_box); sr.addWidget(btn_fix); sr.addWidget(btn); v.addLayout(sr)
        self._tbl_lib = TableWidget(0, 6)
        self._tbl_lib.setHorizontalHeaderLabels(["文件名","类型","作者","时间","大小","pHash前16位"])
        self._tbl_lib.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tbl_lib.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v.addWidget(self._tbl_lib)
        return w

    # ═══════════════════ 后台任务 ═════════════════════
    def _bg(self, fn, done_cb=None, msg="操作", err_cb=None):
        w = Worker(fn)
        self._current_worker = w
        def on_done(r):
            self._progress.setVisible(False)  # 任务完成隐藏进度条
            self._current_worker = None
            if done_cb:
                done_cb(r)
            self._log(f"✅ {msg}完成")
        
        def on_error(e):
            self._progress.setVisible(False)
            self._current_worker = None
            if err_cb:
                err_cb(e)
            self._log(f"❌ {msg}失败: {e}")
        
        w.done.connect(on_done)
        w.error.connect(on_error)
        w.progress.connect(lambda v: (self._progress.setVisible(True), 
                                       self._progress.setValue(v)))
        w.start()
        self._workers.append(w)
    
    def report_progress(self, percent):
        """后台任务可调用此方法报告进度（0-100）"""
        if self._current_worker:
            self._current_worker.progress.emit(percent)


    def _open_log_dir(self):
        log_dir = os.path.join(os.path.expanduser('~'), '.mam_logs')
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        import subprocess
        if os.name == 'nt':
            subprocess.Popen(['explorer', log_dir])

    def _write_daily_log(self, msg):
        import json
        import datetime
        from threading import Thread
        
        def _write():
            log_dir = os.path.join(os.path.expanduser('~'), '.mam_logs')
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            # Clean up logs older than 15 days
            try:
                now = datetime.datetime.now()
                for fname in os.listdir(log_dir):
                    if fname.startswith("mam_log_") and fname.endswith(".json"):
                        fpath = os.path.join(log_dir, fname)
                        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                        if (now - mtime).days > 15:
                            os.remove(fpath)
            except: pass

            today = datetime.datetime.now().strftime("%Y-%m-%d")
            time_str = datetime.datetime.now().strftime("%H:%M:%S")
            log_file = os.path.join(log_dir, f"mam_log_{today}.json")
            
            entry = {"time": time_str, "msg": msg}
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(line)
            except: pass
            
        Thread(target=_write, daemon=True).start()

    def _log(self, msg):
        self._write_daily_log(msg)
        self._log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")
        _append_diag_log(msg)

    # ═══════════════════ 业务处理 ═════════════════════
    def _do_register(self):
        fps = self._drop_raw.files()
        if not fps: QMessageBox.warning(self, "提示", "请先拖入素材文件"); return
        op = self._cfg['user_name']
        def task():
            total = len(fps)
            upload_batch = 120
            upload_buffer = []

            def flush_assets():
                if not upload_buffer:
                    return
                db.upsert_assets_bulk(list(upload_buffer))
                upload_buffer.clear()

            for idx, fp in enumerate(fps):
                img = get_thumbnail(fp)
                if img is None: gui_log(f"⚠️ 无法读取: {os.path.basename(fp)}"); continue
                ph, src = get_phash_from_file(fp, img)
                if not ph: gui_log(f"❌ phash计算失败: {os.path.basename(fp)}"); continue
                fname = os.path.basename(fp); atype = get_asset_type(fp)
                fsize = get_file_size(fp); now = datetime.now()
                # 如已有元数据，可读取文件中已写入的 created_at，避免覆盖
                existing_meta = read_metadata(fp) or {}
                producer = (existing_meta.get('producer') or '').strip()
                if not producer:
                    producer = op
                created_at = now
                if existing_meta.get('created_at'):
                    try:
                        from datetime import datetime as _dt
                        created_at = _dt.fromisoformat(existing_meta['created_at'])
                    except Exception:
                        pass
                rec = {"phash": ph, "filename": fname, "asset_type": atype,
                       "file_size": fsize, "producer": producer,
                       "created_at": created_at.isoformat()}
                write_metadata(fp, rec)
                upload_buffer.append((
                    ph, fname, atype, fsize, producer, created_at,
                    json.dumps(rec, ensure_ascii=False, default=str),
                    make_thumb_bytes(img)
                ))
                if len(upload_buffer) >= upload_batch:
                    flush_assets()
                src_tag = "（保留原元数据）" if existing_meta.get('producer') else ""
                gui_log(f"✅ 已登记: {fname}  作者:{producer}{src_tag}  phash:{ph}")
                # 报告进度
                progress_percent = int((idx + 1) / total * 100)
                self.report_progress(progress_percent)
            flush_assets()
            return {}
        self._bg(task, msg="素材登记")

    def _do_derive(self):
        src_fp = self._drop_src.file(); dst_fp = self._drop_dst.file()
        if not src_fp or not dst_fp:
            QMessageBox.warning(self, "提示", "请同时拖入来源素材和衍生素材"); return
        op = self._cfg['user_name']
        rel_type = self._detect_rel_type(src_fp, dst_fp)
        def task():
            ph_src, rec_src = ensure_registered(src_fp, op)
            ph_dst, rec_dst = ensure_registered(dst_fp, op)
            if not ph_src or not ph_dst:
                gui_log("❌ 素材登记失败，无法建立关联"); return {}
            db.add_derive(ph_src, ph_dst, rel_type, op,
                          remark=f"{os.path.basename(src_fp)} → {os.path.basename(dst_fp)}")
            src_prod = rec_src.get('producer', op) if isinstance(rec_src, dict) else op
            src_chain = db.get_ancestry_string(ph_src)
            dst_rec = read_metadata(dst_fp) or {}
            dst_rec.update({
                "phash": ph_dst, "filename": os.path.basename(dst_fp),
                "asset_type": get_asset_type(dst_fp), "file_size": get_file_size(dst_fp),
                "producer": op, "created_at": datetime.now().isoformat(),
                "derived_from": {"phash": ph_src, "filename": os.path.basename(src_fp),
                                 "producer": src_prod, "rel_type": rel_type,
                                 "ancestry_chain": src_chain}
            })
            write_metadata(dst_fp, dst_rec)
            db.upsert_asset(ph_dst, os.path.basename(dst_fp), get_asset_type(dst_fp),
                            get_file_size(dst_fp), op, datetime.now(),
                            json.dumps(dst_rec, ensure_ascii=False, default=str))
            gui_log(f"✅ 关联: [{src_prod}]{os.path.basename(src_fp)}"
                    f" →({rel_type})→ [{op}]{os.path.basename(dst_fp)}")
            return {}
        self._bg(task, msg="关联")

    def _list_media_files_top(self, folder: str) -> list:
        """只取目录自身一级媒体文件，不递归。"""
        files = []
        try:
            names = sorted(os.listdir(folder))
        except Exception:
            return files
        for nm in names:
            fp = os.path.join(folder, nm)
            if os.path.isfile(fp) and nm.lower().endswith(ALL_EXTS):
                files.append(fp)
        return files

    def _collect_independent_folders(self, dropped_folders: list) -> list:
        """按目录独立处理：目录自身 + 一级子目录；不做深层递归。"""
        out = []
        seen = set()

        def add_folder(fd):
            ap = os.path.abspath(fd)
            if os.path.isdir(ap) and ap not in seen:
                seen.add(ap)
                out.append(ap)

        for fd in dropped_folders:
            root = os.path.abspath(fd)
            if not os.path.isdir(root):
                continue
            try:
                children = sorted(os.listdir(root))
            except Exception:
                children = []
            child_dirs = [
                os.path.join(root, nm)
                for nm in children
                if os.path.isdir(os.path.join(root, nm))
            ]

            if child_dirs:
                for c in child_dirs:
                    add_folder(c)
                # 若父级本身也有媒体文件，则父级也作为独立目录处理
                if self._list_media_files_top(root):
                    add_folder(root)
            else:
                add_folder(root)
        return out

    def _render_compose_pending_jobs(self):
        if not hasattr(self, '_compose_pending_list_lay'):
            return
        lay = self._compose_pending_list_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()

        jobs = list(getattr(self, '_compose_pending_jobs', {}).values())
        if hasattr(self, '_compose_pending_status'):
            self._compose_pending_status.setText(f"待确认目录：{len(jobs)}")

        if not jobs:
            ph = QLabel("暂无待确认目录。检测到多成品目录后会出现在这里。")
            ph.setStyleSheet("color:#8fa2b5;font-size:12px;padding:8px;")
            lay.addWidget(ph)
            return

        for job in jobs:
            folder = job['folder']
            products = job.get('products', [])
            p_names = [os.path.basename(p) for p in products]
            preview = '，'.join(p_names[:3])
            if len(p_names) > 3:
                preview += f" ...共{len(p_names)}个"

            row = QFrame()
            row.setStyleSheet(
                "QFrame{background:#ffffff;border:1px solid #d8e3ef;border-radius:8px;}"
            )
            rv = QVBoxLayout(row)
            rv.setContentsMargins(9, 8, 9, 8); rv.setSpacing(6)

            title = QLabel(
                f"📁 {os.path.basename(folder)}  检测到 {len(products)} 个“成品”文件"
            )
            title.setStyleSheet("font-size:12px;font-weight:700;color:#2f4a67;")
            rv.addWidget(title)

            detail = QLabel(f"成品文件：{preview}")
            detail.setWordWrap(True)
            detail.setStyleSheet("font-size:12px;color:#61788f;")
            rv.addWidget(detail)

            ops = QHBoxLayout(); ops.setSpacing(8)
            btn_ok = PushButton("本目录登记")
            btn_ok.setStyleSheet(
                "background:#2f9e62;color:#fff;height:30px;font-size:12px;"
                "border:none;border-radius:7px;"
            )
            btn_ok.clicked.connect(
                lambda _, f=folder: self._approve_compose_pending_folder(f)
            )

            btn_skip = PushButton("跳过本目录")
            btn_skip.setStyleSheet(
                "background:#edf2f7;color:#2f4a67;height:30px;font-size:12px;"
                "border:1px solid #d7e1ec;border-radius:7px;"
            )
            btn_skip.clicked.connect(
                lambda _, f=folder: self._skip_compose_pending_folder(f)
            )
            ops.addWidget(btn_ok); ops.addWidget(btn_skip); ops.addStretch()
            rv.addLayout(ops)
            lay.addWidget(row)

    def _update_compose_batch_status(self):
        if not hasattr(self, '_compose_batch_status'):
            return
        st = getattr(self, '_compose_batch_stats', None)
        if not st:
            self._compose_batch_status.setText("等待开始…")
            return
        pending = len(getattr(self, '_compose_pending_jobs', {}))
        txt = (
            f"总目录 {st['folders_total']}  |  已处理 {st['folders_finished']}  |  "
            f"待确认 {pending}  |  已跳过 {st['folders_skipped']}  |  "
            f"成品成功 {st['ok_products']}  |  失败 {st['fail_products']}"
        )
        self._compose_batch_status.setText(txt)

        if (not st.get('completion_notified') and st['folders_total'] > 0
                and pending == 0
                and st['folders_finished'] + st['folders_skipped'] >= st['folders_total']):
            st['completion_notified'] = True
            self._log(
                f"✅ 批量封装流程完成：目录 {st['folders_total']}，"
                f"处理 {st['folders_finished']}，跳过 {st['folders_skipped']}，"
                f"成品成功 {st['ok_products']}，失败 {st['fail_products']}"
            )

    def _run_compose_jobs(self, jobs: list, op: str):
        cache = {}
        upsert_buffer = []
        upload_batch = 120

        def ensure_once(fp):
            if fp not in cache:
                cache[fp] = ensure_registered(fp, op)
            return cache[fp]

        def flush_assets():
            if not upsert_buffer:
                return
            db.upsert_assets_bulk(list(upsert_buffer))
            upsert_buffer.clear()

        done_folders = 0
        ok_products = 0
        fail_products = 0

        for idx, job in enumerate(jobs, 1):
            fd = job['folder']
            folder_name = os.path.basename(fd.rstrip('/\\'))
            gui_log(f"📁 [{idx}/{len(jobs)}] 批量封装目录: {folder_name}")

            part_infos = []
            part_phashes = []
            for fp in job['parts']:
                ph, rec = ensure_once(fp)
                if not ph:
                    continue
                prod = rec.get('producer', op) if isinstance(rec, dict) else op
                part_infos.append({
                    "phash": ph,
                    "filename": os.path.basename(fp),
                    "producer": prod,
                    "asset_type": get_asset_type(fp),
                })
                part_phashes.append(ph)

            if not part_infos:
                gui_log("  ⚠️ 本目录无可用组件，仅登记成品")

            for pfp in job['products']:
                ph_product, _ = ensure_once(pfp)
                if not ph_product:
                    fail_products += 1
                    gui_log(f"  ❌ 成品处理失败: {os.path.basename(pfp)}")
                    continue

                if part_phashes:
                    db.add_compose(part_phashes, ph_product)

                payload = []
                for info in part_infos:
                    item = dict(info)
                    item['ancestry_chain'] = db.get_ancestry_string(item['phash'])
                    payload.append(item)

                rec = read_metadata(pfp) or {}
                rec.update({
                    "phash": ph_product,
                    "filename": os.path.basename(pfp),
                    "asset_type": get_asset_type(pfp),
                    "file_size": get_file_size(pfp),
                    "producer": op,
                    "created_at": datetime.now().isoformat(),
                    "composed_from": payload,
                    "batch_folder": folder_name,
                })
                write_metadata(pfp, rec)
                upsert_buffer.append((
                    ph_product,
                    os.path.basename(pfp),
                    get_asset_type(pfp),
                    get_file_size(pfp),
                    op,
                    datetime.now(),
                    json.dumps(rec, ensure_ascii=False, default=str),
                    None,
                ))
                if len(upsert_buffer) >= upload_batch:
                    flush_assets()
                ok_products += 1
                gui_log(
                    f"  ✅ 成品登记: [{op}]{os.path.basename(pfp)}  关联组件 {len(payload)} 个"
                )

            done_folders += 1

        flush_assets()

        return {
            "folders": done_folders,
            "ok_products": ok_products,
            "fail_products": fail_products,
        }

    def _start_compose_jobs_async(self, jobs: list, reason: str):
        if not jobs:
            return
        op = self._cfg['user_name']

        def task():
            return self._run_compose_jobs(jobs, op)

        def done(r):
            st = getattr(self, '_compose_batch_stats', None)
            if st:
                st['folders_finished'] += r['folders']
                st['ok_products'] += r['ok_products']
                st['fail_products'] += r['fail_products']
            self._refresh_lib()
            self._log(
                f"✅ {reason}：目录 {r['folders']}，"
                f"成品成功 {r['ok_products']}，失败 {r['fail_products']}"
            )
            self._update_compose_batch_status()

        self._bg(task, done, msg=f"批量封装-{reason}")

    def _approve_compose_pending_folder(self, folder: str):
        jobs = getattr(self, '_compose_pending_jobs', {})
        job = jobs.pop(folder, None)
        if not job:
            return
        self._render_compose_pending_jobs()
        self._log(f"✅ 已确认目录：{os.path.basename(folder)}")
        self._start_compose_jobs_async([job], "人工确认目录登记")
        self._update_compose_batch_status()

    def _skip_compose_pending_folder(self, folder: str):
        jobs = getattr(self, '_compose_pending_jobs', {})
        job = jobs.pop(folder, None)
        if not job:
            return
        st = getattr(self, '_compose_batch_stats', None)
        if st:
            st['folders_skipped'] += 1
        self._log(f"⏭ 跳过目录：{os.path.basename(folder)}")
        self._render_compose_pending_jobs()
        self._update_compose_batch_status()

    def _approve_all_compose_pending(self):
        jobs_dict = getattr(self, '_compose_pending_jobs', {})
        jobs = list(jobs_dict.values())
        if not jobs:
            return
        jobs_dict.clear()
        self._render_compose_pending_jobs()
        self._log(f"✅ 已确认剩余全部目录，共 {len(jobs)} 个")
        self._start_compose_jobs_async(jobs, "人工确认-剩余全部登记")
        self._update_compose_batch_status()

    def _skip_all_compose_pending(self):
        jobs_dict = getattr(self, '_compose_pending_jobs', {})
        n = len(jobs_dict)
        if n <= 0:
            return
        jobs_dict.clear()
        st = getattr(self, '_compose_batch_stats', None)
        if st:
            st['folders_skipped'] += n
        self._log(f"⏭ 已跳过剩余全部目录，共 {n} 个")
        self._render_compose_pending_jobs()
        self._update_compose_batch_status()

    def _do_compose_batch(self):
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接")
            return
        dropped = self._drop_compose_batch.folders() if hasattr(self, '_drop_compose_batch') else []
        if not dropped:
            QMessageBox.warning(self, "提示", "请先拖入总目录或子目录")
            return

        unit_folders = self._collect_independent_folders(dropped)
        if not unit_folders:
            QMessageBox.warning(self, "提示", "未找到可处理目录")
            return

        jobs = []
        no_media = []
        no_product = []
        for fd in unit_folders:
            media = self._list_media_files_top(fd)
            if not media:
                no_media.append(fd)
                continue
            products = [fp for fp in media if "成品" in os.path.basename(fp)]
            parts = [fp for fp in media if fp not in products]
            if not products:
                no_product.append(fd)
                continue
            jobs.append({"folder": fd, "products": products, "parts": parts})

        for fd in no_media:
            self._log(f"⚠️ 跳过空目录: {os.path.basename(fd)}")
        for fd in no_product:
            self._log(f"⚠️ 跳过（未找到成品文件）: {os.path.basename(fd)}")

        if not jobs:
            QMessageBox.information(self, "提示", "没有符合规则的目录：需要至少 1 个文件名包含“成品”的媒体文件")
            return

        direct_jobs = [j for j in jobs if len(j.get('products', [])) == 1]
        pending_jobs = [j for j in jobs if len(j.get('products', [])) > 1]

        self._compose_pending_jobs = {j['folder']: j for j in pending_jobs}
        self._compose_batch_stats = {
            "folders_total": len(jobs),
            "folders_finished": 0,
            "folders_skipped": 0,
            "ok_products": 0,
            "fail_products": 0,
            "completion_notified": False,
        }
        self._render_compose_pending_jobs()
        self._update_compose_batch_status()

        if pending_jobs:
            self._log(
                f"ℹ️ 发现多成品目录 {len(pending_jobs)} 个，已在下方“待确认目录”逐条列出。"
            )
        if direct_jobs:
            self._log(f"🚀 先自动处理单成品目录 {len(direct_jobs)} 个（后台执行）")
            self._start_compose_jobs_async(direct_jobs, "单成品目录自动处理")
        else:
            self._log("ℹ️ 本次没有可自动处理目录，请在待确认列表中逐条决定。")

    def _do_compose(self):
        part_fps   = self._drop_parts.files()
        product_fp = self._drop_product.file()
        if not product_fp: QMessageBox.warning(self, "提示", "请拖入最终成品文件"); return
        op = self._cfg['user_name']
        def task():
            ph_product, _ = ensure_registered(product_fp, op)
            if not ph_product: gui_log("❌ 成品文件无法处理"); return {}
            part_phashes = []; part_info = []
            for fp in part_fps:
                ph, rec = ensure_registered(fp, op)
                if ph:
                    part_phashes.append(ph)
                    prod = rec.get('producer', op) if isinstance(rec, dict) else op
                    part_info.append({"phash": ph, "filename": os.path.basename(fp),
                                      "producer": prod, "asset_type": get_asset_type(fp)})
                    gui_log(f"  ✅ 组件: [{prod}] {os.path.basename(fp)}")
            if part_phashes:
                db.add_compose(part_phashes, ph_product)
            for info in part_info:
                info['ancestry_chain'] = db.get_ancestry_string(info['phash'])
            product_rec = read_metadata(product_fp) or {}
            product_rec.update({
                "phash": ph_product, "filename": os.path.basename(product_fp),
                "asset_type": get_asset_type(product_fp), "file_size": get_file_size(product_fp),
                "producer": op, "created_at": datetime.now().isoformat(),
                "composed_from": part_info
            })
            write_metadata(product_fp, product_rec)
            db.upsert_asset(ph_product, os.path.basename(product_fp), get_asset_type(product_fp),
                            get_file_size(product_fp), op, datetime.now(),
                            json.dumps(product_rec, ensure_ascii=False, default=str))
            gui_log(f"✅ 成品封装: [{op}]{os.path.basename(product_fp)}  组件{len(part_phashes)}个")
            return {}
        self._bg(task, msg="封装")

    def _do_canva(self):
        fps = self._drop_canva.files()
        if not fps: QMessageBox.warning(self, "提示", "请拖入素材"); return
        tname  = self._canva_name.text().strip() or "未命名模板"
        remark = self._canva_remark.text().strip()
        op     = self._cfg['user_name']
        tid    = datetime.now().strftime("%Y%m%d%H%M%S%f")[:17]
        def task():
            phashes = []
            for fp in fps:
                ph, rec = ensure_registered(fp, op, fill_missing_producer=True)
                if ph:
                    phashes.append(ph)
                    prod = rec.get('producer','?') if isinstance(rec, dict) else '?'
                    gui_log(f"  ✅ 素材: [{prod}] {os.path.basename(fp)}")
            if not phashes: gui_log("❌ 没有有效素材"); return {"id": None}
            db.add_canva_template(tid, tname, op, phashes, remark)
            gui_log(f"✅ 模板ID: 【{tid}】  素材{len(phashes)}个")
            return {"id": tid}
        def done(r):
            if r.get("id"):
                self._last_canva_id = r['id']
                self._canva_id_lbl.setText(f"【{r['id']}】")
                self._btn_copy_canva.setEnabled(True)
                self._refresh_canva()
        self._bg(task, done, msg="Canva登记")

    def _extract_canva_id_from_folder(self, folder: str):
        name = os.path.basename(folder.rstrip('/\\'))
        m = re.search(r'【【(\d+)】】', name)
        return m.group(1) if m else None

    def _do_canva_batch(self):
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接")
            return
        dropped = self._drop_canva_batch.folders() if hasattr(self, '_drop_canva_batch') else []
        if not dropped:
            QMessageBox.warning(self, "提示", "请先拖入总目录或子目录")
            return

        unit_folders = self._collect_independent_folders(dropped)
        if not unit_folders:
            QMessageBox.warning(self, "提示", "未找到可处理目录")
            return

        jobs = []
        no_media = []
        no_id = []
        no_template = []

        for fd in unit_folders:
            media = self._list_media_files_top(fd)
            if not media:
                no_media.append(fd)
                continue

            tid = self._extract_canva_id_from_folder(fd)
            if not tid:
                no_id.append(fd)
                continue

            tracker_data = None
            try:
                for f in os.listdir(fd):
                    if f.startswith("tracker_CANVA_") and f.endswith(".json"):
                        with open(os.path.join(fd, f), "r", encoding="utf-8") as jf:
                            tracker_data = json.load(jf)
                        break
            except:
                pass

            template_pack = db.get_canva_template_assets_basic(tid)
            if not template_pack and not tracker_data:
                no_template.append((fd, tid))
                continue

            tmpl = template_pack.get('template', {}) if template_pack else {}
            assets = template_pack.get('assets', []) if template_pack else []
            src_infos = []
            src_phashes = []
            for a in assets:
                ph = a.get('phash')
                if not ph:
                    continue
                src_phashes.append(ph)
                src_infos.append({
                    "phash": ph,
                    "filename": a.get('filename', '?'),
                    "producer": a.get('producer', '?'),
                    "asset_type": a.get('asset_type', '?'),
                })
                
            if tracker_data:
                if not tmpl.get('creator'):
                    tmpl['creator'] = tracker_data.get('creator', '')
                if not tmpl.get('template_name'):
                    tmpl['template_name'] = ""
                # 如果数据库中没有关联的哈希，使用 tracker 中的
                for ph in tracker_data.get('hashes', []):
                    if ph not in src_phashes:
                        src_phashes.append(ph)
                        src_infos.append({
                            "phash": ph,
                            "filename": "未知(未入库)",
                            "producer": tmpl.get('creator', '?'),
                            "asset_type": "image",
                        })
                # 如果模板在数据库中不存在，这里补录一下
                if not template_pack:
                    db.add_canva_template(tid, tmpl['template_name'], tmpl['creator'], src_phashes, "Canva批量解压时自动补录")

            # 去重并保持顺序
            uniq_ph = []
            seen_ph = set()
            for ph in src_phashes:
                if ph not in seen_ph:
                    seen_ph.add(ph)
                    uniq_ph.append(ph)

            jobs.append({
                "folder": fd,
                "files": media,
                "template_id": tid,
                "template_name": tmpl.get('template_name', ''),
                "template_creator": tmpl.get('creator', ''),
                "source_infos": src_infos,
                "source_phashes": uniq_ph,
            })

        for fd in no_media:
            self._log(f"⚠️ 跳过空目录: {os.path.basename(fd)}")
        for fd in no_id:
            self._log(f"⚠️ 跳过（目录名无模板ID）: {os.path.basename(fd)}")
        for fd, tid in no_template:
            self._log(f"⚠️ 跳过（模板ID未找到）: {os.path.basename(fd)}  ID={tid}")

        if not jobs:
            QMessageBox.information(self, "提示", "没有可登记目录：请确保目录名含【模板ID】且该模板ID已存在")
            return

        op = self._cfg['user_name']

        def task():
            cache = {}
            upsert_buffer = []
            upload_batch = 120

            def flush_assets():
                if not upsert_buffer:
                    return
                db.upsert_assets_bulk(list(upsert_buffer))
                upsert_buffer.clear()

            def ensure_once(fp):
                if fp not in cache:
                    cache[fp] = ensure_registered(fp, op)
                return cache[fp]

            ok_files = 0
            fail_files = 0
            done_folders = 0

            for idx, job in enumerate(jobs, 1):
                fd = job['folder']
                folder_name = os.path.basename(fd.rstrip('/\\'))
                tid = job['template_id']
                tname = job['template_name']
                tcreator = job['template_creator']
                gui_log(f"📁 [{idx}/{len(jobs)}] Canva批量目录: {folder_name}  模板ID={tid}")

                source_payload = []
                for info in job['source_infos']:
                    item = dict(info)
                    item['ancestry_chain'] = db.get_ancestry_string(item['phash'])
                    source_payload.append(item)

                for fp in job['files']:
                    ph, _ = ensure_once(fp)
                    if not ph:
                        fail_files += 1
                        gui_log(f"  ❌ 文件处理失败: {os.path.basename(fp)}")
                        continue

                    part_phashes = [x for x in job['source_phashes'] if x != ph]

                    rec = read_metadata(fp) or {}
                    rec.update({
                        "phash": ph,
                        "filename": os.path.basename(fp),
                        "asset_type": get_asset_type(fp),
                        "file_size": get_file_size(fp),
                        "producer": op,
                        "created_at": datetime.now().isoformat(),
                        "canva_template": {
                            "template_id": tid,
                            "template_name": tname,
                            "creator": tcreator,
                        },
                        "canva_assets": [dict(x) for x in source_payload],
                        # 复用 composed_from 让溯源树可以直接展示层级关系
                        "composed_from": [dict(x) for x in source_payload],
                    })
                    write_metadata(fp, rec)
                    upsert_buffer.append((
                        ph,
                        os.path.basename(fp),
                        get_asset_type(fp),
                        get_file_size(fp),
                        op,
                        datetime.now(),
                        json.dumps(rec, ensure_ascii=False, default=str),
                        None,
                    ))
                    if len(upsert_buffer) >= upload_batch:
                        flush_assets()
                    ok_files += 1
                    gui_log(
                        f"  ✅ Canva关联: [{op}]{os.path.basename(fp)}"
                        f"  <- 模板【{tid}】素材 {len(source_payload)} 项"
                    )

                done_folders += 1

            flush_assets()

            return {
                "folders": done_folders,
                "ok_files": ok_files,
                "fail_files": fail_files,
            }

        def done(r):
            self._refresh_lib()
            summary = (
                f"Canva批量完成：目录 {r['folders']} 个，"
                f"文件成功 {r['ok_files']} 个，失败 {r['fail_files']} 个"
            )
            self._log(summary)
            QMessageBox.information(self, "Canva批量登记", summary)

        self._bg(task, done, msg="Canva批量登记")

    def _copy_canva_id(self):
        if self._last_canva_id:
            QApplication.clipboard().setText(f"【{self._last_canva_id}】")
            self._log(f"\u2705 已复制到剪切板：【{self._last_canva_id}】")

    # ────────────────────────────────────────────────────────────
    # 源迹查询律 — 卡片构建 & 复制
    # ────────────────────────────────────────────────────────────
    def _get_producer_chain(self, lineage: dict) -> list:
        """汇总完整参与人链（上游祖先 → 组件作者 → 当前作者），去重保序。"""
        chain = []
        seen = set()

        def add_name(name):
            p = (name or '').strip()
            if p and p not in seen:
                seen.add(p)
                chain.append(p)

        def walk_ancestors(rows):
            for r in rows or []:
                walk_ancestors(r.get('ancestors', []))
                add_name(r.get('producer', ''))

        def walk_components(rows):
            for r in rows or []:
                walk_ancestors(r.get('ancestors', []))
                add_name(r.get('producer', ''))
                walk_components(r.get('sub_parts', []))

        walk_ancestors(lineage.get('derived_from', []))
        walk_components(lineage.get('composed_from', []))
        asset = lineage.get('asset') or {}
        add_name(asset.get('producer', ''))
        return chain

    def _build_result_card(self, fp, img, lineage, merged_count=1) -> QFrame:
        """卡片：左=缩略图+文件名+制作人；右=制作人链文字+可展开层级树+小复制按钮"""
        card = QFrame()
        card.setStyleSheet(
            "QFrame{border:1px solid #dbe4ee;border-radius:10px;"
            "background:#fff;margin:1px;}")
        row_lay = QHBoxLayout(card)
        row_lay.setContentsMargins(14, 14, 14, 14); row_lay.setSpacing(16)

        # ── 左栏：缩略图 + 文件名 + 制作人 ──────────────────────
        left = QWidget(); left.setMinimumWidth(152); left.setMaximumWidth(190)
        lv = QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(5)
        lv.setAlignment(Qt.AlignmentFlag.AlignTop)

        lbl_th = QLabel(); lbl_th.setFixedSize(108, 108)
        lbl_th.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_th.setStyleSheet("border:1px solid #cad6e3;background:#0f1724;border-radius:8px;")
        if img is not None:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h, w_img, ch = rgb.shape
            qi  = QImage(rgb.data, w_img, h, ch * w_img, QImage.Format.Format_RGB888)
            pm  = QPixmap.fromImage(qi).scaled(
                102, 102, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            lbl_th.setPixmap(pm)
        else:
            lbl_th.setText("?")
            lbl_th.setStyleSheet(
                "border:1px solid #cad6e3;background:#0f1724;border-radius:8px;"
                "color:#c3d3e1;font-size:24px;")
        lv.addWidget(lbl_th)

        lbl_fn = QLabel(os.path.basename(fp))
        lbl_fn.setWordWrap(True); lbl_fn.setMaximumWidth(180)
        lbl_fn.setStyleSheet("font-weight:700;font-size:12px;color:#1f3348;margin-top:3px;")
        lv.addWidget(lbl_fn)

        if lineage:
            ast_d = lineage['asset']
            lbl_prod = QLabel(f"👤 {ast_d.get('producer','?')}")
            lbl_prod.setWordWrap(True)
            lbl_prod.setStyleSheet("font-size:12px;color:#6a7f96;")
            lv.addWidget(lbl_prod)
            
            cv_meta = ast_d.get('canva_template')
            if isinstance(cv_meta, dict) and cv_meta.get('creator'):
                lbl_cv = QLabel(f"🎨 Canva作者: {cv_meta.get('creator')}")
                lbl_cv.setWordWrap(True)
                lbl_cv.setStyleSheet("font-size:11px;color:#c0392b;font-weight:bold;")
                lv.addWidget(lbl_cv)

            lbl_type = QLabel(ast_d.get('asset_type', ''))
            lbl_type.setStyleSheet("font-size:11px;color:#9ba8b4;")
            lv.addWidget(lbl_type)

        if merged_count > 1:
            lbl_dup = QLabel(f"合并 {merged_count} 项")
            lbl_dup.setStyleSheet(
                "font-size:11px;color:#375a7a;background:#ecf4fc;"
                "border:1px solid #cfe0f0;border-radius:8px;padding:2px 5px;")
            lv.addWidget(lbl_dup)

        lv.addStretch()
        row_lay.addWidget(left)

        # ── 右栏：制作人链 + 层级树 ──────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(8)

        top_row = QHBoxLayout(); top_row.setSpacing(8)
        if lineage:
            chain = self._get_producer_chain(lineage)
            if chain:
                if len(chain) > 1:
                    lbl_chain = QLabel(f"参与人员（{len(chain)}人）：" + " → ".join(chain))
                else:
                    lbl_chain = QLabel("制作人员：" + " → ".join(chain))
            else:
                lbl_chain = QLabel("制作人员：未知")
            lbl_chain.setStyleSheet("font-size:13px;font-weight:600;color:#1a6035;")
        else:
            lbl_chain = QLabel("❓ 该文件未在数据库登记")
            lbl_chain.setStyleSheet("font-size:13px;color:#c24a2f;")
        lbl_chain.setWordWrap(True)
        top_row.addWidget(lbl_chain, 1)

        btn_cp = PushButton("复制")
        btn_cp.setMinimumWidth(64); btn_cp.setMinimumHeight(28)
        btn_cp.setStyleSheet(
            "font-size:12px;background:#f0f4f8;border:1px solid #c8d8e8;"
            "border-radius:6px;color:#3c5a78;padding:0;")
        btn_cp.clicked.connect(lambda _, f=fp, lg=lineage: self._copy_lineage_row(f, lg))
        top_row.addWidget(btn_cp)
        rv.addLayout(top_row)

        if lineage:
            tree = QTreeWidget()
            tree.setColumnCount(2)
            tree.setHeaderHidden(True)
            tree.setColumnWidth(0, 520); tree.setColumnWidth(1, 140)
            tree.setAlternatingRowColors(False)
            tree.setMinimumHeight(80)
            tree.setStyleSheet(
                "QTreeWidget{border:1px solid #e0e8f0;border-radius:8px;background:#fafcff;}"
                "QTreeWidget::item{height:30px;font-size:13px;padding-left:2px;}"
                "QTreeWidget::branch{background:#fafcff;}")
            self._fill_lineage_tree(tree, lineage)
            rv.addWidget(tree)

        row_lay.addWidget(right, 1)
        return card

    def _fill_lineage_tree(self, tree: QTreeWidget, lineage: dict):
        """按关系类型填充可折叠分节（2列：名称 | 制作人）"""
        tree.clear()
        f_bold = QFont(); f_bold.setBold(True)
        has_any = False

        if lineage.get('derived_from'):
            has_any = True
            cnt = len(lineage['derived_from'])
            sec = QTreeWidgetItem([f"⬆  衍生自（父级，{cnt}项）", ""])
            sec.setForeground(0, QColor("#b84a0a")); sec.setFont(0, f_bold)
            for r in lineage['derived_from']:
                sec.addChild(self._make_ancestor_item(r))
            tree.addTopLevelItem(sec)
            self._expand_tree_item_recursive(sec)

        if lineage.get('derived_to'):
            has_any = True
            cnt = len(lineage['derived_to'])
            sec = QTreeWidgetItem([f"⬇  衍生出（子级，{cnt}项）", ""])
            sec.setForeground(0, QColor("#1a7a45")); sec.setFont(0, f_bold)
            for r in lineage['derived_to']:
                sec.addChild(self._make_descendant_item(r))
            tree.addTopLevelItem(sec)
            self._expand_tree_item_recursive(sec)

        if lineage.get('composed_from'):
            has_any = True
            cnt = len(lineage['composed_from'])
            sec = QTreeWidgetItem([f"📦  由以下素材合成（{cnt}项）", ""])
            sec.setForeground(0, QColor("#7a3aad")); sec.setFont(0, f_bold)
            for r in lineage['composed_from']:
                sec.addChild(self._make_component_item(r))
            tree.addTopLevelItem(sec)
            self._expand_tree_item_recursive(sec)

        if lineage.get('used_in'):
            has_any = True
            cnt = len(lineage['used_in'])
            sec = QTreeWidgetItem([f"🎦  被应用于（{cnt}项）", ""])
            sec.setForeground(0, QColor("#174eaf")); sec.setFont(0, f_bold)
            for r in lineage['used_in']:
                child = QTreeWidgetItem([
                    f"    📄 {r.get('filename','?')}",
                    r.get('producer', '?'),
                ])
                child.setForeground(0, QColor("#2563a5"))
                child.setForeground(1, QColor("#8899aa"))
                sec.addChild(child)
            tree.addTopLevelItem(sec); sec.setExpanded(True)

        if lineage.get('canva_used'):
            has_any = True
            cnt = len(lineage['canva_used'])
            sec = QTreeWidgetItem([f"🎨  Canva模板（{cnt}个）", ""])
            sec.setForeground(0, QColor("#c0392b")); sec.setFont(0, f_bold)
            for t in lineage['canva_used']:
                tid = str(t.get('template_id', '') or '').strip()
                tname = t.get('template_name', '?')
                creator = str(t.get('creator', '') or '').strip() or '未知'
                mode = str(t.get('match_mode', 'direct') or 'direct')
                mcnt = int(t.get('matched_count') or 0)
                if mode == 'upstream':
                    suffix = f"  （上游关联，命中{mcnt}项素材）"
                else:
                    suffix = "  （直接关联）"
                title = (f"    🎨 【{tid}】{tname}{suffix}" if tid
                         else f"    🎨 {tname}{suffix}")
                child = QTreeWidgetItem([
                    title,
                    f"👤 {creator}",
                ])
                child.setForeground(0, QColor("#c0392b"))
                child.setForeground(1, QColor("#8899aa"))


                sec.addChild(child)
            tree.addTopLevelItem(sec)
            self._expand_tree_item_recursive(sec)

        if not has_any:
            item = QTreeWidgetItem(["（无衍生 / 组合 / 被用关系）", ""])
            item.setForeground(0, QColor("#aab0bb"))
            tree.addTopLevelItem(item)

    def _lineage_to_tsv(self, fp: str, lineage) -> str:
        """\u8f6c\u6362\u4e3a Google Sheets \u53ef\u76f4\u63a5\u7c98\u8d34\u7684 TSV \u683c\u5f0f\uff08\u542b\u8868\u5934\u65f6\u4e00\u884c\uff09"""
        fname = os.path.basename(fp)
        if not lineage:
            return f"{fname}\t\u672a\u767b\u8bb0\t\t\t\t\t\t"
        ast_d = lineage['asset']
        ph    = ast_d.get('phash', '')
        prod  = ast_d.get('producer', '')
        date  = str(ast_d.get('created_at', ''))[:10]
        derived = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('derived_from', [])
        )
        parts = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('composed_from', [])
        )
        used = '; '.join(
            f"{r.get('filename','?')}({r.get('producer','?')})"
            for r in lineage.get('used_in', [])
        )
        canva = '; '.join(
            f"{t.get('template_id','')}({t.get('template_name','?')},创建人:{(t.get('creator') or '未知')},"
            f"关联:{'直接' if (t.get('match_mode') or 'direct') == 'direct' else '上游'})"
            for t in lineage.get('canva_used', [])
        )
        return f"{fname}\t{ph}\t{prod}\t{date}\t{derived}\t{parts}\t{used}\t{canva}"

    def _expand_tree_item_recursive(self, item: QTreeWidgetItem):
        """递归展开树节点，确保层级默认可见。"""
        if not item:
            return
        item.setExpanded(True)
        for i in range(item.childCount()):
            self._expand_tree_item_recursive(item.child(i))

    def _copy_lineage_row(self, fp: str, lineage):
        QApplication.clipboard().setText(self._lineage_to_tsv(fp, lineage))
        self._log(f"\u2705 \u5df2\u590d\u5236: {os.path.basename(fp)}")

    def _copy_all_lineage(self):
        if not self._lineage_results:
            QMessageBox.information(self, "\u63d0\u793a", "\u6682\u65e0\u67e5\u8be2\u7ed3\u679c"); return
        header = "\u6587\u4ef6\u540d\tphash\t\u5236\u4f5c\u4eba\t\u65e5\u671f\t\u884d\u751f\u6765\u6e90\t\u5c01\u88c5\u7ec4\u4ef6\t\u88ab\u7528\u4e8e"
        rows = [self._lineage_to_tsv(r['fp'], r['lineage']) for r in self._lineage_results]
        QApplication.clipboard().setText(header + '\n' + '\n'.join(rows))
        self._log(f"\u2705 \u5df2\u590d\u5236 {len(rows)} \u6761\u8bb0\u5f55\uff08\u542b\u8868\u5934\uff09\uff0c\u53ef\u76f4\u63a5\u7c98\u8d34\u5230 Google Sheets")

    def _clear_query_results(self):
        protected = {self._query_placeholder}
        hdr = getattr(self, '_query_hdr_row', None)
        if hdr:
            protected.add(hdr)
        i = 0
        while i < self._query_result_lay.count():
            item = self._query_result_lay.itemAt(i)
            w = item.widget() if item else None
            if w in protected:
                i += 1
                continue
            self._query_result_lay.takeAt(i)
            if w:
                w.deleteLater()
        self._lineage_results = []
        self._query_placeholder.show()
        if hdr:
            hdr.hide()
        if hasattr(self, '_query_result_title'):
            self._query_result_title.setText("查询结果")

    def _make_component_item(self, row) -> QTreeWidgetItem:
        """构建封装组件树节点（2列），支持衍生来源与子组件"""
        item = QTreeWidgetItem([
            f"    📄 {row.get('filename','?')}",
            row.get('producer', '?'),
        ])
        item.setForeground(0, QColor("#7a3aad"))
        item.setForeground(1, QColor("#8899aa"))

        # 组件本身如果来自衍生链，需要把祖先一并展示，避免只看到当前节点
        ancestors = row.get('ancestors') or []
        if ancestors:
            anc_sec = QTreeWidgetItem([f"    ⬆ 衍生自（父级，{len(ancestors)}项）", ""])
            anc_sec.setForeground(0, QColor("#b84a0a"))
            for anc in ancestors:
                anc_sec.addChild(self._make_ancestor_item(anc))
            anc_sec.setExpanded(True)
            item.addChild(anc_sec)
        else:
            src_tag = QTreeWidgetItem(["    · 原始素材（无衍生父级）", ""])
            src_tag.setForeground(0, QColor("#95a5b2"))
            item.addChild(src_tag)

        if row.get('sub_parts'):
            sub_rows = row.get('sub_parts') or []
            sub_sec = QTreeWidgetItem([f"    🔧 子组件（{len(sub_rows)}项）", ""])
            sub_sec.setForeground(0, QColor("#6c3483"))
            for sub in sub_rows:
                sub_sec.addChild(self._make_component_item(sub))
            sub_sec.setExpanded(True)
            item.addChild(sub_sec)

        if ancestors or row.get('sub_parts'):
            item.setExpanded(True)
        return item

    def _make_ancestor_item(self, row) -> QTreeWidgetItem:
        """构建衍生来源树节点（递归向上），2列：文件名 | 制作人"""
        item = QTreeWidgetItem([
            f"    📄 {row.get('filename','?')}",
            row.get('producer', '?'),
        ])
        item.setForeground(0, QColor("#c05010"))
        item.setForeground(1, QColor("#8899aa"))
        for anc in row.get('ancestors', []):
            item.addChild(self._make_ancestor_item(anc))
        return item

    def _make_descendant_item(self, row) -> QTreeWidgetItem:
        """构建衍生出树节点（递归向下），2列：文件名 | 制作人"""
        item = QTreeWidgetItem([
            f"    📄 {row.get('filename','?')}",
            row.get('producer', '?'),
        ])
        item.setForeground(0, QColor("#167a50"))
        item.setForeground(1, QColor("#8899aa"))
        for desc in row.get('descendants', []):
            item.addChild(self._make_descendant_item(desc))
        return item

    def _refresh_canva(self):
        rows = db.get_all_canva(); self._tbl_canva.setRowCount(0)
        for r in rows:
            try: ph_list = json.loads(r['asset_phashes']) if r['asset_phashes'] else []
            except: ph_list = []
            idx = self._tbl_canva.rowCount(); self._tbl_canva.insertRow(idx)
            self._tbl_canva.setItem(idx,0, QTableWidgetItem(r['template_id']))
            self._tbl_canva.setItem(idx,1, QTableWidgetItem(r['template_name'] or ""))
            self._tbl_canva.setItem(idx,2, QTableWidgetItem(r['creator'] or ""))
            self._tbl_canva.setItem(idx,3, QTableWidgetItem(str(len(ph_list))))

    def _do_query(self):
        fps = self._drop_query.files()
        if not fps: QMessageBox.warning(self, "提示", "请先拖入要查询的文件"); return

        worker_count = int(self._query_threads.value()) if hasattr(self, '_query_threads') else self._recommended_workers()
        enable_fuzzy = self._query_fuzzy.isChecked() if hasattr(self, '_query_fuzzy') else False
        self._cfg['query_threads'] = worker_count
        self._cfg['query_fuzzy'] = enable_fuzzy
        save_config(self._cfg)

        def task():
            t0 = time.perf_counter()

            # 阶段1：并发计算“文件内容哈希”（不再依赖文件名猜测）
            hashed_rows = []

            def calc_hash(fp):
                img = get_thumbnail(fp)
                existing_meta = read_metadata(fp) or {}
                if existing_meta.get('phash'):
                    return {'fp': fp, 'img': img, 'phash': existing_meta['phash']}
                ph = get_phash(img) if img is not None else None
                return {'fp': fp, 'img': img, 'phash': ph}

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(calc_hash, fp): fp for fp in fps}
                total = len(future_map)
                done_count = 0
                for future in as_completed(future_map):
                    fp = future_map[future]
                    done_count += 1
                    try:
                        row = future.result()
                    except Exception as e:
                        gui_log(f"❌ 哈希计算失败 {os.path.basename(fp)}: {e}")
                        row = {'fp': fp, 'img': None, 'phash': None}
                    if not row.get('phash'):
                        gui_log(f"⚠️ 无法计算哈希: {os.path.basename(fp)}")
                    hashed_rows.append(row)
                    self.report_progress(int(done_count / max(total, 1) * 40))

            t_hash = time.perf_counter()
            gui_log(f"⏱️ 查询阶段1/3 哈希计算: {t_hash - t0:.2f}s（文件 {len(hashed_rows)}）")

            # 阶段2：SQL 批量溯源查询
            ph_list = [r['phash'] for r in hashed_rows if r.get('phash')]
            lineage_map = {}
            if ph_list:
                t_db_conn_start = time.perf_counter()
                query_db = DBManager()
                if not query_db.clone_auth(db):
                    ok, msg = query_db.connect(init_tables=False, warm_cache=False)
                    if not ok:
                        raise RuntimeError(msg)
                t_db_conn_end = time.perf_counter()
                gui_log(f"⏱️ API复用/连接耗时: {t_db_conn_end - t_db_conn_start:.4f}s")

                t_db_query_start = time.perf_counter()
                try:
                    lineage_map = query_db.get_lineage_batch(
                        ph_list,
                        exact_only=not enable_fuzzy,
                        workers=worker_count,
                    )
                finally:
                    query_db.close()
                t_db_query_end = time.perf_counter()
                gui_log(f"⏱️ API批量溯源耗时: {t_db_query_end - t_db_query_start:.2f}s")

            t_db = time.perf_counter()
            gui_log(
                f"⏱️ 查询阶段2/3 API溯源: {t_db - t_hash:.2f}s"
                f"（有效哈希 {len(ph_list)}，命中 {sum(1 for v in lineage_map.values() if v)}）"
            )

            # 阶段3：组装返回
            results = []
            total = max(1, len(hashed_rows))
            for i, row in enumerate(hashed_rows):
                ph = row.get('phash')
                lineage = lineage_map.get(ph) if ph else None
                results.append({'fp': row['fp'], 'img': row['img'], 'lineage': lineage})
                self.report_progress(40 + int((i + 1) / total * 60))

            t_end = time.perf_counter()
            gui_log(
                f"⏱️ 查询阶段3/3 结果组装: {t_end - t_db:.2f}s；总耗时: {t_end - t0:.2f}s"
            )

            return results

        def done(results):
            if not results:
                return
            self._clear_query_results()
            if hasattr(self, '_query_placeholder'):
                self._query_placeholder.hide()

            grouped = {}
            for res in results:
                lineage = res['lineage']
                ph = ""
                if lineage and isinstance(lineage, dict):
                    asset = lineage.get('asset') or {}
                    if isinstance(asset, dict):
                        ph = asset.get('phash', '')
                key = f"ph:{ph}" if ph else f"path:{os.path.abspath(res['fp']).lower()}"
                if key not in grouped:
                    grouped[key] = {
                        'fp': res['fp'],
                        'img': res['img'],
                        'lineage': lineage,
                        'merged_count': 1,
                    }
                else:
                    grouped[key]['merged_count'] += 1

            merged_results = sorted(
                grouped.values(),
                key=lambda r: os.path.basename(r['fp']).lower()
            )

            self._lineage_results.extend(
                {'fp': r['fp'], 'lineage': r['lineage']}
                for r in merged_results
            )

            merged_count = len(results) - len(merged_results)
            if hasattr(self, '_query_result_title'):
                if merged_count > 0:
                    self._query_result_title.setText(
                        f"查询结果（{len(merged_results)}项，已合并{merged_count}项重复）")
                else:
                    self._query_result_title.setText(f"查询结果（{len(merged_results)}项）")

            for res in merged_results:
                card = self._build_result_card(
                    res['fp'],
                    res['img'],
                    res['lineage'],
                    res['merged_count']
                )
                self._query_result_lay.addWidget(card)
            self._log(
                f"✅ 源迹查询完成，展示 {len(merged_results)} 项（合并重复 {merged_count} 项，线程 {worker_count}）")
        self._bg(task, done, msg="源迹查询")
    def _do_query_canva(self):
        tid = self._canva_id_search.text().strip()
        if not tid:
            QMessageBox.warning(self, "提示", "请输入Canva模板ID"); return
        def task():
            return db.get_lineage_by_canva_id(tid)
        def done(result):
            if not result:
                self._log(f"❓ Canva模板 [{tid}] 未找到"); return
            self._clear_query_results()
            if hasattr(self, '_query_placeholder'):
                self._query_placeholder.hide()
            tmpl    = result['template']
            tid_val = tmpl.get('template_id', '')
            tname   = tmpl.get('template_name', '?')
            tcreator = tmpl.get('creator', '?')
            card = QFrame()
            card.setStyleSheet(
                "QFrame{border:1px solid #dbe4ee;border-radius:10px;"
                "background:#ffffff;margin:1px;}")
            cv2_lay = QVBoxLayout(card); cv2_lay.setContentsMargins(10, 10, 10, 10)
            hdr = QHBoxLayout()
            lbl = QLabel(f"🎨 {tname}  【{tid_val}】  👤{tcreator}")
            lbl.setStyleSheet("font-weight:700;font-size:15px;color:#27435f;")
            lbl.setWordWrap(True)
            hdr.addWidget(lbl, 1)
            def _make_copy_fn(t):
                def fn():
                    QApplication.clipboard().setText(f"【{t}】")
                    self._log(f"✅ 已复制: 【{t}】")
                return fn
            btn_cp = PushButton("📋 复制模板ID")
            btn_cp.setMinimumWidth(126); btn_cp.setMinimumHeight(32)
            btn_cp.setStyleSheet("font-size:12px;background:#eef4fa;border:1px solid #d2dfec;border-radius:8px;")
            btn_cp.clicked.connect(_make_copy_fn(tid_val))
            hdr.addWidget(btn_cp); cv2_lay.addLayout(hdr)
            tree = QTreeWidget()
            tree.setColumnCount(4)
            tree.setHeaderHidden(True)
            tree.setColumnWidth(0, 520); tree.setColumnWidth(1, 130)
            tree.setColumnWidth(2, 200); tree.setColumnWidth(3, 96)
            tree.setAlternatingRowColors(False)
            tree.setStyleSheet(
                "QTreeWidget{border:1px solid #dde6ef;border-radius:8px;background:#fbfdff;}"
                "QTreeWidget::item{height:30px;font-size:13px;}")
            tree.setMinimumHeight(120); tree.setMaximumHeight(420)
            for asset in result['assets']:
                fname_a  = asset.get('filename', '?')
                prod_a   = asset.get('producer', '?')
                date_a   = str(asset.get('created_at', ''))[:16]
                atype_a  = asset.get('asset_type', '?')
                a_item = QTreeWidgetItem([f"  🖼 {fname_a}", prod_a, date_a, atype_a])
                a_item.setForeground(0, QColor("#2c3e50"))
                for anc in asset.get('ancestors', []):
                    a_item.addChild(self._make_ancestor_item(anc))
                a_item.setExpanded(False)
                tree.addTopLevelItem(a_item)
            cv2_lay.addWidget(tree)
            self._query_result_lay.addWidget(card)
            if hasattr(self, '_query_result_title'):
                self._query_result_title.setText(f"查询结果（模板，素材{len(result['assets'])}项）")
            self._log(f"✅ 模板源迹: {tname}  素材{len(result['assets'])}个")
        self._bg(task, done, msg="Canva模板源迹")
    def _refresh_lib(self):
        self._lib_data = db.get_all_assets(); self._fill_lib(self._lib_data)

    def _fill_lib(self, rows):
        self._tbl_lib.setRowCount(0)
        for r in rows:
            idx = self._tbl_lib.rowCount(); self._tbl_lib.insertRow(idx)
            self._tbl_lib.setItem(idx,0, QTableWidgetItem(r.get('filename','')))
            self._tbl_lib.setItem(idx,1, QTableWidgetItem(r.get('asset_type','')))
            self._tbl_lib.setItem(idx,2, QTableWidgetItem(r.get('producer','')))
            self._tbl_lib.setItem(idx,3, QTableWidgetItem(str(r.get('created_at',''))[:16]))
            self._tbl_lib.setItem(idx,4, QTableWidgetItem(f"{(r.get('file_size') or 0)/1024:.1f} KB"))
            ph = r.get('phash','')
            self._tbl_lib.setItem(idx,5, QTableWidgetItem((ph or '')[:16] + "…"))

    def _filter_lib(self, kw):
        kw = kw.lower()
        if not kw:
            self._fill_lib(self._lib_data); return
        self._fill_lib([r for r in self._lib_data
                        if kw in (r.get('filename') or '').lower()
                        or kw in (r.get('producer') or '').lower()])

    def _fix_wrong_producer_with_password(self):
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接")
            return

        d = QDialog(self)
        d.setWindowTitle("修正错误作者")
        d.setMinimumWidth(460)
        lay = QFormLayout(d)

        old_name = QLineEdit()
        old_name.setPlaceholderText("错误作者名（必填）")
        new_name = QLineEdit()
        new_name.setPlaceholderText("修正为（必填）")
        keyword = QLineEdit()
        keyword.setPlaceholderText("文件名包含（可选）")
        start_day = QLineEdit()
        start_day.setPlaceholderText("开始日期 YYYY-MM-DD（可选）")
        end_day = QLineEdit()
        end_day.setPlaceholderText("结束日期 YYYY-MM-DD（可选）")
        password = QLineEdit()
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText("请输入执行密码")

        lay.addRow("错误作者：", old_name)
        lay.addRow("修正为：", new_name)
        lay.addRow("文件名条件：", keyword)
        lay.addRow("开始日期：", start_day)
        lay.addRow("结束日期：", end_day)
        lay.addRow("验证密码：", password)

        tip = QLabel("仅在密码正确时执行。密码：xiugai2026")
        tip.setStyleSheet("color:#8a6d1f;font-size:12px;")
        lay.addRow("", tip)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        lay.addRow(btns)
        btns.accepted.connect(d.accept)
        btns.rejected.connect(d.reject)

        if d.exec() != QDialog.DialogCode.Accepted:
            return

        old_val = old_name.text().strip()
        new_val = new_name.text().strip()
        key_val = keyword.text().strip()
        start_val = start_day.text().strip()
        end_val = end_day.text().strip()
        pwd_val = password.text().strip()

        if pwd_val != "xiugai2026":
            QMessageBox.warning(self, "验证失败", "密码错误，已取消执行")
            self._log("⛔ 修正错误已拦截：密码错误")
            return

        if not old_val or not new_val:
            QMessageBox.warning(self, "提示", "错误作者和修正作者不能为空")
            return

        if old_val == new_val:
            QMessageBox.warning(self, "提示", "错误作者和修正作者相同，无需执行")
            return

        for txt, field_name in ((start_val, "开始日期"), (end_val, "结束日期")):
            if txt:
                try:
                    datetime.strptime(txt, "%Y-%m-%d")
                except Exception:
                    QMessageBox.warning(self, "提示", f"{field_name}格式应为 YYYY-MM-DD")
                    return

        if start_val and end_val and start_val > end_val:
            QMessageBox.warning(self, "提示", "开始日期不能晚于结束日期")
            return

        def task():
            return db.fix_wrong_producer(
                old_producer=old_val,
                new_producer=new_val,
                filename_keyword=key_val,
                start_date=start_val or None,
                end_date=end_val or None,
            )

        def done(affected):
            self._refresh_lib()
            summary = (
                f"修正完成：把作者【{old_val}】改为【{new_val}】，"
                f"共影响 {affected} 条"
            )
            self._log(f"✅ {summary}")
            QMessageBox.information(self, "修正错误", summary)

        self._bg(task, done, msg="修正错误作者")

    # ═══════════════════ 设置 ═════════════════════════
    def _dlg_settings(self):
        d = QDialog(self); d.setWindowTitle("系统设置"); d.setMinimumWidth(420)
        lay = QFormLayout(d)
        fn = QLineEdit(self._cfg.get('user_name', '操作员'))
        fh = QLineEdit(db.conf.get('host', ''))
        fu = QLineEdit(db.conf.get('user', ''))
        fw = QLineEdit(db.conf.get('password', '')); fw.setEchoMode(QLineEdit.EchoMode.Password)
        lay.addRow("操作员姓名：", fn); lay.addRow("API 地址：", fh)
        lay.addRow("API 用户：", fu); lay.addRow("API 密码：", fw)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(d.accept); btns.rejected.connect(d.reject); lay.addRow(btns)
        if d.exec():
            try:
                user_name = (fn.text() or '').strip() or self._cfg.get('user_name', '操作员')

                host = (fh.text() or '').strip()
                user = (fu.text() or '').strip()
                pwd = fw.text()

                self._cfg['user_name'] = user_name
                save_config(self._cfg)

                db.conf.update({
                    'host': host,
                    'user': user,
                    'password': pwd,
                })
                db.save_conf(db.conf)

                self._log(f"⚙️ 尝试连接服务器: {host}")

                # 在后台线程连接，避免卡主线程；进度框提供视觉反馈
                progress = QProgressDialog("正在连接服务器获取Token，请稍候…", "", 0, 0, self)
                progress.setCancelButton(None)
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.setMinimumDuration(0)
                progress.setValue(0)

                _conn_worker = Worker(lambda: db.connect())
                _conn_worker.setParent(self)

                def _on_conn_done(result, _u=user_name):
                    progress.close()
                    ok2, msg2 = result
                    self._lbl_user.setText(f"操作员 · {self._cfg['user_name']}")
                    self._log("✅ 设置保存，服务器连接" + ("成功" if ok2 else f"失败: {msg2}"))
                    if ok2:
                        self._refresh_code_table_after_db_connect()
                    if not ok2:
                        QMessageBox.warning(
                            self,
                            "连接失败",
                            f"连接失败：{msg2}\n\n请查看诊断日志：{DIAG_LOG_FILE}"
                        )

                def _on_conn_err(e):
                    progress.close()
                    _append_diag_log("设置重连异常", e)
                    self._log(f"❌ 设置保存失败: {e}")
                    QMessageBox.critical(
                        self,
                        "设置保存失败",
                        f"错误：{e}\n\n请查看诊断日志：{DIAG_LOG_FILE}"
                    )

                _conn_worker.done.connect(_on_conn_done)
                _conn_worker.error.connect(_on_conn_err)
                _conn_worker.finished.connect(
                    lambda: self._workers.remove(_conn_worker)
                    if _conn_worker in self._workers else None
                )
                self._workers.append(_conn_worker)
                _conn_worker.start()
                progress.exec()   # 阻塞 UI 直到 progress.close() 被 worker 信号触发
            except Exception as e:
                _append_diag_log("系统设置保存异常", traceback.format_exc())
                self._log(f"❌ 设置保存失败: {e}")
                QMessageBox.critical(
                    self,
                    "设置保存失败",
                    f"错误：{e}\n\n请查看诊断日志：{DIAG_LOG_FILE}"
                )


    # ── Tab7：批量扫描 ─────────────────────────────────
    def _tab_batch_scan(self):
        w = QWidget(); v = QVBoxLayout(w)

        # ─── 人员代码管理区 ──────────────────────────────
        code_box = QFrame()
        code_box.setStyleSheet(
            "QFrame{border:1px solid #bdc3c7;border-radius:6px;"
            "background:#f8f9fa;padding:4px;margin-bottom:4px;}")
        cv = QVBoxLayout(code_box)
        cv.addWidget(QLabel("👤  人员代码对照表  （文件名中的CODE → 真实姓名）"))

        # 表格：展示层
        self._code_table = TableWidget(0, 3)
        self._code_table.setHorizontalHeaderLabels(["CODE", "真实姓名", "操作"])
        self._code_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._code_table.setColumnWidth(0, 110)
        self._code_table.setColumnWidth(2, 72)
        self._code_table.verticalHeader().setVisible(False)
        self._code_table.setMinimumHeight(110)
        self._code_table.setMaximumHeight(230)
        self._code_table.setAlternatingRowColors(True)
        self._code_table.setShowGrid(False)
        cv.addWidget(self._code_table)
        self._code_table.itemChanged.connect(self._on_code_table_changed)

        # 添加行
        add_row = QHBoxLayout()
        self._code_input = LineEdit(); self._code_input.setPlaceholderText("CODE（如 KS、57）")
        self._code_input.setMinimumWidth(120)
        self._name_input = LineEdit(); self._name_input.setPlaceholderText("真实姓名（如 张三）")
        self._name_input.setMinimumWidth(180)
        btn_add_code = PushButton("➕ 添加")
        btn_add_code.setMinimumWidth(82)
        btn_add_code.clicked.connect(self._add_producer_code)
        btn_batch = PushButton("📋 批量粘贴")
        btn_batch.setMinimumWidth(102)
        btn_batch.setStyleSheet(
            "background:#8e44ad;color:#fff;height:30px;border:none;"
            "border-radius:5px;font-size:12px;")
        btn_batch.clicked.connect(self._batch_paste_codes)
        self._btn_save_codes = PushButton("💾 保存")
        self._btn_save_codes.setMinimumWidth(82)
        self._btn_save_codes.clicked.connect(self._save_producer_codes)
        add_row.addWidget(QLabel("CODE:"))
        add_row.addWidget(self._code_input)
        add_row.addWidget(QLabel("姓名:"))
        add_row.addWidget(self._name_input)
        add_row.addWidget(btn_add_code)
        add_row.addWidget(btn_batch)
        add_row.addWidget(self._btn_save_codes)
        add_row.addStretch()
        cv.addLayout(add_row)
        v.addWidget(code_box)
        self._load_code_table()  # 初始化载入已保存的表

        desc = QLabel(
            "批量扫描文件夹，将所有媒体文件自动登记入库。"
            "文件名中的CODE会自动匹配制作人，识别不到则写入「未知」。"
            "文件夹名含【ID】自动建立 Canva 模板关联。"
        )
        desc.setStyleSheet("color:#555;font-size:12px;padding:4px;")
        v.addWidget(desc)

        # 文件夹路径
        fr = QHBoxLayout()
        fr.addWidget(QLabel("扫描文件夹："))
        self._scan_path = LineEdit()
        self._scan_path.setPlaceholderText("粘贴路径，或点击右侧选择…")
        fr.addWidget(self._scan_path)
        btn_br = PushButton("📂 选择"); btn_br.setMinimumWidth(92)
        btn_br.clicked.connect(self._browse_scan_folder)
        fr.addWidget(btn_br); v.addLayout(fr)

        perf = QHBoxLayout()
        perf.addWidget(QLabel("扫描线程："))
        self._scan_threads = QSpinBox()
        self._scan_threads.setRange(1, 32)
        self._scan_threads.setValue(int(self._cfg.get('scan_threads', self._recommended_workers())))
        self._scan_threads.setMaximumWidth(90)
        self._scan_threads.setToolTip("并发读取/计算文件的线程数")
        perf.addWidget(self._scan_threads)

        perf.addWidget(QLabel("批量写库："))
        self._scan_upload_batch = QSpinBox()
        self._scan_upload_batch.setRange(20, 1000)
        self._scan_upload_batch.setSingleStep(20)
        self._scan_upload_batch.setValue(int(self._cfg.get('scan_upload_batch', 120)))
        self._scan_upload_batch.setMaximumWidth(100)
        self._scan_upload_batch.setToolTip("每累计 N 条新素材后批量写入数据库")
        perf.addWidget(self._scan_upload_batch)
        perf.addStretch(1)
        v.addLayout(perf)

        # 操作按钮
        br = QHBoxLayout()
        self._btn_scan_start = PushButton("▶  开始扫描")
        self._btn_scan_start.setStyleSheet(
            "background:#27ae60;color:#fff;height:40px;font-size:14px;")
        self._btn_scan_start.clicked.connect(self._do_scan_start)
        self._btn_scan_stop = PushButton("⏹  停止")
        self._btn_scan_stop.setStyleSheet(
            "background:#c0392b;color:#fff;height:40px;font-size:14px;")
        self._btn_scan_stop.setEnabled(False)
        self._btn_scan_stop.clicked.connect(self._do_scan_stop)
        br.addWidget(self._btn_scan_start); br.addWidget(self._btn_scan_stop)
        br.addStretch(); v.addLayout(br)

        # 进度条
        self._scan_bar = ProgressBar()
        self._scan_bar.setRange(0, 100); self._scan_bar.setValue(0)
        self._scan_bar.setTextVisible(True)
        self._scan_bar.setStyleSheet("height:20px;")
        v.addWidget(self._scan_bar)

        # 统计标签
        self._scan_stats = QLabel("等待开始…")
        self._scan_stats.setStyleSheet(
            "font-size:13px;color:#2c3e50;padding:4px;"
            "background:#ecf0f1;border-radius:4px;")
        v.addWidget(self._scan_stats)

        # 扫描日志（独立于主日志）
        self._scan_log = TextEdit(); self._scan_log.setReadOnly(True)
        self._scan_log.setStyleSheet(
            "background:#0d1117;color:#58a6ff;font-size:12px;font-family:Consolas,monospace;")
        v.addWidget(self._scan_log)
        return w

    def _browse_scan_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择扫描文件夹")
        if d:
            self._scan_path.setText(d)

    def _load_code_table(self):
        """从数据库加载人员代码表，若 DB 为空则自动从 JSON 迁移"""
        codes = db.get_producer_codes()
        if not codes:
            codes = load_producer_codes()  # JSON 备用/迁移
            for c, n in codes.items():
                db.upsert_producer_code(c, n)
        self._code_table.setRowCount(0)
        for code, name in codes.items():
            self._insert_code_row(code, name)
        self._code_table_dirty = False

    def _insert_code_row(self, code, name):
        self._code_table.blockSignals(True)
        idx = self._code_table.rowCount()
        self._code_table.insertRow(idx)
        self._code_table.setItem(idx, 0, QTableWidgetItem(code))
        self._code_table.setItem(idx, 1, QTableWidgetItem(name))
        # 使用纯文本避免部分系统缺少 emoji 字体导致按钮显示为方块
        btn_del = PushButton("删除")
        btn_del.setMinimumWidth(58)
        btn_del.setStyleSheet(
            "background:#fdf2f2;color:#b42318;border:1px solid #f5c2c7;"
            "border-radius:6px;font-size:12px;padding:0 6px;")
        btn_del.setToolTip("删除此条人员代码")
        btn_del.clicked.connect(lambda _, r=idx: self._del_code_row(r))
        self._code_table.setCellWidget(idx, 2, btn_del)
        self._code_table.blockSignals(False)

    def _del_code_row(self, row):
        btn = self.sender()
        for r in range(self._code_table.rowCount()):
            if self._code_table.cellWidget(r, 2) is btn:
                self._code_table.removeRow(r)
                self._code_table_dirty = True
                self._log("📝 已标记删除，点击保存后写入数据库")
                break

    def _add_producer_code(self):
        code = self._code_input.text().strip().upper()
        name = self._name_input.text().strip()
        if not code or not name:
            QMessageBox.warning(self, "提示", "CODE 和姓名不能为空"); return
        for r in range(self._code_table.rowCount()):
            if self._code_table.item(r, 0) and                self._code_table.item(r, 0).text().upper() == code:
                self._code_table.blockSignals(True)
                self._code_table.item(r, 1).setText(name)
                self._code_table.blockSignals(False)
                self._code_input.clear(); self._name_input.clear()
                self._code_table_dirty = True
                self._log(f"📝 已更新: {code} → {name}（待保存）")
                return
        self._insert_code_row(code, name)
        self._code_input.clear(); self._name_input.clear()
        self._code_table_dirty = True
        self._log(f"📝 已添加: {code} → {name}（待保存）")

    def _save_producer_codes(self):
        codes = {}
        for r in range(self._code_table.rowCount()):
            k = self._code_table.item(r, 0)
            v = self._code_table.item(r, 1)
            if k and v and k.text().strip():
                codes[k.text().strip().upper()] = v.text().strip()
        if not self._code_table_dirty:
            self._log("ℹ️ 人员代码无变更，无需保存")
            return

        if hasattr(self, '_btn_save_codes'):
            self._btn_save_codes.setEnabled(False)
            self._btn_save_codes.setText("保存中…")

        def task():
            worker_db = DBManager()
            if not worker_db.clone_auth(db):
                ok, msg = worker_db.connect()
                if not ok:
                    raise RuntimeError(msg)

            # 获取所有现有的代码并删除
            existing = worker_db.get_producer_codes()
            for code in existing.keys():
                worker_db.delete_producer_code(code)
            
            # 插入新的代码
            for c, n in codes.items():
                worker_db.upsert_producer_code(c, n)

            save_producer_codes(codes)  # JSON 备份
            worker_db.close()
            return len(codes)

        def done(saved_count):
            self._code_table_dirty = False
            db._producer_codes_cache = dict(codes)
            if hasattr(self, '_btn_save_codes'):
                self._btn_save_codes.setEnabled(True)
                self._btn_save_codes.setText("💾 保存")
            self._log(f"✅ 已保存 {saved_count} 条人员代码到数据库")

        def on_error(_e):
            if hasattr(self, '_btn_save_codes'):
                self._btn_save_codes.setEnabled(True)
                self._btn_save_codes.setText("💾 保存")

        self._bg(task, done, msg="保存人员代码", err_cb=on_error)

    def _on_code_table_changed(self, item):
        """表格内容变化时仅标记待保存，避免编辑即阻塞 UI。"""
        if item.column() not in (0, 1):
            return
        self._code_table_dirty = True

    def _batch_paste_codes(self):
        """批量粘贴人员代码对话框"""
        d = QDialog(self)
        d.setWindowTitle("批量粘贴人员代码")
        d.setMinimumSize(520, 430)
        vl = QVBoxLayout(d)

        hint = QLabel(
            "支持 Google Sheet 直接复制（多行多列），默认取前两列作为 CODE/姓名。\n"
            "兼容分隔符：Tab / 空格 / = / : / ,，支持全角标点（：，＝）。\n"
            "CODE 大小写均可，导入后统一转大写；已存在 CODE 会自动更新姓名。\n"
            "首行若是表头（CODE/姓名）会自动跳过；以 # 开头行会跳过。\n\n"
            "示例：\n"
            "XQ 张三       # 空格分隔\n"
            "XQ\t张三      # Google Sheet 常见 Tab 分隔\n"
            "34=李四        # = 分隔\n"
            "SXC:王五       # : 分隔\n"
            "85,赵六        # , 分隔"
        )
        hint.setStyleSheet(
            "color:#444;font-size:12px;background:#f5f5f7;"
            "padding:8px;border-radius:5px;font-family:Consolas,monospace;")
        vl.addWidget(hint)

        ta = QTextEdit()
        ta.setPlaceholderText("在此粘贴内容，每行一条 CODE 姓名…")
        ta.setMinimumHeight(180)
        ta.setStyleSheet("font-family:Consolas,monospace;font-size:13px;")
        vl.addWidget(ta)

        # 常用场景：先在表格软件复制，再打开此弹窗
        try:
            clip_txt = QApplication.clipboard().text()
            if clip_txt and clip_txt.strip():
                ta.setPlainText(clip_txt)
        except Exception:
            pass

        stat_lbl = QLabel("")
        stat_lbl.setStyleSheet("color:#2980b9;font-size:12px;")
        vl.addWidget(stat_lbl)

        hl2 = QHBoxLayout()
        btn_fill_clip = PushButton("📥  从剪贴板填充")
        btn_fill_clip.setStyleSheet(
            "background:#f0f3f7;color:#2f4a67;height:34px;"
            "border:1px solid #d4e0ec;border-radius:6px;font-size:12px;")
        hl2.addWidget(btn_fill_clip)
        hl2.addStretch()
        vl.addLayout(hl2)

        hl = QHBoxLayout()
        btn_ok = PushButton("✅  确认导入")
        btn_ok.setStyleSheet(
            "background:#2980b9;color:#fff;height:36px;"
            "border:none;border-radius:6px;font-size:13px;")
        btn_cancel = PushButton("取消")
        btn_cancel.setStyleSheet("height:36px;border:1px solid #ccc;border-radius:6px;font-size:13px;")
        hl.addWidget(btn_ok, 1); hl.addWidget(btn_cancel)
        vl.addLayout(hl)
        btn_cancel.clicked.connect(d.reject)

        def fill_from_clipboard():
            try:
                txt = QApplication.clipboard().text()
            except Exception:
                txt = ""
            if txt and txt.strip():
                ta.setPlainText(txt)
                stat_lbl.setText("✅ 已从剪贴板填充")
            else:
                stat_lbl.setText("⚠️ 剪贴板为空")

        btn_fill_clip.clicked.connect(fill_from_clipboard)

        def do_import():
            """后台线程解析文本，主线程更新表格；最终由“保存”统一落库。"""
            text = (ta.toPlainText() or "").replace('\u00a0', ' ').replace('\u3000', ' ')

            existing_codes = {}
            for r in range(self._code_table.rowCount()):
                code_item = self._code_table.item(r, 0)
                name_item = self._code_table.item(r, 1)
                if code_item and name_item and code_item.text().strip():
                    existing_codes[code_item.text().strip().upper()] = name_item.text().strip()
            
            def clean_cell(s: str) -> str:
                s = (s or "").strip().strip('"').strip("'").strip()
                return s.replace('\u00a0', ' ').replace('\u3000', ' ')

            def parse_line(raw_line: str):
                line = (raw_line or "").strip()
                if not line:
                    return None
                line = line.lstrip('\ufeff')
                if line.startswith('#'):
                    return None
                # 兼容全角分隔符
                line = line.replace('：', ':').replace('，', ',').replace('＝', '=')

                # Google Sheet 常见：Tab 分隔，多列时只取前两列
                if '\t' in line:
                    cols = [clean_cell(c) for c in line.split('\t')]
                    cols = [c for c in cols if c]
                    if len(cols) >= 2:
                        return cols[0], cols[1]

                # 常见键值分隔：= : , ; |
                m = re.match(r'^([^=:,;|\s]+)\s*[=:,;|]\s*(.+)$', line)
                if m:
                    return clean_cell(m.group(1)), clean_cell(m.group(2))

                # 空白分隔
                parts = line.split(None, 1)
                if len(parts) == 2:
                    return clean_cell(parts[0]), clean_cell(parts[1])
                return None

            def normalize_code(code: str) -> str | None:
                code = clean_cell(code)
                code = code.lstrip("'").strip()
                # 表格数值列常见：34.0 -> 34
                if re.match(r'^\d+\.0+$', code):
                    code = code.split('.', 1)[0]
                code = re.sub(r'\s+', '', code)
                if not re.match(r'^[A-Za-z0-9]{1,10}$', code):
                    return None
                return code.upper()

            # ── 后台任务：仅做解析，避免跨线程访问 UI / DB 连接竞争 ──
            def bg_task():
                skipped = 0
                parsed = {}
                
                for line in text.splitlines():
                    pair = parse_line(line)
                    if not pair:
                        skipped += 1
                        continue
                    code_raw, name_raw = pair
                    code = normalize_code(code_raw)
                    name = clean_cell(name_raw)
                    # 自动跳过表头
                    if code and code.upper() in {"CODE", "ID", "NO"} and name in {
                        "姓名", "真实姓名", "制作人", "人员", "名称", "NAME"
                    }:
                        continue
                    if not code or not name:
                        skipped += 1
                        continue
                    parsed[code] = name

                to_add = []
                to_update = {}
                for code, name in parsed.items():
                    old_name = existing_codes.get(code)
                    if old_name is None:
                        to_add.append((code, name))
                    elif old_name != name:
                        to_update[code] = name
                
                return {
                        'added': len(to_add),
                        'updated': len(to_update),
                        'skipped': skipped,
                        'parsed': len(parsed),
                        'to_add': to_add, 'to_update': to_update}
            
            def on_import_done(result):
                """主线程回调：更新 UI"""
                added = result['added']
                updated = result['updated']
                skipped = result['skipped']
                changed = False
                
                # 更新现有行
                for code, name in result['to_update'].items():
                    for r in range(self._code_table.rowCount()):
                        ci = self._code_table.item(r, 0)
                        if ci and ci.text().upper() == code:
                            self._code_table.blockSignals(True)
                            self._code_table.item(r, 1).setText(name)
                            self._code_table.blockSignals(False)
                            changed = True
                            break
                
                # 添加新行
                for code, name in result['to_add']:
                    self._insert_code_row(code, name)
                    changed = True

                if changed:
                    self._code_table_dirty = True
                
                msg = f"新增 {added} 条，更新 {updated} 条"
                if skipped:
                    msg += f"，跳过 {skipped} 行"
                if changed:
                    msg += "，请点击保存写入数据库"
                stat_lbl.setText(f"✅ {msg}")
                self._log(f"✅ 批量导入完成：{msg}")
                btn_ok.setText("关闭")
                btn_ok.clicked.disconnect()
                btn_ok.clicked.connect(d.accept)
            
            # 在后台线程中执行 bg_task
            self._bg(bg_task, on_import_done, msg="批量导入代码")

        btn_ok.clicked.connect(do_import)
        d.exec()

    def _get_code_map(self) -> dict:
        """从当前表格读取 code_map（不依赖磁盘文件，实时生效）"""
        codes = {}
        for r in range(self._code_table.rowCount()):
            k = self._code_table.item(r, 0)
            v = self._code_table.item(r, 1)
            if k and v and k.text().strip():
                codes[k.text().strip().upper()] = v.text().strip()
        if not codes and db.conn:
            db_codes = db.get_producer_codes() or {}
            if db_codes and not self._code_table_dirty:
                self._load_code_table()
            return dict(db_codes)
        return codes

    def _do_scan_start(self):
        folder = self._scan_path.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "提示", "请输入或选择有效的文件夹路径"); return
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接"); return

        worker_count = int(self._scan_threads.value()) if hasattr(self, '_scan_threads') else self._recommended_workers()
        upload_batch = int(self._scan_upload_batch.value()) if hasattr(self, '_scan_upload_batch') else 120
        self._cfg['scan_threads'] = worker_count
        self._cfg['scan_upload_batch'] = upload_batch
        save_config(self._cfg)

        op = self._cfg['user_name']
        code_map = self._get_code_map()
        self._scan_log.clear()
        self._scan_bar.setValue(0)
        self._scan_stats.setText("正在加载数据库已有素材列表…")
        known = db.get_all_phashes()
        self._scan_stats.setText(
            f"数据库已有 {len(known)} 个素材，开始扫描…（线程 {worker_count}，批量写库 {upload_batch}）"
        )
        self._scan_worker = ScanWorker(
            folder, op, known, code_map,
            workers=worker_count,
            upload_batch=upload_batch
        )
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.log_line.connect(self._scan_log.append)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.start()
        self._btn_scan_start.setEnabled(False)
        self._btn_scan_stop.setEnabled(True)

    def _do_scan_stop(self):
        if hasattr(self, '_scan_worker') and self._scan_worker.isRunning():
            self._scan_worker.stop()
            self._btn_scan_stop.setEnabled(False)
            self._scan_stats.setText("正在停止，等待当前文件处理完毕…")

    def _on_scan_progress(self, total, done, added, skipped, failed):
        pct = int(done / total * 100) if total else 0
        self._scan_bar.setValue(pct)
        self._scan_stats.setText(
            f"进度: {done} / {total}  |  "
            f"✅ 新增 {added}  |  ⏭ 跳过 {skipped}  |  ❌ 失败 {failed}"
        )

    def _on_scan_done(self, result):
        self._btn_scan_start.setEnabled(True)
        self._btn_scan_stop.setEnabled(False)
        if not result.get('stopped'):
            self._scan_bar.setValue(100)
        try:
            db._refresh_cache()
        except Exception:
            pass
        status = "⏸ 已手动停止" if result.get('stopped') else "✅ 扫描完成"
        msg = (f"{status}  |  总计 {result['total']} 个文件  |  "
               f"✅ 新增 {result['added']}  |  "
               f"⏭ 跳过 {result['skipped']}  |  "
               f"❌ 失败 {result['failed']}")
        if result.get('canva_id'):
            msg += f"  |  🎨 Canva【{result['canva_id']}】已登记"
        self._scan_stats.setText(msg)
        self._scan_log.append(f"\n{'─' * 60}\n{msg}")
        self._log(msg)
        self._refresh_lib()

    def _on_canva_auto_found_products(self, files, template_id, tracker_data=None):
        if not files: return
        
        if not db.conn:
            self._log("⚠️ 数据库未连接，跳过成品自动关联登记")
            return
            
        tmpl_name = ""
        tmpl_creator = ""
        src_phashes = []
        
        # 优先使用隐形 Tracker 数据
        if tracker_data and isinstance(tracker_data, dict):
            import uuid
            template_id = template_id or f"CANVA_{uuid.uuid4().hex[:8]}"
            tmpl_creator = tracker_data.get('creator', '')
            src_phashes = tracker_data.get('hashes', [])
            self._log(f"🔍 成功解析画布隐形追踪器: 制作者={tmpl_creator}, 源素材数={len(src_phashes)}")
        elif template_id:
            # 兼容老版本：通过 ID 去数据库查
            template_pack = db.get_canva_template_assets_basic(template_id)
            if template_pack:
                tmpl = template_pack.get('template', {})
                assets = template_pack.get('assets', [])
                tmpl_name = tmpl.get('template_name', '')
                tmpl_creator = tmpl.get('creator', '')
                src_phashes = [a.get('phash') for a in assets if a.get('phash')]
            else:
                self._log(f"⚠️ 模板ID【{template_id}】不存在，跳过成品自动登记")
                return
        else:
            self._log("⚠️ 无法识别衍生关系（无 Tracker 且无模板ID），跳过")
            return
            
        uniq_ph = list(set(src_phashes))
        op = self._cfg.get('user_name', 'System')
        
        def task():
          try:
            w.progress.emit(10)
            
            thread_db = DBManager()
            if not thread_db.clone_auth(db):
                ok, msg = thread_db.connect(init_tables=False, warm_cache=False)
                if not ok:
                    gui_log(f"❌ 自动检测后台数据库连接失败: {msg}")
                    return {"success": False}
                    
            ok_files = 0
            new_phashes = []
            for idx, fp in enumerate(files):
                w.progress.emit(10 + int(80 * (idx / len(files))))
                ph, _ = ensure_registered(fp, op, db_inst=thread_db)
                if not ph:
                    gui_log(f"  ❌ 文件处理失败: {os.path.basename(fp)}")
                    continue

                # Update metadata
                rec = read_metadata(fp) or {}
                
                ct_meta = {}
                if tmpl_creator: ct_meta["creator"] = tmpl_creator

                rec.update({
                    "phash": ph,
                    "filename": os.path.basename(fp),
                    "asset_type": get_asset_type(fp),
                    "file_size": get_file_size(fp),
                    "producer": op,
                    "created_at": datetime.now().isoformat(),
                    "canva_template": ct_meta
                })
                write_metadata(fp, rec)
                
                # Upsert to DB
                thread_db.upsert_assets_bulk([(
                    ph,
                    os.path.basename(fp),
                    get_asset_type(fp),
                    get_file_size(fp),
                    op,
                    datetime.now(),
                    json.dumps(rec, ensure_ascii=False, default=str),
                    None,
                )])
                ok_files += 1
                new_phashes.append(ph)
                
                # 不再建立衍生关系，仅保留模板归属信息
                
                gui_log(f"  ✅ 自动检测并成功入库: [{op}]{os.path.basename(fp)} (phash:{ph}) -> 涉及源素材 {len(uniq_ph)} 个")
                
            if template_id and uniq_ph:
                thread_db.add_canva_template(template_id, template_id, tmpl_creator or op, uniq_ph, "画布隐形标记自动登记")
                
            thread_db.close()
            w.progress.emit(100)
            return {"success": True, "done": ok_files, "total": len(files)}
          except Exception as e:
            import traceback
            gui_log(f"❌ Route B Error: {e} | {traceback.format_exc()}")
            return {"success": False}
            
        self._log(f"🎬 触发 Canva 成品自动关联入库: 发现 {len(files)} 个文件，隶属模板【{template_id}】")
        w = Worker(task)
        w.setParent(self)
        w.progress.connect(lambda v: (self._progress.setVisible(True), self._progress.setValue(v)))
        w.finished.connect(lambda: self._progress.setVisible(False))
        w.start()
        self._workers.append(w)


if __name__ == "__main__":
    _install_exception_hook()

    # ── Windows DPI 感知（必须在 QApplication 创建之前调用）──────
    # 不设置时 Windows 11 会用 GDI 缩放，导致窗口位置计算错误甚至不显示
    if sys.platform.startswith('win'):
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor DPI Aware v2
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # ── 单实例检测：已有实例运行时激活其窗口并退出 ──────────────
    if not _single_instance_check():
        sys.exit(0)

    try:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setStyleSheet("""
QWidget {
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
    color: #1b2533;
}
QMainWindow {
    background: #edf3f8;
}
QFrame#navPane {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #f8fbff,
                                stop:1 #e8eff7);
    border-right: 1px solid #d6e0ea;
}
QLabel#brandTitle {
    font-size: 18px;
    font-weight: 700;
    color: #102235;
}
QLabel#brandSub {
    font-size: 12px;
    color: #5f7388;
}
QLabel#userBadge {
    background: #dce8f4;
    border: 1px solid #c8d9ea;
    border-radius: 10px;
    padding: 7px 10px;
    color: #23405d;
    font-weight: 600;
}
QPushButton#navButton {
    text-align: left;
    border: none;
    border-radius: 10px;
    padding: 9px 12px;
    color: #31475e;
    background: transparent;
    font-size: 13px;
    min-height: 36px;
}
QPushButton#navButton:hover {
    background: #dce8f5;
}
QPushButton#navButton:checked {
    background: #1c8fff;
    color: #ffffff;
    font-weight: 700;
}
QPushButton#ghostButton {
    background: #eff5fb;
    border: 1px solid #d0ddeb;
    border-radius: 10px;
    padding: 7px 10px;
    color: #284460;
}
QPushButton#ghostButton:hover {
    background: #e2edf7;
}
QFrame#topCard, QFrame#logCard {
    background: #ffffff;
    border: 1px solid #d6e0ea;
    border-radius: 14px;
}
QLabel#pageTitle {
    font-size: 19px;
    font-weight: 700;
    color: #0f263d;
}
QLabel#pageHint {
    font-size: 13px;
    color: #64788c;
}
QLabel#logTitle {
    font-size: 13px;
    font-weight: 700;
    color: #1d3148;
}
QPushButton {
    background: #f3f7fb;
    border: 1px solid #ccdae7;
    border-radius: 8px;
    padding: 5px 14px;
    color: #213447;
    font-size: 13px;
    min-height: 30px;
}
QPushButton:hover {
    background: #e5eef7;
}
QPushButton:pressed {
    background: #d7e5f2;
}
QLineEdit, QTextEdit, QComboBox {
    background: #ffffff;
    border: 1px solid #cfdae6;
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 13px;
    selection-background-color: #1c8fff;
    min-height: 30px;
}
QLineEdit:focus, QTextEdit:focus {
    border-color: #1c8fff;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QTableWidget {
    background: #ffffff;
    border: 1px solid #d7e2ee;
    border-radius: 8px;
    gridline-color: #edf3f8;
    selection-background-color: #1c8fff;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #f4f8fc;
    border: none;
    border-bottom: 1px solid #dce6f0;
    padding: 5px 8px;
    font-size: 12px;
    color: #5c7086;
    font-weight: 600;
}
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #b7c7d8;
    border-radius: 3px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #b7c7d8;
    border-radius: 3px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar {
    background: #dce6f0;
    border: 1px solid #c8d5e3;
    border-radius: 6px;
    min-height: 18px;
    text-align: center;
}
QProgressBar::chunk {
    background: #1c8fff;
    border-radius: 5px;
}
QTextEdit#logbox {
    background: #0f1724;
    color: #8cf8ab;
    border: 1px solid #223a56;
    border-radius: 10px;
    font-family: Consolas, Menlo, monospace;
    font-size: 12px;
}
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    color: #dce6f0;
}
""")
        win = MamApp(); win.show()
        sys.exit(app.exec())
    except Exception as e:
        _append_diag_log("程序启动失败", traceback.format_exc())
        try:
            QMessageBox.critical(
                None,
                "启动失败",
                f"程序启动失败：{e}\n\n请查看诊断日志：{DIAG_LOG_FILE}"
            )
        except:
            pass
        raise


