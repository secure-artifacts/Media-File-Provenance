# API 参考 - 技术文档

## 📦 核心模块

### mam_gui.py - 图形界面和数据库操作

#### 类: DBManager

**初始化**
```python
db = DBManager()
ok, msg = db.connect()  # 返回 (success: bool, message: str)
```

**数据库配置**
```python
# 加载
conf = db.load_db_config()  
# 修改
db.conf['host'] = 'new_host'
db.save_db_config(db.conf)
```

**核心方法**

##### register(data)
```python
db.register({
    "phash": "a1b2c3d4e5f6g7h8",
    "filename": "image_001.png",
    "file_size": 1024000,           # 字节
    "asset_type": "image",          # "image" or "video"
    "producer": "李明",
    "producer_id": "user_001",
    "time": datetime.now(),
    "meta": json.dumps({...}),      # 元数据JSON
    "thumb": jpeg_bytes             # 100x100 jpg缩略图二进制
})
```

**描述**: 注册或更新一个素材记录到assets表

---

##### query(phash)
```python
match = db.query("a1b2c3d4e5f6g7h8")
# 返回: 
# {
#   'phash': 'a1b2c3d4e5f6g7h8',
#   'filename': 'image_001.png',
#   'producer': '李明',
#   'confidence': '98%',
#   ...
# } 或 None
```

**描述**: 根据phash查询最相似的记录
- 使用汉明距离计算相似度
- 阈值: 12 bits (约81%相似度)

---

##### query_hierarchy(phash)
```python
hier = db.query_hierarchy("a1b2c3d4e5f6g7h8")
# 返回:
# {
#   "asset": {...asset info...},
#   "sources_11": [
#     {
#       "relation": {...relation_record...},
#       "asset": {...source_asset...}
#     }
#   ],
#   "targets_11": [...],  # 修改产物
#   "sources_nm": [...],  # 被其他成品使用
#   "targets_nm": [...]   # 使用的组件
# }
```

**描述**: 查询完整的溯源链路（一对一和一对多）

---

##### create_relation_11(source_phash, target_phash, relation_type, operator, operator_id, remark)
```python
db.create_relation_11(
    source_phash="hash1",
    target_phash="hash2",
    relation_type="image_edit",  # "image_edit", "image_to_video", etc.
    operator="王芳",
    operator_id="user_002",
    remark="修改了色调"
)
```

**描述**: 创建一对一关联记录（修改或处理链）
- 记录到 `asset_relations_11` 表
- 用于追踪: 原图 → 修改图, 图片 → 视频, 视频→编辑版 等

---

##### create_relations_nm(component_phashes, final_phash, component_roles)
```python
db.create_relations_nm(
    component_phashes=["hash1", "hash2", "hash3"],
    final_phash="final_hash",
    component_roles=["image", "video", "image"]
)
```

**描述**: 创建一对多关联记录（成品合成链）
- 记录到 `asset_relations_nm` 表
- 用于追踪: 多个素材 → 成品
- component_order 自动按输入顺序分配 (0, 1, 2...)

---

#### 全局函数

##### extract_hash_from_metadata(filepath)
```python
existing_hash, source = extract_hash_from_metadata("path/to/image.png")
# 返回: ("a1b2c3d4e5f6g7h8", "metadata") 或 (None, None)
# 
# source 值:
#   - "metadata": 从PNG的MamLineage读取
#   - "filename": 从文件名解析
#   - None: 未找到
```

**描述**: 优先级检测已有的hash

---

##### get_phash(img)
```python
import cv2
img = cv2.imread("image.png")
phash = get_phash(img)  # 返回16位hex字符串或None
```

**描述**: 计算图片的感知哈希值
- 基于DCT变换的低频信息
- 对旋转、缩放、压缩相对容错
- 格式: 16个hex字符 (64bits)

---

##### get_asset_thumbnail(filepath)
```python
thumbnail = get_asset_thumbnail("video.mp4")
# 返回: cv2格式的BGR图片数组 或 None
```

**描述**: 获取视频或图片的缩略图
- 图片: 直接读取
- 视频: 自动提取第0.5秒帧

---

##### get_file_size(filepath)
```python
size = get_file_size("video.mp4")  # 返回: 字节数 (int)
```

---

##### get_asset_type(filepath)
```python
t = get_asset_type("image.png")   # 返回: "image"
t = get_asset_type("video.mp4")   # 返回: "video"
t = get_asset_type("file.txt")    # 返回: "unknown"
```

---

### mam_system.py - 业务模板和工具函数

#### templates (业务模板)

##### template_raw_asset(filepath, user, producer_id)
```python
record = template_raw_asset(
    "image_001.png",
    "李明",
    "user_001"
)
# 返回:
# {
#   "type": "raw_asset",
#   "role": "原始素材",
#   "user": "李明",
#   "user_id": "user_001",
#   "hash": "abc123def456...",
#   "time": "2026-03-13T10:30:45.123456",
#   "filename": "image_001.png",
#   "file_size": 1024000
# }
```

**用途**: 生成原始素材的记录模板

---

##### template_edit_asset(source_phash, new_filepath, user, producer_id, relation_type)
```python
record = template_edit_asset(
    source_phash="abc123...",
    new_filepath="image_edited.png",
    user="王芳",
    producer_id="user_002",
    relation_type="image_edit"
)
# 返回: 修改记录模板
```

**关系类型参考**:
- `"image_edit"`: 图片修改
- `"image_to_video"`: 图片→视频
- `"video_edit"`: 视频编辑

---

##### template_composition_asset(component_phashes, final_filepath, user, producer_id)
```python
record = template_composition_asset(
    ["hash1", "hash2"],
    "final_video.mp4",
    "张三",
    "user_003"
)
```

---

#### Metadata 操作

##### read_metadata(filepath)
```python
lineage = read_metadata("image.png")
# 返回: [
#   {"type": "raw", "user": "李明", ...},
#   {"type": "edit", "time": "2026-03-13T..."}
# ]
# 
# 如果无metadata: 返回 []
```

---

##### write_metadata(filepath, lineage_data)
```python
success = write_metadata(
    "image.png",
    [
        {"type": "raw", "user": "李明"},
        {"type": "edit", "time": "2026-03-13T..."}
    ]
)
# 返回: bool
```

**描述**: 写入到PNG的MamLineage字段

---

## 🗄️ 数据库表结构

### assets (主表)

```sql
CREATE TABLE assets (
    phash VARCHAR(64) PRIMARY KEY,
    filename VARCHAR(255),
    file_size BIGINT,
    asset_type VARCHAR(20),
    producer VARCHAR(50),
    producer_id VARCHAR(50),
    created_at DATETIME,
    metadata_json MEDIUMTEXT,
    thumbnail MEDIUMBLOB
);
```

**字段说明**:
- **phash**: 素材的感知哈希值 (主键)
- **filename**: 原始文件名
- **file_size**: 文件大小（字节）
- **asset_type**: "image" / "video" / "unknown"
- **producer**: 上传/创建者名字
- **producer_id**: 上传/创建者ID
- **created_at**: 创建时间
- **metadata_json**: 项目特定的元数据（JSON格式）
- **thumbnail**: 100x100 jpg缩略图（二进制）

---

### asset_relations_11 (一对一关联)

```sql
CREATE TABLE asset_relations_11 (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source_phash VARCHAR(64),      -- 源素材
    target_phash VARCHAR(64),      -- 修改后素材
    relation_type VARCHAR(50),     -- 关系类型
    operator VARCHAR(50),          -- 操作员
    operator_id VARCHAR(50),       -- 操作员ID
    created_at DATETIME,           -- 操作时间
    remark TEXT,                   -- 备注
    FOREIGN KEY (source_phash) REFERENCES assets(phash),
    FOREIGN KEY (target_phash) REFERENCES assets(phash)
);
```

**用途**: 追踪修改链路
- 原图 → 修改图
- 图片 → 视频
- 视频 → 编辑版

**查询例子**:
```sql
-- 查询所有以某张图为源的修改
SELECT * FROM asset_relations_11 
WHERE source_phash = 'xxx' 
ORDER BY created_at;

-- 查询某个素材的完整修改链
SELECT * FROM asset_relations_11 
WHERE source_phash = 'xxx' OR target_phash = 'xxx';
```

---

### asset_relations_nm (一对多关联)

```sql
CREATE TABLE asset_relations_nm (
    id INT AUTO_INCREMENT PRIMARY KEY,
    component_phash VARCHAR(64),    -- 组件素材
    final_phash VARCHAR(64),        -- 成品
    component_order INT,            -- 组件顺序 (0, 1, 2...)
    component_role VARCHAR(50),     -- 组件角色 (image/video)
    created_at DATETIME,            -- 创建时间
    FOREIGN KEY (component_phash) REFERENCES assets(phash),
    FOREIGN KEY (final_phash) REFERENCES assets(phash)
);
```

**用途**: 追踪成品合成链路
- 多个素材 → Canva视频
- 多个素材 → 剪辑版本

**查询例子**:
```sql
-- 查询某个成品使用的所有组件
SELECT * FROM asset_relations_nm 
WHERE final_phash = 'final_xxx' 
ORDER BY component_order;

-- 查询某个素材被用于哪些成品
SELECT * FROM asset_relations_nm 
WHERE component_phash = 'comp_xxx';
```

---

## 🔄 工作流数据流

### 素材登记流

```
File → extract_hash_from_metadata()
    ↓
    如果读不到 → get_phash()
    ↓
get_file_size() + get_asset_type()
    ↓
db.register({
    phash, filename, file_size, 
    asset_type, producer, time, meta, thumb
})
    ↓
mysql.assets ← ✅ 记录写入
```

---

### 一对一关联流

```
源文件 → extract_hash() → ph_src
修改文件 → extract_hash() → ph_new
    ↓
db.query(ph_src) → 检验源是否存在
    ↓
db.register({
    phash: ph_new,
    ...新素材信息...
    meta: json.dumps([...历史链...] + 新修改)
})
    ↓
db.create_relation_11(
    ph_src, ph_new,
    relation_type, operator
)
    ↓
mysql.assets ← 新素材记录
mysql.asset_relations_11 ← 关系记录
```

---

### 一对多组合流

```
素材1, 素材2, ... → extract_hash() → [ph1, ph2, ...]
成品文件 → extract_hash() → ph_final
    ↓
for each component:
    db.query(component_ph) → 获取元数据
    合并元数据 → combined_lineage
    ↓
db.register({
    phash: ph_final,
    ...成品信息...
    meta: json.dumps(combined_lineage)
})
    ↓
db.create_relations_nm([ph1, ph2], ph_final)
    ↓
mysql.assets ← 成品记录
mysql.asset_relations_nm ← 3条关系记录（各个component→final）
```

---

## 🔍 查询示例

### 审计场景：某个视频的完整溯源

```python
# 1. 输入待审计文件
audit_video = "suspect_video.mp4"

# 2. 计算hash
t = get_asset_thumbnail(audit_video)
ph = extract_hash_from_metadata(audit_video)
if not ph: ph = get_phash(t)

# 3. 查询完整链路
hier = db.query_hierarchy(ph)

# 4. 输出结果
asset = hier['asset']
print(f"素材: {asset['filename']}")
print(f"上传者: {asset['producer']}")

if hier['targets_nm']:  # 如果这是个成品
    print("使用的组件：")
    for item in hier['targets_nm']:
        comp = item['asset']
        print(f"  - {comp['filename']} (创建者: {comp['producer']})")

if hier['sources_11']:  # 如果这是修改产物
    print("来源素材：")
    for item in hier['sources_11']:
        src = item['asset']
        rel = item['relation']
        print(f"  - {src['filename']} (类型: {rel['relation_type']})")
```

---

## ⚙️ 配置参考

### mam_config.json
```json
{
  "user_name": "李明",
  "user_id": "user_001"
}
```

### mam_db_config.json
```json
{
  "host": "localhost",
  "user": "root",
  "password": "your_password",
  "db": "mam_system",
  "port": 3306
}
```

---

## 🚀 扩展开发

### 添加新的关系类型

在 `create_relation_11` 中添加新的 `relation_type`:

```python
# 示例：支持新的关系类型
db.create_relation_11(
    source_phash, target_phash,
    relation_type="YOUR_NEW_TYPE",  # e.g., "ai_enhancement"
    operator=user,
    operator_id=user_id
)
```

### 自定义元数据字段

在各个流程中的 `meta` JSON中添加自定义字段：

```python
meta_dict = {
    "type": "raw_asset",
    "source": "register",
    "custom_field_1": "value1",  # 自定义
    "custom_field_2": {"nested": "data"}  # 支持嵌套
}
db.register({
    ...
    "meta": json.dumps(meta_dict),
    ...
})
```

---

## 📈 性能提示

1. **大量查询**: phash查询是全表扫描，如果assets表很大，考虑添加索引
```sql
ALTER TABLE assets ADD INDEX idx_producer(producer);
ALTER TABLE assets ADD INDEX idx_created_at(created_at);
```

2. **批量操作**: 使用事务提高效率
```python
with db.conn.cursor() as cursor:
    cursor.execute("START TRANSACTION")
    # ...多个insert...
    cursor.execute("COMMIT")
```

3. **缩略图存储**: 当前thumbnail是MEDIUMBLOB，如果有海量图片，考虑文件系统存储

---

Version: 2.0  
Last Updated: 2026-03-13
