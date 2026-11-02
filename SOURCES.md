# 来源与改编说明

整理日期：2026-10-31。

## 表情包插件与资源

- 插件：[anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager)，本机核对版本 5.2.11。
- 起步图库：[官方表情包1号](https://github.com/anka-afk/astrbot-meme-pack-official-01)，其 `manifest.json` 声明版本 1.0.0、许可 CC-BY-NC-SA-4.0。

## 上游项目

- 项目：[whyever77/dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot)
- 本次固定提交：`7ee692a7479aa2774fa41ff54390ad8bef46d8d3`
- 上游 MIT 许可证原文：[third_party/dafeiyu-astrbot/LICENSE](third_party/dafeiyu-astrbot/LICENSE)
- 上游人格原文备份：[persona-original.md](third_party/dafeiyu-astrbot/persona-original.md)
- 上游素材说明备份：[avatars-README.md](third_party/dafeiyu-astrbot/avatars-README.md)
- 每个下载文件的固定链接、字节数、SHA-256：[sources.json](third_party/dafeiyu-astrbot/sources.json)

本仓库保留用户已有的根目录 LICENSE。上游材料同时保留其贡献者版权及 MIT 许可。上游素材说明称 v3 为用户提供的 ChatGPT 生成图，用于 QQ 机器人头像、立绘及社区二创参考；本次未采用带旧画师条款的 v1/v2 素材，也未将图片标记为 DeepSeek 官方授权素材。

## 图片映射

所有图片均来自上游 `assets/avatars/v3-20261025/`，下载后只重命名，没有修改图像内容。

| 本地文件 | 上游文件 | 用途建议 |
| --- | --- | --- |
| `assets/avatars/avatar-wave.png` | `01_招手微笑.png` | 默认 QQ 头像 |
| `assets/avatars/portrait-whale.png` | `03_抱鲸鱼.png` | 备用头像或角色展示 |
| `assets/avatars/standing.png` | `05_站姿.png` | 角色全身参考 |
| `assets/avatars/evening.png` | `14_夜间陪伴.png` | 氛围参考 |

## 人格改编

`prompts/dafeiyu.md` 以用户提供的《大肥鱼-妹妹人设.md》为主稿，适配当前 AstrBot。导出的 `personas/dafeiyu_custom.json` 使用同一正文；早期采用的上游人格保留在 `third_party/` 中供对照。

主要调整：

- 保留妹妹版的黏人、撒娇、护短和认真时的反差。
- 吸收用户提供的《人设.doc》中的群聊语感、引用判断、搜索核对和接梗示例；被怼优先顶回去，闲聊允许装傻和明显荒诞的玩笑答案。
- 被 @ 必回，夜间也回应；普通闲聊每 3～5 轮配一张表情。
- 放宽固定字数，适配现有搜索、网页读取、表情包和长期记忆工具。
- 示例仅作为系统提示词中的风格参考，`begin_dialogs` 留空，避免当成真实共同经历。

## 其他形象检索入口

这些链接用于浏览社区中的其他二创版本，本仓库未从这些站点下载或再分发图片：

- [鲸鱼娘社区二创报道](https://www.vgover.com/news/227900)
- [梗鲸 · DeepSeek 娘标签](https://aigengtu.com/en/tag/deepseek-chan)

“鲸鱼娘 / 大肥鱼”是社区二创称呼。不同作者画法不同，本仓库选用的是上述上游项目的 v3 版本，不将其声称为唯一原设或官方形象。
