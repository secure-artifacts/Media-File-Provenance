import os
import io
import time
import zipfile
import uuid
import re
import ssl
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import sys
import hashlib
import cv2
import imagehash
from PIL import Image

def get_dist_dir():
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, 'canvatools_dist')
    return os.path.abspath(os.path.join(os.path.dirname(__file__), 'canvatools_dist'))


import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app, render_errors=True, supports_credentials=True)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# 使用系统临时目录，适应 MacOS 的 App Bundle 沙盒和 Windows 的 UAC 环境
import tempfile
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'canva_tools_temp')
os.makedirs(TEMP_DIR, exist_ok=True)

file_store = {}
staged_store = {}   # { stagedId: { "fileName": str, "buffer": bytes } }
plugin_page_store = {}
pending_queue = []
executor = ThreadPoolExecutor(max_workers=4)

DOWNLOAD_HEADERS = {
    "User-Agent": "CanvaToolsStandalone/1.0",
    "Accept": "*/*",
    "Connection": "close",
}

SELF_CHECK_DEFAULT_URLS = [
    "https://www.canva.com",
    "https://www.canva.dev",
    "https://static.canva.com",
]
SELF_CHECK_MAX_URLS = 10

def guess_extension(content_type, fallback):
    if not content_type: return fallback
    normalized = content_type.split(";")[0].strip().lower()
    ext_map = {
        "video/mp4": ".mp4", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
        "application/pdf": ".pdf", "image/svg+xml": ".svg", 
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/zip": ".zip", "application/x-zip-compressed": ".zip",
    }
    return ext_map.get(normalized, fallback)

def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip()

def safe_url_for_log(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:48]}"
    except Exception:
        pass
    return (url or "")[:80]

def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except PermissionError:
        # Windows 下视频句柄可能晚一点释放，忽略清理失败不影响主流程
        pass

def _is_permission_related_error(err):
    text = repr(err).lower()
    return (
        "permission denied" in text
        or "winerror 10013" in text
        or "access permissions" in text
    )

def _download_via_requests(url, timeout, trust_env=True):
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    with requests.Session() as session:
        session.trust_env = trust_env
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        response = session.get(
            url,
            timeout=(10, timeout),
            allow_redirects=True,
            headers=DOWNLOAD_HEADERS,
        )
        response.raise_for_status()
        return response.content, {k.lower(): v for k, v in response.headers.items()}

def _download_via_urllib(url, timeout):
    req = urllib.request.Request(url, headers=DOWNLOAD_HEADERS, method="GET")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read(), {k.lower(): v for k, v in resp.headers.items()}

def download_bytes(url, timeout=30):
    first_error = None

    try:
        return _download_via_requests(url, timeout, trust_env=True)
    except Exception as e:
        first_error = e

    if _is_permission_related_error(first_error):
        try:
            print(
                f"[警告] requests 下载受限，尝试关闭系统代理重试: "
                f"{safe_url_for_log(url)}"
            )
            return _download_via_requests(url, timeout, trust_env=False)
        except Exception as e:
            first_error = e

    try:
        return _download_via_urllib(url, timeout)
    except Exception as e:
        raise RuntimeError(
            f"下载失败 url={safe_url_for_log(url)} | "
            f"requests={repr(first_error)} | urllib={repr(e)}"
        ) from e

def _normalize_self_check_urls(urls):
    normalized = []
    if isinstance(urls, list):
        for raw in urls:
            if not isinstance(raw, str):
                continue
            item = raw.strip()
            if not item:
                continue
            parsed = urllib.parse.urlsplit(item)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            normalized.append(item)
            if len(normalized) >= SELF_CHECK_MAX_URLS:
                break
    if normalized:
        return normalized
    return list(SELF_CHECK_DEFAULT_URLS)

def _probe_url_via_requests(url, timeout, trust_env=True):
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=0,
        backoff_factor=0.2,
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    headers = dict(DOWNLOAD_HEADERS)
    headers["Range"] = "bytes=0-0"
    with requests.Session() as session:
        session.trust_env = trust_env
        adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=5)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        resp = session.get(
            url,
            timeout=(5, timeout),
            allow_redirects=True,
            headers=headers,
            stream=True,
        )
        status = int(resp.status_code)
        final_url = safe_url_for_log(resp.url)
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
        content_length = resp.headers.get("content-length")
        resp.close()
        return {
            "ok": True,
            "method": "requests",
            "mode": "trust_env" if trust_env else "no_env_proxy",
            "status": status,
            "finalUrl": final_url,
            "contentType": content_type,
            "contentLength": content_length,
        }

def _probe_url_via_urllib(url, timeout):
    headers = dict(DOWNLOAD_HEADERS)
    headers["Range"] = "bytes=0-0"
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    with opener.open(req, timeout=timeout) as resp:
        status = int(getattr(resp, "status", resp.getcode()))
        final_url = safe_url_for_log(getattr(resp, "url", url) or url)
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip()
        content_length = resp.headers.get("content-length")
        return {
            "ok": True,
            "method": "urllib",
            "mode": "no_env_proxy",
            "status": status,
            "finalUrl": final_url,
            "contentType": content_type,
            "contentLength": content_length,
        }

def _probe_url_connectivity(url, timeout=8):
    started = time.time()
    first_error = None

    try:
        result = _probe_url_via_requests(url, timeout=timeout, trust_env=True)
        result["url"] = safe_url_for_log(url)
        result["elapsedMs"] = int((time.time() - started) * 1000)
        return result
    except Exception as e:
        first_error = e

    if _is_permission_related_error(first_error):
        try:
            result = _probe_url_via_requests(url, timeout=timeout, trust_env=False)
            result["url"] = safe_url_for_log(url)
            result["elapsedMs"] = int((time.time() - started) * 1000)
            return result
        except Exception as e:
            first_error = e

    try:
        result = _probe_url_via_urllib(url, timeout=timeout)
        result["url"] = safe_url_for_log(url)
        result["elapsedMs"] = int((time.time() - started) * 1000)
        return result
    except Exception as e:
        return {
            "ok": False,
            "url": safe_url_for_log(url),
            "method": "none",
            "mode": "none",
            "status": None,
            "finalUrl": safe_url_for_log(url),
            "error": f"requests={repr(first_error)} | urllib={repr(e)}",
            "elapsedMs": int((time.time() - started) * 1000),
        }

def run_network_self_check(urls=None, timeout=8):
    try:
        timeout = float(timeout)
    except Exception:
        timeout = 8
    timeout = min(max(timeout, 3), 30)

    check_urls = _normalize_self_check_urls(urls)
    details = [_probe_url_connectivity(url, timeout=timeout) for url in check_urls]
    ok_count = sum(1 for item in details if item.get("ok"))
    used_no_env_proxy = sum(1 for item in details if item.get("mode") == "no_env_proxy")

    return {
        "summary": {
            "total": len(details),
            "ok": ok_count,
            "failed": len(details) - ok_count,
            "timeoutSec": timeout,
            "proxyBypassUsed": used_no_env_proxy,
        },
        "details": details,
    }

@app.route('/health', methods=['GET'])
def health():
    print("[日志] 收到健康检查请求")
    return jsonify({"ok": True})

@app.route('/network-self-check', methods=['GET', 'POST'])
def network_self_check():
    data = request.json if request.method == 'POST' else {}
    data = data or {}

    urls = data.get("urls")
    if not urls and isinstance(data.get("assets"), list):
        urls = [
            item.get("url")
            for item in data.get("assets")
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]

    single_url = request.args.get("url")
    if single_url and isinstance(single_url, str):
        if isinstance(urls, list):
            urls = urls + [single_url]
        else:
            urls = [single_url]

    timeout = data.get("timeout", request.args.get("timeout", 8))
    result = run_network_self_check(urls=urls, timeout=timeout)
    return jsonify(result)

@app.route('/pre-stage-assets', methods=['POST'])
def pre_stage_assets():
    data = request.json or {}
    assets = data.get("assets", [])
    print(f"[日志] 收到预处理请求: 共 {len(assets)} 个素材。")
    if not assets:
        return jsonify({"staged": []})

    def process_asset(asset):
        try:
            print(f"[日志] 正在下载素材: {asset.get('label')} ...")
            content, headers = download_bytes(asset['url'], timeout=30)

            fallback_ext = asset.get('urlExt') or (".mp4" if asset.get('assetType') == 'video' else ".jpg")
            ext = guess_extension(headers.get("content-type"), fallback_ext)
            filename = f"{sanitize_filename(asset['label'])}{ext}"

            staged_id = str(uuid.uuid4())
            # 缓存到内存（buffer 保留供打包时使用）
            staged_store[staged_id] = {"fileName": filename, "buffer": content}
            print(f"[日志] 素材 {filename} 预处理完成，已缓存到内存。")
            
            # 计算 Hash，严格调用本地核心算法
            asset_hash = None
            try:
                import mam_core
                fd, temp_path = tempfile.mkstemp(suffix=ext)
                try:
                    with os.fdopen(fd, 'wb') as f:
                        f.write(content)
                    
                    # 使用与本地 GUI 完全一致的特征提取和 Hash 函数
                    img = mam_core.get_thumbnail(temp_path)
                    asset_hash = mam_core.get_phash(img)
                finally:
                    safe_remove(temp_path)
            except Exception as e:
                print(f"[警告] Hash 计算失败，降级为普通 MD5: {repr(e)}")
            
            if not asset_hash:
                asset_hash = hashlib.md5(content).hexdigest()[:16]

            return {"stagedId": staged_id, "label": asset['label'], "hash": asset_hash}
        except Exception as e:
            print(f"[错误] 预处理过程发生异常: {repr(e)}")
            print(traceback.format_exc(limit=1).strip())
            return None

    results = list(executor.map(process_asset, assets))
    staged = [r for r in results if r]
    print(f"[日志] 成功预处理 {len(staged)}/{len(assets)} 个素材。")
    return jsonify({"staged": staged})

@app.route('/add-page-blob', methods=['POST'])
def add_page_blob():
    data = request.json or {}
    url = data.get("url")
    if not url: return jsonify({"ok": False})

    print(f"[日志] 收到录入页面请求 (第 {data.get('pageNum')} 页)")
    try:
        content, _headers = download_bytes(url, timeout=60)
        ext = data.get("ext", "mp4")
        safe_name = sanitize_filename(data.get("projectName") or "Canva")
        page_num = str(data.get("pageNum", 0)).zfill(2)
        filename = f"{safe_name}_Page{page_num}.{ext}"

        plugin_page_store[filename] = {"buffer": content, "fileName": filename}
        print(f"[日志] 页面暂存成功: {filename}")
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[错误] 页面暂存失败: {repr(e)}")
        return jsonify({"ok": False})


def get_strict_target_name(page_idx, export_file_names):
    if not export_file_names:
        return f"{page_idx}.jpg"
    
    # 如果只有一个导出文件，说明用户可能只导出了一页单图
    if len(export_file_names) == 1:
        return export_file_names[0]
        
    # 按照顺序匹配实际导出的文件名 (Canva ZIP 中的文件通常按页顺序排列)
    idx = page_idx - 1
    if 0 <= idx < len(export_file_names):
        return export_file_names[idx]
        
    import os
    # 提取实际导出文件的后缀名
    ext = os.path.splitext(export_file_names[0])[1] or ".jpg"
    
    # Fallback：当数组越界时使用默认命名
    return f"{page_idx}{ext}"

def pack_user_assets_to_zip(zf, asset_download_items, export_file_names=None):
    """将扫描到的用户素材下载并按页分组写入 ZIP 的 '素材/' 子目录，生成对照表。"""
    if not asset_download_items:
        return

    export_file_names = export_file_names or []
    # 使用字典来按 page_idx 累积数据
    pages_map = {}

    for asset in asset_download_items:
        try:
            page_idx = asset.get('pageIndex', 1)
            assoc_export = get_strict_target_name(page_idx, export_file_names)
            
            # 使用成品文件名来命名素材文件夹（去掉后缀）
            export_base = os.path.splitext(assoc_export)[0]
            folder_name = f"{export_base}_素材"

            if page_idx not in pages_map:
                pages_map[page_idx] = {
                    "source": [],
                    "target": assoc_export
                }

            staged_id = asset.get('stagedId')
            if staged_id and staged_id in staged_store:
                cached = staged_store.pop(staged_id)
                content = cached['buffer']
                filename = cached['fileName']
                print(f"[日志] 使用缓存素材打包: {filename}")
            else:
                print(f"[日志] 直接下载素材打包: {asset.get('label')}")
                content, headers = download_bytes(asset['url'], timeout=30)
                fallback_ext = asset.get('urlExt') or (".mp4" if asset.get('assetType') == 'video' else ".jpg")
                ext = guess_extension(headers.get("content-type"), fallback_ext)
                filename = f"{sanitize_filename(asset.get('label', 'asset'))}{ext}"

            zip_path = f"素材/{folder_name}/{filename}"
            zf.writestr(zip_path, content)
            
            # 使用基于 ZIP 根目录的相对路径
            pages_map[page_idx]["source"].append(zip_path)
            
        except Exception as e:
            print(f"[错误] 打包素材失败 ({asset.get('label')}): {repr(e)}")

    import json
    # 将字典转为用户期望的数组格式
    mapping_list = list(pages_map.values())
    zf.writestr("素材与成品对照表.json", json.dumps(mapping_list, ensure_ascii=False, indent=2).encode('utf-8'))


@app.route('/pack-plugin-pages', methods=['POST'])
def pack_plugin_pages():
    data = request.json or {}
    title = sanitize_filename(data.get("title") or "Unnamed_Export")
    asset_download_items = data.get("assetDownloadItems") or []

    print(f"[日志] 正在打包页面 ZIP: {title}.zip")
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        if plugin_page_store:
            for fname, page_data in plugin_page_store.items():
                print(f"[日志] 添加文件到 ZIP: {fname}")
                zf.writestr(fname, page_data['buffer'])
        else:
            print("[日志] 没有收到任何画布页面，将生成空打包说明。")
            zf.writestr('empty.txt', b'no custom pages')

        # 打包用户扫描素材到 素材/ 子目录
        pack_user_assets_to_zip(zf, asset_download_items)

        # 清单
        zf.writestr("素材名称清单.txt", manifest_text.encode("utf-8"))
        zf.writestr("素材名称清单.json", manifest_json.encode("utf-8"))
            
    plugin_page_store.clear()
    zip_id = str(uuid.uuid4())
    file_name = f"{title}-含素材清单.zip"
    file_store[zip_id] = {
        "buffer": zip_buffer.getvalue(),
        "fileName": file_name,
        "createdAt": time.time()
    }
    download_url = f"http://localhost:3001/download/{zip_id}"
    pending_queue.append({"id": zip_id, "url": download_url, "fileName": file_name, "filename": file_name})
    print(f"[日志] 打包完成！下载标识: {zip_id[:8]}...")
    return jsonify({"success": True, "id": zip_id, "fileName": file_name})

@app.route('/export-bundle', methods=['POST'])
def export_bundle():
    data = request.json or {}
    title = sanitize_filename(data.get("title") or "canva-export")
    export_blobs = data.get("exportBlobs") or []
    asset_download_items = data.get("assetDownloadItems") or []
    canva_tracker = data.get("canvaTracker")

    if not export_blobs:
        return jsonify({"error": "exportBlobs is required"}), 400

    print(f"[日志] 正在打包导出 ZIP: {title}")
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 1. 下载并打包成品导出文件（根目录）
        export_file_names = []
        for idx, blob in enumerate(export_blobs):
            try:
                content, headers = download_bytes(blob['url'], timeout=120)
                content_type = headers.get("content-type", "")
                is_zip = "zip" in content_type or (len(content) > 1 and content[0] == 0x50 and content[1] == 0x4b)

                if is_zip:
                    # 解包嵌套 ZIP
                    inner_buf = io.BytesIO(content)
                    with zipfile.ZipFile(inner_buf, 'r') as inner_zf:
                        for inner_name in inner_zf.namelist():
                            info = inner_zf.getinfo(inner_name)
                            if not info.is_dir():
                                zf.writestr(inner_name, inner_zf.read(inner_name))
                                export_file_names.append(inner_name)
                    print(f"[日志] 已解包嵌套 ZIP (第 {idx+1} 个导出文件)")
                else:
                    fallback_ext = ".bin"
                    ext = guess_extension(content_type, fallback_ext)
                    fname = f"{title}-{str(idx+1).zfill(2)}{ext}" if len(export_blobs) > 1 else f"{title}{ext}"
                    zf.writestr(fname, content)
                    export_file_names.append(fname)
                    print(f"[日志] 已打包成品: {fname}")
            except Exception as e:
                print(f"[错误] 下载成品文件失败 (第 {idx+1} 个): {repr(e)}")

        # 2. 打包用户扫描素材到 素材/ 子目录，并生成关联映射
        pack_user_assets_to_zip(zf, asset_download_items, export_file_names)
        
        # 3. 如果有 canva_tracker 信息，也打包进去供桌面端读取
        if canva_tracker:
            import json
            if isinstance(canva_tracker, list):
                # 转换 tracker_data 的 pageIndex 为 target filename
                transformed_trackers = []
                for tracker in canva_tracker:
                    page_idx = tracker.get("pageIndex", 1)
                    target_name = get_strict_target_name(page_idx, export_file_names)
                    transformed_trackers.append({
                        "target": target_name,
                        "creator": tracker.get("creator", ""),
                        "hashes": tracker.get("hashes", [])
                    })
                canva_tracker = transformed_trackers

            zf.writestr("canva_tracker.json", json.dumps(canva_tracker, ensure_ascii=False, indent=2).encode('utf-8'))

    zip_id = str(uuid.uuid4())
    file_name = f"{title}-含素材清单.zip"
    file_store[zip_id] = {
        "buffer": zip_buffer.getvalue(),
        "fileName": file_name,
        "createdAt": time.time()
    }
    
    local_path = ""
    save_dir = os.environ.get('STANDALONE_SAVE_PATH', '')
    if save_dir and os.path.isdir(save_dir):
        base_name, ext = os.path.splitext(file_name)
        counter = 1
        local_path = os.path.join(save_dir, file_name)
        while os.path.exists(local_path):
            file_name = f"{base_name} ({counter}){ext}"
            local_path = os.path.join(save_dir, file_name)
            counter += 1

        try:
            with open(local_path, "wb") as f:
                f.write(zip_buffer.getvalue())
            print(f"[日志] 打包完成！文件已直接保存至: {local_path}")
        except Exception as e:
            print(f"[错误] 保存文件到本地失败: {e}")
            local_path = ""
    
    download_url = f"http://localhost:3001/download/{zip_id}"
    pending_queue.append({"id": zip_id, "url": download_url, "fileName": file_name, "filename": file_name})
    if not local_path:
        print(f"[日志] 打包完成！下载标识: {zip_id[:8]}...")
        
    return jsonify({"id": zip_id, "fileName": file_name, "localPath": local_path})


@app.route('/pending', methods=['GET'])
def long_poll_pending():
    for _ in range(30):
        if pending_queue:
            item = pending_queue.pop(0)
            return jsonify(item)
        time.sleep(1)
    return jsonify({})

@app.route('/download/<zip_id>', methods=['GET'])
def download_zip(zip_id):
    entry = file_store.get(zip_id)
    if not entry:
        print(f"[错误] 请求的下载文件不存在或已过期: {zip_id}")
        return "Not found or expired", 404
        
    print(f"[日志] 用户正在下载打包文件: {entry['fileName']}")
    response = make_response(entry["buffer"])
    encoded_name = urllib.parse.quote(entry["fileName"])
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_name}"
    response.headers["Content-Type"] = "application/zip"
    return response

from flask import request

@app.route('/api/config', methods=['GET'])
def get_config():
    cfg = {}
    try:
        import mam_core
        cfg = mam_core.load_config()
    except Exception as e:
        pass
        
    try:
        import json
        p = os.path.join(os.path.expanduser('~'), '.canva_tools_config.json')
        with open(p, 'r', encoding='utf-8') as f:
            canva_cfg = json.load(f)
            cfg.update(canva_cfg)
    except Exception as e:
        pass
        
    return jsonify({
        "hash_only_mode": cfg.get('hash_only_mode', False),
        "user_name": cfg.get('user_name', '操作员')
    })

@app.route('/api/fast-bind', methods=['POST'])
def fast_bind():
    data = request.json or {}
    q = app.config.get('FAST_BIND_QUEUE')
    if q:
        q.put(data)
        return jsonify({"status": "queued"})
    return jsonify({"error": "Fast bind queue not available"}), 500


# IMPORTANT: Catch-all to serve app.js or ANY other requested static files properly for Canva
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    dist_dir = get_dist_dir()
    file_to_serve = path if path else 'app.js'
    target_path = os.path.join(dist_dir, file_to_serve)
    
    def serve_with_dynamic_host(file_path):
        # Read the built js file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace hardcoded ports dynamically
        current_host = request.host_url.rstrip("/")
        content = content.replace("http://localhost:3001", current_host)
        
        # For variable references that rely on BACKEND_HOST being injected globally:
        prefix = f'window.BACKEND_HOST = "{current_host}";\n'
        resp = make_response(prefix + content, 200, {'Content-Type': 'application/javascript'})
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '-1'
        return resp

    # Canva requested exact file that exists
    if os.path.exists(target_path) and os.path.isfile(target_path):
        if file_to_serve.endswith('.js'):
            return serve_with_dynamic_host(target_path)
        return send_from_directory(dist_dir, file_to_serve)

    # Standard Canva request (usually root or an unknown path)
    if os.path.exists(os.path.join(dist_dir, 'app.js')):
        return serve_with_dynamic_host(os.path.join(dist_dir, 'app.js'))



