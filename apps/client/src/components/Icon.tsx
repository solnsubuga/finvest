import Svg, { Circle, Path } from "react-native-svg";

import { colors } from "@/theme";

const paths = {
  back: ["M15 6l-6 6 6 6"],
  chevron: ["M9 6l6 6-6 6"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  eye: ["M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"],
  eyeOff: [
    "M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z",
    "M4 4l16 16",
  ],
  key: ["M12 12h9M18 12v3M21 12v2"],
  home: ["M4 11l8-7 8 7v9H4z"],
  invest: ["M4 19V5M4 19h16M8 15l4-4 3 3 5-6"],
  portfolio: ["M4 8h16v11H4zM9 8V5h6v3"],
  goals: [],
  activity: ["M5 7h14M5 12h14M5 17h9"],
  documents: ["M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5"],
  profile: ["M5 20c1-4 4-5.5 7-5.5s6 1.5 7 5.5"],
  help: ["M9.5 9.5a2.5 2.5 0 115 0c0 1.5-2.5 2-2.5 4M12 17.5v.01"],
  phone: [
    "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
  ],
  message: ["M4 5h16v11H9l-5 4z"],
  mail: ["M3 6h18v12H3z", "M3 7l9 6 9-6"],
  bell: ["M6 16V11a6 6 0 0112 0v5l2 2H4zM10 21h4"],
  insights: ["M5 20v-7M10 20V6M15 20v-10M20 20V9"],
  repeat: [
    "M17 3l3 3-3 3M4 12V9a3 3 0 013-3h13M7 21l-3-3 3-3M20 12v3a3 3 0 01-3 3H4",
  ],
  plus: ["M12 5v14M5 12h14"],
  send: ["M5 12h14M13 6l6 6-6 6"],
  download: ["M12 4v11M7 10.5l5 5 5-5M5 20h14"],
  calendar: ["M4 6h16v14H4zM4 10h16M8 3v4M16 3v4"],
  // A shield with a tick: the identity check.
  shield: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", "M8.5 12l2.5 2.5 4.5-5"],
  // Ridges of a fingertip: unlocking with a fingerprint or face.
  fingerprint: [
    "M5.5 8.5A7.5 7.5 0 0119 10",
    "M8 19.5c1-1.8 1.5-3.8 1.5-6.5a2.5 2.5 0 015 0c0 2-.3 3.8-.9 5.5",
    "M5 15c.6-1.2.9-2.4.9-3.8a6.1 6.1 0 0112.2 0c0 1.6-.1 3.1-.5 4.6",
    "M12 13c0 3-.8 5.6-2.2 8",
  ],
} as const;

export type IconName = keyof typeof paths;

type Props = {
  name: IconName;
  size?: number;
  color?: string;
  strokeWidth?: number;
};

/** Line icons drawn to match the design canvas. Decorative: label the control, not the icon. */
export function Icon({
  name,
  size = 20,
  color = colors.ink,
  strokeWidth = 2,
}: Props) {
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      {paths[name].map((d) => (
        <Path key={d} d={d} />
      ))}
      {name === "eye" || name === "eyeOff" ? (
        <Circle cx={12} cy={12} r={2.8} />
      ) : null}
      {name === "key" ? <Circle cx={8} cy={12} r={4} /> : null}
      {name === "goals" ? (
        <>
          <Circle cx={12} cy={12} r={8} />
          <Circle cx={12} cy={12} r={3} />
        </>
      ) : null}
      {name === "profile" ? <Circle cx={12} cy={8} r={3.5} /> : null}
      {name === "help" ? <Circle cx={12} cy={12} r={9} /> : null}
    </Svg>
  );
}
