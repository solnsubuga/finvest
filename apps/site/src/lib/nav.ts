/** The pages in the header, in order. Footer links are in components/Footer.astro. */
export const mainNav = [
  { href: "/products", label: "Products" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/safety", label: "Safety" },
  { href: "/about", label: "About" },
  { href: "/help", label: "Help" },
] as const;
