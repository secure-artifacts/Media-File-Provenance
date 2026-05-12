# mam_db.py — 数据库管理（已迁移为 REST API 客户端）
import os
import sys
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import base64
import requests

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

def _hamming(h1, h2):
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except:
        return 64

class DBManager:
    def __init__(self):
        self.conn = None
        self.conf = self._load_conf()
        self._phash_cache = None
        self._producer_codes_cache = None
        self.token = None
        self._session = requests.Session()

    def _load_conf(self):
        for p in (DB_CONFIG_FILE, LEGACY_DB_CONFIG_FILE):
            if os.path.exists(p):
                try:
                    return json.load(open(p, 'r', encoding='utf-8'))
                except:
                    pass
        return {"host": "https://api.mediahashdezd.online", "user": "admin", "password": ""}

    def save_conf(self, conf):
        self.conf = conf
        os.makedirs(os.path.dirname(DB_CONFIG_FILE), exist_ok=True)
        json.dump(conf, open(DB_CONFIG_FILE, 'w', encoding='utf-8'), indent=2)

    @property
    def base_url(self):
        return self.conf.get("host", "https://api.mediahashdezd.online").rstrip('/')

    def connect(self, init_tables=True, warm_cache=True):
        url = f"{self.base_url}/auth/login"
        payload = {
            "username": self.conf.get("user", ""),
            "password": self.conf.get("password", "")
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                data = r.json()
                self.token = data.get("access_token")
                self._session.headers.update({"Authorization": f"Bearer {self.token}"})
                self.conn = True
                if warm_cache:
                    self._refresh_cache()
                return True, "登录成功"
            else:
                return False, f"登录失败: {r.status_code} {r.text}"
        except Exception as e:
            return False, f"连接异常: {str(e)}"

    def close(self):
        self.conn = None
        self.token = None
        self._session.headers.pop("Authorization", None)

    def _init_tables(self):
        pass

    def _dict_to_str_meta(self, item):
        if item and isinstance(item.get("metadata_json"), dict):
            item["metadata_json"] = json.dumps(item["metadata_json"], ensure_ascii=False)
        return item

    def upsert_asset(self, phash, filename, asset_type, file_size,
                     producer, created_at, metadata_json, thumbnail=None):
        if not self.conn: return False
        url = f"{self.base_url}/assets"
        payload = {
            "phash": phash,
            "filename": filename,
            "asset_type": asset_type,
            "file_size": file_size,
            "producer": producer,
            "created_at": created_at.isoformat() if isinstance(created_at, datetime) else created_at,
            "metadata_json": json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json,
        }
        if thumbnail:
            payload["thumbnail_base64"] = base64.b64encode(thumbnail).decode('utf-8')
        try:
            r = self._session.post(url, json=payload, timeout=15)
            r.raise_for_status()
            if self._phash_cache is not None and phash:
                self._phash_cache.add(phash)
            return True
        except:
            return False

    def upsert_assets_bulk(self, rows):
        if not self.conn or not rows: return 0
        success = 0
        for r in rows:
            phash, fname, atype, fsize, producer, created_at, metadata_json, thumb = r
            if self.upsert_asset(phash, fname, atype, fsize, producer, created_at, metadata_json, thumb):
                success += 1
        return success

    def fill_asset_producer_if_missing(self, phash, producer):
        if not self.conn or not phash or not producer: return False
        try:
            r = self._session.get(f"{self.base_url}/assets/{phash}")
            if r.status_code != 200: return False
            asset = r.json()
            if asset.get("producer") and str(asset.get("producer")).strip():
                return False
            asset["producer"] = producer
            if asset.get("metadata_json") and isinstance(asset["metadata_json"], dict):
                asset["metadata_json"]["producer"] = producer
            self._session.post(f"{self.base_url}/assets", json=asset)
            return True
        except:
            return False

    def add_derive(self, src, dst, rel_type, operator, remark=""):
        if not self.conn: return
        payload = {
            "src_phash": src, "dst_phash": dst, "rel_type": rel_type,
            "operator": operator, "created_at": datetime.now().isoformat(), "remark": remark
        }
        try: self._session.post(f"{self.base_url}/rel-derive", json=payload)
        except: pass

    def add_compose(self, part_phashes, product_phash, roles=None):
        if not self.conn or not part_phashes: return
        now = datetime.now().isoformat()
        for i, ph in enumerate(part_phashes):
            role = roles[i] if roles and i < len(roles) else "component"
            payload = {
                "part_phash": ph, "product_phash": product_phash,
                "part_order": i, "part_role": role, "created_at": now
            }
            try: self._session.post(f"{self.base_url}/rel-compose", json=payload)
            except: pass

    def _refresh_cache(self):
        if not self.conn: return
        try:
            self._producer_codes_cache = self.get_producer_codes()
        except:
            self._producer_codes_cache = {}

    def add_canva_template(self, template_id, name, creator, phash_list, remark=""):
        if not self.conn: return
        payload = {
            "template_id": template_id, "template_name": name, "creator": creator,
            "created_at": datetime.now().isoformat(),
            "asset_phashes": json.dumps(phash_list, ensure_ascii=False), "remark": remark
        }
        try: self._session.post(f"{self.base_url}/templates", json=payload)
        except: pass

    def lookup(self, phash, threshold=12):
        if not self.conn or not phash: return None
        try:
            payload = {"phash": phash, "max_distance": threshold, "limit": 1, "include_thumbnail": False}
            r = self._session.post(f"{self.base_url}/assets/lookup", json=payload)
            if r.status_code == 200:
                data = r.json()
                if data.get("items"):
                    best = data["items"][0]
                    target_phash = best["phash"]
                    d = best["distance"]
                    ar = self._session.get(f"{self.base_url}/assets/{target_phash}")
                    if ar.status_code == 200:
                        full_asset = ar.json()
                        full_asset['distance'] = d
                        full_asset['similarity'] = f"{int((1 - d/64)*100)}%"
                        return self._dict_to_str_meta(full_asset)
            return None
        except: return None

    def get_assets_by_phashes(self, phashes):
        if not self.conn: return {}
        clean = [p for p in (phashes or []) if p]
        if not clean: return {}
        uniq = list(dict.fromkeys(clean))
        try:
            r = self._session.post(f"{self.base_url}/assets/bulk-get", json={"phashes": uniq})
            if r.status_code == 200:
                out = {}
                data = r.json()
                items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if isinstance(data, dict) and not "items" in data:
                    items = list(data.values())
                for item in items:
                    item['distance'] = 0
                    item['similarity'] = "100%"
                    out[item['phash']] = self._dict_to_str_meta(item)
                return out
        except: pass
        return {}

    def _fetch_canva_templates(self):
        if not self.conn: return []
        try:
            r = self._session.get(f"{self.base_url}/templates", params={"limit": 500})
            if r.status_code == 200:
                data = r.json()
                return data.get("items", []) if isinstance(data, dict) else data
        except: pass
        return []

    def _prepare_canva_templates(self, templates):
        prepared = []
        for tmpl in templates or []:
            t = dict(tmpl)
            try:
                phashes = json.loads(t['asset_phashes']) if t.get('asset_phashes') else []
            except: phashes = []
            if not isinstance(phashes, list): phashes = []
            t['_asset_phashes'] = phashes
            t['_asset_phash_set'] = set(phashes)
            prepared.append(t)
        return prepared

    def _get_cached_derive_up(self, phash, local_cache=None):
        if not phash: return []
        if local_cache is None: return self._get_derive_chain_up(phash)
        cache = local_cache.setdefault('derive_up', {})
        if phash not in cache: cache[phash] = self._get_derive_chain_up(phash)
        return cache[phash]

    def _get_cached_derive_down(self, phash, local_cache=None):
        if not phash: return []
        if local_cache is None: return self._get_derive_chain_down(phash)
        cache = local_cache.setdefault('derive_down', {})
        if phash not in cache: cache[phash] = self._get_derive_chain_down(phash)
        return cache[phash]

    def _get_cached_compose(self, phash, local_cache=None):
        if not phash: return []
        if local_cache is None: return self._get_compose_tree(phash)
        cache = local_cache.setdefault('compose', {})
        if phash not in cache: cache[phash] = self._get_compose_tree(phash, local_cache=local_cache)
        return cache[phash]

    def _get_derive_chain_up(self, phash, visited=None, depth=0, max_depth=8):
        if visited is None: visited = set()
        if phash in visited or depth >= max_depth: return []
        visited.add(phash)
        rows = []
        try:
            r = self._session.get(f"{self.base_url}/rel-derive", params={"dst_phash": phash, "limit": 100})
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                for rel in items:
                    src = rel.get("src_phash")
                    ar = self._session.get(f"{self.base_url}/assets/{src}")
                    if ar.status_code == 200:
                        a = ar.json()
                        row = {
                            "src_phash": src, "rel_type": rel.get("rel_type"), "operator": rel.get("operator"),
                            "filename": a.get("filename"), "producer": a.get("producer"),
                            "created_at": a.get("created_at"), "asset_type": a.get("asset_type")
                        }
                        row['ancestors'] = self._get_derive_chain_up(src, visited, depth + 1, max_depth)
                        rows.append(row)
        except: pass
        return rows

    def _get_derive_chain_down(self, phash, visited=None, depth=0, max_depth=8):
        if visited is None: visited = set()
        if phash in visited or depth >= max_depth: return []
        visited.add(phash)
        rows = []
        try:
            r = self._session.get(f"{self.base_url}/rel-derive", params={"src_phash": phash, "limit": 100})
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                for rel in items:
                    dst = rel.get("dst_phash")
                    ar = self._session.get(f"{self.base_url}/assets/{dst}")
                    if ar.status_code == 200:
                        a = ar.json()
                        row = {
                            "dst_phash": dst, "rel_type": rel.get("rel_type"), "operator": rel.get("operator"),
                            "filename": a.get("filename"), "producer": a.get("producer"),
                            "created_at": a.get("created_at"), "asset_type": a.get("asset_type")
                        }
                        row['descendants'] = self._get_derive_chain_down(dst, visited, depth + 1, max_depth)
                        rows.append(row)
        except: pass
        return rows

    def _get_compose_tree(self, phash, visited=None, depth=0, max_depth=6, local_cache=None):
        if visited is None: visited = set()
        if phash in visited or depth >= max_depth: return []
        visited.add(phash)
        rows = []
        try:
            r = self._session.get(f"{self.base_url}/rel-compose", params={"product_phash": phash, "limit": 100})
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                for rel in sorted(items, key=lambda x: x.get('part_order', 0)):
                    part = rel.get("part_phash")
                    ar = self._session.get(f"{self.base_url}/assets/{part}")
                    if ar.status_code == 200:
                        a = ar.json()
                        row = {
                            "part_phash": part, "part_role": rel.get("part_role"), "part_order": rel.get("part_order"),
                            "filename": a.get("filename"), "producer": a.get("producer"),
                            "created_at": a.get("created_at"), "asset_type": a.get("asset_type")
                        }
                        row['ancestors'] = self._get_cached_derive_up(part, local_cache)
                        row['sub_parts'] = self._get_compose_tree(part, visited.copy(), depth + 1, max_depth, local_cache)
                        rows.append(row)
        except: pass
        return rows

    def _collect_derive_src_phashes(self, rows, out_set: set):
        for row in rows or []:
            ph = row.get('src_phash')
            if ph: out_set.add(ph)
            self._collect_derive_src_phashes(row.get('ancestors', []), out_set)

    def _collect_compose_part_phashes(self, rows, out_set: set):
        for row in rows or []:
            ph = row.get('part_phash')
            if ph: out_set.add(ph)
            self._collect_derive_src_phashes(row.get('ancestors', []), out_set)
            self._collect_compose_part_phashes(row.get('sub_parts', []), out_set)

    def _build_canva_assets_lineage(self, phash_list, local_cache=None, template_assets_cache=None, template_cache_lock=None):
        if not self.conn: return []
        cache_key = tuple(phash_list or [])
        if template_assets_cache is not None:
            if template_cache_lock is not None:
                with template_cache_lock: cached = template_assets_cache.get(cache_key)
            else:
                cached = template_assets_cache.get(cache_key)
            if cached is not None: return cached

        base_map = self.get_assets_by_phashes(phash_list)
        assets = []
        for ph in phash_list or []:
            asset = base_map.get(ph)
            if not asset: continue
            node = dict(asset)
            node['ancestors'] = self._get_cached_derive_up(ph, local_cache)
            node['composed_from'] = self._get_cached_compose(ph, local_cache)
            assets.append(node)
            
        if template_assets_cache is not None:
            if template_cache_lock is not None:
                with template_cache_lock: template_assets_cache[cache_key] = assets
            else:
                template_assets_cache[cache_key] = assets
        return assets

    def _build_lineage_from_base(self, base, canva_templates=None, local_cache=None, canva_assets_cache=None, canva_cache_lock=None):
        exact = base['phash']
        result = {
            "asset": base, "derived_from": [], "derived_to": [], "composed_from":[], "used_in": [], "canva_used": []
        }
        result["derived_from"] = self._get_cached_derive_up(exact, local_cache)
        
        try:
            r = self._session.get(f"{self.base_url}/rel-derive", params={"src_phash": exact, "limit": 100})
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                for rel in items:
                    dst = rel.get("dst_phash")
                    ar = self._session.get(f"{self.base_url}/assets/{dst}")
                    if ar.status_code == 200:
                        a = ar.json()
                        row = {
                            "dst_phash": dst, "rel_type": rel.get("rel_type"), "operator": rel.get("operator"),
                            "filename": a.get("filename"), "producer": a.get("producer"),
                            "created_at": a.get("created_at"), "asset_type": a.get("asset_type")
                        }
                        row['descendants'] = self._get_cached_derive_down(dst, local_cache)
                        result["derived_to"].append(row)
        except: pass

        result["composed_from"] = self._get_cached_compose(exact, local_cache)

        canva_scope = {exact}
        self._collect_derive_src_phashes(result["derived_from"], canva_scope)
        self._collect_compose_part_phashes(result["composed_from"], canva_scope)

        try:
            r = self._session.get(f"{self.base_url}/rel-compose", params={"part_phash": exact, "limit": 100})
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                for rel in items:
                    prod = rel.get("product_phash")
                    ar = self._session.get(f"{self.base_url}/assets/{prod}")
                    if ar.status_code == 200:
                        a = ar.json()
                        result["used_in"].append({
                            "product_phash": prod, "part_role": rel.get("part_role"),
                            "filename": a.get("filename"), "producer": a.get("producer"),
                            "created_at": a.get("created_at"), "asset_type": a.get("asset_type")
                        })
        except: pass

        tmpls = canva_templates if canva_templates is not None else self._prepare_canva_templates(self._fetch_canva_templates())
        for tmpl in tmpls:
            phashes = tmpl.get('_asset_phashes', [])
            phash_set = tmpl.get('_asset_phash_set', set())
            if exact in phash_set:
                mode = 'direct'
                matched = [exact]
            else:
                mode = 'upstream'
                matched = [ph for ph in phashes if ph in canva_scope]
            if not matched: continue
            t = {k: v for k, v in tmpl.items() if not str(k).startswith('_')}
            t['match_mode'] = mode
            t['matched_phashes'] = matched
            t['matched_count'] = len(matched)
            t['assets'] = self._build_canva_assets_lineage(
                phashes, local_cache=local_cache,
                template_assets_cache=canva_assets_cache,
                template_cache_lock=canva_cache_lock
            )
            result["canva_used"].append(t)
        return result

    def get_lineage(self, phash, exact_only=False):
        if not self.conn: return None
        if exact_only:
            try:
                r = self._session.get(f"{self.base_url}/assets/{phash}")
                if r.status_code == 200:
                    base = self._dict_to_str_meta(r.json())
                    base['distance'] = 0
                    base['similarity'] = "100%"
                else: base = None
            except: base = None
        else:
            base = self.lookup(phash)
        if not base: return None
        templates = self._prepare_canva_templates(self._fetch_canva_templates())
        local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
        return self._build_lineage_from_base(base, templates, local_cache=local_cache)

    def get_lineage_batch(self, phashes, exact_only=True, workers=4):
        if not self.conn: return {}
        ordered = [p for p in (phashes or []) if p]
        if not ordered: return {}
        uniq = list(dict.fromkeys(ordered))
        base_map = self.get_assets_by_phashes(uniq)
        if not exact_only:
            for ph in uniq:
                if ph not in base_map:
                    b = self.lookup(ph)
                    if b: base_map[ph] = b
        canva_templates = self._prepare_canva_templates(self._fetch_canva_templates())
        out = {}
        canva_assets_cache = {}
        # 为避免 requests session 在多线程下的小概率问题，使用单线程按顺序处理，因为内部已有各种缓存优化。
        # 原版这里有 ThreadPoolExecutor，但 API 请求复用 session 可能会有影响，保守采用单线程。
        local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
        for ph in uniq:
            base = base_map.get(ph)
            if base:
                out[ph] = self._build_lineage_from_base(
                    dict(base), canva_templates,
                    local_cache=local_cache,
                    canva_assets_cache=canva_assets_cache
                )
            else:
                out[ph] = None
        return out

    def get_lineage_by_canva_id(self, template_id):
        if not self.conn: return None
        try:
            r = self._session.get(f"{self.base_url}/templates/{template_id}")
            if r.status_code != 200: return None
            tmpl = r.json()
            phash_list = json.loads(tmpl.get('asset_phashes', '[]'))
            local_cache = {'derive_up': {}, 'derive_down': {}, 'compose': {}}
            assets = self._build_canva_assets_lineage(phash_list, local_cache=local_cache)
            return {"template": tmpl, "assets": assets}
        except: return None

    def get_canva_template_assets_basic(self, template_id):
        if not self.conn: return None
        try:
            r = self._session.get(f"{self.base_url}/templates/{template_id}")
            if r.status_code != 200: return None
            tmpl = r.json()
            phash_list = json.loads(tmpl.get('asset_phashes', '[]'))
            base_map = self.get_assets_by_phashes(phash_list)
            assets = []
            for ph in phash_list:
                a = base_map.get(ph)
                if not a: continue
                assets.append({
                    'phash': a.get('phash'), 'filename': a.get('filename'),
                    'producer': a.get('producer'), 'asset_type': a.get('asset_type'),
                    'created_at': a.get('created_at'),
                })
            return {"template": tmpl, "assets": assets}
        except: return None

    def get_ancestry_string(self, phash, visited=None, depth=0, max_depth=8):
        if visited is None: visited = set()
        if not self.conn or not phash or phash in visited or depth >= max_depth: return phash or ""
        visited.add(phash)
        try:
            r = self._session.get(f"{self.base_url}/assets/{phash}")
            if r.status_code != 200: return phash or ""
            row = r.json()
            name = row.get('producer', '')
            me = f"{phash}({name})" if name else phash
            
            rd = self._session.get(f"{self.base_url}/rel-derive", params={"dst_phash": phash, "limit": 1})
            if rd.status_code == 200:
                items = rd.json().get("items", []) if isinstance(rd.json(), dict) else rd.json()
                if items:
                    src = items[0].get('src_phash')
                    parent_str = self.get_ancestry_string(src, visited.copy(), depth + 1, max_depth)
                    return f"{me}>{parent_str}" if parent_str else me
            
            rc = self._session.get(f"{self.base_url}/rel-compose", params={"product_phash": phash, "limit": 10})
            if rc.status_code == 200:
                parts = rc.json().get("items", []) if isinstance(rc.json(), dict) else rc.json()
                if parts:
                    part_strs = []
                    for p in parts:
                        ps = self.get_ancestry_string(p.get('part_phash'), visited.copy(), depth + 1, max_depth)
                        if ps: part_strs.append(ps)
                    if part_strs:
                        return f"{me}>[{','.join(part_strs)}]"
            return me
        except: return phash or ""

    def get_all_assets(self, limit=200):
        if not self.conn: return []
        try:
            r = self._session.get(f"{self.base_url}/assets", params={"limit": limit})
            if r.status_code == 200:
                data = r.json()
                return data.get("items", []) if isinstance(data, dict) else data
        except: pass
        return []

    def fix_wrong_producer(self, old_producer, new_producer, filename_keyword="", start_date=None, end_date=None):
        if not self.conn: return 0
        try:
            r = self._session.get(f"{self.base_url}/assets", params={"producer": old_producer, "limit": 500})
            if r.status_code != 200: return 0
            items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
            affected = 0
            for item in items:
                if filename_keyword and filename_keyword not in item.get('filename', ''): continue
                if start_date and str(item.get('created_at'))[:10] < start_date: continue
                if end_date and str(item.get('created_at'))[:10] > end_date: continue
                
                item["producer"] = new_producer
                if item.get("metadata_json") and isinstance(item["metadata_json"], dict):
                    item["metadata_json"]["producer"] = new_producer
                self._session.post(f"{self.base_url}/assets", json=item)
                affected += 1
            return affected
        except: return 0

    def get_all_canva(self):
        return self._fetch_canva_templates()

    def get_all_phashes(self):
        if not self.conn: return set()
        if self._phash_cache is not None: return set(self._phash_cache)
        try:
            self._phash_cache = set()
            offset = 0
            while True:
                r = self._session.get(f"{self.base_url}/assets", params={"limit": 500, "offset": offset})
                if r.status_code != 200: break
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                if not items: break
                for item in items:
                    self._phash_cache.add(item['phash'])
                if len(items) < 500: break
                offset += 500
            return set(self._phash_cache)
        except: return set()

    def get_producer_codes(self) -> dict:
        if not self.conn: return {}
        if self._producer_codes_cache is not None: return dict(self._producer_codes_cache)
        try:
            r = self._session.get(f"{self.base_url}/producer-codes")
            if r.status_code == 200:
                items = r.json().get("items", []) if isinstance(r.json(), dict) else r.json()
                self._producer_codes_cache = {item['code']: item['name'] for item in items}
                return dict(self._producer_codes_cache)
        except: pass
        return {}

    def upsert_producer_code(self, code: str, name: str):
        if not self.conn: return
        try:
            self._session.post(f"{self.base_url}/producer-codes", json={"code": code.upper().strip(), "name": name.strip()})
            if self._producer_codes_cache is not None:
                self._producer_codes_cache[code.upper().strip()] = name.strip()
        except: pass

    def delete_producer_code(self, code: str):
        if not self.conn: return
        code = code.upper().strip()
        try:
            self._session.delete(f"{self.base_url}/producer-codes/{code}")
            if self._producer_codes_cache is not None and code in self._producer_codes_cache:
                self._producer_codes_cache.pop(code, None)
        except: pass
