/** Product artwork and labels drawn from the SKU (no external images to break). */

type Art = { paths: string[]; from: string; to: string };

const SHIRT = "M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23Z";
const BOX = ["M21 8 12 3 3 8v8l9 5 9-5V8Z", "m3 8 9 5 9-5M12 13v8"];

const ART: Record<string, Art> = {
  KUR: { paths: [SHIRT, "M12 4v6"], from: "#6c5b9e", to: "#c9b8e8" },
  TSH: { paths: [SHIRT], from: "#7a5af5", to: "#b9a8ef" },
  JNS: { paths: ["M6 2h12l1.2 20h-5L12 10l-2.2 12h-5L6 2Z", "M6 6h12"], from: "#2b4c7e", to: "#7ea6d8" },
  SNK: { paths: ["M2 17v-3.5l5-5.5 3 3 4-1 6.2 3A2 2 0 0 1 22 15v2H2Z", "M2 20h20", "M9 12l1.5 1.5M12 11l1.5 1.5"], from: "#e76f51", to: "#f2a541" },
  SND: { paths: ["M3 17c0-3 2-6 6-7l5-1c3 0 7 2 7 5v3H3Z", "M3 20h18", "M9 10l2 7M14 9l1 8"], from: "#b5651d", to: "#f2c086" },
  EBD: { paths: ["M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"], from: "#1f8a7d", to: "#7fd1c4" },
  PHN: { paths: ["M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z", "M11 18h2"], from: "#1c1530", to: "#6c5b9e" },
  SWT: { paths: ["M12 6a6 6 0 1 1 0 12 6 6 0 0 1 0-12Z", "M12 10v2l1 1", "m16.1 7.7-.8-4.1a2 2 0 0 0-2-1.6h-2.6a2 2 0 0 0-2 1.6l-.8 4.1", "m7.9 16.4.8 4a2 2 0 0 0 2 1.6h2.6a2 2 0 0 0 2-1.6l.8-4"], from: "#0f3d3e", to: "#2a9d8f" },
  SRM: { paths: ["M10 2h4v3h-4z", "M9 5h6v3l1 2v10a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2V10l1-2Z", "M8 14h8"], from: "#d1495b", to: "#f6bd60" },
  MUG: { paths: ["M17 8h1a4 4 0 1 1 0 8h-1", "M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V8Z"], from: "#8d99ae", to: "#c9d2e0" },
  MNG: { paths: ["M12 20c-4.4 0-8-3-8-7.5S8 4 12 4s8 4 8 8.5-3.6 7.5-8 7.5Z", "M12 4c0-1 1-2 3-2"], from: "#f2a541", to: "#ffd166" },
};

function family(sku: string): string {
  return sku.split("-")[0].toUpperCase();
}

/** "SNK-9" → "Size 9", "EBD-white" → "White", "SRM-30ml" → "30ml". */
export function variantLabel(sku: string): string {
  const v = sku.split("-").slice(1).join("-");
  if (!v) return "";
  if (/^(\d+|XS|S|M|L|XL|XXL)$/i.test(v)) return `Size ${v.toUpperCase()}`;
  return v.charAt(0).toUpperCase() + v.slice(1);
}

export function ProductArt({ sku, size = 72 }: { sku: string; size?: number }) {
  const art = ART[family(sku)] ?? { paths: BOX, from: "#6b6480", to: "#c9c3d6" };
  return (
    <span className="product-art" aria-hidden="true"
      style={{ width: size, height: size, background: `linear-gradient(135deg, ${art.from}, ${art.to})` }}>
      <svg width={size * 0.48} height={size * 0.48} viewBox="0 0 24 24" fill="none" stroke="white"
        strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        {art.paths.map((d, i) => <path key={i} d={d} />)}
      </svg>
    </span>
  );
}

/** Days left in the 30-day return window (null before delivery). */
export function daysLeft(deliveredAt: string | null, windowDays = 30): number | null {
  if (!deliveredAt) return null;
  const used = (Date.now() - new Date(deliveredAt).getTime()) / 86_400_000;
  return Math.max(0, Math.ceil(windowDays - used));
}
