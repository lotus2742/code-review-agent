# Node.js 后端编码规范

## 使用 async/await，禁止回调地狱
所有异步操作使用 async/await，禁止多层嵌套回调（callback hell）。Promise chain 超过 3 层时也应改为 async/await。

**Bad:**
```js
fs.readFile(path, (err, data) => {
  if (err) return callback(err);
  parse(data, (err, result) => { ... });
});
```

**Good:**
```ts
const data = await fs.promises.readFile(path);
const result = await parse(data);
```

## 错误处理必须覆盖 async 函数
async 函数调用必须有 try/catch 或 .catch()，不允许 unhandled promise rejection。

**Good:**
```ts
app.get('/users/:id', async (req, res, next) => {
  try {
    const user = await userService.findById(req.params.id);
    res.json(user);
  } catch (err) {
    next(err);
  }
});
```

## HTTP 状态码必须语义化
API 响应必须使用正确的 HTTP 状态码，禁止所有错误都返回 200。

| 场景 | 状态码 |
|------|--------|
| 创建成功 | 201 |
| 无内容 | 204 |
| 参数错误 | 400 |
| 未认证 | 401 |
| 无权限 | 403 |
| 资源不存在 | 404 |
| 服务器错误 | 500 |

## 输入校验必须在路由层完成
所有外部输入（请求参数、body、header）必须在进入业务逻辑前完成校验，使用 Zod 或 Joi 等校验库。

**Good:**
```ts
const schema = z.object({ name: z.string().min(1), age: z.number().int().positive() });
const body = schema.parse(req.body);
```

## 禁止在路由 handler 中直接操作数据库
路由 handler 只负责请求解析和响应格式化，业务逻辑和数据库操作必须放在 Service 层。

## 环境变量必须有默认值或启动时校验
启动时校验必要的环境变量，缺失时立即退出并打印清晰错误，不允许运行时才发现配置缺失。

## 日志使用结构化格式
使用 pino 或 winston 输出 JSON 格式日志，包含 timestamp、level、requestId 字段，禁止 console.log。

## 避免同步 I/O 操作
禁止在请求处理路径中使用 `fs.readFileSync`、`execSync` 等同步 I/O，会阻塞事件循环。

## 依赖版本锁定
`package.json` 中依赖版本不允许使用 `*` 或过宽的范围（如 `>=1.0.0`），必须锁定 major 版本（如 `^3.0.0`）。

## 接口返回值统一包装格式
所有 API 响应使用统一的 wrapper 格式，便于前端统一处理：
```json
{ "code": 0, "data": {}, "message": "ok" }
```
