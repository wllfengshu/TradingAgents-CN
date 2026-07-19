# Python 项目开发规范（面向 AI 辅助开发）

本规范旨在为 AI 生成的 Python 代码提供统一的风格、结构和最佳实践，确保代码可读性、可维护性和团队协作一致性。

---

## 1. 代码风格

- **遵循 PEP 8**：所有代码必须符合 https://peps.python.org/pep-0008/ 标准。
- **自动格式化**：使用 `black` 作为默认格式化工具，行长度限制为 **88 字符**（Black 默认）。
- **导入顺序**：
  1. 标准库
  2. 第三方库
  3. 本地模块/包
  
  每组之间空一行，使用绝对导入，避免通配符 `from module import *`。
- **引号**：字符串统一使用双引号 `" "`，除非单引号内包含双引号。

```python
# 正确
import os
import sys

import requests
from flask import Flask

from myapp.utils import helper
```

---

## 2. 命名约定

| 类型               | 风格              | 示例                     |
|-------------------|-------------------|--------------------------|
| 变量名             | 小写+下划线        | `user_name`, `item_list` |
| 函数名             | 小写+下划线        | `get_user()`, `validate_input()` |
| 类名               | 驼峰大写首字母      | `UserService`, `ConfigParser` |
| 常量               | 全大写+下划线       | `MAX_RETRIES = 3`        |
| 私有属性/方法       | 前导下划线          | `_internal_cache`        |
| 强私有（name mangling） | 双下划线前缀    | `__private_method`       |
| 模块名             | 简短小写+下划线     | `data_loader.py`         |
| 包名               | 简短小写（无下划线） | `utils`, `models`        |

**禁止**：单字母变量（除循环计数器 `i`, `j` 外）、拼音、缩写不明确的名称。

---

## 3. 项目目录结构

推荐采用 **src 布局**，将应用代码与测试、配置分离：

```
project/
├── src/                    # 主源码
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── user.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── auth.py
│   └── utils/
│       ├── __init__.py
│       └── helpers.py
├── tests/                  # 单元测试
│   ├── __init__.py
│   ├── test_services/
│   └── conftest.py
├── docs/                   # 文档
├── scripts/                # 运维脚本
├── .env.example            # 环境变量模板
├── requirements.txt        # 生产依赖
├── requirements-dev.txt    # 开发依赖
├── pyproject.toml          # 项目元数据与工具配置
├── setup.cfg               # 可选
└── README.md
```

---

## 4. 依赖管理

- **生产依赖**：写入 `requirements.txt`，固定版本号（如 `flask==2.3.0`）。
- **开发依赖**：写入 `requirements-dev.txt`，包含 `pytest`, `black`, `mypy`, `pre-commit` 等。
- **虚拟环境**：必须使用 `venv` 或 `conda` 隔离环境，不要全局安装。
- **pyproject.toml**：优先使用现代打包方式，定义项目名、版本、作者等信息。

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "my-project"
version = "0.1.0"
dependencies = [
    "requests>=2.28",
    "pydantic>=2.0",
]
```

---

## 5. 版本控制与提交规范

- **分支策略**：`main`（稳定版）、`develop`（开发集成分支）、`feature/*`（功能分支）、`fix/*`（修复分支）。
- **提交信息**：遵循 Conventional Commits 格式：
  - `feat: 添加用户登录接口`
  - `fix: 修复空指针异常`
  - `docs: 更新API文档`
  - `refactor: 重构数据库连接模块`
- **忽略文件**：`.gitignore` 必须包含 `__pycache__/`, `.env`, `*.pyc`, `dist/`, `.idea/` 等。

---

## 6. 测试

- **框架**：使用 `pytest`，禁止使用 `unittest`（除非遗留项目）。
- **覆盖率**：核心逻辑覆盖率达到 **80%** 以上，使用 `pytest-cov` 检查。
- **测试文件命名**：`test_<模块名>.py`，函数命名 `test_<功能描述>`。
- **Fixture**：利用 `conftest.py` 共享资源，避免重复代码。
- **Mock**：对外部服务（HTTP请求、数据库）使用 `unittest.mock` 或 `pytest-mock`。

```python
# tests/test_auth.py
def test_login_success(mocker):
    mock_db = mocker.patch("src.services.auth.get_user")
    mock_db.return_value = {"id": 1, "password_hash": "..."}
    result = login("user", "pass")
    assert result["status"] == "ok"
```

---

## 7. 文档与注释

- **Docstring**：所有公共模块、类、函数必须编写 docstring，采用 **Google 风格** 或 **NumPy 风格**。

```python
def calculate_discount(price: float, rate: float = 0.1) -> float:
    """计算折扣后的价格。

    Args:
        price: 原始价格（大于0）
        rate: 折扣率，默认为0.1

    Returns:
        折扣后的价格

    Raises:
        ValueError: 当价格或折扣率为负数时
    """
    if price < 0 or rate < 0:
        raise ValueError("价格和折扣率不能为负")
    return price * (1 - rate)
```

- **行内注释**：仅用于解释复杂逻辑，避免显而易见的注释。
- **README.md**：必须包含项目简介、安装步骤、快速开始、测试命令、许可证。

---

## 8. 类型注解

- 所有函数参数和返回值必须标注类型（`def func(a: int, b: str) -> bool:`）。
- 复杂类型使用 `typing` 模块：`List[int]`, `Dict[str, Any]`, `Optional[str]`, `Union[int, float]`。
- 使用 `mypy` 进行静态类型检查，配置在 `pyproject.toml` 中：

```toml
[tool.mypy]
strict = true
ignore_missing_imports = true
```

---

## 9. 错误处理

- **优先使用自定义异常**：继承 `Exception` 创建业务异常类。
- **避免裸 except**：捕获具体异常（`except ValueError as e`），除非在最顶层记录日志后重新抛出。
- **使用上下文管理器**：处理文件、网络连接等资源时使用 `with` 语句。
- **返回 vs 异常**：正常流程返回结果，异常情况抛出异常，不要用返回码或 `None` 表示错误。

```python
class UserNotFoundError(Exception):
    pass

def get_user(user_id: int) -> dict:
    user = db.query(...)
    if not user:
        raise UserNotFoundError(f"用户 {user_id} 不存在")
    return user
```

---

## 10. 日志

- 使用标准库 `logging`，不要使用 `print`。
- 日志级别：DEBUG（开发调试）、INFO（关键操作）、WARNING（潜在问题）、ERROR（异常）、CRITICAL（系统崩溃）。
- 日志格式：`%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- 敏感信息（密码、Token）必须脱敏后再记录。

```python
import logging

logger = logging.getLogger(__name__)

def process_payment(card_number: str):
    logger.info("处理支付，卡号最后四位: %s", card_number[-4:])
```

---

## 11. 安全规范

- **绝不硬编码密钥**：使用环境变量（`os.getenv("DB_PASSWORD")`）或 `.env` 文件加载。
- **SQL注入防护**：始终使用参数化查询（ORM 或 `cursor.execute(sql, params)`）。
- **输入验证**：使用 `pydantic` 或 `marshmallow` 对用户输入做 schema 校验。
- **文件路径**：禁止拼接用户输入到路径，使用 `pathlib.Path` 并做合法性检查。

---

## 12. 性能与并发

- **避免重复计算**：使用缓存（`functools.lru_cache` 或 Redis）。
- **列表推导式优先于 for 循环**（简单场景）。
- **大数据处理**：使用生成器（`yield`）减少内存占用。
- **并发**：I/O 密集型任务使用 `asyncio` 或线程池，CPU 密集型使用多进程。

---

## 13. 工具链配置（pyproject.toml 示例）

```toml
[tool.black]
line-length = 88
target-version = ['py310']

[tool.isort]
profile = "black"
line_length = 88

[tool.pytest.ini_options]
minversion = "7.0"
testpaths = ["tests"]
addopts = "-v --cov=src --cov-report=term-missing"

[tool.mypy]
strict = true
```

---

## 14. 其他约定

- **魔法数字**：定义为有意义的常量，例如 `TIMEOUT_SECONDS = 30`。
- **函数单一职责**：每个函数只做一件事，长度尽量不超过 50 行。
- **不要重复造轮子**：优先使用成熟库（`requests` 而非 `urllib`，`pandas` 而非手写 CSV 解析）。
- **兼容性**：目标 Python 版本 >= 3.10，使用 f-string 替代 `%` 或 `.format()`。

---

> **AI 注意事项**：当你根据此规范生成代码时，请严格遵守上述规则。若用户未指定额外要求，默认采用本规范。如有冲突，以用户明确指示为准。