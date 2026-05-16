这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，我删掉了很多无用的代码。我需要你按照如下要求来协助我进行二次开发。
注意事项：
1、我当前电脑是Windows环境，使用powershell执行命令行，多行命令需要使用分号分隔。
2、第一次执行命令前需要启动虚拟环境： .venv\Scripts\activate
3、当前项目分前后端，后端启动命令是： python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
前端启动命令是： cd frontend; npm run dev
4、该项目使用了mongodb和redis，我已经把环境都启动好了。
5、如下文件目录，你直接忽略：./github ./idea ./venv /data /eval_results /logs /results .env.back .env.example .gitignore .python-version .error.log
6、你写的代码务必要仿照现有的交互、页面风格来做，保证风格统一
你的任务：
我已经在frontend/src/components/Layout/SidebarMenu.vue文件中增加了“AI选股”的菜单，你需要实现菜单的点击事件，点击后打开“AI选股页面”。
“AI选股页面”页面可以参考frontend/src/views/Analysis/SingleAnalysis.vue来画。页面包含几个模块：分析师团队、运行按钮、运行结果。
分析师团队有（纯静态页面展示）：
大盘分析师（分析指数/北向资金/涨跌比等指标）
主线板块分析师（分析涨停集中度/5日强度等指标）
市场合力分析师（分析主力+散户双向净流入等指标）
股票龙头分析师（分析连板/板块排名/成交量等指标）
风险分析师（分析股票的风险，排除ST/新股/退市等指标）
决策分析师（给出最终的决策）
运行按钮：
运行按钮分为2个，一个是立即运行，一个是定时运行。

第2次和ai对话：
“AI选股页面”画的不错，下面开始实现具体的功能，先实现“立即运行”功能，点击后会调用后端的接口，后端接口的逻辑是：
1、构建一个“大盘分析师 Agent”，使用代码计算指数/北向资金/涨跌比等指标（可扩展），然后把计算好的数据发给Agent，Agent分析后得出结果，然后继续下一步；
2、构建一个“主线板块分析师 Agent”，使用代码涨停集中度/5日强度等指标（可配置），然后把计算好的数据发给Agent，Agent分析后得出结果，然后继续下一步；
3、以此类推依次构建“市场合力分析师 Agent”、“股票龙头分析师 Agent”、“风险分析师 Agent”、“决策分析师 Agent”，每个Agent都使用代码计算相关指标（可配置），然后把计算好的数据发给Agent，Agent分析后得出结果，最后由“决策分析师 Agent”给出最终的选股决策。
注意事项：
由于这些Agent设计模式是相同的，都是先计算指标，再给Agent分析，最后由Agent给出结果，所以你可以参考当前项目里面已有的比如“市场分析师”、“基本面分析师”、“新闻分析师”这些Agent来设计。
切记不要重复造轮子，多参考当前项目已有的功能！

第3次和ai对话：
有一个很严重的问题，你写的所有ai大模型调用全部失败了，错误是：2026-05-10 18:21:52 | app.services.ai_selector_service | ERROR    | ❌ [风险分析师] LLM调用失败: Error code: 401 - {'error': {'message': 'Incorrect API key provided. For details, see: https://help.aliyun.com/zh/model-studio/error-code#apikey-error', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}, 'request_id': '10838285-1df1-974b-ad13-820047fbab0d'}
根本原因是你用错了大模型的调用方法，因为我使用这个项目已有的“股票分析”功能是正常的，你需要完全按照“股票分析”功能的方式来调用大模型，已有的大模型调用的逻辑在tradingagents/llm_adapters这个目录下。
下面是“股票分析”功能的日志：
2026-05-10 18:26:59 | uvicorn.access       | INFO     | 127.0.0.1:61660 - "GET /api/analysis/tasks/2df5f657-6f97-4c4d-9e94-58d545ff5cc9/status HTTP/1.1" 200 trace=-
2026-05-10 18:27:03,173 | httpx                | INFO | HTTP Request: POST https://models.inference.ai.azure.com/chat/completions "HTTP/1.1 200 OK"
2026-05-10 18:27:03,175 | llm_adapters         | INFO | 📊 Token使用 - Provider: custom_openai, Model: gpt-4o, 总tokens: None, 提示: None, 补全: None, 用时: 13.43s
2026-05-10 18:27:03,175 | default              | INFO | 🐻 [空头研究员] 发言完成，计数: 1 -> 2
2026-05-10 18:27:03,176 | default              | INFO | 🔍 [投资辩论控制] 当前发言次数: 2, 最大次数: 2 (配置轮次: 1)
2026-05-10 18:27:03,177 | default              | INFO | 🔍 [投资辩论控制] 当前发言者: Bear Analyst: 在辩论中，我将从宏观经济环境、公司竞争劣势、潜在风险和负


第4轮对话：
目前“AI选股”流程已经可以正常跑通，我需要你把每次分析结果记录到MongoDB数据库中。
然后在“AI选股”菜单下增加一个“选股记录”子菜单，这个子菜单会列出所有分析结果，并且点击可以查看详情。
“选股记录”页面可以参考frontend/src/views/Reports/index.vue来画



这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，我删掉了很多无用的代码。我需要你按照如下要求来协助我进行二次开发。
注意事项：
1、我当前电脑是Windows环境，使用powershell执行命令行，多行命令需要使用分号分隔。
2、第一次执行命令前需要启动虚拟环境： .venv\Scripts\activate
3、当前项目分前后端，后端启动命令是： python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
前端启动命令是： cd frontend; npm run dev
4、该项目使用了mongodb和redis，我已经把环境都启动好了。
5、如下文件目录，你直接忽略：./github ./idea ./venv /data /eval_results /logs /results .env.back .env.example .gitignore .python-version .error.log
6、你写的代码务必要仿照现有的交互、页面风格来做，保证风格统一
你的任务：
我设计了一个“AI选股”功能，代码在app/services/ai_selector_service.py中，需要你解决如下问题：
目前的代码多个Agent是一起分析的，其实他们是有输入和输出依赖的。
大盘分析师如果分析出大盘环境不好，那么应该取消后面的分析。
主线板块分析师如果分析出没有明显的主线板块，那么也应该取消后面的分析，并且把得到的主线板块传给市场合力分析师。
市场合力分析师根据上一步传的主线板块来分析主力和散户的资金流向，得到2到3支股票（如果没有分析出结果，直接终止），并且把分析出的股票传给后面的股票龙头分析师。
股票龙头分析师根据上一步传的股票，分析出真正的龙头股（1到2支），传给风险分析师。
风险分析师根据上一步传的股票，分析出是否有风险，如果有风险，则取消后面的分析，如果没有风险，则把最终的股票传给决策分析师。
决策分析师根据上一步传的股票，给出最终的选股决策。


这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，我删掉了很多无用的代码。我需要你按照如下要求来协助我进行二次开发。
注意事项：
1、我当前电脑是Windows环境，使用powershell执行命令行，多行命令需要使用分号分隔。
2、第一次执行命令前需要启动虚拟环境： .venv\Scripts\activate
3、当前项目分前后端，后端启动命令是： python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
前端启动命令是： cd frontend; npm run dev
4、该项目使用了mongodb和redis，我已经把环境都启动好了。
5、如下文件目录，你直接忽略：./github ./idea ./venv /data /eval_results /logs /results .env.back .env.example .gitignore .python-version .error.log
6、你写的代码务必要仿照现有的交互、页面风格来做，保证风格统一
你的任务：
“AI选股”功能，代码在app/services/ai_selector_service.py中，需要你解决如下问题：
1、这里面很多接口调用了多次，比如stock_zt_pool_em这个接口。我需要你做一下缓存。做成和java的threadlocal类似的效果，
当运行“AI选股”功能时，每调用一个接口就进行缓存，当运行结束，就清空缓存。


背景：
这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，我删掉了很多无用的代码。我需要你按照如下要求来协助我进行二次开发。
注意事项：
1、我当前电脑是Windows环境，使用powershell执行命令行，多行命令需要使用分号分隔。
2、第一次执行命令前需要启动虚拟环境： .venv\Scripts\activate
3、当前项目分前后端，后端启动命令是： python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
前端启动命令是： cd frontend; npm run dev
4、该项目使用了mongodb和redis，我已经把环境都启动好了。
5、如下文件目录，你直接忽略：./github ./idea ./venv /data /eval_results /logs /results .env.back .env.example .gitignore .python-version .error.log
6、你写的代码务必要仿照现有的交互、页面风格来做，保证风格统一
你的任务：
1、“AI选股”功能已经实现了，前端页面在“frontend/src/views/AiSelector”目录中，后端代码在“app/services/ai_selector_service.py”。它是多Agent协同来选出股票。
2、“单个股票”分析功能也实现了，前端页面在“frontend/src/views/Analysis/SingleAnalysis.vue”中，后端代码在“app/services/analysis_service.py”。它是多Agent协同来分析股票。
3、我现在要你完成“AI交易”功能，前端页面在“frontend/src/views/AITrading”目录中，前后端代码可以参考“AI选股”功能的代码来实现。
“AI交易”功能是：
（1）先获取账户持仓情况（我封装了xtquant 工具模块 — 封装 miniQMT 连接、账户查询、下单操作。代码在“app/utils/xtquant_util.py”中。但是我当前电脑环境没有安装miniQMT，所以这个类不可用，我需要你自己mock一个）
（2）如果有持仓，先调用“单个股票”分析功能，同时并发调用“AI选股”功能，把持仓情况、股票分析结果、AI选股结果等数据传给“仓位管理分析师Agent”，让“仓位管理分析师Agent”给出卖卖信号（以什么价格买或者卖出多少股）；然后把买卖信号给到“交易决策分析师Agent”，“交易决策分析师Agent”调用xtquant_util.py工具类进行下单。
（3）如果没有持仓，和步骤二的区别是不需要调用“单个股票”分析功能，其他逻辑一样。

你先把“AI交易”页面设计出来；然后再开始写后端逻辑


