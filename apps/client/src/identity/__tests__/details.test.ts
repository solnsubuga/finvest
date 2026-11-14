import { emptyForm, parseDay, readForm, showDay } from "../details";

const TODAY = "2026-11-13";
const good = {
  surname: " nakato ",
  givenNames: "Amara  Grace",
  documentNumber: "cf94-0123 45a2kx",
  dateOfBirth: "22/06/1994",
  expiresOn: "18/09/2030",
  tin: "",
};

describe("the details form", () => {
  it("reads dates as day, month and year, and refuses days that do not exist", () => {
    expect(parseDay("22/06/1994")).toBe("1994-06-22");
    expect(parseDay("2-6-1994")).toBe("1994-06-02");
    expect(parseDay("31/02/2000")).toBeNull();
    expect(parseDay("1994-06-22")).toBeNull();
    expect(showDay("1994-06-22")).toBe("22/06/1994");
  });

  it("tidies the details the way the API does before sending them", () => {
    expect(readForm(good, TODAY)).toEqual({
      ok: true,
      details: {
        surname: "nakato",
        givenNames: "Amara Grace",
        documentNumber: "CF94012345A2KX",
        dateOfBirth: "1994-06-22",
        expiresOn: "2030-09-18",
        tin: null,
      },
    });
  });

  it("says what is wrong with each field", () => {
    const read = readForm(
      { surname: "N4kato", givenNames: "", documentNumber: "12", dateOfBirth: "14/11/2008", expiresOn: "12/11/2026", tin: "123" },
      TODAY,
    );
    expect(read.ok).toBe(false);
    if (read.ok) return;
    expect(Object.keys(read.errors).sort()).toEqual(["dateOfBirth", "documentNumber", "expiresOn", "givenNames", "surname", "tin"]);
    expect(read.errors.dateOfBirth).toMatch(/18 or older/);
    expect(read.errors.expiresOn).toMatch(/expired/);
  });

  it("takes a client on their 18th birthday, and an ID that expires today", () => {
    const read = readForm({ ...good, dateOfBirth: "13/11/2008", expiresOn: "13/11/2026" }, TODAY);
    expect(read.ok).toBe(true);
  });

  it("fills the form again from confirmed details", () => {
    expect(
      emptyForm({ surname: "NAKATO", givenNames: "Amara", documentNumber: "CF94", dateOfBirth: "1994-06-22", expiresOn: "2030-09-18", tin: null }),
    ).toEqual({ surname: "NAKATO", givenNames: "Amara", documentNumber: "CF94", dateOfBirth: "22/06/1994", expiresOn: "18/09/2030", tin: "" });
  });
});
