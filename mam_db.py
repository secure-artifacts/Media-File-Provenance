# mam_db.py — 数据库管理（MySQL）
# 列名使用 metadata_json 兼容旧表；关系表不使用外键，避免 charset 不兼容
import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

DB_CONFIG_FILE = "mam_db_config.json"

try:
    import pymysql
    MYSQL_OK = True
except ImportError:
    MYSQL_OK = False


def _hamming(h1, h2):
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except:
        return 64


class DBManager:
    def __init__(self):
        self.conn = None
        self.conf = self._load_conf()
        self._phash_cache = None  # 缓存所有 phash（用于快速去重）
        self._producer_codes_cache = None  # 缓存产生者代码

    def _load_conf(self):
        if os.path.exists(DB_CONFIG_FILE):
            try:
                return json.load(open(DB_CONFIG_FILE, 'r', encoding='utf-8'))
            except:
                pass
        return {"host": "localhost", "user": "root",
                "password": "", "db": "mam_system", "port": 3306}

    def save_conf(self, conf):
        self.conf = conf
        json.dump(conf, open(DB_CONFIG_FILE, 'w', encoding='utf-8'), indent=2)

    def connect(self):
        if not MYSQL_OK:
            return False, "未安装 pymysql，请运行: pip install pymysql"
        try:
            self.conn = pymysql.connect(
                host=self.conf['host'], user=self.conf['user'],
                password=self.conf['password'], database=self.conf['db'],
                port=int(self.conf['port']), charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor, autocommit=True
            )
            self._init_tables()
            # 连接成功后立即初始化缓存
            self._refresh_cache()
            return True, "连接成功"
        except Exception as e:
            return False, str(e)

    def close(self):
        """关闭数据库连接。"""
        if self.conn:
            try:
                self.conn.close()
            except:
                pass
            self.conn = None

    def _init_tables(self):
        with self.conn.cursor() as cur:
            # ── 主资产表 ────────────────────────────────
            # 兼容旧表：使用 IF NOT EXISTS，已有表不会被覆盖
            cur.execute("""
                CREATE TABLE IF NOT EXISTS assets (
                    phash          VARCHAR(64)  NOT NULL,
                    filename       VARCHAR(255),
                    file_size      BIGINT,
                    asset_type     VARCHAR(20),
                    producer       VARCHAR(100),
                    created_at     DATETIME,
                    metadata_json  MEDIUMTEXT,
                    thumbnail      MEDIUMBLOB,
                    PRIMARY KEY (phash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 旧表缺少的列，尝试补充（已存在则静默跳过）
            _extra_cols = [
                "ALTER TABLE assets ADD COLUMN asset_type VARCHAR(20)",
                "ALTER TABLE assets ADD COLUMN file_size BIGINT",
                "ALTER TABLE assets ADD COLUMN producer VARCHAR(100)",
                "ALTER TABLE assets ADD COLUMN metadata_json MEDIUMTEXT",
                "ALTER TABLE assets ADD COLUMN thumbnail MEDIUMBLOB",
            ]
            for sql in _extra_cols:
                try:
                    cur.execute(sql)
                except:
                    pass  # 列已存在

            # ── 衍生关系表（无外键，避免 charset 兼容问题）────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rel_derive (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    src_phash   VARCHAR(64) NOT NULL  COMMENT '来源素材 phash',
                    dst_phash   VARCHAR(64) NOT NULL  COMMENT '衍生素材 phash',
                    rel_type    VARCHAR(50)            COMMENT '关系类型',
                    operator    VARCHAR(100),
                    created_at  DATETIME,
                    remark      TEXT,
                    INDEX idx_src(src_phash),
                    INDEX idx_dst(dst_phash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── 成品组合关系表 ───────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rel_compose (
                    id             INT AUTO_INCREMENT PRIMARY KEY,
                    part_phash     VARCHAR(64) NOT NULL  COMMENT '组件 phash',
                    product_phash  VARCHAR(64) NOT NULL  COMMENT '成品 phash',
                    part_order     INT DEFAULT 0,
                    part_role      VARCHAR(50),
                    created_at     DATETIME,
                    INDEX idx_part(part_phash),
                    INDEX idx_product(product_phash)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── Canva 模板表 ─────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS canva_templates (
                    template_id    VARCHAR(30)  NOT NULL PRIMARY KEY,
                    template_name  VARCHAR(255),
                    creator        VARCHAR(100),
                    created_at     DATETIME,
                    asset_phashes  TEXT         COMMENT '使用的素材 phash 列表(JSON)',
                    remark         TEXT
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── 人员代码表 ──────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS producer_codes (
                    code        VARCHAR(20)  NOT NULL PRIMARY KEY,
                    name        VARCHAR(100) NOT NULL,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                                           ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── 统一关系表 collation，消除 JOIN 时『Illegal mix of collations』错误 ──
            cur.execute("""
                SELECT TABLE_COLLATION FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'assets'
            """)
            c = cur.fetchone()
            if c and c.get('TABLE_COLLATION'):
                cl = c['TABLE_COLLATION']
                for tbl in ('rel_derive', 'rel_compose', 'canva_templates'):
                    try:
                        cur.execute(
                            f"ALTER TABLE `{tbl}` CONVERT TO CHARACTER SET utf8mb4 COLLATE {cl}"
                        )
                    except:
                        pass

            # ── 添加索引以提升查询性能 ─────────────────────
            indexes = [
                ("CREATE INDEX IF NOT EXISTS idx_assets_producer ON assets(producer)", "assets producer"),
                ("CREATE INDEX IF NOT EXISTS idx_assets_filename ON assets(filename(20))", "assets filename"),
                ("CREATE INDEX IF NOT EXISTS idx_rel_derive_src ON rel_derive(src_phash)", "rel_derive src"),
                ("CREATE INDEX IF NOT EXISTS idx_rel_derive_dst ON rel_derive(dst_phash)", "rel_derive dst"),
                ("CREATE INDEX IF NOT EXISTS idx_rel_compose_part ON rel_compose(part_phash)", "rel_compose part"),
                ("CREATE INDEX IF NOT EXISTS idx_rel_compose_product ON rel_compose(product_phash)", "rel_compose product"),
            ]
            for sql, desc in indexes:
                try:
                    cur.execute(sql)
                except:
                    pass  # 索引可能已存在

    # ── 写入 / 更新 ─────────────────────────────────────
    def upsert_asset(self, phash, filename, asset_type, file_size,
                     producer, created_at, metadata_json, thumbnail=None):
        if not self.conn:
            return False
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO assets
                    (phash, filename, asset_type, file_size, producer,
                     created_at, metadata_json, thumbnail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    filename      = VALUES(filename),
                    asset_type    = VALUES(asset_type),
                    file_size     = VALUES(file_size),
                    producer      = VALUES(producer),
                    metadata_json = VALUES(metadata_json),
                    thumbnail     = COALESCE(VALUES(thumbnail), thumbnail)
            """, (phash, filename, asset_type, file_size,
                  producer, created_at, metadata_json, thumbnail))
        if self._phash_cache is not None and phash:
            self._phash_cache.add(phash)
        return True

    def upsert_assets_bulk(self, rows):
        """批量写入素材，显著降低逐条 INSERT 的网络往返成本。"""
        if not self.conn or not rows:
            return 0
        with self.conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO assets
                    (phash, filename, asset_type, file_size, producer,
                     created_at, metadata_json, thumbnail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    filename      = VALUES(filename),
                    asset_type    = VALUES(asset_type),
                    file_size     = VALUES(file_size),
                    producer      = VALUES(producer),
                    metadata_json = VALUES(metadata_json),
                    thumbnail     = COALESCE(VALUES(thumbnail), thumbnail)
            """, rows)
        if self._phash_cache is not None:
            for r in rows:
                try:
                    self._phash_cache.add(r[0])
                except:
                    pass
        return len(rows)

    def add_derive(self, src, dst, rel_type, operator, remark=""):
        if not self.conn:
            return
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rel_derive
                    (src_phash, dst_phash, rel_type, operator, created_at, remark)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (src, dst, rel_type, operator, datetime.now(), remark))

    def add_compose(self, part_phashes, product_phash, roles=None):
        if not self.conn:
            return
        with self.conn.cursor() as cur:
            for i, ph in enumerate(part_phashes):
                role = roles[i] if roles and i < len(roles) else "component"
                cur.execute("""
                    INSERT INTO rel_compose
                        (part_phash, product_phash, part_order, part_role, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                """, (ph, product_phash, i, role, datetime.now()))
        self._phash_cache = None  # 清空缓存，触发下次刷新

    # ── 缓存管理 ────────────────────────────────────────
    def _refresh_cache(self):
        """连接成功后立即加载缓存，加速后续查询"""
        if not self.conn:
            return
        try:
            with self.conn.cursor() as cur:
                # 缓存所有 phash 用于快速去重检查
                cur.execute("SELECT phash FROM assets")
                self._phash_cache = {r['phash'] for r in cur.fetchall()}
                # 缓存产生者代码用于 UI 快速显示
                self._producer_codes_cache = self.get_producer_codes()
        except:
            self._phash_cache = set()
            self._producer_codes_cache = {}

    def add_canva_template(self, template_id, name, creator, phash_list, remark=""):
        if not self.conn:
            return
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO canva_templates
                    (template_id, template_name, creator,
                     created_at, asset_phashes, remark)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    template_name  = VALUES(template_name),
                    asset_phashes  = VALUES(asset_phashes)
            """, (template_id, name, creator, datetime.now(),
                  json.dumps(phash_list, ensure_ascii=False), remark))

    # ── 查询 ────────────────────────────────────────────
    def lookup(self, phash, threshold=12):
        """模糊查询：汉明距离 ≤ threshold 时返回最相近记录（优化版：使用缓存）"""
        if not self.conn or not phash:
            return None
        
        # 首先尝试精确匹配（最常见情况）
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT phash, filename, asset_type, file_size, producer, "
                "created_at, metadata_json FROM assets WHERE phash = %s",
                (phash,)
            )
            exact = cur.fetchone()
            if exact:
                exact['distance'] = 0
                exact['similarity'] = "100%"
                return exact

        # 若无精确匹配，先做前缀候选，避免大库全表扫描
        candidates = []
        with self.conn.cursor() as cur:
            for prefix_len in (2, 1):
                p = phash[:prefix_len]
                if not p:
                    continue
                cur.execute(
                    "SELECT phash, filename, asset_type, file_size, producer, "
                    "created_at, metadata_json FROM assets WHERE phash LIKE %s",
                    (f"{p}%",)
                )
                rows = cur.fetchall()
                if rows:
                    candidates = rows
                    break

            if not candidates:
                cur.execute(
                    "SELECT phash, filename, asset_type, file_size, producer, "
                    "created_at, metadata_json FROM assets LIMIT 50000"
                )
                candidates = cur.fetchall()

        best, best_d = None, 64
        for r in candidates:
            d = _hamming(phash, r['phash'])
            if d < best_d:
                best_d = d
                best = r
        
        if best and best_d <= threshold:
            best['distance'] = best_d
            best['similarity'] = f"{int((1 - best_d/64)*100)}%"
            return best
        return None

    def get_assets_by_phashes(self, phashes):
        """按 phash 列表批量读取资产基础信息，返回 {phash: row}。"""
        if not self.conn:
            return {}
        clean = [p for p in (phashes or []) if p]
        if not clean:
            return {}
        uniq = list(dict.fromkeys(clean))
        placeholders = ",".join(["%s"] * len(uniq))
        sql = (
            "SELECT phash, filename, asset_type, file_size, producer, "
            "created_at, metadata_json FROM assets "
            f"WHERE phash IN ({placeholders})"
        )
        with self.conn.cursor() as cur:
            cur.execute(sql, tuple(uniq))
            rows = cur.fetchall()
        out = {}
        for r in rows:
            r['distance'] = 0
            r['similarity'] = "100%"
            out[r['phash']] = r
        return out

    # ── 递归溯源链（优化：避免 visited.copy() 副本）──────────────────────────────────────
    def _get_derive_chain_up(self, phash, visited=None, depth=0, max_depth=8):
        """递归向上：此 phash 是从哪些素材衍生而来（返回带 ancestors 键的行列表）"""
        if visited is None:
            visited = set()
        if phash in visited or depth >= max_depth:
            return []
        visited.add(phash)
        rows = []
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT d.src_phash, d.rel_type, d.operator,
                           a.filename, a.producer, a.created_at, a.asset_type
                    FROM rel_derive d JOIN assets a ON d.src_phash = a.phash
                    WHERE d.dst_phash = %s
                """, (phash,))
                rows = list(cur.fetchall())
        except:
            return []
        for row in rows:
            # 直接传递 visited 集合，避免 .copy() 操作，同时新加项目会被追踪
            row['ancestors'] = self._get_derive_chain_up(
                row['src_phash'], visited, depth + 1, max_depth
            )
        return rows

    def _get_derive_chain_down(self, phash, visited=None, depth=0, max_depth=8):
        """递归向下：此 phash 被衍生成了哪些版本（返回带 descendants 键的行列表）"""
        if visited is None:
            visited = set()
        if phash in visited or depth >= max_depth:
            return []
        visited.add(phash)
        rows = []
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT d.dst_phash, d.rel_type, d.operator,
                           a.filename, a.producer, a.created_at, a.asset_type
                    FROM rel_derive d JOIN assets a ON d.dst_phash = a.phash
                    WHERE d.src_phash = %s
                """, (phash,))
                rows = list(cur.fetchall())
        except:
            return []
        for row in rows:
            # 直接传递 visited 集合，避免 .copy() 操作，同时新加项目会被追踪
            row['descendants'] = self._get_derive_chain_down(
                row['dst_phash'], visited, depth + 1, max_depth
            )
        return rows

    def _collect_derive_src_phashes(self, rows, out_set: set):
        """从 derived_from/ancestors 结构中提取所有上游 src_phash。"""
        for row in rows or []:
            ph = row.get('src_phash')
            if ph:
                out_set.add(ph)
            self._collect_derive_src_phashes(row.get('ancestors', []), out_set)

    def _collect_compose_part_phashes(self, rows, out_set: set):
        """从 composed_from/sub_parts 结构中提取组件 phash，并继续提取其祖先链。"""
        for row in rows or []:
            ph = row.get('part_phash')
            if ph:
                out_set.add(ph)
            self._collect_derive_src_phashes(row.get('ancestors', []), out_set)
            self._collect_compose_part_phashes(row.get('sub_parts', []), out_set)

    def _build_canva_assets_lineage(self, phash_list):
        """构建 Canva 模板素材列表，并附带每个素材的衍生/封装溯源。"""
        if not self.conn:
            return []
        assets = []
        for ph in phash_list or []:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT phash, filename, asset_type, file_size, producer, created_at "
                        "FROM assets WHERE phash = %s", (ph,)
                    )
                    asset = cur.fetchone()
            except:
                asset = None
            if not asset:
                continue
            asset['ancestors'] = self._get_derive_chain_up(ph)
            asset['composed_from'] = self._get_compose_tree(ph)
            assets.append(asset)
        return assets

    def _fetch_canva_templates(self):
        if not self.conn:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT template_id, template_name, creator, created_at, asset_phashes "
                "FROM canva_templates"
            )
            return list(cur.fetchall())

    def _build_lineage_from_base(self, base, canva_templates=None):
        """基于已命中的基础资产构建完整溯源，避免重复 base 查询。"""
        exact = base['phash']
        result = {
            "asset":        base,
            "derived_from": [],
            "derived_to":   [],
            "composed_from":[],
            "used_in":      [],
            "canva_used":   [],
        }
        with self.conn.cursor() as cur:
            # ── 直接来源 + 递归祖先链 ─────────────────────
            cur.execute("""
                SELECT d.src_phash, d.rel_type, d.operator,
                       a.filename, a.producer, a.created_at, a.asset_type
                FROM rel_derive d JOIN assets a ON d.src_phash = a.phash
                WHERE d.dst_phash = %s
            """, (exact,))
            rows = list(cur.fetchall())
            for row in rows:
                row['ancestors'] = self._get_derive_chain_up(row['src_phash'])
            result["derived_from"] = rows

            # ── 向下衍生 + 递归后代链 ─────────────────────
            cur.execute("""
                SELECT d.dst_phash, d.rel_type, d.operator,
                       a.filename, a.producer, a.created_at, a.asset_type
                FROM rel_derive d JOIN assets a ON d.dst_phash = a.phash
                WHERE d.src_phash = %s
            """, (exact,))
            rows = list(cur.fetchall())
            for row in rows:
                row['descendants'] = self._get_derive_chain_down(row['dst_phash'])
            result["derived_to"] = rows

        result["composed_from"] = self._get_compose_tree(exact)

        # Canva 关联匹配范围：当前素材 + 上游衍生 + 封装组件 + 组件祖先
        canva_scope = {exact}
        self._collect_derive_src_phashes(result["derived_from"], canva_scope)
        self._collect_compose_part_phashes(result["composed_from"], canva_scope)

        with self.conn.cursor() as cur:
            # ── 被哪些成品使用 ────────────────────────────
            cur.execute("""
                SELECT c.product_phash, c.part_role,
                       a.filename, a.producer, a.created_at, a.asset_type
                FROM rel_compose c JOIN assets a ON c.product_phash = a.phash
                WHERE c.part_phash = %s
            """, (exact,))
            result["used_in"] = list(cur.fetchall())

        tmpls = canva_templates if canva_templates is not None else self._fetch_canva_templates()
        for tmpl in tmpls:
            try:
                phashes = json.loads(tmpl['asset_phashes']) if tmpl['asset_phashes'] else []
            except:
                phashes = []
            phash_set = set(phashes)
            if exact in phash_set:
                mode = 'direct'
                matched = [exact]
            else:
                mode = 'upstream'
                matched = [ph for ph in phashes if ph in canva_scope]

            if not matched:
                continue

            t = dict(tmpl)
            t['match_mode'] = mode
            t['matched_phashes'] = matched
            t['matched_count'] = len(matched)
            t['assets'] = self._build_canva_assets_lineage(phashes)
            result["canva_used"].append(t)
        return result

    def get_lineage(self, phash, exact_only=False):
        """返回完整溯源树 dict（含多级递归衍生链 + Canva 模板引用）"""
        if not self.conn:
            return None
        if exact_only:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT phash, filename, asset_type, file_size, producer, "
                    "created_at, metadata_json FROM assets WHERE phash = %s",
                    (phash,)
                )
                base = cur.fetchone()
            if base:
                base['distance'] = 0
                base['similarity'] = "100%"
        else:
            base = self.lookup(phash)
        if not base:
            return None
        return self._build_lineage_from_base(base)

    def get_lineage_batch(self, phashes, exact_only=True, workers=4):
        """批量溯源：先 SQL 批量取 base，再并发构建 lineage。"""
        if not self.conn:
            return {}

        ordered = [p for p in (phashes or []) if p]
        if not ordered:
            return {}
        uniq = list(dict.fromkeys(ordered))

        base_map = self.get_assets_by_phashes(uniq)
        if not exact_only:
            for ph in uniq:
                if ph not in base_map:
                    b = self.lookup(ph)
                    if b:
                        base_map[ph] = b

        canva_templates = self._fetch_canva_templates()
        if workers <= 1 or len(uniq) <= 1:
            out = {}
            for ph in uniq:
                base = base_map.get(ph)
                out[ph] = self._build_lineage_from_base(dict(base), canva_templates) if base else None
            return out

        lock = threading.Lock()
        thread_dbs = {}

        def get_thread_db():
            tid = threading.get_ident()
            with lock:
                existing = thread_dbs.get(tid)
                if existing:
                    return existing
                wdb = DBManager()
                wdb.conf = dict(self.conf)
                ok, msg = wdb.connect()
                if not ok:
                    raise RuntimeError(msg)
                thread_dbs[tid] = wdb
                return wdb

        def process_one(ph):
            base = base_map.get(ph)
            if not base:
                return ph, None
            wdb = get_thread_db()
            return ph, wdb._build_lineage_from_base(dict(base), canva_templates)

        out = {ph: None for ph in uniq}
        try:
            with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
                fut_map = {ex.submit(process_one, ph): ph for ph in uniq}
                for fut in as_completed(fut_map):
                    ph = fut_map[fut]
                    try:
                        key, value = fut.result()
                        out[key] = value
                    except:
                        out[ph] = None
        finally:
            with lock:
                for wdb in thread_dbs.values():
                    wdb.close()

        return out

    def get_lineage_by_canva_id(self, template_id):
        """通过 Canva 模板ID 查询使用的所有素材及其完整溯源链"""
        if not self.conn:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canva_templates WHERE template_id = %s",
                (template_id.strip(),)
            )
            tmpl = cur.fetchone()
        if not tmpl:
            return None
        try:
            phash_list = json.loads(tmpl['asset_phashes']) if tmpl['asset_phashes'] else []
        except:
            phash_list = []
        assets = self._build_canva_assets_lineage(phash_list)
        return {"template": tmpl, "assets": assets}

    def _get_compose_tree(self, phash, visited=None, depth=0, max_depth=6):
        """递归获取封装组件树：此 phash 由哪些组件构成（含组件自身的衍生祖先及子组件）"""
        if visited is None:
            visited = set()
        if phash in visited or depth >= max_depth:
            return []
        visited.add(phash)
        rows = []
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT c.part_phash, c.part_role, c.part_order,
                           a.filename, a.producer, a.created_at, a.asset_type
                    FROM rel_compose c JOIN assets a ON c.part_phash = a.phash
                    WHERE c.product_phash = %s ORDER BY c.part_order
                """, (phash,))
                rows = list(cur.fetchall())
        except:
            return []
        for row in rows:
            row['ancestors'] = self._get_derive_chain_up(row['part_phash'])
            row['sub_parts'] = self._get_compose_tree(row['part_phash'], visited.copy(), depth + 1)
        return rows

    def get_ancestry_string(self, phash, visited=None, depth=0, max_depth=8):
        """
        从 phash 向上递归查询所有祖先，返回完整链式字符串用于写入文件元数据。
        衍生关系：ph(制作人)>parent(制作人)>grandparent(制作人)
        封装关系：ph(制作人)>[part1chain,part2chain]
        无祖先时只返回 ph(制作人) 本身。
        """
        if visited is None:
            visited = set()
        if not self.conn or not phash or phash in visited or depth >= max_depth:
            return phash or ""
        visited.add(phash)
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT producer FROM assets WHERE phash=%s", (phash,))
                row = cur.fetchone()
            name = row['producer'] if row else ''
            me = f"{phash}({name})" if name else phash
            # 1. 首先查衍生父级
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT src_phash FROM rel_derive WHERE dst_phash=%s LIMIT 1",
                    (phash,)
                )
                derive_row = cur.fetchone()
            if derive_row:
                parent_str = self.get_ancestry_string(
                    derive_row['src_phash'], visited.copy(), depth + 1, max_depth
                )
                return f"{me}>{parent_str}" if parent_str else me
            # 2. 无衍生父级 → 查封装组件
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT part_phash FROM rel_compose WHERE product_phash=%s ORDER BY part_order",
                    (phash,)
                )
                parts = cur.fetchall()
            if parts:
                part_strs = []
                for p in parts:
                    ps = self.get_ancestry_string(p['part_phash'], visited.copy(), depth + 1, max_depth)
                    if ps:
                        part_strs.append(ps)
                if part_strs:
                    return f"{me}>[{','.join(part_strs)}]"
            return me
        except:
            return phash or ""

    def get_all_assets(self, limit=200):
        if not self.conn:
            return []
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT phash, filename, asset_type, file_size, producer, created_at
                FROM assets ORDER BY created_at DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()

    def get_all_canva(self):
        if not self.conn:
            return []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM canva_templates ORDER BY created_at DESC"
            )
            return cur.fetchall()

    def get_all_phashes(self):
        """返回数据库中所有已有 phash 的集合（用于批量扫描快速去重，避免逐条查询）"""
        if not self.conn:
            return set()
        if self._phash_cache is not None:
            return set(self._phash_cache)
        with self.conn.cursor() as cur:
            cur.execute("SELECT phash FROM assets")
            self._phash_cache = {r['phash'] for r in cur.fetchall()}
            return set(self._phash_cache)

    # ── 人员代码 CRUD ────────────────────────────────────────────────────────
    def get_producer_codes(self) -> dict:
        """返回 {code: name} 字典"""
        if not self.conn:
            return {}
        if self._producer_codes_cache is not None:
            return dict(self._producer_codes_cache)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, name FROM producer_codes ORDER BY code")
            self._producer_codes_cache = {r['code']: r['name'] for r in cur.fetchall()}
            return dict(self._producer_codes_cache)

    def upsert_producer_code(self, code: str, name: str):
        if not self.conn:
            return
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO producer_codes (code, name) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE name = VALUES(name)
            """, (code.upper().strip(), name.strip()))
        self.conn.commit()
        if self._producer_codes_cache is not None:
            self._producer_codes_cache[code.upper().strip()] = name.strip()

    def delete_producer_code(self, code: str):
        if not self.conn:
            return
        code = code.upper().strip()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM producer_codes WHERE code = %s",
                        (code,))
        self.conn.commit()
        if self._producer_codes_cache is not None and code in self._producer_codes_cache:
            self._producer_codes_cache.pop(code, None)
