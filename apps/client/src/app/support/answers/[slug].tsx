import { View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";

import { Notice } from "@/components/Blocks";
import { Button } from "@/components/Button";
import { Screen } from "@/components/Screen";
import { StackHeader } from "@/components/StackHeader";
import { Title, Txt } from "@/components/Txt";
import { answerFor } from "@/support/answers";

/** One quick answer, with a way into the chat if it did not help. */
export default function AnswerScreen() {
  const router = useRouter();
  const { slug } = useLocalSearchParams<{ slug: string }>();
  const answer = answerFor(slug);

  return (
    <Screen
      signedIn
      footer={
        <Button
          label="Still stuck? Write to us"
          onPress={() =>
            router.navigate({ pathname: "/help", params: { topic: answer?.topic ?? "other" } })
          }
          testID="answer-to-chat"
        />
      }
    >
      <Stack.Screen options={{ title: answer?.title ?? "Help" }} />
      <StackHeader title="Help" fallback="/help" />
      <View style={{ marginTop: 24, gap: 14 }}>
        {answer ? (
          <>
            <Title>{answer.title}</Title>
            {answer.paragraphs.map((paragraph) => (
              <Txt key={paragraph} lineHeight={23}>
                {paragraph}
              </Txt>
            ))}
          </>
        ) : (
          <Notice tone="neutral">We could not find that answer. Write to us and we will help.</Notice>
        )}
      </View>
    </Screen>
  );
}
