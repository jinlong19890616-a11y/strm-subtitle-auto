# STRM 中文字幕自动处理

MoviePilot 插件：监听新入库事件，等待 Symedia 在 `/mnt/strm` 生成对应 `.strm`，检查 STRM 同目录是否已有中文字幕；没有时搜索并写入外挂字幕。

## MoviePilot 配置

- 插件仓库：`https://github.com/jinlong19890616-a11y/strm-subtitle-auto`
- 启用插件
- 开启 `STRM 生成后自动搜索中文字幕`
- STRM 目录：`/mnt/strm`
- 等待 STRM 生成：默认 180 秒
- 配置在线字幕源

插件不会在启动时扫描全部 STRM，也不修改 `.strm` 内容。字幕写到 STRM 旁边。

## 开发测试

```bash
python3 -m compileall -q .
PYTHONPATH=. python3 -m unittest discover -s tests -v
```
