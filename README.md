# 素材溯源管理系统 (MAM) v3.1

MAM 是一个面向图片和视频素材的溯源管理工具，核心能力是把素材登记、衍生关系、成品封装、Canva 模板关联和查询串成一条可追踪链路。

系统基于 pHash 与数据库关系表，支持多级祖先追溯、组件树追溯、模板素材追溯，并把关键信息写回文件元数据，方便跨库或离线场景核验。

## 下载安装

### 预编译版本
访问 [GitHub Releases](https://github.com/secure-artifacts/Media-File-Provenance/releases) 获取最新版本的可执行文件。

- **Windows**: `mam-setup-vX.Y.Z.exe`（推荐）
- **macOS**: `mam-vX.Y.Z.dmg`

### 源码运行
```bash
git clone https://github.com/secure-artifacts/Media-File-Provenance.git
cd Media-File-Provenance
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
python mam_gui.py
```

## 当前功能

### 1. 登记中心
- 拖拽多个图片或视频批量登记。
- 自动计算 pHash，写入数据库并写入文件元数据。
- 支持从文件名自动识别制作人（人员代码映射）。

### 2. 衍生关联
- 记录来源素材到衍生素材的关系。
- 自动识别关系类型：图片到图片、图片到视频、视频到视频。
- 建立关联后，查询时可回看完整父级链路。

### 3. 成品封装
- 记录多个组件素材到一个成品文件的封装关系。
- 支持组件本身再向上追溯（子组件、祖先组件）。

### 4. 批量封装（按目录）
- 按目录自身和一级子目录拆分任务。
- 文件名包含"成品"的文件作为成品，其余作为组件。
- 多成品目录进入待确认列表，不中断整批流程。

### 5. Canva 模板
- 为一组素材生成模板 ID（可复制）。
- 保存模板名称、创建人、素材列表。
- 查询时展示模板与素材上下游关系。

### 6. Canva 批量
- 目录名包含【模板ID】时可自动按模板关联。
- 自动跳过无 ID 或模板不存在的目录。

### 7. 溯源查询
- 支持按文件批量查询。
- 支持按 Canva 模板 ID 查询。
- 查询结果以卡片和可展开树展示：衍生父级、衍生子级、组合来源、被应用项、Canva 模板与模板素材。
- 支持一键复制为 Google Sheets 可粘贴格式。

### 8. 素材总览
- 查看库内全量素材。
- 支持按文件名、作者实时筛选。

### 9. 批量扫描
- 扫描目录并自动登记未入库素材。
- 支持人员代码表维护、批量粘贴导入（含 Google Sheet 复制内容）。
- 兼容大小写、常见历史文件名格式、数字代码归一化等场景。

## 环境要求

- **Python**: 3.10+
- **系统**: Windows / macOS / Linux
- **exiftool**: （源码运行建议本机安装，预编译版已内置）

### 依赖安装

```bash
pip install -r requirements.txt
```

主要依赖：PyQt6、opencv-python、numpy、requests、Pillow、ImageHash、piexif、mutagen。

## 启动软件

### 预编译版
直接运行 exe 文件或 DMG 应用。

### 源码运行

**Windows**：
```bash
cd C:\path\to\Media-File-Provenance
.venv\Scripts\activate
python mam_gui.py
```

**macOS/Linux**：
```bash
cd /path/to/Media-File-Provenance
source .venv/bin/activate
python mam_gui.py
```

首次使用建议：
1. 点击左侧底部"系统设置"。
2. 填写操作员姓名与 MySQL 连接参数。
3. 连接成功后开始登记和关联流程。

## 使用指南

### 📸 生图组 - 原始素材登记

**操作步骤**：
1. 打开软件，切换到 **"素材登记"** 标签
2. 拖入生成的图片文件
3. 点击 **"执行批量登记"** 
4. 查看日志确认所有图片已登记 ✅

系统自动记录：pHash、文件大小、登记时间、操作员名称

---

### 🎬 生视频组 - 图片到视频关联

**操作步骤**：
1. **素材登记**：先登记原始图片和生成的视频
2. **建立关联**：
   - 切换到 **"处理关联"** 标签
   - 左侧"引用前序素材"：拖入原始图片
   - 右侧"修改后新素材"：拖入生成的视频
   - 点击 **"建立溯源关联"**

系统自动判断关系类型为 `image_to_video`

---

### 🎨 修图/视频编辑

**操作步骤**：
1. 登记原始素材和修改后的素材
2. 在 **"处理关联"** 中建立关联
3. 关系类型自动判断：`image_edit` 或 `video_edit`

---

### 🎬 Canva 成品合成

**操作步骤**：
1. **登记所有素材**：拖入所有下载的原始图片/视频
2. **切换"成品封装"标签**
3. **左侧"组件素材"**（多选）：拖入所有使用过的素材
4. **右侧"最终成品"**：拖入 Canva 导出的视频
5. 点击 **"建立成品关系"**

系统记录所有组件与成品的对应关系

---



### 📋 Canva 模板关联

**操作步骤**：
1. **创建模板**：
   - 切换到 **"Canva 模板"** 标签
   - 选择相关素材
   - 点击 **"生成模板 ID"**
   - 保存模板名称和创建人信息

2. **模板查询**：使用"溯源查询"按模板 ID 查询

3. **特殊场景**：
   - 目录名包含【模板ID】可自动按模板关联（"Canva 批量"）

---

### 🔍 溯源查询

**操作步骤**：
1. **按文件查询**：拖入或选择文件
2. **按模板查询**：输入 Canva 模板 ID
3. **查看完整链路**：
   - 衍生自（父级素材）
   - 衍生出（子级素材）
   - 由以下素材合成（组件）
   - 被应用于（被使用的地方）
   - Canva 模板及上游素材链
4. **导出结果**：一键复制为 Google Sheets 可粘贴格式

---

### 📊 素材总览 & 批量扫描

- **素材总览**：查看库内全量素材，按文件名、作者实时筛选
- **批量扫描**：扫描目录并自动登记未入库素材
- **人员代码表**：维护、导入生产人员代码映射

## 快速工作流

1. **初期准备**：在"素材登记"登记所有原始素材
2. **建立关系**：在"处理关联"记录素材之间的衍生关系
3. **成品记录**：在"成品封装"记录成品由哪些组件合成
4. **模板管理**：在"Canva 模板"或"Canva 批量"关联模板
5. **查询验证**：在"溯源查询"复核全链路并导出

## 常见问题

### 1. 制作人识别不对
- 先检查"批量扫描"中的人员代码表。
- 可使用"批量粘贴"一次导入大量代码映射。

### 2. 查询结果为空
- 先确认文件可读取且已登记。
- Canva 查询需输入已登记模板 ID。

### 3. macOS 无法写入元数据
- 确认已安装 exiftool：
    - brew install exiftool

## 如何发布新版本

本项目使用 GitHub Actions 自动构建和发布安装包。发布时会自动构建 Windows 和 macOS 产物，并上传到 Releases。

### 发布步骤

#### 1. 确保代码已提交并推送

```bash
git status
git add .
git commit -m "你的改动说明"
git push origin master
```

#### 2. 创建版本 Tag

```bash
git tag -a vX.Y.Z -m "Release version X.Y.Z"
```

#### 3. 推送 Tag 触发自动构建

```bash
git push origin vX.Y.Z
```

#### 4. 查看构建结果

- Actions 页面查看构建进度：
   https://github.com/secure-artifacts/Media-File-Provenance/actions
- Releases 页面查看安装包：
   https://github.com/secure-artifacts/Media-File-Provenance/releases

### 版本号说明

- vX.0.0：重大更新
- vX.Y.0：新增功能
- vX.Y.Z：问题修复

### 如果构建失败怎么办

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z

# 修复问题后重试
git add .
git commit -m "fix(ci): 修复构建失败"
git push origin master
git tag -a vX.Y.Z -m "Release version X.Y.Z"
git push origin vX.Y.Z
```

---

最后更新：2026-03-15
