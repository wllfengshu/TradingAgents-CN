这是基于网上的TradingAgents-CN开源项目做的二次开发的项目，我删掉了很多无用的代码。 我需要你按照如下要求来协助我进行二次开发。
1、我当前电脑是Windows环境，使用powershell执行命令行，多行命令需要使用分号分隔。
2、第一次执行命令前需要启动虚拟环境： .venv\Scripts\activate
3、当前项目分前后端，后端启动命令是： python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
前端启动命令是： cd frontend; npm run dev
4、该项目使用了mongodb和redis，我已经把环境都启动好了。
5、如下文件目录，你直接忽略：./github ./idea ./venv /data /eval_results /logs /results .env.back .env.example .gitignore .python-version .error.log
