# Aemulo 卡片导出

一个 Codex Skill：引导连接 Filza，定位 Aemulo 卡片数据库，导出并转换成 NFC 卡包的 `.nfccards` 文件，保存到「我的 iPhone」根目录。

## 安装

在未安装此技能的电脑上：

```bash
git clone https://github.com/komakizhu/aemulo-card-export.git ~/.codex/skills/aemulo-card-export
```

若目录已存在，保留本地修改，不直接覆盖。让 Codex 重新加载技能或开启新会话后使用：

> 用 $aemulo-card-export 导出我的 Aemulo 卡片。

按提示在 Filza 中启用 WebDAV，并提供其显示的连接地址。连通后，代理自动完成定位、备份、转换、上传与读回校验。操作完成后可关闭 WebDAV。

## 行为与支持范围

- 优先检查 Aemulo 共享应用组的默认数据库位置，仅未找到时搜索候选位置；不依赖固定容器 UUID。
- 源数据库只读，保留数据库及存在的 WAL/SHM，在本地副本验证完整性。
- 原始备份保留全部用户表；当前自动转换支持 `MiFareTagTable`。其他表有非空记录时明确停止，不静默遗漏卡片。
- 保留 UID、UUID、收藏、SAK、ATQA、原始协议值和源记录；不推测原始日期时区。
- 结果保存到已识别的 Files 本地存储根目录，不覆盖同名异内容文件。
- 文件导入不代表 NFC 模拟功能可用，也不等于完整扇区备份。

已在 iPhone 15 / iOS 17.0 / TrollStore / Aemulo 2.0 上验证数据库导出及转换。新版一键流程的「我的 iPhone」根目录交付通过模拟 WebDAV 测试，尚待真机 Files 界面可见性验证。

## 独立运行

需要 Python 3，仅使用标准库。将示例地址替换成手机实际显示的 Filza 地址，并选择一个不存在的本地输出目录：

```bash
python3 scripts/aemulo_export.py run --url http://PHONE_IP:PORT --out ./backups/new-export
```

`export` 可单独备份，`convert` 可单独转换。使用 `--help` 查看参数。认证可通过 `FILZA_USER`、`FILZA_PASSWORD` 环境变量提供；不要把密码提交到仓库。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -v
```

测试仅使用虚构数据和本地模拟服务。流程规则见 [SKILL.md](SKILL.md)，格式说明见 [references/formats.md](references/formats.md)。
