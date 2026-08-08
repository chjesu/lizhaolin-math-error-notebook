# 跨学科试卷自动入库服务

`services/exam_ingest_watcher.py` 监听 `C:\Users\Administrator\Downloads`，把新增 DOCX 试卷按学科送入数学、物理或化学错题本已有的权威导入流程。它只负责编排，不直接访问任何 SQLite 数据库。

## 安全规则

- 先等待文件大小和修改时间连续稳定，避免处理尚未下载完成的文件。
- 先按文件名分类；文件名不足时只读取 DOCX 正文文本辅助判断。
- 只自动处理 DOCX。PDF、DOC、DOCM 会留在下载目录并标记为 `manual_format_required`，避免把“登记来源”误当成“题目已入库”。
- 每份试卷先进入目标项目的隔离暂存目录，再调用该项目现有转换器/导入器和质量门。
- 数学要求批次导入无失败、无阻塞且恰好处理一个来源；物理/化学要求题目数大于零、无解析失效、每题有解析，物理还要求无转换警告。
- 导入后再次运行目标题库的 `bank-info` 完整性检查。
- 只有结果为 `imported` 或 `already_imported` 才移动源文件；分类不明、质量阻塞或导入失败均保留原文件。
- 成功文件归档到 `E:\李兆霖错题本\已入库试卷\学科\年\月`，文件名加入日期和 SHA-256 前缀，绝不覆盖同名文件。
- 暂时性命令失败最多重试 3 次；质量门失败不会盲目重试。

## 使用

在项目根目录运行：

```powershell
# 环境检查
powershell -ExecutionPolicy Bypass -File scripts\exam_ingest_watcher.ps1 -Action doctor

# 后台启动（首次只为当前下载文件建立基线，以后处理新增文件）
powershell -ExecutionPolicy Bypass -File scripts\exam_ingest_watcher.ps1 -Action start

# 状态 / 停止
powershell -ExecutionPolicy Bypass -File scripts\exam_ingest_watcher.ps1 -Action status
powershell -ExecutionPolicy Bypass -File scripts\exam_ingest_watcher.ps1 -Action stop
```

解析器或源文件修复后，可用 Python 权威入口显式重试指定的终结状态文件；
命令只接受下载目录中的具体路径，不会批量放宽质量门：

```powershell
python -X utf8 -B services\exam_ingest_watcher.py retry "C:\Users\Administrator\Downloads\待重试数学试卷.docx"
```

仅在明确希望把下载目录现有文件也纳入处理时，使用 `-IncludeExisting`。后台日志、状态和事件记录位于 `data/exam-ingest-watcher/`；这是运行数据，不提交 Git。

配置文件是 `config/exam-ingest-watcher.json`。修改三个项目路径、归档根目录或轮询参数时只改这里，不复制脚本或创建第二套导入流程。
