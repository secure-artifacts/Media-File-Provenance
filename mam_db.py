# mam_db.py — 数据库管理（MySQL）
# 列名使用 metadata_json 兼容旧表；关系表不使用外键，避免 charset 不兼容
import os
import json
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
            return True, "连接成功"
        except Exception as e:
            return False, str(e)

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

            # ── 统一关系表 collation，消除 JOIN 时「Illegal mix of collations」错误 ──
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
        return True

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
        """模糊查询：汉明距离 ≤ threshold 时返回最相近记录"""
        if not self.conn or not phash:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT phash,filename,asset_type,file_size,producer,"
                "created_at,metadata_json FROM assets"
            )
            rows = cur.fetchall()
        best, best_d = None, 64
        for r in rows:
            d = _hamming(phash, r['phash'])
            if d < best_d:
                best_d = d
                best = r
        if best and best_d <= threshold:
            best['distance']   = best_d
            best['similarity'] = f"{int((1 - best_d/64)*100)}%"
            return best
        return None

    # ── 递归溯源链 ──────────────────────────────────────
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
            row['ancestors'] = self._get_derive_chain_up(
                row['src_phash'], visited.copy(), depth + 1, max_depth
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
            row['descendants'] = self._get_derive_chain_down(
                row['dst_phash'], visited.copy(), depth + 1, max_depth
            )
        return rows

    def get_lineage(self, phash):
        """返回完整溯源树 dict（含多级递归衍生链 + Canva 模板引用）"""
        if not self.conn:
            return None
        base = self.lookup(phash)
        if not base:
            return None
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

        with self.conn.cursor() as cur:
            # ── 被哪些成品使用 ────────────────────────────
            cur.execute("""
                SELECT c.product_phash, c.part_role,
                       a.filename, a.producer, a.created_at, a.asset_type
                FROM rel_compose c JOIN assets a ON c.product_phash = a.phash
                WHERE c.part_phash = %s
            """, (exact,))
            result["used_in"] = list(cur.fetchall())

            # ── 出现在哪些 Canva 模板 ─────────────────────
            cur.execute(
                "SELECT template_id, template_name, creator, created_at, asset_phashes "
                "FROM canva_templates"
            )
            for tmpl in cur.fetchall():
                try:
                    phashes = json.loads(tmpl['asset_phashes']) if tmpl['asset_phashes'] else []
                except:
                    phashes = []
                if exact in phashes:
                    result["canva_used"].append(tmpl)
        return result

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
        assets = []
        for ph in phash_list:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT phash, filename, asset_type, file_size, producer, created_at "
                    "FROM assets WHERE phash = %s", (ph,)
                )
                asset = cur.fetchone()
            if asset:
                asset['ancestors'] = self._get_derive_chain_up(ph)
                assets.append(asset)
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
