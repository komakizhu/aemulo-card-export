# Aemulo 卡片导出

把 Aemulo 里存的 NFC 卡片导出来，转成 NFC 卡包能导入的 `.nfccards` 文件。

这是给 Codex 用的技能。你连好 Filza，剩下的查找、备份、转换和传文件交给它。Aemulo 里的原卡不会被修改。

## 安装

```bash
git clone https://github.com/komakizhu/aemulo-card-export.git ~/.codex/skills/aemulo-card-export
```

需要 Python 3，无需额外安装 Python 库。如果之前装过，更新已有目录即可。

## 使用

在 Codex 中开一个新会话，输入：

> 用 $aemulo-card-export 导出我的 Aemulo 卡片。

手机和电脑连同一个网络，在 **Filza → 设置** 中开启 WebDAV，把显示的地址发给 Codex，截图也行。

技能会先查 Aemulo 的默认存储位置，找不到再查其他位置。转换后的文件默认放在 **文件 → 我的 iPhone** 根目录，原始备份留在电脑上。完成后可以关闭 WebDAV。

在 NFC 卡包里打开 **工具 → 导入卡片或备份**，选择生成的 `.nfccards` 文件即可。

## 目前的限制

导出和转换已在 iPhone 15、iOS 17.0、TrollStore、Aemulo 2.0 上验证。目前能转换 `MiFareTagTable` 中的卡片；遇到其他类型会停下来说明，不会漏掉后直接报成功。

保存到「我的 iPhone」根目录的流程已通过模拟测试，真机上能否在 Files 中正常显示还需要确认。

导入保存的是已有卡片信息，不会让目标 App 自动具备模拟刷卡能力，也不能当作完整扇区备份。

脚本用法见 [SKILL.md](SKILL.md)，数据格式见 [references/formats.md](references/formats.md)。
