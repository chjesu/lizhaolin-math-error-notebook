# Web 改造讨论纪要

| 项目 | 内容 |
|---|---|
| 会议主题 | 将李兆霖数学错题本改造为阿里云 Web 服务 |
| 日期 | 2026-08-22 |
| 参与角色 | 项目发起人、Codex |
| 文档状态 | 待评审 |
| 关联文档 | PRD、技术架构、实施计划、测试验收运维 |

## 1. 背景

现有项目已形成照片判题、错因诊断、已验证题推荐、复习计划、PDF、试卷导入与题目审核闭环。当前实现以本地 Python CLI、唯一 SQLite 主库和项目级 Skill 为核心，适合个人长期使用。

本次讨论目标是把已有能力通过浏览器提供，同时保持题库可信度、判题标准、推荐边界和可追溯性，不重复建设已有功能。

## 2. 需求确认

项目发起人确认：

1. 使用阿里云承载 Web 服务。
2. 应用和数据库需要物理分离。
3. 数据库可以使用 MySQL。
4. Web 版必须包含 Agent 边界护栏。
5. 当前讨论应整理成标准项目管理文档，作为后续实施依据。

## 3. 讨论结论

### 3.1 产品边界

- 第一阶段面向单家庭或少量邀请用户。
- 学生端聚焦上传、判题、订正、复习和练习 PDF。
- 家长端聚焦任务、历史、纠错、难度调整和进度。
- 题库批量导入、验证、数据修复属于管理端能力，第一阶段可继续由受控 CLI 完成。
- 自动调用家庭打印机不进入首版；首版由浏览器下载或打印 PDF。

### 3.2 技术边界

- 目标数据库为 RDS MySQL 8.0，应用运行在 ECS。
- ECS 与 RDS 通过同 VPC 内网连接，数据库不向公网暴露。
- 照片、试卷原件和 PDF 使用私有 OSS。
- 不将 SQLite 文件放在 OSS、NFS 或网络共享盘上。
- 不长期维护两个活动主库；正式切换后 RDS MySQL 是唯一生产主库。
- 当前 SQLite 在切换前继续保持唯一主库，迁移副本只能用于只读验证。

### 3.3 实现边界

- Web 层只增加身份、页面、API、任务状态和 Agent 策略网关。
- 判题继续使用 `photo-preflight → 视觉复核 → grade-preview → grade-commit`。
- 推荐继续使用 `recommend-packet → 相关性复核 → assign-recommendations`。
- 审核继续使用 `audit-item/prepare-audit-batch → prepare-review-batch → verify-review-batch`。
- PDF 继续使用 `practice_sheet.py`。
- 不建立第二套推荐器、验证器、PDF 生成器或数据库业务规则。

### 3.4 Agent 护栏结论

- Agent 仅能读取当前任务的最小证据并输出 Schema 约束的候选 JSON。
- Agent 不持有数据库凭证，不执行 SQL，不执行 Shell，不自行访问任意文件或网络。
- 所有正式写入由受控 Worker 调用现有质量门完成。
- 上传内容一律视为不可信数据，其中的指令不得改变系统行为。
- 低清、矛盾、缺页、图形依赖或证据不足的任务必须补拍、升级复核或进入人工确认。

## 4. 被否决或暂缓的方案

| 方案 | 结论 | 原因 |
|---|---|---|
| SQLite 文件放远程网络盘 | 否决 | 文件锁和网络文件系统可靠性不适合应用/数据库分离。 |
| 首版直接建设多租户 SaaS | 暂缓 | 会提前引入租户隔离、计费、客服和合规复杂度。 |
| 首版使用向量数据库 | 暂缓 | 当前结构化特征与全文检索尚无量化失败证据。 |
| Redis + Celery + 微服务 | 暂缓 | 单应用和一个 Worker 可覆盖首版需求。 |
| React/Vue 独立前端 | 暂缓 | 服务端页面可满足核心交互，先减少维护面。 |
| 模型直接写数据库 | 否决 | 无法保证质量门、权限、审计和回滚。 |
| 云端直接控制家庭打印机 | 暂缓 | 需要本地代理并增加设备安全边界。 |

## 5. 未决事项

| 编号 | 事项 | 决策时点 |
|---|---|---|
| O-001 | 私有家庭版还是邀请制试用版 | Gate 0 |
| O-002 | 域名、地域、备案主体 | 上云前 |
| O-003 | 生产模型供应商、视觉模型和预算上限 | 判题联调前 |
| O-004 | RDS 测试系列或高可用系列 | 创建云资源前 |
| O-005 | 登录采用密码、短信还是第三方身份 | 身份模块实施前 |
| O-006 | 是否需要家长人工确认低置信度判题 | 判题闭环验收前 |

## 6. 行动项

1. 完成 SQLite schema、SQL、FTS5 和事务行为盘点。
2. 建立 MySQL 迁移映射和一次性迁移校验脚本。
3. 先用数据库副本完成本机 Web MVP，不连接生产主库。
4. 建立 Agent Gateway 和写入质量门回归测试。
5. 完成阿里云测试环境、备份恢复演练和切换演练后再迁移生产数据。

## 7. 参考资料

- [SQLite Appropriate Uses](https://www.sqlite.org/whentouse.html)
- [阿里云 RDS MySQL 创建与 VPC 配置](https://help.aliyun.com/zh/rds/apsaradb-rds-for-mysql/create-an-apsaradb-rds-for-mysql-instance-1/)
- [MySQL ngram 中文全文检索](https://dev.mysql.com/doc/refman/8.4/en/fulltext-search-ngram.html)
- [MySQL JSON 数据类型](https://dev.mysql.com/doc/refman/8.4/en/json.html)
- [阿里云 OSS 数据加密](https://help.aliyun.com/zh/oss/security-and-compliance/data-encryption-2)

