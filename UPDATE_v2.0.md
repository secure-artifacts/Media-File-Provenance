# MAM 素材管理系统 - v2.0 升级说明

## 🎯 核心改进

### 1. 数据库架构升级
**三表合一关系管理体系：**

#### assets 表（资产主表）
```
✅ 新增字段：
- file_size: BIGINT          # 文件大小（字节）
- asset_type: VARCHAR(20)    # 素材类型：image / video / unknown
```

#### asset_relations_11 表（一对一关联）
用于追踪修改链路：源素材 → 修改后素材
```
Fields:
- source_phash: 源素材hash
- target_phash: 修改后素材hash
- relation_type: image_edit / image_to_video / video_edit 等
- operator: 操作员名称
- created_at: 操作时间
```

**应用场景：**
- 生视频组：图片(hash) → 视频(hash)
- 修图组：原图 → 修改图
- 视频编辑：原视频 → 编辑版视频

#### asset_relations_nm 表（一对多关联）
用于追踪成品合成链路：多个源 → 成品
```
Fields:
- component_phash: 组件素材hash
- final_phash: 最终成品hash
- component_order: 组件序号（0,1,2...）
- component_role: 组件角色（image / video）
```

**应用场景：**
- Canva视频制作：多图+多视频 → Canva成品
- 剪辑视频制作：多素材 → 最终剪辑版

---

### 2. 元数据检测智能化
**检测优先级（从高到低）：**

```python
1. PNG metadata 中的 MamLineage  → 最准确
2. 文件名中的 Hash              → 备选方案
3. 计算 phash                    → 实时计算
```

**支持的文件格式：**
- PNG: 直接读写 MamLineage 元数据字段
- 其他格式: 计算phash并记录到数据库

---

### 3. 三个核心处理流程

#### 流程 1：素材登记 (Tab 1)
```
文件拖入 → 检测/计算hash → 登记到库
        ↓
    记录：filename, file_size, asset_type, producer
```

**新增能力：**
- ✅ 自动读取PNG中已有的hash
- ✅ 记录文件大小和类型
- ✅ 支持批量登记

---

#### 流程 2：一对一关联 (Tab 2) 
```
源素材file1 ──→ 修改后file2
        ↓
    创建关联记录
    写入metadata
```

**应用案例：**
- **生视频组**: 
  - 图片A(hash1) → 视频A(hash2) 
  - 关系记录：image_to_video

- **修图组**:
  - 原图(hash1) → 修改图(hash2)
  - 关系记录：image_edit

**新增能力：**
- ✅ 自动从PNG metadata读取源hash
- ✅ 创建明确的数据库关联记录
- ✅ 自动保存元数据链路到目标PNG

---

#### 流程 3：成品封装 (Tab 3)
```
素材1 ┐
素材2 ├─→ 最终成品
素材3 ┘    (记录所有关系)
```

**应用案例：**
- **Canva视频组**:
  - 素材：图片A(hash1) + 图片B(hash2) + 视频C(hash3)
  - 结果：Canva成品(hash_final)
  - 记录：3个一对多关联

- **剪辑视频组**:
  - 素材：原视频(hash1) + 字幕视频(hash2) + 特效(hash3)
  - 结果：最终剪辑版(hash_final)
  - 记录：3个一对多关联

**新增能力：**
- ✅ 支持多个源素材
- ✅ 自动合并所有元数据
- ✅ 创建完整的组件关联记录
- ✅ 支持手动补链（JSON或文本）

---

### 4. 审计看板增强 (Tab 4)
**支持完整链路查询：**

```
待审计文件 → 计算/检测hash
         ↓
    查询数据库 → 找到匹配记录
         ↓
    显示链路：
    ├─ 来源于：[源素材1, 源素材2]      (一对一反向)
    ├─ 被修改为：[修改版1, 修改版2]    (一对一正向)  
    └─ 被用于成品：[成品A, 成品B]      (一对多)
```

**新增方法：`query_hierarchy(phash)`**
- 自动查询所有相关关系
- 返回完整的层级信息
- 支持展示溯源树

---

## 📊 工作流示意

### 场景：Canva视频制作

```
Step 1: 生图组登记
  新建 → 拖入generated_image_1.png
        └─ hash: 3f1a2b4c...
        └─ type: image
        └─ size: 2.3MB
        └─ producer: 李明

Step 2: 素材下载审核
  新建 → 拖入 canva_template.mp4
        └─ hash: 5e2d1f8a...
        └─ type: video
        └─ producer: 王芳 (审批)

Step 3: Canva制作与关联
  素材关联 ×2:
    source: generated_image_1.png (hash: 3f1a2b4c)
    target: canva_template.mp4 (hash: 5e2d1f8a)
    type: image_to_video
    → 创建 asset_relations_11 record

Step 4: 成品封装
  来源素材：
    - generated_image_1.png (hash: 3f1a2b4c)
    - canva_template.mp4 (hash: 5e2d1f8a)
  
  成品: canva_final_export.mp4
    hash: 7c3e5a9b...
  
  → 创建2条 asset_relations_nm record
  → 合并所有元数据
  → 保存到PNG metadata

Step 5: 审计验证
  输入: canva_final_export.mp4
  → query_hierarchy()
  → 显示完整链路：
     ├─ 直接组件: 
     │  ├─ generated_image_1.png (李明, 2.3MB)
     │  └─ canva_template.mp4 (王芳, 48MB)
     ├─ 历史记录:
     │  └─ image_to_video (王芳, xxx)
```

---

## 🔧 数据库初始化

首次运行时自动创建新表：
```sql
-- 已有表升级添加：
ALTER TABLE assets ADD COLUMN file_size BIGINT;
ALTER TABLE assets ADD COLUMN asset_type VARCHAR(20);

-- 新增表自动创建：
CREATE TABLE asset_relations_11 (...)  -- 一对一
CREATE TABLE asset_relations_nm (...)  -- 一对多
```

无需手动操作！

---

## 📝 API 参考

### 核心方法

#### 元数据检测
```python
existing_hash, source = extract_hash_from_metadata(filepath)
# 返回: (hash值 or None, "metadata"/"filename"/"computed")
```

#### 素材类型判断
```python
asset_type = get_asset_type(filepath)  # "image" / "video" / "unknown"
file_size = get_file_size(filepath)    # 字节数
```

#### 数据库操作
```python
# 注册资产
db.register({
    "phash": phash_value,
    "filename": name,
    "file_size": size_bytes,
    "asset_type": "image/video",
    "producer": user_name,
    ...
})

# 创建一对一关联
db.create_relation_11(
    source_phash, target_phash,
    relation_type="image_edit",
    operator=user_name,
    operator_id=user_id
)

# 创建一对多关联
db.create_relations_nm(
    [hash1, hash2, hash3],    # 源素材
    final_phash,              # 成品
    ["image", "video", ...]   # 角色
)

# 查询完整链路
hierarchy = db.query_hierarchy(phash)
# 返回: 
# {
#   "asset": {...},
#   "sources_11": [...],       # 这个哈希的源修改记录
#   "targets_11": [...],       # 由这个修改后的产物
#   "sources_nm": [...],       # 这个作为component的成品
#   "targets_nm": [...]        # 这个使用的component
# }
```

---

## 🎯 各个团队具体使用

### 生图组
```
Action: 素材登记 (Tab 1)
- 拖入 generated_image_*.png
- 点击"执行批量登记"
- 系统自动记录：hash, 大小, 时间, 你的名字
```

### 生视频组
```
Action 1: 素材登记 (Tab 1)
- 拖入原始图片

Action 2: 处理关联 (Tab 2)
- 源素材: 原始图片
- 修改后: 生成的视频
- 点击"建立溯源关联"
- 系统记录：image → video 关系
```

### Canva视频组
```
Action 1: 素材下载审核后登记 (Tab 1)
- 拖入所有使用的素材（图片/视频）
- 登记到库

Action 2: 成品封装 (Tab 3)
- 组件素材: 选择所有使用的图片+视频
- 最终成品: 选择Canva导出的视频
- 点击"封装成品"
- 系统自动：
  - 合并元数据
  - 创建关系记录
  - 保存到PNG metadata（如果是PNG）
```

### 剪辑视频组
```
完全同 Canva 视频组
（只是最终成品是剪辑视频而非Canva）
```

### 审计
```
Action: 审计看板 (Tab 4)
- 拖入待审计的文件夹
- 点击"启动比对"
- 系统显示：
  - ✅/❓ 状态
  - 原始作者
  - 相似度
  - 完整链路信息
  
报告内容示例：
  待审计.mp4
  ├─ 原始库中找到: final_video.mp4
  ├─ 作者: 张三
  ├─ 相似度: 98%
  └─ 链路:
     ├─ 来源素材: image1.png, image2.png
     ├─ 编辑记录: 修改人 (xxx时间)
     └─ 合成: Canva处理 (yyy时间)
```

---

## ⚠️ 已知限制

1. **视频metadata**: 目前只支持PNG的嵌入式metadata，MP4/MKV需要通过数据库查询
2. **关联限制**: 当前哈希相似度阈值为12，可根据需要调整 (line ~170)
3. **手动补链**: 需要用户自行输入JSON或文本描述（Tab 3的手动补链板块）

---

## 🚀 下一步计划

- [ ] 支持MP4/MKV的metadata嵌入（FFmpeg集成）
- [ ] 前端层级树形展示（审计看板树状UI）
- [ ] 导出审计报告为PDF/Excel
- [ ] 批量关联助手
- [ ] 自动去重机制（基于余弦相似度）

---

## 📞 常见问题

**Q: 为什么查询不到新登记的素材？**  
A: 确保MySQL连接成功（顶部提示"✅ 数据库连接成功"），新素材需要数秒才能被查询到。

**Q: PNG metadata会被覆盖吗？**  
A: 不会，系统会保留原有metadata，只添加/更新MamLineage和MamHash字段。

**Q: 一个素材能被多个成品使用吗？**  
A: 可以！一对多表支持多条记录，一张图可以同时被Canva和剪辑视频使用。

**Q: 如何清除错误的关联？**  
A: 直接修改数据库中的 asset_relations_11 或 asset_relations_nm 表。

---

Version: 2.0  
Updated: 2026-03-13  
Status: ✅ Ready for Production
