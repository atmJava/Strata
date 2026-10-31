# AGENT-CODEX-MODEL-CATALOG-QUALITY contract

日期：2026-11-01

状态：目录供给完成；真实工具面已修复。最终回复严格收口和 DSH 终端对照仍开放。

## 问题

用户从原来的十余分钟任务降到几分钟后，主观代码质量下降。现有证据不能把这一感受归因给
GPU 引擎或模型量化：观察到的一个确定问题是，Codex 请求中的模型 slug 不在本地模型目录时，
客户端只发送缩减后的工具面，模型仍可能写 `apply_patch`，但该工具没有被声明，服务只能把
它退回到普通文本。这个协议失败会直接表现成“改不动代码”“多绕几轮”或“最后没收口”，
与生成质量变差相似。

官方 Codex 文档提供启动时加载 JSON 模型目录的设置 `model_catalog_json`，以及用于兼容
Responses API 网关的自定义 model provider：

- <https://developers.openai.com/codex/config-reference>
- <https://developers.openai.com/codex/config-file/config-advanced#custom-model-providers>

## 目标

`AGENT-CODEX-MODEL-CATALOG-QUALITY`：让用户通过 Strata 连接 Codex 时，默认获得与当前
模型 slug 匹配的工具元数据和上下文限制；在不改变模型、量化、引擎和生产配置的前提下，
验证代码修改、测试、最终回复和工具协议是否正确。

质量是硬门。任何速度候选都必须先通过本契约，不能以“更快但成功率略低”换收益。

## 必须回答

1. 产品启动、安装或连接流程能否自动写入 Codex 模型目录，还是只能给出手工步骤？
2. 目录条目必须包含哪些字段，才能让 `apply_patch` 以 `custom`/freeform 工具发送？
3. 目录条目在 Codex 重启、模型切换、升级和陌生用户目录下是否幂等、可回滚？
4. Qwen 当前模型与用户原来的参考模型在代表性 coding 任务上的成功率、严格收口和工具
   调用差异是什么？哪些差异是模型本身，哪些是客户端目录、上下文限制或采样配置？
5. 高、低 reasoning effort 的速度收益是否值得它的质量损失？
6. DSH 能否配置为经 Strata endpoint 运行？若不能，所有对照必须保留 DeepSeek 直连标签。

## 验收门

- 真实 Codex 请求的工具面包含 `apply_patch`，类型为 `custom`；
- 服务日志不再出现“写了未声明的 `apply_patch`”事件；
- 至少 7 个确定性 coding fixture 全部满足：测试通过、测试文件字节不变、return code 为 0、
  最终回复严格为约定 sentinel、无未声明工具错误、无超时；
- `apply_patch` fixture 至少 3 次独立运行全部通过；只通过一次不算稳定；
- 目录更新幂等，重复运行不重复添加，写入前有备份，失败可恢复；
- 默认不改变引擎、pack、量化、GPU 分配、上下文、6 slots / 2 groups 或生产 config；
- 任何代表性真实任务失败都必须保留为质量缺口；不能因小 fixture 通过就宣称整体质量已恢复。

## 证据边界

- 小 fixture 证明协议和基本 coding 成功，不证明所有真实任务质量。
- Qwen/Strata 与 DeepSeek 直连的比较同时改变模型、量化、hosting 和 provider，只能作为
  参考对照。
- 串行 wall 受 fixture、缓存和服务器状态影响，不用于建立通用速度结论。
- 未经真实用户路径复测，不能把目录修复写成主观质量已经恢复。
