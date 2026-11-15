/**
 * The goal a payment saves towards, remembered when a goal screen sends the client to pay. The
 * payment screens know only the investment, so the receipt reads this to name the goal and to
 * bring the client back to it. Kept in memory: after a reload on the web the receipt says what
 * it says for any investment, and Done goes Home.
 */
export type GoalSaving = { goalId: string; goalName: string };

const savings = new Map<string, GoalSaving>();

export function rememberGoalSaving(investmentId: string, saving: GoalSaving): void {
  savings.set(investmentId, saving);
}

export function goalSavingFor(investmentId: string): GoalSaving | null {
  return savings.get(investmentId) ?? null;
}
