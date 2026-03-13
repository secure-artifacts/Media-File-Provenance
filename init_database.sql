-- MAM 素材管理系统 - MySQL 初始化脚本
-- 运行此脚本以创建所有必要的表

-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS mam_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 切换到数据库
USE mam_system;

-- ============================================
-- 表 1: assets (资产主表)
-- ============================================
-- 存储所有原始素材和成品的记录
-- 
-- 字段说明：
--   phash: 感知哈希值 (16位hex字符)
--   filename: 原始文件名
--   file_size: 文件大小（字节）
--   asset_type: 素材类型 (image / video / unknown)
--   producer: 上传/创建者姓名
--   producer_id: 上传/创建者ID
--   created_at: 创建/上传时间
--   metadata_json: 项目特定的元数据（JSON）
--   thumbnail: 缩略图（100x100 jpeg二进制）

CREATE TABLE IF NOT EXISTS assets (
    phash VARCHAR(64) PRIMARY KEY COMMENT '感知哈希值(主键)',
    filename VARCHAR(255) COMMENT '原始文件名',
    file_size BIGINT DEFAULT 0 COMMENT '文件大小(字节)',
    asset_type VARCHAR(20) DEFAULT 'unknown' COMMENT 'image/video/unknown',
    producer VARCHAR(50) COMMENT '上传者/创建者',
    producer_id VARCHAR(50) COMMENT '上传者ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    metadata_json MEDIUMTEXT COMMENT '元数据(JSON)',
    thumbnail MEDIUMBLOB COMMENT '缩略图',
    
    -- 索引优化
    INDEX idx_producer (producer),
    INDEX idx_created_at (created_at),
    INDEX idx_asset_type (asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT='资产主表';

-- ============================================
-- 表 2: asset_relations_11 (一对一关联)
-- ============================================
-- 用于追踪修改链路：源素材 → 修改后素材
-- 
-- 应用场景：
--   - 原图 → 修改图
--   - 图片 → 生成视频
--   - 视频 → 编辑版本
--   - 任何素材的"前驱"和"后继"关系

CREATE TABLE IF NOT EXISTS asset_relations_11 (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '关联ID',
    source_phash VARCHAR(64) NOT NULL COMMENT '源素材hash',
    target_phash VARCHAR(64) NOT NULL COMMENT '修改后素材hash',
    relation_type VARCHAR(50) COMMENT '关系类型(image_edit/image_to_video/etc)',
    operator VARCHAR(50) COMMENT '操作员名称',
    operator_id VARCHAR(50) COMMENT '操作员ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    remark TEXT COMMENT '操作备注',
    
    -- 外键约束
    CONSTRAINT fk_11_source FOREIGN KEY (source_phash) 
        REFERENCES assets(phash) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_11_target FOREIGN KEY (target_phash) 
        REFERENCES assets(phash) ON DELETE CASCADE ON UPDATE CASCADE,
    
    -- 索引优化
    INDEX idx_source (source_phash),
    INDEX idx_target (target_phash),
    INDEX idx_created_at (created_at),
    INDEX idx_relation_type (relation_type)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT='一对一关联(修改链)';

-- ============================================
-- 表 3: asset_relations_nm (一对多关联)
-- ============================================
-- 用于追踪成品合成链路：多个源素材 → 成品
--
-- 应用场景：
--   - 多个素材 → Canva视频
--   - 多个素材 → 剪辑版本
--   - 任何成品的"组成部分"关系

CREATE TABLE IF NOT EXISTS asset_relations_nm (
    id INT AUTO_INCREMENT PRIMARY KEY COMMENT '关联ID',
    component_phash VARCHAR(64) NOT NULL COMMENT '组件素材hash',
    final_phash VARCHAR(64) NOT NULL COMMENT '最终成品hash',
    component_order INT DEFAULT 0 COMMENT '组件顺序(0,1,2...)',
    component_role VARCHAR(50) COMMENT '组件角色(image/video)',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    
    -- 外键约束
    CONSTRAINT fk_nm_component FOREIGN KEY (component_phash) 
        REFERENCES assets(phash) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_nm_final FOREIGN KEY (final_phash) 
        REFERENCES assets(phash) ON DELETE CASCADE ON UPDATE CASCADE,
    
    -- 索引优化
    INDEX idx_component (component_phash),
    INDEX idx_final (final_phash),
    INDEX idx_created_at (created_at)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE utf8mb4_unicode_ci COMMENT='一对多关联(成品链)';

-- ============================================
-- 视图：完整链路查询（可选）
-- ============================================
-- 用于快速查看某个素材的完整溯源链路

CREATE OR REPLACE VIEW v_asset_lineage AS
SELECT 
    a.phash,
    a.filename,
    a.asset_type,
    a.producer,
    a.created_at,
    
    -- 一对一：作为源的修改
    GROUP_CONCAT(DISTINCT r11_t.target_phash SEPARATOR ',') AS modified_to,
    
    -- 一对一：作为目标的修改来源
    GROUP_CONCAT(DISTINCT r11_s.source_phash SEPARATOR ',') AS modified_from,
    
    -- 一对多：作为组件的成品
    GROUP_CONCAT(DISTINCT nm_p.final_phash SEPARATOR ',') AS used_in_compositions,
    
    -- 一对多：使用的组件
    GROUP_CONCAT(DISTINCT nm_c.component_phash SEPARATOR ',') AS contains_components
    
FROM assets a
LEFT JOIN asset_relations_11 r11_t ON a.phash = r11_t.source_phash
LEFT JOIN asset_relations_11 r11_s ON a.phash = r11_s.target_phash
LEFT JOIN asset_relations_nm nm_p ON a.phash = nm_p.component_phash
LEFT JOIN asset_relations_nm nm_c ON a.phash = nm_c.final_phash
GROUP BY a.phash;

-- ============================================
-- 初始化完成
-- ============================================

-- 显示表结构验证
SHOW TABLES;

-- 显示创建统计
SELECT CONCAT(
    'MAM 系统初始化完成\n',
    'Database: mam_system\n',
    'Tables: assets, asset_relations_11, asset_relations_nm\n',
    'Views: v_asset_lineage\n',
    '状态: ✅ 就绪'
) AS 'Initialization Result';

-- ============================================
-- 常用查询示例
-- ============================================

-- 1. 查询某个素材的所有修改版本（一对一）
-- SELECT * FROM asset_relations_11 
-- WHERE source_phash = 'target_phash_here';

-- 2. 查询某个成品的所有组成部分（一对多）
-- SELECT a.*, nm.component_order, nm.component_role 
-- FROM assets a
-- JOIN asset_relations_nm nm ON a.phash = nm.component_phash
-- WHERE nm.final_phash = 'target_phash_here'
-- ORDER BY nm.component_order;

-- 3. 查询完整链路（使用视图）
-- SELECT * FROM v_asset_lineage 
-- WHERE phash = 'target_phash_here';

-- 4. 按生产者统计
-- SELECT producer, COUNT(*) as count, asset_type
-- FROM assets
-- GROUP BY producer, asset_type
-- ORDER BY count DESC;

-- 5. 查询最近7天的登记
-- SELECT * FROM assets
-- WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
-- ORDER BY created_at DESC;

-- ============================================
-- 数据备份建议
-- ============================================
-- 定期备份数据库：
-- mysqldump -u root -p mam_system > backup_$(date +\%Y\%m\%d).sql
--
-- 恢复备份：
-- mysql -u root -p mam_system < backup_20260313.sql
