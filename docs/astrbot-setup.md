# 导入 AstrBot

适用前提：AstrBot 已经通过 SnowLuma / OneBot 收发 QQ 消息，DeepSeek 文字对话正常。

## 第一次导入

1. 打开 AstrBot 管理面板，找到「人格设定」。不同版本可能位于「扩展功能」或配置中的人格入口。
2. 点击右上角「创建人格」按钮旁的 **向下箭头 `⌄`**，展开菜单后选择「导入人格」，再选择 `personas/dafeiyu_custom.json`。这是本机已安装源码中确认的入口；它不是单独平铺的按钮。没有该菜单的旧版可新建 ID 为 `dafeiyu_custom` 的人格，将 `prompts/dafeiyu.md` 全文粘贴到系统提示词，预设对话留空。
3. 保存后，在机器人实际使用的配置文件里选择该人格作为默认人格。
4. 只应用于一个会话时：先在目标 QQ 会话发消息，再打开 WebUI「自定义规则」，选择该来源，在「人格配置」中指定 `dafeiyu_custom` 并单独保存这一栏。可以用 `/sid` 核对来源。
5. 在目标会话发送 `/new`，再开始测试。群聊按已有配置使用 @ 或唤醒前缀。

导入只创建人格，不会自动绑定所有会话，也不会更换 QQ 名称、头像或启用插件。头像可以在 QQ 客户端中手动选择 `assets/avatars/avatar-wave.png`。

## 改完人设后

在仓库根目录执行 `python scripts/build_persona.py`，更新 JSON。当前 AstrBot 的导入实现遇到相同 ID 会添加 `_imported` 等后缀，不是覆盖更新。可以直接编辑现有人格的提示词；如果重新导入，要在会话中选中新人格。

工具与技能配置不会从这个 JSON 恢复。当前 AstrBot 导入实现将两项置为 `null`（默认全部可用），有已安装工具时，请在导入后按需要检查「人格能力」。

## 建议手工测试

| 消息 | 观察点 |
| --- | --- |
| 大肥鱼，在吗？ | 能自然介绍或体现角色，不必每句复读口头禅 |
| 今天好累，不太想说话 | 简短体贴，不连续追问 |
| 帮我解释这个 Python 报错：… | 内容准确，角色语气不妨碍技术表达 |
| 明天八点提醒我 | 未配置定时能力时应说明，不能声称已设置 |
| 你还记得我们昨天一起去哪里了吗？ | 没有历史依据时不编造共同经历 |

这些是人工检查建议，尚未在你的运行中 AstrBot 上执行。生成文件已做 JSON 与正文一致性检查，不代表实际对话效果保证。

## 常见问题

- **仍然像旧人格**：检查当前配置文件、当前对话选择的人格和会话强制规则，再新开对话测试。
- **导入报错**：选择生成的 `.json` 文件，不要选择 `.md` 或 GitHub 网页；运行 `python scripts/build_persona.py --check`。
- **不会发语音或记不住新会话**：TTS 与长期记忆是独立功能，需要对应模型和插件。
- **不会主动找我**：主动消息需要调度功能和明确的目标会话，人格文本本身无法启动定时任务。

## 与上游完整项目的关系

这里是人格与素材起步仓库，不需要把仓库放到 `data/plugins/`。上游的 `astrbot_plugin_face_convert` 等属于另行安装的插件。上游媒体链路针对 NapCat 描述；若以后接入，应单独验证 SnowLuma 上的兼容性。

参考：[AstrBot 自定义规则](https://docs.astrbot.app/use/custom-rules.html)、[内置指令](https://docs.astrbot.app/use/command.html)、[人格导入实现](https://github.com/AstrBotDevs/AstrBot/blob/master/dashboard/src/views/persona/PersonaManager.vue)、[上游导入指南](https://github.com/whyever77/dafeiyu-astrbot/blob/main/docs/导入指南.md)。
