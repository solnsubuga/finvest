/** "By you", "By Ruth Namata", or "By a colleague" while their name is not known. */
export function proposedBy(mine: boolean, name: string | null | undefined): string {
  if (mine) return "By you";
  return name ? `By ${name}` : "By a colleague";
}
