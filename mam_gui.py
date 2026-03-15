# mam_gui.py — 主界面（纯 UI，业务逻辑见 mam_core / mam_db / mam_meta）
import sys

import os
import re
import json
import cv2
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime

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
    QFileDialog, QProgressBar, QStackedWidget
)
from PyQt6.QtCore  import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui   import QPixmap, QImage, QColor, QFont

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

# ─────────────────────────────────────────────────────
# 辅助：确保素材已在库中（自动登记）
# ─────────────────────────────────────────────────────
def ensure_registered(filepath, operator_name):
    """
    若素材未登记则自动登记并写入元数据。
    返回 (phash, record_dict) 或 (None, None)
    """
    img = get_thumbnail(filepath)
    if img is None:
        gui_log(f"❌ 无法读取: {os.path.basename(filepath)}")
        return None, None

    ph, source = get_phash_from_file(filepath, img)
    if not ph:
        gui_log(f"❌ phash计算失败: {os.path.basename(filepath)}")
        return None, None

    existing = db.lookup(ph, threshold=12)
    if existing:
        return existing['phash'], existing

    # 新素材 → 登记
    fname = os.path.basename(filepath)
    atype = get_asset_type(filepath)
    fsize = get_file_size(filepath)
    now   = datetime.now()
    rec   = {
        "phash": ph, "filename": fname,
        "asset_type": atype, "file_size": fsize,
        "producer": operator_name, "created_at": now.isoformat()
    }
    write_metadata(filepath, rec)
    db.upsert_asset(ph, fname, atype, fsize, operator_name, now,
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

    def __init__(self, folder, operator, known_phashes, code_map=None):
        super().__init__()
        self._folder       = folder
        self._operator     = operator
        self._known        = set(known_phashes)   # 线程内独立副本
        self._code_map     = code_map or {}
        self._should_stop  = False

    def stop(self):
        self._should_stop = True

    def run(self):
        folder      = self._folder
        folder_name = os.path.basename(folder.rstrip('/\\'))
        # 自动识别 Canva 文件夹名中的 【ID】
        m_id        = re.search(r'【(\d+)】', folder_name)
        canva_id    = m_id.group(1) if m_id else None
        canva_name  = re.sub(r'【\d+】', '', folder_name).strip() if m_id else None

        # ── 遍历收集所有媒体文件 ────────────────────────
        self.log_line.emit(f"📂 正在扫描文件列表: {folder}")
        all_files = []
        for rt, _, fs in os.walk(folder):
            for f in fs:
                if f.lower().endswith(ALL_EXTS):
                    all_files.append(os.path.join(rt, f))
        total = len(all_files)
        if canva_id:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件  |  🎨 Canva模板ID: 【{canva_id}】")
        else:
            self.log_line.emit(f"📋 发现 {total} 个媒体文件")
        if total == 0:
            self.finished.emit({'total': 0, 'added': 0, 'skipped': 0,
                                'failed': 0, 'canva_id': canva_id, 'stopped': False})
            return

        added = skipped = failed = 0
        canva_phashes = []

        for i, fp in enumerate(all_files):
            if self._should_stop:
                break
            try:
                img = get_thumbnail(fp)
                if img is None:
                    failed += 1
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                ph, _ = get_phash_from_file(fp, img)
                if not ph:
                    failed += 1
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                if ph in self._known:
                    skipped += 1
                    if canva_id:
                        canva_phashes.append(ph)
                    self.progress.emit(total, i + 1, added, skipped, failed)
                    continue
                # 新素材 → 注册并写入元数据
                fname    = os.path.basename(fp)
                atype    = get_asset_type(fp)
                fsize    = get_file_size(fp)
                now      = datetime.now()
                producer = parse_producer_from_filename(fname, self._code_map)
                rec   = {"phash": ph, "filename": fname, "asset_type": atype,
                         "file_size": fsize, "producer": producer,
                         "created_at": now.isoformat()}
                write_metadata(fp, rec)
                db.upsert_asset(ph, fname, atype, fsize, producer, now,
                                json.dumps(rec, ensure_ascii=False, default=str),
                                make_thumb_bytes(img))
                self._known.add(ph)
                if canva_id:
                    canva_phashes.append(ph)
                added += 1
                if added % 50 == 1:
                    self.log_line.emit(f"✅ 新增: {fname[:45]}  phash:{ph}")
            except Exception as e:
                failed += 1
                self.log_line.emit(f"❌ {os.path.basename(fp)}: {str(e)[:80]}")
            self.progress.emit(total, i + 1, added, skipped, failed)

        # ── Canva 模板自动登记 ───────────────────────────
        if canva_id and canva_phashes:
            try:
                unique_ph = list(dict.fromkeys(canva_phashes))
                db.add_canva_template(canva_id, canva_name, self._operator, unique_ph)
                self.log_line.emit(
                    f"🎨 Canva模板【{canva_id}】({canva_name}) 已登记，关联{len(unique_ph)}个素材"
                )
            except Exception as e:
                self.log_line.emit(f"⚠️ Canva模板登记失败: {e}")

        self.finished.emit({
            'total': total, 'added': added, 'skipped': skipped,
            'failed': failed, 'canva_id': canva_id, 'stopped': self._should_stop
        })


# ─────────────────────────────────────────────────────
# 后台线程
# ─────────────────────────────────────────────────────
class Worker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)
    def __init__(self, fn): super().__init__(); self._fn = fn
    def run(self):
        try:   self.done.emit(self._fn())
        except Exception as e: self.error.emit(str(e))

# ─────────────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────────────
class MamApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MAM 素材溯源管理系统 v3.1")
        self.setMinimumSize(1280, 920)
        self._cfg     = load_config()
        self._workers = []
        self._lib_data = []
        self._last_canva_id = None
        self._build_ui()
        log_bus.sig.connect(self._log)
        ok, msg = db.connect()
        self._log("✅ 数据库连接成功" if ok else f"⚠️ 数据库: {msg}")
        # exiftool 状态
        self._log(exiftool_status())
        # 检查 Python 依赖
        missing = check_deps()
        for m in missing:
            self._log(f"⚠️ 缺少依赖: {m}")

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
            ("Canva批量", "🗃", self._tab_canva_batch()),
            ("溯源查询", "🔍", self._tab_query()),
            ("素材总览", "🗂", self._tab_library()),
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
        log_title = QLabel("运行日志")
        log_title.setObjectName("logTitle")
        self._log_box = TextEdit(); self._log_box.setReadOnly(True)
        self._log_box.setMaximumHeight(150)
        self._log_box.setObjectName("logbox")
        log_l.addWidget(log_title)
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
        self._canva_id_search.clear()
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
        v.addStretch()
        return w

    # ── Tab5：源迹查询 ──────────────────────────────────────────────
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

        sep = QLabel("Canva 模板ID查询")
        sep.setStyleSheet("color:#60758b;font-size:12px;padding-top:4px;")
        lv.addWidget(sep)

        canva_row = QHBoxLayout(); canva_row.setSpacing(8)
        self._canva_id_search = LineEdit()
        self._canva_id_search.setPlaceholderText("输入Canva模板ID…")
        btn_cv = PushButton("查模板")
        btn_cv.setMinimumWidth(90)
        btn_cv.setStyleSheet(
            "background:#e9eef5;color:#2f4a67;height:38px;font-size:13px;"
            "border:1px solid #d4e0ec;border-radius:8px;")
        btn_cv.clicked.connect(self._do_query_canva)
        canva_row.addWidget(self._canva_id_search); canva_row.addWidget(btn_cv)
        lv.addLayout(canva_row)

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
        btn = PushButton("🔄 刷新"); btn.clicked.connect(self._refresh_lib)
        sr.addWidget(self._search_box); sr.addWidget(btn); v.addLayout(sr)
        self._tbl_lib = TableWidget(0, 6)
        self._tbl_lib.setHorizontalHeaderLabels(["文件名","类型","作者","时间","大小","pHash前16位"])
        self._tbl_lib.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tbl_lib.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v.addWidget(self._tbl_lib)
        return w

    # ═══════════════════ 后台任务 ═════════════════════
    def _bg(self, fn, done_cb=None, msg="操作"):
        w = Worker(fn)
        w.done.connect(lambda r: (done_cb(r) if done_cb else None,
                                   self._log(f"✅ {msg}完成")))
        w.error.connect(lambda e: self._log(f"❌ {msg}失败: {e}"))
        w.start(); self._workers.append(w)

    def _log(self, msg):
        self._log_box.append(f"[{datetime.now().strftime('%H:%M:%S')}]  {msg}")

    # ═══════════════════ 业务处理 ═════════════════════
    def _do_register(self):
        fps = self._drop_raw.files()
        if not fps: QMessageBox.warning(self, "提示", "请先拖入素材文件"); return
        op = self._cfg['user_name']
        code_map = self._get_code_map()   # 在主线程读取，供后台任务使用
        def task():
            for fp in fps:
                img = get_thumbnail(fp)
                if img is None: gui_log(f"⚠️ 无法读取: {os.path.basename(fp)}"); continue
                ph, src = get_phash_from_file(fp, img)
                if not ph: gui_log(f"❌ phash计算失败: {os.path.basename(fp)}"); continue
                fname = os.path.basename(fp); atype = get_asset_type(fp)
                fsize = get_file_size(fp); now = datetime.now()
                # 优先从文件名前缀码识别制作人（补充登记时能正确归属）
                producer = parse_producer_from_filename(fname, code_map) or op
                # 如已有元数据，可读取文件中已写入的 created_at，避免覆盖
                existing_meta = read_metadata(fp) or {}
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
                db.upsert_asset(ph, fname, atype, fsize, producer, created_at,
                                json.dumps(rec, ensure_ascii=False, default=str),
                                make_thumb_bytes(img))
                src_tag = "（从文件名识别）" if producer != op else ""
                gui_log(f"✅ 已登记: {fname}  作者:{producer}{src_tag}  phash:{ph}")
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

        def ensure_once(fp):
            if fp not in cache:
                cache[fp] = ensure_registered(fp, op)
            return cache[fp]

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
                db.upsert_asset(
                    ph_product,
                    os.path.basename(pfp),
                    get_asset_type(pfp),
                    get_file_size(pfp),
                    op,
                    datetime.now(),
                    json.dumps(rec, ensure_ascii=False, default=str),
                )
                ok_products += 1
                gui_log(
                    f"  ✅ 成品登记: [{op}]{os.path.basename(pfp)}  关联组件 {len(payload)} 个"
                )

            done_folders += 1

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
                ph, rec = ensure_registered(fp, op)
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
        m = re.search(r'【(\d+)】', name)
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

            lineage = db.get_lineage_by_canva_id(tid)
            if not lineage:
                no_template.append((fd, tid))
                continue

            tmpl = lineage.get('template', {})
            assets = lineage.get('assets', [])
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
                    if part_phashes:
                        db.add_compose(part_phashes, ph)

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
                    db.upsert_asset(
                        ph,
                        os.path.basename(fp),
                        get_asset_type(fp),
                        get_file_size(fp),
                        op,
                        datetime.now(),
                        json.dumps(rec, ensure_ascii=False, default=str),
                    )
                    ok_files += 1
                    gui_log(
                        f"  ✅ Canva关联: [{op}]{os.path.basename(fp)}"
                        f"  <- 模板【{tid}】素材 {len(source_payload)} 项"
                    )

                done_folders += 1

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

        def walk_canva_assets(rows):
            for r in rows or []:
                walk_ancestors(r.get('ancestors', []))
                walk_components(r.get('composed_from', []))
                add_name(r.get('producer', ''))

        walk_ancestors(lineage.get('derived_from', []))
        walk_components(lineage.get('composed_from', []))
        for t in lineage.get('canva_used', []):
            walk_canva_assets(t.get('assets', []))
            add_name(t.get('creator', ''))
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

                assets = t.get('assets') or []
                matched_ph = set(t.get('matched_phashes') or [])
                if assets:
                    sec_assets = QTreeWidgetItem([f"      📚 模板素材（{len(assets)}项）", ""])
                    sec_assets.setForeground(0, QColor("#a8432d"))
                    for a in assets:
                        aph = a.get('phash', '')
                        hit = "🔗 " if aph and aph in matched_ph else ""
                        a_item = QTreeWidgetItem([
                            f"        📄 {hit}{a.get('filename', '?')}",
                            a.get('producer', '?'),
                        ])
                        a_item.setForeground(0, QColor("#a04c3a"))
                        a_item.setForeground(1, QColor("#8899aa"))

                        ancestors = a.get('ancestors') or []
                        if ancestors:
                            anc_sec = QTreeWidgetItem([
                                f"        ⬆ 衍生自（父级，{len(ancestors)}项）", ""
                            ])
                            anc_sec.setForeground(0, QColor("#b84a0a"))
                            for anc in ancestors:
                                anc_sec.addChild(self._make_ancestor_item(anc))
                            a_item.addChild(anc_sec)
                        else:
                            src_tag = QTreeWidgetItem(["        · 原始素材（无衍生父级）", ""])
                            src_tag.setForeground(0, QColor("#95a5b2"))
                            a_item.addChild(src_tag)

                        comp_parts = a.get('composed_from') or []
                        if comp_parts:
                            comp_sec = QTreeWidgetItem([
                                f"        📦 由以下素材合成（{len(comp_parts)}项）", ""
                            ])
                            comp_sec.setForeground(0, QColor("#7a3aad"))
                            for p in comp_parts:
                                comp_sec.addChild(self._make_component_item(p))
                            a_item.addChild(comp_sec)

                        sec_assets.addChild(a_item)
                    child.addChild(sec_assets)
                else:
                    miss = QTreeWidgetItem(["      · 模板素材未找到或未入库", ""])
                    miss.setForeground(0, QColor("#95a5b2"))
                    child.addChild(miss)

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
            f"关联:{'直接' if (t.get('match_mode') or 'direct') == 'direct' else '上游'},"
            f"模板素材:{len(t.get('assets') or [])}项)"
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
        header = "\u6587\u4ef6\u540d\tphash\t\u5236\u4f5c\u4eba\t\u65e5\u671f\t\u884d\u751f\u6765\u6e90\t\u5c01\u88c5\u7ec4\u4ef6\t\u88ab\u7528\u4e8e\tCanva\u6a21\u677f(ID/\u5236\u4f5c\u4eba)"
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
        def task():
            results = []
            for fp in fps:
                try:
                    img = get_thumbnail(fp)
                    ph, _ = get_phash_from_file(fp, img)
                    lineage = db.get_lineage(ph) if ph else None
                except Exception as e:
                    gui_log(f"❌ {os.path.basename(fp)}: {e}")
                    img, lineage = None, None
                results.append({'fp': fp, 'img': img, 'lineage': lineage})
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
                f"✅ 源迹查询完成，展示 {len(merged_results)} 项（合并重复 {merged_count} 项）")
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

    # ═══════════════════ 设置 ═════════════════════════
    def _dlg_settings(self):
        d = QDialog(self); d.setWindowTitle("系统设置"); d.setMinimumWidth(420)
        lay = QFormLayout(d)
        fn = QLineEdit(self._cfg['user_name'])
        fh = QLineEdit(db.conf['host'])
        fp = QLineEdit(str(db.conf['port']))
        fu = QLineEdit(db.conf['user'])
        fw = QLineEdit(db.conf['password']); fw.setEchoMode(QLineEdit.EchoMode.Password)
        fd = QLineEdit(db.conf['db'])
        lay.addRow("操作员姓名：", fn); lay.addRow("MySQL 地址：", fh)
        lay.addRow("MySQL 端口：", fp); lay.addRow("MySQL 用户：", fu)
        lay.addRow("MySQL 密码：", fw); lay.addRow("数据库名：",   fd)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(d.accept); btns.rejected.connect(d.reject); lay.addRow(btns)
        if d.exec():
            self._cfg['user_name'] = fn.text(); save_config(self._cfg)
            db.conf.update({'host': fh.text(), 'port': int(fp.text() or 3306),
                            'user': fu.text(), 'password': fw.text(), 'db': fd.text()})
            db.save_conf(db.conf)
            ok, msg = db.connect()
            self._lbl_user.setText(f"操作员 · {self._cfg['user_name']}")
            self._log("✅ 设置保存，数据库重连" + ("成功" if ok else f"失败: {msg}"))


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
        btn_save_codes = PushButton("💾 保存")
        btn_save_codes.setMinimumWidth(82)
        btn_save_codes.clicked.connect(self._save_producer_codes)
        add_row.addWidget(QLabel("CODE:"))
        add_row.addWidget(self._code_input)
        add_row.addWidget(QLabel("姓名:"))
        add_row.addWidget(self._name_input)
        add_row.addWidget(btn_add_code)
        add_row.addWidget(btn_batch)
        add_row.addWidget(btn_save_codes)
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
                code_item = self._code_table.item(r, 0)
                if code_item and code_item.text().strip():
                    db.delete_producer_code(code_item.text())
                self._code_table.removeRow(r); break

    def _add_producer_code(self):
        code = self._code_input.text().strip().upper()
        name = self._name_input.text().strip()
        if not code or not name:
            QMessageBox.warning(self, "提示", "CODE 和姓名不能为空"); return
        db.upsert_producer_code(code, name)
        for r in range(self._code_table.rowCount()):
            if self._code_table.item(r, 0) and                self._code_table.item(r, 0).text().upper() == code:
                self._code_table.blockSignals(True)
                self._code_table.item(r, 1).setText(name)
                self._code_table.blockSignals(False)
                self._code_input.clear(); self._name_input.clear()
                self._log(f"✅ 已更新: {code} → {name}"); return
        self._insert_code_row(code, name)
        self._code_input.clear(); self._name_input.clear()
        self._log(f"✅ 已添加: {code} → {name}")

    def _save_producer_codes(self):
        codes = {}
        for r in range(self._code_table.rowCount()):
            k = self._code_table.item(r, 0)
            v = self._code_table.item(r, 1)
            if k and v and k.text().strip():
                codes[k.text().strip().upper()] = v.text().strip()
        if db.conn:
            with db.conn.cursor() as cur:
                cur.execute("DELETE FROM producer_codes")
            db.conn.commit()
            for code, name in codes.items():
                db.upsert_producer_code(code, name)
        save_producer_codes(codes)  # JSON 备份
        self._log(f"✅ 已保存 {len(codes)} 条人员代码到数据库")

    def _on_code_table_changed(self, item):
        """表格内容变化时自动保存到 DB"""
        if item.column() not in (0, 1):
            return
        code_item = self._code_table.item(item.row(), 0)
        name_item = self._code_table.item(item.row(), 1)
        if code_item and name_item and \
                code_item.text().strip() and name_item.text().strip():
            db.upsert_producer_code(code_item.text().strip(),
                                    name_item.text().strip())

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
            text = (ta.toPlainText() or "").replace('\u00a0', ' ').replace('\u3000', ' ')
            added = updated = skipped = 0

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

                db.upsert_producer_code(code, name)
                # 更新或插入表格行
                found = False
                for r in range(self._code_table.rowCount()):
                    ci = self._code_table.item(r, 0)
                    if ci and ci.text().upper() == code:
                        self._code_table.blockSignals(True)
                        self._code_table.item(r, 1).setText(name)
                        self._code_table.blockSignals(False)
                        found = True
                        updated += 1
                        break
                if not found:
                    self._insert_code_row(code, name)
                    added += 1

            msg = f"新增 {added} 条，更新 {updated} 条"
            if skipped:
                msg += f"，跳过 {skipped} 行"
            stat_lbl.setText(f"✅ {msg}")
            self._log(f"✅ 批量导入完成：{msg}")
            btn_ok.setText("关闭")
            btn_ok.clicked.disconnect()
            btn_ok.clicked.connect(d.accept)

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
        return codes

    def _do_scan_start(self):
        folder = self._scan_path.text().strip()
        if not folder or not os.path.isdir(folder):
            QMessageBox.warning(self, "提示", "请输入或选择有效的文件夹路径"); return
        if not db.conn:
            QMessageBox.warning(self, "提示", "数据库未连接，请先在【系统设置】中连接"); return
        op = self._cfg['user_name']
        code_map = self._get_code_map()
        self._scan_log.clear()
        self._scan_bar.setValue(0)
        self._scan_stats.setText("正在加载数据库已有素材列表…")
        known = db.get_all_phashes()
        self._scan_stats.setText(f"数据库已有 {len(known)} 个素材，开始扫描…")
        self._scan_worker = ScanWorker(folder, op, known, code_map)
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


if __name__ == "__main__":
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
