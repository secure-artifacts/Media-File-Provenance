import hashlib
import json
import os
import shutil
import tempfile
import numpy as np
import cv2
from datetime import datetime
from PIL import Image, PngImagePlugin
import warnings

warnings.filterwarnings('ignore')

# --- 辅助功能：支持中文路径的 OpenCV 读写 ---
def cv2_imread(filepath):
    """读取带有中文路径的图片"""
    return cv2.imdecode(np.fromfile(filepath, dtype=np.uint8), cv2.IMREAD_UNCHANGED)

def cv2_imwrite(filepath, img):
    """写入带有中文路径的图片"""
    ext = os.path.splitext(filepath)[1]
    _, res = cv2.imencode(ext, img)
    res.tofile(filepath)

# --- 核心功能：获取文件hash ---
def get_file_hash(filepath):
    """计算文件的 SHA-256 值"""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def get_phash(img):
    """计算图片的感知哈希值 (pHash)"""
    try:
        if img is None: 
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(np.float32(resized))
        dct_low = dct[:8, :8]
        avg = np.mean(dct_low)
        bits = (dct_low > avg).flatten()
        return "".join([hex(int("".join([str(int(b)) for b in bits[i:i+4]]), 2))[2:] for i in range(0, 64, 4)])
    except:
        return None

# --- 元数据读写 ---
def read_metadata(filepath):
    """读取文件内的溯源 Metadata (支持 PNG)"""
    if not filepath.lower().endswith('.png'):
        return []
    try:
        img = Image.open(filepath)
        if hasattr(img, 'text'):
            text_info = img.text
            if 'MamLineage' in text_info:
                return json.loads(text_info['MamLineage'])
    except Exception as e:
        pass
    return []

def write_metadata(filepath, lineage_data):
    """将溯源记录写入 PNG Metadata"""
    if not filepath.lower().endswith('.png'):
        return False
    try:
        img = Image.open(filepath)
        metadata = PngImagePlugin.PngInfo()
        if hasattr(img, 'text'):
            for k, v in img.text.items():
                if k not in ['MamLineage', 'MamHash']:
                    metadata.add_text(k, v)
        metadata.add_text('MamLineage', json.dumps(lineage_data, ensure_ascii=False))
        metadata.add_text('MamHash', get_file_hash(filepath)[:16])
        img.save(filepath, "PNG", pnginfo=metadata)
        return True
    except Exception as e:
        print(f"[错误] 元数据写入失败: {str(e)}")
        return False

def get_short_id(hash_str):
    """提取短 Hash 作为成品的唯一 ID"""
    return hash_str[:16]

# --- 业务流程模板 ---
def template_raw_asset(filepath, user, producer_id):
    """模板：原始素材登记"""
    return {
        "type": "raw_asset",
        "role": "原始素材",
        "user": user,
        "user_id": producer_id,
        "hash": get_file_hash(filepath),
        "time": datetime.now().isoformat(),
        "filename": os.path.basename(filepath),
        "file_size": os.path.getsize(filepath)
    }

def template_edit_asset(source_phash, new_filepath, user, producer_id, relation_type="edit"):
    """模板：编辑/修改素材 (一对一关联)"""
    return {
        "type": "edit_1to1",
        "relation_type": relation_type,
        "user": user,
        "user_id": producer_id,
        "source_phash": source_phash,
        "target_hash": get_file_hash(new_filepath),
        "time": datetime.now().isoformat(),
        "filename": os.path.basename(new_filepath),
        "file_size": os.path.getsize(new_filepath)
    }

def template_composition_asset(component_phashes, final_filepath, user, producer_id):
    """模板：成品封装 (一对多关联)"""
    return {
        "type": "composition",
        "user": user,
        "user_id": producer_id,
        "component_phashes": component_phashes,
        "final_hash": get_file_hash(final_filepath),
        "final_id": get_short_id(get_file_hash(final_filepath)),
        "time": datetime.now().isoformat(),
        "filename": os.path.basename(final_filepath),
        "file_size": os.path.getsize(final_filepath),
        "component_count": len(component_phashes)
    }

if __name__ == "__main__":
    print("="*60)
    print("MAM 素材管理系统 - 工作流模板演示")
    print("="*60)
    
    # 演示：原始素材
    print("\n[原始素材登记]")
    raw_record = template_raw_asset("step1_source.png", "生图组-李明", "user_001")
    print(json.dumps(raw_record, indent=2, ensure_ascii=False))
    
    # 演示：编辑修改
    print("\n[一对一关联 - 图片修改]")
    edit_record = template_edit_asset(raw_record["hash"][:16], "step2_edited.png", "修图组-王芳", "user_002", "image_edit")
    print(json.dumps(edit_record, indent=2, ensure_ascii=False))
    
    # 演示：成品封装
    print("\n[一对多关联 - 成品合成]")
    comp_record = template_composition_asset([raw_record["hash"][:16], edit_record["target_hash"][:16]], "step3_final.mp4", "合成组-张三", "user_003")
    print(json.dumps(comp_record, indent=2, ensure_ascii=False))
    
    print("\n✅ 所有模板已演示完毕")
