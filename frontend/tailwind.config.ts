import type { Config } from "tailwindcss";

// 색상 토큰은 UI_GUIDE.md를 따른다. 손익/방향성 시맨틱 외에는 컬러를 쓰지 않는다.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 배경 표면
        background: "#0a0a0a",
        surface: "#141414",
        "surface-input": "#1a1a1a",
        // 데이터/시맨틱 색상
        up: "#22c55e",
        down: "#ef4444",
        warn: "#f59e0b",
        flat: "#525252",
      },
    },
  },
  plugins: [],
};

export default config;
