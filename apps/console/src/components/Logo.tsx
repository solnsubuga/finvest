import { colors } from "@finvest/tokens";

interface LogoMarkProps {
  size?: number;
  /** "light" sits on a light background, "dark" sits on the black sidebar. */
  tone?: "light" | "dark";
}

/** The Finvest mark: a white rising line ending in a lime dot on a rounded square. */
export function LogoMark({ size = 32, tone = "light" }: LogoMarkProps) {
  return (
    <svg role="img" aria-label="Finvest" width={size} height={size} viewBox="0 0 64 64">
      <rect width="64" height="64" rx="18" fill={tone === "dark" ? colors.onBlackSurface : colors.black} />
      <path
        d="M14 44l11-12 9 8 9-11"
        fill="none"
        stroke={colors.white}
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="49" cy="20" r="6.5" fill={colors.lime} />
    </svg>
  );
}
