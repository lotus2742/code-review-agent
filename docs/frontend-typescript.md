# TypeScript 编码规范

## 禁止使用 any 类型
不允许在代码中使用显式 `any` 类型，包括函数参数、返回值和变量声明。应使用具体类型、泛型或 `unknown` 替代。

**Bad:**
```ts
function process(data: any) { return data.name; }
```

**Good:**
```ts
function process(data: Record<string, unknown>) { return String(data.name); }
```

## 函数返回类型必须显式声明
所有导出函数和类方法必须显式声明返回类型，不允许完全依赖类型推断。有助于 API 契约的稳定性和文档清晰度。

**Bad:**
```ts
export function getUser(id: string) { return fetchUser(id); }
```

**Good:**
```ts
export function getUser(id: string): Promise<User> { return fetchUser(id); }
```

## interface 与 type 的选择
- 描述对象形状优先用 `interface`（支持声明合并、可扩展）
- 联合类型、交叉类型、工具类型用 `type`
- 不允许同一概念同时存在 interface 和 type 两个定义

**Bad:**
```ts
type UserProps = { name: string };
interface UserProps { name: string } // 重复定义
```

## readonly 修饰不可变属性
函数参数中不会被修改的对象属性，使用 `readonly` 修饰，防止意外变更。

**Good:**
```ts
function render(props: Readonly<{ title: string; count: number }>) { ... }
```

## 可选链和空值合并
使用 `?.` 和 `??` 替代手动 null 检查，禁止使用 `||` 做默认值（`0` 和 `''` 会被误判为假值）。

**Bad:**
```ts
const name = user && user.profile && user.profile.name || 'anonymous';
```

**Good:**
```ts
const name = user?.profile?.name ?? 'anonymous';
```

## 枚举使用 const enum 或联合类型
避免使用普通 `enum`（会生成运行时对象），优先使用 `const enum` 或字符串联合类型。

**Good:**
```ts
type Status = 'pending' | 'success' | 'error';
const enum Direction { Up, Down, Left, Right }
```

## 非空断言操作符谨慎使用
`!` 非空断言只在能 100% 确定值不为 null/undefined 时使用，不允许用来"消除"类型错误。

## 泛型命名规范
泛型参数使用有意义的名称，单字母仅限于简单工具类型。`T` 表示主类型，`K` 表示键，`V` 表示值，`E` 表示错误。
