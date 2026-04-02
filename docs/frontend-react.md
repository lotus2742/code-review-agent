# React 组件规范

## 组件必须使用函数式写法
禁止新增 class component，所有新组件使用函数式组件 + Hooks。现有 class component 在重构时迁移。

## Props 必须有 TypeScript 类型定义
每个组件的 props 必须声明对应 interface，不允许用 `any` 或省略类型。

**Bad:**
```tsx
const Button = ({ onClick, label }: any) => <button onClick={onClick}>{label}</button>;
```

**Good:**
```tsx
interface ButtonProps {
  onClick: () => void;
  label: string;
  disabled?: boolean;
}
const Button = ({ onClick, label, disabled = false }: ButtonProps) => (
  <button onClick={onClick} disabled={disabled}>{label}</button>
);
```

## useEffect 依赖数组不得遗漏
useEffect 中使用到的所有外部变量必须出现在依赖数组中，不允许用 `eslint-disable` 绕过此规则。

**Bad:**
```tsx
useEffect(() => { fetchData(userId); }, []); // userId 缺失
```

**Good:**
```tsx
useEffect(() => { fetchData(userId); }, [userId]);
```

## key 不能使用数组 index
列表渲染时 `key` 不能使用数组下标，必须使用业务唯一标识（如 id）。index 作为 key 会导致组件状态错乱。

**Bad:**
```tsx
items.map((item, index) => <Item key={index} {...item} />)
```

**Good:**
```tsx
items.map((item) => <Item key={item.id} {...item} />)
```

## 状态更新使用函数式更新
当新 state 依赖旧 state 时，必须使用 `setState(prev => ...)` 形式，避免 stale closure 问题。

**Good:**
```tsx
setCount(prev => prev + 1);
setList(prev => [...prev, newItem]);
```

## 避免在 JSX 中定义内联函数
JSX 中不允许定义匿名函数（如 `onClick={() => handleX(id)}`），应提取为具名函数，避免每次渲染重新创建。对于需要传参的场景，使用 `useCallback`。

## 自定义 Hook 必须以 use 开头
自定义 Hook 文件名和函数名都以 `use` 开头，且只能在组件顶层调用，不能在条件语句内调用。

**Bad（违反规范）：**
```ts
export function fetchUserData() { ... }    // 不以 use 开头，是违规的 Hook
export function postEditHandler() { ... }  // 不以 use 开头，是违规的 Hook
```

**Good（符合规范）：**
```ts
export function useUserData() { ... }   // 以 use 开头，符合规范
export function usePostEdit() { ... }   // 以 use 开头，符合规范
```

注意：`usePostEdit`、`useUserInfo`、`useFormState` 等已经以 `use` 开头的函数**符合规范**，不需要修改。

## 组件文件命名使用 PascalCase
组件文件名与组件名保持一致，使用 PascalCase，例如 `UserProfile.tsx`。工具函数文件使用 camelCase。

## 避免过深的组件嵌套
单个组件的 JSX 嵌套层级不超过 5 层，超出时应拆分子组件。

## Context 不得滥用
Context 只用于真正的全局状态（主题、用户信息、语言），局部状态通过 props 传递或 Zustand/Jotai 等状态库管理。
