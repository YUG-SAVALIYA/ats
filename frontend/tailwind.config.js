/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#000000",
        card: "#0a0a0a",
        border: "#27272a",
        accent: "#ffffff",
        muted: "#71717a",
      },
    },
  },
  plugins: [],
}
