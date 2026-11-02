# RSI 接入指南

本指南只配置自我迭代能力。先确认 AstrBot 已经能够正常收发消息并调用模型。

## 安装插件

在仓库根目录执行：

```powershell
python scripts/deploy_persona_rsi.py --core "<AstrBot核心目录>" --enable --provider "<已配置的模型提供商ID>"
```

`--core` 指向包含 `astrbot/` 和 `data/` 的核心目录；`--provider` 使用 AstrBot 中实际配置的提供商 ID。

也可以将 `plugins/astrbot_plugin_persona_rsi/` 复制到 AstrBot 的 `data/plugins/` 中，再在插件配置页面启用。

安装后重载插件或重启 AstrBot。

## 选择学习对象

在插件配置中填写：

| 配置项 | 用途 |
| --- | --- |
| `enabled` | 启用 RSI |
| `persona_id` | 机器人实际使用的目标人格 ID |
| `provider_id` | 提案与回复回放使用的模型提供商 ID |
| `review_provider_id` | 审查模型；留空复用上述模型 |
| `allowed_sessions` | 学习来源的会话列表；留空使用该人格的会话 |

人格名称、模型和连接端按自己的部署选择。当前核心约束围绕大肥鱼角色维护，具体实现见 [policy.py](../plugins/astrbot_plugin_persona_rsi/policy.py)。

## 接入群友交流

旁观学习使用 LivingMemory 已采集的原始群聊：

1. 安装并启用 LivingMemory 的全量群聊采集。
2. 开启 RSI 的 `group_learning_enabled`。
3. 按需要设置 `group_history_days` 和 `excluded_bot_ids`。

所有已接入群的样本进入同一个学习流程。群号用于理解上下文和核对证据，学到的共同习惯统一生效。

未接入群聊采集时，RSI 仍可从机器人参与的对话中学习。

## 查看第一轮结果

管理员发送 `/prsi status`，检查是否启用、样本是否增长。达到样本和间隔条件后，后台会自动尝试一次改进。

- `/prsi history`：查看提案及其采用或拒绝原因。
- `/prsi habits`：查看生效的人格习惯。
- `/prsi runtime`：查看生效的参数与行为代码版本。
- `/prsi run`：立即尝试评估，仍需足够样本。
- `/prsi rollback`：整体回退上一版本，并暂停自动迭代。
- `/prsi resume`：恢复自动迭代。

没有采用新版本时，可以继续聊天、积累证据。功能已启用和某次改进通过评估，是两个不同的状态。

## 先做一次预演

```powershell
python scripts/preview_persona_rsi.py --core "<AstrBot核心目录>" --provider "<模型提供商ID>" --persona "<目标人格ID>" --turns 40
```

旁观学习预演可以追加 `--group-session "<会话UMO>"`，多个群重复填写该参数。预演使用模型额度，候选版本保存在临时环境，不修改运行中的机器人。

版本文件、参数范围和评估方法见 [技术说明](../plugins/astrbot_plugin_persona_rsi/README.md)。
