"use client";

import { type InputHTMLAttributes, useId, useState } from "react";

/**
 * AuthInput — 登录/注册表单的统一输入框。
 * 标签（Medium 13）+ 圆角卡片 + focus 主色光圈（accent 注入），与现有
 * globals.css 输入框语义一致但改为主题化。trailing 可放「忘记密码」「获取验证码」等。
 */
export default function AuthInput({
  label,
  accent,
  hint,
  trailing,
  className,
  ...rest
}: {
  label: string;
  accent: string;
  hint?: string;
  trailing?: React.ReactNode;
} & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  const [focused, setFocused] = useState(false);

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block text-[13px] font-medium text-[#3F4A46]"
      >
        {label}
      </label>
      <div
        className="flex items-center rounded-xl border bg-white/90 px-3.5 transition-shadow"
        style={{
          borderColor: focused ? accent : "#E3E8E4",
          boxShadow: focused ? `0 0 0 3px ${accent}1f` : "none",
        }}
      >
        <input
          id={id}
          {...rest}
          onFocus={(e) => {
            setFocused(true);
            rest.onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocused(false);
            rest.onBlur?.(e);
          }}
          className={`h-12 w-full bg-transparent text-[14px] text-[#16191A] outline-none placeholder:text-[#A3ADA8] ${
            className ?? ""
          }`}
        />
        {trailing}
      </div>
      {hint && <p className="mt-1.5 text-[12px] text-[#98A29D]">{hint}</p>}
    </div>
  );
}
