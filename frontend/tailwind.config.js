/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Escala tomada del CSS de producción de platzi.com (fondo/paneles/bordes reales).
        ink: { 950: "#13161c", 900: "#171b21", 850: "#1e2229", 800: "#2d323a", 700: "#404650" },
        // Verde de marca de Platzi (#0ae98a, muestreado del logo oficial)
        // + su variante hover/oscura reales (#05a460).
        platzi: { DEFAULT: "#0ae98a", soft: "#38e99e", dark: "#05a460" },
        volt: { DEFAULT: "#27e0b3", soft: "#5cf0cc" },
        amber: { DEFAULT: "#f5b942" },
        // Rojo real de platzi.com para error/alerta — separado del verde de marca.
        danger: { DEFAULT: "#e53256", soft: "#ff6b83" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(10,233,138,0.25), 0 8px 30px -8px rgba(10,233,138,0.35)",
      },
      keyframes: {
        pulse2: { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: { pulse2: "pulse2 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};
