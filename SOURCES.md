# 来源与改编说明

## 表情包插件与资源

- 相关插件：[anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)。
- 起步图库：[官方表情包1号](https://github.com/anka-afk/astrbot-meme-pack-official-01)，其 `manifest.json` 声明版本 1.0.0、许可 CC-BY-NC-SA-4.0。

## 上游项目

- 项目：[whyever77/dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot)
- 本次固定提交：`7ee692a7479aa2774fa41ff54390ad8bef46d8d3`
- 上游 MIT 许可证原文：[third_party/dafeiyu-astrbot/LICENSE](third_party/dafeiyu-astrbot/LICENSE)
- 上游人格原文备份：[persona-original.md](third_party/dafeiyu-astrbot/persona-original.md)
- 上游素材说明备份：[avatars-README.md](third_party/dafeiyu-astrbot/avatars-README.md)
- 每个下载文件的固定链接、字节数、SHA-256：[sources.json](third_party/dafeiyu-astrbot/sources.json)

项目代码使用根目录的 MIT LICENSE，上游材料保留其贡献者版权及许可。上游素材说明称 v3 为用户提供的 ChatGPT 生成图，用于机器人头像、立绘及社区二创参考；本仓库使用该版本素材。

## 图片映射

所有图片均来自上游 `assets/avatars/v3-20261025/`，下载后只重命名，没有修改图像内容。

| 本地文件 | 上游文件 | 用途建议 |
| --- | --- | --- |
| `assets/avatars/avatar-wave.png` | `01_招手微笑.png` | 默认 QQ 头像 |
| `assets/avatars/portrait-whale.png` | `03_抱鲸鱼.png` | 备用头像或角色展示 |
| `assets/avatars/standing.png` | `05_站姿.png` | 角色全身参考 |
| `assets/avatars/evening.png` | `14_夜间陪伴.png` | 氛围参考 |

## 人格改编

`prompts/dafeiyu.md` 结合维护者提供的人格草稿整理、改编，导出的 `personas/dafeiyu_custom.json` 使用同一正文。早期参考的上游人格保留在 `third_party/` 中供对照。

这些文件作为角色示例保留。项目目前专注 RSI 的学习、执行、评估与版本管理。

## 其他形象检索入口

这些链接用于浏览社区中的其他二创版本，本仓库未从这些站点下载或再分发图片：

- [鲸鱼娘社区二创报道](https://www.vgover.com/news/227900)
- [梗鲸 · DeepSeek 娘标签](https://aigengtu.com/en/tag/deepseek-chan)

“鲸鱼娘 / 大肥鱼”是社区二创称呼。不同作者画法不同，本仓库选用的是上述上游项目的 v3 版本，不将其声称为唯一原设或官方形象。
