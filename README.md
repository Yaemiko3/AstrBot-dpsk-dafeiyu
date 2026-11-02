# AstrBot · DeepSeek 鲸鱼娘

给自己的 QQ 机器人准备的人格与形象素材仓库，适用于已接通 DeepSeek 的 AstrBot。当前使用「大肥鱼」妹妹版群聊人格：被 @ 必回，普通闲聊每 3～5 轮配一张表情，使用现有 SnowLuma / OneBot 连接。

<img src="assets/avatars/avatar-wave.png" alt="大肥鱼招手头像" width="320">

这是个人二创配置，非 DeepSeek 官方角色或官方产品。人格以用户提供的妹妹版为主稿，图片选自 [whyever77/dafeiyu-astrbot](https://github.com/whyever77/dafeiyu-astrbot) 公开的 v3 素材；来源与许可见 [SOURCES.md](SOURCES.md)。

## 立即使用

1. 打开 AstrBot 管理面板，进入「人格设定」，点击右上角「创建人格」旁的 **向下箭头 `⌄`**，在菜单里选择「导入人格」。部分版本入口位于「扩展功能」下。
2. 选择本仓库的 **[personas/dafeiyu_custom.json](personas/dafeiyu_custom.json)**。
3. 在机器人实际使用的配置文件中，把默认人格设为 `dafeiyu_custom`；或者通过「自定义规则」只指定给某个 QQ 私聊或群聊。
4. 在目标 QQ 会话中发送 `/new`，再发送「大肥鱼，今天有点累」进行测试。改过唤醒前缀时使用对应前缀。
5. 想更换 QQ 头像，可在 QQ 客户端手动选择 [avatar-wave.png](assets/avatars/avatar-wave.png)。人格导入不会自动更换账号头像。

详细步骤与排查方法见 [导入说明](docs/astrbot-setup.md)。没有导入按钮的版本，可新建人格并将 [人设正文](prompts/dafeiyu.md) 全文粘贴到系统提示词。

## 聊天表情包

使用「表情包管理器」，按 [配置步骤](docs/meme-setup.md) 导入并绑定大肥鱼。自定义图片放在 `assets/memes/`。

## 形象素材

| 招手头像 | 抱鲸鱼 | 全身站姿 | 夜间陪伴 |
| --- | --- | --- | --- |
| ![招手](assets/avatars/avatar-wave.png) | ![抱鲸鱼](assets/avatars/portrait-whale.png) | ![站姿](assets/avatars/standing.png) | ![夜间](assets/avatars/evening.png) |

上游将这批素材标注为用户提供的 ChatGPT 生成图。这里保留原图，未裁剪或修改。它们是社区二创版本，不代表唯一或最初的鲸鱼娘设计。

## 修改自己的人格

人格自动迭代项目已放在 [Persona RSI](plugins/astrbot_plugin_persona_rsi/README.md)：固定核心设定，按真实对话提出聊天习惯修改，通过审查和回复对照后写入 AstrBot 人格，支持版本查看与回退。

日常编辑 **`prompts/dafeiyu.md`**，随后在仓库目录执行：

```powershell
python scripts/build_persona.py
```

脚本使用 Python 3 标准库，不需要安装依赖，会生成 `personas/dafeiyu_custom.json`。检查导出是否与正文一致：

```powershell
python scripts/build_persona.py --check
```

在 AstrBot 中编辑现有人格，或重新导入生成的 JSON。当前 AstrBot 遇到同名导入会创建带后缀的新人格，重新导入后应确认会话选中了最新版本。Git 提交不会自动同步到 AstrBot。

## 目录

```text
assets/avatars/                 4 张形象素材
assets/memes/                   自定义表情素材
prompts/dafeiyu.md              日常维护的人格正文
personas/dafeiyu_custom.json    可导入 AstrBot 的导出文件
scripts/build_persona.py        导出 / 一致性检查工具
docs/astrbot-setup.md           接入与测试说明
docs/meme-setup.md              表情包导入与绑定
third_party/dafeiyu-astrbot/    上游原文、许可与来源哈希
SOURCES.md                     二创说明与素材出处
```

## 当前能力

[Persona RSI](plugins/astrbot_plugin_persona_rsi/README.md) 将人格习惯、消息运行参数和行为代码纳入同一自改进流程。结合实际回复反馈与群友交流提出改动，执行功能检查和真人回复回放，统一生效与回退；固定妹妹身份等核心设定。

LivingMemory 每 10 轮整理对话时会提取有依据的互动反馈；明确的称呼、语气偏好和重要纠正也可由人格主动记忆。再次遇到相关话题时，结合召回的经验调整回应。后台复盘模板见 [memory_system_prompt_with_persona.txt](prompts/livingmemory/memory_system_prompt_with_persona.txt)，部署在 LivingMemory 数据目录的同名 `prompts/` 文件中。

这一版负责角色身份、聊天语气、私聊与群聊分寸。需要 AstrBot 已配置对话模型。长期记忆、主动发消息、TTS、图片理解和设备操作需要各自的插件、模型或工具；人格文本不能自行启用这些功能。

现有 `.git` 和根目录 MIT `LICENSE` 已保留。上游贡献者的许可位于 [third_party/dafeiyu-astrbot/LICENSE](third_party/dafeiyu-astrbot/LICENSE)。不要把 API Key、QQ 登录信息、AstrBot 数据库或私人聊天记录提交到仓库。
