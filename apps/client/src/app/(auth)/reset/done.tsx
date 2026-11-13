import { useRouter } from "expo-router";

import { Done } from "@/components/Done";

/** Forgot password, done: the new password is set and every device was signed out. */
export default function ResetDone() {
  const router = useRouter();
  return (
    <Done
      title="Your password is changed"
      detail="We signed you out on every device. Log in with your new password. For the next 24 hours, withdrawals only go to accounts you have used before."
      action="Log in"
      onPress={() => router.replace("/login")}
    />
  );
}
