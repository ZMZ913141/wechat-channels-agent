"""web 包：Flask 应用与按业务拆分的视图模块。

模块划分：
    base.py            应用实例、扩展、共用上下文
    views_core.py      首页（素材库）、素材 CRUD、批量操作、文件服务
    views_library.py   素材库维护（查重/孤儿清理/归档）、目录导入
    views_accounts.py  账号管理、扫码登录、登录态探测
    views_publish.py   发布中心、发布任务、结果轮询
"""