# Python 后端编码规范

## 类型注解强制要求
所有函数参数和返回值必须有类型注解。使用 Python 3.10+ 的 union 语法（`X | None`）而非 `Optional[X]`。

**Bad:**
```python
def get_user(user_id):
    return db.query(user_id)
```

**Good:**
```python
def get_user(user_id: int) -> User | None:
    return db.query(user_id)
```

## 异常处理不允许裸 except
禁止使用裸 `except:` 或无处理逻辑的 `except Exception: pass`。必须捕获具体异常类，并做有意义的处理或日志记录。

**Bad:**
```python
try:
    result = api_call()
except:
    pass
```

**Good:**
```python
try:
    result = api_call()
except httpx.TimeoutException as e:
    logger.error("API 超时", exc_info=e)
    raise ServiceUnavailableError from e
```

## 使用 dataclass 或 Pydantic 定义数据结构
函数间传递的复杂数据结构必须使用 `@dataclass` 或 `BaseModel`，禁止使用裸 dict 传递多个字段。

**Good:**
```python
from pydantic import BaseModel

class UserCreate(BaseModel):
    name: str
    email: str
    role: str = "user"
```

## 异步函数统一使用 async/await
涉及 I/O 操作（HTTP 请求、数据库查询、文件读写）的函数必须声明为 `async def`，不允许在异步上下文中使用阻塞调用（如 `time.sleep`，应使用 `asyncio.sleep`）。

## 环境变量通过集中配置类管理
不允许在代码中散落 `os.getenv()`，所有环境变量通过集中的 Settings 类（pydantic-settings）读取和校验。

**Good:**
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    api_key: str
    debug: bool = False

settings = Settings()
```

## 禁止在生产代码中使用 print
生产代码中禁止使用 `print()`，必须使用 `logging` 模块或 `structlog`，包含 level 和上下文信息。

**Bad:**
```python
print(f"用户 {user_id} 登录成功")
```

**Good:**
```python
logger.info("用户登录成功", user_id=user_id)
```

## 函数长度不超过 50 行
单个函数超过 50 行时必须拆分，每个函数只做一件事。

## 数据库查询必须防止 N+1 问题
ORM 查询涉及关联关系时，必须使用 `selectinload` 或 `joinedload` 预加载，禁止在循环中发起查询。

## 密码和敏感数据不得明文存储
密码必须使用 bcrypt 或 argon2 哈希，API Key 存储前必须加密，日志中不得出现敏感字段。

## 使用 pathlib 替代 os.path
文件路径操作使用 `pathlib.Path`，不使用 `os.path.join` 等旧式 API。
