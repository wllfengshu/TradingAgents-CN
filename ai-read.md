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

第二次和ai对话：


