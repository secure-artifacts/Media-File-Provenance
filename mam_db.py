# mam_db.py — 数据库管理（MySQL）
# 列名使用 metadata_json 兼容旧表；关系表不使用外键，避免 charset 不兼容
import os
import sys
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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


LEGACY_DB_CONFIG_FILE = "mam_db_config.json"
DB_CONFIG_FILE = os.path.join(_app_data_dir(), LEGACY_DB_CONFIG_FILE)

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
        for p in (DB_CONFIG_FILE, LEGACY_DB_CONFIG_FILE):
            if os.path.exists(p):
                try:
                    return json.load(open(p, 'r', encoding='utf-8'))
                except:
                    pass
        return {"host": "localhost", "user": "root",
                "password": "", "db": "mam_system", "port": 3306}

    def save_conf(self, conf):
        self.conf = conf
        os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
        json.dump(conf, open(DB_CONFIG_FILE, 'w', encoding='utf-8'), indent=2)

    def connect(self, init_tables=True, warm_cache=True):
        if not MYSQL_OK:
            return False, "未安装 pymysql，请运行: pip install pymysql"
        try:
            self.conn = pymysql.connect(
                host=self.conf['host'], user=self.conf['user'],
                password=self.conf['password'], database=self.conf['db'],
                port=int(self.conf['port']), charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor, autocommit=True
            )
            if init_tables:
                self._init_tables()
            # 仅在需要时预热缓存，避免轻量查询连接触发全量扫描
            if warm_cache:
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

    def fill_asset_producer_if_missing(self, phash, producer):
        """仅在素材作者为空时回填作者，并同步 metadata_json 中的 producer。"""
        if not self.conn or not phash or not producer:
            return False

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT producer, metadata_json FROM assets WHERE phash = %s",
                (phash,)
            )
            row = cur.fetchone()
            if not row:
                return False

            current = (row.get('producer') or '').strip()
            if current:
                return False

            params = [producer]
            sql = "UPDATE assets SET producer = %s"

            raw_meta = row.get('metadata_json')
            if raw_meta:
                try:
                    meta = json.loads(raw_meta)
                except:
                    meta = None
                if isinstance(meta, dict):
                    meta['producer'] = producer
                    sql += ", metadata_json = %s"
                    params.append(json.dumps(meta, ensure_ascii=False, default=str))

            sql += " WHERE phash = %s AND (producer IS NULL OR TRIM(producer) = '')"
            params.append(phash)
            cur.execute(sql, tuple(params))
            return bool(cur.rowcount)

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
        if not part_phashes:
            return
        now = datetime.now()
        rows = []
        for i, ph in enumerate(part_phashes):
            role = roles[i] if roles and i < len(roles) else "component"
            rows.append((ph, product_phash, i, role, now))
        with self.conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO rel_compose
                    (part_phash, product_phash, part_order, part_role, created_at)
                VALUES (%s, %s, %s, %s, %s)
            """, rows)
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

    def _get_cached_derive_up(self, phash, local_cache=None):
        """读取并缓存某个 phash 的向上衍生链。"""
        if not phash:
            return []
        if local_cache is None:
            return self._get_derive_chain_up(phash)
        cache = local_cache.setdefault('derive_up', {})
        if phash not in cache:
            cache[phash] = self._get_derive_chain_up(phash)
        return cache[phash]

    def _get_cached_derive_down(self, phash, local_cache=None):
        """读取并缓存某个 phash 的向下衍生链。"""
        if not phash:
            return []
        if local_cache is None:
            return self._get_derive_chain_down(phash)
        cache = local_cache.setdefault('derive_down', {})
        if phash not in cache:
            cache[phash] = self._get_derive_chain_down(phash)
        return cache[phash]

    def _get_cached_compose(self, phash, local_cache=None):
        """读取并缓存某个 phash 的封装组件树。"""
        if not phash:
            return []
        if local_cache is None:
            return self._get_compose_tree(phash)
        cache = local_cache.setdefault('compose', {})
        if phash not in cache:
            cache[phash] = self._get_compose_tree(phash, local_cache=local_cache)
        return cache[phash]

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

    def _build_canva_assets_lineage(self, phash_list, local_cache=None,
                                    template_assets_cache=None, template_cache_lock=None):
        """构建 Canva 模板素材列表，并附带每个素材的衍生/封装溯源。"""
        if not self.conn:
            return []

        cache_key = tuple(phash_list or [])
        if template_assets_cache is not None:
            if template_cache_lock is not None:
                with template_cache_lock:
                    cached = template_assets_cache.get(cache_key)
            else:
                cached = template_assets_cache.get(cache_key)
            if cached is not None:
                return cached

        base_map = self.get_assets_by_phashes(phash_list)
        assets = []
        for ph in phash_list or []:
            asset = base_map.get(ph)
            if not asset:
                continue
            node = dict(asset)
            node['ancestors'] = self._get_cached_derive_up(ph, local_cache)
            node['composed_from'] = self._get_cached_compose(ph, local_cache)
            assets.append(node)

        if template_assets_cache is not None:
            if template_cache_lock is not None:
                with template_cache_lock:
                    template_assets_cache[cache_key] = assets
            else:
                template_assets_cache[cache_key] = assets

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

    def _prepare_canva_templates(self, templates):
        """预解析 Canva 模板中的素材列表，避免重复 JSON 反序列化。"""
        prepared = []
        for tmpl in templates or []:
            t = dict(tmpl)
            try:
                phashes = json.loads(t['asset_phashes']) if t.get('asset_phashes') else []
            except:
                phashes = []
            if not isinstance(phashes, list):
                phashes = []
            t['_asset_phashes'] = phashes
            t['_asset_phash_set'] = set(phashes)
            prepared.append(t)
        return prepared

    def _build_lineage_from_base(self, base, canva_templates=None, local_cache=None,
                                 canva_assets_cache=None, canva_cache_lock=None):
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
                row['ancestors'] = self._get_cached_derive_up(row['src_phash'], local_cache)
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
                row['descendants'] = self._get_cached_derive_down(row['dst_phash'], local_cache)
            result["derived_to"] = rows

        result["composed_from"] = self._get_cached_compose(exact, local_cache)

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

        tmpls = canva_templates if canva_templates is not None else self._prepare_canva_templates(self._fetch_canva_templates())
        for tmpl in tmpls:
            phashes = tmpl.get('_asset_phashes')
            if phashes is None:
                try:
                    phashes = json.loads(tmpl['asset_phashes']) if tmpl.get('asset_phashes') else []
                except:
                    phashes = []
            phash_set = tmpl.get('_asset_phash_set')
            if phash_set is None:
                phash_set = set(phashes)
            if exact in phash_set:
                mode = 'direct'
                matched = [exact]
            else:
                mode = 'upstream'
                matched = [ph for ph in phashes if ph in canva_scope]

            if not matched:
                continue

            t = {k: v for k, v in tmpl.items() if not str(k).startswith('_')}
            t['match_mode'] = mode
            t['matched_phashes'] = matched
            t['matched_count'] = len(matched)
            t['assets'] = self._build_canva_assets_lineage(
                phashes,
                local_cache=local_cache,
                template_assets_cache=canva_assets_cache,
                template_cache_lock=canva_cache_lock,
            )
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
        templates = self._prepare_canva_templates(self._fetch_canva_templates())
        local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
        return self._build_lineage_from_base(base, templates, local_cache=local_cache)

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

        canva_templates = self._prepare_canva_templates(self._fetch_canva_templates())
        canva_assets_cache = {}
        # 小批量时使用单连接串行更快：避免多线程重复建连与缓存预热开销。
        if workers <= 1 or len(uniq) <= 20:
            out = {}
            local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
            for ph in uniq:
                base = base_map.get(ph)
                out[ph] = self._build_lineage_from_base(
                    dict(base),
                    canva_templates,
                    local_cache=local_cache,
                    canva_assets_cache=canva_assets_cache,
                ) if base else None
            return out

        lock = threading.Lock()
        canva_cache_lock = threading.Lock()
        thread_states = {}

        def get_thread_state():
            tid = threading.get_ident()
            with lock:
                existing = thread_states.get(tid)
                if existing:
                    return existing
                wdb = DBManager()
                wdb.conf = dict(self.conf)
                ok, msg = wdb.connect(init_tables=False, warm_cache=False)
                if not ok:
                    raise RuntimeError(msg)
                state = {
                    'db': wdb,
                    'local_cache': {'derive_up': {}, 'derive_down': {}, 'compose': {}},
                }
                thread_states[tid] = state
                return state

        def process_one(ph):
            base = base_map.get(ph)
            if not base:
                return ph, None
            state = get_thread_state()
            wdb = state['db']
            return ph, wdb._build_lineage_from_base(
                dict(base),
                canva_templates,
                local_cache=state['local_cache'],
                canva_assets_cache=canva_assets_cache,
                canva_cache_lock=canva_cache_lock,
            )

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
                for state in thread_states.values():
                    state['db'].close()

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
        local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
        assets = self._build_canva_assets_lineage(phash_list, local_cache=local_cache)
        return {"template": tmpl, "assets": assets}

    def get_canva_template_assets_basic(self, template_id):
        """轻量查询 Canva 模板及素材基础信息（不构建递归溯源树）。"""
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
            phash_list = json.loads(tmpl['asset_phashes']) if tmpl.get('asset_phashes') else []
        except:
            phash_list = []
        base_map = self.get_assets_by_phashes(phash_list)
        assets = []
        for ph in phash_list:
            a = base_map.get(ph)
            if not a:
                continue
            assets.append({
                'phash': a.get('phash'),
                'filename': a.get('filename'),
                'producer': a.get('producer'),
                'asset_type': a.get('asset_type'),
                'created_at': a.get('created_at'),
            })
        return {"template": tmpl, "assets": assets}

    def _get_compose_tree(self, phash, visited=None, depth=0, max_depth=6, local_cache=None):
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
            row['ancestors'] = self._get_cached_derive_up(row['part_phash'], local_cache)
            row['sub_parts'] = self._get_compose_tree(
                row['part_phash'],
                visited.copy(),
                depth + 1,
                max_depth,
                local_cache,
            )
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

    def fix_wrong_producer(self, old_producer, new_producer,
                           filename_keyword="", start_date=None, end_date=None):
        """按条件批量修正作者，并同步 metadata_json 中 producer 字段。"""
        if not self.conn:
            return 0

        old_name = (old_producer or "").strip()
        new_name = (new_producer or "").strip()
        if not old_name or not new_name or old_name == new_name:
            return 0

        where = ["producer = %s"]
        params = [old_name]

        keyword = (filename_keyword or "").strip()
        if keyword:
            where.append("filename LIKE %s")
            params.append(f"%{keyword}%")

        if start_date:
            where.append("created_at >= %s")
            params.append(f"{start_date} 00:00:00")

        if end_date:
            where.append("created_at <= %s")
            params.append(f"{end_date} 23:59:59")

        where_sql = " AND ".join(where)
        select_sql = f"SELECT phash, metadata_json FROM assets WHERE {where_sql}"
        update_sql = f"UPDATE assets SET producer = %s WHERE {where_sql}"

        with self.conn.cursor() as cur:
            cur.execute(select_sql, tuple(params))
            rows = list(cur.fetchall())
            cur.execute(update_sql, tuple([new_name] + params))
            affected = cur.rowcount or 0

        json_updates = []
        for row in rows:
            raw = row.get('metadata_json')
            if not raw:
                continue
            try:
                meta = json.loads(raw)
            except:
                continue
            if not isinstance(meta, dict):
                continue
            meta['producer'] = new_name
            json_updates.append((
                json.dumps(meta, ensure_ascii=False, default=str),
                row.get('phash')
            ))

        if json_updates:
            with self.conn.cursor() as cur:
                cur.executemany(
                    "UPDATE assets SET metadata_json = %s WHERE phash = %s",
                    json_updates
                )

        return affected

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
