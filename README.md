# MoviePilot STRM Subtitle Auto

轻量级 MoviePilot 插件：监听新入库事件，等待 Symedia 在 `/mnt/strm` 生成对应的 `.strm`，检查 STRM 同目录是否已有中文字幕；没有时调用在线字幕源并把外挂字幕写到 STRM 旁边。

## 设计目标

- 不在 MoviePilot 启动时扫描 20 多万条 STRM。
- 不递归监听远程/FUSE STRM 根目录。
- 只处理新的 `TransferComplete` 事件。
- 等待单个媒体对应的 STRM 生成，默认最多 180 秒。
- 字幕写入 STRM 同目录，不修改 `.strm` 内容，不下载真实视频。
- 复用海拉鲁字幕搜索、下载、语言识别和写入代码。

## 部署

将仓库目录作为 MoviePilot 插件安装，插件类名为 `StrmSubtitleAuto`，插件 ID 为 `strmsubtitleauto`。

建议配置：

- 启用插件
- 开启“STRM 生成后自动搜索中文字幕”
- STRM 目录填写 `/mnt/strm`
- 等待 STRM 生成秒数按 Symedia 延迟调整，默认 180 秒
- 配置在线字幕源，例如 SubHD、Zimuku
- 不开启旧的“额外 STRM 实时监控”

处理链路：

```text
MoviePilot TransferComplete
  -> worker 等待 /mnt/strm 下对应 STRM
  -> 检查 STRM 同目录中文字幕
  -> 在线搜索/下载
  -> 写入 xxx.zh-Hans.srt / xxx.chs.srt 等外挂字幕
```

## 当前限制

事件中的媒体信息用于匹配 STRM 文件名、目录、年份、季集；如果 Symedia 的命名与 MoviePilot 媒体标题差异很大，需要继续增强 TMDB ID/目录映射规则。

## 开发

源码和测试均位于 Mac 资料盘，避免占用系统盘：

```bash
python3 -m compileall -q strmsubtitleauto
python3 -m unittest discover -s tests -v
```
