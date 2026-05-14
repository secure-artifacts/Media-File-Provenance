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
Ran command: ` c:; cd 'c:\Users\newnew\Desktop\素材管理2'; & 'c:\Python314\python.exe' 'c:\Users\newnew\.antigravity\extensions\ms-python.debugpy-2026.6.0-win32-x64\bundled\libs\debugpy\launcher' '57812' '--' 'C:\Users\newnew\Desktop\素材管理2\mam_gui.py' `
Ran command: `Get-Content README.md -TotalCount 50`

这份教学大纲和功能介绍为您精心整理，非常适合作为**内部培训、操作手册或给团队成员的演示文档**。您可以直接复制使用。

---

# 🎨 MAM Desktop (素材资产防伪与溯源系统) 教学指南

## 🌟 一、 软件简介 (What is it?)
**MAM Desktop** 是一款专为设计团队与素材管理定制的桌面端“资产防伪与溯源”系统。
在日常设计工作中（特别是使用 Canva 等在线工具时），一个成品往往是由多个零散素材拼凑、修改而成的。时间一长，谁制作了哪个素材、这个成品到底用了哪些版权素材，往往成了一笔糊涂账。

本软件的核心目的就是：**给每一个素材打上“隐形身份证”，并自动记录它们从“原始素材”到“最终成品”的完整演变族谱（溯源树）。**

---

## 🚀 二、 核心功能亮点 (Core Features)

### 1. 🧬 隐形身份证技术 (pHash & 物理写入)
- **特征提取**：当您将图片或视频拖入系统时，软件会自动提取其视觉特征（pHash 感知哈希）。
- **物理烙印**：提取后，系统会将该身份信息**物理写入**文件的底层元数据（EXIF / PNG Info）中。即使文件被重命名、移动，系统依然能一眼认出它是谁。

### 2. 🌳 全链路族谱溯源 (Asset Lineage)
- **血缘追踪**：系统能够自动记录素材的“繁衍”过程：
  - **衍生（Derived）**：A图加了滤镜变成B图。
  - **组合（Composed）**：A图和B图拼在一起变成了C海报。
- **一键查祖宗**：只要把任意一个成品或素材拖进“查询窗口”，系统会立刻画出一棵“血缘树”，清清楚楚地展示它**由哪些素材合成**，或者它**被用在了哪些成品上**。

### 3. 🪄 Canva 深度自动化 (Canva Auto-Tracking)
这是本软件的一大杀器，彻底解放双手！
- **无感追踪**：配合我们的 Canva 插件，当您在画布上作图时，系统已经在后台悄悄记录了“哪一页用到了哪个素材”。
- **ZIP 拖拽即闭环**：当您从 Canva 导出打包好的 `.zip` 文件时，**不需要解压，直接把 ZIP 拖进软件！**
- **黑科技解析**：软件会自动安全解压，提取出成品图片，提取出隐藏的追踪数据，并将**成品与源素材进行“精确靶向绑定”**。整个录入和溯源关系建立在 1 秒内全自动完成。

### 4. 👥 团队协同与自动确权 (Attribution)
- 软件会通过底层识别或文件名匹配，自动为每个入库的素材和成品打上“制作人员”的标签。
- 团队共享同一个远端云数据库（REST API 架构）。无论是张三入库的素材，还是李四合成的海报，大家都能在溯源树中清晰看到各自的贡献，彻底杜绝版权争议和业绩归属问题。

---

## 🎯 三、 极简标准工作流 (SOP 教学演示)

为了让团队快速上手，日常使用只需遵循以下简单的 **“四步走”**：

### 🛠️ Step 1: 原始素材登记（打标签）
1. 打开系统，进入【素材登记】页面。
2. 将今天刚刚画好的插画、拍好的照片（甚至是包含几百个素材的整个文件夹）直接拖进窗口。
3. 系统瞬间完成特征提取、写入 EXIF 并在云端建档。
   *(此时，素材就拥有了全网唯一的身份！)*

### 🎨 Step 2: Canva 创作与记录
1. 设计师在 Canva 中导入刚才的素材进行排版设计。
2. 打开配套的 Canva 侧边栏插件，点击 **“扫描并记录素材”**。
3. 插件会自动生成一个包含溯源关系的 `.json` 文件。
4. 点击下载，将设计好的海报连同记录文件一起导出为 `ZIP 压缩包`。

### 📦 Step 3: ZIP 包一键全自动入库
1. 回到 MAM Desktop 软件。
2. 将刚才下载的 `ZIP 压缩包` **直接原封不动地拖拽到软件界面**。
3. 系统日志会疯狂闪动：
   - `📦 发现新压缩包，统一安全解压中...`
   - `✅ 关联素材自动收录`
   - `✅ 精准靶向入库: 绑定源素材 x 个`
4. 恭喜！您不需要手动填任何表格，成品与素材的父子关系已经永久绑定。

### 🔍 Step 4: 随时抽查与溯源
1. 某天老板拿着一张海报问：“这个海报是谁做的？里面那个卡通小熊是谁画的？”
2. 切换到软件的【源迹查询】界面。
3. 把这张海报拖进去，右侧立刻呈现：
   - 制作人：李四
   - `📦 由以下素材合成 (2项)`
     - 📄 卡通小熊.png (制作人：王五)
     - 📄 背景底纹.jpg (制作人：张三)
4. 追溯完成，业绩一目了然！

---

### 💡 导师授课小贴士：
在给团队演示时，建议**实机演示 Step 3 和 Step 4**。看着一个未解压的 ZIP 包拖进去，然后立马能在查询界面拉出一长串清晰的“父子血缘树”，这种“全自动魔术”的视觉冲击力是最强的，能立刻让团队理解这个系统的巨大价值。

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
