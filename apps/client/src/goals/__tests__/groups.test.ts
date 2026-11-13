import { ApiError } from "@/api/http";

import { toGroupError } from "../apiGroupsClient";
import { GroupError, groupOutcomeUnknown, inviteLink, memberSummary, readInviteCode } from "../groups";

jest.mock("expo-secure-store", () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: "x",
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => {}),
  deleteItemAsync: jest.fn(async () => {}),
}));

const CODE = "Ab3_-Ab3_-Ab3_-Ab3_-Ab";

describe("invite links", () => {
  it("puts the code after #, so it never reaches a server", () => {
    expect(inviteLink(CODE)).toBe(`https://app.finvestug.com/goals/groups/join#${CODE}`);
  });

  it("reads a code from a link or on its own, and nothing else", () => {
    expect(readInviteCode(inviteLink(CODE))).toBe(CODE);
    expect(readInviteCode(`  ${CODE} `)).toBe(CODE);
    expect(readInviteCode(CODE.slice(1))).toBeNull();
    expect(readInviteCode(`${CODE}!`)).toBeNull();
    expect(readInviteCode("https://app.finvestug.com/goals/groups/join")).toBeNull();
  });
});

describe("member summary", () => {
  const member = (displayName: string, isYou = false) => ({
    displayName,
    joinedAt: "2026-11-13T09:00:00Z",
    isYou,
    isOwner: false,
    memberId: null,
  });

  it("names up to three and counts the rest", () => {
    expect(memberSummary([member("Grace", true)])).toBe("Just you so far");
    expect(memberSummary([member("Grace", true), member("Daniel")])).toBe("You and Daniel");
    expect(memberSummary([member("Grace"), member("Daniel"), member("Miriam")])).toBe("Grace, Daniel and Miriam");
    expect(memberSummary([member("A"), member("B"), member("C"), member("D")])).toBe("A, B and 2 others");
    expect(memberSummary([member("Grace"), member("Amara", true)])).toBe("Grace and you");
  });
});

describe("errors from the API", () => {
  const api = (code: string, fields: { field_path: string[]; code: string }[] = []) =>
    new ApiError(code as never, { status: 409, fields } as never);

  it("says a link is not working for any not found while joining", () => {
    expect(toGroupError(api("not_found"), "join").code).toBe("link_not_working");
    expect(toGroupError(api("not_found")).code).toBe("not_found");
  });

  it("reads a conflict by the call that met it", () => {
    expect(toGroupError(api("conflict"), "create").code).toBe("too_many_goals");
    expect(toGroupError(api("conflict"), "join").code).toBe("not_added");
    expect(toGroupError(api("conflict"), "invite").code).toBe("full");
    expect(toGroupError(api("conflict"), "leave").code).toBe("owner_cannot_leave");
    expect(toGroupError(api("conflict")).code).toBe("closed");
  });

  it("treats a lost answer as an unknown outcome and a refusal as nothing made", () => {
    expect(groupOutcomeUnknown(toGroupError(new TypeError("Network request failed")))).toBe(true);
    expect(groupOutcomeUnknown(new GroupError("not_added"))).toBe(false);
  });
});
