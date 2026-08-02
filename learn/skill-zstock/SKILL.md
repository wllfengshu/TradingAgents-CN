---
name: skill-zstock
description: 智股（Zstock）量化交易系统开发技能库。协助 zstock 系统的开发、调试、架构设计和数据获取；当用户提到 zstock、智股、量化交易系统开发、因子研究、策略层、执行层、XtQuant、MiniQMT、截面日频策略等相关主题时使用。
---

# skill-zstock · 智股系统开发技能库

## 系统定位

本技能服务于 **智股-Zstock** 量化交易系统的完整开发周期。系统采用五层架构：

```
第 0 层  治理与监控层  (AI Agent + 性能监控)
第 1 层  数据层        (AKShare + XtQuant + Redis + MongoDB)
第 2 层  研究层        (Qlib · 多因子)
第 3 层  策略层        (信号生成 + 组合优化 + 风控)
第 4 层  执行层        (XtQuant API · MiniQMT)
```

代码路径：`/zstock`

---

## 路由规则（按意图优先级）

| 优先级 | 触发意图 | 路由目标 | 操作 |
|--------|----------|----------|------|
| 1 | zstock 系统整体架构、各层设计、模块职责、MongoDB/Redis 数据流 | `knowledge-zstock/zstock-项目说明文档.md` | 阅读后回答 |
| 2 | 截面日频策略、因子计算（M1-M5）、龙头因子、合力因子、选股流程 | `knowledge-zstock/截面日频策略-开发指南.md` | 阅读后回答 |
| 3 | XtQuant API、xtdata 行情订阅、xttrader 下单、MiniQMT 接口 | `knowledge-xtdata/` 目录（按需选文件） | 阅读对应文件后回答 |
| 4 | TradingAgents-CN 主项目架构、多智能体机制、AI 选股功能 | `knowledge-TradingAgents-CN/` 目录 | 阅读对应文件后回答 |
| 5 | 写代码获取 A 股行情/K线/资金流/研报/龙虎榜等数据 | `skill-a-stock-data/SKILL.md` | 加载该 skill |
| 6 | 使用 akshare 获取 A 股/港股/期货等数据 | `skill-akshare/SKILL.md` | 加载该 skill |
| 7 | Python 代码风格、项目结构、导入规范 | `rule-coding/python系统开发规范.md` | 阅读后遵守 |
| 8 | 因子封装、分层架构、量化代码设计原则 | `rule-coding/量化系统开发规范.md` | 阅读后遵守 |

---

## 知识库文件索引

### knowledge-zstock（核心）
| 文件 | 内容 |
|------|------|
| `zstock-项目说明文档.md` | 五层架构全貌、各层模块设计、MongoDB collections、Redis 数据结构、数据流图 |
| `截面日频策略-开发指南.md` | 市场宇宙定义、M0-M5 因子流水线、选股规则、止损逻辑、参数表 |

### knowledge-xtdata
| 文件 | 内容 |
|------|------|
| `00_overview.md` | XtQuant 总览、模块关系 |
| `01_xtdata_api.md` | 行情 API（订阅/查询/下载） |
| `02_xtdata_fields.md` | 行情字段说明 |
| `03_xttrader_api.md` | 交易 API（下单/撤单/查询） |
| `04_xttrader_data_structures.md` | 订单/持仓数据结构 |
| `05_examples.md` | 完整可运行示例 |
| `06_faq.md` | 常见问题与坑 |

### knowledge-TradingAgents-CN
| 文件 | 内容 |
|------|------|
| `0-TradingAgents-CN概述.md` | 主项目概述 |
| `1-app模块.md` | 后端 API 模块 |
| `2-frontend模块.md` | 前端模块 |
| `3-tradingagents模块.md` | 核心 tradingagents 模块 |
| `4-多智能体运行机制深度解析.md` | Agent 协作机制 |
| `5-AI选股功能架构设计文档.md` | AI 选股完整设计 |

---

## 开发规范（编写代码时强制遵守）

1. **Python 风格**：Black 格式化，行宽 88，绝对导入，无通配符导入，双引号字符串。
2. **量化因子**：每个因子完全封装于独立类；`calculate` 方法接收统一数据容器；数据异常必须抛异常，禁止静默兜底。
3. **分层原则**：数据层不做因子计算；因子层不做信号过滤；策略层不直接操作订单。
4. **数据流**：Redis 存实时/缓存；MongoDB 存历史/报告/审计；禁止在内存中堆积大量历史行情。
5. **XtQuant**：必须先启动 MiniQMT 客户端；使用 mock（`xtquant_mock_util`）在无客户端环境开发。

---

## 工作流（接到开发任务时）

```
1. 意图澄清
   └─ 明确目标层（数据/研究/策略/执行/监控）和具体模块

2. 加载知识库
   └─ 按上方路由表读取对应 knowledge 文件

3. 检查现有代码
   └─ 读 /zstock 目录下相关模块，避免重复造轮子

4. 方案设计
   └─ 遵守分层原则 + 量化开发规范，不引入额外抽象

5. 实现 & 验证
   └─ 写代码 → 补单测 → 在 zstock 环境中验证
```

---

## 触发词

zstock、智股、量化系统、截面策略、日频策略、龙头因子、合力因子、因子流水线、
信号生成、组合优化、风控检查、换手控制、Buffer机制、执行层、订单生成、
XtQuant、xtdata、xttrader、MiniQMT、集合竞价、TWAP、
MongoDB collections、Redis 持仓、数据层设计、研究层、Qlib 因子、
治理层、监控告警、Brinson 归因、晨报生成
