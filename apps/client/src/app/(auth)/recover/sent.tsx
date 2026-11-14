import { useRouter } from "expo-router";

import { Done } from "@/components/Done";

/**
 * The ask is in. The copy is the same whether or not the number has an account, as the server's
 * answer is, so this screen cannot be used to find out who is a client. The numbers stay out of
 * the address bar, so the screen does not repeat them.
 */
export default function CallBackSent() {
  const router = useRouter();
  return (
    <Done
      title="We will call you"
      detail="If that number has a Finvest account, our team will call you on the number you gave us within one working day to check it is you. Keep your ID with you. We never ask for your password, PIN or a code."
      action="Back to log in"
      onPress={() => router.replace("/login")}
    />
  );
}
