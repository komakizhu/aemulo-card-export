---
name: aemulo-card-export
description: 引导连接 Filza，自动定位并导出 Aemulo 的全部已存 NFC 卡片，转换为 .nfccards 并保存到「我的 iPhone」根目录。默认位置优先，未找到才搜索候选位置。适用于卡片备份或迁移，不用于开发模拟驱动。
---

# Aemulo 卡片导出

默认任务是一条完整流水线：引导连接 → 自动定位全部卡片 → 备份并转换为 `.nfccards` → 保存到「文件 → 浏览 → 我的 iPhone」根目录。不要在原始数据库导出后停下，不要让用户手动找容器或另发一次转换请求。用户明确只要原始备份、仅分析或另指定目的地时遵循其范围。

用户说的“MC cards”等口述格式按这里的 `.nfccards` 理解。文件传入手机不等于已经导入 App，更不等于可以模拟刷卡。

## 接通手机

- 利用已知连接和已有备份，不要求重复授权或重读实体卡。
- TrollStore 应用权限不会自动赋予 USB 文件共享服务；USB 拒绝不能推断 Filza 也无权读取。
- 首先确认有可用的 Filza 通信通道。优先复用用户已经开放的 WebDAV 或其他可读写文件通道；不要因为 USB 文件共享失败就反复尝试同一路径。
- 没有可用通道时，明确引导：“打开 Filza → 设置 → 启用 WebDAV 服务，手机和 Mac 连同一网络，保持 Filza 打开，把显示的地址发来。”截图中地址可直接读取，不要求重打一遍。用 GET / PROPFIND 验证连接后自动继续，不再请求开始导出的许可。
- 不猜 IP 或扫描整个局域网。不安装调试桥、挂载 DDI 或修改系统。其他已授权通道可按同样的路径发现、源只读及交付校验规则执行；附带脚本实现的是 WebDAV。
- 使用后台 HTTP/WebDAV；不必打开窗口。401 时请求服务认证信息，不关闭认证。脚本从 `FILZA_USER` / `FILZA_PASSWORD` 环境变量读取认证，不写入技能或备份。

## 默认一键流程

运行 Python 3 标准库脚本，脚本路径以本技能目录为基准：

```bash
python3 scripts/aemulo_export.py run --url http://PHONE_IP:PORT --out /absolute/path/to/new-backup-folder
```

代理自行选择带时间的本地备份目录，不为常规命名询问用户。目录必须是新的；重试保留之前的部分备份。手机源数据只用 GET / PROPFIND 读取，交付仅新建结果文件：

1. 在 `/private/var/mobile/Containers/Shared/AppGroup/` 枚举容器元数据，匹配 `MCMMetadataIdentifier = group.amywhile.aemulo`。不照搬历史 UUID。
2. 先直接检查匹配组根的 `TagDatabase.sqlite3`，命中立即使用，不每次递归搜索。默认位置未命中才查此组子目录、`com.amywhile.Aemulo` 数据容器及旧版 `/private/var/mobile/Library/Application Support/Aemulo/`。候选位置是搜索线索，不保证存在；多个结果时核实后选择，不任意合并重复数据库。
3. 保存数据库及存在的 `-wal`、`-shm`。读取两次并核对清单、属性和全部字节；若持续变化，停止，让用户结束 Aemulo 当前活动后再导出。
4. 在本地临时副本检查 SQLite 完整性，导出每张表全部行、列及 BLOB，生成 `aemulo-records.json`、计数清单及 `.nfccards`。不限定三张，不只导出收藏卡或当前选中卡。
5. 匹配共享组 `group.com.apple.FileProvider.LocalStorage`，确认其已有 `File Provider Storage/` 目录，将最终 `.nfccards` 直接写入该目录根部，读回比较完整字节。不放进 LocalSend、Downloads、iCloud Drive 或新的子文件夹。

找不到容器或文件时报告实际结果，再按版本调查；不把未找到当作没有卡片。不无目的读取其他应用数据。版本、表结构和格式见 [references/formats.md](references/formats.md)。

## 转换边界与异常恢复

`run` 已自动转换并传回手机。若用户只要本地转换，或已有备份无需重读手机，单独运行：

```bash
python3 scripts/aemulo_export.py convert --input /absolute/path/to/backup/aemulo-records.json --output /absolute/path/to/Aemulo-cards.nfccards
```

- 转换器支持已验证的 `MiFareTagTable`。其他表若有非空记录，停止转换并报告，不静默丢弃；原始备份仍可交付。
- 仅原卡未命名时使用“卡片 1、2…”；保留 UUID、UID、收藏、原始日期字符串、SAK、ATQA、RF_PROTOCOL 和完整源记录。不猜原始日期时区，归档时间使用转换时间。
- 新版本读取 `rfParameters`；相同信息也存入旧版可识别的 `details`。不把 RF_PROTOCOL 数字猜成其他枚举。旧版重新导出可能丢弃未知顶层字段，必须保留数据库和无损 JSON。
- 目标源码可读时，用实际 `CardArchive` 解码器验证生成文件及往返一致性。不能为迁就转换文件修改 App 源码。
- 「我的 iPhone」不是设备文件系统 `/`，也不是 Filza 首页。必须匹配 Files 的 LocalStorage 组并确认真实目录；不复制历史 UUID。找不到时保留本地成果，核实 Files 的映射或请用户在 Files 选择「我的 iPhone」保存，不悄悄换到 LocalSend 或其他位置。
- 成果文件名默认 `Aemulo-cards-时间.nfccards`。同名同内容可复用，异内容不覆盖；选新文件名即可。上传后读回验证。传输中断或结果不明时先读取目标，不盲目覆盖重试。不为传文件向别的任务发消息。
- NFC 卡包 0.1.4 的导入路径：工具 → 导入卡片或备份 → 选择 `.nfccards` → 核对数量 → 导入。无控制入口则给这一必要操作，不声称已经导入。

完成时先告诉用户：“已导出 N 张，文件位于「我的 iPhone → 文件名.nfccards」。”只有校验成功才这样表述；仅写入底层目录尚未确认 Files 界面可见时，注明读回通过、界面可见性待确认。原始数据库和无损 JSON 留作本地备份，不挤进手机根目录。不在聊天、测试样例或技能中复制真实卡号、记录、IP、设备 UUID 或密码。提醒用户可关闭本次开启的 WebDAV。

导出身份参数不等于完整扇区备份；导入也不代表模拟驱动可用。不修改源数据库、停止系统 NFC 服务、移除原卡或扩大到破解卡片。
