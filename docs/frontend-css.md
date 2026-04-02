# CSS / SCSS 规范

## 使用 BEM 命名规范
CSS 类名使用 BEM（Block-Element-Modifier）命名，避免全局污染。

**Good:**
```css
.card {}
.card__title {}
.card__button--disabled {}
```

## 禁止使用 !important
不允许在业务代码中使用 `!important`，应通过提高选择器优先级或重构样式结构解决。第三方库覆盖除外，需加注释说明原因。

## 颜色值使用设计系统变量
所有颜色必须引用 CSS 变量或设计 token，禁止硬编码十六进制颜色值。

**Bad:**
```css
.button { background-color: #1677ff; }
```

**Good:**
```css
.button { background-color: var(--color-primary); }
```

## 避免内联样式
禁止在 JSX 中使用内联 style（动态样式除外），应使用 CSS Module 或 styled-components。

**Bad:**
```tsx
<div style={{ color: 'red', fontSize: 14 }}>text</div>
```

## 响应式设计使用移动优先
媒体查询使用 `min-width`（移动优先），不使用 `max-width`，与主流 CSS 框架保持一致。

**Good:**
```css
.container { width: 100%; }
@media (min-width: 768px) { .container { width: 720px; } }
```

## 单位规范
- 字体大小使用 `rem`，不使用 `px`（方便无障碍缩放）
- 间距（margin/padding）使用设计系统的间距变量
- 动画时长使用变量，不硬编码毫秒数

## z-index 必须使用变量
禁止直接写 z-index 数字（如 `z-index: 999`），必须使用预定义的层级变量，避免层级混乱。

```css
/* Good */
.modal { z-index: var(--z-index-modal); }
.tooltip { z-index: var(--z-index-tooltip); }
```

## SCSS 嵌套不超过 3 层
SCSS 选择器嵌套不超过 3 层，超出时应拆分或使用 BEM 扁平化。
