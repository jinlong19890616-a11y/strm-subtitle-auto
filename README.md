# STRM 中文字幕自动处理

MoviePilot 插件仓库。

插件目录：`plugins/strmsubtitleauto`
插件名称：`STRM 中文字幕自动处理`
插件 ID：`strmsubtitleauto`

监听 MoviePilot 新入库事件，等待 Symedia 在 `/mnt/strm` 生成对应 STRM，检查 STRM 同目录是否已有中文字幕；没有时搜索并写入外挂字幕。

不会在启动时扫描全部 STRM，也不修改 `.strm` 内容。
