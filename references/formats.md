# 已验证的存储与转换边界

2026-10-04 实测：iPhone 15、iOS 17.0、TrollStore、Aemulo 2.0、Filza WebDAV。这是已验证组合，不是其他版本的保证。

## 定位依据

- App bundle ID：`com.amywhile.Aemulo`。
- 共享应用组：`group.amywhile.aemulo`。
- 元数据：容器根的 `.com.apple.mobile_container_manager.metadata.plist`，读取 `MCMMetadataIdentifier`。
- 数据库：共享组根目录的 `TagDatabase.sqlite3`。其他版本可能不同，只在匹配的 Aemulo 容器内递归找；多个数据库时不随意挑选。
- 实测 Filza WebDAV `/` 对应设备文件系统根，优先 `/private/var/...`；可访问范围由实际权限决定。

## 默认交付位置

通过同一容器元数据机制匹配 `group.com.apple.FileProvider.LocalStorage`，在该组根目录查找已存在的 `File Provider Storage/`。这是「我的 iPhone」根目录的候选映射；必须核实设备实际存在，不能在未找到时创建同名目录假装交付。不使用其他组里同名的目录。

此映射有 [Apple 开发者论坛的模拟器讨论](https://developer.apple.com/forums/thread/846994) 作为线索；它不能替代真机验证。当前技能更新通过模拟 WebDAV 服务验证了定位和上传，尚未验证这条新流程在真机 Files 界面的可见性。

## SQLite

已见表：`GenericATag`、`GenericBTag`、`GenericFTag`、`MiFareTagTable`、`EMVTagTable`、`NDEFTagTable`、`AmiiboTagTable`。导出器保存所有用户表。

| MiFareTagTable 字段 | 原类型 | 迁移处理 |
|---|---|---|
| uuid | TEXT | 归档 id、来源标识 |
| isFavourite | INTEGER | 收藏，须为 0 或 1 |
| dateAdded | TEXT | 原样存入 details，不推测时区 |
| name | TEXT / NULL | 有效名称原样保留；空名称提供默认值 |
| uid | BLOB | Base64 原字节，不反转 |
| sak | INTEGER | 0…255，两位十六进制及原整数 |
| atqa | INTEGER | 0…65535，四位十六进制及原整数；不推断无线字节序 |
| family | TEXT | 原样保留 |
| RF_PROTOCOL | INTEGER | 原样保留，不猜协议名称 |

此表没有完整扇区或密钥字段，不能据此推断卡本身没有其他内容。

## 输出

- 原始 SQLite 及存在的 WAL / SHM；不在原件上 checkpoint。
- `aemulo-records.json`：格式 `aemulo.sqlite-export.v1`，`tables` 数组保存 name、columns、rows。BLOB 为 `{ "encoding": "base64", "data": "..." }`，其他值保留 JSON 类型。
- `manifest.json`：源数据库路径、文件大小、各表计数及完整性结果，不记录 URL 密码。
- `.nfccards`：`format=app.nfccardwallet.archive`、`version=1`，时间为 Unix 毫秒；technology 为 `mifare`、records 为空，不伪造 NDEF。源记录 JSON 存入 details 保留额外字段。超出目标大小/文本限制时停止，不截断。

## 官方参考

- [Filza WebDAV 使用说明](https://www.tigisoftware.com/default/?page_id=177)
- [TrollStore 沙盒说明](https://github.com/opa334/TrollStore#unsandboxing)
- [SQLite WAL 文件属于持久状态](https://sqlite.org/wal.html)
